"""Central config loader for apinav.

Reads config.yaml once at import. All modules import settings from here
instead of keeping hard-coded values.
"""
import os
from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).with_name("config.yaml")


def _load(path: Path = _CONFIG_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


_cfg = _load()

# Helper to navigate nested dicts with a default.
def get(*keys, default=None):
    d = _cfg
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


# Convenience paths
APINAV_DIR = Path(get("paths", "apinav_dir", default="/home/carl/apinav"))
ENV_FILE = Path(get("paths", "env_file", default="/home/carl/.hermes/.env"))
DB_PATH = Path(get("paths", "db", default="/home/carl/apinav/catalog.db"))

# --- Secrets (kept in .env, never in config.yaml) --------------------------
# Each key is a dotenv VAR_NAME. `secret("EMBEDDING_API_KEY")` reads it from
# the env file at import; caller is responsible for the env var name.
_SECRETS: dict[str, str | None] = {}


def _load_secret(name: str) -> str | None:
    try:
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        return None
    return None


def secret(name: str) -> str | None:
    """Look up a dotenv secret by its VAR_NAME."""
    if name not in _SECRETS:
        _SECRETS[name] = _load_secret(name)
    return _SECRETS[name]
