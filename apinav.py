#!/home/carl/mcp-gateway-venv/bin/python
"""Command-line search against the local API catalog.

Loads the MCP server module in-process (same code the gateway runs) and calls
its tools directly, skipping the network for a snappy CLI.

Usage:
  apinav search "find me an API for gold prices"            # semantic (default)
  apinav search -r typesafe "find me an API for gold prices"  # pick reranker
  apinav keyword "weather"                                  # FTS5 keyword search
  apinav get adyen.com:AccountService                       # full record incl. links
  apinav stats                                              # catalog totals
  apinav source <plugin> "query"                          # test one plugin directly

Rerankers (-r): typesafe (default, Jev System One) | nvidia (cross-encoder)
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


async def _run(mod, cmd, args, limit=None, rerank_backend=None):
    if cmd == "search":
        out = await mod.apinav_semantic_search(args[0], rerank_backend=rerank_backend)
    elif cmd == "keyword":
        out = await mod.apinav_keyword_search(args[0])
    elif cmd == "get":
        out = await mod.apinav_get_api(args[0])
    elif cmd == "stats":
        out = await mod.apinav_catalog_stats()
    elif cmd == "source":
        if len(args) < 2:
            out = json.dumps({"error": "usage: apinav source <plugin> <query>"})
        else:
            plugin_name, query = args[0], " ".join(args[1:])
            sys.path.insert(0, "./plugins")
            try:
                mod = importlib.import_module(plugin_name)
                rows = mod.search(query, limit=limit or 5)
                out = json.dumps(rows, indent=2)
            except Exception as e:
                out = json.dumps({"error": str(e)})
    else:
        out = json.dumps({"error": f"unknown command '{cmd}'. Use search|keyword|get|stats|source"})
    return out


def _pretty(out: str, limit: int | None):
    """Print a tool's JSON response as a human-readable terminal listing.

    This is the DISPATCH layer for the CLI: it figures out which of the
    several output shapes a tool returned and how to render it, leaving the
    per-row formatting to _render_row():

      - keyword search  -> a bare JSON array of result rows
      - semantic search -> a dict with a "results" list (plus timing/stats keys)
      - stats / get     -> a dict but NOT a list-backed result set; print raw
      - any tool error  -> a dict with an "error" key; surface it loudly
      - non-list rows   -> each rendered via _render_row()

    For list-backed results it renders each row as a compact terminal line:
    similarity score (right-aligned), name, pricing, endpoint count, a wrapped
    description, and any per-source docs/spec links. `limit` (from `search N`
    on the CLI line) truncates the listing with a "… N more" tail.
    """
    # All tools return JSON as text; if it isn't parseable, it isn't ours to
    # pretty-print — just echo it verbatim rather than guessing.
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

    # These two shapes carry a single entity, not a result list: print the whole
    # dict untouched instead of entering the per-row loop below.
    if "total_apis" in d and not results:  # stats
        print(json.dumps(d, indent=1))
        return
    if "id" in d and "name" in d and not results:  # single get
        print(json.dumps(d, indent=1))
        return

    shown = 0
    for r in results:
        if limit and shown >= limit:
            # Truncated: mention how many real hits were hidden, then stop.
            remaining = len(results) - shown
            if remaining:
                print(f"  ... {remaining} more")
            break
        for line in _render_row(r):
            print(line)
        shown += 1


def _render_row(r: dict) -> list[str]:
    """Format one API result row into the terminal lines to print.

    Handles only the per-row shape, separate from _pretty's job of deciding
    which rows to print. Returns the lines (main + any description/docs/spec),
    so the caller controls interleaving and the truncation tail. No output is
    written here — the caller prints.
    """
    import re
    # Live sources wrap terms in <em> tags ("<em>Weather</em>"); strip them
    # so the terminal line reads clean, even when a name is only a tag.
    name = re.sub(r"<[^>]+>", "", r.get("name") or "?")
    desc = (r.get("description") or "")[:150].replace("\n", " ")
    desc = re.sub(r"<[^>]+>", "", desc)
    # Prefix the similarity score only when the row actually has one
    # (cosine similarity from semantic search); keyword rows don't.
    sim = r.get("similarity")
    sim_s = f"{sim:6.3f}  " if isinstance(sim, (int, float)) else ""
    line = f"{sim_s}{name}"
    if r.get("pricing"):
        line += f"  [{r['pricing']}]"
    # endpoint_count -1 means 'unknown/N-A class' (e.g. an MCP server or
    # Apify actor, which aren't REST APIs) — only show it when real.
    ep = r.get("endpoint_count")
    if isinstance(ep, (int, float)) and ep >= 0:
        line += f"  ({ep} endpoints)"
    out = [line]
    if desc:
        out.append(f"        {desc}")
    links = r.get("links") or {}
    # Docs/spec URLs, if the owning plugin resolved any for this row.
    if links.get("url_docs"):
        out.append(f"        docs: {links['url_docs']}")
    if links.get("url_spec_json"):
        out.append(f"        spec: {links['url_spec_json']}")
    if links.get("url_spec_yaml"):
        out.append(f"        yaml: {links['url_spec_yaml']}")
    return out


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    # -r <backend> selects the reranker for search; position-independent so it
    # composes with the trailing-N limit shorthand (e.g. search -r typesafe q 3).
    rerank_backend = None
    if "-r" in argv:
        i = argv.index("-r")
        if i + 1 >= len(argv):
            print("ERROR: -r requires a backend: nvidia | typesafe")
            sys.exit(1)
        rerank_backend = argv[i + 1]
        if rerank_backend not in ("nvidia", "typesafe"):
            print(f"ERROR: unknown reranker '{rerank_backend}'. Use typesafe | nvidia")
            sys.exit(1)
        argv = argv[:i] + argv[i + 2 :]
    cmd, args = argv[0], argv[1:]
    if cmd == "search" and len(args) > 1 and args[-1].isdigit():
        limit = int(args[-1])
        args = args[:-1]
    else:
        limit = None
    mod = _load_gateway_meta()
    out = _run_backend(mod, cmd, args, limit, rerank_backend)
    _pretty(out, limit)


def _run_backend(mod, cmd, args, limit=None, rerank_backend=None):
    import asyncio
    return asyncio.run(_run(mod, cmd, args, limit, rerank_backend))


if __name__ == "__main__":
    main()