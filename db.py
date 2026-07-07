import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

import config

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS talents (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        domain TEXT NOT NULL CHECK(domain IN ('music', 'actor')),
        external_ids_json TEXT NOT NULL DEFAULT '{}',
        genres_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS performances (
        id SERIAL PRIMARY KEY,
        talent_id INTEGER NOT NULL REFERENCES talents(id),
        venue_name TEXT,
        city TEXT NOT NULL,
        estimated_date TEXT,
        target_capacity INTEGER NOT NULL,
        budget REAL NOT NULL,
        assumed_ticket_price REAL,
        assumed_sell_through_rate REAL,
        notes TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS historical_comps (
        id SERIAL PRIMARY KEY,
        talent_id INTEGER REFERENCES talents(id),
        comparable_name TEXT NOT NULL,
        is_self INTEGER NOT NULL DEFAULT 1,
        domain TEXT NOT NULL CHECK(domain IN ('music', 'actor')),
        venue_name TEXT,
        city TEXT,
        event_date TEXT,
        capacity INTEGER,
        attendance INTEGER,
        ticket_price_avg REAL,
        gross_revenue REAL,
        talent_fee REAL,
        total_expenses REAL,
        source TEXT NOT NULL DEFAULT 'manual',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS expense_templates (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        venue_pct REAL NOT NULL,
        marketing_pct REAL NOT NULL,
        production_pct REAL NOT NULL,
        talent_fee_pct REAL NOT NULL,
        other_pct REAL NOT NULL,
        is_default INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS api_cache (
        id SERIAL PRIMARY KEY,
        source TEXT NOT NULL,
        cache_key TEXT NOT NULL,
        response_json TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        UNIQUE(source, cache_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS demand_metrics (
        id SERIAL PRIMARY KEY,
        performance_id INTEGER NOT NULL UNIQUE REFERENCES performances(id),
        local_fan_density REAL,
        search_interest_index REAL,
        social_engagement_rate REAL,
        streaming_popularity REAL,
        ticket_conversion_rate REAL,
        audience_purchasing_power REAL,
        market_competition_index INTEGER,
        vip_conversion_rate REAL,
        merch_revenue_total REAL,
        promoter_reliability_score REAL,
        fan_sentiment_score REAL,
        demand_growth_rate REAL,
        updated_at TEXT NOT NULL
    )
    """,
]

ALL_TABLES = (
    "talents, performances, historical_comps, expense_templates, api_cache, demand_metrics"
)

DEMAND_METRIC_FIELDS = [
    "local_fan_density", "search_interest_index", "social_engagement_rate",
    "streaming_popularity", "ticket_conversion_rate", "audience_purchasing_power",
    "market_competition_index", "vip_conversion_rate", "merch_revenue_total",
    "promoter_reliability_score", "fan_sentiment_score", "demand_growth_rate",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_connection():
    if not config.DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to .env (local) or the app's Secrets (deployed)."
        )
    conn = psycopg2.connect(config.DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_connection() as conn:
        cur = conn.cursor()
        for statement in SCHEMA_STATEMENTS:
            cur.execute(statement)
        cur.execute("SELECT id FROM expense_templates WHERE is_default = 1")
        existing_default = cur.fetchone()
        if not existing_default:
            cur.execute(
                """INSERT INTO expense_templates
                   (name, venue_pct, marketing_pct, production_pct, talent_fee_pct, other_pct, is_default)
                   VALUES (%s, %s, %s, %s, %s, %s, 1)""",
                ("Default", 0.25, 0.15, 0.20, 0.30, 0.10),
            )


# ---------------- talents ----------------

def create_talent(name: str, domain: str, external_ids: Optional[dict] = None,
                   genres: Optional[list] = None) -> int:
    now = _now()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO talents (name, domain, external_ids_json, genres_json, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (name, domain, json.dumps(external_ids or {}), json.dumps(genres or []), now, now),
        )
        return cur.fetchone()["id"]


def get_talent(talent_id: int):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM talents WHERE id = %s", (talent_id,))
        return cur.fetchone()


def find_talent(name: str, domain: str):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM talents WHERE name = %s AND domain = %s", (name, domain)
        )
        return cur.fetchone()


def get_or_create_talent(name: str, domain: str):
    existing = find_talent(name, domain)
    if existing:
        return existing
    talent_id = create_talent(name, domain)
    return get_talent(talent_id)


def update_talent_enrichment(talent_id: int, external_ids: dict, genres: list):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE talents SET external_ids_json = %s, genres_json = %s, updated_at = %s
               WHERE id = %s""",
            (json.dumps(external_ids), json.dumps(genres), _now(), talent_id),
        )


def list_talents() -> list:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM talents ORDER BY name")
        return cur.fetchall()


def search_talent_names(domain: str, query: str, limit: int = 8) -> list[str]:
    """Distinct talent names already in the DB (this domain) matching query."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT DISTINCT name FROM talents
               WHERE domain = %s AND name ILIKE %s
               ORDER BY name LIMIT %s""",
            (domain, f"%{query}%", limit),
        )
        return [r["name"] for r in cur.fetchall()]


def search_cities(query: str, limit: int = 8) -> list[str]:
    """Distinct cities from past bookings/comps matching query."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT DISTINCT city FROM (
                   SELECT city FROM performances WHERE city ILIKE %s
                   UNION
                   SELECT city FROM historical_comps WHERE city ILIKE %s
               ) cities
               WHERE city IS NOT NULL AND city != ''
               ORDER BY city LIMIT %s""",
            (f"%{query}%", f"%{query}%", limit),
        )
        return [r["city"] for r in cur.fetchall()]


def search_venues(query: str, limit: int = 8) -> list[str]:
    """Distinct venue names from past bookings/comps matching query."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT DISTINCT venue_name FROM (
                   SELECT venue_name FROM performances WHERE venue_name ILIKE %s
                   UNION
                   SELECT venue_name FROM historical_comps WHERE venue_name ILIKE %s
               ) venues
               WHERE venue_name IS NOT NULL AND venue_name != ''
               ORDER BY venue_name LIMIT %s""",
            (f"%{query}%", f"%{query}%", limit),
        )
        return [r["venue_name"] for r in cur.fetchall()]


# ---------------- performances ----------------

def create_performance(talent_id: int, venue_name: str, city: str, estimated_date: str,
                        target_capacity: int, budget: float,
                        assumed_ticket_price: Optional[float] = None,
                        assumed_sell_through_rate: Optional[float] = None,
                        notes: str = "") -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO performances
               (talent_id, venue_name, city, estimated_date, target_capacity, budget,
                assumed_ticket_price, assumed_sell_through_rate, notes, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (talent_id, venue_name, city, estimated_date, target_capacity, budget,
             assumed_ticket_price, assumed_sell_through_rate, notes, _now()),
        )
        return cur.fetchone()["id"]


def get_performance(performance_id: int):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM performances WHERE id = %s", (performance_id,))
        return cur.fetchone()


def list_performances_for_talent(talent_id: int) -> list:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM performances WHERE talent_id = %s ORDER BY created_at DESC", (talent_id,)
        )
        return cur.fetchall()


def list_all_performances() -> list:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT p.*, t.name AS talent_name, t.domain AS talent_domain
               FROM performances p JOIN talents t ON p.talent_id = t.id
               ORDER BY p.created_at DESC"""
        )
        return cur.fetchall()


# ---------------- historical_comps ----------------

def create_historical_comp(comparable_name: str, domain: str, is_self: bool = True,
                            talent_id: Optional[int] = None, venue_name: str = "",
                            city: str = "", event_date: str = "",
                            capacity: Optional[int] = None, attendance: Optional[int] = None,
                            ticket_price_avg: Optional[float] = None,
                            gross_revenue: Optional[float] = None,
                            talent_fee: Optional[float] = None,
                            total_expenses: Optional[float] = None,
                            source: str = "manual") -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO historical_comps
               (talent_id, comparable_name, is_self, domain, venue_name, city, event_date,
                capacity, attendance, ticket_price_avg, gross_revenue, talent_fee,
                total_expenses, source, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (talent_id, comparable_name, int(is_self), domain, venue_name, city, event_date,
             capacity, attendance, ticket_price_avg, gross_revenue, talent_fee,
             total_expenses, source, _now()),
        )
        return cur.fetchone()["id"]


def list_historical_comps(talent_id: Optional[int] = None, comparable_name: Optional[str] = None,
                           domain: Optional[str] = None, is_self: Optional[bool] = None) -> list:
    query = "SELECT * FROM historical_comps WHERE 1=1"
    params: list = []
    if talent_id is not None:
        query += " AND talent_id = %s"
        params.append(talent_id)
    if comparable_name is not None:
        query += " AND comparable_name = %s"
        params.append(comparable_name)
    if domain is not None:
        query += " AND domain = %s"
        params.append(domain)
    if is_self is not None:
        query += " AND is_self = %s"
        params.append(int(is_self))
    query += " ORDER BY event_date DESC"
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        return cur.fetchall()


def list_all_comps_for_domain(domain: str) -> list:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM historical_comps WHERE domain = %s ORDER BY event_date DESC", (domain,)
        )
        return cur.fetchall()


# ---------------- expense_templates ----------------

def get_default_expense_template():
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM expense_templates WHERE is_default = 1")
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("No default expense template found; call init_db() first.")
        return row


def list_expense_templates() -> list:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM expense_templates ORDER BY id")
        return cur.fetchall()


def update_expense_template(template_id: int, venue_pct: float, marketing_pct: float,
                             production_pct: float, talent_fee_pct: float, other_pct: float):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE expense_templates
               SET venue_pct = %s, marketing_pct = %s, production_pct = %s, talent_fee_pct = %s, other_pct = %s
               WHERE id = %s""",
            (venue_pct, marketing_pct, production_pct, talent_fee_pct, other_pct, template_id),
        )


