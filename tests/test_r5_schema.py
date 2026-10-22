"""R5 ticket T3: the column vocabulary view (migration 0023, D153).

No network. The web role (`saley_ro`) reads views only, so it gets this view and no table.
"""

from __future__ import annotations

import json

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from saley.cli import alembic_config
from saley.core.db import dispose_engines
from saley.sales.vocab import column_vocab
from tests.conftest import RO_TEST_URL, contact_id, require_ro_role, sql_act, sql_call

ROLES = {
    "meeting_status",
    "first_contact",
    "first_contact_type",
    "first_contact_date",
    "li_connected_date",
    "meeting_date",
}


def test_vocab_view_counts_values_of_the_done_roles_only(synced: Session) -> None:
    rows = synced.execute(text("select role, value, n from v_column_vocab")).all()
    assert rows and {r[0] for r in rows} <= ROLES
    assert all(r[2] >= 1 for r in rows)
    status = {r[1]: r[2] for r in rows if r[0] == "meeting_status"}
    assert status == {"completed": 1}
    types = {r[1]: r[2] for r in rows if r[0] == "first_contact_type"}
    assert types == {"LinkedIn": 1}
    # Names, emails and notes never reach the view.
    assert not {"name", "email", "next_steps"} & {r[0] for r in rows}


def test_vocab_view_skips_blank_and_removed_people(synced: Session) -> None:
    synced.execute(
        text("update contact set cells = cells || cast(:p as jsonb) where name = 'Ro One'"),
        {"p": json.dumps({"meeting_status": "  ", "first_contact_type": None})},
    )
    synced.execute(text("update contact set removed_at = now() where name = 'David Gamez'"))
    synced.commit()
    assert column_vocab(synced.connection(), "meeting_status") == {}
    synced.rollback()


def test_vocab_view_is_readable_by_the_web_role_and_the_table_is_not(synced: Session) -> None:
    require_ro_role()
    engine = create_engine(RO_TEST_URL["url"])
    try:
        with engine.connect() as conn:
            assert conn.execute(text("select count(*) from v_column_vocab")).scalar_one() > 0
        granted = synced.execute(
            text("select has_table_privilege('saley_ro', 'contact', 'SELECT')")
        ).scalar_one()
        assert granted is False
    finally:
        engine.dispose()


def _promise(db: Session, owner: str) -> int:
    cid = db.execute(
        text(
            "insert into commitment(owner, text, due) "
            "select id, 'I will send the deck.', current_date from users where short = :o "
            "returning id"
        ),
        {"o": owner},
    ).scalar_one()
    db.commit()
    return int(cid)


def test_commitment_done_function_refuses_non_owner(synced: Session) -> None:
    """R5-C02 (D155): SQL refuses a non-owner (an admin too) and the undo of a non-signer."""
    cid = _promise(synced, "vaishnavi")
    name = synced.execute(text("select name from users where short = 'vaishnavi'")).scalar_one()
    call = "select saley_commitment_done(:u, :i, 'dashboard', 5)"
    for actor in ("sid", "kushal", "trishi"):  # trishi is an admin: no exception
        got = sql_call(synced, call, u=actor, i=cid)
        assert got == {"ok": False, "code": "not_owner", "owner_name": name}, actor
    assert synced.execute(text("select state from commitment")).scalar_one() == "open"
    assert synced.execute(text("select count(*) from decision")).scalar_one() == 0

    done = sql_call(synced, call, u="vaishnavi", i=cid)
    assert done["ok"] is True and done["already"] is False
    # A second person gets the refusal also for a promise that is already done.
    assert sql_call(synced, call, u="sid", i=cid)["code"] == "not_owner"
    for fn in ("saley_undo", "saley_undo_group"):
        refused = sql_call(synced, f"select {fn}('sid', :w, 'dashboard')", w=done["write_id"])
        assert refused == {"ok": False, "code": "not_signer"}, fn
    assert synced.execute(text("select state from commitment")).scalar_one() == "done"
    ok = sql_call(synced, "select saley_undo('vaishnavi', :w, 'dashboard')", w=done["write_id"])
    assert ok["ok"] is True
    assert synced.execute(text("select state from commitment")).scalar_one() == "open"


