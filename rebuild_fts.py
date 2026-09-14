#!/usr/bin/env python3
"""Rebuild the FTS index to include the slug column.

The FTS table schema changed (added slug). Existing rows won't have slug
indexed until we rebuild. This drops and recreates the FTS table and repopulates
it from the apis table.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema

conn = schema.get_conn()
conn.execute("DROP TABLE IF EXISTS apis_fts")
conn.executescript(schema.SCHEMA)  # recreates apis_fts + triggers
# Repopulate from apis
conn.execute(
    """INSERT INTO apis_fts(rowid, name, description, category, author, slug)
       SELECT rowid, name, description, category, author, slug FROM apis"""
)
conn.commit()
n = conn.execute("SELECT COUNT(*) c FROM apis_fts").fetchone()["c"]
print(f"Rebuilt FTS index with {n} rows (slug now indexed).")
conn.close()
