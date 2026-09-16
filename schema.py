"""Shared SQLite schema for the API catalog index.

Tables:
- apis: one row per API (id, name, description, slug, pricing, category,
  scores, author, updated, endpoint_count, source, raw node JSON)
- embeddings: one row per API embedding (api_id -> 2048-dim float vector,
  stored as a JSON array)
- apis_fts: FTS5 virtual table over name/description/category/author/slug,
  kept in sync by triggers so keyword search needs no manual index updates

endpoint_count uses -1 as 'unknown/N-A class' (vs 0 = junk, skipped by the
gates) so execution-class rows (Apify actors, HF spaces, MCP servers) are
accepted but not shown as having zero endpoints.
"""
import json
import os
import sqlite3

import config

DB_PATH = str(config.DB_PATH)

SCHEMA = """
CREATE TABLE IF NOT EXISTS apis (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    slug TEXT,
    pricing TEXT,
    category TEXT,
    popularity REAL,
    latency_ms REAL,
    service_level REAL,
    success_rate REAL,
    author TEXT,
    updated_at TEXT,
    endpoint_count INTEGER DEFAULT -1,
    source TEXT NOT NULL DEFAULT 'rapidapi',
    raw TEXT
);

CREATE TABLE IF NOT EXISTS embeddings (
    api_id TEXT PRIMARY KEY REFERENCES apis(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector TEXT NOT NULL,   -- JSON array of floats
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS apis_fts USING fts5(
    name, description, category, author, slug,
    content='apis', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS apis_ai AFTER INSERT ON apis BEGIN
    INSERT INTO apis_fts(rowid, name, description, category, author, slug)
    VALUES (new.rowid, new.name, new.description, new.category, new.author, new.slug);
END;

CREATE TRIGGER IF NOT EXISTS apis_ad AFTER DELETE ON apis BEGIN
    INSERT INTO apis_fts(apis_fts, rowid, name, description, category, author, slug)
    VALUES ('delete', old.rowid, old.name, old.description, old.category, old.author, old.slug);
END;

CREATE TRIGGER IF NOT EXISTS apis_au AFTER UPDATE ON apis BEGIN
    INSERT INTO apis_fts(apis_fts, rowid, name, description, category, author, slug)
    VALUES ('delete', old.rowid, old.name, old.description, old.category, old.author, old.slug);
    INSERT INTO apis_fts(rowid, name, description, category, author, slug)
    VALUES (new.rowid, new.name, new.description, new.category, new.author, new.slug);
END;
"""


def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def upsert_api(conn: sqlite3.Connection, api: dict) -> None:
    """Insert or replace one API row.

    INSERT OR REPLACE wipes the row, so fields that arrive missing must be
    preserved from the existing row instead of reset. endpoint_count is the
    classic trap: a re-sighted node often lacks it, and the default (-1) would
    clobber a real count. source likewise falls back to the stored value.
    """
    score = api.get("score") or {}
    user = api.get("user") or {}
    existing = conn.execute(
        "SELECT endpoint_count FROM apis WHERE id=?", (api.get("id"),)
    ).fetchone()
    ep = api.get("endpoint_count")
    if ep is None and existing is not None:
        ep = existing["endpoint_count"]
    if ep is None:
        ep = -1
    src = api.get("source")
    if src is None and existing is not None:
        src = existing["source"]
    if src is None:
        src = "rapidapi"
    conn.execute(
        """INSERT OR REPLACE INTO apis
           (id, name, description, slug, pricing, category, popularity,
            latency_ms, service_level, success_rate, author, updated_at,
            endpoint_count, source, raw)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            api.get("id"),
            api.get("name"),
            api.get("description"),
            api.get("slugifiedName"),
            api.get("pricing"),
            api.get("categoryName"),
            score.get("popularityScore"),
            score.get("avgLatency"),
            score.get("avgServiceLevel"),
            score.get("avgSuccessRate"),
            user.get("name") or user.get("username"),
            api.get("updatedAt"),
            ep,
            src,
            json.dumps(api),
        ),
    )


def set_endpoint_count(conn: sqlite3.Connection, api_id: str, count: int) -> None:
    conn.execute(
        "UPDATE apis SET endpoint_count=? WHERE id=?", (count, api_id)
    )


def set_embedding(conn: sqlite3.Connection, api_id: str, model: str, vector: list) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO embeddings (api_id, model, dim, vector)
           VALUES (?,?,?,?)""",
        (api_id, model, len(vector), json.dumps(vector)),
    )


def get_embedding(conn: sqlite3.Connection, api_id: str) -> list | None:
    row = conn.execute(
        "SELECT vector FROM embeddings WHERE api_id=?", (api_id,)
    ).fetchone()
    return json.loads(row["vector"]) if row else None


def count_apis(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS c FROM apis").fetchone()["c"]


def count_embedded(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS c FROM embeddings").fetchone()["c"]
