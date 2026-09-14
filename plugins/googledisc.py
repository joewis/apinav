"""Google API Discovery plugin (live overlay, never persisted).

www.googleapis.com/discovery/v1/apis — one static directory fetch (531
official Google APIs, verified 2026-09-11), each item carries title,
description, and a working discoveryRestUrl. No server-side search, so
this plugin fetches the directory (cached in-process, TTL) and filters
by keyword in-process — same overlay shape, zero persistence.
Docs link = discoveryRestUrl is the machine spec; documentationLink is
the human docs (both present on items).
"""
import sys, os
sys.path.insert(0, __import__('os').path.dirname(__file__))
import base

BASE = "https://www.googleapis.com/discovery/v1/apis"
SOURCE = "googledisc"
_TTL = 24 * 3600  # directory is static; refetch daily at most
_cache = {"fetched": 0.0, "items": []}


def _directory() -> list[dict]:
    import time
    if time.time() - _cache["fetched"] < _TTL and _cache["items"]:
        return _cache["items"]
    d = base.get(BASE, SOURCE)
    _cache["items"] = d.get("items") or []
    _cache["fetched"] = time.time()
    return _cache["items"]


def search(query: str, limit: int = 5) -> list[dict]:
    terms = [t for t in (query or "").lower().replace("-", " ").split() if len(t) > 2]
    out = []
    for it in _directory():
        blob = f"{it.get('title','')} {it.get('description','')} {it.get('name','')}".lower()
        if terms and not any(t in blob for t in terms):
            continue
        out.append(_normalize(it))
        if len(out) >= limit:
            break
    return out


def _normalize(it: dict) -> dict:
    return {
        "id": f"googledisc:{it.get('name')}:{it.get('version')}",
        "name": it.get("title") or it.get("name"),
        "description": it.get("description") or "",
        "slugifiedName": it.get("name"),
        "pricing": None,
        "categoryName": "Google API",
        "updatedAt": None,
        # Google APIs are real REST APIs; discovery docs list resources
        "endpoint_count": -1,
        "source": SOURCE,
        "preferred": it.get("preferred"),
        "author": None,  # _is_spam reads row['author'] on every candidate
        # docs = documentationLink; spec = discoveryRestUrl
        "humanURL": it.get("documentationLink"),
        "spec_url": it.get("discoveryRestUrl"),
    }