"""apis.io live-search plugin (source='apisio').

Their search (q= param, verified live 2026-09-11) is genuinely good: BM25-style
relevance over 133.6k APIs, agent-permitted (robots.txt + terms), no auth.
This module wraps it as the second live source for apinav search tools.

Design (Joerg): apis.io search results are MERGED into the semantic-search
candidate pool (merge-first / rerank-last pipeline, user-mandated ordering)
and NEW rows are persisted into the local catalog + embedded — same
per-query plugin pattern, plain HTTP: no special sessions, no CSRF, no challenges.

Rate-limit contract: plain GETs, paced (SEARCH_DELAY between calls); any 429
raises RateLimited (shared type from plugins/base) so callers handle it the
same way. Retry-After parsed when present.
"""
import json
import time
import urllib.parse
import urllib.request

BASE = "https://apis.io/api/v1"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Elitedesk-API-Search"}
SEARCH_DELAY = 0.7  # polite: ~1.4 req/s max on their open API
_last_call = [0.0]


class RateLimited(Exception):
    """apis.io answered 429; .seconds = Retry-After (may be None)."""

    def __init__(self, seconds=None):
        self.seconds = seconds
        super().__init__(f"apis.io 429, retry_after={seconds}")


def _get(url: str) -> dict:
    # pacing shared across calls
    since = time.time() - _last_call[0]
    if since < SEARCH_DELAY:
        time.sleep(SEARCH_DELAY - since)
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            _last_call[0] = time.time()
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            ra = e.headers.get("Retry-After") if e.headers else None
            seconds = int(ra) if (ra and ra.isdigit()) else None
            raise RateLimited(seconds) from None
        raise


def search(query: str, limit: int = 5) -> list[dict]:
    """Live apis.io search. Returns normalized node dicts (apinav row shape).

    Uses /search (q=) which returns curated top-5 per type; falls back to
    /apis?q= pagination for the full sortable list. The description embeds
    'N operation(s)' — parsed as the endpoint count (verified format)."""
    d = _get(f"{BASE}/search?query=&q=" + urllib.parse.quote(query))
    nodes = []
    for t in (d.get("apis", {}).get("top") or [])[:limit]:
        nodes.append(_normalize(t))
    return nodes


def search_curated(query: str, limit: int = 10) -> list[dict]:
    """PRIMARY overlay path (Joerg-directed 2026-09-11, the 'moomoo' case):
    /search?q= — their curated/semantic search. Brand-tolerant: bare brand
    names like 'moomoo' resolve to the right API (Futu OpenAPI) where the
    literal /apis?q= full list returns 0. Nodes carry 'aio_rank' = their
    0-based position in apis.io's curated ranking — downstream _rerank uses
    it as a small additive boost because the cross-encoder only sees the
    stored text (which says 'Futu', not 'Moomoo') and would threshold the
    bridge away. Their ranking IS the brand-bridge signal."""
    d = _get(f"{BASE}/search?query=&q=" + urllib.parse.quote(query))
    nodes = []
    for i, t in enumerate((d.get("apis", {}).get("top") or [])[:limit]):
        n = _normalize(t)
        n["aio_rank"] = i
        nodes.append(n)
    return nodes


def search_full(query: str, limit: int = 25) -> list[dict]:
    """Literal-match full list via /apis?q= (paginated, not just top-5).

    EXTENSION path: appends more candidates when curated search returns
    fewer than the overlay wants — literal match only, 0 for brand names."""
    d = _get(f"{BASE}/apis?q=" + urllib.parse.quote(query) + f"&limit={min(limit,100)}")
    return [_normalize(r) for r in d.get("data", [])]


def get_api(aid: str) -> dict | None:
    """Detail fetch: adds `properties` (spec URLs) to the normalized node.

    NOTE: the aid must go in the path UNQUOTED — the colon is a path segment
    separator their router expects raw (quote() 404s, verified 2026-09-11)."""
    try:
        d = _get(f"{BASE}/apis/{aid}")
        node = _normalize(d)
        props = d.get("properties") or []
        spec = next((p["url"] for p in props if p.get("type") == "OpenAPI" and p.get("url")), None)
        if spec:
            node["spec_url"] = spec
        return node
    except Exception:
        return None


def _normalize(r: dict) -> dict:
    """Map an apis.io row to the apinav node dict shape (schema.upsert_api keys)."""
    desc = r.get("description") or ""
    # 'The Balances API from Airwallex — 3 operation(s) for beneficiaries.'
    ep_count = None
    marker = "operation(s)"
    if marker in desc:
        try:
            head = desc.split(marker)[0]
            ep_count = int(head.split()[-1])
        except (ValueError, IndexError):
            ep_count = None
    # Fallback: artifact_count hints at least one machine-readable artifact
    if ep_count is None:
        ep_count = 1 if r.get("artifact_count") else -1
    return {
        "id": r.get("aid"),
        "name": r.get("name"),
        "description": desc,
        "slugifiedName": r.get("slug"),
        "pricing": None,
        "categoryName": (r.get("tags") or [None])[0],
        "updatedAt": None,
        "endpoint_count": ep_count,
        "source": "apisio",
        # extras kept out of the SQL row, used by link resolution + display
        "provider_name": r.get("provider_name"),
        "provider_slug": r.get("provider_slug"),
        "baseURL": r.get("baseURL"),
        "humanURL": r.get("humanURL"),
    }