def test_email_write_is_refused_when_the_cell_is_filled(synced: Session) -> None:
    """T4 (D154): `saley_act` writes an email only into a blank cell; no decision row otherwise."""
    cid = contact_id(synced, "Alex Search")
    cell = [{"role": "email", "value": "new@example.com"}]
    synced.execute(text("update contact set email = 'old@example.com' where id = :i"), {"i": cid})
    synced.commit()
    refused = sql_act(synced, "vaishnavi", cell, [cid], action="email_write", allow_email=True)
    assert refused == {"ok": False, "code": "email_present"}
    assert synced.execute(text("select count(*) from decision")).scalar_one() == 0

    synced.execute(text("update contact set email = '  ' where id = :i"), {"i": cid})
    synced.commit()
    ok = sql_act(synced, "vaishnavi", cell, [cid], action="email_write", allow_email=True)
    assert ok["ok"] is True
    assert synced.execute(text("select count(*) from decision")).scalar_one() == 1


def _add_email(db: Session, cid: int, value: str = "new@example.com") -> dict:
    cell = [{"role": "email", "value": value}]
    return sql_act(db, "vaishnavi", cell, [cid], action="email_write", allow_email=True)


def test_second_email_add_is_refused_while_the_first_is_pending(synced: Session) -> None:
    """T4 review: the sheet value is stale until the next sync, so a live earlier add counts."""
    cid = contact_id(synced, "Alex Search")
    first = _add_email(synced, cid)
    assert first["ok"] is True
    again = _add_email(synced, cid, "other@example.com")
    assert again == {"ok": False, "code": "email_present"}
    count = "select count(*) from decision where kind = 'column_write' and role = 'email'"
    assert synced.execute(text(count)).scalar_one() == 1


def test_email_add_is_allowed_again_after_the_first_is_undone(synced: Session) -> None:
    cid = contact_id(synced, "Alex Search")
    first = _add_email(synced, cid)
    undone = sql_call(
        synced, "select saley_undo('vaishnavi', :w, 'dashboard')", w=first["items"][0]["write_id"]
    )
    assert undone["ok"] is True
    assert _add_email(synced, cid, "other@example.com")["ok"] is True


def test_email_add_is_allowed_again_after_the_first_failed(synced: Session) -> None:
    cid = contact_id(synced, "Alex Search")
    _add_email(synced, cid)
    synced.execute(text("update sync_outbox set state = 'failed' where role = 'email'"))
    synced.commit()
    assert _add_email(synced, cid, "other@example.com")["ok"] is True


def test_bad_email_shape_is_refused_in_sql(synced: Session) -> None:
    cid = contact_id(synced, "Alex Search")
    bad = ["a@b", "a@.com", "a b@c.com", "a@b..com", "a,b@c.com", "a@b.c;d@e.f", "a​@b.com"]
    bad += ["a@b.com‮", "é@b.com", "a@" + "b" * 200 + ".com"]
    for value in bad:
        assert _add_email(synced, cid, value)["code"] in ("bad_email", "bad_value"), value
    assert synced.execute(text("select count(*) from decision")).scalar_one() == 0


def test_two_concurrent_email_adds_write_one_decision(synced: Session, engine) -> None:
    """T4 review: the guard runs after the cell lock, so the second caller sees the first."""
    import json
    import threading

    cid = contact_id(synced, "Alex Search")
    call = text(
        "select saley_act('vaishnavi','dashboard','email_write',cast(:t as jsonb),"
        "cast(:p as jsonb),:k,2,10,true)"
    )

    def params(value: str, key: str) -> dict:
        p = {"cells": [{"role": "email", "value": value}]}
        return {"t": json.dumps([cid]), "p": json.dumps(p), "k": key}

    out: list[dict] = []

    def second() -> None:
        with engine.connect() as conn:
            out.append(conn.execute(call, params("two@example.com", "k2" * 8)).scalar_one())
            conn.commit()

    with engine.connect() as first:
        got = first.execute(call, params("one@example.com", "k1" * 8)).scalar_one()
        racer = threading.Thread(target=second)
        racer.start()
        racer.join(0.5)
        assert racer.is_alive(), out  # blocked on the cell lock
        first.commit()
        racer.join(10)
    assert got["ok"] is True
    assert out == [{"ok": False, "code": "email_present"}]
    count = "select count(*) from decision where kind = 'column_write' and role = 'email'"
    assert synced.execute(text(count)).scalar_one() == 1


# ------------------------------------------------------------------ 0024: the story topic (T7)

TOPICS = [
    "Hiring",
    "Funding",
    "Product launch",
    "Research",
    "Partnership",
    "Opportunity",
    "Event",
    "Other",
]


