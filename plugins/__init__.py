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
