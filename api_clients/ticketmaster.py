"""Ticketmaster Discovery API client - event/venue search and ticket pricing.

Docs: https://developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/
Free API key (instant self-serve): https://developer.ticketmaster.com/

Note: the Discovery API surfaces current/on-sale events, not a true historical
box-office archive - it's used here for ticket price benchmarking (via
priceRanges on live/recent event listings), not for the "historical
performance" summary (that's Setlist.fm's job).
"""

import config
from api_clients.base import cached_get

BASE_URL = "https://app.ticketmaster.com/discovery/v2"
EVENTS_TTL = 86400


def _auth_params() -> dict:
    return {"apikey": config.TICKETMASTER_API_KEY}


def search_events(keyword: str, city: str | None = None, size: int = 20) -> list[dict]:
    """Returns simplified event dicts: name, date, venue, city, price_min, price_max."""
    if not config.HAS_TICKETMASTER:
        return []
    params = {**_auth_params(), "keyword": keyword, "size": size}
    if city:
        params["city"] = city
    data = cached_get("ticketmaster", f"{BASE_URL}/events.json", params=params, ttl_seconds=EVENTS_TTL)
    if not data:
        return []
    events = data.get("_embedded", {}).get("events", [])

    simplified = []
    for ev in events:
        venues = ev.get("_embedded", {}).get("venues", [])
        venue_name = venues[0]["name"] if venues else None
        venue_city = venues[0].get("city", {}).get("name") if venues else None
        price_ranges = ev.get("priceRanges", [])
        price_min = min((p["min"] for p in price_ranges), default=None)
        price_max = max((p["max"] for p in price_ranges), default=None)
        simplified.append({
            "name": ev.get("name"),
            "date": ev.get("dates", {}).get("start", {}).get("localDate"),
            "venue": venue_name,
            "city": venue_city,
            "price_min": price_min,
            "price_max": price_max,
        })
    return simplified


def search_attractions(keyword: str, limit: int = 8) -> list[str]:
    """Candidate performer/attraction names for autocomplete."""
    if not config.HAS_TICKETMASTER or not keyword:
        return []
    data = cached_get(
        "ticketmaster", f"{BASE_URL}/attractions.json",
        params={**_auth_params(), "keyword": keyword, "size": limit}, ttl_seconds=EVENTS_TTL,
    )
    if not data:
        return []
    attractions = data.get("_embedded", {}).get("attractions", [])
    return [a["name"] for a in attractions[:limit] if a.get("name")]


def search_venues(keyword: str, city: str | None = None, limit: int = 8) -> list[str]:
    """Candidate venue names for autocomplete."""
    if not config.HAS_TICKETMASTER or not keyword:
        return []
    params = {**_auth_params(), "keyword": keyword, "size": limit}
    if city:
        params["city"] = city
    data = cached_get("ticketmaster", f"{BASE_URL}/venues.json", params=params, ttl_seconds=EVENTS_TTL)
    if not data:
        return []
    venues = data.get("_embedded", {}).get("venues", [])
    return [v["name"] for v in venues[:limit] if v.get("name")]


def estimate_ticket_price_for_city(keyword: str, city: str) -> float | None:
    """Midpoint of price ranges across events matching this artist in this city,
    falling back to the artist's events anywhere if none found locally."""
    events = search_events(keyword, city=city)
    prices = [
        (e["price_min"] + e["price_max"]) / 2
        for e in events if e["price_min"] is not None and e["price_max"] is not None
    ]
    if not prices:
        events = search_events(keyword)
        prices = [
            (e["price_min"] + e["price_max"]) / 2
            for e in events if e["price_min"] is not None and e["price_max"] is not None
        ]
    if not prices:
        return None
    return sum(prices) / len(prices)
