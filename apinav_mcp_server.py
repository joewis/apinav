#!/home/carl/mcp-gateway-venv/bin/python
"""MCP server exposing the local multi-source API catalog index (apinav).

Sources: live directory plugins (provenance in the `source` column).

Tools:
- apinav_keyword_search: FTS5 keyword search over name/description/category
- apinav_semantic_search: cosine-similarity semantic search (embeds the query
  with NVIDIA nemotron-3-embed-1b, then ranks stored vectors)
- apinav_get_api: fetch one API's full record by id or slug
- apinav_live_search: live source search + merge
- apinav_catalog_stats: counts + freshness

Shared capability for all agents through the gateway. Reads NVIDIA_API_KEY from
~/.hermes/.env for query embedding.
"""
import asyncio
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, "/home/carl/apinav")
import schema
from spam import is_spam as _is_spam

from mcp.server.mcpserver import MCPServer

server = MCPServer("apinav-mcp", "1.0.0")

EMBED_URL = "https://integrate.api.nvidia.com/v1/embeddings"
EMBED_MODEL = "nvidia/nemotron-3-embed-1b"
ENV_PATH = "/home/carl/.hermes/.env"

# Cross-encoder reranker via OpenRouter (Joerg-directed 2026-09-11: x380 is
# not server-grade; prefer the cloud model). llama-nemotron-rerank-vl-1b-v2:free
# — 1.7B cross-encoder, Cohere-shape /rerank API, benchmarked 2026-09-11:
# top-1 agreement 4/5 with bge-reranker, clean junk separation (real 0.27-0.68
# vs junk <=0.02). Latency ~0.7-1s at 100-130 docs. Junk floor ~0.02 →
# threshold 0.02 (replaces bge's 0.0005). x380 kept as FALLBACK only.
OR_RERANK_URL = "https://openrouter.ai/api/v1/rerank"
OR_RERANK_MODEL = "nvidia/llama-nemotron-rerank-vl-1b-v2:free"
X380_RERANK_URL = "http://192.168.18.22:8080/v1/rerank"
RERANK_TOP_N = 100  # retrieve this many candidates, then cross-encoder rerank
# FTS/bm25 pre-pass: extra local candidates fed into the SAME rerank pool,
# catching exact-term hits the bi-encoder cosine misses (rank-2085 TTS row
# case). Kept modest — the reranker costs ~30ms/doc on x380.
FTS_PREPASS_N = 30


# --- Near-duplicate suppression (UAT 2026-09-10, Kiko) ----------------------
# Catalog has many clone entries (same name+description, different slug or
# author). Without dedup one bad match can occupy several top-10 slots —
# e.g. five identical "Audio File to Text Converter" rows filled positions
# 4-10 of a TTS query. Dedupe on normalized (name, description-head), keeping
# the first occurrence (highest cosine, since candidates arrive in cosine
# order). Distinct APIs that share a name but differ in description survive.

# Some source results carry <em> highlight tags inside name/category/description
# (e.g. "<em>Speech</em>2<em>Text</em>"). Strip them before any matching —
# they break regexes (direction guard) and dedup normalization alike.

def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "")

def _dedup_key(c: dict) -> str:
    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", _strip_tags(s).lower())
    return _norm(c.get("name")) + "|" + _norm((c.get("description") or "")[:120])


# --- Audio direction guard (UAT 2026-09-10, Kiko) --------------------------
# bge-reranker-base can't reliably separate "text to speech" from "speech to
# text" (too many shared tokens). Penalize candidates whose audio direction
# contradicts the query's; the zeroed score drops them below the relevance
# threshold. Direction is judged on the NAME (descriptions may legitimately
# mention both directions for a converter API).

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

def _load_or_key() -> str:
    with open(ENV_PATH) as f:
        for line in f:
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("OPENROUTER_API_KEY not found")


