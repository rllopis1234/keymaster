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


# ============================================================
# Confidence scores (Demand, Marketing, Financial, Risk, Overall)
#
# Every score function returns {"score": float|None, "breakdown": [...]}.
# `score` is None when there isn't enough entered data to say anything
# meaningful - callers should show "not enough data" rather than a
# misleading number. Banding thresholds below are deliberately named
# constants: they're reasonable defaults, not objective facts, and are
# meant to be tuned as real booking outcomes accumulate.
# ============================================================

def _band_score(value: Optional[float], bands: list[tuple[float, int]]) -> Optional[int]:
    """bands: ascending list of (upper_bound, score). Returns the score for
    the first band whose upper_bound >= value, or the last band's score if
    value exceeds every bound. None in, None out."""
    if value is None:
        return None
    for upper_bound, score in bands:
        if value <= upper_bound:
            return score
    return bands[-1][1]


MONTHLY_LISTENERS_BANDS = [(10_000, 20), (100_000, 40), (500_000, 60), (2_000_000, 80), (float("inf"), 100)]
CITY_LISTENERS_BANDS = [(1_000, 20), (10_000, 40), (50_000, 60), (200_000, 80), (float("inf"), 100)]
PLAYLIST_REACH_BANDS = [(50_000, 20), (500_000, 40), (2_000_000, 60), (10_000_000, 80), (float("inf"), 100)]
GROWTH_6MO_BANDS = [(0, 20), (5, 40), (15, 60), (30, 80), (float("inf"), 100)]  # percent

INSTAGRAM_FOLLOWERS_BANDS = [(10_000, 20), (50_000, 40), (200_000, 60), (1_000_000, 80), (float("inf"), 100)]
INSTAGRAM_ENGAGEMENT_BANDS = [(1, 20), (2, 40), (4, 60), (8, 80), (float("inf"), 100)]  # percent
TIKTOK_FOLLOWERS_BANDS = [(10_000, 20), (50_000, 40), (200_000, 60), (1_000_000, 80), (float("inf"), 100)]
TIKTOK_VIRAL_RATE_BANDS = [(1, 20), (3, 40), (6, 60), (12, 80), (float("inf"), 100)]  # percent
YOUTUBE_SUBSCRIBERS_BANDS = [(10_000, 20), (50_000, 40), (200_000, 60), (1_000_000, 80), (float("inf"), 100)]
YOUTUBE_VIEWS_RATIO_BANDS = [(5, 20), (15, 40), (30, 60), (60, 80), (float("inf"), 100)]  # avg_views/subscribers, percent

ROI_BANDS = [(0, 20), (10, 40), (20, 60), (35, 80), (float("inf"), 100)]  # percent

COMPETITION_COUNT_BANDS = [(0, 0), (2, 10), (5, 20), (float("inf"), 30)]


def _composite_score(items: list[tuple[str, Optional[float], list, str]]) -> dict:
    breakdown = []
    sub_scores = []
    for label, raw_value, bands, note in items:
        sub_score = _band_score(raw_value, bands)
        breakdown.append({"label": label, "raw_value": raw_value, "sub_score": sub_score, "note": note})
        if sub_score is not None:
            sub_scores.append(sub_score)
    score = round(mean(sub_scores), 1) if sub_scores else None
    return {"score": score, "breakdown": breakdown}


def score_demand(audience: Optional[dict]) -> dict:
    """Streaming/audience reach - not social media (see score_marketing)."""
    audience = audience or {}
    return _composite_score([
        ("Monthly listeners", audience.get("monthly_listeners"), MONTHLY_LISTENERS_BANDS,
         "Total monthly streaming listeners across platforms."),
        ("Listeners in this city", audience.get("city_listeners"), CITY_LISTENERS_BANDS,
         "Streaming listeners based in the booking's city."),
        ("Playlist reach", audience.get("playlist_reach"), PLAYLIST_REACH_BANDS,
         "Combined reach of playlists featuring this talent."),
        ("Growth over last 6 months", audience.get("growth_6mo_pct"), GROWTH_6MO_BANDS,
         "% growth in listeners/followers over the last 6 months."),
    ])


