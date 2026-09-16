#!/home/carl/mcp-gateway-venv/bin/python
"""Command-line search against the local API catalog.

Loads the MCP server module in-process (same code the gateway runs) and calls
its tools directly, skipping the network for a snappy CLI.

Usage:
  apinav search "find me an API for gold prices"            # semantic (default)
  apinav keyword "weather"                                  # FTS5 keyword search
  apinav get adyen.com:AccountService                       # full record incl. links
  apinav stats                                              # catalog totals
"""
import sys
import json
import importlib.util

import config


def _load_gateway_meta():
    """Load the MCP server module in-process so the CLI shares its exact logic."""
    server_path = str(config.APINAV_DIR / "apinav_mcp_server.py")
    spec = importlib.util.spec_from_file_location("apinav_server", server_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _run(mod, cmd, args):
    if cmd == "search":
        out = await mod.apinav_semantic_search(args[0])
    elif cmd == "keyword":
        out = await mod.apinav_keyword_search(args[0])
    elif cmd == "get":
        out = await mod.apinav_get_api(args[0])
    elif cmd == "stats":
        out = await mod.apinav_catalog_stats()
    else:
        out = json.dumps({"error": f"unknown command '{cmd}'. Use search|keyword|get|stats"})
    return out


def _pretty(out: str, limit: int | None):
    try:
        d = json.loads(out)
    except Exception:
        print(out)
        return
    # apinav_keyword_search returns a bare JSON list of rows; the other tools
    # return a dict (possibly with "results", or "error").
    if isinstance(d, list):
        results = d
    elif "error" in d:
        print("ERROR:", d["error"])
        return
    else:
        results = d.get("results") or []
    if "total_apis" in d and not results:  # stats
        print(json.dumps(d, indent=1))
        return
    if "id" in d and "name" in d and not results:  # single get
        print(json.dumps(d, indent=1))
        return
    shown = 0
    for r in results:
        if limit and shown >= limit:
            remaining = len(results) - shown
            if remaining:
                print(f"  ... {remaining} more")
            break
        import re
        name = re.sub(r"<[^>]+>", "", r.get("name") or "?")
        desc = (r.get("description") or "")[:150].replace("\n", " ")
        desc = re.sub(r"<[^>]+>", "", desc)
        sim = r.get("similarity")
        sim_s = f"{sim:6.3f}  " if isinstance(sim, (int, float)) else ""
        line = f"{sim_s}{name}"
        if r.get("pricing"):
            line += f"  [{r['pricing']}]"
        ep = r.get("endpoint_count")
        if isinstance(ep, (int, float)) and ep >= 0:
            line += f"  ({ep} endpoints)"
        print(line)
        if desc:
            print(f"        {desc}")
        links = r.get("links") or {}
        if links.get("url_docs"):
            print(f"        docs: {links['url_docs']}")
        if links.get("url_spec_json"):
            print(f"        spec: {links['url_spec_json']}")
        if links.get("url_spec_yaml"):
            print(f"        yaml: {links['url_spec_yaml']}")
        shown += 1


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd, args = argv[0], argv[1:]
    if cmd == "search" and len(args) > 1 and args[-1].isdigit():
        limit = int(args[-1])
        args = args[:-1]
    else:
        limit = None
    mod = _load_gateway_meta()
    out = _run_backend(mod, cmd, args)
    _pretty(out, limit)


def _run_backend(mod, cmd, args):
    import asyncio
    return asyncio.run(_run(mod, cmd, args))


if __name__ == "__main__":
    main()