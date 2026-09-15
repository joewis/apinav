# apinav

Agent-driven API discovery: a local SQLite catalog of public APIs with keyword
(FTS5) and semantic (embeddings + LLM rerank) search, exposed to AI agents as
an MCP (Model Context Protocol) server. Live marketplace and directory sources
overlay the local index as per-query plugins — fetched, merged into the
candidate pool before reranking, and **persisted when new**, so the catalog
grows organically during usage.

Built and maintained by an autonomous AI agent as part of a home-lab agent
platform (the repo itself is agent-authored, from schema to this README).

## What it does

- **Catalog ingestion** — public catalogs (APIs.guru plus a commercial
  marketplace via a private headless-browser client not included here) are
  dumped into a local SQLite database by private ingestion scripts.
- **Keyword search** — FTS5 full-text search over name/description/category/author.
- **Semantic search** — NVIDIA embeddings + LLM reranking; live plugin results
  are merged into the candidate pool *before* reranking so they compete on
  relevance instead of being appended to the tail.
- **Live plugins** — ALL live sources are uniform `search(query, limit)` plugins
  (apis.io, marketplace, Smithery, Apify, Google APIs directory, HF Spaces)
  with polite pacing, Retry-After handling.
- **Organic growth** — live results that pass the spam/junk gates and carry an
  id the catalog has never seen are **persisted to the local database**
  (`ingest.py`): upsert with provenance (`source` column), NVIDIA-embedded in
  the same call, FTS5-indexed via triggers. Re-sights are cheap id skips; the
  index grows organically as agents search, with no bulk re-crawl required.
- **MCP integration** — the search tools are published through a shared MCP
  gateway so any agent can discover and call them on demand.
- **Junk filtering** — spam detection (buy/verified-account/gamble/adult
  patterns, English + Vietnamese, SEO markers), near-duplicate suppression,
  and relevance guards run on every merged row — live results only enter the
  catalog if they pass the same battery.

## Repo layout

| File | Purpose |
|---|---|
| `apinav_mcp_server.py` | MCP server exposing the search tools (merges live plugin results into the candidate pool, then persists novel rows) |
| `ingest.py` | Organic-growth persistence: spam/junk gates, dedup, upsert + NVIDIA embed + vector-cache append for novel live results |
| `apinav.py` | Gateway registration helper |
| `schema.py` | SQLite schema (catalog + FTS5 + embeddings + source registry) |
| `apisio_client.py` | apis.io live client (curated + full search) |
| `plugins/apisio.py` | apis.io live plugin |
| `embed_catalog.py` / `embed_matrix.py` | Embedding pipeline (NVIDIA) + incremental matrix growth |
| `rebuild_fts.py` | Rebuild the FTS5 index |
| `keywords.py` | Per-category keyword partitioning for paginated ingestion |
| `prefilter_spam.py` | Junk filtering |
| `source_links.py` | Per-source docs/spec URL resolution |
| `sources.py` | Source registry: cadence, delta strategy, freshness report |
| `plugins/` | Live-search plugins (one module per source, uniform shape) |

The catalog-ingestion dump scripts are **not included** in this repository —
they use private browsing machinery and stay local.

## Plugin contract

Every live plugin has the same shape:

```python
def search(query: str, limit: int = 5) -> list[dict]:
    # plain HTTP GET (or optional local client), polite pacing via plugins/base
    # returns rows normalized to the apinav node shape with a `source` tag
```

- Polite pacing (default 0.5 s between calls per source)
- `RateLimited(seconds)` on HTTP 429 with `Retry-After` parsed and honored
- One dead plugin never takes the others down
- Results are overlays: fetched per query, merged before rerank — and novel
  ids among them are persisted by `ingest.py` (idempotent, lock-safe,
  best-effort: a locked or failed persist never blocks the query)

## Requirements

- Python 3.11+
- `openai`-compatible client for embeddings/rerank — API keys read from the
  environment only (`NVIDIA_API_KEY`, `OPENROUTER_API_KEY`); nothing is hardcoded
- SQLite with FTS5
- A headless-browser client for the marketplace source is **not included** in
  this repository — provide your own module named `marketplace_client.py` (with
  `open_tab/close_tab/check_rate_limit/fetch_all`) or delete
  `plugins/rapidapi.py`; the plugin degrades gracefully to an empty result
  when the client is missing.

## Status

Personal home-lab project, published as-is. The catalog database and embedding
matrix are multi-GB build artifacts and are not included — run the ingestion
scripts (yours to write) to build your own index.