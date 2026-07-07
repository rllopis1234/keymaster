"""Bridges api_clients/* (network + cache) into db.py (persistent talent
records) and supplies read-only supplementary data for the dashboard.

Two responsibilities, kept deliberately separate from metrics.py:
  1. `enrich_talent_if_needed` - a one-time-per-talent DB write (genres/IDs),
     safe to call on every form submission since it no-ops once populated.
  2. `get_live_context` - a read-only, call-every-render aggregator of live
     API data shown as supplementary context in the UI. It never writes to
     the DB, so it can't create duplicate historical_comps rows across
     Streamlit reruns; the core revenue/expense math in metrics.py stays
     100% DB-driven and testable without any network access.
"""

import json

import config
import db
from api_clients import musicbrainz, setlistfm, spotify, ticketmaster, tmdb


def enrich_talent_if_needed(talent_row) -> dict:
    talent = dict(talent_row)
    if talent["genres_json"] and talent["genres_json"] != "[]":
        return talent  # already enriched, don't re-hit APIs

    external_ids = json.loads(talent["external_ids_json"] or "{}")
    genres: list[str] = []

    if talent["domain"] == "actor":
        result = tmdb.enrich_actor(talent["name"])
        if result:
            external_ids["tmdb_person_id"] = result["tmdb_person_id"]
            genres = result["genres"]
    else:
        mb_result = musicbrainz.enrich_music_artist(talent["name"])
        if mb_result:
            external_ids["musicbrainz_id"] = mb_result["musicbrainz_id"]
            genres = mb_result["genres"]
        if not genres and config.HAS_SPOTIFY:
            spotify_genres = spotify.get_genres_for_artist(talent["name"])
            if spotify_genres:
                genres = spotify_genres

    if genres or external_ids != json.loads(talent["external_ids_json"] or "{}"):
        db.update_talent_enrichment(talent["id"], external_ids, genres)
        talent = dict(db.get_talent(talent["id"]))

    return talent


def get_live_context(talent_name: str, domain: str, city: str) -> dict:
    """Read-only supplementary data for the dashboard. Every key degrades to
    an empty/None value if the relevant API key is missing or the call
    fails - callers should treat absence as 'not available', not an error."""
    context: dict = {}

    if domain == "music":
        context["ticketmaster_price_estimate"] = (
            ticketmaster.estimate_ticket_price_for_city(talent_name, city)
            if config.HAS_TICKETMASTER else None
        )
        context["ticketmaster_events"] = (
            ticketmaster.search_events(talent_name, city=city) if config.HAS_TICKETMASTER else []
        )
        context["setlistfm_history"] = (
            setlistfm.get_historical_performances(talent_name) if config.HAS_SETLISTFM else []
        )
    else:
        person = tmdb.search_person(talent_name) if config.HAS_TMDB else None
        context["tmdb_profile"] = person

    return context
