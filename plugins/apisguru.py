"""APIs.guru plugin (live overlay).

https://api.apis.guru/v2/list.json — one fetch returns ~2,500 APIs with
title, description, category, and a preferred version carrying swaggerUrl.
No auth, plain HTTP, agent-permitted.

This plugin follows the uniform live-plugin contract:
- per-query fetch only
- polite pacing via plugins.base
- normalized to the apinav node shape
- results merged before rerank; novel ids persisted by ingest.py

Endpoint counts require parsing the OpenAPI spec. To keep per-query latency
bounded, we count endpoints only for the top `limit` candidates that pass
keyword filtering. Zero-endpoint entries are dropped (junk gate).
"""
import sys, os
sys.path.insert(0, __import__('os').path.dirname(__file__))
import base

BASE = "https://api.apis.guru/v2"
SOURCE = "apisguru"
_LIST_TTL = 3600  # list.json changes slowly; cache 1 hour
_cache = {"fetched": 0.0, "list": {}}

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}


def _count_endpoints(spec: dict) -> int:
    """Count operations in an OpenAPI spec's paths."""
    paths = spec.get("paths") or {}
    n = 0
    for _path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for m in methods:
            if m.lower() in _HTTP_METHODS:
                n += 1
    return n


def _list() -> dict:
    """Fetch (or return cached) APIs.guru list.json mapping api_key -> entry."""
    import time
    if time.time() - _cache["fetched"] < _LIST_TTL and _cache["list"]:
        return _cache["list"]
    d = base.get(f"{BASE}/list.json", SOURCE)
    _cache["list"] = d or {}
    _cache["fetched"] = time.time()
    return _cache["list"]


def _entry_info(api_key: str, entry: dict) -> dict:
    """Extract title/description/category from the preferred version."""
    versions = entry.get("versions") or {}
    pref = entry.get("preferred")
    ver_key = pref if pref in versions else (next(iter(versions), None))
    if not ver_key:
        return {}
    ver = versions[ver_key]
    info = ver.get("info") or {}
    categories = info.get("x-apisguru-categories") or []
    return {
        "version_key": ver_key,
        "version": ver,
        "title": info.get("title") or api_key,
        "description": info.get("description") or "",
        "category": categories[0] if categories else None,
        "updated": ver.get("updated"),
        "swagger_url": ver.get("swaggerUrl") or "",
        "swagger_yaml_url": ver.get("swaggerYamlUrl") or "",
    }


def _normalize(api_key: str, entry: dict, info: dict, endpoint_count: int = -1) -> dict:
    """Map an APIs.guru entry to the apinav node dict shape."""
    return {
        "id": api_key,
        "name": info.get("title") or api_key,
        "description": info.get("description") or "",
        "slugifiedName": api_key,
        "pricing": None,
        "categoryName": info.get("category"),
        "updatedAt": info.get("updated"),
        "endpoint_count": endpoint_count,
        "source": SOURCE,
        "author": api_key.split(":")[0] if ":" in api_key else None,
        # Extras for source_links.py (it already has an apisguru fast path)
        "humanURL": info.get("swagger_url"),
        "spec_url": info.get("swagger_url"),
        "swaggerYamlUrl": info.get("swagger_yaml_url"),
    }


def search(query: str, limit: int = 5) -> list[dict]:
    """Live keyword search over APIs.guru directory.

    Fetches list.json once (cached), filters in-process, then fetches specs
    for the top candidates to count endpoints. Zero-endpoint candidates are
    dropped. Bounded to `limit` spec fetches per query.
    """
    catalog = _list()
    if not catalog:
        return []

    terms = [t for t in (query or "").lower().replace("-", " ").replace(".", " ").split() if len(t) > 2]
    matches = []

    for api_key, entry in catalog.items():
        info = _entry_info(api_key, entry)
        if not info:
            continue
        blob = f"{api_key} {info.get('title', '')} {info.get('description', '')} {info.get('category', '')}".lower()
        if terms and not any(t in blob for t in terms):
            continue
        matches.append((api_key, entry, info))

    # Sort by title length as a crude relevance proxy (shorter = more likely exact match)
    matches.sort(key=lambda m: len(m[2].get("title") or m[0]))

    out = []
    for api_key, entry, info in matches:
        if len(out) >= limit:
            break
        ep_count = -1
        swagger_url = info.get("swagger_url") or ""
        if swagger_url:
            try:
                spec = base.get(swagger_url, SOURCE)
                ep_count = _count_endpoints(spec)
            except Exception:
                ep_count = -1
        if ep_count == 0:
            continue
        out.append(_normalize(api_key, entry, info, ep_count))

    return out


def get_api(aid: str) -> dict | None:
    """Detail fetch for a single APIs.guru entry.

    Returns the normalized node with an accurate endpoint count from the spec.
    """
    catalog = _list()
    entry = catalog.get(aid)
    if not entry:
        return None
    info = _entry_info(aid, entry)
    if not info:
        return None
    ep_count = -1
    swagger_url = info.get("swagger_url") or ""
    if swagger_url:
        try:
            spec = base.get(swagger_url, SOURCE)
            ep_count = _count_endpoints(spec)
        except Exception:
            ep_count = -1
    return _normalize(aid, entry, info, ep_count)
