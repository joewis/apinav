"""Apify Store plugin (live overlay).

api.apify.com/v2/store?search=... — open, no auth, JSON (verified
2026-09-11: 5,131 searchable Actors, rich stats). Actors are automation /
execution jobs, NOT REST APIs — rows carry mcp_type='apify-actor' so
downstream agents annotate them correctly. Quality signal: stats.
Docs link = actor's store URL built from username/name (stable pattern).
"""
import sys, os
sys.path.insert(0, __import__('os').path.dirname(__file__))
import base

BASE = "https://api.apify.com/v2/store"
SOURCE = "apify"


def search(query: str, limit: int = 5) -> list[dict]:
    d = base.get(f"{BASE}?search={base.enc(query)}&limit={min(limit, 10)}", SOURCE)
    return [_normalize(a) for a in d.get("data", {}).get("items") or []]


def _normalize(a: dict) -> dict:
    username = a.get("username") or "apify"
    name = a.get("name") or ""
    stats = a.get("stats") or {}
    return {
        "id": f"apify:{username}/{name}",
        "name": a.get("title") or name,
        "description": a.get("description") or "",
        "slugifiedName": name,
        "pricing": "pay-per-result",
        "categoryName": (a.get("categories") or [None])[0] or "Apify Actor",
        "updatedAt": a.get("modifiedAt"),
        "endpoint_count": -1,  # execution class, not REST
        "source": SOURCE,
        "mcp_type": "apify-actor",
        "author": username,
        "total_runs": stats.get("totalRuns"),
        "humanURL": f"https://apify.com/{username}/{name}",
    }