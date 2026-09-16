"""apis.io plugin (live overlay, self-contained).

BM25-style relevance over ~133.6k APIs, agent-permitted (robots.txt + terms),
no auth, plain HTTP. Per-query only: curated /search?q= first (brand-tolerant
— bare brand names like 'moomoo' resolve to the right API where the literal
list search returns 0), literal /apis?q= as extension. Nodes carry
aio_rank = 0-based position in apis.io's curated ranking — downstream rerank
uses it as a small additive boost: the cross-encoder only sees the stored
text (which says 'Futu', not 'Moomoo') and would threshold the bridge away;
their ranking IS the brand-bridge signal.

Novel results are persisted by ingest.py (organic growth); re-sights are
cheap id skips.
"""
import sys, os
sys.path.insert(0, __import__('os').path.dirname(__file__))
import base

SOURCE = "apisio"
BASE = "https://apis.io/api/v1"


def _search_curated(query: str, limit: int = 10) -> list[dict]:
    """PRIMARY path: /search?q= — their curated/semantic search."""
    d = base.get(f"{BASE}/search?query=&q=" + base.enc(query), SOURCE)
    nodes = []
    for i, t in enumerate((d.get("apis", {}).get("top") or [])[:limit]):
        n = _normalize(t)
        n["aio_rank"] = i
        nodes.append(n)
    return nodes


def _search_full(query: str, limit: int = 25) -> list[dict]:
    """EXTENSION path: /apis?q= literal-match full list (paginated).

    Appends more candidates when curated search returns fewer than the
    overlay wants — literal match only, 0 for brand names."""
    d = base.get(f"{BASE}/apis?q=" + base.enc(query) + f"&limit={min(limit,100)}", SOURCE)
    return [_normalize(r) for r in d.get("data", [])]


def get_api(aid: str) -> dict | None:
    """Detail fetch: adds `properties` (spec URLs) to the normalized node.

    NOTE: the aid must go in the path UNQUOTED — the colon is a path segment
    separator their router expects raw (quote() 404s, verified 2026-09-11)."""
    try:
        d = base.get(f"{BASE}/apis/{aid}", SOURCE)
        node = _normalize(d)
        props = d.get("properties") or []
        spec = next((p["url"] for p in props if p.get("type") == "OpenAPI" and p.get("url")), None)
        if spec:
            node["spec_url"] = spec
        return node
    except Exception:
        return None


def _normalize(r: dict) -> dict:
    """Map an apis.io row to the apinav node dict shape (schema.upsert_api keys)."""
    desc = r.get("description") or ""
    # 'The Balances API from Airwallex — 3 operation(s) for beneficiaries.'
    ep_count = None
    marker = "operation(s)"
    if marker in desc:
        try:
            head = desc.split(marker)[0]
            ep_count = int(head.split()[-1])
        except (ValueError, IndexError):
            ep_count = None
    # Fallback: artifact_count hints at least one machine-readable artifact
    if ep_count is None:
        ep_count = 1 if r.get("artifact_count") else -1
    return {
        "id": r.get("aid"),
        "name": r.get("name"),
        "description": desc,
        "slugifiedName": r.get("slug"),
        "pricing": None,
        "categoryName": (r.get("tags") or [None])[0],
        "updatedAt": None,
        "endpoint_count": ep_count,
        "source": SOURCE,
        # extras kept out of the SQL row, used by link resolution + display
        "provider_name": r.get("provider_name"),
        "provider_slug": r.get("provider_slug"),
        "baseURL": r.get("baseURL"),
        "humanURL": r.get("humanURL"),
    }


def search(query: str, limit: int = 5) -> list[dict]:
    out = []
    seen: set = set()
    try:
        for x in _search_curated(query, limit=max(limit, 8)):
            if x.get("id") and x["id"] not in seen:
                seen.add(x["id"])
                out.append(x)
    except base.RateLimited:
        raise
    except Exception:
        pass
    try:
        for x in _search_full(query, limit=max(limit, 10)):
            if x.get("id") and x["id"] not in seen:
                seen.add(x["id"])
                out.append(x)
    except Exception:
        pass
    return out[:limit]


def build_links(row: dict) -> dict:
    """apis.io rows carry humanURL (docs) and baseURL (spec endpoint base)."""
    return {
        "url_docs": row.get("humanURL") or row.get("baseURL"),
        "url_spec_json": row.get("spec_url"),
        "url_spec_yaml": None,
    }
