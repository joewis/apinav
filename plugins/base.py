"""Shared plumbing for live-search plugins: HTTP + RateLimited + pacing.

Joerg's plugin route (2026-09-11): live results enter the candidate pool
BEFORE the reranker; novel results are persisted by ingest.py (organic growth) — per-query fetch only.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Elitedesk-API-Search"}
PACING = 0.5  # seconds between calls per plugin (polite default)
_last_call = {}


class RateLimited(Exception):
    """Source answered 429; .seconds = Retry-After (may be None)."""

    def __init__(self, seconds=None, source=""):
        self.seconds = seconds
        self.source = source
        super().__init__(f"{source} 429, retry_after={seconds}")


def get(url: str, source: str, delay: float = PACING, timeout: int = 30) -> dict:
    """Polite paced GET returning parsed JSON. Raises RateLimited on 429."""
    now = time.time()
    since = now - _last_call.get(source, 0.0)
    if since < delay:
        time.sleep(delay - since)
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            _last_call[source] = time.time()
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            ra = e.headers.get("Retry-After") if e.headers else None
            seconds = int(ra) if (ra and ra.isdigit()) else None
            raise RateLimited(seconds, source) from None
        raise


def enc(s: str) -> str:
    return urllib.parse.quote(s or "")