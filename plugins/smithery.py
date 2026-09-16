"""Smithery MCP Registry plugin (live overlay).

registry.smithery.ai/servers?q=... — open, no auth, JSON, paginated.
174 curated MCP servers (verified 2026-09-11), quality signals included
(verified badge, useCount, score). MCP servers are the layer above APIs:
the result row is annotated with mcp_type='mcp-server' so agents know it
isn't a REST API. Docs link = homepage (verified present on rows).
"""
import sys, os
sys.path.insert(0, __import__('os').path.dirname(__file__))
import base
from plugins import get_config

BASE = get_config("smithery").get("base_url", "https://registry.smithery.ai")
SOURCE = "smithery"


def search(query: str, limit: int = 5) -> list[dict]:
    d = base.get(f"{BASE}/servers?q={base.enc(query)}&pageSize={min(limit, 25)}&page=1",
                 SOURCE)
    out = []
    for s in d.get("servers") or []:
        if s.get("unlisted") or s.get("inactive"):
            continue
        out.append(_normalize(s))
    return out


def _normalize(s: dict) -> dict:
    desc = s.get("description") or ""
    return {
        "id": f"smithery:{s.get('qualifiedName') or s.get('id')}",
        "name": s.get("displayName") or s.get("qualifiedName"),
        "description": desc,
        "slugifiedName": s.get("slug") or s.get("qualifiedName"),
        "pricing": None,
        "categoryName": "MCP Server",
        "updatedAt": s.get("createdAt"),
        # MCP servers expose tools, not REST endpoints; -1 marks 'N/A class'
        "endpoint_count": -1,
        "source": SOURCE,
        "mcp_type": "mcp-server",
        "author": (s.get("owner") or {}).get("name") if isinstance(s.get("owner"), dict) else s.get("owner"),
        "use_count": s.get("useCount"),
        "verified": s.get("verified"),
        # extras for live link resolution
        "homepage": s.get("homepage"),
        "humanURL": s.get("homepage"),
    }


def build_links(row: dict) -> dict:
    return {
        "url_docs": row.get("humanURL") or row.get("homepage"),
        "url_spec_json": None,
        "url_spec_yaml": None,
    }
