"""TMDB API client - actor search, popularity, and genre enrichment.

Docs: https://developer.themoviedb.org/reference/intro/getting-started
Free API key: https://www.themoviedb.org/settings/api

TMDB has no "similar person" endpoint and no data on appearance fees, so this
client only supplies popularity + genre signals. Similarity ranking itself
happens in metrics.py against whatever talents/historical_comps already exist
locally.
"""

from collections import Counter

import config
from api_clients.base import cached_get

BASE_URL = "https://api.themoviedb.org/3"
GENRE_TTL = 30 * 86400  # genre lists barely change
PERSON_TTL = 7 * 86400
SEARCH_TTL = 86400


def _auth_params() -> dict:
    return {"api_key": config.TMDB_API_KEY}


def _genre_map() -> dict[int, str]:
    genres: dict[int, str] = {}
    for kind in ("movie", "tv"):
        data = cached_get(
            "tmdb", f"{BASE_URL}/genre/{kind}/list", params=_auth_params(), ttl_seconds=GENRE_TTL
        )
        if data:
            for g in data.get("genres", []):
                genres[g["id"]] = g["name"]
    return genres


def search_person(name: str) -> dict | None:
    """Returns {tmdb_id, name, popularity} for the best-matching person, or None."""
    if not config.HAS_TMDB:
        return None
    data = cached_get(
        "tmdb", f"{BASE_URL}/search/person",
        params={**_auth_params(), "query": name}, ttl_seconds=SEARCH_TTL,
    )
    if not data or not data.get("results"):
        return None
    top = max(data["results"], key=lambda r: r.get("popularity", 0))
    return {"tmdb_id": top["id"], "name": top["name"], "popularity": top.get("popularity", 0)}


def search_people(query: str, limit: int = 8) -> list[str]:
    """Candidate person names for autocomplete (not just the single best match)."""
    if not config.HAS_TMDB or not query:
        return []
    data = cached_get(
        "tmdb", f"{BASE_URL}/search/person",
        params={**_auth_params(), "query": query}, ttl_seconds=SEARCH_TTL,
    )
    if not data:
        return []
    results = sorted(data.get("results", []), key=lambda r: r.get("popularity", 0), reverse=True)
    return [r["name"] for r in results[:limit]]


def get_genres_for_person(tmdb_id: int, top_n: int = 5) -> list[str]:
    """Derives genre tags from the person's combined film/TV credits."""
    if not config.HAS_TMDB:
        return []
    data = cached_get(
        "tmdb", f"{BASE_URL}/person/{tmdb_id}/combined_credits",
        params=_auth_params(), ttl_seconds=PERSON_TTL,
    )
    if not data:
        return []
    genre_ids = Counter()
    for credit in data.get("cast", []) + data.get("crew", []):
        for gid in credit.get("genre_ids", []):
            genre_ids[gid] += 1
    if not genre_ids:
        return []
    genre_lookup = _genre_map()
    ranked_ids = [gid for gid, _ in genre_ids.most_common(top_n)]
    return [genre_lookup[gid] for gid in ranked_ids if gid in genre_lookup]


def enrich_actor(name: str) -> dict | None:
    """Returns {tmdb_person_id, popularity, genres} for use by db.update_talent_enrichment,
    or None if TMDB is unavailable or the actor isn't found."""
    person = search_person(name)
    if not person:
        return None
    genres = get_genres_for_person(person["tmdb_id"])
    return {
        "tmdb_person_id": person["tmdb_id"],
        "popularity": person["popularity"],
        "genres": genres,
    }
