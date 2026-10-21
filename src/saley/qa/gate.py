"""The gate's own server (D113): `saley_test`, `GATE_PORT`, worker slot `worker:gate`.

Why: gate step 7 used to start `saley dev` on `WEB_PORT` against the dev database, so a human's
own dev server blocked the gate, and the gate's QA seed and worker touched the dev data. The
gate now runs on its own port, on the test database, as its own worker slot. It never touches
`WEB_PORT`, the dev database or the dev worker.

The test database is shared with pytest. A pytest session drops the schema when it starts, so
the gate holds the same advisory lock as `tests/conftest.py` while its server runs: a pytest
run started meanwhile waits instead of dropping the tables under the gate.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from alembic import command
from sqlalchemy.engine import make_url

from saley.core.settings import Settings, get_settings

TEST_DB_LOCK = 727_302_019  # one owner of the test database at a time (pytest or the gate)
TEST_DB_LOCK_WAIT = "30min"
GATE_SLOT = "worker:gate"
RO_ROLE = "saley_ro"


class GateConfigError(RuntimeError):
    """The gate cannot run safely with these settings. The text names the setting."""


@dataclass(frozen=True)
class GatePlan:
    port: int
    database: str  # the database name only: never print a URL (it holds a password)
    env: dict[str, str]


def plan(settings: Settings | None = None) -> GatePlan:
    """Where the gate runs and the environment its child processes get."""
    s = settings or get_settings()
    if s.gate_port == s.web_port:
        raise GateConfigError(f"GATE_PORT ({s.gate_port}) must differ from WEB_PORT")
    test_url = make_url(s.effective_test_database_url)
    name = test_url.database or ""
    if not name.endswith("_test"):
        raise GateConfigError("the gate database name must end in _test (TEST_DATABASE_URL)")
    if name == (make_url(s.database_url).database or ""):
        raise GateConfigError("TEST_DATABASE_URL must not be the dev database")
    rw = test_url.render_as_string(hide_password=False)
    ro = make_url(s.database_url_ro).set(database=name).render_as_string(hide_password=False)
    env = {
        "DATABASE_URL": rw,
        "DATABASE_URL_ADMIN": rw,
        "DATABASE_URL_RO": ro,
        "WORKER_SLOT": GATE_SLOT,
        "SALEY_ENV": "dev",
        # R4 (D114-D116): every /api route needs a session; the gate signs in through the mock.
        "AUTH_PROVIDER": "mock",
        # The gate presses controls; a sheet write belongs only in SALEY_TEST (docs/16 §3).
        "SHEET_WRITE_ENABLED": "false",
    }
    return GatePlan(port=s.gate_port, database=name, env=env)


def hold_and_reset(gate: GatePlan) -> psycopg.Connection:
    """Take the test-database lock, then rebuild the schema at head. Keep the result open.

    The caller closes the connection when the gate's server has stopped; that frees the lock.
    """
    url = make_url(gate.env["DATABASE_URL"])
    conn = psycopg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        dbname=url.database,
        autocommit=True,
        connect_timeout=5,
    )
    try:
        conn.execute(f"set lock_timeout = '{TEST_DB_LOCK_WAIT}'")
        conn.execute("select pg_advisory_lock(%s)", (TEST_DB_LOCK,))
        conn.execute("drop schema public cascade")
        conn.execute("create schema public")
        # A new schema gives other roles no USAGE; the web login reads the views through it.
        conn.execute(
            "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
            "grant usage on schema public to saley_ro; end if; end $$"
        )
        from saley.cli import alembic_config

        command.upgrade(alembic_config(gate.env["DATABASE_URL"]), "head")
    except Exception:
        conn.close()
        raise
    return conn
