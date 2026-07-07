import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import db


@pytest.fixture()
def fresh_db():
    """Truncates all tables and reseeds the default expense template before
    each test. Requires DATABASE_URL to point at a dedicated dev/test Postgres
    database - never the deployed app's production database, since this wipes
    every row on every test run."""
    db.init_db()
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"TRUNCATE {db.ALL_TABLES} RESTART IDENTITY CASCADE")
    db.init_db()
    return db
