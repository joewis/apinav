#!/home/carl/mcp-gateway-venv/bin/python
"""MCP server exposing the local multi-source API catalog index.

Sources come from two paths that converge in apinav_semantic_search:
1. The local SQLite catalog (keyword + semantic search over stored rows).
2. Live directory plugins auto-discovered from plugins/ — per-query overlays
   merged into the candidate pool BEFORE reranking; novel ids are persisted
   by ingest.py (organic growth), so the catalog grows during usage.

Tools:
- apinav_keyword_search: FTS5 keyword search over name/description/category
- apinav_semantic_search: cosine similarity + live plugin merge + rerank
- apinav_get_api: fetch one API's full record by id or slug
- apinav_live_search: search a single live plugin directly (not cached)
- apinav_catalog_stats: counts + freshness

Secrets (NVIDIA embed key, rerank key) come from config.secret(), sourced
from the per-host .env file — never hard-coded.
"""
import asyncio
import json
import math
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import schema
from spam import is_spam as _is_spam

from mcp.server.mcpserver import MCPServer

server = MCPServer("apinav-mcp", "1.0.0")

EMBED_URL = config.get("embeddings", "url")
EMBED_MODEL = config.get("embeddings", "model")
RERANK_URL = config.get("rerank", "url")
RERANK_MODEL = config.get("rerank", "model")
RERANK_TOP_N = config.get("rerank", "top_n")
FTS_PREPASS_N = config.get("search", "fts_prepass_n")
RERANK_THRESHOLD = config.get("rerank", "threshold")


# --- Near-duplicate suppression ------------------------------------------------
# The catalog has many clone rows (same name+description, different slug). Without
# dedup one bad clone can occupy several top-N slots. Dedupe on normalized
# (name, description-head), keeping the first occurrence (candidates arrive in
# cosine order, so the first is the highest-scored). Distinct APIs that share only
# a name survive, because the description-head differs.
#
# Some live sources return <em> highlight tags inside name/category/description;
# strip them before any matching — they'd otherwise break the regexes and the
# dedup normalization alike.

def _strip_tags(s: str) -> str:
    """Strip HTML <em>-style tags from a string (may be None)."""
    return re.sub(r"<[^>]+>", "", s or "")

def _dedup_key(c: dict) -> str:
    """Generate a deduplication key from name and description prefix."""
    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", _strip_tags(s).lower())
    return _norm(c.get("name")) + "|" + _norm((c.get("description") or "")[:120])


# --- Audio direction guard ------------------------------------------------------
# The cross-encoder can't reliably tell "text to speech" from "speech to text"
# (too many shared tokens). Penalize candidates whose audio direction contradicts
# the query's — the zeroed score drops them below the relevance threshold. Direction
# is judged on the NAME only: descriptions may legitimately mention both directions
# for a converter API.

_TTS_RE = re.compile(
    r"\b(text[\s\-_]*(?:to|2)[\s\-_]*(?:spe(?:e|a)ch|voice|audio|sound)|"
    r"\btts\b|voice\s*generator|speech\s*generat|voice\s*over)", re.I)
_STT_RE = re.compile(
    r"\b((?:spe(?:e|a)ch|voice|audio)[\s\-_]*(?:to|2)[\s\-_]*text|"
    r"audio\s*(?:file\s*)?to\s*text|transcri\w*|speech\s*recognition|"
    r"voice\s*recognition)", re.I)

def _direction_penalty(query: str, candidate: dict):
    """Return 0.0 if the candidate's audio direction contradicts the query,
    else None (no penalty)."""
    q = query or ""
    q_tts = bool(_TTS_RE.search(q)) and not _STT_RE.search(q)
    q_stt = bool(_STT_RE.search(q)) and not _TTS_RE.search(q)
    if not (q_tts or q_stt):
        return None
    name = _strip_tags(candidate.get("name") or "")
    c_tts = bool(_TTS_RE.search(name))
    c_stt = bool(_STT_RE.search(name))
    if q_tts and c_stt and not c_tts:
        return 0.0
    if q_stt and c_tts and not c_stt:
        return 0.0
    return None

