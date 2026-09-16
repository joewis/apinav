"""Source registry for apinav: metadata per source.

With the live-plugin + organic-growth architecture, sources are queried per
search and novel results are persisted by ingest.py. There are no batch
"syncs", so this registry only tracks source metadata and current row counts
(computed live from the catalog).

Usage:
    python3 sources.py init            # create table + register known sources
    python3 sources.py report          # metadata + current row counts
    python3 sources.py set <source> <field> <value>
"""
import sqlite3
import sys

import yaml

sys.path.insert(0, "/home/carl/apinav")
import config
import schema

_CONFIG_PATH = str(config.APINAV_DIR / "plugins" / "plugins.yaml")


def load_known_sources(path: str = _CONFIG_PATH) -> list[dict]:
    """Load source metadata from the plugin registry config."""
    with open(path) as f:
        data = yaml.safe_load(f)
    plugins = data.get("plugins", [])
    return [
        {
            "source": p["source"],
            "refresh_cadence": p.get("refresh_cadence"),
            "drift_notes": p.get("drift_notes"),
        }
        for p in plugins
    ]


def init_registry(conn) -> None:
    # Migration: old registry had sync/etag/result columns from the batch era.
    conn.execute("DROP TABLE IF EXISTS source_registry")
    conn.execute(
        """CREATE TABLE source_registry (
               source TEXT PRIMARY KEY,
               refresh_cadence TEXT,
               drift_notes TEXT
           )"""
    )
    for s in load_known_sources():
        conn.execute(
            """INSERT OR IGNORE INTO source_registry
               (source, refresh_cadence, drift_notes)
               VALUES (?,?,?)""",
            (s["source"], s["refresh_cadence"], s["drift_notes"]),
        )
    conn.commit()


def get(conn, source: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM source_registry WHERE source=?", (source,)
    ).fetchone()


def report(conn) -> str:
    rows = conn.execute(
        """SELECT r.*, COUNT(a.id) AS row_count
           FROM source_registry r
           LEFT JOIN apis a ON a.source = r.source
           GROUP BY r.source
           ORDER BY r.source"""
    ).fetchall()
    lines = [
        "source        cadence      rows   drift_notes",
        "-" * 90,
    ]
    for r in rows:
        lines.append(
            f"{r['source']:<13} {r['refresh_cadence'] or '?':<12} "
            f"{r['row_count'] if r['row_count'] is not None else 0:>6}  "
            f"{r['drift_notes'] or ''}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
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
        if field not in ("refresh_cadence", "drift_notes"):
            sys.exit(f"field '{field}' not editable here")
        conn.execute(f"UPDATE source_registry SET {field}=? WHERE source=?", (val, sys.argv[2]))
        conn.commit()
        print(report(conn))
    else:
        sys.exit("usage: sources.py init|report|set <source> <field> <value>")
    conn.close()
