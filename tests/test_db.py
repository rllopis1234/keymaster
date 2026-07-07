import pytest

import db


def test_init_db_seeds_default_expense_template(fresh_db):
    template = fresh_db.get_default_expense_template()
    assert template["is_default"] == 1
    pct_sum = (
        template["venue_pct"] + template["marketing_pct"] + template["production_pct"]
        + template["talent_fee_pct"] + template["other_pct"]
    )
    assert abs(pct_sum - 1.0) < 0.01


def test_get_or_create_talent_is_idempotent(fresh_db):
    first = fresh_db.get_or_create_talent("Test Artist", "music")
    second = fresh_db.get_or_create_talent("Test Artist", "music")
    assert first["id"] == second["id"]


def test_get_or_create_talent_distinguishes_by_domain(fresh_db):
    music = fresh_db.get_or_create_talent("Same Name", "music")
    actor = fresh_db.get_or_create_talent("Same Name", "actor")
    assert music["id"] != actor["id"]


def test_search_talent_names_matches_substring_and_domain(fresh_db):
    fresh_db.get_or_create_talent("Radiohead", "music")
    fresh_db.get_or_create_talent("Radiohead", "actor")  # different domain, shouldn't match
    fresh_db.get_or_create_talent("Coldplay", "music")

    results = fresh_db.search_talent_names("music", "radio")
    assert results == ["Radiohead"]


def test_search_cities_matches_across_performances_and_comps(fresh_db):
    talent = fresh_db.get_or_create_talent("Artist C", "music")
    fresh_db.create_performance(
        talent_id=talent["id"], venue_name="V", city="Austin",
        estimated_date="2026-01-01", target_capacity=100, budget=1000.0,
    )
    fresh_db.create_historical_comp(
        comparable_name="Artist C", domain="music", is_self=True, talent_id=talent["id"],
        venue_name="V", city="Austinburg", event_date="2024-01-01",
    )
    results = fresh_db.search_cities("austin")
    assert set(results) == {"Austin", "Austinburg"}


def test_search_venues_matches_across_performances_and_comps(fresh_db):
    talent = fresh_db.get_or_create_talent("Artist D", "music")
    fresh_db.create_performance(
        talent_id=talent["id"], venue_name="The Arena", city="Denver",
        estimated_date="2026-01-01", target_capacity=100, budget=1000.0,
    )
    results = fresh_db.search_venues("arena")
    assert results == ["The Arena"]


def test_search_functions_return_empty_list_when_no_match(fresh_db):
    assert fresh_db.search_talent_names("music", "zzz_nonexistent") == []
    assert fresh_db.search_cities("zzz_nonexistent") == []
    assert fresh_db.search_venues("zzz_nonexistent") == []


def test_historical_comp_filters_by_self_and_domain(fresh_db):
    talent = fresh_db.get_or_create_talent("Artist A", "music")
    fresh_db.create_historical_comp(
        comparable_name="Artist A", domain="music", is_self=True, talent_id=talent["id"],
        venue_name="Venue 1", city="Austin", event_date="2024-01-01", capacity=1000, attendance=900,
    )
    fresh_db.create_historical_comp(
        comparable_name="Artist B", domain="music", is_self=False,
        venue_name="Venue 2", city="Austin", event_date="2024-02-01", capacity=1200, attendance=1000,
    )

    self_rows = fresh_db.list_historical_comps(domain="music", is_self=True)
    other_rows = fresh_db.list_historical_comps(domain="music", is_self=False)
    assert len(self_rows) == 1 and self_rows[0]["comparable_name"] == "Artist A"
    assert len(other_rows) == 1 and other_rows[0]["comparable_name"] == "Artist B"


def test_cache_get_returns_none_when_absent(fresh_db):
    assert fresh_db.cache_get("ticketmaster", "nonexistent-key") is None


def test_cache_set_then_get_round_trips(fresh_db):
    fresh_db.cache_set("ticketmaster", "key1", {"hello": "world"}, ttl_seconds=3600)
    assert fresh_db.cache_get("ticketmaster", "key1") == {"hello": "world"}


