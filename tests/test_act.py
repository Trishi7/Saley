"""The act path: SQL functions, grants and POST /api/act (R2 phases 1 and 3).

Why: the web logs in as `saley_ro`. It has no table grant, so every write goes through four
SECURITY DEFINER functions. These tests guard that narrow door and the D24 / D48 behaviour.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from saley.core.clock import today_ist
from saley.core.settings import get_settings
from saley.web import act as act_module
from saley.web.app import create_app
from tests.conftest import RO_TEST_URL, SignedClient, contact_id, require_ro_role, sql_act, sql_call

ACT_FUNCTIONS = {
    "saley_act",
    "saley_undo",
    "saley_confirm",
    "saley_dismiss",
    "saley_commitment_done",
    "saley_mark_seen",  # R3, D75: per-person seen
    "saley_request_search",  # R3, D77: the "Find people" click
    "saley_session_start",  # R4, D114: sign-in
    "saley_session_end",
    "saley_ask_log",  # R4: the learn log
    "saley_proposal_resolve",  # R4 ticket 7, D129: Do it / Edit / Skip on a card
    "saley_undo_group",  # R5 T3: one undo click for all cells of one Done
    "saley_request_news_run",  # R5 T8, D150: Run news now (admin)
}
H = {"X-Saley-Act": "1"}
LI_DATE = {"role": "li_connected_date", "value": "2026-10-19"}


@pytest.fixture
def client(synced: Session) -> Iterator[TestClient]:
    with SignedClient(create_app()) as c:
        yield c


def post_act(
    client: TestClient,
    person: int,
    cell: dict[str, str] = LI_DATE,
    as_user: str | None = None,
) -> Any:
    body = {"action": "write", "target_ids": [person], "payload": cell}
    headers = {**H, "X-Saley-As": as_user} if as_user else H
    return client.post("/api/act", json=body, headers=headers)


def count(db: Session, table: str, where: str = "true") -> int:
    return db.execute(text(f"select count(*) from {table} where {where}")).scalar_one()


# ------------------------------------------------------------------ R2-G02


def test_ro_role_has_only_act_functions(db: Session) -> None:
    """R2-G02: the web role has no table grant; it may only EXECUTE the saley_* act functions."""
    require_ro_role()
    relations = db.execute(
        text(
            "select c.relname, c.relkind, "
            "has_table_privilege('saley_ro', c.oid, 'SELECT') as sel, "
            "has_table_privilege('saley_ro', c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE, "
            "REFERENCES, TRIGGER') as writes "
            "from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relkind in ('r', 'v', 'm', 'p', 'S')"
        )
    ).all()
    assert not [r.relname for r in relations if r.writes], "saley_ro must not write any relation"
    readable_tables = {r.relname for r in relations if r.sel and r.relkind in ("r", "p")}
    assert readable_tables == {"sheet_snapshot", "sheet_tab_status"}
    new_views = {"v_decision", "v_inbox", "v_commitment", "v_fact_working_on"}
    new_views |= {"v_pending_write", "v_task_inputs"}
    assert new_views <= {r.relname for r in relations if r.sel and r.relkind == "v"}
    assert not db.execute(
        text("select has_schema_privilege('saley_ro', 'public', 'CREATE')")
    ).scalar()

    # Functions: the only definer-rights functions saley_ro may run are the seven act functions.
    definers = db.execute(
        text(
            "select p.proname, p.proconfig, "
            "exists (select 1 from aclexplode(p.proacl) a where a.grantee = 0) as public_exec "
            "from pg_proc p where p.pronamespace = 'public'::regnamespace and p.prosecdef "
            "and has_function_privilege('saley_ro', p.oid, 'EXECUTE')"
        )
    ).all()
    assert {r.proname for r in definers} == ACT_FUNCTIONS
    for row in definers:
        assert not row.public_exec, f"{row.proname} is executable by PUBLIC"
        assert any(c.startswith("search_path=") and "pg_temp" in c for c in row.proconfig or [])


def test_real_web_role_cannot_touch_tables_directly() -> None:
    """Even with the read-only default switched off, the role cannot read or write R2 tables."""
    require_ro_role()
    ro = create_engine(RO_TEST_URL["url"])
    try:
        for statement in (
            "insert into decision(kind) values ('column_write')",
            "update users set role = 'admin'",
            "delete from sync_outbox",
            "select * from decision",
            "select * from sync_outbox",
            "select * from proposal",
        ):
            with ro.connect() as conn, pytest.raises(DBAPIError, match="permission denied"):
                conn.execute(text("set transaction read write"))
                conn.execute(text(statement))
    finally:
        ro.dispose()


def test_act_function_search_path_cannot_be_hijacked(db: Session) -> None:
    """A temp table named `users` never replaces the real one inside a definer function."""
    db.execute(text("create temp table users (short text, role text, id int)"))
    db.execute(text("insert into pg_temp.users values ('ghost', 'admin', 1)"))
    db.execute(text("set local search_path = pg_temp, public"))
    result = db.execute(text("select saley_undo('ghost', 1, 'dashboard')")).scalar_one()
    db.rollback()
    assert result == {"ok": False, "code": "not_allowed"}


def test_migration_0006_downgrades_and_upgrades_again(test_db_url: str, db: Session) -> None:
    """The downgrade is complete: no table, view or function of 0006 stays behind."""
    from alembic import command

    from saley.cli import alembic_config

    db.rollback()
    cfg = alembic_config(test_db_url)
    engine = create_engine(test_db_url)
    try:
        command.downgrade(cfg, "0005")
        with engine.connect() as conn:
            left = conn.execute(
                text(
                    "select relname from pg_class where relnamespace = 'public'::regnamespace "
                    "and relname in ('decision', 'sync_outbox', 'proposal', 'fact', 'v_decision')"
                )
            ).all()
            funcs = conn.execute(
                text("select proname from pg_proc where proname like 'saley_a%'")
            ).all()
        assert left == [] and funcs == []
    finally:
        command.upgrade(cfg, "head")
        engine.dispose()


# ------------------------------------------------------------------ R2-T02, R2-W06, R2-W07


def test_done_writes_with_signature_and_undo(client: TestClient, synced: Session) -> None:
    """R2-T02: Done writes at once with a signature and returns an undo id."""
    alex = contact_id(synced, "Alex Search")
    response = post_act(client, alex, {"role": "li_connected_date", "value": "today"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] and body["already"] is False
    assert body["label"] == "Saved: LI Connected Date"
    assert body["write_id"] > 0 and body["undo_until"]
    today = today_ist().isoformat()
    # The signature: who, when, where, and an undo window of UNDO_MINUTES.
    row = synced.execute(
        text(
            "select d.kind, u.short, d.surface, d.role, d.after, d.state, "
            "extract(epoch from d.undo_until - d.at) as window_s from decision d "
            "join users u on u.id = d.user_id where d.id = :i"
        ),
        {"i": body["write_id"]},
    ).one()
    assert row[:6] == ("column_write", "trishi", "dashboard", "li_connected_date", today, "active")
    window = get_settings().undo_minutes * 60  # D143: 5 minutes by default
    assert window - 10 <= row.window_s <= window + 10
    outbox = synced.execute(text("select state, value from sync_outbox")).one()
    assert tuple(outbox) == ("pending", today)
    # The page shows the pending value at once, from the view the web role can read.
    pending = synced.execute(text("select role, value, open from v_pending_write")).one()
    assert tuple(pending) == ("li_connected_date", today, True)

    undone = client.post(f"/api/undo/{body['write_id']}", headers=H)
    assert undone.status_code == 200
    assert undone.json() == {"ok": True, "write_id": body["write_id"], "cancelled": True}
    assert client.post(f"/api/undo/{body['write_id']}", headers=H).status_code == 409


def test_done_on_a_step_task_logs_and_touches_without_a_sheet_write(
    client: TestClient, synced: Session
) -> None:
    """D49: a task Done is a decision and a dashboard touch. The outbox stays empty."""
    sshubam = contact_id(synced, "Sshubam Verma")
    body = {"action": "task_done", "target_ids": [sshubam], "payload": {"task": "research"}}
    response = client.post("/api/act", json=body, headers=H)
    assert response.status_code == 200
    assert response.json()["label"] == "Saved: Task done"
    assert count(synced, "sync_outbox") == 0
    touch = synced.execute(text("select type, source from touch where source = 'dashboard'")).one()
    assert tuple(touch) == ("research", "dashboard")
    assert synced.execute(text("select kind from decision")).scalar_one() == "task_done"
    client.post(f"/api/undo/{response.json()['write_id']}", headers=H)
    assert count(synced, "touch", "source = 'dashboard'") == 0


def test_second_user_gets_already_done(client: TestClient, synced: Session) -> None:
    """R2-W06: two users on one row within 2 s: the second gets "Already done by X at HH:MM"."""
    alex = contact_id(synced, "Alex Search")
    assert post_act(client, alex).status_code == 200

    second = post_act(client, alex, as_user="sid")

    assert second.status_code == 409
    assert re.fullmatch(r"Already done by Trishi at \d\d:\d\d", second.json()["detail"])
    assert count(synced, "decision") == 1
    # The clock is the row, not a sleep: once the first write is older than the window, it works.
    synced.execute(text("update decision set at = now() - interval '5 seconds'"))
    synced.commit()
    later = {"role": "li_connected_date", "value": "2026-10-20"}
    other = post_act(client, alex, later, as_user="sid")
    assert other.status_code == 200 and other.json()["already"] is False
    assert count(synced, "decision") == 2


def test_cooldown_comes_from_settings(
    client: TestClient, synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACT_COOLDOWN_SECONDS", "0")
    get_settings.cache_clear()
    try:
        alex = contact_id(synced, "Alex Search")
        assert post_act(client, alex).status_code == 200
        later = {"role": "li_connected_date", "value": "2026-10-20"}
        assert post_act(client, alex, later, as_user="sid").status_code == 200
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_same_instruction_twice_is_one_write(client: TestClient, synced: Session) -> None:
    """R2-W07: the same instruction from chat and dashboard is one write (D24, D48)."""
    alex = contact_id(synced, "Alex Search")
    first = post_act(client, alex)
    again = post_act(client, alex)
    assert first.status_code == again.status_code == 200
    assert again.json()["already"] is True
    assert again.json()["write_id"] == first.json()["write_id"]
    assert count(synced, "decision") == 1
    assert count(synced, "sync_outbox") == 1

    # Chat sends the same instruction key through the same function: still one write.
    key = act_module.instruction_key("write", [alex], [LI_DATE], "")
    from_chat = sql_act(synced, "trishi", [LI_DATE], [alex], surface="chat", key=key)
    assert from_chat["items"][0]["already"] is True
    assert count(synced, "decision") == 1


def test_a_then_b_then_a_is_three_writes(client: TestClient, synced: Session) -> None:
    """The same key is "already" only while it is the latest write on the cell."""
    alex = contact_id(synced, "Alex Search")
    a = {"role": "meeting_status", "value": "booked"}
    b = {"role": "meeting_status", "value": "completed"}
    ids = [post_act(client, alex, cell).json()["write_id"] for cell in (a, b, a)]
    assert len(set(ids)) == 3
    assert count(synced, "decision", "undone_at is null") == 3


def test_undo_after_the_window_is_409_with_the_time(client: TestClient, synced: Session) -> None:
    alex = contact_id(synced, "Alex Search")
    write_id = post_act(client, alex).json()["write_id"]
    synced.execute(text("update decision set undo_until = now() - interval '1 minute'"))
    synced.commit()
    response = client.post(f"/api/undo/{write_id}", headers=H)
    assert response.status_code == 409
    assert re.fullmatch(r"Undo window closed at \d\d:\d\d", response.json()["detail"])


def test_unknown_undo_and_viewer_are_refused(client: TestClient, synced: Session) -> None:
    assert client.post("/api/undo/999999", headers=H).status_code == 404
    synced.execute(text("insert into users(short, name, role) values ('vee', 'Vee', 'viewer')"))
    synced.commit()
    try:
        alex = contact_id(synced, "Alex Search")
        refused = post_act(client, alex, as_user="vee")
        assert refused.status_code == 403
        assert refused.json() == {"detail": "Only approvers can do this."}
        assert post_act(client, alex, as_user="nobody").status_code == 403
    finally:
        synced.execute(text("delete from users where short = 'vee'"))
        synced.commit()
    assert count(synced, "decision") == 0


def test_value_checks(client: TestClient, synced: Session) -> None:
    alex = contact_id(synced, "Alex Search")
    for cell in (
        {"role": "li_connected_date", "value": "19/10/2026"},
        {"role": "li_connected_date", "value": ""},
        {"role": "meeting_status", "value": "a\nb"},
        {"role": "meeting_status", "value": "x" * 201},
        {"role": "first_contact", "value": "maybe"},
    ):
        assert post_act(client, alex, cell).status_code == 422, cell
    ok = post_act(client, alex, {"role": "first_contact", "value": "true"})
    assert ok.status_code == 200
    assert synced.execute(text("select after from decision")).scalar_one() == "TRUE"


def test_several_cells_and_people_in_one_call(client: TestClient, synced: Session) -> None:
    """The R05 pick: First Contact, Type and Date for two people in one click."""
    people = [contact_id(synced, n) for n in ("Alex Search", "Ro One")]
    cells = [
        {"role": "first_contact", "value": "TRUE"},
        {"role": "first_contact_type", "value": "LinkedIn"},
        {"role": "first_contact_date", "value": "today"},
    ]
    body = {"action": "write", "target_ids": people, "payload": {"cells": cells}}
    response = client.post("/api/act", json=body, headers=H)
    assert response.status_code == 200
    assert len(response.json()["items"]) == 6
    assert count(synced, "sync_outbox") == 6
    assert client.post("/api/act", json=body, headers=H).json()["already"] is True


def test_proposal_id_marks_the_card_acted(client: TestClient, synced: Session) -> None:
    alex = contact_id(synced, "Alex Search")
    synced.execute(text("insert into proposal(type, card_key) values ('meeting_status', 'k1')"))
    synced.commit()
    pid = synced.execute(text("select id from proposal")).scalar_one()
    body = {"action": "write", "target_ids": [alex], "payload": {**LI_DATE, "proposal_id": pid}}
    assert client.post("/api/act", json=body, headers=H).status_code == 200
    assert synced.execute(text("select state from proposal")).scalar_one() == "acted"
    assert synced.execute(text("select proposal_id from decision")).scalar_one() == pid


def test_sql_function_rejects_bad_requests(synced: Session) -> None:
    alex = contact_id(synced, "Alex Search")
    cell = [{"role": "meeting_status", "value": "booked"}]
    assert sql_act(synced, "sid", cell, [alex], surface="email")["code"] == "bad_request"
    assert sql_act(synced, "sid", cell, [999999])["code"] == "no_target"
    assert sql_act(synced, "sid", cell, [alex], action="delete")["code"] == "bad_request"
    assert sql_act(synced, "sid", cell, [alex], key="short")["code"] == "bad_request"
    assert sql_act(synced, "sid", cell + cell, [alex])["code"] == "bad_request"
    assert sql_act(synced, "sid", [], [alex], task="nope")["code"] == "bad_request"
    assert sql_act(synced, "sid", cell, [alex], action="email_write")["code"] == "locked"
    assert count(synced, "decision") == 0


# ------------------------------------------------------------------ confirm and dismiss


def test_confirm_a_proposed_decision(client: TestClient, synced: Session) -> None:
    synced.execute(
        text(
            "insert into decision(kind, choice, state, reason) "
            "values ('strategy_decision', 'D45', 'proposed', 'test')"
        )
    )
    synced.commit()
    did = synced.execute(text("select id from decision")).scalar_one()
    response = client.post(f"/api/decision/{did}/confirm", headers={**H, "X-Saley-As": "kushal"})
    assert response.json() == {"ok": True, "decision_id": did, "already": False}
    row = synced.execute(
        text(
            "select d.state, u.short, d.confirmed_surface, d.confirmed_at is not null "
            "from decision d join users u on u.id = d.confirmed_by"
        )
    ).one()
    assert tuple(row) == ("active", "kushal", "dashboard", True)
    assert client.post(f"/api/decision/{did}/confirm", headers=H).json()["already"] is True
    assert client.post("/api/decision/99999/confirm", headers=H).status_code == 404


def test_dismiss_a_card_once(client: TestClient, synced: Session) -> None:
    synced.execute(text("insert into proposal(type, card_key) values ('meeting_status', 'k2')"))
    synced.commit()
    pid = synced.execute(text("select id from proposal")).scalar_one()
    first = client.post(f"/api/proposal/{pid}/dismiss", headers=H)
    assert first.json() == {"ok": True, "proposal_id": pid, "already": False}
    assert client.post(f"/api/proposal/{pid}/dismiss", headers=H).json()["already"] is True
    assert synced.execute(text("select state from proposal")).scalar_one() == "dismissed"
    assert tuple(synced.execute(text("select kind, choice from decision")).one()) == (
        "dismiss",
        "dismiss",
    )
    assert client.post("/api/proposal/99999/dismiss", headers=H).status_code == 404
    synced.execute(text("update proposal set state = 'acted'"))
    synced.commit()
    assert client.post(f"/api/proposal/{pid}/dismiss", headers=H).status_code == 409


# ------------------------------------------------------------------ the real web role


def test_act_works_as_the_real_web_role(synced: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """The web role has no table grant, yet the act path works through the SQL functions."""
    from saley.core.db import dispose_engines, ro_engine

    require_ro_role()
    alex = contact_id(synced, "Alex Search")
    monkeypatch.setenv("DATABASE_URL_RO", RO_TEST_URL["url"])
    get_settings.cache_clear()
    dispose_engines()
    try:
        with ro_engine().connect() as conn:
            assert conn.execute(text("select current_user")).scalar_one() == "saley_ro"
        with SignedClient(create_app()) as c:
            response = post_act(c, alex)
            assert response.status_code == 200, response.text
            assert c.post(f"/api/undo/{response.json()['write_id']}", headers=H).status_code == 200
    finally:
        get_settings.cache_clear()
        dispose_engines()
    assert count(synced, "decision") == 1


# ------------------------------------------------------------------ R2-D01 the Decisions view


def test_decisions_view_filters(client: TestClient, synced: Session) -> None:
    """R2-D01: the Decisions view lists decision rows with person, surface, undo, state filters."""
    alex, ro_one = contact_id(synced, "Alex Search"), contact_id(synced, "Ro One")
    first = post_act(client, alex).json()["write_id"]  # trishi, dashboard, window open
    sql_act(
        synced,
        "sid",
        [{"role": "meeting_status", "value": "booked"}],
        [ro_one],
        surface="chat",
    )
    closed = post_act(client, alex, {"role": "first_contact", "value": "TRUE"}, as_user="kushal")
    assert closed.status_code == 200
    synced.execute(
        text("update decision set undo_until = now() - interval '1 minute' where id = :i"),
        {"i": closed.json()["write_id"]},
    )
    synced.execute(
        text(
            "insert into decision(kind, choice, reason, state) "
            "values ('strategy_decision', 'D88', 'Waiting for a confirm', 'proposed')"
        )
    )
    synced.commit()
    undone = post_act(client, ro_one, {"role": "first_contact", "value": "TRUE"})
    client.post(f"/api/undo/{undone.json()['write_id']}", headers=H)

    def ids(query: str) -> list[dict]:
        response = client.get(f"/api/decisions{query}")
        assert response.status_code == 200, response.text
        return response.json()["items"]

    everything = client.get("/api/decisions").json()
    assert everything["total"] == len(everything["items"]) == 5
    assert [i["person"] for i in ids("?person=sid")] == ["Ro One"]
    assert {i["user_short"] for i in ids("?surface=chat")} == {"sid"}
    assert {i["id"] for i in ids("?undo=undone")} == {undone.json()["write_id"]}
    assert first in {i["id"] for i in ids("?undo=open")}
    assert {i["id"] for i in ids("?undo=closed")} >= {closed.json()["write_id"]}
    proposed = ids("?state=proposed")
    assert [(i["label"], i["text"], i["can_confirm"]) for i in proposed] == [
        ("D88", "Waiting for a confirm", True)
    ]
    row = next(i for i in ids("?person=trishi&surface=dashboard&undo=open") if i["id"] == first)
    assert (row["what"], row["value"], row["can_undo"], row["can_confirm"]) == (
        "LI Connected Date",
        "2026-10-19",
        True,
        False,
    )
    # Filters add up, and paging cuts the list but keeps the total.
    assert ids("?person=sid&surface=dashboard") == []
    page = client.get("/api/decisions?limit=2&offset=1").json()
    assert len(page["items"]) == 2 and page["total"] == 5
    for bad in ("?surface=email", "?undo=late", "?state=none", "?person=A1", "?limit=0"):
        assert client.get(f"/api/decisions{bad}").status_code == 422, bad


# ------------------------------------------------------------------ Fix B: commitment Done


def _commitment(db: Session, owner: str = "trishi", state: str = "open") -> int:
    """A commitment owned by the dev user, who is the signed-in client (D155: owner only)."""
    cid = db.execute(
        text(
            "insert into commitment(owner, text, due, state) "
            "select id, 'I will send the deck.', :d, :s from users where short = :o returning id"
        ),
        {"d": today_ist(), "s": state, "o": owner},
    ).scalar_one()
    db.execute(
        text(
            "insert into reminder(for_user, text, fire_at, commitment_id, kind, state) "
            "select id, 'Due today: x', now(), :c, 'due_day', 'pending' "
            "from users where short = :o"
        ),
        {"c": cid, "o": owner},
    )
    db.commit()
    return cid


def test_commitment_done_closes_it_and_skips_its_reminders(
    client: TestClient, synced: Session
) -> None:
    cid = _commitment(synced)
    first = client.post(f"/api/commitment/{cid}/done", headers=H)
    assert first.status_code == 200, first.text
    body = first.json()
    assert (body["ok"], body["commitment_id"], body["already"]) == (True, cid, False)
    assert body["write_id"] > 0 and body["undo_until"]  # M11: signed, with an undo window
    assert synced.execute(text("select state from commitment")).scalar_one() == "done"
    assert synced.execute(text("select state from reminder")).scalar_one() == "skipped"
    again = client.post(f"/api/commitment/{cid}/done", headers=H)
    assert again.json()["already"] is True
    listed = client.get("/api/commitments?state=done").json()["items"]
    assert [i["id"] for i in listed] == [cid]


def test_commitment_done_refusals(client: TestClient, synced: Session) -> None:
    cid = _commitment(synced, state="dropped")
    assert client.post(f"/api/commitment/{cid}/done", headers=H).status_code == 409
    assert client.post("/api/commitment/99999/done", headers=H).status_code == 404
    assert client.post(f"/api/commitment/{cid}/done").status_code == 403  # no header
    ghost = {**H, "X-Saley-As": "nobody"}
    assert client.post(f"/api/commitment/{cid}/done", headers=ghost).status_code == 403
    for bad in ("0", "abc", "-1", "1;drop"):
        assert client.post(f"/api/commitment/{bad}/done", headers=H).status_code == 422
    for method in ("get", "put", "patch", "delete"):
        assert getattr(client, method)(f"/api/commitment/{cid}/done").status_code in (404, 405)
    cross = {**H, "Origin": "https://evil.example"}
    assert client.post(f"/api/commitment/{cid}/done", headers=cross).status_code == 403
    assert synced.execute(text("select state from commitment")).scalar_one() == "dropped"


def test_commitment_done_function_checks_the_role(synced: Session) -> None:
    cid = _commitment(synced)
    synced.execute(text("update users set role = 'viewer' where short = 'sid'"))
    synced.commit()
    try:
        denied = sql_call(synced, "select saley_commitment_done('sid', :i)", i=cid)
    finally:  # `users` is not emptied between tests
        synced.execute(text("update users set role = 'approver' where short = 'sid'"))
        synced.commit()
    assert denied == {"ok": False, "code": "not_allowed"}
    assert synced.execute(text("select state from commitment")).scalar_one() == "open"
