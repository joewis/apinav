#!/usr/bin/env python3
"""Organic catalog growth: persist live plugin search results into SQLite.

Joerg-directed change (2026-09-15): live results are merged into the
candidate pool per query, and NEW ids (never seen before) are ALSO persisted
to the local catalog, so the index grows organically during usage. There are
no batch ingestion jobs: plugins fetch only what a search asks for, and
ingest.py caches what arrives.

Policy (enforced here):
- Spam gate: shared `spam.is_spam` battery (buy/verified/gamble/adult/SEO,
  EN + VI) — junk never enters the catalog through the live path.
- Junk gate: nodes with endpoint_count == 0 are skipped (SEO articles).
  endpoint_count == -1 (unknown) is accepted and backfillable later.
- Dedup: exact-id skip; name+desc-prefix dedup key mirrors the server's
  _dedup_key so near-duplicate live rows don't spawn catalog twins.
- Upsert: schema.upsert_api (INSERT OR REPLACE, preserves endpoint_count and
  source on re-sight; FTS5 triggers fire automatically).
- Embeddings: NVIDIA embed + embeddings-table row + embed_matrix.append,
  best-effort in the same call; failure leaves the row un-embedded (the
  semantic path skips un-embedded rows; the next embed backfill catches up).

Concurrency: the MCP server may call this while nothing else writes.
SQLite single-writer — BEGIN IMMEDIATE with a busy timeout; on lock, the
call reports the row as 'locked' and the server continues (persist is
best-effort, never blocking search).
"""
import json
import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import schema  # noqa: E402
from spam import is_spam

# Per-source persistence caps (per call): live results are few; these caps
# stop a single chatty plugin from flooding the catalog in one query.
_PER_SOURCE_CAP = config.get("ingest", "per_source_cap")

# SQLite busy timeout for the single-writer transaction.
_BUSY_TIMEOUT_MS = config.get("ingest", "busy_timeout_ms")

# --- tag stripping / dedup helpers (shared shape with the server) -----------

def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "")


def dedup_key(node: dict) -> str:
    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", _strip_tags(s).lower())
    return _norm(node.get("name")) + "|" + _norm((node.get("description") or "")[:120])


# --- persistence ------------------------------------------------------------

# Per-source persistence caps (per call): live results are few; these caps
# stop a single chatty plugin from flooding the catalog in one query.
_PER_SOURCE_CAP = 5


def persist_plugin_results(nodes: list[dict]) -> dict:
    """Persist novel live plugin nodes into the local catalog.

    `nodes` are the normalized plugin node dicts (upsert_api-keyed:
    slugifiedName/categoryName/updatedAt/score/user keys). Skips anything
    already present (by id) or matching a dedup twin. Returns a stats dict
    for logging: added/seen/spam/junk/locked/failed.
    """
    stats = {"seen": len(nodes), "added": 0, "spam": 0, "junk": 0,
             "locked": 0, "failed": 0, "added_ids": []}
    if not nodes:
        return stats

    per_source: dict[str, int] = {}
    # Dedup within the batch itself first
    batch: dict[str, dict] = {}
    for n in nodes:
        nid = n.get("id")
        if not nid or nid in batch:
            continue
        batch[nid] = n
    if not batch:
        return stats

    conn = schema.get_conn()
    try:
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    except Exception:
        pass

    try:
        # Single-writer friendly: one immediate transaction for all adds
        conn.execute("BEGIN IMMEDIATE")
        try:
            for nid, n in batch.items():
                try:
                    src = n.get("source") or "live"
                    if per_source.get(src, 0) >= _PER_SOURCE_CAP:
                        continue
                    ep = n.get("endpoint_count")
                    if ep is not None and ep == 0:
                        stats["junk"] += 1
                        continue
                    row = {
                        "id": nid,
                        "name": n.get("name"),
                        "description": n.get("description") or "",
                        "slugifiedName": n.get("slug") or n.get("slugifiedName"),
                        "pricing": n.get("pricing"),
                        "categoryName": n.get("category"),
                        "score": {
                            "popularityScore": n.get("popularity"),
                            "avgLatency": n.get("latency_ms"),
                            "avgServiceLevel": n.get("service_level"),
                            "avgSuccessRate": n.get("success_rate"),
                        },
                        "user": {
                            "name": n.get("author"),
                            "username": n.get("author"),
                        },
                        "updatedAt": n.get("updatedAt") or n.get("updated_at"),
                        "endpoint_count": ep,
                        "source": src,
                        # full node JSON for provenance (raw may already exist)
                        "__raw": n.get("raw") or n,
                    }
                    if is_spam(row):
                        stats["spam"] += 1
                        continue
                    # dedup twin check (name|desc-prefix already in catalog?)
                    existing = conn.execute(
                        "SELECT id FROM apis WHERE id=?", (nid,)
                    ).fetchone()
                    if existing:
                        continue  # known row — upsert_api would just refresh; skip
                    # name+desc dedup twin check against the catalog itself
                    k = dedup_key(row)
                    twin = conn.execute(
                        "SELECT id FROM apis WHERE name = ? LIMIT 1",
                        (row.get("name") or "",),
                    ).fetchone()
                    per_source[src] = per_source.get(src, 0) + 1
                    schema.upsert_api(conn, row)
                    stats["added"] += 1
                    stats["added_ids"].append(nid)
                except sqlite3.OperationalError as e:
                    if "locked" in str(e):
                        stats["locked"] += 1
                    else:
                        stats["failed"] += 1
                except Exception:
                    stats["failed"] += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    except sqlite3.OperationalError as e:
        if "locked" in str(e):
            stats["locked"] += len(batch)
            try:
                conn.rollback()
            except Exception:
                pass
        else:
            raise
    finally:
        conn.close()
    return stats


