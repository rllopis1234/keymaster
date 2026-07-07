"""No-API-key smoke test: inserts synthetic data through db.py and runs
metrics.py against it, asserting the numbers come out sane. Run with:

    python scripts/sanity_check.py

Safe to run repeatedly - it uses a throwaway talent name each run isn't
required, but it will accumulate rows in the local dev DB. Delete
data/talent_tracker.db to reset if desired.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import db
import metrics

FAILURES = []


def check(label: str, condition: bool):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def main():
    db.init_db()

    talent_name = "__SanityCheck Artist__"
    domain = "music"
    city = "Austin"

    talent = db.get_or_create_talent(talent_name, domain)
    talent_id = talent["id"]

    # Historical comps for the talent itself, in-city and elsewhere
    db.create_historical_comp(
        comparable_name=talent_name, domain=domain, is_self=True, talent_id=talent_id,
        venue_name="Moody Amphitheater", city=city, event_date="2024-05-01",
        capacity=5000, attendance=4200, ticket_price_avg=60.0, gross_revenue=252000.0,
    )
    db.create_historical_comp(
        comparable_name=talent_name, domain=domain, is_self=True, talent_id=talent_id,
        venue_name="The Fillmore", city="San Francisco", event_date="2023-11-10",
        capacity=3000, attendance=2100, ticket_price_avg=55.0, gross_revenue=115500.0,
    )

    # A comparable act for similarity ranking
    db.create_historical_comp(
        comparable_name="__SanityCheck Comparable__", domain=domain, is_self=False,
        venue_name="Stubb's", city=city, event_date="2024-03-01",
        capacity=5200, attendance=4600, ticket_price_avg=58.0, gross_revenue=266800.0,
    )

    performance_id = db.create_performance(
        talent_id=talent_id, venue_name="Moody Amphitheater", city=city,
        estimated_date="2026-09-01", target_capacity=5000, budget=100000.0,
    )
    performance = dict(db.get_performance(performance_id))

    revenue_info = metrics.estimate_revenue(performance, talent_name, domain)
    check("ticket price resolves from historical in-city data", revenue_info["ticket_price"] == 60.0)
    check("ticket price source labeled correctly", "Austin" in revenue_info["ticket_price_source"])
    check("estimated attendance > 0", revenue_info["estimated_attendance"] > 0)
    check("estimated revenue > 0", revenue_info["estimated_revenue"] > 0)

    template = dict(db.get_default_expense_template())
    expense_info = metrics.estimate_expenses(performance, template)
    check("expense percentages sum to ~1.0", expense_info["pct_sum_valid"])
    check("total expenses > 0", expense_info["total_expenses"] > 0)
    check(
        "expense breakdown sums to total",
        abs(sum(expense_info["breakdown"].values()) - expense_info["total_expenses"]) < 0.5,
    )

    net_margin = metrics.estimate_net_margin(revenue_info, expense_info)
    check("net margin is a finite number", isinstance(net_margin, float))

    summary = metrics.historical_summary(talent_name, domain, city)
    check("historical summary has 1 in-city record", len(summary["in_city"]) == 1)
    check("historical summary has 1 elsewhere record", len(summary["elsewhere"]) == 1)

    similar = metrics.rank_similar_talent(domain, exclude_name=talent_name, target_capacity=5000)
    check("similar talent ranking returns the comparable act", any(
        s["comparable_name"] == "__SanityCheck Comparable__" for s in similar
    ))

    # No-historical-data fallback path (a talent with zero comps)
    fresh_name = "__SanityCheck Fresh Talent__"
    fresh_performance = {
        "city": "Denver", "target_capacity": 2000, "budget": 50000.0,
        "assumed_ticket_price": None, "assumed_sell_through_rate": None,
    }
    fresh_revenue = metrics.estimate_revenue(fresh_performance, fresh_name, domain)
    check("falls back to global default ticket price with no history",
          fresh_revenue["ticket_price"] == metrics.GLOBAL_DEFAULT_TICKET_PRICE)
    check("falls back to global default sell-through with no history",
          fresh_revenue["sell_through_rate"] == metrics.GLOBAL_DEFAULT_SELL_THROUGH_RATE)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("All sanity checks PASSED.")


if __name__ == "__main__":
    main()
