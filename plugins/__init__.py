"""Live-search plugins for apinav: per-query fetch, merge before rerank,
novel results persisted by ingest.py (organic growth). One module per source,
uniform shape:

    search(query, limit) -> list[node_dict]  # apinav row shape + source tag

Shared behavior (via plugins/base):
- plain HTTP GET, no auth, polite pacing (PACING between calls)
- RateLimited(seconds) on 429 with Retry-After parsed
- _normalize() maps to: id, name, description, endpoint_count, source,
  plus source-specific extras (humanURL etc.) for live link resolution.

Sources (all probe-verified 2026-09-11):
- apisio   — 133.6k APIs, q= search, docs from humanURL
- smithery — 174 curated MCP servers, q= search, homepage links
- apify    — ~5.1k searchable Actors, search= param, type-annotated
- googledisc — 531 official Google APIs, static list fetched+filtered
- hfspace  — HF Spaces demos, ?search= param, likes as quality signal
"""

import importlib


def build_links(row: dict) -> dict:
    """Dispatch link resolution to the plugin that owns the row's source.

    Returns {"url_docs": ..., "url_spec_json": ..., "url_spec_yaml": ...}.
    Missing/unsupported sources return all-None. Never raises.
    """
    out = {"url_docs": None, "url_spec_json": None, "url_spec_yaml": None}
    source = (row.get("source") or "").lower()
    if not source:
        return out
    try:
        mod = importlib.import_module(f"plugins.{source}")
        fn = getattr(mod, "build_links", None)
        if fn is None:
            return out
        links = fn(row)
        if isinstance(links, dict):
            out.update({k: links.get(k) for k in out})
    except Exception:
        pass
    return out

