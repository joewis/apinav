"""Source link-builder table: one row per catalog source, holding the URL
recipes to reach an API's docs/specs. Tokens like {version} resolve at call
time from the row's `raw` JSON or a cached listing — no per-API column needed.

Canonical source of this design (Joerg, 2026-09-10): 'add a table that holds
the information to build the links to the api based on the source as key'.
If this drifts from the vault (API Search.md), trust the vault.
"""
import json
import os
import time

CACHE_PATH = "/home/carl/apinav/cache/apisguru_list.json"

# Default rows inserted on init. Templates use str.format-style placeholders.
# tokens_* keys name where each placeholder's value comes from:
#   "raw"  -> parse the API row's raw JSON with the given JSON-path-ish expr
#   "id"   -> derive from the row's id (regex group)
#   "row"  -> a plain column value
#   "cache"-> look up in the cached source listing (e.g. apisguru preferred)
DEFAULT_SOURCE_LINKS = [
    {
        "source": "apisio",
        "url_docs": "{humanURL}",
        "url_spec_json": None,
        "url_spec_yaml": None,
        "notes": "LIVE OVERLAY (Joerg 2026-09-11: NO apis.io scraping/dump). "
                 "Live summaries carry humanURL/baseURL on the node; the "
                 "semantic-search merge passes a synthetic raw carrying them "
                 "so url_docs resolves per-query. spec URLs would need a "
                 "detail fetch per query — skipped for latency.",
        "tokens": json.dumps({
            "url_docs": {
                "humanURL": {"from": "raw", "path": ["humanURL"]},
            },
        }),
    },
    {
        "source": "rapidapi",
        "url_docs": None,
        "url_spec_json": None,
        "url_spec_yaml": None,
        "notes": "Live overlay source: docs links come from the live row when "
                 "resolvable; no static URL template is configured.",
        "tokens": json.dumps({}),
    },
    {
        "source": "apisguru",
        "url_docs": "https://api.apis.guru/v2/specs/{domain}/{service}/{version}/openapi.json",
        "url_spec_json": "https://api.apis.guru/v2/specs/{domain}/{service}/{version}/openapi.json",
        "url_spec_yaml": "https://api.apis.guru/v2/specs/{domain}/{service}/{version}/openapi.yaml",
        "notes": "id is '<domain>:<service>' (or '<domain>' when no colon); "
                 "version comes from the cached list.json preferred version.",
        "tokens": json.dumps({
            "url_docs": {
                "domain": {"from": "id", "regex": r"^([^:]+)"},
                "service": {"from": "id", "regex": r"(?::)(.+)$"},
                "version": {"from": "cache", "listing": "apisguru", "path": ["preferred"]},
            },
            "url_spec_json": {
                "domain": {"from": "id", "regex": r"^([^:]+)"},
                "service": {"from": "id", "regex": r"(?::)(.+)$"},
                "version": {"from": "cache", "listing": "apisguru", "path": ["preferred"]},
            },
            "url_spec_yaml": {
                "domain": {"from": "id", "regex": r"^([^:]+)"},
                "service": {"from": "id", "regex": r"(?::)(.+)$"},
                "version": {"from": "cache", "listing": "apisguru", "path": ["preferred"]},
            },
        }),
    },
]


