"""Setlist.fm API client - historical setlists/tour history by artist and city.

Docs: https://api.setlist.fm/docs/1.0/index.html
Free API key: apply via your setlist.fm account settings - approval is
sometimes manual, so this may not be instant like the other services.
"""

import config
from api_clients.base import cached_get

BASE_URL = "https://api.setlist.fm/rest/1.0"
SETLISTS_TTL = 7 * 86400


def _headers() -> dict:
    return {"x-api-key": config.SETLISTFM_API_KEY, "Accept": "application/json"}


def search_artist_mbid(name: str) -> str | None:
    if not config.HAS_SETLISTFM:
        return None
    data = cached_get(
        "setlistfm", f"{BASE_URL}/search/artists",
        params={"artistName": name, "sort": "relevance"}, headers=_headers(), ttl_seconds=SETLISTS_TTL,
    )
    if not data or not data.get("artist"):
        return None
    return data["artist"][0]["mbid"]


def get_setlists(mbid: str, page: int = 1) -> list[dict]:
    """Returns simplified {date, venue, city} dicts, most recent first."""
    if not config.HAS_SETLISTFM:
        return []
    data = cached_get(
        "setlistfm", f"{BASE_URL}/artist/{mbid}/setlists",
        params={"p": page}, headers=_headers(), ttl_seconds=SETLISTS_TTL,
    )
    if not data:
        return []
    simplified = []
    for s in data.get("setlist", []):
        venue = s.get("venue", {})
        city = venue.get("city", {})
        simplified.append({
            "date": s.get("eventDate"),
            "venue": venue.get("name"),
            "city": city.get("name"),
        })
    return simplified


def get_historical_performances(name: str) -> list[dict]:
    """Full known setlist.fm history for an artist by name. Returns [] if the
    artist can't be resolved or the API is unavailable."""
    mbid = search_artist_mbid(name)
    if not mbid:
        return []
    return get_setlists(mbid)
