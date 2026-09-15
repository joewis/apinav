"""Marketplace catalog plugin (live overlay).

Per-query search over a large commercial API directory, via an optional local
session client. Results are normalized to the uniform plugin row shape and
merged into the candidate pool before reranking; novel ids are persisted by
ingest.py (organic growth). No batch jobs: only what a search asks for.

Quality signals: popularity score, latency, success rate (when provided).
Docs link = source catalog page when resolvable by source_links.
"""
import sys, os
sys.path.insert(0, __import__('os').path.dirname(__file__))
import base

SOURCE = "rapidapi"


def search(query: str, limit: int = 5) -> list[dict]:
    # The live client (browser-session based) is an optional local dependency;
    # if unavailable the plugin reports empty rather than raising, matching the
    # "one dead plugin never takes the others down" rule.
    try:
        import marketplace_client as rc  # local private module, not part of this repo
    except Exception:
        return []
    tab_id = None
    try:
        tab_id = rc.open_tab()
        try:
            if rc.check_rate_limit(tab_id) >= 400:
                raise base.RateLimited(None, SOURCE)
        except base.RateLimited:
            raise
        except Exception:
            pass  # rate-limit probe is best-effort; fetch_all handles 429 itself
        nodes = rc.fetch_all(tab_id, term=query)
        return [_normalize(a) for a in nodes[: max(limit, 1)]]
    finally:
        if tab_id:
            rc.close_tab(tab_id)


def _normalize(a: dict) -> dict:
    score = a.get("score") or {}
    user = a.get("user") or {}
    return {
        "id": f"{SOURCE}:{a.get('id')}" if a.get("id") else f"{SOURCE}:{a.get('slugifiedName')}",
        "name": a.get("name"),
        "description": a.get("description") or "",
        "slugifiedName": a.get("slugifiedName"),
        "pricing": a.get("pricing"),
        "categoryName": a.get("categoryName"),
        "updatedAt": a.get("updatedAt"),
        "endpoint_count": -1,
        "source": SOURCE,
        "author": user.get("name") or user.get("username"),
        "popularity": score.get("popularityScore"),
        "latency_ms": score.get("avgLatency"),
        "success_rate": score.get("avgSuccessRate"),
    }