def init_source_links(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS source_links (
               source TEXT PRIMARY KEY,
               url_docs TEXT,
               url_spec_json TEXT,
               url_spec_yaml TEXT,
               tokens TEXT,          -- JSON: placeholder -> resolver rule
               notes TEXT
           )"""
    )
    for row in DEFAULT_SOURCE_LINKS:
        conn.execute(
            """INSERT OR IGNORE INTO source_links
               (source, url_docs, url_spec_json, url_spec_yaml, tokens, notes)
               VALUES (?,?,?,?,?,?)""",
            (
                row["source"], row["url_docs"], row["url_spec_json"],
                row["url_spec_yaml"], row["tokens"], row["notes"],
            ),
        )
    conn.commit()


def _apisguru_preferred(api_id: str, cache: dict) -> str | None:
    entry = cache.get(api_id)
    if entry:
        return entry.get("preferred")
    # some keys are 'domain:service'; try plain domain too
    return (cache.get(api_id.split(":")[0]) or {}).get("preferred")


def _apisguru_parts(api_id: str, cache: dict) -> dict:
    """Resolve domain/service/version for an apisguru row id.

    ids come in two shapes (verified 2026-09-10 against live list.json):
      'domain:Service'  -> spec path /<domain>/<service>/<version>/openapi.json
      'domain'          -> spec path /<domain>/<version>/swagger.json (2 parts!)
    The listing entry itself carries the authoritative swaggerUrl — prefer it
    verbatim; template fallback only when absent."""
    parts = {}
    entry = cache.get(api_id) or {}
    pref = entry.get("preferred")
    ver = (entry.get("versions") or {}).get(pref) if pref else None
    if not ver:
        return parts
    parts["version"] = pref
    swagger = ver.get("swaggerUrl") or ""
    # parse the authoritative spec path out of swaggerUrl
    # https://api.apis.guru/v2/specs/<rest>/openapi.json|swagger.json
    marker = "/v2/specs/"
    if marker in swagger:
        tail = swagger.split(marker, 1)[1]
        parts["_spec_tail"] = tail.rsplit("/", 1)[0]  # e.g. 'adyen.com/AccountService/6'
    return parts


def build_links(conn, row) -> dict:
    """Return {'url_docs':..., 'url_spec_json':..., 'url_spec_yaml':..., ...} for an
    apis row. Accepts either a sqlite3.Row (full catalog row) or a summary
    dict (live-merged node: id/source/slug/author keys). Resolves placeholders
    via the source's token rules. Missing pieces become None; never raises."""
    out = {"url_docs": None, "url_spec_json": None, "url_spec_yaml": None}
    try:
        # Normalize access: sqlite3.Row or dict both support [] + get().
        _get = row.get if isinstance(row, dict) else (lambda k, d=None: row[k] if k in row.keys() else d)
        _has = (lambda k: k in row) if isinstance(row, dict) else (lambda k: k in row.keys())
        if not _has("source"):
            try:
                r2 = conn.execute(
                    "SELECT source, slug FROM apis WHERE id=?", (row["id"],)
                ).fetchone()
                if r2:
                    row = dict(r2) | dict(row)  # row wins; DB fills the gaps
            except Exception:
                pass
        # apisguru fast path: the listing's swaggerUrl IS the authoritative
        # spec URL (covers both id shapes; no-colon ids have 2-part paths and
        # swagger 2.0 filenames the generic template can't express).
        if _get("source") == "apisguru":
            cache = _load_apisguru_cache()
            parts = _apisguru_parts(row["id"], cache)
            tail = parts.get("_spec_tail")
            if tail:
                # The listing's swaggerUrl verbatim is the truth; the guessed
                # openapi.json/openapi.yaml pair only when the verbatim URL
                # already matches that naming (OpenAPI 3.x entries do).
                entry = cache.get(row["id"]) or {}
                ver = (entry.get("versions") or {}).get(parts.get("version")) or {}
                sw = ver.get("swaggerUrl")
                sw_yaml = ver.get("swaggerYamlUrl")
                if sw and "/openapi." in sw:
                    out["url_spec_json"] = sw
                    out["url_spec_yaml"] = sw_yaml or None
                else:
                    out["url_spec_json"] = sw or None
                    out["url_spec_yaml"] = sw_yaml or None
                out["url_docs"] = out["url_spec_json"]
                return out
        meta = conn.execute(
            "SELECT * FROM source_links WHERE source=?", (row["source"],)
        ).fetchone()
        if not meta:
            return out
        tokens = json.loads(meta["tokens"] or "{}")
        raw = {}
        try:
            raw = json.loads(row["raw"]) if row["raw"] else {}
        except Exception:
            pass
        if not raw:
            # Live-merged rows arrive as summary dicts without the raw payload;
            # pull it from the catalog so tokens like raw.user.username work.
            try:
                r2 = conn.execute(
                    "SELECT raw FROM apis WHERE id=?", (row["id"],)
                ).fetchone()
                if r2 and r2["raw"]:
                    raw = json.loads(r2["raw"])
            except Exception:
                pass

        # cache for listing-based lookups (lazy, per call)
        cache = None
        if any(
            rule.get("from") == "cache" and rule.get("listing") == "apisguru"
            for rules in tokens.values() for rule in rules.values()
        ):
            cache = _load_apisguru_cache()

        def resolve(rule):
            frm = rule.get("from")
            if frm == "raw":
                v = raw
                for k in rule.get("path", []):
                    if not isinstance(v, dict):
                        return None
                    v = v.get(k)
                return v
            if frm == "row":
                try:
                    return row[rule.get("column")]
                except (IndexError, KeyError):
                    return None
            if frm == "id":
                import re
                m = re.search(rule.get("regex", ""), row["id"] or "")
                return m.group(1) if m and m.groups() else None
            if frm == "cache":
                c = cache.get(row["id"]) if cache else None
                for k in rule.get("path", []):
                    c = c.get(k) if isinstance(c, dict) else None
                return c
            return None

        for field in ("url_docs", "url_spec_json", "url_spec_yaml"):
            tpl = meta[field]
            if not tpl:
                continue
            rules = tokens.get(field, {})
            vals = {name: resolve(rule) for name, rule in rules.items()}
            if any(v is None for v in vals.values()):
                continue  # incomplete -> skip this field (agent falls back)
            try:
                out[field] = tpl.format(**vals)
            except (KeyError, IndexError):
                continue
        return out
    except Exception:
        return out


def _load_apisguru_cache() -> dict:
    """Load (and lazily fetch) the APIs.guru list.json mapping api_key -> entry."""
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    import urllib.request
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    url = "https://api.apis.guru/v2/list.json"
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.loads(r.read())
    with open(CACHE_PATH, "w") as f:
        json.dump(data, f)
    return data