def _rerank_endpoint(query: str, documents: list[str]) -> list[float]:
    """Rerank via the configured rerank endpoint (Cohere shape) → scores by index.

    Returns scores aligned with `documents` order. Raises on failure — caller
    decides the fallback.
    """
    body = json.dumps({
        "model": RERANK_MODEL,
        "query": query,
        "documents": documents,
    }).encode()
    req = urllib.request.Request(
        RERANK_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {config.secret('OPENROUTER_API_KEY')}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    # Endpoint returns unranked scores by index.
    scores = [0.0] * len(documents)
    for x in data.get("results", []):
        i = x.get("index")
        if isinstance(i, int) and 0 <= i < len(documents):
            scores[i] = float(x.get("relevance_score") or 0.0)
    return scores


def _rerank(query: str, candidates: list[dict]) -> list[dict]:
    """Rerank candidate API summaries by cross-encoder relevance.

    Runs over the MERGED pool (local cosine + FTS + live plugins) so all
    sources compete for top slots on relevance, rather than live results
    being appended unranked to the tail. Falls back to the input (cosine)
    order if the endpoint errors or returns a mismatched score count —
    the caller never fails on a rerank hiccup.
    """
    if not candidates:
        return candidates
    # Dedup before reranking so a clone can't crowd out real distinct rows.
    seen = set()
    deduped = []
    for c in candidates:
        k = _dedup_key(c)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(c)
    candidates = deduped

    documents = []
    for c in candidates:
        name = _strip_tags(c.get("name") or "")
        cat = _strip_tags(c.get("category") or "")
        # Reuse the shared MAX_DESC_CHARS truncation so huge descriptions
        # (e.g. APIs.guru specs at 250KB) don't bloat the re-ranker payload;
        # the same limit guards embed_matrix._api_text.
        desc = _strip_tags(c.get("description") or "")[:MAX_DESC_CHARS]
        documents.append(f"{name} | {cat} | {desc}")
    scores = None
    threshold = RERANK_THRESHOLD
    try:
        scores = _rerank_endpoint(query, documents)
    except Exception:
        return candidates
    if scores is None or len(scores) != len(candidates):
        return candidates
    for c, score in zip(candidates, scores):
        s = float(score)
        if _direction_penalty(query, c) is not None:
            s = 0.0
        c["_rerank_score"] = s
    candidates = [c for c in candidates if c.get("_rerank_score", 0) >= threshold]
    if not candidates:
        return []  # caller will fall back to cosine order
    candidates.sort(key=lambda c: c.get("_rerank_score", 0), reverse=True)
    for c in candidates:
        c.pop("_rerank_score", None)
    return candidates


def _embed_query(text: str) -> list[float]:
    key = config.secret('NVIDIA_API_KEY')
    body = json.dumps({"model": EMBED_MODEL, "input": [text]}).encode()
    req = urllib.request.Request(
        EMBED_URL,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    return data["data"][0]["embedding"]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _fts_keyword_rows(conn, query: str, limit: int, *, min_endpoints: bool = False) -> list:
    """FTS5 pre-pass over name/desc/category/author, ordered by BM25.

    Shared by apinav_keyword_search (public tool) and the semantic-search
    candidate pre-pass so the query shape stays in one place. FTS5 treats '-'
    as a column separator, so hyphenated terms like 'sky-scrapper' are
    space-normalized to 'sky scrapper' (both tokens then match).
    """
    safe_query = query.replace("-", " ").replace("_", " ")
    ep_clause = "AND a.endpoint_count > 0\n" if min_endpoints else ""
    return conn.execute(
        f"""SELECT a.* FROM apis_fts f
              JOIN apis a ON a.rowid = f.rowid
              WHERE apis_fts MATCH ?
                {ep_clause}
              ORDER BY bm25(apis_fts)
              LIMIT ?""",
        (safe_query, limit),
    ).fetchall()


def _api_summary(row) -> dict:
    from plugins import build_links
    d = {
        "id": row["id"],
        "name": row["name"],
        "description": (row["description"] or "")[:300],
        "slug": row["slug"],
        "pricing": row["pricing"],
        "category": row["category"],
        "popularity": row["popularity"],
        "latency_ms": row["latency_ms"],
        "success_rate": row["success_rate"],
        "author": row["author"],
        "endpoint_count": row["endpoint_count"],
        "source": row["source"],
    }
    # Resolve docs/spec links from the owning plugin.
    try:
        links = build_links(dict(row))
        d["links"] = {k: v for k, v in links.items() if v}
    except Exception:
        pass
    return d


def _node_summary(node: dict) -> dict:
    """Summary from a live plugin node dict.

    Live plugins normalize to the apinav shape with quality scores as TOP-LEVEL
    keys (`popularity`, `latency_ms`, `success_rate`, `author`) — see each
    plugin's _normalize. This is deliberately flat (unlike the apis table's
    nested score/user, which only exists for stored rows) so live and cached
    results expose an identical result shape.
    """
    return {
        "id": node.get("id"),
        "name": node.get("name"),
        "description": (node.get("description") or "")[:300],
        "slug": node.get("slugifiedName"),
        "pricing": node.get("pricing"),
        "category": node.get("categoryName"),
        "popularity": node.get("popularity"),
        "latency_ms": node.get("latency_ms"),
        "success_rate": node.get("success_rate"),
        "author": node.get("author"),
        "endpoint_count": node.get("endpoint_count"),
        "source": node.get("source") or "rapidapi",
        "raw": node.get("raw"),
        # apis.io display fields (not persisted).
        "provider_name": node.get("provider_name"),
        "baseURL": node.get("baseURL"),
        "humanURL": node.get("humanURL"),
        "live": True,
    }


def _embed_apis(api_ids: list[str]) -> int:
    """Embed a list of API ids (by id) with NVIDIA. Returns count embedded."""
    if not api_ids:
        return 0
    key = config.secret('NVIDIA_API_KEY')
    conn = schema.get_conn()
    import embed_matrix
    done = 0
    # Batch (NVIDIA limits per-request size) and append each batch to the numpy
    # cache as we go, instead of a full 16s rebuild on the next search.
    for i in range(0, len(api_ids), 32):
        batch_ids = api_ids[i : i + 32]
        rows = conn.execute(
            f"SELECT id, name, description, category FROM apis WHERE id IN ({','.join('?'*len(batch_ids))})",
            batch_ids,
        ).fetchall()
        if not rows:
            continue
        # Compose embed text via the shared helper (applies the MAX_DESC_CHARS
        # truncation that prevents HTTP 400 on huge descriptions).
        texts = [embed_matrix._api_text(dict(r)) for r in rows]
        body = json.dumps({"model": EMBED_MODEL, "input": texts}).encode()
        req = urllib.request.Request(
            EMBED_URL,
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
            by_index = {d["index"]: d["embedding"] for d in data["data"]}
            conn.execute("BEGIN")
            for idx, api_id in enumerate(batch_ids):
                if idx in by_index:
                    schema.set_embedding(conn, api_id, EMBED_MODEL, by_index[idx])
                    done += 1
            conn.commit()
            embed_matrix.append(conn, batch_ids)
        except Exception:
            pass
    conn.close()
    return done
@server.tool(
    name="apinav_keyword_search",
    description=(
        "Search the local API catalog by keyword (FTS5 over name, description, "
        "category, author). query: the search terms. limit: max results (default 10). "
        "Use this to find APIs by exact words, e.g. 'flight', 'weather', 'sms', "
        "'translation'. Returns matching APIs with name, category, pricing, scores. "
        "For natural-language or fuzzy intent use apinav_semantic_search instead."
    ),
)
async def apinav_keyword_search(query: str, limit: int = 10) -> str:
    try:
        conn = schema.get_conn()
        rows = _fts_keyword_rows(conn, query, limit)
        conn.close()
        return json.dumps([_api_summary(r) for r in rows])
    except Exception as e:
        return json.dumps({"error": str(e)})


@server.tool(
    name="apinav_semantic_search",
    description=(
        "Semantic search over the local API catalog. query: a natural-language "
        "description of the API you want, e.g. 'find me an API that checks flight "
        "prices' or 'an API to send SMS'. Embeds the query with NVIDIA "
        "nemotron-3-embed-1b and ranks stored vectors by cosine similarity. "
        "TRANSPARENTLY ALSO queries live catalog plugins in the background and merges "
        "the live results into the candidate pool before reranking (overlay only — "
        "novel results are persisted to the local catalog). limit: max results "
        "(default 10). Returns the most semantically relevant APIs with name, "
        "category, pricing, and a similarity score. Use this for fuzzy/intent-"
        "based discovery; use apinav_keyword_search for exact words."
    ),
)
async def apinav_semantic_search(query: str, limit: int = 10) -> str:
    try:
        t_start = time.time()
        qvec = _embed_query(query)
        t_embed = time.time()
        conn = schema.get_conn()
        # Use cached normalized embedding matrix for fast ranking.
        scored = None
        try:
            import numpy as np
            import embed_matrix as em
            mat, ids = em.load_fresh(conn)
            if mat is not None:
                qv = np.array(qvec, dtype=np.float32)
                qn = float(np.linalg.norm(qv))
                if qn > 0:
                    qv = qv / qn
                sims = np.asarray(mat @ qv).ravel()
                order = np.argsort(-sims)[: RERANK_TOP_N * 3]
                want_ids = [ids[int(i)] for i in order]
                by_id = {}
                for cs in range(0, len(want_ids), 50):
                    chunk = want_ids[cs:cs + 50]
                    qmarks = ",".join("?" * len(chunk))
                    for r in conn.execute(
                        f"SELECT * FROM apis WHERE id IN ({qmarks})", chunk
                    ):
                        by_id[r["id"]] = r
                picked = []
                for i in order:
                    if len(picked) >= RERANK_TOP_N:
                        break
                    r = by_id.get(ids[int(i)])
                    if r is None or _is_spam(r) or r["endpoint_count"] == 0:
                        continue
                    picked.append((float(sims[int(i)]), r))
                scored = picked
        except Exception:
            scored = None
        if scored is None:
            scored = []
            rows = conn.execute(
                """SELECT a.*, e.vector FROM apis a
                   JOIN embeddings e ON a.id = e.api_id"""
            ).fetchall()
            for r in rows:
                if _is_spam(r):
                    continue
                if r["endpoint_count"] == 0:
                    continue
                vec = json.loads(r["vector"])
                sim = _cosine(qvec, vec)
                scored.append((sim, r))
            scored.sort(key=lambda x: x[0], reverse=True)
        conn.close()
        t_cos = time.time()

        # Build the candidate pool from cosine similarity and FTS keyword search.
        # Cosine alone has a recall hole: bi-encoders rank keyword-stuffed
        # descriptions above terse real ones, so a legit row can sit at cosine
        # rank ~2000 while junk ranks top-8. FTS/bm25 recovers exact-term hits
        # the embeddings miss. Both paths feed the SAME rerank pool.
        candidates = []
        for sim, r in scored[:RERANK_TOP_N]:
            s = _api_summary(r)
            s["similarity"] = round(sim, 4)
            candidates.append(s)
        seen = {c["id"] for c in candidates}
        seen_names = {_dedup_key(c) for c in candidates}
        try:
            # Same FTS pre-pass as apinav_keyword_search, but filtered to rows
            # with endpoints (junk gate) added to the already-seen names.
            fts_rows = _fts_keyword_rows(conn, query, FTS_PREPASS_N, min_endpoints=True)
            fts_added = 0
            for r in fts_rows:
                if r["id"] in seen or _is_spam(r):
                    continue
                k = _dedup_key({"name": r["name"], "description": r["description"]})
                if k in seen_names:
                    continue
                s = _api_summary(r)
                candidates.append(s)
                seen.add(r["id"])
                seen_names.add(k)
                fts_added += 1
        except Exception:
            fts_added = 0
        conn.close()
        t_cand = time.time()

        # Merge live plugin results into the candidate pool BEFORE reranking,
        # so the cross-encoder judges the merged set on relevance and live hits
        # can win top slots. All sources are uniform plugins (auto-discovered
        # from plugins/, tuned in plugins.yaml). Overlay only — see below for
        # the organic-growth persistence of novel rows.
        live_merged = 0
        plugin_nodes = []
        try:
            import plugins
            for n in plugins.search_all(query):
                plugin_nodes.append(_node_summary(n))
        except Exception:
            pass
        live_merged = len(plugin_nodes)
        if plugin_nodes:
            for n in plugin_nodes:
                if n["id"] in seen or _is_spam(n):
                    continue
                if _direction_penalty(query, n) is not None:
                    continue
                k = _dedup_key(n)
                if k in seen_names:
                    continue
                try:
                    from plugins import build_links
                    links = build_links(n)
                    if any(links.values()):
                        n["links"] = {nk: v for nk, v in links.items() if v}
                except Exception:
                    pass
                seen_names.add(k)
                candidates.append(n)
                seen.add(n["id"])

        # Persist novel live rows to the local catalog (organic growth).
        # Best-effort: a lock or failure here never blocks the search — the
        # rows are still merged into the results, just not persisted yet.
        persisted = 0
        try:
            import sys as _sys
            _here = os.path.dirname(os.path.abspath(__file__))
            if _here not in _sys.path:
                _sys.path.insert(0, _here)
            import ingest as _ingest
            _ps = _ingest.persist_plugin_results(plugin_nodes)
            persisted = _ps.get("added", 0)
            if _ps.get("added_ids"):
                _ingest.embed_new_rows(_ps["added_ids"])
        except Exception:
            persisted = -1

        # Rerank the merged candidate pool with cross-encoder.
        t_merge_done = time.time()
        reranked = False
        try:
            candidates = _rerank(query, candidates)
            reranked = True
        except Exception:
            pass
        t_rerank_done = time.time()
        out = candidates[:limit]

        return json.dumps({
            "results": out,
            "reranked": reranked,
            "live_merged": live_merged,
            "persisted": persisted,
            "timing_ms": {
                "embed": round((t_embed - t_start) * 1000),
                "cosine": round((t_cos - t_embed) * 1000),
                "candidates": round((t_cand - t_cos) * 1000),
                "live_merge": round((t_merge_done - t_cand) * 1000),
                "rerank": round((t_rerank_done - t_merge_done) * 1000),
            },
            "note": "Live source results were merged into the candidate pool." if live_merged else "No new live APIs found.",
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


@server.tool(
    name="apinav_get_api",
    description=(
        "Fetch one API's full record from the local catalog by id or slug. "
        "identifier: the API id (e.g. 'api_...') or slugified name (e.g. 'sky-scrapper'). "
        "Returns the complete stored record including description, pricing, category, "
        "all quality scores, author, and last-updated time."
    ),
)
async def apinav_get_api(identifier: str) -> str:
    try:
        conn = schema.get_conn()
        row = conn.execute(
            "SELECT * FROM apis WHERE id=? OR slug=?", (identifier, identifier)
        ).fetchone()
        conn.close()
        if not row:
            return json.dumps({"error": f"no API found for '{identifier}'"})
        d = dict(row)
        d["raw"] = json.loads(d["raw"]) if d.get("raw") else None
        # Per-source docs/spec links from the owning plugin.
        try:
            from plugins import build_links
            d["links"] = {k: v for k, v in build_links(d).items() if v}
        except Exception:
            pass
        return json.dumps(d)
    except Exception as e:
        return json.dumps({"error": str(e)})


@server.tool(
    name="apinav_live_search",
    description=(
        "Search a live API source directly (not the local cache) by keyword. "
        "query: the search terms, e.g. 'flight prices' or 'airline comparison'. "
        "category: source name to query (auto-discovered from plugins/). Defaults to rapidapi. "
        "limit: max results to return (default 20). Results are a live overlay "
        "merged into the candidate pool — they are NOT persisted. Returns "
        "matching APIs with name, category, pricing, scores, and a live flag."
    ),
)
async def apinav_live_search(query: str, category: str | None = None, limit: int = 20) -> str:
    """Search a live API source directly (not the local cache) by keyword.

    category: source name to query. Available sources are the auto-discovered
              plugins in plugins/. Defaults to rapidapi.
    """
    try:
        import plugins
        mod = plugins.PLUGINS.get(category or "rapidapi")
        if mod is None:
            return json.dumps({
                "error": f"unknown live source '{category}'",
                "available": sorted(plugins.PLUGINS.keys()),
                "live": True,
            })
        nodes = mod.search(query, limit=limit)
        if not nodes:
            return json.dumps({"error": f"no live results for '{query}'", "source": category, "live": True})
        # Same shaping as semantic-search live results for a consistent shape.
        out = [_node_summary(n) for n in nodes]
        return json.dumps({
            "query": query,
            "category": category,
            "total_found": len(nodes),
            "results": out,
        })
    except Exception as e:
        return json.dumps({"error": str(e), "live": True})


@server.tool(
    name="apinav_catalog_stats",
    description=(
        "Report the local catalog index status: total APIs, how many are "
        "embedded for semantic search, and the last-updated timestamp. Use this to "
        "check whether the catalog is populated and how fresh it is before searching."
    ),
)
async def apinav_catalog_stats() -> str:
    try:
        conn = schema.get_conn()
        apis = schema.count_apis(conn)
        embedded = schema.count_embedded(conn)
        last = conn.execute("SELECT MAX(updated_at) AS u FROM apis").fetchone()["u"]
        conn.close()
        return json.dumps({
            "total_apis": apis,
            "embedded": embedded,
            "embedding_model": EMBED_MODEL,
            "last_updated": last,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def main() -> None:
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
