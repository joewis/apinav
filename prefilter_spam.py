#!/usr/bin/env python3
"""Pre-filter pass: delete unambiguous crypto-recovery / hacker-for-hire spam
from the unchecked rows, WITHOUT a per-API detail query.

This is a fast, keyword-based deletion of the strongest-signal junk so the
slow endpoint backfill doesn't waste a detail query on them. Only rows that
match the STRONG recovery pattern (recovery/hack verb + crypto/fund context)
are deleted — these are unambiguous (Spartan Tech Group, Digital Web Recovery,
etc.). The broad crypto-loan pattern is NOT used (too many false positives).

Run with the backfill STOPPED (SQLite single-writer lock).
"""
import re
import sys

sys.path.insert(0, "/home/carl/apinav")
import schema

# Strong recovery-spam: recovery/hack verb + crypto/fund context
RECOVERY_STRONG = re.compile(
    r"\b(recover|recovery|retrieval|retrieve|hacker|hack|scam|scammed|fraud|stolen|"
    r"reclaim|regain)\b.*\b(bitcoin|btc|crypto|funds|money|wallet|assets|usdt|eth)\b", re.I
)


def main() -> None:
    conn = schema.get_conn()
    rows = conn.execute(
        "SELECT id, name, description FROM apis WHERE endpoint_count=-1 AND source='rapidapi'"
    ).fetchall()
    print(f"unchecked rows: {len(rows)}")

    to_delete = []
    for r in rows:
        blob = ((r["name"] or "") + " " + (r["description"] or "")).lower()
        if RECOVERY_STRONG.search(blob):
            to_delete.append(r["id"])

    print(f"recovery-spam to delete: {len(to_delete)}")
    if not to_delete:
        print("nothing to delete")
        conn.close()
        return

    # Delete in a transaction
    conn.execute("BEGIN")
    for api_id in to_delete:
        conn.execute("DELETE FROM apis WHERE id=?", (api_id,))
    conn.commit()
    print(f"deleted {len(to_delete)} rows")

    remaining = conn.execute(
        "SELECT COUNT(*) c FROM apis WHERE endpoint_count=-1 AND source='rapidapi'"
    ).fetchone()["c"]
    print(f"unchecked remaining: {remaining}")
    conn.close()


if __name__ == "__main__":
    main()