def score_marketing(audience: Optional[dict]) -> dict:
    """Social media reach/engagement across Instagram, TikTok, YouTube."""
    audience = audience or {}
    breakdown = []
    platform_scores = []

    def platform(prefix: str, followers, followers_bands, engagement, engagement_bands,
                 engagement_label: str, engagement_note: str, followers_note: str):
        f_score = _band_score(followers, followers_bands)
        e_score = _band_score(engagement, engagement_bands)
        breakdown.append({"label": f"{prefix} followers", "raw_value": followers, "sub_score": f_score,
                           "note": followers_note})
        breakdown.append({"label": f"{prefix} {engagement_label}", "raw_value": engagement, "sub_score": e_score,
                           "note": engagement_note})
        sub = [s for s in (f_score, e_score) if s is not None]
        if sub:
            platform_scores.append(mean(sub))

    platform("Instagram", audience.get("instagram_followers"), INSTAGRAM_FOLLOWERS_BANDS,
              audience.get("instagram_engagement_pct"), INSTAGRAM_ENGAGEMENT_BANDS,
              "engagement rate", "(likes + comments) ÷ followers.", "Instagram follower count.")
    platform("TikTok", audience.get("tiktok_followers"), TIKTOK_FOLLOWERS_BANDS,
              audience.get("tiktok_viral_rate_pct"), TIKTOK_VIRAL_RATE_BANDS,
              "viral rate", "Share of videos that significantly outperform average views.",
              "TikTok follower count.")

    youtube_views_ratio = None
    if audience.get("youtube_subscribers") and audience.get("youtube_avg_views") is not None:
        youtube_views_ratio = (audience["youtube_avg_views"] / audience["youtube_subscribers"]) * 100
    platform("YouTube", audience.get("youtube_subscribers"), YOUTUBE_SUBSCRIBERS_BANDS,
              youtube_views_ratio, YOUTUBE_VIEWS_RATIO_BANDS,
              "views-to-subscriber ratio", "Average views as a % of subscriber count.",
              "YouTube subscriber count.")

    score = round(mean(platform_scores), 1) if platform_scores else None
    return {"score": score, "breakdown": breakdown}


def score_financial(revenue_info: dict, expense_info: dict, performance: dict,
                     financial_details: Optional[dict] = None) -> dict:
    financial_details = financial_details or {}
    expense_fields = [
        "artist_guarantee", "venue_rental", "production_cost", "marketing_cost", "security_cost",
        "insurance_cost", "travel_cost", "hotels_cost", "crew_cost", "taxes_cost",
    ]
    has_detail = any(financial_details.get(f) is not None for f in expense_fields)

    if has_detail:
        ticket_gross = revenue_info["estimated_revenue"]
        food_pct = financial_details.get("food_pct") or 0
        parking_pct = financial_details.get("parking_pct") or 0
        detailed_revenue = (
            ticket_gross
            + (financial_details.get("vip_package_revenue") or 0)
            + (financial_details.get("merch_revenue") or 0)
            + (financial_details.get("sponsorship_revenue") or 0)
            + ticket_gross * (food_pct / 100)
            + ticket_gross * (parking_pct / 100)
        )
        detailed_expenses = sum(financial_details.get(f) or 0 for f in expense_fields)
        profit = detailed_revenue - detailed_expenses
        roi_pct = (profit / detailed_expenses * 100) if detailed_expenses else None
        breakdown = [
            {"label": "Detailed gross revenue ($)", "raw_value": round(detailed_revenue, 2), "sub_score": None,
             "note": "Ticket gross + VIP + merch + sponsorship + food/parking share of gross."},
            {"label": "Detailed expenses ($)", "raw_value": round(detailed_expenses, 2), "sub_score": None,
             "note": "Sum of all entered expense line items."},
            {"label": "Profit ($)", "raw_value": round(profit, 2), "sub_score": None,
             "note": "Revenue minus expenses."},
            {"label": "ROI (%)", "raw_value": round(roi_pct, 1) if roi_pct is not None else None,
             "sub_score": _band_score(roi_pct, ROI_BANDS),
             "note": "Profit ÷ expenses, based on the detailed financial breakdown."},
        ]
        basis = "detailed financial breakdown"
    else:
        budget = performance.get("budget") or 0
        net_margin = revenue_info["estimated_revenue"] - expense_info["total_expenses"]
        roi_pct = (net_margin / budget * 100) if budget else None
        breakdown = [
            {"label": "Estimated revenue ($)", "raw_value": round(revenue_info["estimated_revenue"], 2),
             "sub_score": None, "note": "Target capacity × sell-through rate × ticket price."},
            {"label": "Estimated expenses ($)", "raw_value": round(expense_info["total_expenses"], 2),
             "sub_score": None, "note": "Budget × expense template percentages."},
            {"label": "Net margin ($)", "raw_value": round(net_margin, 2), "sub_score": None,
             "note": "Revenue minus expenses."},
            {"label": "ROI-equivalent (%)", "raw_value": round(roi_pct, 1) if roi_pct is not None else None,
             "sub_score": _band_score(roi_pct, ROI_BANDS),
             "note": "Net margin ÷ budget - add a detailed financial breakdown for a more precise score."},
        ]
        basis = "simple budget-based estimate"

    return {"score": _band_score(roi_pct, ROI_BANDS), "breakdown": breakdown, "basis": basis}


