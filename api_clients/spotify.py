"""Spotify Web API client - OPTIONAL, best-effort genre enrichment for music
artists. Treated as the lowest-priority data source in this app:

- The `related-artists` endpoint is no longer available to new apps (Spotify
  restricted it in Nov 2024), so no similarity/popularity signal is used here.
- Some Spotify API changes reported in early 2026 suggest Development Mode
  access may now require the developer's own account to have Premium, and
  that `followers`/`popularity` fields may be stripped from responses. This
  is unverified and may be wrong or may change again - so every function here
  fails soft (catches errors, returns None) rather than assuming any of it.

If this integration doesn't work in your account, the app falls back to
MusicBrainz (api_clients/musicbrainz.py, no key required) for genre data.
"""

import base64
import time

import requests

import config
from api_clients.base import cached_get

TOKEN_URL = "https://accounts.spotify.com/api/token"
SEARCH_URL = "https://api.spotify.com/v1/search"
SEARCH_TTL = 7 * 86400

_token_cache = {"access_token": None, "expires_at": 0}


def _get_access_token() -> str | None:
    if not config.HAS_SPOTIFY:
        return None
    if _token_cache["access_token"] and _token_cache["expires_at"] > time.time():
        return _token_cache["access_token"]

    credentials = f"{config.SPOTIFY_CLIENT_ID}:{config.SPOTIFY_CLIENT_SECRET}".encode()
    auth_header = base64.b64encode(credentials).decode()
    try:
        response = requests.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {auth_header}"},
            timeout=10,
        )
    except requests.RequestException:
        return None

    if response.status_code != 200:
        # Covers the reported Premium-account requirement (403) and any other
        # auth failure - Spotify is simply unavailable, use MusicBrainz instead.
        return None

    payload = response.json()
    _token_cache["access_token"] = payload["access_token"]
    _token_cache["expires_at"] = time.time() + payload.get("expires_in", 3600) - 30
    return _token_cache["access_token"]


def get_genres_for_artist(name: str) -> list[str]:
    """Returns Spotify genre tags for the best-matching artist, or [] if
    Spotify is unavailable/unconfigured/errors for any reason."""
    token = _get_access_token()
    if not token:
        return []

    data = cached_get(
        "spotify", SEARCH_URL,
        params={"q": name, "type": "artist", "limit": 5},
        headers={"Authorization": f"Bearer {token}"},
        ttl_seconds=SEARCH_TTL,
    )
    if not data:
        return []
    items = data.get("artists", {}).get("items", [])
    if not items:
        return []
    return items[0].get("genres", [])
