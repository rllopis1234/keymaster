import db
import metrics


def _performance(**overrides):
    base = {
        "city": "Austin", "target_capacity": 1000, "budget": 50000.0,
        "assumed_ticket_price": None, "assumed_sell_through_rate": None,
    }
    base.update(overrides)
    return base


def test_ticket_price_falls_back_to_global_default_with_no_history(fresh_db):
    perf = _performance()
    revenue = metrics.estimate_revenue(perf, "Unknown Artist", "music")
    assert revenue["ticket_price"] == metrics.GLOBAL_DEFAULT_TICKET_PRICE
    assert revenue["ticket_price_source"] == "default"
    assert revenue["sell_through_rate"] == metrics.GLOBAL_DEFAULT_SELL_THROUGH_RATE


def test_user_override_takes_priority_over_history(fresh_db):
    talent = fresh_db.get_or_create_talent("Artist X", "music")
    fresh_db.create_historical_comp(
        comparable_name="Artist X", domain="music", is_self=True, talent_id=talent["id"],
        venue_name="V", city="Austin", event_date="2024-01-01",
        capacity=1000, attendance=900, ticket_price_avg=99.0,
    )
    perf = _performance(assumed_ticket_price=20.0, assumed_sell_through_rate=0.5)
    revenue = metrics.estimate_revenue(perf, "Artist X", "music")
    assert revenue["ticket_price"] == 20.0
    assert revenue["ticket_price_source"] == "user override"
    assert revenue["sell_through_rate"] == 0.5


def test_in_city_history_preferred_over_other_cities(fresh_db):
    talent = fresh_db.get_or_create_talent("Artist Y", "music")
    fresh_db.create_historical_comp(
        comparable_name="Artist Y", domain="music", is_self=True, talent_id=talent["id"],
        venue_name="V1", city="Denver", event_date="2024-01-01",
        capacity=1000, attendance=900, ticket_price_avg=80.0,
    )
    fresh_db.create_historical_comp(
        comparable_name="Artist Y", domain="music", is_self=True, talent_id=talent["id"],
        venue_name="V2", city="Austin", event_date="2024-02-01",
        capacity=1000, attendance=900, ticket_price_avg=40.0,
    )
    perf = _performance(city="Austin")
    revenue = metrics.estimate_revenue(perf, "Artist Y", "music")
    assert revenue["ticket_price"] == 40.0
    assert "Austin" in revenue["ticket_price_source"]


def test_estimate_expenses_breakdown_sums_to_total(fresh_db):
    template = dict(fresh_db.get_default_expense_template())
    perf = _performance(budget=10000.0)
    expenses = metrics.estimate_expenses(perf, template)
    assert expenses["pct_sum_valid"]
    assert abs(sum(expenses["breakdown"].values()) - expenses["total_expenses"]) < 0.5


def test_estimate_expenses_flags_invalid_percentage_sum(fresh_db):
    template = {
        "venue_pct": 0.5, "marketing_pct": 0.5, "production_pct": 0.5,
        "talent_fee_pct": 0.0, "other_pct": 0.0,
    }
    perf = _performance(budget=10000.0)
    expenses = metrics.estimate_expenses(perf, template)
    assert not expenses["pct_sum_valid"]


def test_net_margin_is_revenue_minus_expenses(fresh_db):
    revenue_info = {"estimated_revenue": 1000.0}
    expense_info = {"total_expenses": 400.0}
    assert metrics.estimate_net_margin(revenue_info, expense_info) == 600.0


def test_historical_summary_splits_by_city(fresh_db):
    talent = fresh_db.get_or_create_talent("Artist Z", "music")
    fresh_db.create_historical_comp(
        comparable_name="Artist Z", domain="music", is_self=True, talent_id=talent["id"],
        venue_name="V1", city="Austin", event_date="2024-01-01",
    )
    fresh_db.create_historical_comp(
        comparable_name="Artist Z", domain="music", is_self=True, talent_id=talent["id"],
        venue_name="V2", city="Denver", event_date="2024-02-01",
    )
    summary = metrics.historical_summary("Artist Z", "music", "Austin")
    assert len(summary["in_city"]) == 1
    assert len(summary["elsewhere"]) == 1


def test_rank_similar_talent_prefers_genre_match(fresh_db):
    fresh_db.get_or_create_talent("Rock Band", "music")
    fresh_db.update_talent_enrichment(
        fresh_db.find_talent("Rock Band", "music")["id"], {}, ["rock"]
    )
    fresh_db.get_or_create_talent("Jazz Band", "music")
    fresh_db.update_talent_enrichment(
        fresh_db.find_talent("Jazz Band", "music")["id"], {}, ["jazz"]
    )
    fresh_db.create_historical_comp(
        comparable_name="Rock Band", domain="music", is_self=False,
        venue_name="V1", city="Austin", event_date="2024-01-01", capacity=2000, attendance=1800,
    )
    fresh_db.create_historical_comp(
        comparable_name="Jazz Band", domain="music", is_self=False,
        venue_name="V2", city="Austin", event_date="2024-01-01", capacity=1900, attendance=1700,
    )
    ranked = metrics.rank_similar_talent("music", exclude_name="Booked Artist", target_capacity=2000,
                                          genres=["rock"])
    assert ranked[0]["comparable_name"] == "Rock Band"


def test_rank_similar_talent_empty_when_no_comps(fresh_db):
    ranked = metrics.rank_similar_talent("music", exclude_name="Nobody", target_capacity=1000)
    assert ranked == []


def test_venue_fit_score_computes_ratio():
    assert metrics.venue_fit_score(900, 1000) == 0.9


def test_venue_fit_score_none_when_no_capacity():
    assert metrics.venue_fit_score(500, 0) is None


def test_marketing_efficiency_computes_cost_per_ticket():
    assert metrics.marketing_efficiency(5000.0, 1000) == 5.0


def test_marketing_efficiency_none_when_no_attendance():
    assert metrics.marketing_efficiency(5000.0, 0) is None


def test_merch_spend_per_attendee_computes_ratio():
    assert metrics.merch_spend_per_attendee(2000.0, 1000) == 2.0


def test_merch_spend_per_attendee_none_when_missing_revenue():
    assert metrics.merch_spend_per_attendee(None, 1000) is None
