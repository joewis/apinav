"""HuggingFace Spaces plugin (live overlay).

huggingface.co/api/spaces?search=... — open, no auth (verified
2026-09-11). Spaces are demo apps / hosted models, not REST APIs; rows
carry mcp_type='hf-space' + likes as the quality signal. Docs link =
the space page itself. Weakest of the plugin set (demo-class), keep
limit low so real APIs outrank demos.
"""
import sys, os
sys.path.insert(0, __import__('os').path.dirname(__file__))
import base
from plugins import get_config

BASE = get_config("hfspace").get("base_url", "https://huggingface.co/api/spaces")
SOURCE = "hfspace"


def search(query: str, limit: int = 5) -> list[dict]:
    d = base.get(f"{BASE}?search={base.enc(query)}&limit={min(limit, 10)}", SOURCE)
    rows = sorted(d or [], key=lambda s: s.get("likes") or 0, reverse=True)
    return [_normalize(s) for s in rows[:limit]]


def _normalize(s: dict) -> dict:
    sid = s.get("id") or ""
    return {
        "id": f"hfspace:{sid}",
        "name": (sid.split("/")[-1].replace("-", " ").replace("_", " ").strip().title()
                 if sid else sid),
        "description": (s.get("cardData") or {}).get("summary") or "",
        "slugifiedName": sid.split("/")[-1] if sid else None,
        "pricing": None,
        "categoryName": "HF Space (demo)",
        "updatedAt": s.get("lastModified"),
        "endpoint_count": -1,  # demo app class, not REST
        "source": SOURCE,
        "mcp_type": "hf-space",
        "author": (sid.split("/")[0] if "/" in sid else None),
        "likes": s.get("likes"),
        "humanURL": f"https://huggingface.co/spaces/{sid}",
    }


def build_links(row: dict) -> dict:
    return {
        "url_docs": row.get("humanURL"),
        "url_spec_json": None,
        "url_spec_yaml": None,
    }
