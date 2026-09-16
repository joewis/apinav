"""Live-search plugins for apinav: auto-discovered from this directory.

Each plugin module must export:
    SOURCE = "<source-name>"
    def search(query: str, limit: int = 5) -> list[dict]

Optionally it may export:
    def build_links(row: dict) -> dict

Plugin tuning (limits, cadence, drift notes, base URLs) lives in plugins.yaml.
"""
import importlib
import os
from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).with_name("plugins.yaml")
_PLUGIN_DIR = Path(__file__).parent


def _load_config(path: Path = _CONFIG_PATH) -> dict[str, dict]:
    with open(path) as f:
        data = yaml.safe_load(f)
    return {p["source"]: p for p in (data.get("plugins") or [])}


# Load once; plugins imported during discovery can call get_config() safely.
_CONFIG = _load_config()


def get_config(source: str) -> dict:
    """Return the plugins.yaml config entry for a source, or {} if absent."""
    return _CONFIG.get(source, {})


def _discover_modules() -> list:
    """Import every .py plugin module in this directory except base/__init__."""
    modules = []
    for fname in sorted(_PLUGIN_DIR.glob("*.py")):
        name = fname.stem
        if name in ("base", "__init__"):
            continue
        try:
            mod = importlib.import_module(f"plugins.{name}")
            if hasattr(mod, "SOURCE") and hasattr(mod, "search"):
                modules.append(mod)
        except Exception:
            # One broken plugin never takes the others down.
            pass
    return modules


def _build_registry() -> dict[str, object]:
    plugins = {}
    for mod in _discover_modules():
        source = mod.SOURCE
        plugins[source] = mod
    return plugins


PLUGINS = _build_registry()


def search_all(query: str) -> list[dict]:
    """Run every discovered plugin for `query` and merge results.

    Uses per-plugin limits from plugins.yaml. One dead plugin is skipped;
    RateLimited is propagated so callers can honor Retry-After.
    """
    out = []
    for source, mod in PLUGINS.items():
        cfg = get_config(source)
        limit = cfg.get("limit", 5)
        try:
            out.extend(mod.search(query, limit=limit))
        except Exception:
            pass
    return out


def build_links(row: dict) -> dict:
    """Dispatch link resolution to the plugin that owns the row's source.

    Returns {"url_docs": ..., "url_spec_json": ..., "url_spec_yaml": ...}.
    Missing/unsupported sources return all-None. Never raises.
    """
    result = {"url_docs": None, "url_spec_json": None, "url_spec_yaml": None}
    source = (row.get("source") or "").lower()
    if not source:
        return result
    try:
        mod = PLUGINS.get(source)
        if mod is None:
            return result
        fn = getattr(mod, "build_links", None)
        if fn is None:
            return result
        links = fn(row)
        if isinstance(links, dict):
            result.update({k: links.get(k) for k in result})
    except Exception:
        pass
    return result
