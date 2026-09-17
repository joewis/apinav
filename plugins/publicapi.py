# Public API for public-apis.org plugin
# Fetches from https://api.publicapis.org/ — no auth required.
# https://github.com/davemachado/public-api
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import urllib.request
import json


SOURCE = "public-api.org"


def _api(path, params=None):
    """Make a request to the public APIs.org service."""
    base = "https://api.publicapis.org"
    url = f"{base}{path}"
    if params:
        qs = urllib.parse.urlencode(params)
        url = f"{url}?{qs}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "apinav-plugin"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except Exception:
        return None


def search(query, limit=5):
    """Search the public-apis.org directory for APIs matching the query.

    Uses the public API at api.publicapis.org:
    - GET /entries?title={query} — substring match on entry name
    - GET /entries?category={query} — category filter
    - GET /random — random entry (fallback when title search returns 0)

    The public-apis project maintains a community-curated list of free APIs.
    No authentication required.
    """
    results = []

    # Try searching by title first
    data = _api("/entries", {"title": query})
    if data and data.get("entries"):
        for entry in data["entries"][:limit]:
            results.append(
                {
                    "name": entry.get("API", "Unnamed API"),
                    "description": entry.get("Description", ""),
                    "source": "public-api.org",
                    "auth": entry.get("Auth", "unknown"),
                    "category": entry.get("Category", ""),
                    "url": entry.get("URL", ""),
                }
            )
    
    # If title search returned nothing, try category search
    if not results:
        data = _api("/entries", {"category": query})
        if data and data.get("entries"):
            for entry in data["entries"][:limit]:
                results.append(
                    {
                        "name": entry.get("API", "Unnamed API"),
                        "description": entry.get("Description", ""),
                        "source": "public-api.org",
                        "auth": entry.get("Auth", "unknown"),
                        "category": entry.get("Category", ""),
                        "url": entry.get("URL", ""),
                    }
                )
    
    # Final fallback: get a random entry
    if not results:
        data = _api("/random")
        if data and data.get("entry"):
            entry = data["entry"]
            results.append(
                {
                    "name": entry.get("API", "Unnamed API"),
                    "description": entry.get("Description", ""),
                    "source": "public-api.org",
                    "auth": entry.get("Auth", "unknown"),
                    "category": entry.get("Category", ""),
                    "url": entry.get("URL", ""),
                }
            )
    
    return results