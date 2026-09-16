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