def score_risk(market_competition: Optional[dict], touring_history: Optional[dict]) -> dict:
    """0-100, lower is better - accumulates penalty points, capped at 100."""
    market_competition = market_competition or {}
    touring_history = touring_history or {}

    competition_fields = [
        "other_concerts_count", "sports_events_count", "festivals_count", "local_events_count",
        "major_holiday_conflict", "college_schedule_conflict", "school_break_overlap", "weather_season_risk",
    ]
    touring_fields = [
        "sold_out_similar_venues", "average_attendance_pct", "no_shows_count",
        "repeat_cities", "festival_performance", "venue_size_progression",
    ]
    has_data = (
        any(market_competition.get(f) is not None for f in competition_fields)
        or any(touring_history.get(f) is not None for f in touring_fields)
    )
    if not has_data:
        return {"score": None, "breakdown": []}

    breakdown = []
    risk_points = 0

    for label, key, note in [
        ("Other concerts nearby", "other_concerts_count", "Competing concerts within the market window."),
        ("Sports events nearby", "sports_events_count", "Competing sports events."),
        ("Festivals nearby", "festivals_count", "Competing festivals."),
        ("Local events nearby", "local_events_count", "Other local events."),
    ]:
        count = market_competition.get(key)
        points = _band_score(count, COMPETITION_COUNT_BANDS) or 0
        risk_points += points
        breakdown.append({"label": label, "raw_value": count, "sub_score": points, "note": note})

    weather_risk = market_competition.get("weather_season_risk")
    weather_points = {"low": 0, "medium": 15, "high": 30}.get(weather_risk, 0)
    risk_points += weather_points
    breakdown.append({"label": "Weather season risk", "raw_value": weather_risk, "sub_score": weather_points,
                       "note": "Seasonal weather risk for the venue/date."})

    for label, condition, points, note in [
        ("Major holiday conflict", market_competition.get("major_holiday_conflict"), 15,
         "Booking date conflicts with a major holiday."),
        ("College schedule conflict", market_competition.get("college_schedule_conflict"), 10,
         "Booking date conflicts with the local college calendar."),
        ("School break overlap", market_competition.get("school_break_overlap"), 5,
         "Booking date overlaps with school breaks."),
        ("History of no-shows", (touring_history.get("no_shows_count") or 0) > 0, 20,
         "Artist has a history of no-show/cancelled performances."),
        ("Declining venue progression", touring_history.get("venue_size_progression") == "declining", 20,
         "Venue sizes have been trending down over time."),
        ("Hasn't sold out similar venues", touring_history.get("sold_out_similar_venues") is False, 15,
         "Artist has not sold out comparable venues previously."),
        ("Low average attendance", (touring_history.get("average_attendance_pct") or 100) < 70, 15,
         "Average attendance below 70% of capacity historically."),
    ]:
        points = points if condition else 0
        risk_points += points
        breakdown.append({"label": label, "raw_value": bool(condition), "sub_score": points, "note": note})

    return {"score": min(risk_points, 100), "breakdown": breakdown}


def score_overall(demand: dict, marketing: dict, financial: dict, risk: dict) -> dict:
    """Weighted composite - renormalized across whichever categories have data."""
    components = [
        ("Demand", demand.get("score"), 0.25),
        ("Marketing", marketing.get("score"), 0.20),
        ("Financial", financial.get("score"), 0.30),
        ("Risk (inverted)", (100 - risk["score"]) if risk.get("score") is not None else None, 0.25),
    ]
    breakdown = [
        {"label": label, "raw_value": value, "sub_score": value,
         "note": f"Weight: {weight:.0%}" if value is not None else "Not enough data"}
        for label, value, weight in components
    ]
    available = [(value, weight) for _, value, weight in components if value is not None]
    if not available:
        return {"score": None, "breakdown": breakdown}
    total_weight = sum(w for _, w in available)
    weighted_sum = sum(v * w for v, w in available)
    return {"score": round(weighted_sum / total_weight, 1), "breakdown": breakdown}
