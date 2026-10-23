"""Shared fixtures. Tests run on a PostgreSQL test database, never SQLite (decision D17).

The test database is TEST_DATABASE_URL, else DATABASE_URL with `_test` added to the name.
Its name must end in `_test`: the schema is dropped and rebuilt with Alembic at session start.
Tests never read `.env`; settings come from the process environment and defaults.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from saley.core import egress
from saley.core.settings import Settings, get_settings

Settings.model_config["env_file"] = None  # never read .env in tests

RO_TEST_URL: dict[str, str] = {}
# Does the saley_ro login exist on the test server? Set once per session (`_ensure_roles`).
RO_ROLE_STATE: dict[str, bool] = {"ok": False}
RO_ROLE_MISSING = (
    "role saley_ro missing and the test login cannot create it (run deploy/sql/roles.sql)"
)

DATA_TABLES = (
    "answer_intent",
    "discord_event_link",
    "chat_fact",
    "chat_summary",
    "chat_message",
    "bot_interaction",
    "bot_exchange",
    "bot_post",
    "weekly_pack",
    "scorecard_snapshot",
    "web_session",
    "notes_sync_state",
    "notes_file",
    "search_hit",
    "search_request",
    "news_run_request",
    "signal",
    "news_sent",
    "news_dropped",
    "news_seen",
    "news_stories",
    "news_source_runs",
    "news_editions",
    "news_sources",
    "user_seen",
    "ask",
    "reminder",
    "commitment",
    "fact",
    "sync_audit",
    "sync_outbox",
    "decision",
    "proposal",
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


def _ensure_roles(url: str) -> bool:
    """Make the web and admin roles when they are missing and this login may (B2).

    Why: CI starts an empty server. `deploy/sql/roles.sql` is the real script and CI runs it,
    but a developer who forgot it should not see bare failures. A superuser login creates the
    two roles with the same names and passwords as roles.sql (idempotent). Any other login
    cannot: then the role tests skip with a clear reason (`require_ro_role`).
    The roles must exist BEFORE the migrations run: they grant to saley_ro only if it exists.
    """
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
        have = {
            r[0]
            for r in conn.execute(
                "select rolname from pg_roles where rolname in ('saley_ro', 'saley_admin')"
            )
        }
        if "saley_ro" in have:
            return True
        row = conn.execute("select rolsuper from pg_roles where rolname = current_user").fetchone()
        if not (row and row[0]):
            return False
        for role in ("saley_ro", "saley_admin"):
            if role not in have:
                conn.execute(f"create role {role} login password '{role}'")
        conn.execute(f'grant connect on database "{u.database}" to saley_ro')
        conn.execute("alter role saley_ro set default_transaction_read_only = on")
        return True


# One owner of the test database at a time: a pytest session or the gate's server (D113).
from saley.qa.gate import TEST_DB_LOCK, TEST_DB_LOCK_WAIT  # noqa: E402


def _own_test_database(url: str) -> psycopg.Connection:
    """Wait until no other pytest session uses this database, then hold it until we are done.

    Why: a session starts with `drop schema public cascade`. A second pytest run against the same
    database (a reviewer agent, a CI step, a second terminal) would drop the tables under the
    first run and give random setup ERRORs in unrelated tests (the test_signals flake). An
    advisory lock on a held connection serialises the runs. It is freed when the process ends.
    """
    u = make_url(url)
    conn = psycopg.connect(
        host=u.host,
        port=u.port or 5432,
        user=u.username,
        password=u.password,
        dbname=u.database,
        autocommit=True,
        connect_timeout=5,
    )
    conn.execute(f"set lock_timeout = '{TEST_DB_LOCK_WAIT}'")
    conn.execute("select pg_advisory_lock(%s)", (TEST_DB_LOCK,))
    return conn


def require_ro_role() -> None:
    """Skip a test that needs the real web role when the role is not there (B2)."""
    if not RO_ROLE_STATE["ok"]:
        pytest.skip(RO_ROLE_MISSING)


@pytest.fixture(scope="session")
def test_db_url() -> Iterator[str]:
    url = _test_url()
    _ensure_database(url)
    owner = _own_test_database(url)
    RO_ROLE_STATE["ok"] = _ensure_roles(url)
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
    owner.close()  # the next waiting session may start


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


# ------------------------------------------------------------------ write helpers (R2)


def contact_id(db: Session, name: str) -> int:
    """The id of a synced fixture person, by name."""
    return db.execute(text("select id from contact where name = :n"), {"n": name}).scalar_one()


def sql_act(
    db: Session,
    user: str,
    cells: list[dict[str, str]],
    targets: list[int],
    *,
    action: str = "write",
    surface: str = "dashboard",
    key: str | None = None,
    task: str | None = None,
    cooldown: int = 2,
    allow_email: bool = False,
) -> dict[str, Any]:
    """Call saley_act directly (what the web and the chat bot do), and commit."""
    import json
    import secrets

    payload: dict[str, Any] = {"task": task} if task else {"cells": cells}
    result = db.execute(
        text("select saley_act(:u, :s, :a, cast(:t as jsonb), cast(:p as jsonb), :k, :c, 10, :e)"),
        {
            "u": user,
            "s": surface,
            "a": action,
            "t": json.dumps(targets),
            "p": json.dumps(payload),
            "k": key or secrets.token_hex(16),
            "c": cooldown,
            "e": allow_email,
        },
    ).scalar_one()
    db.commit()
    return result


def sql_row_add(
    db: Session,
    user: str,
    account_id: int,
    fields: dict[str, str],
    *,
    surface: str = "dashboard",
    key: str | None = None,
    allow_email: bool = False,
) -> dict[str, Any]:
    """Call saley_act(row_add) directly: one new person row for a company, and commit."""
    import json
    import secrets

    payload = {"fields": [{"role": r, "value": v} for r, v in fields.items()]}
    result = db.execute(
        text(
            "select saley_act(:u, :s, 'row_add', cast(:t as jsonb), cast(:p as jsonb), "
            ":k, 2, 10, :e)"
        ),
        {
            "u": user,
            "s": surface,
            "t": json.dumps([account_id]),
            "p": json.dumps(payload),
            "k": key or secrets.token_hex(16),
            "e": allow_email,
        },
    ).scalar_one()
    db.commit()
    return result


def sql_call(db: Session, sql: str, **params: Any) -> dict[str, Any]:
    result = db.execute(text(sql), params).scalar_one()
    db.commit()
    return result


@pytest.fixture
def fake_sheet(raw_sheet: dict[str, list[list[Any]]]):
    """A FakeSheet holding the same cells as the synced fixture sheet."""
    from saley.sales.writer import FakeSheet

    return FakeSheet(raw_sheet)


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


# ------------------------------------------------------------------ LLM fixtures (R3)

FIXTURES = Path(__file__).parent / "fixtures"


def llm_fixture(name: str) -> dict[str, Any]:
    """A recorded model answer from tests/fixtures/llm/<name>.json."""
    return json.loads((FIXTURES / "llm" / f"{name}.json").read_text(encoding="utf-8"))


def score_everything(content: str) -> dict[str, Any]:
    """An answer that scores every `id: N` found in the prompt (for batch and cap tests)."""
    ids = [int(n) for n in re.findall(r"^id: (\d+)$", content, flags=re.MULTILINE)]
    return {
        "items": [{"id": i, "importance": 3, "impact": 1, "tag": "Market", "why": ""} for i in ids]
    }


class FixtureLLM:
    """A fake Anthropic client. No network: each call answers from a recorded fixture.

    `answers` is a list of fixture names or dicts, used in order (the last one repeats), or a
    function from the prompt text to an answer dict. `calls` holds every request sent.
    """

    def __init__(self, answers: list[str | dict[str, Any]] | Callable[[str], dict[str, Any]]):
        self.answers = answers
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def client_factory(self) -> Any:
        return SimpleNamespace(messages=self)

    def create(self, **kwargs: Any) -> Any:
        from saley.core import llm

        self.calls.append(kwargs)
        content = kwargs["messages"][0]["content"]
        if callable(self.answers):
            answer: Any = self.answers(content)
        else:
            answer = self.answers[min(len(self.calls), len(self.answers)) - 1]
            answer = llm_fixture(answer) if isinstance(answer, str) else answer
        block = SimpleNamespace(type="tool_use", name=llm.TOOL_NAME, input=answer)
        return SimpleNamespace(
            content=[block],
            stop_reason="tool_use",
            usage=SimpleNamespace(input_tokens=100, output_tokens=40),
        )


@pytest.fixture
def set_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    """Change settings for one test: set_env(SCORER_BATCH="2"). The cache is reset after."""

    def apply(**values: str) -> None:
        for name, value in values.items():
            monkeypatch.setenv(name, value)
        get_settings.cache_clear()

    yield apply
    monkeypatch.undo()
    get_settings.cache_clear()


# ------------------------------------------------------------------ sign-in (R4, D114-D116)


def sign_in(client: Any, email: str | None = None) -> Any:
    """Sign a TestClient in through POST /auth/mock (dev + loopback). Returns the client.

    Why: since R4 every /api route needs a session in every mode. No `email` signs in as
    DEV_USER_EMAIL (DEV_USER_SHORT). An `email` must be on WEB_ALLOWED_EMAILS.
    """
    data = {"email": email} if email else None
    response = client.post("/auth/mock", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text
    return client


class SignedClient(TestClient):
    """A TestClient that signs in as the dev user when its `with` block opens."""

    def __enter__(self) -> SignedClient:
        super().__enter__()
        sign_in(self)
        return self


@pytest.fixture
def signed_in() -> Callable[..., Any]:
    """`signed_in(client, email=None)` signs a client in (see `sign_in`)."""
    return sign_in


# ------------------------------------------------------------------ chat instructions (R4 ticket 7)

# Fake Discord ids (never real ones). Approvers = Kushal, Sid, Vaishnavi; Trishi may only
# instruct (D135).
TEAM_IDS = {
    "sid": "300000000000000003",
    "kushal": "400000000000000004",
    "vaishnavi": "500000000000000005",
    "trishi": "600000000000000006",
}
STRANGER_ID = 777


@pytest.fixture
def team(synced: Session, set_env: Callable[..., None]) -> Iterator[Session]:
    """The synced sheet, the four people mapped to fake Discord ids, and the D135 allowlists."""
    from saley.bot import identity

    approvers = ",".join(TEAM_IDS[s] for s in ("kushal", "sid", "vaishnavi"))
    set_env(
        DISCORD_USER_MAP=";".join(f"{short}:{i}" for short, i in TEAM_IDS.items()),
        DISCORD_APPROVER_IDS=approvers,
        DISCORD_INSTRUCT_IDS=",".join(TEAM_IDS.values()),
        # The R4 tests use three approvers; D257 (R7) narrows Discord writes to DISCORD_WRITE_IDS
        # (default sid,vaishnavi), tested in tests/test_bot_press.py.
        DISCORD_WRITE_IDS="kushal,sid,vaishnavi",
        SALEY_ENV="dev",
        TEST_MODE="true",
    )
    identity.sync_user_map(synced, get_settings())
    yield synced
    synced.rollback()
    synced.execute(text("update users set discord_id = null, discord_writer = false"))
    synced.commit()


def make_card(
    db: Session, name: str = "Alex Search", role: str = "meeting_status", value: str = "completed"
) -> int:
    """One open action card (a proposal with one cell) for a synced person. Returns its id."""
    person = db.execute(
        text("select contact_id as id, sheet_row_key, company from v_task_inputs where name = :n"),
        {"n": name},
    ).one()
    payload = {
        "contact_id": person.id,
        "row": person.sheet_row_key,
        "name": name,
        "company": person.company,
        "day": "2026-10-19",
        "status": "booked",
        "button": "mark_completed",
        "cells": [{"role": role, "value": value}],
    }
    card_id = db.execute(
        text(
            "insert into proposal (type, card_key, entity_type, entity_id, payload) values "
            "('meeting_status', :k, 'contact', :c, cast(:p as jsonb)) returning id"
        ),
        {"k": f"test:{person.sheet_row_key}:{role}", "c": person.id, "p": json.dumps(payload)},
    ).scalar_one()
    db.commit()
    return int(card_id)


# ------------------------------------------------------------------ R7 cards


@pytest.fixture
def writers(db: Session) -> Iterator[Session]:
    """D257: sid and vaishnavi may write from Discord (users.discord_writer); reset after."""
    db.execute(text("update users set discord_writer = short in ('sid', 'vaishnavi')"))
    db.commit()
    yield db
    db.rollback()
    db.execute(text("update users set discord_writer = false"))
    db.commit()


def put_card(
    db: Session,
    card_id: str,
    acts: dict[str, Any],
    keys: list[str] | None = None,
    *,
    card_type: str = "M10",
    state: str = "sent",
    extra: dict[str, Any] | None = None,
) -> int:
    """One card row as cards_plan writes it (payload with keys and acts). Returns its id."""
    payload = {"keys": keys or [], "acts": acts, "message": {"content": "x"}, **(extra or {})}
    row = db.execute(
        text(
            "insert into bot_post (kind, shape, channel, mentions, body, rank, due_at, state, "
            "card_type, priority, card_id, payload, rendered_hash) values "
            "('card', 'card', 'sales', '{}', '', 0, now(), :st, :t, 0, :c, cast(:p as jsonb), 'h') "
            "returning id"
        ),
        {"st": state, "t": card_type, "c": card_id, "p": json.dumps(payload)},
    ).scalar_one()
    db.commit()
    return int(row)


def press(db: Session, card_id: str, user: str, action: str, **values: Any) -> dict[str, Any]:
    """Call saley_card_press as the bot does, and commit."""
    return sql_call(
        db,
        "select saley_card_press(:c, :u, :a, cast(:p as jsonb), 'discord')",
        c=card_id,
        u=user,
        a=action,
        p=json.dumps({"undo_minutes": 5, "cooldown": 0, **values}),
    )


@pytest.fixture
def crew(synced: Session, set_env: Callable[..., None]) -> Iterator[Session]:
    """R7: the four people mapped to fake Discord ids, the default writers (sid, vaishnavi, D257),
    a sent-to-Discord-ready test setup (TEST_MODE, dev)."""
    from saley.bot import identity

    set_env(
        DISCORD_USER_MAP=";".join(f"{short}:{i}" for short, i in TEAM_IDS.items()),
        DISCORD_APPROVER_IDS=",".join(TEAM_IDS[s] for s in ("kushal", "sid", "vaishnavi")),
        DISCORD_INSTRUCT_IDS=",".join(TEAM_IDS.values()),
        DISCORD_BOT_TOKEN="tkn",
        DISCORD_TEST_CHANNEL_ID="100000000000000001",
        DISCORD_SALES_CHANNEL_ID="200000000000000002",
        SALEY_ENV="dev",
        TEST_MODE="true",
    )
    identity.sync_user_map(synced, get_settings())
    yield synced
    synced.rollback()
    synced.execute(text("update users set discord_id = null, discord_writer = false"))
    synced.commit()
