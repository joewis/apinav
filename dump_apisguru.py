#!/usr/bin/env python3
"""Dump the APIs.guru catalog into SQLite, tagged source='apisguru'.

APIs.guru is a clean plain-HTTP REST source:
- `https://api.apis.guru/v2/list.json` — one fetch returns all 2,529 APIs
  with title/description/category (in the preferred version's `info`).
- Each API's OpenAPI spec (`swaggerUrl`) contains the `paths` — we count
  operations (get/post/put/delete/patch) as the endpoint count. This feeds
  the endpoint-count filter directly, with NO per-API detail query (unlike
  the marketplace).

Mirrors the marketplace dump pattern: resumable (upsert by id), polite
rate-limit, skips zero-endpoint entries (junk gate).

Usage:
    python3 dump_apisguru.py            # full dump
    python3 dump_apisguru.py --limit 20 # test on 20 APIs
"""
import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema

LIST_URL = "https://api.apis.guru/v2/list.json"
BASE_DELAY = 0.15  # polite delay between spec fetches
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Elitedesk-API-Search"}

# HTTP methods that count as an endpoint operation
_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}


def http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def count_endpoints(spec: dict) -> int:
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


def fetch_spec_count(swagger_url: str) -> int:
    """Fetch a spec and return its endpoint count. 0 on any failure."""
    try:
        spec = http_get_json(swagger_url)
        return count_endpoints(spec)
    except Exception:
        return 0


def dump(limit: int | None = None) -> None:
    schema.init_db()
    conn = schema.get_conn()
    print("Fetching APIs.guru list.json ...")
    catalog = http_get_json(LIST_URL)
    print(f"  {len(catalog)} APIs in list.json")

    added = 0
    skipped = 0
    for i, (api_key, entry) in enumerate(catalog.items()):
        if limit is not None and i >= limit:
            break
        # Prefer the preferred version; fall back to first
        versions = entry.get("versions") or {}
        pref = entry.get("preferred")
        ver_key = pref if pref in versions else (next(iter(versions), None))
        if not ver_key:
            continue
        ver = versions[ver_key]
        info = ver.get("info") or {}
        title = info.get("title") or api_key
        desc = info.get("description") or ""
        category = (info.get("x-apisguru-categories") or [None])[0]

        # Skip if already present (resumable)
        existing = conn.execute("SELECT id FROM apis WHERE id=?", (api_key,)).fetchone()
        if existing:
            continue

        # Endpoint gate: count from the spec, skip zero-endpoint entries
        ep_count = fetch_spec_count(ver.get("swaggerUrl") or "")
        if ep_count <= 0:
            skipped += 1
            continue

        node = {
            "id": api_key,
            "name": title,
            "description": desc,
            "slugifiedName": api_key,
            "pricing": None,
            "categoryName": category,
            "updatedAt": ver.get("updated"),
            "endpoint_count": ep_count,
            "source": "apisguru",
        }
        schema.upsert_api(conn, node)
        added += 1
        if added % 50 == 0:
            conn.commit()
            print(f"  ... {added} added, {skipped} skipped (total {schema.count_apis(conn)})")
        time.sleep(BASE_DELAY)

    conn.commit()
    conn.close()
    print(f"Done. added={added}, skipped_zero_endpoint={skipped}, "
          f"total={schema.count_apis(schema.get_conn())}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="max APIs to dump (test)")
    args = ap.parse_args()
    dump(args.limit)