# ---------------- api_cache ----------------

def cache_get(source: str, cache_key: str) -> Optional[dict]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM api_cache WHERE source = %s AND cache_key = %s", (source, cache_key)
        )
        row = cur.fetchone()
        if row is None:
            return None
        if row["expires_at"] < _now():
            return None
        return json.loads(row["response_json"])


def cache_set(source: str, cache_key: str, response: dict, ttl_seconds: int):
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO api_cache (source, cache_key, response_json, fetched_at, expires_at)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (source, cache_key) DO UPDATE SET
                   response_json = excluded.response_json,
                   fetched_at = excluded.fetched_at,
                   expires_at = excluded.expires_at""",
            (source, cache_key, json.dumps(response), now.isoformat(), expires_at),
        )


# ---------------- demand_metrics ----------------

def get_demand_metrics(performance_id: int):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM demand_metrics WHERE performance_id = %s", (performance_id,))
        return cur.fetchone()


def upsert_demand_metrics(performance_id: int, **fields):
    """fields may include any of DEMAND_METRIC_FIELDS; omitted ones are left
    NULL on insert or untouched on update (only provided keys are written)."""
    unknown = set(fields) - set(DEMAND_METRIC_FIELDS)
    if unknown:
        raise ValueError(f"Unknown demand metric field(s): {unknown}")

    columns = list(fields.keys())
    values = [fields[c] for c in columns]
    now = _now()

    with get_connection() as conn:
        cur = conn.cursor()
        insert_columns = ["performance_id", *columns, "updated_at"]
        insert_placeholders = ", ".join(["%s"] * len(insert_columns))
        update_clause = ", ".join(f"{c} = %s" for c in columns) + ", updated_at = %s"
        cur.execute(
            f"""INSERT INTO demand_metrics ({", ".join(insert_columns)})
                VALUES ({insert_placeholders})
                ON CONFLICT (performance_id) DO UPDATE SET {update_clause}""",
            (performance_id, *values, now, *values, now),
        )