def test_topic_column_check_and_index(db: Session) -> None:
    """0024 (D156): a nullable `topic` with a check and an index. 0025 adds Opportunity (D239)."""
    col = db.execute(
        text(
            "select data_type, is_nullable from information_schema.columns "
            "where table_name = 'news_stories' and column_name = 'topic'"
        )
    ).one()
    assert tuple(col) == ("text", "YES")
    check = db.execute(
        text(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conname = 'ck_news_stories_topic'"
        )
    ).scalar_one()
    assert all(f"'{t}'" in check for t in TOPICS) and check.count("'") == 2 * len(TOPICS)
    index = db.execute(
        text("select indexdef from pg_indexes where indexname = 'ix_news_stories_topic'")
    ).scalar_one()
    assert "(topic)" in index
    for letter in ("F", "other", ""):
        with pytest.raises(IntegrityError):
            db.execute(
                text(
                    "insert into news_stories (title, url, url_hash, fingerprint, topic) "
                    "values ('t', 'https://x.test/1', 'h1', 'f1', :t)"
                ),
                {"t": letter},
            )
        db.rollback()


def test_topic_rides_the_signal_views_and_the_web_role_reads_them(db: Session) -> None:
    require_ro_role()
    from tests.test_signals import add_story

    add_story(db, "A funding story", topic="Funding")
    engine = create_engine(RO_TEST_URL["url"])
    try:
        with engine.connect() as conn:
            for view in ("v_signal_all", "v_signal_linked", "v_needs_look"):
                assert conn.execute(text(f"select count(*) from {view}")).scalar_one() >= 0
            topics = conn.execute(text("select topic from v_signal_all")).scalars().all()
            assert topics == ["Funding"]
            cols = (
                conn.execute(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_name = 'v_signal_linked' order by ordinal_position"
                    )
                )
                .scalars()
                .all()
            )
            assert cols[-1] == "topic"  # new columns go last: the old order is kept
        granted = db.execute(
            text("select has_table_privilege('saley_ro', 'news_stories', 'SELECT')")
        ).scalar_one()
        assert granted is False
    finally:
        engine.dispose()


def test_0024_downgrades_to_0023_and_comes_back(test_db_url: str) -> None:
    """The topic part of 0024 leaves no table column, check or index behind.

    The Signals views keep a constant null `topic` column: other views read them, and Postgres
    cannot remove a view column with `create or replace`.
    """
    probe = create_engine(test_db_url)
    with probe.connect() as conn:
        owner = conn.execute(
            text(
                "select relowner::regrole::name from pg_class where oid = 'news_stories'::regclass"
            )
        ).scalar_one()
        can = conn.execute(
            text("select current_user = :o or pg_has_role(current_user, :o, 'member')"),
            {"o": owner},
        ).scalar_one()
    if not can:
        probe.dispose()
        pytest.skip(f"the test login cannot act as the owner of news_stories ({owner})")
    config = alembic_config(test_db_url)

    def shape() -> dict[str, bool]:
        with probe.connect() as conn:
            one = lambda q: bool(conn.execute(text(q)).scalar())  # noqa: E731
            return {
                "column": one(
                    "select exists (select 1 from information_schema.columns "
                    "where table_name = 'news_stories' and column_name = 'topic')"
                ),
                "check": one(
                    "select exists (select 1 from pg_constraint "
                    "where conname = 'ck_news_stories_topic')"
                ),
                "index": one("select to_regclass('ix_news_stories_topic') is not null"),
                "view": one(
                    "select exists (select 1 from information_schema.columns "
                    "where table_name = 'v_signal_all' and column_name = 'topic')"
                ),
                "dependents": one(
                    "select to_regclass('v_needs_look') is not null "
                    "and to_regclass('v_suggest_newco') is not null"
                ),
                "request_table": one("select to_regclass('news_run_request') is not null"),
                "request_view": one("select to_regclass('v_news_run_request') is not null"),
                "request_function": one(
                    "select exists (select 1 from pg_proc where proname = 'saley_request_news_run')"
                ),
            }

    assert all(shape().values())
    dispose_engines()
    try:
        command.downgrade(config, "0023")
        down = shape()
        assert down == {
            "column": False,
            "check": False,
            "index": False,
            "view": True,  # a constant null, not the table column
            "dependents": True,
            "request_table": False,  # T8: the request table, view and function go too
            "request_view": False,
            "request_function": False,
        }
    finally:
        command.upgrade(config, "head")
        dispose_engines()
    assert all(shape().values())
    with probe.connect() as conn:
        current = conn.execute(text("select version_num from alembic_version")).scalar_one()
    probe.dispose()
    assert current == ScriptDirectory.from_config(config).get_current_head()


# ------------------------------------------------------------------ 0024: Run news now (T8)

ASK = "select saley_request_news_run(:u)"


