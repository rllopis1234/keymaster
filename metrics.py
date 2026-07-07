"""Revenue, expense, and similarity calculations.

All functions here operate purely against local DB data (manual entries and/or
cached API results already written into `historical_comps`/`talents`). None of
this module talks to the network directly - api_clients/* populate the DB, and
these functions only ever read from db.py. That keeps every formula testable
and usable with zero API keys configured.
"""

from statistics import mean
from typing import Optional

import db

GLOBAL_DEFAULT_TICKET_PRICE = 45.0
GLOBAL_DEFAULT_SELL_THROUGH_RATE = 0.75


def _avg(values: list) -> Optional[float]:
    cleaned = [v for v in values if v is not None]
    return mean(cleaned) if cleaned else None


def resolve_ticket_price(performance: dict, talent_name: str, domain: str, city: str) -> tuple[float, str]:
    """Returns (price, source_label)."""
    if performance.get("assumed_ticket_price"):
        return performance["assumed_ticket_price"], "user override"

    self_city_comps = [
        c for c in db.list_historical_comps(comparable_name=talent_name, domain=domain, is_self=True)
        if c["city"] == city
    ]
    price = _avg([c["ticket_price_avg"] for c in self_city_comps])
    if price:
        return price, f"historical average in {city}"

    self_comps = db.list_historical_comps(comparable_name=talent_name, domain=domain, is_self=True)
    price = _avg([c["ticket_price_avg"] for c in self_comps])
    if price:
        return price, "historical average (all cities)"

    return GLOBAL_DEFAULT_TICKET_PRICE, "default"


def resolve_sell_through_rate(performance: dict, talent_name: str, domain: str, city: str) -> tuple[float, str]:
    """Returns (rate, source_label)."""
    if performance.get("assumed_sell_through_rate"):
        return performance["assumed_sell_through_rate"], "user override"

    def sell_through(c):
        if c["capacity"] and c["attendance"] is not None and c["capacity"] > 0:
            return c["attendance"] / c["capacity"]
        return None

    self_city_comps = [
        c for c in db.list_historical_comps(comparable_name=talent_name, domain=domain, is_self=True)
        if c["city"] == city
    ]
    rate = _avg([sell_through(c) for c in self_city_comps])
    if rate:
        return rate, f"historical average in {city}"

    self_comps = db.list_historical_comps(comparable_name=talent_name, domain=domain, is_self=True)
    rate = _avg([sell_through(c) for c in self_comps])
    if rate:
        return rate, "historical average (all cities)"

    return GLOBAL_DEFAULT_SELL_THROUGH_RATE, "default"


def estimate_revenue(performance: dict, talent_name: str, domain: str) -> dict:
    city = performance["city"]
    target_capacity = performance["target_capacity"]

    ticket_price, price_source = resolve_ticket_price(performance, talent_name, domain, city)
    sell_through_rate, rate_source = resolve_sell_through_rate(performance, talent_name, domain, city)

    estimated_attendance = round(target_capacity * sell_through_rate)
    estimated_revenue = estimated_attendance * ticket_price

    return {
        "ticket_price": ticket_price,
        "ticket_price_source": price_source,
        "sell_through_rate": sell_through_rate,
        "sell_through_rate_source": rate_source,
        "estimated_attendance": estimated_attendance,
        "estimated_revenue": estimated_revenue,
    }


def estimate_expenses(performance: dict, template: dict) -> dict:
    budget = performance["budget"]
    categories = {
        "venue": template["venue_pct"],
        "marketing": template["marketing_pct"],
        "production": template["production_pct"],
        "talent_fee": template["talent_fee_pct"],
        "other": template["other_pct"],
    }
    pct_sum = sum(categories.values())
    breakdown = {name: round(budget * pct, 2) for name, pct in categories.items()}
    return {
        "total_expenses": round(budget * pct_sum, 2),
        "pct_sum": pct_sum,
        "breakdown": breakdown,
        "pct_sum_valid": abs(pct_sum - 1.0) < 0.01,
    }


def estimate_net_margin(revenue_info: dict, expense_info: dict) -> float:
    return round(revenue_info["estimated_revenue"] - expense_info["total_expenses"], 2)


def venue_fit_score(estimated_attendance: int, target_capacity: int) -> Optional[float]:
    """Predicted attendance / venue capacity. Ideal range is ~85-95%."""
    if not target_capacity:
        return None
    return round(estimated_attendance / target_capacity, 4)


def marketing_efficiency(marketing_expense: float, estimated_attendance: int) -> Optional[float]:
    """Cost per ticket sold ($/ticket) - lower is more efficient."""
    if not estimated_attendance:
        return None
    return round(marketing_expense / estimated_attendance, 2)


def merch_spend_per_attendee(merch_revenue_total: Optional[float], estimated_attendance: int) -> Optional[float]:
    if merch_revenue_total is None or not estimated_attendance:
        return None
    return round(merch_revenue_total / estimated_attendance, 2)


def historical_summary(talent_name: str, domain: str, city: str) -> dict:
    self_comps = db.list_historical_comps(comparable_name=talent_name, domain=domain, is_self=True)
    in_city = [dict(c) for c in self_comps if c["city"] == city]
    elsewhere = [dict(c) for c in self_comps if c["city"] != city]
    return {"in_city": in_city, "elsewhere": elsewhere}


def _aggregate_by_name(rows: list) -> dict[str, dict]:
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["comparable_name"], []).append(r)

    stats = {}
    for name, recs in grouped.items():
        stats[name] = {
            "comparable_name": name,
            "avg_capacity": _avg([r["capacity"] for r in recs]),
            "avg_attendance": _avg([r["attendance"] for r in recs]),
            "avg_ticket_price": _avg([r["ticket_price_avg"] for r in recs]),
            "avg_gross_revenue": _avg([r["gross_revenue"] for r in recs]),
            "record_count": len(recs),
        }
    return stats


def rank_similar_talent(domain: str, exclude_name: str, target_capacity: int,
                         genres: Optional[list] = None, limit: int = 5) -> list[dict]:
    """Rank comparable talent (from historical_comps) by closeness of historical
    average venue capacity to the target booking's capacity. If `genres` is
    supplied, candidates sharing at least one genre tag (looked up via the
    talents table, when the comparable name also exists as a talent) are
    ranked ahead of those without a genre match.
    """
    all_comps = db.list_all_comps_for_domain(domain)
    candidate_rows = [c for c in all_comps if c["comparable_name"] != exclude_name]
    stats = _aggregate_by_name(candidate_rows)

    genre_set = set(g.lower() for g in (genres or []))

    def genre_match(name: str) -> bool:
        if not genre_set:
            return False
        talent_row = db.find_talent(name, domain)
        if not talent_row:
            return False
        import json
        candidate_genres = set(g.lower() for g in json.loads(talent_row["genres_json"] or "[]"))
        return bool(genre_set & candidate_genres)

    ranked = []
    for name, s in stats.items():
        if s["avg_capacity"] is None:
            continue
        s["genre_match"] = genre_match(name)
        s["capacity_delta"] = abs(s["avg_capacity"] - target_capacity)
        ranked.append(s)

    ranked.sort(key=lambda s: (not s["genre_match"], s["capacity_delta"]))
    return ranked[:limit]