def _rerank_openrouter(query: str, documents: list[str]) -> list[float]:
    """Rerank via OpenRouter (Cohere shape) → relevance scores by index.

    PRIMARY reranker (Joerg 2026-09-11). Returns scores aligned with
    `documents` order. Raises on failure — caller decides the fallback.
    """
    body = json.dumps({
        "model": OR_RERANK_MODEL,
        "query": query,
        "documents": documents,
    }).encode()
    req = urllib.request.Request(
        OR_RERANK_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {_load_or_key()}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    # OR returns [{"index": i, "relevance_score": s}, ...] — unranked order
    scores = [0.0] * len(documents)
    for x in data.get("results", []):
        i = x.get("index")
        if isinstance(i, int) and 0 <= i < len(documents):
            scores[i] = float(x.get("relevance_score") or 0.0)
    return scores


def _rerank_x380(query: str, documents: list[str]) -> list[float]:
    """Rerank via the x380 LAN GPU (bge-reranker-base). FALLBACK path."""
    body = json.dumps({"query": query, "documents": documents}).encode()
    req = urllib.request.Request(
        X380_RERANK_URL,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=900) as r:
        data = json.loads(r.read())
    return [float(s) for s in data.get("result", {}).get("scores", [])]


def _rerank(query: str, candidates: list[dict]) -> list[dict]:
    """Rerank candidate API summaries by cross-encoder relevance.

    PRIMARY: OpenRouter nemotron-rerank (Joerg-directed; x380 not server
    grade). FALLBACK: x380 bge-reranker on the LAN. LAST resort: unchanged
    (cosine order). Threshold differs per backend: OR junk floor ~0.02,
    bge junk floor ~0.0005 — scored below threshold are dropped.
    """
    if not candidates:
        return candidates
    # Near-duplicate suppression BEFORE reranking
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
        # Compose a single doc string for the cross-encoder (tags stripped:
        # they'd otherwise be scored as literal tokens)
        name = _strip_tags(c.get("name") or "")
        cat = _strip_tags(c.get("category") or "")
        desc = _strip_tags(c.get("description") or "")[:200]
        documents.append(f"{name} | {cat} | {desc}")
    # --- PRIMARY: OpenRouter, then x380 fallback, then no-rerank ----------
    scores = None
    threshold = 0.02
    try:
        scores = _rerank_openrouter(query, documents)
    except Exception:
        try:
            scores = _rerank_x380(query, documents)
            threshold = 0.0005  # bge junk floor
        except Exception:
            return candidates  # keep cosine order
    if scores is None or len(scores) != len(candidates):
        return candidates
    for c, score in zip(candidates, scores):
        s = float(score)
        # Opposite-direction audio candidates: zero out so the threshold drops them
        if _direction_penalty(query, c) is not None:
            s = 0.0
        # apis.io curated-rank boost: their /search already ranked this node
        # for the query (brand-bridge cases like moomoo→Futu where the doc
        # text can't show the association). Small additive bonus, decaying
        # by rank: rank0=+0.06 … rank5=+0.01. Capped under the smallest real
        # OR score (0.27 in benchmarks) so it reorders junk-tail only, never
        # promotes overlay rows above genuinely-scored real matches.
        if c.get("aio_rank") is not None:
            s += 0.06 / (1 + int(c["aio_rank"]))
            c["aio_boosted"] = True
        c["_rerank_score"] = s
    # Drop weak matches (threshold per backend, set above)
    candidates = [c for c in candidates if c.get("_rerank_score", 0) >= threshold]
    if not candidates:
        return []  # caller will fall back to cosine order
    candidates.sort(key=lambda c: c.get("_rerank_score", 0), reverse=True)
    for c in candidates:
        c.pop("_rerank_score", None)
    return candidates


def _load_key() -> str:
    with open(ENV_PATH) as f:
        for line in f:
            if line.startswith("NVIDIA_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("NVIDIA_API_KEY not found")


def _embed_query(text: str) -> list[float]:
    key = _load_key()
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
    # Resolve docs/spec links from the owning plugin. Missing links stay absent.
    try:
        links = build_links(dict(row))
        d["links"] = {k: v for k, v in links.items() if v}
    except Exception:
        pass
    return d


def _node_summary(node: dict) -> dict:
    """Summary from a live node dict (any plugin source)."""
    score = node.get("score") or {}
    user = node.get("user") or {}
    return {
        "id": node.get("id"),
        "name": node.get("name"),
        "description": (node.get("description") or "")[:300],
        "slug": node.get("slugifiedName"),
        "pricing": node.get("pricing"),
        "category": node.get("categoryName"),
        "popularity": score.get("popularityScore"),
        "latency_ms": score.get("avgLatency"),
        "success_rate": score.get("avgSuccessRate"),
        "author": user.get("name") or user.get("username"),
        "endpoint_count": node.get("endpoint_count"),
        "source": node.get("source") or "rapidapi",
        "raw": node.get("raw"),
        # apis.io extras (display + live link resolution; NOT persisted)
        "provider_name": node.get("provider_name"),
        "baseURL": node.get("baseURL"),
        "humanURL": node.get("humanURL"),
        # curated-search rank (brand-bridge boost in _rerank; overlay only)
        "aio_rank": node.get("aio_rank"),
        "live": True,
    }


def _embed_apis(api_ids: list[str]) -> int:
    """Embed a list of API ids (by id) with NVIDIA. Returns count embedded."""
    if not api_ids:
        return 0
    key = _load_key()
    conn = schema.get_conn()
    done = 0
    # batch by 32
    for i in range(0, len(api_ids), 32):
        batch_ids = api_ids[i : i + 32]
        rows = conn.execute(
            f"SELECT id, name, description, category FROM apis WHERE id IN ({','.join('?'*len(batch_ids))})",
            batch_ids,
        ).fetchall()
        if not rows:
            continue
        texts = []
        for r in rows:
            parts = [r["name"] or ""]
            if r["description"]:
                parts.append(r["description"])
            if r["category"]:
                parts.append(r["category"])
            texts.append("\n".join(parts))
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
            # Incremental cache growth: add the new vectors to the numpy
            # matrix in milliseconds instead of a 16s full rebuild on the
            # next search (catalog-publish path — frequent additions).
            import embed_matrix
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
        # FTS5 treats '-' as a column separator in queries, which breaks
        # hyphenated terms like 'sky-scrapper'. Replace hyphens with spaces
        # so 'sky-scrapper' -> 'sky scrapper' (both tokens match).
        safe_query = query.replace("-", " ").replace("_", " ")
        conn = schema.get_conn()
        rows = conn.execute(
            """SELECT a.* FROM apis_fts f
               JOIN apis a ON a.rowid = f.rowid
               WHERE apis_fts MATCH ?
               ORDER BY bm25(apis_fts)
               LIMIT ?""",
            (safe_query, limit),
        ).fetchall()
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
        # Fast path: cached normalized embedding matrix (numpy) mirroring the
        # embeddings table — stable while the endpoint backfill runs (it only
        # mutates `apis`). Stale/missing cache is rebuilt inline; any failure
        # falls back to the pure-Python scan (~31s for 46k rows).
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
                        continue  # deleted since cache build, spam, or junk
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

        # --- 1b. Build the candidate pool (top-N cosine + FTS pre-pass) ------
        # Cosine alone has a recall hole (instrumented 2026-09-11): the DB's
        # own "Text to Speech" row sat at cosine rank 2085 while junk ranked
        # top-8 — bi-encoders rank keyword-stuffed descriptions above terse
        # real ones. FTS/bm25 catches exact-term hits the embeddings miss;
        # the cross-encoder (which scores local TTS 0.877 vs junk 0.004)
        # then judges the union. Both paths feed the SAME rerank pool.
        candidates = []
        for sim, r in scored[:RERANK_TOP_N]:
            s = _api_summary(r)
            s["similarity"] = round(sim, 4)
            candidates.append(s)
        seen = {c["id"] for c in candidates}
        seen_names = {_dedup_key(c) for c in candidates}
        try:
            safe_query = query.replace("-", " ").replace("_", " ")
            fts_rows = conn.execute(
                """SELECT a.* FROM apis_fts f
                   JOIN apis a ON a.rowid = f.rowid
                   WHERE apis_fts MATCH ?
                     AND a.endpoint_count > 0
                   ORDER BY bm25(apis_fts)
                   LIMIT ?""",
                (safe_query, FTS_PREPASS_N),
            ).fetchall()
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

        # --- 2. MERGE FIRST (user-mandated ordering) ------------------------
        # Live search runs BEFORE reranking, so the cross-encoder scores the
        # merged pool — live results compete for top slots on relevance instead
        # of being appended unranked to the tail. All live sources are uniform
        # plugins (see 2b): per-query overlays; novel ids are persisted by ingest.py.
        live_merged = 0
        # --- 2b. LIVE PLUGINS -------------------------------------------------
        # All live sources are plugins with a uniform search() shape: per-query
        # overlays — fetched, guarded, links resolved live, merged into the
        # candidate pool BEFORE rerank — never persisted.
        plugin_nodes = []
        # ALL live sources are uniform plugins: auto-discovered from plugins/,
        # each exports SOURCE + search(query, limit); tuning lives in plugins.yaml.
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
                    # live plugin summaries carry their docs URL on the
                    # node (humanURL) — the owning plugin resolves links.
                    links = build_links(n)
                    if any(links.values()):
                        n["links"] = {nk: v for nk, v in links.items() if v}
                except Exception:
                    pass
                seen_names.add(k)
                candidates.append(n)
                seen.add(n["id"])

        # --- 2c. ORGANIC GROWTH: persist novel live rows (Joerg 2026-09-15) --
        # Live results that passed the gates AND were never seen before are
        # persisted to the local catalog so it grows during usage. Best-effort:
        # lock/failure never blocks the query. Re-sights (known ids) are cheap
        # skips inside ingest.persist_plugin_results.
        persisted = 0
        try:
            import sys as _sys
            if "/home/carl/apinav" not in _sys.path:
                _sys.path.insert(0, "/home/carl/apinav")
            import ingest as _ingest
            _ps = _ingest.persist_plugin_results(plugin_nodes)
            persisted = _ps.get("added", 0)
            if _ps.get("added_ids"):
                # embed in-line (fast, batches of 32); failure leaves them
                # un-embedded and invisible to semantic search until backfill
                _ingest.embed_new_rows(_ps["added_ids"])
        except Exception:
            persisted = -1  # signal: persist errored, search unaffected

        # --- 3. RERANK LAST: cross-encoder over the merged pool -------------
        t_merge_done = time.time()
        reranked = False
        try:
            candidates = _rerank(query, candidates)
            reranked = True
        except Exception:
            pass  # fallback to cosine order
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
        out = []
        for n in nodes:
            out.append({
                "id": n.get("id"),
                "name": n.get("name"),
                "description": (n.get("description") or "")[:300],
                "slug": n.get("slugifiedName"),
                "pricing": n.get("pricing"),
                "category": n.get("categoryName"),
                "popularity": n.get("popularity"),
                "latency_ms": n.get("latency_ms"),
                "success_rate": n.get("success_rate"),
                "author": n.get("author"),
                "endpoint_count": n.get("endpoint_count"),
                "live": True,
            })
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
