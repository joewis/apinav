"""apis.io plugin (live overlay, never persisted).

BM25-style relevance over ~133.6k APIs, agent-permitted (robots.txt + terms),
no auth, plain HTTP. Curated /search?q= first (brand-tolerant), literal
/apis?q= as extension. Nodes carry aio_rank = 0-based position in apis.io's
curated ranking — downstream rerank uses it as a small additive boost.
"""
import sys, os
sys.path.insert(0, __import__('os').path.dirname(__file__))
sys.path.insert(0, __import__('os').path.dirname(__file__) + "/..")
import base

SOURCE = "apisio"


def search(query: str, limit: int = 5) -> list[dict]:
    out = []
    seen: set = set()
    try:
        import apisio_client as aio
    except Exception:
        return []
    try:
        for x in aio.search_curated(query, limit=max(limit, 8)):
            if x.get("id") and x["id"] not in seen:
                seen.add(x["id"])
                out.append(_normalize(x))
    except base.RateLimited:
        raise
    except Exception:
        pass
    try:
        for x in aio.search_full(query, limit=max(limit, 10)):
            if x.get("id") and x["id"] not in seen:
                seen.add(x["id"])
                out.append(_normalize(x))
    except Exception:
        pass
    return out[:limit]


def _normalize(x: dict) -> dict:
    n = dict(x)
    n["source"] = SOURCE
    if n.get("id") and not str(n["id"]).startswith(SOURCE + ":"):
        pass  # apisio ids are already globally unique (aid), keep as-is
    return n
