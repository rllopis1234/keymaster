"""Builds a downloadable Excel workbook covering everything shown on a
booking's dashboard - inputs and computed/researched outputs alike - so an
agency staffer can save/share a booking as a single file.
"""

from io import BytesIO

import pandas as pd


def build_booking_workbook(
    talent: dict, performance: dict, revenue_info: dict, expense_info: dict,
    net_margin: float, venue_fit_score, marketing_efficiency,
    demand: dict, merch_per_attendee, similar: list[dict], historical_summary: dict,
) -> bytes:
    buffer = BytesIO()

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

    expense_rows = [
        {"Category": category, "Amount ($)": amount}
        for category, amount in expense_info["breakdown"].items()
    ]

    demand_labels = {
        "local_fan_density": "Local fan density (per 100k residents)",
        "search_interest_index": "Search interest / SEO score",
        "social_engagement_rate": "Social engagement rate (%)",
        "streaming_popularity": "Streaming popularity (monthly listeners)",
        "ticket_conversion_rate": "Ticket conversion rate (%)",
        "audience_purchasing_power": "Audience purchasing power ($)",
        "market_competition_index": "Market competition index (# events)",
        "vip_conversion_rate": "VIP conversion rate (%)",
        "merch_revenue_total": "Merchandise revenue ($)",
        "promoter_reliability_score": "Promoter reliability score",
        "fan_sentiment_score": "Fan sentiment score",
        "demand_growth_rate": "Demand growth rate (%)",
    }
    demand_rows = [
        {"Metric": label, "Value": demand.get(key)}
        for key, label in demand_labels.items() if demand.get(key) is not None
    ]
    if merch_per_attendee is not None:
        demand_rows.append({"Metric": "Merchandise spend per attendee ($)", "Value": merch_per_attendee})

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Booking summary", index=False)
        pd.DataFrame(expense_rows).to_excel(writer, sheet_name="Expense breakdown", index=False)
        pd.DataFrame(demand_rows if demand_rows else [{"Metric": "(none entered)", "Value": ""}]).to_excel(
            writer, sheet_name="Demand metrics", index=False
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