def test_cache_expired_entry_returns_none(fresh_db):
    fresh_db.cache_set("ticketmaster", "key-expired", {"a": 1}, ttl_seconds=-1)
    assert fresh_db.cache_get("ticketmaster", "key-expired") is None


def _make_performance(fresh_db):
    talent = fresh_db.get_or_create_talent("Demand Test Artist", "music")
    return fresh_db.create_performance(
        talent_id=talent["id"], venue_name="V", city="Austin",
        estimated_date="2026-01-01", target_capacity=1000, budget=10000.0,
    )


def test_get_demand_metrics_returns_none_when_absent(fresh_db):
    performance_id = _make_performance(fresh_db)
    assert fresh_db.get_demand_metrics(performance_id) is None


def test_upsert_demand_metrics_inserts_then_updates(fresh_db):
    performance_id = _make_performance(fresh_db)

    fresh_db.upsert_demand_metrics(performance_id, search_interest_index=42.0, fan_sentiment_score=80.0)
    row = fresh_db.get_demand_metrics(performance_id)
    assert row["search_interest_index"] == 42.0
    assert row["fan_sentiment_score"] == 80.0
    assert row["ticket_conversion_rate"] is None

    fresh_db.upsert_demand_metrics(performance_id, search_interest_index=99.0)
    row = fresh_db.get_demand_metrics(performance_id)
    assert row["search_interest_index"] == 99.0
    assert row["fan_sentiment_score"] == 80.0  # untouched by the second call


def test_upsert_demand_metrics_rejects_unknown_field(fresh_db):
    performance_id = _make_performance(fresh_db)
    with pytest.raises(ValueError):
        fresh_db.upsert_demand_metrics(performance_id, not_a_real_field=1)


def test_upsert_audience_metrics_inserts_then_updates(fresh_db):
    performance_id = _make_performance(fresh_db)
    fresh_db.upsert_audience_metrics(performance_id, monthly_listeners=850_000.0, instagram_followers=150_000.0)
    row = fresh_db.get_audience_metrics(performance_id)
    assert row["monthly_listeners"] == 850_000.0
    assert row["instagram_followers"] == 150_000.0
    assert row["tiktok_followers"] is None

    fresh_db.upsert_audience_metrics(performance_id, monthly_listeners=900_000.0)
    row = fresh_db.get_audience_metrics(performance_id)
    assert row["monthly_listeners"] == 900_000.0
    assert row["instagram_followers"] == 150_000.0


def test_upsert_financial_details_inserts_then_updates(fresh_db):
    performance_id = _make_performance(fresh_db)
    fresh_db.upsert_financial_details(performance_id, artist_guarantee=20000.0, merch_revenue=1500.0)
    row = fresh_db.get_financial_details(performance_id)
    assert row["artist_guarantee"] == 20000.0
    assert row["merch_revenue"] == 1500.0
    assert row["venue_rental"] is None


def test_upsert_touring_history_inserts_then_updates(fresh_db):
    performance_id = _make_performance(fresh_db)
    fresh_db.upsert_touring_history(
        performance_id, sold_out_similar_venues=True, no_shows_count=0,
        venue_size_progression="growing",
    )
    row = fresh_db.get_touring_history(performance_id)
    assert row["sold_out_similar_venues"] is True
    assert row["no_shows_count"] == 0
    assert row["venue_size_progression"] == "growing"


def test_upsert_market_competition_inserts_then_updates(fresh_db):
    performance_id = _make_performance(fresh_db)
    fresh_db.upsert_market_competition(
        performance_id, other_concerts_count=2, weather_season_risk="medium",
        major_holiday_conflict=False,
    )
    row = fresh_db.get_market_competition(performance_id)
    assert row["other_concerts_count"] == 2
    assert row["weather_season_risk"] == "medium"
    assert row["major_holiday_conflict"] is False


def test_new_detail_tables_return_none_when_absent(fresh_db):
    performance_id = _make_performance(fresh_db)
    assert fresh_db.get_audience_metrics(performance_id) is None
    assert fresh_db.get_financial_details(performance_id) is None
    assert fresh_db.get_touring_history(performance_id) is None
    assert fresh_db.get_market_competition(performance_id) is None
