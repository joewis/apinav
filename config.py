"""Central config loader for apinav.

Reads config.yaml once at import. All modules import settings from here
instead of keeping hard-coded values.

Two kinds of path are kept strictly apart:

  * CODE  (this directory) — shipped by the package, root-owned and read-only
    to the account that runs the tool. APINAV_DIR.
  * STATE (the catalog, the embedding matrix) — written at runtime by whatever
    account runs the tool. STATE_DIR, resolved from the environment so a
    service account can point it at its own writable tree.

Keeping them apart matters: if state lived beside the code in a directory the
service account can write, that account could rewrite the program it runs.
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
    return default if d is None else d


# --- Paths ------------------------------------------------------------------

# Where the code lives (this file's directory's configured home).
APINAV_DIR = Path(get("paths", "apinav_dir", default=str(Path(__file__).parent)))


def _default_state_dir() -> Path:
    """Writable state location when the environment says nothing.

    APINAV_STATE_DIR wins so a service can be pointed at its own tree;
    otherwise the XDG state directory, which is correct for interactive use
    and never inside the (read-only) code tree.
    """
    env = os.environ.get("APINAV_STATE_DIR")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "apinav"


STATE_DIR = Path(get("paths", "state_dir", default=str(_default_state_dir())))

# The catalog and the embedding cache are state, not shipped content: the
# package installs code only, and these are rebuilt/regrown in place.
DB_PATH = Path(
    os.environ.get("APINAV_DB_PATH")
    or get("paths", "db")
    or str(STATE_DIR / "catalog.db")
)

# Dotenv fallback for interactive use. Normally absent under the service
# account, where credentials arrive by injection instead.
ENV_FILE = Path(
    os.environ.get("APINAV_ENV_FILE")
    or get("paths", "env_file")
    or str(Path(__file__).with_name(".env"))
)

# --- Secrets ---------------------------------------------------------------
# Resolution order is fixed, and mirrors the gateway's shared secret_source
# helper so there is one contract on this box:
#
#   1. the process environment — where the MCP gateway injects credentials, via
#      the apinav backend's `env:` block (expanded from its own gopass store)
#   2. the dotenv file at ENV_FILE, if it exists
#
# The file is consulted only when the environment is silent, so a value the
# gateway supplied always wins.
#
# Callers use provider-agnostic names (EMBEDDING_API_KEY, RERANK_API_KEY) while
# the environment and the dotenv file use the provider's own name
# (NVIDIA_API_KEY, OPENROUTER_API_KEY). _ALIASES maps the two so either
# spelling resolves, without renaming every call site.
_SECRETS: dict[str, str | None] = {}

_ALIASES: dict[str, tuple[str, ...]] = {
    "EMBEDDING_API_KEY": ("NVIDIA_API_KEY",),
    "RERANK_API_KEY": ("OPENROUTER_API_KEY",),
}


def _candidates(name: str) -> tuple[str, ...]:
    """Every name `name` may be spelled as, in lookup order."""
    return (name,) + _ALIASES.get(name, ())


def _from_env(name: str) -> str | None:
    for key in _candidates(name):
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip()
    return None


def _from_file(name: str) -> str | None:
    """Read NAME=value (or an alias) from the dotenv fallback.

    Returns None when the file is absent, unreadable, or does not define any
    candidate name — all three mean the same thing to a caller.
    """
    wanted = _candidates(name)
    try:
        lines = ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep and key.strip() in wanted:
            value = value.strip().strip('"').strip("'")
            return value or None
    return None


def secret(name: str) -> str | None:
    """Return credential `name` from the environment, else the dotenv fallback."""
    if name not in _SECRETS:
        _SECRETS[name] = _from_env(name) or _from_file(name)
    return _SECRETS[name]


def source_of(name: str) -> str:
    """Report where secret(name) would resolve from, without returning it.

    Diagnostics only — nothing should branch on this in production code.
    """
    if _from_env(name) is not None:
        return "env"
    if _from_file(name) is not None:
        return "file"
    return "missing"
