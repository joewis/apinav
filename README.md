# apinav

Agent-driven API discovery: a local SQLite catalog of public APIs with keyword
(FTS5) and semantic (embeddings + LLM rerank) search, exposed to AI agents as
an MCP (Model Context Protocol) server.

The catalog has no bulk ingestion step. Live directory plugins fetch results
per search query; every result that passes the quality gates is upserted into
the database, embedded, and immediately searchable — the index grows
organically during usage.

Built and maintained by an autonomous AI agent as part of a home-lab agent
platform (the repo itself is agent-authored, from schema to this README).

## Architecture

1. **Plugins query API sources.** Every live source is a uniform
   `search(query, limit)` plugin (apis.io, marketplace, Smithery, Apify,
   Google APIs directory, HF Spaces) — polite pacing, `Retry-After` handling,
   one dead plugin never takes the others down. No batch jobs, no crawls:
   plugins only fetch what a search actually asks for.
2. **Results cached in SQLite.** After live results are merged into the
   candidate pool for the current query, novel ids (never seen before) are
   persisted to the local database via `ingest.py` — spam/junk-gated,
   deduplicated, upserted with per-source provenance in the `source` column.
   Re-sights are cheap id skips; a locked or failed persist never blocks the
   query.
3. **Embeddings for semantic search.** Persisted rows are embedded with
   NVIDIA `nemotron-3-embed-1b` in the same call (batch of 32) and appended
   to a numpy vector cache, so they are semantically searchable immediately.
   FTS5 triggers keep the keyword index current.

## Search workflow

1. **Semantic candidates** — embed the query, rank stored vectors by cosine
   similarity (numpy matrix fast path), plus an FTS5 pre-pass.
2. **Live merge** — every plugin runs its per-query `search()` in the
   background; results are merged into the candidate pool *before* reranking
   so they compete on relevance instead of being appended to the tail.
3. **Persist** — novel live rows are written to the database (organic growth).
4. **Rerank last** — LLM cross-encoder over the merged pool; results are
   returned with per-source provenance, similarity scores, and doc links.

## Repo layout

| File | Purpose |
|---|---|
| `apinav_mcp_server.py` | MCP server: keyword search, semantic search (live merge + persist), live search, record fetch, stats |
| `ingest.py` | Organic-growth persistence: quality gates, dedup, upsert + NVIDIA embed + vector-cache append for novel live results |
| `schema.py` | SQLite schema (catalog + FTS5 + embeddings + source registry) |
| `config.yaml` | Central configuration: paths, endpoints, models, tuning parameters |
| `config.py` | Config loader used by all modules |
| `plugins/plugins.yaml` | Plugin registry config: base URLs, limits, cadence, drift notes |
| `sources.py` | Source registry CLI: init, report, set |
| `plugins/` | Live-search plugins (one module per source, uniform `search()` shape) |
| `plugins/base.py` | Shared plugin plumbing: polite pacing, `RateLimited`/Retry-After |
| `spam.py` | Shared spam/junk detection battery |
| `embed_matrix.py` | NVIDIA embedding backfill + numpy vector cache for fast ranking |
| `apinav.py` | CLI and gateway registration helper |

## Plugin contract

Every live plugin has the same shape:

```python
def search(query: str, limit: int = 5) -> list[dict]:
    # plain HTTP GET (or optional local client), polite pacing via plugins/base
    # returns rows normalized to the apinav node shape with a `source` tag

def build_links(row: dict) -> dict:
    # optional: return {"url_docs": ..., "url_spec_json": ..., "url_spec_yaml": ...}
    # for the row's source. The MCP server dispatches through plugins.build_links().
```

- Polite pacing (default 0.5 s between calls per source)
- `RateLimited(seconds)` on HTTP 429 with `Retry-After` parsed and honored
- One dead plugin never takes the others down
- Results are overlays: fetched per query, merged before rerank — and novel
  ids among them are persisted by `ingest.py` (idempotent, lock-safe,
  best-effort: a locked or failed persist never blocks the query)
- Link resolution is owned by the plugin that owns the `source` tag

A plugin that needs a non-public client (e.g. a session-based marketplace
client) loads it as an optional local module named `marketplace_client.py`
(with `open_tab/close_tab/check_rate_limit/fetch_all`); if it is missing the
plugin degrades gracefully to an empty result.

## Requirements

- Python 3.11+
- `openai`-compatible client for embeddings/rerank — API keys are sourced via
  `config.secret()` from the per-host `.env` file (`NVIDIA_API_KEY`,
  `OPENROUTER_API_KEY`); nothing is hardcoded in code or `config.yaml`
- SQLite with FTS5

## Coding standards

This repo is maintained by an autonomous agent, so the bar for readability is
explicit. Follow these when editing:

**Comments say *why*, not *what*.** The *what* belongs in the code (names,
control flow). A comment earns its place when it captures tribal knowledge a
reader would otherwise have to dig out of git history or reverse-engineer:

- Non-obvious data invariants (e.g. `endpoint_count` `-1` = "unknown class", vs
  `0` = junk).
- Gotchas that bite (`INSERT OR REPLACE` wipes a row, so preserve missing
  `endpoint_count`/`source` from the existing row).
- Rationale for a surprising choice (why FTS pre-pass exists, why live results
  merge before rerank, why the audio-direction regex judges the name only).

Generic restatements like `# batch by 32` are noise — delete them.

**Names over comments.** Prefer a descriptive identifier over a comment that
explains a vague one.

**Docstrings are contracts, not history.** State what a function/module does,
its inputs/outputs, and its invariants. Do not put dates or attributions there —
that is what git is for.

**Section banners sparingly.** Use `# --- Section ---` only for big logical
blocks in a long module (the server). Skip them for 3-line helpers.

**Secrets never in config.** Operational settings live in `config.yaml`;
credentials stay in `.env` and are reached only through `config.secret()`.

## Status

Personal home-lab project, published as-is. The catalog database and embedding
matrix are multi-GB build artifacts and are not included — they accumulate
from plugin results as you search.