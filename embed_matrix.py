"""Normalized embedding-matrix cache for fast numpy cosine ranking.

The cache mirrors the `embeddings` table (id list + row-normalized float32
matrix). Grows INCREMENTALLY: `append()` adds newly-embedded rows in
milliseconds (normalize one vector, np.append, atomic replace) instead of
the ~16s full reparse rebuild. Deletions never invalidate: orphaned vectors
(apis row deleted after cache build) drop out at row-fetch time in the
search fast path. The full rebuild only fires when the cache is missing,
corrupt, or otherwise unresolvable — the self-healing path of last resort.

Measured 2026-09-11: pure-Python cosine over 46k x 2048 = 31.5s; numpy on
the cached matrix = 0.08s; matrix reload from disk = 0.04s; full rebuild
~16.5s vs append ~0.05s/row.
"""
import json
import os

import numpy as np

MATRIX_PATH = "/home/carl/apinav/embed_matrix.npy"
IDS_PATH = "/home/carl/apinav/embed_ids.json"


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