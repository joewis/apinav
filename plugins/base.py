"""Shared plumbing for live-search plugins: HTTP, pacing, and 429 handling.

Every plugin routes its requests through this so rate-limits are paced and
respected uniformly, and so one provider answering 429 surfaces as a
RateLimited that the caller can honor (Retry-After, or just skip).
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import config

# Polite pacing + timeout sourced from central config, not per-plugin.
UA = {"User-Agent": config.get("http", "user_agent")}
PACING = config.get("http", "pacing")
TIMEOUT = config.get("http", "timeout")
_last_call = {}


class RateLimited(Exception):
    """Source answered 429; .seconds = Retry-After (may be None)."""

    def __init__(self, seconds=None, source=""):
        self.seconds = seconds
        self.source = source
        super().__init__(f"{source} 429, retry_after={seconds}")


def get(url: str, source: str, delay: float = PACING, timeout: int = TIMEOUT) -> dict:
    """Polite paced GET returning parsed JSON. Raises RateLimited on 429.

    `source` keys the pacing tracker so each provider gets its own timer.
    """
    now = time.time()
    since = now - _last_call.get(source, 0.0)
    if since < delay:
        # Space out calls to the same source to stay under its rate cap.
        time.sleep(delay - since)
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            _last_call[source] = time.time()
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            # Honor Retry-After when the provider gives one (seconds).
            ra = e.headers.get("Retry-After") if e.headers else None
            seconds = int(ra) if (ra and ra.isdigit()) else None
            raise RateLimited(seconds, source) from None
        raise


def enc(s: str) -> str:
    return urllib.parse.quote(s or "")