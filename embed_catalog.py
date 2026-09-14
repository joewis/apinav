#!/usr/bin/env python3
"""Embed the API catalog with NVIDIA nemotron-3-embed-1b (2048-dim).

Reads NVIDIA_API_KEY from ~/.hermes/.env. Resumable: skips APIs already
embedded. Batches requests (up to 32 per call) to be efficient, with a delay
between batches to be nice to the server.

Usage:
    python3 embed_catalog.py            # embed all un-embedded APIs
    python3 embed_catalog.py --limit 50 # embed only 50 (test)
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema

EMBED_URL = "https://integrate.api.nvidia.com/v1/embeddings"
MODEL = "nvidia/nemotron-3-embed-1b"
BATCH_SIZE = 32
BASE_DELAY = 0.5          # seconds between batches
MAX_RETRIES = 5
RETRY_BACKOFF = 2.0
MAX_DESC_CHARS = 4000     # truncate descriptions to avoid NVIDIA 400 on huge specs

ENV_PATH = "/home/carl/.hermes/.env"


def load_key() -> str:
    with open(ENV_PATH) as f:
        for line in f:
            if line.startswith("NVIDIA_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("NVIDIA_API_KEY not found in " + ENV_PATH)


def embed_batch(key: str, texts: list[str]) -> list[list[float]]:
    body = json.dumps({"model": MODEL, "input": texts}).encode()
    req = urllib.request.Request(
        EMBED_URL,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    # Preserve input order
    by_index = {d["index"]: d["embedding"] for d in data["data"]}
    return [by_index[i] for i in range(len(texts))]


def api_text(api: dict) -> str:
    """Compose the text to embed for one API.

    Truncate the description to MAX_DESC_CHARS — some APIs.guru specs carry
    huge descriptions (up to 250KB) that exceed NVIDIA's embedding input
    limit and cause HTTP 400.
    """
    parts = [api.get("name") or ""]
    if api.get("description"):
        parts.append(api["description"][:MAX_DESC_CHARS])
    if api.get("category"):
        parts.append(api["category"])
    return "\n".join(parts)


def embed(limit: int | None = None) -> None:
    schema.init_db()
    conn = schema.get_conn()
    key = load_key()
    print(f"Using model {MODEL} (2048-dim).")

    # Fetch un-embedded APIs
    rows = conn.execute(
        """SELECT a.id, a.name, a.description, a.category
           FROM apis a LEFT JOIN embeddings e ON a.id = e.api_id
           WHERE e.api_id IS NULL"""
    ).fetchall()
    if limit:
        rows = rows[:limit]
    print(f"{len(rows)} APIs to embed.")

    done = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        texts = [api_text(dict(r)) for r in batch]
        ids = [r["id"] for r in batch]

        vectors = None
        for attempt in range(MAX_RETRIES):
            try:
                vectors = embed_batch(key, texts)
                break
            except Exception as e:
                wait = RETRY_BACKOFF ** attempt
                print(f"  batch {i//BATCH_SIZE} attempt {attempt+1} failed ({e}); retry in {wait:.0f}s")
                time.sleep(wait)
        if vectors is None:
            print(f"Giving up on batch {i//BATCH_SIZE} after {MAX_RETRIES} retries.")
            continue

        conn.execute("BEGIN")
        for api_id, vec in zip(ids, vectors):
            schema.set_embedding(conn, api_id, MODEL, vec)
        conn.commit()

        done += len(batch)
        print(f"embedded {done}/{len(rows)} (batch {i//BATCH_SIZE})")
        time.sleep(BASE_DELAY)

    conn.close()
    print(f"Done. Embedded {schema.count_embedded(schema.get_conn())} total.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="max APIs to embed (test)")
    args = ap.parse_args()
    embed(args.limit)