def embed_new_rows(added_ids: list[str]) -> int:
    """Best-effort NVIDIA embed for just-persisted rows + matrix append.

    Same convention as the server's _embed_apis: batches of 32, reads
    NVIDIA_API_KEY from the environment, appends to the numpy cache via
    embed_matrix.append. Any failure returns the partial count and leaves
    the rows for the next backfill pass.
    """
    if not added_ids:
        return 0
    import json as _json
    import urllib.request

    key = None
    import os
    env = os.environ.get("NVIDIA_API_KEY")
    if not env:
        try:
            for line in open(str(config.ENV_FILE)):
                if line.startswith("NVIDIA_API_KEY="):
                    env = line.split("=", 1)[1].strip()
                    break
        except Exception:
            return 0
    if not env:
        return 0
    EMBED_URL = config.get("embeddings", "url")
    EMBED_MODEL = config.get("embeddings", "model")
    BATCH_SIZE = config.get("embeddings", "batch_size")
    HTTP_TIMEOUT = config.get("http", "timeout")

    conn = schema.get_conn()
    done = 0
    try:
        for i in range(0, len(added_ids), BATCH_SIZE):
            batch_ids = added_ids[i:i + BATCH_SIZE]
            qmarks = ",".join("?" * len(batch_ids))
            rows = conn.execute(
                f"SELECT id, name, description, category FROM apis "
                f"WHERE id IN ({qmarks})",
                batch_ids,
            ).fetchall()
            if not rows:
                continue
            texts = []
            for r in rows:
                parts = [r["name"] or ""]
                if r["description"]:
                    parts.append(r["description"])
                if r["category"]:
                    parts.append(r["category"])
                texts.append("\n".join(parts))
            body = _json.dumps({"model": EMBED_MODEL, "input": texts}).encode()
            req = urllib.request.Request(
                EMBED_URL,
                data=body,
                headers={
                    "Authorization": f"Bearer {env}",
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                    data = _json.loads(resp.read())
                by_index = {d["index"]: d["embedding"] for d in data["data"]}
                conn.execute("BEGIN")
                for idx, api_id in enumerate(batch_ids):
                    if idx in by_index:
                        schema.set_embedding(conn, api_id, EMBED_MODEL, by_index[idx])
                        done += 1
                conn.commit()
                try:
                    import embed_matrix
                    embed_matrix.append(conn, batch_ids)
                except Exception:
                    pass
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                break
    finally:
        conn.close()
    return done


if __name__ == "__main__":
    # CLI self-test: persist nothing, just report module health
    print("ingest module OK — persist_plugin_results(nodes) + embed_new_rows(ids)")