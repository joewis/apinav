"""Source registry for apinav: freshness contract per ingested source.

One row per source: cadence, last sync, delta strategy, last result.
Created as a side-table (safe mid-backfill per the SKILL.md pitfall).
Never ALTERs existing tables.

Usage:
    python3 sources.py init            # create table + register known sources
    python3 sources.py report          # freshness report to stdout
    python3 sources.py set <source> <field> <json-or-string-value>
"""
import json
import sqlite3
import sys

sys.path.insert(0, "/home/carl/apinav")
import schema

# The freshness contract. delta_strategy values:
#   'updatedAt-cursor' — source supports change-since filtering/pagination
#   'refetch-all'      — source is small/cheap enough to re-walk fully
#   'etag'             — conditional GET supported
#   'none'             — static snapshot
KNOWN_SOURCES = [
    {
        "source": "apisio",
        "refresh_cadence": "per-query",
        "delta_strategy": "none",
        "drift_notes": "Live plugin: curated + full search per query. Agent-permitted (robots+terms).",
    },
    {
        "source": "rapidapi",
        "refresh_cadence": "per-query",
        "delta_strategy": "none",
        "drift_notes": "Live plugin: per-query search via optional local session client; rate-limit aware with Retry-After handling.",
    },
    {
        "source": "smithery",
        "refresh_cadence": "per-query",
        "delta_strategy": "none",
        "drift_notes": "Live plugin: per-query registry search.",
    },
    {
        "source": "apify",
        "refresh_cadence": "per-query",
        "delta_strategy": "none",
        "drift_notes": "Live plugin: per-query store search. Execution marketplace (actors), not REST APIs: endpoint gate N/A.",
    },
    {
        "source": "googledisc",
        "refresh_cadence": "per-query",
        "delta_strategy": "none",
        "drift_notes": "Live plugin: per-query directory search.",
    },
    {
        "source": "hfspace",
        "refresh_cadence": "per-query",
        "delta_strategy": "none",
        "drift_notes": "Live plugin: per-query Spaces search. Demos, not REST APIs: endpoint gate N/A.",
    },
]


def init_registry(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS source_registry (
               source TEXT PRIMARY KEY,
               refresh_cadence TEXT,
               last_full_sync TEXT,
               last_delta_sync TEXT,
               delta_strategy TEXT,
               last_result TEXT,   -- JSON {added,updated,removed,skipped,errors,duration_s}
               last_etag TEXT,
               row_count INTEGER,
               drift_notes TEXT
           )"""
    )
    for s in KNOWN_SOURCES:
        conn.execute(
            """INSERT OR IGNORE INTO source_registry
               (source, refresh_cadence, delta_strategy, drift_notes)
               VALUES (?,?,?,?)""",
            (s["source"], s["refresh_cadence"], s["delta_strategy"], s["drift_notes"]),
        )
    conn.commit()


def mark_full_sync(conn, source: str, result: dict, row_count: int | None = None, etag: str | None = None) -> None:
    conn.execute(
        """UPDATE source_registry
           SET last_full_sync=datetime('now'), last_result=?, row_count=?, last_etag=COALESCE(?, last_etag)
           WHERE source=?""",
        (json.dumps(result), row_count, etag, source),
    )
    conn.commit()


def mark_delta_sync(conn, source: str, result: dict) -> None:
    conn.execute(
        """UPDATE source_registry
           SET last_delta_sync=datetime('now'), last_result=?, row_count=(SELECT COUNT(*) FROM apis WHERE source=?)
           WHERE source=?""",
        (json.dumps(result), source),
    )
    conn.commit()


def get(conn, source: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM source_registry WHERE source=?", (source,)
    ).fetchone()


def report(conn) -> str:
    rows = conn.execute("SELECT * FROM source_registry ORDER BY source").fetchall()
    lines = ["source        cadence  last_full_sync      rows     delta            last result",
             "-" * 110]
    for r in rows:
        res = {}
        try:
            res = json.loads(r["last_result"] or "{}")
        except Exception:
            pass
        res_s = "+{added} ~{updated} -{removed} err={errors}".format(
            added=res.get("added", 0), updated=res.get("updated", 0),
            removed=res.get("removed", 0), errors=res.get("errors", 0),
        ) if res else "never synced"
        lines.append(
            f"{r['source']:<13} {r['refresh_cadence'] or '?':<8} "
            f"{r['last_full_sync'] or 'never':<19} "
            f"{r['row_count'] if r['row_count'] is not None else 'n/a':>7}  "
            f"{r['delta_strategy'] or '?':<16} {res_s}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    conn = schema_conn = None
    import schema as _s
    conn = _s.get_conn()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "init":
        init_registry(conn)
        print(report(conn))
    elif cmd == "report":
        print(report(conn))
    elif cmd == "set" and len(sys.argv) >= 5:
        field, val = sys.argv[3], sys.argv[4]
        if field not in ("refresh_cadence", "delta_strategy", "drift_notes"):
            sys.exit(f"field '{field}' not editable here")
        conn.execute(f"UPDATE source_registry SET {field}=? WHERE source=?", (val, sys.argv[2]))
        conn.commit()
        print(report(conn))
    else:
        sys.exit("usage: sources.py init|report|set <source> <field> <value>")
    conn.close()