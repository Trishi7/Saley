"""Shared fixtures. Tests run on a PostgreSQL test database, never SQLite (decision D17).

The test database is TEST_DATABASE_URL, else DATABASE_URL with `_test` added to the name.
Its name must end in `_test`: the schema is dropped and rebuilt with Alembic at session start.
Tests never read `.env`; settings come from the process environment and defaults.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from saley.core import egress
from saley.core.settings import Settings, get_settings

Settings.model_config["env_file"] = None  # never read .env in tests

RO_TEST_URL: dict[str, str] = {}

DATA_TABLES = (
    "touch",
    "stage_log",
    "opportunity",
    "contact",
    "alias",
    "account",
    "deliverable",
    "goal",
    "strategy",
    "package",
    "event",
    "metric_snapshot",
    "data_quality",
    "job_runs",
    "job_locks",
    "egress_log",
    "parity_checks",
    "llm_calls",
    "llm_cache",
)


def _test_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL") or Settings().effective_test_database_url
    if not (make_url(url).database or "").endswith("_test"):
        raise RuntimeError("The test database name must end in _test")
    return url


def _ensure_database(url: str) -> None:
    u = make_url(url)
    with psycopg.connect(
        host=u.host,
        port=u.port or 5432,
        user=u.username,
        password=u.password,
        dbname="postgres",
        autocommit=True,
        connect_timeout=5,
    ) as conn:
        if not conn.execute(
            "select 1 from pg_database where datname = %s", (u.database,)
        ).fetchone():
            conn.execute(f'create database "{u.database}"')


@pytest.fixture(scope="session")
def test_db_url() -> Iterator[str]:
    url = _test_url()
    _ensure_database(url)
    # The real read-only login, pointed at the test database (test_core checks it cannot write).
    RO_TEST_URL["url"] = (
        make_url(Settings().database_url_ro)
        .set(database=make_url(url).database)
        .render_as_string(hide_password=False)
    )
    mp = pytest.MonkeyPatch()
    for name in ("DATABASE_URL", "DATABASE_URL_RO", "DATABASE_URL_ADMIN"):
        mp.setenv(name, url)
    mp.setenv("SALEY_ENV", "dev")
    mp.setenv("TEST_MODE", "true")
    get_settings.cache_clear()
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("drop schema public cascade"))
        conn.execute(text("create schema public"))
    engine.dispose()
    from saley.cli import alembic_config

    command.upgrade(alembic_config(url), "head")
    yield url
    from saley.core.db import dispose_engines

    dispose_engines()
    mp.undo()
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def engine(test_db_url: str) -> Iterator[Engine]:
    from saley.core.db import rw_engine

    yield rw_engine()


@pytest.fixture(autouse=True)
def _quiet_egress() -> Iterator[None]:
    """Each test gets a fresh egress policy without DB counting (tests opt in)."""
    egress.reset_policy(egress.build_policy(recorder=None))
    yield
    egress.reset_policy(None)


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    """A session on the test DB. All data rows are removed after the test."""
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        with engine.begin() as conn:
            conn.execute(text(f"truncate {', '.join(DATA_TABLES)} restart identity cascade"))


# ------------------------------------------------------------------ sheet fixtures

POC_HEADERS = [
    "Sr No",
    "Company/Uni",
    "Name",
    "About PoC",
    "Industry",
    "Designation",
    "Email id",
    "Based (Sept 2026)",
    "Research Paper Link",
    "LI Url",
    "First Contact",
    "First Contact Type",
    "First Contact Date",
    "Sid - LI Addition",
    "LI Connected Date",
    "Next Steps (dropdown)",
    "Connected with VR on LI",
    "Application Bucket",
    "1st Email Sent",
    "1st Email Date",
    "2nd Email Sent",
    "2nd Email Date",
    "3rd Email Sent",
    "3rd Email Date",
    "LI DM Sent",
    "LI DM Date",
    "Meeting Date",
    "Meeting Status",
    "Reconnect Date",
    "Notes/Remarks",
    "Package",
    "Status",
    "Closure Prob%",
    "Estd. Deal Size (USD)",
    "Deal Status",
    "Priority",
    "X handle (or website)",
]
POC_ROLE_ORDER = [
    "sr_no",
    "company",
    "name",
    "about_poc",
    "industry",
    "designation",
    "email",
    "based",
    "paper_links",
    "li_url",
    "first_contact",
    "first_contact_type",
    "first_contact_date",
    "sid_li_added",
    "li_connected_date",
    "outreach_step",
    "connected_with_vr",
    "application_bucket",
    "email_1_sent",
    "email_1_date",
    "email_2_sent",
    "email_2_date",
    "email_3_sent",
    "email_3_date",
    "li_dm_sent",
    "li_dm_date",
    "meeting_date",
    "meeting_status",
    "reconnect_date",
    "next_steps",
    "package",
    "prospect_status",
    "closure_prob",
    "deal_size",
    "deal_status",
    "poc_priority",
    "x_handle",
]
SHEETS_EPOCH = date(1899, 12, 30)


def serial(day: date) -> int:
    """A real date as Google returns it with SERIAL_NUMBER."""
    return (day - SHEETS_EPOCH).days


def poc_row(**cells: Any) -> list[Any]:
    return [cells.get(role, "") for role in POC_ROLE_ORDER]


def default_sheet(today: date) -> dict[str, list[list[Any]]]:
    """A small GTM sheet with one row per interesting case."""
    week = today - timedelta(days=today.weekday())
    late = today - timedelta(days=20)
    return {
        "Outreach PoCs": [
            list(POC_HEADERS),  # a copy: tests change the header row
            poc_row(
                sr_no=1,
                company="Google DeepMind",
                name="Dinesh Tewari",
                industry="AI Labs",
                designation="Research Program Manager",
                email="dinesh@example.com",
                first_contact="VR",
                first_contact_type="LinkedIn",
                first_contact_date=serial(week),
                sid_li_added="Connected",
                li_connected_date=serial(week),
                outreach_step="Send email 1",
                poc_priority="P1",
            ),
            poc_row(
                sr_no=2,
                company="Google",
                name="Alex Search",
                industry="AI Labs",
                first_contact=True,
                sid_li_added="Sent",
                first_contact_date="02.09.2026",
                poc_priority="P1",
            ),
            poc_row(
                sr_no=3,
                company="Sarvam AI",
                name="Sshubam Verma",
                industry="AI Voice Agents",
                first_contact="Sid",
                sid_li_added="Connected",
                email_1_sent=True,
                email_1_date=serial(week - timedelta(days=7)),
                first_contact_date=serial(week - timedelta(days=7)),
            ),
            poc_row(
                sr_no=4,
                company="Middlesex University London",
                name="David Gamez",
                industry="Academia",
                first_contact=True,
                sid_li_added="Connected",
                meeting_date=serial(today - timedelta(days=10)),
                meeting_status="completed",
                prospect_status="Lead >50%",
                closure_prob=0.6,
                deal_size="USD 30k",
                email="d@example.ac.uk",
            ),
            poc_row(sr_no=5, company="Rosebud AI", name="Ro One", first_contact=False),
            poc_row(
                sr_no=6,
                company="Rosebud wellness",
                name="Ro Two",
                first_contact=False,
                first_contact_date="not a date",
            ),
            poc_row(
                sr_no=7,
                company="Parked Co",
                name="Pat Park",
                first_contact=True,
                reconnect_date=serial(today - timedelta(days=1)),
            ),
        ],
        "Master Pipeline": [
            ["Company", "Industry", "Geography", "Approx. Funding", "Outreach Line - Researchers"],
            ["Google DeepMind", "AI Labs", "UK", "", ""],
            ["Sarvam AI", "AI Voice Agents", "India", "$41M", ""],
            ["AI4Bharat", "AI Labs", "India", "$12M", ""],
        ],
        "Sept2026_Sales Strategy": [
            [
                "Sr No.",
                "Strategy Name",
                "Description",
                "ICP Type",
                "Target ICPs/month",
                "Target Orgs/month",
                "Collaterals",
                "Are all ready?",
                "What's pending?",
                "Timeline",
                "Success Metrics",
            ],
            [
                1,
                "Founder-led",
                "",
                "Labs",
                5,
                2,
                "",
                "No",
                "Pulse Product Overview Document",
                "",
                "",
            ],
        ],
        "Q4-OND2026-Goal Setting": [
            [
                "Row No.",
                "Goal Setting",
                "Estd. Value",
                "Success Metric",
                "Q4 Target",
                "Accountable",
                "Dependencies",
                "Total Leads",
                "Status",
                "Due Date",
            ],
            [
                1,
                "Close one evals project with a tier-one AI team",
                "USD 30–50k",
                "",
                "3",
                "Sid",
                "NDA, MSA, Overview doc",
                "",
                "",
                serial(date(2026, 12, 31)),
            ],
            [
                2,
                "Close one training project with a leading AI team",
                "USD 30–50k",
                "",
                "3",
                "Sid",
                "NDA, MSA",
                "",
                "",
                serial(date(2026, 12, 31)),
            ],
            [
                3,
                "Close one Hinglish STT project with an Indian sovereign model",
                "USD 10–20k",
                "",
                "2",
                "Sid",
                "NDA",
                "",
                "",
                serial(date(2026, 12, 31)),
            ],
            [
                4,
                "Active commercial conversation with one leading global AI lab",
                "USD 50–100k",
                "",
                "3",
                "Sid",
                "Overview doc",
                "",
                "",
                serial(date(2026, 12, 31)),
            ],
            [
                5,
                "Land one researcher project at a leading university",
                "",
                "",
                "1",
                "Sid",
                "",
                "",
                "",
                serial(date(2026, 12, 31)),
            ],
        ],
        "Deliverables Checklist": [
            [
                "Sr No",
                "Action Item",
                "Functional Dependency",
                "Priority",
                "Tentative Deadline",
                "Timelines",
                "Link",
                "Status",
                "Remarks",
                "Reminder Freq",
            ],
            [1, "NDA", "Legal", "P1", serial(late), "", "", "Not started", "", ""],
            [
                2,
                "Pulse Product Overview Document",
                "Design",
                "P1",
                serial(late),
                "",
                "",
                "In progress",
                "",
                "",
            ],
            [3, "Brochure", "Design", "P2", serial(late), "", "", "Completed", "", ""],
            [
                4,
                "Future item",
                "Sales",
                "P2",
                serial(today + timedelta(days=5)),
                "",
                "",
                "",
                "",
                "",
            ],
        ],
        "Sales Packages": [
            ["Package", "Name", "Purpose", "Use Case", "Size", "% Completion", "Ready?", "Status"],
            ["Package 7", "Hinglish evals", "Evals", "", "", 1, "Yes", "Complete"],
            [
                "Package 3",
                "Hinglish STT training set",
                "Training",
                "",
                "",
                0.33,
                "No",
                "In progress",
            ],
        ],
        "AI Events & Summits": [
            [
                "Sr No",
                "Location",
                "Event Name",
                "Link",
                "Date",
                "Timings",
                "Key People",
                "Last day for registration",
                "Registered?",
                "Attended?",
            ],
            [
                1,
                "London",
                "AI LIVE London",
                "",
                "20–21 Oct 2026",
                "",
                "",
                "",
                "not available",
                "No",
            ],
        ],
        "Sales Funnel - March-June 2026": [["Vertical", "Contacted"], ["AI Labs", 100]],
    }


@pytest.fixture
def sheet_today() -> date:
    from saley.core.clock import today_ist

    return today_ist()


@pytest.fixture
def raw_sheet(sheet_today: date) -> dict[str, list[list[Any]]]:
    return default_sheet(sheet_today)


@pytest.fixture
def synced(db: Session, raw_sheet: dict[str, list[list[Any]]]) -> Session:
    """The default sheet, synced and committed, plus one ok sheet_sync job_runs row."""
    from saley.core.clock import now_ist
    from saley.sales.sheet import build_tabs
    from saley.sales.sync import run_sync

    run_sync(db, build_tabs(raw_sheet), now_ist())
    db.execute(
        text(
            "insert into job_runs(job, status, started_at, finished_at, note) "
            "values ('sheet_sync', 'ok', now(), now(), 'fixture')"
        )
    )
    db.commit()
    return db
