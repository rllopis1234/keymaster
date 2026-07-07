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


# ---------------- confidence scores ----------------

def test_score_demand_none_when_no_data():
    assert metrics.score_demand({})["score"] is None
    assert metrics.score_demand(None)["score"] is None


def test_score_demand_averages_available_sub_scores():
    result = metrics.score_demand({"monthly_listeners": 850_000, "growth_6mo_pct": 8})
    assert result["score"] == 70.0  # (80 + 60) / 2
    assert len(result["breakdown"]) == 4  # all 4 metrics listed even when 2 are missing


def test_score_demand_top_band():
    result = metrics.score_demand({"monthly_listeners": 5_000_000})
    sub_score = next(b["sub_score"] for b in result["breakdown"] if b["label"] == "Monthly listeners")
    assert sub_score == 100


def test_score_marketing_none_when_no_data():
    assert metrics.score_marketing({})["score"] is None


def test_score_marketing_uses_only_platforms_with_data():
    result = metrics.score_marketing({"instagram_followers": 500_000, "instagram_engagement_pct": 5})
    assert result["score"] == 80.0  # (80 + 80) / 2 - followers band is <=1M, engagement band is <=8%
    assert len([b for b in result["breakdown"] if "TikTok" in b["label"]]) == 2  # still listed, just null sub-scores


def test_score_financial_matches_worked_example():
    revenue_info = {"estimated_revenue": 63960.0}
    expense_info = {"total_expenses": 49000.0}
    performance = {"budget": 49000.0}
    financial_details = {
        "artist_guarantee": 20000, "venue_rental": 8000, "production_cost": 6000,
        "marketing_cost": 5000, "security_cost": 3000, "insurance_cost": 2000,
        "travel_cost": 2500, "hotels_cost": 1500, "crew_cost": 1000, "taxes_cost": 0,
    }
    result = metrics.score_financial(revenue_info, expense_info, performance, financial_details)
    profit_entry = next(b for b in result["breakdown"] if b["label"] == "Profit ($)")
    roi_entry = next(b for b in result["breakdown"] if b["label"] == "ROI (%)")
    assert profit_entry["raw_value"] == 14960.0
    assert roi_entry["raw_value"] == 30.5
    assert result["score"] == 80
    assert result["basis"] == "detailed financial breakdown"


def test_score_financial_falls_back_without_detail():
    revenue_info = {"estimated_revenue": 60000.0}
    expense_info = {"total_expenses": 40000.0}
    performance = {"budget": 40000.0}
    result = metrics.score_financial(revenue_info, expense_info, performance, None)
    assert result["basis"] == "simple budget-based estimate"
    assert result["score"] is not None


def test_score_risk_none_when_no_data():
    assert metrics.score_risk(None, None)["score"] is None
    assert metrics.score_risk({}, {})["score"] is None


def test_score_risk_accumulates_penalty_points():
    market_competition = {"other_concerts_count": 6, "weather_season_risk": "high"}
    touring_history = {"no_shows_count": 2, "venue_size_progression": "declining"}
    result = metrics.score_risk(market_competition, touring_history)
    # 30 (6 concerts) + 30 (high weather) + 20 (no-shows) + 20 (declining) = 100
    assert result["score"] == 100


def test_score_risk_capped_at_100():
    market_competition = {
        "other_concerts_count": 10, "sports_events_count": 10, "festivals_count": 10,
        "local_events_count": 10, "weather_season_risk": "high",
        "major_holiday_conflict": True, "college_schedule_conflict": True, "school_break_overlap": True,
    }
    touring_history = {"no_shows_count": 5, "venue_size_progression": "declining",
                        "sold_out_similar_venues": False, "average_attendance_pct": 40}
    result = metrics.score_risk(market_competition, touring_history)
    assert result["score"] == 100


def test_score_overall_renormalizes_across_available_categories():
    demand = {"score": 80}
    marketing = {"score": None}  # missing
    financial = {"score": 60}
    risk = {"score": 20}
    result = metrics.score_overall(demand, marketing, financial, risk)
    # weights renormalized across Demand(.25) + Financial(.30) + Risk-inverted(.25) = .80 total
    expected = round((80 * 0.25 + 60 * 0.30 + 80 * 0.25) / 0.80, 1)
    assert result["score"] == expected


def test_score_overall_none_when_nothing_available():
    empty = {"score": None}
    result = metrics.score_overall(empty, empty, empty, empty)
    assert result["score"] is None
