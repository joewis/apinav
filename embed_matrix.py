"""Embedding layer: NVIDIA vectorization + normalized numpy cache for fast ranking.

Two responsibilities live here because both touch the same data (the
`embeddings` table):

1. Backfill (`embed_all`): batch-embed catalog rows that have no vector yet,
   using NVIDIA nemotron-3-embed-1b (2048-dim). Resumable, batched, polite.
2. Search cache (`build`/`append`/`load_fresh`): a row-normalized float32
   matrix + id list mirroring the `embeddings` table. Grows incrementally so
   semantic search runs in ~0.08s instead of ~31s of pure-Python cosine.

Measured 2026-09-11: pure-Python cosine over 46k x 2048 = 31.5s; numpy on
the cached matrix = 0.08s; matrix reload from disk = 0.04s; full rebuild
~16.5s vs append ~0.05s/row.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import schema

MATRIX_PATH = str(config.APINAV_DIR / "embed_matrix.npy")
IDS_PATH = str(config.APINAV_DIR / "embed_ids.json")

EMBED_URL = config.get("embeddings", "url")
EMBED_MODEL = config.get("embeddings", "model")
BATCH_SIZE = config.get("embeddings", "batch_size")
BASE_DELAY = config.get("embeddings", "base_delay")
MAX_RETRIES = config.get("embeddings", "max_retries")
RETRY_BACKOFF = config.get("embeddings", "retry_backoff")
MAX_DESC_CHARS = config.get("embeddings", "max_desc_chars")


def _embed_batch(key: str, texts: list[str]) -> list[list[float]]:
    body = json.dumps({"model": EMBED_MODEL, "input": texts}).encode()
    req = urllib.request.Request(
        EMBED_URL,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    by_index = {d["index"]: d["embedding"] for d in data["data"]}
    return [by_index[i] for i in range(len(texts))]


def _api_text(api: dict) -> str:
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


def embed_all(conn=None, limit: int | None = None) -> int:
    """Backfill embeddings for catalog rows that have no vector yet.

    Returns the number of rows newly embedded. Opens its own connection if
    none is provided.
    """
    close_conn = conn is None
    if conn is None:
        schema.init_db()
        conn = schema.get_conn()

    key = config.secret('EMBEDDING_API_KEY')
    print(f"Using model {EMBED_MODEL} (2048-dim).")

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
        texts = [_api_text(dict(r)) for r in batch]
        ids = [r["id"] for r in batch]

        vectors = None
        for attempt in range(MAX_RETRIES):
            try:
                vectors = _embed_batch(key, texts)
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
            schema.set_embedding(conn, api_id, EMBED_MODEL, vec)
        conn.commit()

        # Incrementally grow the search cache for the new rows.
        append(conn, ids)

        done += len(batch)
        print(f"embedded {done}/{len(rows)} (batch {i//BATCH_SIZE})")
        time.sleep(BASE_DELAY)

    if close_conn:
        total = schema.count_embedded(conn)
        conn.close()
        print(f"Done. Embedded {total} total.")
    return done


# --- normalized matrix cache ------------------------------------------------

def _atomic_write(mat, ids):
    tmp_m = MATRIX_PATH + ".rebuild.npy"  # ends with .npy: np.save won't append
    tmp_i = IDS_PATH + ".rebuild"
    np.save(tmp_m, mat)
    with open(tmp_i, "w") as f:
        json.dump(ids, f)
    os.replace(tmp_m, MATRIX_PATH)
    os.replace(tmp_i, IDS_PATH)


def build(conn):
    """Full rebuild from the embeddings table. Returns (mat, ids)."""
    rows = conn.execute("SELECT api_id, vector FROM embeddings").fetchall()
    ids = [r["api_id"] for r in rows]
    mat = np.array([json.loads(r["vector"]) for r in rows], dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1)
    norms[norms == 0] = 1
    mat /= norms[:, None]
    _atomic_write(mat, ids)
    return mat, ids


def append(conn, api_ids):
    """Incrementally add newly-embedded rows to the cache (O(ms)/row).

    For each id: fetch its vector, normalize, append to the matrix, rewrite
    the id list. Idempotent (id already in cache = skip). Falls back
    gracefully: on any error the caller just leaves the cache stale — the
    next `load_fresh` full-rebuilds inline, so append can never corrupt
    anything beyond what `build` would fix. Returns count appended.
    """
    if not api_ids:
        return 0
    try:
        mat = np.load(MATRIX_PATH)
        with open(IDS_PATH) as f:
            ids = json.load(f)
        if mat.shape[0] != len(ids):
            return 0  # inconsistent pair; let load_fresh rebuild
        existing = set(ids)
        qmarks = ",".join("?" * len(api_ids))
        rows = conn.execute(
            f"SELECT api_id, vector FROM embeddings WHERE api_id IN ({qmarks})",
            list(api_ids),
        ).fetchall()
        new_ids, new_vecs = [], []
        for r in rows:
            if r["api_id"] in existing:
                continue
            try:
                vec = np.array(json.loads(r["vector"]), dtype=np.float32)
            except Exception:
                continue
            n = float(np.linalg.norm(vec))
            if n <= 0:
                continue
            new_ids.append(r["api_id"])
            new_vecs.append(vec / n)
        if not new_ids:
            return 0
        mat = np.append(mat, np.stack(new_vecs), axis=0)
        ids = ids + new_ids
        _atomic_write(mat, ids)
        return len(new_ids)
    except Exception:
        return 0


def load_fresh(conn):
    """Return (mat, ids) matching the embeddings table; rebuild inline when
    stale or absent. Returns (None, None) when numpy is unavailable."""
    try:
        import numpy as np  # noqa: F401  (availability probe)
    except ImportError:
        return None, None
    n_db = conn.execute("SELECT COUNT(*) c FROM embeddings").fetchone()["c"]
    if os.path.exists(MATRIX_PATH) and os.path.exists(IDS_PATH):
        try:
            mat = np.load(MATRIX_PATH, mmap_mode="r")
            ids = json.load(open(IDS_PATH))
            if len(ids) == n_db and mat.shape[0] == n_db:
                return mat, ids
        except Exception:
            pass
    return build(conn)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Backfill embeddings for un-embedded catalog rows."
    )
    ap.add_argument("--limit", type=int, default=None, help="max APIs to embed (test)")
    args = ap.parse_args()
    embed_all(limit=args.limit)