def _age_requests(db: Session, minutes: int) -> None:
    """Move every request `minutes` into the past (the clock is the database's)."""
    db.execute(
        text(
            "update news_run_request set requested_at = requested_at - make_interval(mins => :m), "
            "started_at = started_at - make_interval(mins => :m)"
        ),
        {"m": minutes},
    )
    db.commit()


def test_news_request_function_is_admin_only(db: Session) -> None:
    for who in ("sid", "vaishnavi", "kushal", "nobody", "", "trishi; drop table users"):
        assert sql_call(db, ASK, u=who) == {"ok": False, "code": "not_allowed"}, who
    assert sql_call(db, ASK, u=None) == {"ok": False, "code": "not_allowed"}
    assert db.execute(text("select count(*) from news_run_request")).scalar_one() == 0
    got = sql_call(db, ASK, u="trishi")
    assert got["ok"] is True and got["state"] == "queued"
    row = db.execute(
        text(
            "select u.short, r.state from news_run_request r join users u on u.id = r.requested_by"
        )
    ).one()
    assert tuple(row) == ("trishi", "queued")


def test_news_request_one_open_then_cooldown(db: Session) -> None:
    first = sql_call(db, ASK, u="trishi")
    again = sql_call(db, ASK, u="trishi")
    assert again == {
        "ok": False,
        "code": "busy",
        "request_id": first["request_id"],
        "state": "queued",
    }
    db.execute(text("update news_run_request set state = 'done', finished_at = now()"))
    db.commit()
    wait = sql_call(db, ASK, u="trishi")
    assert wait["code"] == "cooldown" and 0 < wait["wait_seconds"] <= 600
    _age_requests(db, 11)
    assert sql_call(db, ASK, u="trishi")["ok"] is True
    assert db.execute(text("select count(*) from news_run_request")).scalar_one() == 2


def test_news_request_unique_index_allows_one_open_row(db: Session) -> None:
    uid = db.execute(text("select id from users where short = 'trishi'")).scalar_one()
    insert = text("insert into news_run_request (requested_by, state) values (:u, :s)")
    db.execute(insert, {"u": uid, "s": "queued"})
    db.commit()
    for state in ("queued", "running"):
        with pytest.raises(IntegrityError):
            db.execute(insert, {"u": uid, "s": state})
        db.rollback()
    with pytest.raises(IntegrityError):  # the state list is closed
        db.execute(insert, {"u": uid, "s": "paused"})
    db.rollback()
    for state in ("done", "failed", "done"):  # closed rows are not limited
        db.execute(insert, {"u": uid, "s": state})
    db.commit()


def test_news_request_open_rows_nobody_closed_are_failed_after_30_minutes(db: Session) -> None:
    """A lost worker must not block the button for ever."""
    sql_call(db, ASK, u="trishi")
    _age_requests(db, 31)
    assert sql_call(db, ASK, u="trishi")["ok"] is True  # the old queued row was closed first
    old = db.execute(text("select state, note from news_run_request order by id")).all()
    assert [tuple(r) for r in old] == [("failed", "no worker took it"), ("queued", None)]
    db.execute(
        text("update news_run_request set state = 'running', started_at = now() where id = 2")
    )
    db.commit()
    _age_requests(db, 31)
    assert sql_call(db, ASK, u="trishi")["ok"] is True
    notes = db.execute(text("select note from news_run_request where id = 2")).scalar_one()
    assert notes == "worker stopped during the run"


def test_news_request_web_role_reads_the_view_and_runs_the_function_only(db: Session) -> None:
    require_ro_role()
    engine = create_engine(RO_TEST_URL["url"])
    try:
        with engine.connect() as conn:
            assert conn.execute(text("select count(*) from v_news_run_request")).scalar_one() == 0
            with pytest.raises(Exception, match="permission denied"):
                conn.execute(text("select count(*) from news_run_request"))
        for fn_args in ("saley_request_news_run(text)",):
            ok = db.execute(
                text("select has_function_privilege('saley_ro', :f, 'EXECUTE')"), {"f": fn_args}
            ).scalar_one()
            assert ok is True
        secdef = db.execute(
            text(
                "select prosecdef, proconfig::text from pg_proc "
                "where proname = 'saley_request_news_run'"
            )
        ).one()
        assert secdef[0] is True and "search_path=public, pg_temp" in secdef[1]
        public = db.execute(
            text(
                "select has_function_privilege('public', 'saley_request_news_run(text)', 'EXECUTE')"
            )
        ).scalar_one()
        assert public is False
    finally:
        engine.dispose()
