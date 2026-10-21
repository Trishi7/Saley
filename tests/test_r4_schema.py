"""R4 ticket 1: the schema of the weekly pack, real sign-in and the bot queue (migration 0018).

No network. The tables are written as the owner role; the web role (`saley_ro`) must read views
only and may call three SECURITY DEFINER functions (D114, D118, D132).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from saley.cli import alembic_config
from saley.core.db import dispose_engines
from tests.conftest import RO_TEST_URL, require_ro_role

NEW_TABLES = ("web_session", "scorecard_snapshot", "weekly_pack", "bot_post", "bot_exchange")
NEW_VIEWS = (
    "v_web_session",
    "v_weekly_pack",
    "v_scorecard_delta",
    "v_bot_queue_health",
    "v_scorecard_history",  # 0019
)
NEW_FUNCTIONS = ("saley_session_start", "saley_session_end", "saley_ask_log")


@pytest.fixture(autouse=True)
def _clean(db: Session) -> Iterator[None]:
    yield
    db.rollback()
    db.execute(
        text(
            "truncate web_session, scorecard_snapshot, weekly_pack, bot_post, bot_exchange "
            "restart identity cascade"
        )
    )
    db.commit()


def token_hash(seed: str = "a") -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def call(db: Session, sql: str, **params: object) -> Any:
    return db.execute(text(sql), params).scalar_one()


def start(db: Session, user: str = "sid", provider: str = "mock", h: str | None = None) -> Any:
    return call(
        db,
        "select saley_session_start(:u, :p, :h, 12)",
        u=user,
        p=provider,
        h=h or token_hash(),
    )


# ------------------------------------------------------------------ tables


def test_new_tables_and_views_exist(db: Session) -> None:
    for name in (*NEW_TABLES, *NEW_VIEWS):
        assert db.execute(text("select to_regclass(:n)"), {"n": name}).scalar() is not None
    for name in NEW_FUNCTIONS:
        assert db.execute(text("select to_regproc(:n)"), {"n": name}).scalar() is not None


def test_users_discord_id_is_unique(db: Session) -> None:
    try:
        db.execute(text("update users set discord_id = '111' where short = 'sid'"))
        with pytest.raises(IntegrityError):
            db.execute(text("update users set discord_id = '111' where short = 'kushal'"))
    finally:
        db.rollback()


def test_weekly_pack_week_start_must_be_a_monday(db: Session) -> None:
    db.execute(text("insert into weekly_pack (week_start, body) values ('2026-10-19', '{}')"))
    db.commit()
    with pytest.raises(IntegrityError):  # a Tuesday
        db.execute(text("insert into weekly_pack (week_start, body) values ('2026-10-20', '{}')"))
    db.rollback()
    with pytest.raises(IntegrityError):  # the same Monday twice
        db.execute(text("insert into weekly_pack (week_start, body) values ('2026-10-19', '{}')"))
    db.rollback()


def test_scorecard_snapshot_key_is_day_and_industry(db: Session) -> None:
    sql = "insert into scorecard_snapshot (day, industry, people) values ('2026-10-19', 'AI', 3)"
    db.execute(text(sql))
    db.commit()
    with pytest.raises(IntegrityError):
        db.execute(text(sql))
    db.rollback()


BOT_POST = (
    "insert into bot_post (shape, channel, body, due_at, {col}) "
    "values ('ping', {channel}, 'hi', now(), {val})"
)


def test_bot_post_defaults(db: Session) -> None:
    db.execute(text(BOT_POST.format(col="kind", channel="'test'", val="'x'")))
    row = db.execute(
        text("select state, rank, mentions, is_meeting, is_reconnect, for_user from bot_post")
    ).one()
    assert tuple(row) == ("queued", 0, [], False, False, None)
    db.rollback()


@pytest.mark.parametrize("channel", ["live", "", "TEST"])
def test_bot_post_channel_check(db: Session, channel: str) -> None:
    with pytest.raises(IntegrityError):
        db.execute(text(BOT_POST.format(col="kind", channel=f"'{channel}'", val="'x'")))
    db.rollback()


def test_bot_post_state_check(db: Session) -> None:
    for state in ("queued", "ready", "sent", "deferred", "dropped"):
        db.execute(text(BOT_POST.format(col="state", channel="'sales'", val=f"'{state}'")))
    db.commit()
    with pytest.raises(IntegrityError):
        db.execute(text(BOT_POST.format(col="state", channel="'sales'", val="'failed'")))
    db.rollback()


def test_bot_post_dm_needs_a_recipient(db: Session) -> None:
    """D132: a DM with no for_user would count for nobody and slip past the cap."""
    with pytest.raises(IntegrityError, match="ck_bot_post_dm_user"):
        db.execute(text(BOT_POST.format(col="kind", channel="'dm'", val="'x'")))
    db.rollback()


@pytest.mark.parametrize("col", ["channel", "state"])
def test_bot_post_channel_and_state_are_not_null(db: Session, col: str) -> None:
    sql = (
        "insert into bot_post (shape, channel, state, body, due_at) "
        "values ('ping', 'test', 'queued', 'hi', now())"
    ).replace(f"'{'test' if col == 'channel' else 'queued'}'", "null", 1)
    with pytest.raises(IntegrityError):
        db.execute(text(sql))
    db.rollback()


def test_web_session_expiry_is_indexed(db: Session) -> None:
    names = {
        r[0]
        for r in db.execute(
            text("select indexname from pg_indexes where tablename = 'web_session'")
        )
    }
    assert "ix_web_session_expires" in names


def test_bot_post_indexes(db: Session) -> None:
    names = {
        r[0]
        for r in db.execute(text("select indexname from pg_indexes where tablename = 'bot_post'"))
    }
    assert {"ix_bot_post_state_due", "ix_bot_post_user_sent"} <= names


def test_decision_surface_allows_discord(db: Session) -> None:
    sql = (
        "insert into decision (kind, choice, user_id, surface, state) "
        "select 'dismiss', 'x', id, :s, 'active' from users where short = 'sid'"
    )
    try:
        db.execute(text(sql), {"s": "discord"})
        with pytest.raises(IntegrityError):
            db.execute(text(sql), {"s": "slack"})
    finally:
        db.rollback()


# ------------------------------------------------------------------ session functions


def test_session_start_inserts_a_row_and_end_revokes_it(db: Session) -> None:
    out = start(db)
    db.commit()
    assert out["ok"] is True and out["expires_at"]
    row = db.execute(text("select id_hash, provider, revoked_at from web_session")).one()
    assert row.id_hash == token_hash() and row.provider == "mock" and row.revoked_at is None
    view = db.execute(text("select user_short, revoked_at from v_web_session")).one()
    assert view.user_short == "sid" and view.revoked_at is None

    ended = call(db, "select saley_session_end(:h)", h=token_hash())
    db.commit()
    assert ended["ok"] is True
    assert db.execute(text("select revoked_at is not null from web_session")).scalar_one() is True
    # Ending twice, or an unknown token, is a clean answer and not an error.
    assert call(db, "select saley_session_end(:h)", h=token_hash())["ok"] is False
    assert call(db, "select saley_session_end(:h)", h=token_hash("zz"))["ok"] is False


def test_session_start_refuses_unknown_user_and_bad_input(db: Session) -> None:
    assert start(db, user="nobody")["ok"] is False
    assert start(db, provider="github")["ok"] is False
    assert start(db, h="not-a-hash")["ok"] is False
    assert start(db, h=token_hash().upper())["ok"] is False
    for hours in (0, -1, 24 * 31):
        out = call(db, "select saley_session_start('sid','mock',:h,:n)", h=token_hash(), n=hours)
        assert out["ok"] is False
    assert call(db, "select count(*) from web_session") == 0


def test_session_start_refuses_a_duplicate_token(db: Session) -> None:
    assert start(db)["ok"] is True
    assert start(db, user="kushal")["ok"] is False
    assert call(db, "select count(*) from web_session") == 1
    db.rollback()


def test_session_start_refuses_a_role_that_is_not_allowed(db: Session) -> None:
    try:
        db.execute(text("alter table users drop constraint ck_users_role"))
        db.execute(text("update users set role = 'guest' where short = 'sid'"))
        assert start(db)["ok"] is False
    finally:
        db.rollback()


def test_ask_log_writes_one_row(db: Session) -> None:
    new_id = call(
        db,
        "select saley_ask_log('sid','discord','what is new?','news',jsonb_build_object('a', 1),"
        "'two items', now() - interval '1 day', now(), 3)",
    )
    db.commit()
    row = db.execute(text("select * from bot_exchange where id = :i"), {"i": new_id}).one()
    assert row.surface == "discord" and row.intent == "news" and row.left_out == 3
    assert row.refs == {"a": 1} and row.answer == "two items"


@pytest.mark.parametrize(
    "args",
    [
        "'nobody','discord','t','i','{}'::jsonb,'a',now(),now(),0",
        "'sid','slack','t','i','{}'::jsonb,'a',now(),now(),0",
        "'sid','discord',repeat('x',2001),'i','{}'::jsonb,'a',now(),now(),0",
        "'sid','discord','bad' || chr(7),'i','{}'::jsonb,'a',now(),now(),0",
        "'sid','discord','t','i','{}'::jsonb,'a' || chr(27),now(),now(),0",
        "'sid','discord','t','i','{}'::jsonb,'a',now(),now(),-1",
    ],
)
def test_ask_log_refuses_bad_input(db: Session, args: str) -> None:
    with pytest.raises(DBAPIError):
        db.execute(text(f"select saley_ask_log({args})"))
    db.rollback()
    assert call(db, "select count(*) from bot_exchange") == 0


# ------------------------------------------------------------------ views


def test_scorecard_delta_shows_last_week_or_null(db: Session) -> None:
    db.execute(
        text(
            "insert into scorecard_snapshot (day, industry, people, contacted, connect_pct, "
            "reply_pct, meetings, deals) values "
            "('2026-10-12','AI',10,4,20,null,1,0), ('2026-10-19','AI',12,6,30,5,2,1), "
            "('2026-10-19','Bio',4,1,null,null,0,0), ('2026-10-18','Old',9,9,9,9,9,9)"
        )
    )
    db.commit()
    rows = {
        r.industry: r for r in db.execute(text("select * from v_scorecard_delta order by industry"))
    }
    assert set(rows) == {"AI", "Bio"}  # only the latest snapshot day
    assert rows["AI"].people == 12 and rows["AI"].last_week_people == 10
    assert rows["AI"].last_week_connect_pct == 20 and rows["AI"].last_week_meetings == 1
    assert rows["AI"].last_week_reply_pct is None  # missing stays NULL, never 0 (D118)
    assert rows["Bio"].last_week_people is None and rows["Bio"].last_week_deals is None


def test_weekly_pack_and_queue_health_views(db: Session) -> None:
    db.execute(
        text(
            "insert into weekly_pack (week_start, body, post_id) "
            "values ('2026-10-19', jsonb_build_object('k', 1), 'p1')"
        )
    )
    db.execute(text(BOT_POST.format(col="state", channel="'test'", val="'queued'")))
    db.execute(text(BOT_POST.format(col="state", channel="'test'", val="'queued'")))
    db.execute(
        text(
            "insert into bot_post (shape, channel, body, due_at, state, sent_at) "
            "values ('ping','test','x', now(), 'sent', '2026-10-19 10:00+00')"
        )
    )
    db.commit()
    pack = db.execute(text("select week_start, body, post_id from v_weekly_pack")).one()
    assert str(pack.week_start) == "2026-10-19" and pack.body == {"k": 1}
    health = {r.state: r for r in db.execute(text("select * from v_bot_queue_health"))}
    assert health["queued"].n == 2 and health["sent"].n == 1
    assert health["sent"].last_sent_at is not None and health["queued"].last_sent_at is None


# ------------------------------------------------------------------ the web role


def test_web_role_has_views_and_functions_but_no_table_grants(db: Session) -> None:
    require_ro_role()
    for name in NEW_TABLES:
        priv = db.execute(
            text(
                "select has_table_privilege('saley_ro', :n, "
                "'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')"
            ),
            {"n": name},
        ).scalar_one()
        assert priv is False, name
    for name in NEW_VIEWS:
        assert db.execute(
            text("select has_table_privilege('saley_ro', :n, 'SELECT')"), {"n": name}
        ).scalar_one()
        assert not db.execute(
            text("select has_table_privilege('saley_ro', :n, 'INSERT, UPDATE, DELETE')"),
            {"n": name},
        ).scalar_one()
    for name in NEW_FUNCTIONS:
        row = db.execute(
            text(
                "select p.prosecdef, has_function_privilege('saley_ro', p.oid, 'EXECUTE'), "
                "has_function_privilege('public', p.oid, 'EXECUTE'), "
                "coalesce(p.proconfig::text, '') like '%pg_temp%' "
                "from pg_proc p where p.proname = :n and p.pronamespace = 'public'::regnamespace"
            ),
            {"n": name},
        ).one()
        assert tuple(row) == (True, True, False, True), name


def test_web_role_can_run_the_session_functions_and_read_the_view(db: Session) -> None:
    require_ro_role()
    ro = create_engine(RO_TEST_URL["url"])
    try:
        with ro.connect() as conn:
            conn.execute(text("set transaction read write"))  # as web/act.py does
            out = conn.execute(
                text("select saley_session_start('sid','mock',:h,12)"), {"h": token_hash("ro")}
            ).scalar_one()
            assert out["ok"] is True
            seen = conn.execute(
                text("select user_short from v_web_session where id_hash = :h"),
                {"h": token_hash("ro")},
            ).scalar_one()
            assert seen == "sid"
            with pytest.raises(DBAPIError, match="permission denied"):
                conn.execute(text("select * from web_session"))
    finally:
        ro.dispose()


# ------------------------------------------------------------------ round trip


def test_the_chain_downgrades_to_0017_and_comes_back(test_db_url: str) -> None:
    probe = create_engine(test_db_url)
    with probe.connect() as conn:
        owner = conn.execute(
            text(
                "select proowner::regrole::name from pg_proc "
                "where proname = 'saley_act' and pronamespace = 'public'::regnamespace limit 1"
            )
        ).scalar_one()
        can = conn.execute(
            text("select current_user = :o or pg_has_role(current_user, :o, 'member')"),
            {"o": owner},
        ).scalar_one()
    if not can:
        probe.dispose()
        pytest.skip(f"the test login cannot act as the owner of saley_act ({owner})")
    config = alembic_config(test_db_url)

    def present() -> list[bool]:
        with probe.connect() as conn:
            return [
                bool(conn.execute(text("select to_regclass(:n)"), {"n": n}).scalar())
                for n in (*NEW_TABLES, *NEW_VIEWS)
            ]

    dispose_engines()
    try:
        with probe.begin() as conn:
            conn.execute(
                text(
                    "insert into decision (kind, choice, user_id, surface, state) "
                    "select 'dismiss', 'x', id, 'discord', 'active' from users "
                    "where short = 'sid'"
                )
            )
        command.downgrade(config, "0017")
        assert not any(present())
        with probe.connect() as conn:
            surfaces = conn.execute(text("select surface from decision")).scalars().all()
            assert surfaces == ["chat"]  # 'discord' rows are remapped, not lost
            for fn in NEW_FUNCTIONS:
                assert conn.execute(text("select to_regproc(:n)"), {"n": fn}).scalar() is None
    finally:
        command.upgrade(config, "head")
        dispose_engines()
        with probe.begin() as conn:
            conn.execute(text("delete from decision"))
    assert all(present())
    with probe.connect() as conn:
        current = conn.execute(text("select version_num from alembic_version")).scalar_one()
    probe.dispose()
    assert current == ScriptDirectory.from_config(config).get_current_head()
