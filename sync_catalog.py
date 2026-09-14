#!/usr/bin/env python3
"""Weekly catalog sync: dump + embed.

Runs dump_catalog.py (full catalog) and embed_catalog.py (embed any new APIs)
in parallel. Logs to a dated file. Safe to run on a schedule.

Usage:
    python3 sync_catalog.py
"""
import datetime
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = "/home/carl/mcp-gateway-venv/bin/python"
LOG_DIR = "/home/carl/apinav/logs"
os.makedirs(LOG_DIR, exist_ok=True)
TS = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
LOG = os.path.join(LOG_DIR, f"sync-{TS}.log")


def run(name: str, script: str) -> int:
    with open(LOG, "a") as f:
        f.write(f"\n=== {name} started {TS} ===\n")
        f.flush()
        r = subprocess.run(
            [PY, os.path.join(HERE, script)],
            stdout=f, stderr=subprocess.STDOUT,
        )
        f.write(f"=== {name} exit {r.returncode} ===\n")
    return r.returncode


if __name__ == "__main__":
    # Run SEQUENTIALLY: dump and embed share resources, and running
    # them in parallel caused the embed's tab-close to idle-stop the browser,
    # killing the dump's tab mid-pagination (2026-09-10). Dump first, then embed.
    dump_rc = run("dump", "dump_catalog.py")
    embed_rc = run("embed", "embed_catalog.py")
    print(f"dump exit {dump_rc}, embed exit {embed_rc}. Log: {LOG}")
    sys.exit(0 if dump_rc == 0 and embed_rc == 0 else 1)
