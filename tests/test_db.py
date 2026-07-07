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

    fresh_db.upsert_demand_metrics(performance_id, local_fan_density=42.0, fan_sentiment_score=80.0)
    row = fresh_db.get_demand_metrics(performance_id)
    assert row["local_fan_density"] == 42.0
    assert row["fan_sentiment_score"] == 80.0
    assert row["demand_growth_rate"] is None

    fresh_db.upsert_demand_metrics(performance_id, local_fan_density=99.0)
    row = fresh_db.get_demand_metrics(performance_id)
    assert row["local_fan_density"] == 99.0
    assert row["fan_sentiment_score"] == 80.0  # untouched by the second call


def test_upsert_demand_metrics_rejects_unknown_field(fresh_db):
    performance_id = _make_performance(fresh_db)
    with pytest.raises(ValueError):
        fresh_db.upsert_demand_metrics(performance_id, not_a_real_field=1)
