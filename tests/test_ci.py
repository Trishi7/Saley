"""R1.1-C01: the GitHub Actions run matches a fresh server (roles.sql, migrations, pytest).

Why: CI once failed in pytest because the Postgres service had no `saley_ro` role and the
worker registry ran `sheet_sync` with no sheet id. This test reads the workflow and checks the
parts that must hold on an empty server. It also migrates a throwaway database from nothing,
when the local login may create one. No network.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import psycopg
import yaml
from alembic import command
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
ROLES_SQL = ROOT / "deploy" / "sql" / "roles.sql"


def workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def steps() -> list[dict[str, Any]]:
    return workflow()["jobs"]["test"]["steps"]


def index_of(needle: str) -> int:
    found = [i for i, s in enumerate(steps()) if needle in str(s.get("run", ""))]
    assert len(found) == 1, f"want one step that runs {needle!r}, found {len(found)}"
    return found[0]


def test_ci_workflow_matches_a_fresh_database(test_db_url: str) -> None:
    job = workflow()["jobs"]["test"]
    service = job["services"]["postgres"]

    # 1. roles.sql runs against the service, as its superuser, before pytest.
    roles, pytest_step = index_of("roles.sql"), index_of("pytest")
    assert roles < pytest_step
    command_line = steps()[roles]["run"]
    superuser, password = service["env"]["POSTGRES_USER"], service["env"]["POSTGRES_PASSWORD"]
    assert "psql" in command_line and "-h localhost" in command_line
    assert f"-U {superuser}" in command_line and "-f deploy/sql/roles.sql" in command_line
    assert "ON_ERROR_STOP" in command_line  # a broken script must stop the run, not pass
    assert steps()[roles]["env"]["PGPASSWORD"] == password
    assert service["env"].get("POSTGRES_DB", "postgres") != "saley"  # roles.sql makes that one
    assert "5432:5432" in service["ports"]
    assert f"pg_isready -U {superuser}" in service["options"]

    # 2. The tests log in as `saley` to saley_test: the names roles.sql makes.
    url = make_url(job["env"]["TEST_DATABASE_URL"].replace("+psycopg", ""))
    sql = ROLES_SQL.read_text(encoding="utf-8")
    assert (url.host, url.port, url.database) == ("localhost", 5432, "saley_test")
    assert f"CREATE ROLE {url.username} LOGIN PASSWORD '{url.password}'" in sql
    assert f"CREATE DATABASE {url.database} OWNER {url.username}" in sql
    for role in ("saley_ro", "saley_admin"):
        assert f"CREATE ROLE {role} LOGIN PASSWORD '{role}'" in sql
    assert steps()[roles]["run"] != steps()[pytest_step]["run"]

    # 3. Tests need no sheet: nothing in the workflow sets a sheet id, a key file or a .env.
    text = WORKFLOW.read_text(encoding="utf-8")
    for name in ("GTM_SHEET_ID", "GOOGLE_SERVICE_ACCOUNT_FILE", "DISCORD", "ANTHROPIC", ".env"):
        assert name not in text, f"CI must not depend on {name}"

    # 4. Order of the other steps: lint and format come before the database work.
    assert index_of("ruff check") < index_of("ruff format") < roles < pytest_step
    assert "--cov-fail-under=80" in steps()[pytest_step]["run"]

    migrate_a_fresh_database(test_db_url)


def migrate_a_fresh_database(test_db_url: str) -> None:
    """Create a throwaway database, run every migration from nothing, drop it.

    Where the local login may not create a database (some laptops and the CI `saley` role), only
    the workflow structure above is asserted: that is the documented limit of this test.
    """
    from saley.cli import alembic_config

    base = make_url(test_db_url)
    name = f"saley_ci_{uuid.uuid4().hex[:8]}_test"
    connect = {
        "host": base.host,
        "port": base.port or 5432,
        "user": base.username,
        "password": base.password,
        "autocommit": True,
        "connect_timeout": 5,
    }
    try:
        with psycopg.connect(dbname="postgres", **connect) as admin:
            admin.execute(f'create database "{name}"')
    except psycopg.errors.InsufficientPrivilege:
        return  # this login cannot create a database: the workflow structure was the check
    try:
        fresh = base.set(database=name).render_as_string(hide_password=False)
        command.upgrade(alembic_config(fresh), "head")
        with psycopg.connect(dbname=name, **connect) as conn:
            head = conn.execute("select version_num from alembic_version").fetchone()
            tables = {
                r[0]
                for r in conn.execute("select tablename from pg_tables where schemaname = 'public'")
            }
        assert head is not None and re.fullmatch(r"\d{4}", head[0])
        assert {"users", "sync_outbox", "news_stories", "user_seen"} <= tables
    finally:
        with psycopg.connect(dbname="postgres", **connect) as admin:
            admin.execute(f'drop database if exists "{name}" with (force)')


def test_ci_has_node_and_the_web_checks_before_pytest() -> None:
    """R5-X06: CI green on a fresh runner with Node. Gate step 1 and the web unit tests need
    Node 24 and web/node_modules; CI was red when the test job had neither (21 Oct)."""
    test_steps = steps()
    node = [
        i
        for i, s in enumerate(test_steps)
        if str(s.get("uses", "")).startswith("actions/setup-node@")
    ]
    assert len(node) == 1, "the test job sets up Node once"
    assert test_steps[node[0]]["with"]["node-version-file"] == "web/.nvmrc"
    assert (ROOT / "web" / ".nvmrc").read_text(encoding="utf-8").strip() == "24"
    install = index_of("npm --prefix web ci --ignore-scripts")
    checks = index_of("npm --prefix web test")
    web_run = str(test_steps[checks]["run"])
    assert "npm --prefix web run lint" in web_run and "npm --prefix web run typecheck" in web_run
    pytest_at = index_of("uv run pytest")
    assert node[0] < install < checks < pytest_at
