"""Builds a downloadable Excel workbook covering everything shown on a
booking's dashboard - inputs and computed/researched outputs alike - so an
agency staffer can save/share a booking as a single file.
"""

from io import BytesIO

import pandas as pd


def _rows_from_labels(data: dict, labels: dict) -> list[dict]:
    return [{"Field": label, "Value": data.get(key)} for key, label in labels.items() if data.get(key) is not None]


def build_booking_workbook(
    talent: dict, performance: dict, revenue_info: dict, expense_info: dict,
    net_margin: float, venue_fit_score, marketing_efficiency,
    demand: dict, audience: dict, financial_details: dict,
    touring_history: dict, market_competition: dict, scores: dict,
    similar: list[dict], historical_summary: dict,
) -> bytes:
    buffer = BytesIO()
    demand = demand or {}
    audience = audience or {}
    financial_details = financial_details or {}
    touring_history = touring_history or {}
    market_competition = market_competition or {}

    summary_rows = [
        {"Field": "Talent name", "Value": talent["name"]},
        {"Field": "Domain", "Value": talent["domain"]},
        {"Field": "Venue", "Value": performance["venue_name"]},
        {"Field": "City", "Value": performance["city"]},
        {"Field": "Estimated date", "Value": performance["estimated_date"]},
        {"Field": "Target capacity", "Value": performance["target_capacity"]},
        {"Field": "Budget ($)", "Value": performance["budget"]},
        {"Field": "Notes", "Value": performance["notes"]},
        {"Field": "Ticket price ($)", "Value": revenue_info["ticket_price"]},
        {"Field": "Ticket price source", "Value": revenue_info["ticket_price_source"]},
        {"Field": "Sell-through rate", "Value": revenue_info["sell_through_rate"]},
        {"Field": "Sell-through rate source", "Value": revenue_info["sell_through_rate_source"]},
        {"Field": "Estimated attendance", "Value": revenue_info["estimated_attendance"]},
        {"Field": "Estimated revenue ($)", "Value": revenue_info["estimated_revenue"]},
        {"Field": "Estimated expenses ($)", "Value": expense_info["total_expenses"]},
        {"Field": "Estimated net margin ($)", "Value": net_margin},
        {"Field": "Venue fit score", "Value": venue_fit_score},
        {"Field": "Marketing efficiency ($/ticket)", "Value": marketing_efficiency},
    ]

    score_rows = [
        {"Score": "Demand", "Value": scores["demand"]["score"]},
        {"Score": "Financial", "Value": scores["financial"]["score"]},
        {"Score": "Marketing", "Value": scores["marketing"]["score"]},
        {"Score": "Risk (lower is better)", "Value": scores["risk"]["score"]},
        {"Score": "Overall viability", "Value": scores["overall"]["score"]},
    ]
    breakdown_rows = [
        {"Category": category, "Label": item["label"], "Raw value": item.get("raw_value"),
         "Sub-score": item.get("sub_score"), "Note": item.get("note", "")}
        for category, key in [
            ("Demand", "demand"), ("Financial", "financial"), ("Marketing", "marketing"),
            ("Risk", "risk"), ("Overall", "overall"),
        ]
        for item in scores[key]["breakdown"]
    ]

    expense_rows = [
        {"Category": category, "Amount ($)": amount}
        for category, amount in expense_info["breakdown"].items()
    ]

    demand_labels = {
        "search_interest_index": "Search interest / SEO score",
        "ticket_conversion_rate": "Ticket conversion rate (%)",
        "audience_purchasing_power": "Audience purchasing power ($)",
        "vip_conversion_rate": "VIP conversion rate (%)",
        "promoter_reliability_score": "Promoter reliability score",
        "fan_sentiment_score": "Fan sentiment score",
    }
    audience_labels = {
        "monthly_listeners": "Monthly listeners", "city_listeners": "Listeners in this city",
        "playlist_reach": "Playlist reach", "growth_6mo_pct": "Growth over last 6 months (%)",
        "instagram_followers": "Instagram followers", "instagram_avg_likes": "Instagram average likes",
        "instagram_avg_comments": "Instagram average comments",
        "instagram_engagement_pct": "Instagram engagement %",
        "tiktok_followers": "TikTok followers", "tiktok_avg_views": "TikTok average views",
        "tiktok_viral_rate_pct": "TikTok viral rate %",
        "youtube_subscribers": "YouTube subscribers", "youtube_avg_views": "YouTube average views",
    }
    financial_labels = {
        "vip_package_revenue": "VIP package revenue ($)", "merch_revenue": "Merch revenue ($)",
        "sponsorship_revenue": "Sponsorship revenue ($)", "food_pct": "Food (% of ticket gross)",
        "parking_pct": "Parking (% of ticket gross)", "artist_guarantee": "Artist guarantee ($)",
        "venue_rental": "Venue rental ($)", "production_cost": "Production ($)",
        "marketing_cost": "Marketing ($)", "security_cost": "Security ($)",
        "insurance_cost": "Insurance ($)", "travel_cost": "Travel ($)", "hotels_cost": "Hotels ($)",
        "crew_cost": "Crew ($)", "taxes_cost": "Taxes ($)",
    }
    touring_labels = {
        "sold_out_similar_venues": "Sold out similar venues?", "average_attendance_pct": "Average attendance (%)",
        "no_shows_count": "No-shows (count)", "average_ticket_price": "Average ticket price ($)",
        "repeat_cities": "Repeat cities?", "festival_performance": "Festival performance?",
        "venue_size_progression": "Venue size progression",
    }
    competition_labels = {
        "other_concerts_count": "Other concerts nearby", "sports_events_count": "Sports events nearby",
        "festivals_count": "Festivals nearby", "local_events_count": "Local events nearby",
        "major_holiday_conflict": "Major holiday conflict", "college_schedule_conflict": "College schedule conflict",
        "school_break_overlap": "School break overlap", "weather_season_risk": "Weather season risk",
    }

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Booking summary", index=False)
        pd.DataFrame(score_rows).to_excel(writer, sheet_name="Confidence scores", index=False)
        pd.DataFrame(breakdown_rows if breakdown_rows else [{"Note": "No score breakdown available"}]).to_excel(
            writer, sheet_name="Score breakdown", index=False
        )
        pd.DataFrame(expense_rows).to_excel(writer, sheet_name="Expense breakdown", index=False)
        pd.DataFrame(_rows_from_labels(demand, demand_labels) or [{"Field": "(none entered)", "Value": ""}]).to_excel(
            writer, sheet_name="Demand metrics", index=False
        )
        pd.DataFrame(_rows_from_labels(audience, audience_labels) or [{"Field": "(none entered)", "Value": ""}]).to_excel(
            writer, sheet_name="Audience & social media", index=False
        )
        pd.DataFrame(_rows_from_labels(financial_details, financial_labels) or [{"Field": "(none entered)", "Value": ""}]).to_excel(
            writer, sheet_name="Financial details", index=False
        )
        pd.DataFrame(_rows_from_labels(touring_history, touring_labels) or [{"Field": "(none entered)", "Value": ""}]).to_excel(
            writer, sheet_name="Touring history", index=False
        )
        pd.DataFrame(_rows_from_labels(market_competition, competition_labels) or [{"Field": "(none entered)", "Value": ""}]).to_excel(
            writer, sheet_name="Market competition", index=False
        )
        pd.DataFrame(similar if similar else [{"Note": "No comparable talent data yet"}]).to_excel(
            writer, sheet_name="Similar talent", index=False
        )
        pd.DataFrame(
            historical_summary["in_city"] if historical_summary["in_city"] else [{"Note": "No records for this city yet"}]
        ).to_excel(writer, sheet_name="History - in city", index=False)
        pd.DataFrame(
            historical_summary["elsewhere"] if historical_summary["elsewhere"] else [{"Note": "No records elsewhere yet"}]
        ).to_excel(writer, sheet_name="History - elsewhere", index=False)

    return buffer.getvalue()
