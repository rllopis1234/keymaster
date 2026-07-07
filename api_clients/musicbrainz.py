"""MusicBrainz API client - free, no API key required, used as the default
genre-tag source for music artists (Spotify's related-data has become
unreliable for new apps - see api_clients/spotify.py).

Docs: https://musicbrainz.org/doc/MusicBrainz_API
Requires a descriptive User-Agent per their usage policy; informal courtesy
rate limit of ~1 request/second.
"""

from api_clients.base import cached_get

BASE_URL = "https://musicbrainz.org/ws/2"
TAGS_TTL = 30 * 86400

_HEADERS = {"User-Agent": "TalentTracker/0.1 (internal agency tool)"}


def search_artist_mbid(name: str) -> str | None:
    data = cached_get(
        "musicbrainz", f"{BASE_URL}/artist/",
        params={"query": name, "fmt": "json"}, headers=_HEADERS, ttl_seconds=TAGS_TTL,
    )
    if not data or not data.get("artists"):
        return None
    return data["artists"][0]["id"]


def get_genres(mbid: str, top_n: int = 5) -> list[str]:
    data = cached_get(
        "musicbrainz", f"{BASE_URL}/artist/{mbid}",
        params={"inc": "tags", "fmt": "json"}, headers=_HEADERS, ttl_seconds=TAGS_TTL,
    )
    if not data:
        return []
    tags = sorted(data.get("tags", []), key=lambda t: t.get("count", 0), reverse=True)
    return [t["name"] for t in tags[:top_n]]


def enrich_music_artist(name: str) -> dict | None:
    mbid = search_artist_mbid(name)
    if not mbid:
        return None
    genres = get_genres(mbid)
    return {"musicbrainz_id": mbid, "genres": genres}
