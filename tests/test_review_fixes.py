"""R2 review fixes (pipeline stage 5): each finding has a test that failed before the fix.

H1 formula injection, H4 downgrades, H5 failed writes shown, H6 undo rules, M1 busy lock,
M2 body size, M4 contact mirror, M7 dev host, M8 indexes, M11 commitment signature, and the
LOW items. Every sheet write goes to a FakeSheet; nothing here calls Google.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from saley.core.clock import now_ist, today_ist
from saley.core.settings import get_settings
from saley.sales import writer
from saley.sales.outbox import mirror_value, push_pending
from saley.sales.writer import FakeSheet, FormulaRefused, as_literal, refuse_formula
from saley.web import act as act_module
from saley.web import words as w
from saley.web.app import create_app
from saley.web.guards import MAX_BODY_BYTES
from tests.conftest import POC_ROLE_ORDER, SignedClient, contact_id, sql_act, sql_call

POC = "Outreach PoCs"
H = {"X-Saley-Act": "1"}
FORMULAS = ["=1+1", "+1", "-1", "@SUM(A1)", " =x", '=HYPERLINK("http://x")']


@pytest.fixture
def client(synced: Session) -> Iterator[TestClient]:
    with SignedClient(create_app()) as c:
        yield c


@pytest.fixture
def setting(monkeypatch: pytest.MonkeyPatch):
    def apply(name: str, value: str) -> None:
        monkeypatch.setenv(name, value)
        get_settings.cache_clear()

    yield apply
    monkeypatch.undo()
    get_settings.cache_clear()


def col(role: str) -> int:
    return POC_ROLE_ORDER.index(role)


def count(db: Session, table: str, where: str = "true") -> int:
    return db.execute(text(f"select count(*) from {table} where {where}")).scalar_one()


def cell_write(db: Session, user: str, name: str, role: str, value: str, **kw: Any) -> dict:
    """One write through the SQL function (what the chat bot and the web call)."""
    result = sql_act(db, user, [{"role": role, "value": value}], [contact_id(db, name)], **kw)
    assert result["ok"], result
    return result["items"][0]


def undo(db: Session, user: str, write_id: int) -> dict[str, Any]:
    return sql_call(db, "select saley_undo(:u, :id, 'dashboard')", u=user, id=write_id)


def post_act(client: TestClient, person: int, role: str, value: str, who: str | None = None) -> Any:
    body = {"action": "write", "target_ids": [person], "payload": {"role": role, "value": value}}
    headers = {**H, "X-Saley-As": who} if who else H
    return client.post("/api/act", json=body, headers=headers)


# ------------------------------------------------------------------ H1 formula injection


@pytest.mark.parametrize("role", ["meeting_status", "first_contact_type", "sid_li_added"])
@pytest.mark.parametrize("value", FORMULAS)
def test_web_refuses_a_formula_value(
    client: TestClient, synced: Session, role: str, value: str
) -> None:
    alex = contact_id(synced, "Alex Search")
    response = post_act(client, alex, role, value)
    assert response.status_code == 422
    assert response.json() == {"detail": "That value is not allowed."}
    assert count(synced, "decision") == 0 and count(synced, "sync_outbox") == 0


def test_web_refuses_a_formula_email_when_email_writes_are_on(
    client: TestClient, synced: Session, setting: Any
) -> None:
    setting("EMAIL_WRITE_ALLOWED", "true")
    ro_one = contact_id(synced, "Ro One")
    body = {
        "action": "email_write",
        "target_ids": [ro_one],
        "payload": {"role": "email", "value": "=cmd@evil.example"},
    }
    assert client.post("/api/act", json=body, headers=H).status_code == 422
    body["payload"]["value"] = "ok@example.com"  # type: ignore[index]
    assert client.post("/api/act", json=body, headers=H).status_code == 200
    assert count(synced, "decision") == 1


@pytest.mark.parametrize("value", FORMULAS)
def test_sql_function_refuses_a_formula_value(synced: Session, value: str) -> None:
    alex = contact_id(synced, "Alex Search")
    for role in ("meeting_status", "first_contact_type", "sid_li_added"):
        result = sql_act(synced, "sid", [{"role": role, "value": value}], [alex])
        assert result == {"ok": False, "code": "bad_value"}, (role, value)
    assert count(synced, "decision") == 0


def test_sql_function_checks_date_and_first_contact_shapes(synced: Session) -> None:
    """Defence in depth: the SQL door is as strict as the web layer."""
    alex = contact_id(synced, "Alex Search")
    for role, value in (
        ("li_connected_date", "19/10/2026"),
        ("li_connected_date", "today"),
        ("meeting_date", "2026-13-01"),
        ("first_contact_date", "2026-10-32"),
        ("first_contact", "maybe"),
    ):
        got = sql_act(synced, "sid", [{"role": role, "value": value}], [alex])
        assert got == {"ok": False, "code": "bad_value"}, (role, value)
    assert count(synced, "decision") == 0
    for role, value in (
        ("li_connected_date", "2026-10-19"),
        ("first_contact", "vr"),
        ("first_contact", "TRUE"),
        ("meeting_status", "booked"),
    ):
        assert sql_act(synced, "sid", [{"role": role, "value": value}], [alex])["ok"], role


def test_locked_roles_stay_locked_before_the_value_rules(synced: Session) -> None:
    alex = contact_id(synced, "Alex Search")
    got = sql_act(synced, "sid", [{"role": "company", "value": "=x"}], [alex])
    assert got["code"] == "locked"


def _force_outbox(db: Session, name: str, role: str, value: str) -> int:
    """Put a row in the outbox that skipped the web and SQL checks (a bug or a bad insert)."""
    row_key = db.execute(
        text("select sheet_row_key from contact where name = :n"), {"n": name}
    ).scalar_one()
    decision = db.execute(
        text(
            "insert into decision(kind, user_id, surface, tab, row_key, role, after, "
            "instruction_key, undo_until) values ('column_write', "
            "(select id from users where short = 'sid'), 'dashboard', 'poc', :k, :r, :v, "
            "md5(random()::text) || md5(random()::text), now() + interval '10 minutes') "
            "returning id"
        ),
        {"k": row_key, "r": role, "v": value},
    ).scalar_one()
    outbox = db.execute(
        text(
            "insert into sync_outbox(decision_id, tab, row_key, role, value, next_at) "
            "values (:d, 'poc', :k, :r, :v, now()) returning id"
        ),
        {"d": decision, "k": row_key, "r": role, "v": value},
    ).scalar_one()
    db.commit()
    return outbox


@pytest.mark.parametrize("value", ["=1+1", "+44", "-1", "@a", "  =x"])
def test_worker_never_sends_a_formula_value(
    synced: Session, fake_sheet: FakeSheet, value: str
) -> None:
    outbox = _force_outbox(synced, "Alex Search", "meeting_status", value)
    stats = push_pending(fake_sheet, now=now_ist())
    assert (stats.failed, stats.done) == (1, 0)
    row = synced.execute(
        text("select state, error from sync_outbox where id = :i"), {"i": outbox}
    ).one()
    assert row.state == "failed" and "formula" in row.error
    assert not [c for c in fake_sheet.calls if c[0] in ("write_cell", "write_row")]


def test_sheet_writers_refuse_a_formula_text() -> None:
    with pytest.raises(FormulaRefused):
        refuse_formula("=1+1")
    refuse_formula("2026-10-19")
    refuse_formula("")
    fake = FakeSheet()
    fake.add_tab("T", rows=3, cols=3)
    with pytest.raises(FormulaRefused):
        fake.write_cell("T", 1, 0, "+1")
    with pytest.raises(FormulaRefused):
        fake.write_row("T", 2, ["ok", "@x"])
    assert fake.calls == []  # refused before any call is recorded

    class Ws:
        def __init__(self) -> None:
            self.updates = 0

        def update(self, *args: Any, **kwargs: Any) -> None:
            self.updates += 1

    class Book:
        def __init__(self) -> None:
            self.ws = Ws()

        def worksheet(self, title: str) -> Ws:
            return self.ws

    book = Book()
    real = writer.GspreadSheet(book, "SALEY_TEST")
    with pytest.raises(FormulaRefused):
        real.write_cell("T", 1, 0, "-5")
    with pytest.raises(FormulaRefused):
        real.write_row("T", 1, ["a", "=b"])
    assert book.ws.updates == 0
    real.write_cell("T", 1, 0, "fine")
    assert book.ws.updates == 1


# ------------------------------------------------------------------ H4 downgrades


def test_downgrade_through_0009_and_back(test_db_url: str, db: Session) -> None:
    """H4: 0009 goes down to 0008 (old function back, new columns gone) and up again."""
    from alembic import command

    from saley.cli import alembic_config

    db.rollback()
    cfg = alembic_config(test_db_url)
    engine = create_engine(test_db_url)
    try:
        command.downgrade(cfg, "0008")
        with engine.connect() as conn:
            old = conn.execute(
                text("select to_regprocedure('saley_commitment_done(text, bigint)')")
            ).scalar()
            new = conn.execute(
                text("select to_regprocedure('saley_commitment_done(text, bigint, text, integer)')")
            ).scalar()
            columns = {
                r[0]
                for r in conn.execute(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_name = 'v_decision'"
                    )
                )
            }
            indexes = conn.execute(
                text("select count(*) from pg_indexes where indexname = 'ix_outbox_row_open'")
            ).scalar_one()
        assert old is not None and new is None
        assert "sync_state" not in columns and indexes == 0
        command.downgrade(cfg, "0005")
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert "sync_state" in {
                r[0]
                for r in conn.execute(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_name = 'v_decision'"
                    )
                )
            }
    finally:
        command.upgrade(cfg, "head")
        engine.dispose()


def test_downgrade_text_names_the_owner_rule() -> None:
    """H4, then Q-R6-4: the drop never switches role (saley_admin is a member of the owner,
    D244), and it says what to do when the role cannot drop."""
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    for name, attr in (
        ("0006_writes.py", "DROP_FUNCTIONS"),
        ("0008_commitment_done_cycle.py", "DROP_FUNCTION"),
    ):
        spec = importlib.util.spec_from_file_location(f"m_{attr}", root / name)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sql = getattr(module, attr)
        assert "set local role" not in sql and "reset role" not in sql
        assert "insufficient_privilege" in sql and "saley_admin, a member of saley" in sql


# ------------------------------------------------------------------ H5 failed writes are visible


def _status(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/status")
    assert response.status_code == 200
    return response.json()


def test_status_says_pending_held_and_failed(
    client: TestClient, synced: Session, setting: Any
) -> None:
    quiet = _status(client)
    assert (quiet["pending"], quiet["held"], quiet["failed"]) == (0, 0, 0)
    assert quiet["write_note"] is None and quiet["write_bad"] is False

    item = cell_write(synced, "sid", "Alex Search", "meeting_status", "booked")
    waiting = _status(client)
    assert (waiting["pending"], waiting["pending_writes"]) == (1, 1)
    assert waiting["write_note"] == w.PENDING_NOTE and waiting["write_bad"] is False

    setting("SHEET_WRITE_ENABLED", "false")
    synced.execute(text("update sync_outbox set state = 'held'"))
    synced.commit()
    held = _status(client)
    assert (held["held"], held["pending"]) == (1, 0)
    assert held["write_note"] == w.WRITES_OFF  # not "updates in a few seconds"

    setting("SHEET_WRITE_ENABLED", "true")
    assert _status(client)["write_note"] == w.PENDING_NOTE  # writes are on again

    synced.execute(text("update sync_outbox set state = 'failed', error = 'boom'"))
    synced.commit()
    failed = _status(client)
    assert failed["failed"] == 1 and failed["write_bad"] is True
    assert failed["write_note"] == "Sheet write failed: 1. See Decisions."
    assert item["write_id"]


def test_a_failed_write_stays_in_the_bar_until_fixed_or_undone(
    client: TestClient, synced: Session
) -> None:
    item = cell_write(synced, "sid", "Alex Search", "meeting_status", "booked")
    synced.execute(text("update sync_outbox set state = 'failed', error = 'x'"))
    synced.commit()
    for _ in range(3):  # time does not clear it
        assert _status(client)["failed"] == 1
    # A newer pending write on the same cell does not hide the failure either.
    cell_write(synced, "sid", "Alex Search", "meeting_status", "completed")
    both = _status(client)
    assert (both["failed"], both["pending"]) == (1, 1) and both["write_bad"] is True
    # Undo of the failed write is the way out (the newer write has to go first).
    assert client.post(f"/api/undo/{item['write_id']}", headers=H).status_code == 409
    newest = synced.execute(text("select max(id) from decision")).scalar_one()
    assert client.post(f"/api/undo/{newest}", headers=H).status_code == 200
    assert client.post(f"/api/undo/{item['write_id']}", headers=H).status_code == 200
    assert _status(client)["failed"] == 0


def test_a_good_newer_write_clears_an_old_failure(
    client: TestClient, synced: Session, fake_sheet: FakeSheet
) -> None:
    cell_write(synced, "sid", "Alex Search", "meeting_status", "booked")
    synced.execute(text("update sync_outbox set state = 'failed', error = 'x'"))
    synced.commit()
    assert _status(client)["failed"] == 1
    cell_write(synced, "sid", "Alex Search", "meeting_status", "completed")
    assert push_pending(fake_sheet, now=now_ist()).done == 1
    assert _status(client)["failed"] == 0


def test_decisions_view_shows_the_write_state_and_a_fixed_reason(
    client: TestClient, synced: Session
) -> None:
    ids = {}
    for state, role in (
        ("pending", "meeting_status"),
        ("held", "first_contact_type"),
        ("done", "li_connected_date"),
        ("failed", "first_contact"),
    ):
        value = "2026-10-19" if role == "li_connected_date" else "booked"
        value = "TRUE" if role == "first_contact" else value
        ids[state] = cell_write(synced, "sid", "Alex Search", role, value, cooldown=0)["write_id"]
        synced.execute(
            text("update sync_outbox set state = :s, error = :e where decision_id = :d"),
            {
                "s": state,
                "e": "Secret: do not show 12345" if state == "failed" else None,
                "d": ids[state],
            },
        )
    synced.commit()
    items = {i["id"]: i for i in client.get("/api/decisions").json()["items"]}
    assert {s: items[i]["sync_state"] for s, i in ids.items()} == {
        s: s for s in ("pending", "held", "done", "failed")
    }
    assert items[ids["failed"]]["sync_reason"] == w.SYNC_REASON_OTHER
    assert items[ids["done"]]["sync_reason"] is None
    assert "Secret" not in json.dumps(items) and "12345" not in json.dumps(items)


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        ("cell changed since, not restored", w.SYNC_REASON_CELL),
        ("the row was not found in the sheet", w.SYNC_REASON_ROW),
        ("role 'company' is read-only on an existing row", w.SYNC_REASON_LOCKED),
        ("the value starts with a formula sign", w.SYNC_REASON_FORMULA),
        ("APIError: 500", w.SYNC_REASON_OTHER),
        (None, w.SYNC_REASON_OTHER),
    ],
)
def test_sync_reason_is_fixed_text(error: str | None, reason: str) -> None:
    from saley.web.team import sync_reason

    assert sync_reason(error, "failed") == reason
    assert sync_reason(error, "done") is None


# ------------------------------------------------------------------ H6 undo rules


def test_undo_is_refused_when_a_newer_write_is_on_the_cell(
    client: TestClient, synced: Session
) -> None:
    first = cell_write(synced, "sid", "Alex Search", "meeting_status", "booked")
    second = cell_write(synced, "sid", "Alex Search", "meeting_status", "completed")
    refused = client.post(f"/api/undo/{first['write_id']}", headers=H)
    assert refused.status_code == 409
    assert refused.json() == {"detail": "A newer change is on this cell."}
    assert undo(synced, "sid", first["write_id"]) == {"ok": False, "code": "superseded"}
    assert count(synced, "decision", "undone_at is not null") == 0
    # Newest first, then the older one: both work.
    assert client.post(f"/api/undo/{second['write_id']}", headers=H).status_code == 200
    assert client.post(f"/api/undo/{first['write_id']}", headers=H).status_code == 200


def test_a_failed_or_cancelled_newer_write_does_not_block_an_undo(synced: Session) -> None:
    first = cell_write(synced, "sid", "Alex Search", "meeting_status", "booked")
    cell_write(synced, "sid", "Alex Search", "meeting_status", "completed")
    synced.execute(
        text("update sync_outbox set state = 'failed', error = 'x' where value = 'completed'")
    )
    synced.commit()
    assert undo(synced, "sid", first["write_id"])["ok"] is True


def test_other_cells_do_not_block_an_undo(synced: Session) -> None:
    first = cell_write(synced, "sid", "Alex Search", "meeting_status", "booked")
    cell_write(synced, "sid", "Alex Search", "first_contact_type", "LinkedIn")
    cell_write(synced, "sid", "Ro One", "meeting_status", "booked")
    assert undo(synced, "sid", first["write_id"])["ok"] is True


def test_the_same_instruction_after_a_failed_write_queues_a_new_write(synced: Session) -> None:
    """H6 b: "already" must not hide a write that never reached the sheet."""
    key = "k" * 32
    first = cell_write(synced, "sid", "Alex Search", "meeting_status", "booked", key=key)
    again = cell_write(synced, "sid", "Alex Search", "meeting_status", "booked", key=key)
    assert again["already"] is True and again["write_id"] == first["write_id"]

    synced.execute(text("update sync_outbox set state = 'failed', error = 'x'"))
    synced.commit()
    retry = cell_write(synced, "sid", "Alex Search", "meeting_status", "booked", key=key)
    assert retry["already"] is False and retry["write_id"] != first["write_id"]
    assert count(synced, "sync_outbox") == 2
    assert count(synced, "sync_outbox", "state = 'pending'") == 1
    # The new write is now the live one: the same instruction is "already" again.
    third = cell_write(synced, "sid", "Alex Search", "meeting_status", "booked", key=key)
    assert third["already"] is True and third["write_id"] == retry["write_id"]


def test_a_failed_restore_marks_the_decision_undo_failed(
    client: TestClient, synced: Session, fake_sheet: FakeSheet
) -> None:
    """H6 c: the undone write says "undo failed", and the top bar counts the failed restore."""
    row = fake_sheet.find_row(POC, col("name"), "Alex Search")
    item = cell_write(synced, "sid", "Alex Search", "meeting_status", "booked")
    push_pending(fake_sheet, now=now_ist())
    fake_sheet.write_cell(POC, row, col("meeting_status"), "human edit")
    assert undo(synced, "sid", item["write_id"])["ok"] is True
    assert push_pending(fake_sheet, now=now_ist()).failed == 1

    items = {i["id"]: i for i in client.get("/api/decisions").json()["items"]}
    original = items[item["write_id"]]
    assert original["undo_state"] == "undo_failed"
    assert (original["sync_state"], original["sync_reason"]) == ("failed", w.SYNC_REASON_CELL)
    assert client.get("/api/decisions?undo=undo_failed").json()["total"] == 1
    assert client.get("/api/decisions?undo=undone").json()["total"] == 0
    status = _status(client)
    assert status["failed"] == 1 and status["write_bad"] is True
    assert fake_sheet.read_cell(POC, row, col("meeting_status")) == "human edit"


def test_a_clean_undo_is_plain_undone(
    client: TestClient, synced: Session, fake_sheet: FakeSheet
) -> None:
    item = cell_write(synced, "sid", "Alex Search", "meeting_status", "booked")
    push_pending(fake_sheet, now=now_ist())
    undo(synced, "sid", item["write_id"])
    assert push_pending(fake_sheet, now=now_ist()).done == 1
    original = next(
        i for i in client.get("/api/decisions").json()["items"] if i["id"] == item["write_id"]
    )
    assert (original["undo_state"], original["sync_state"]) == ("undone", "done")
    assert _status(client)["failed"] == 0


@pytest.mark.parametrize("old", ["-", "+91 98450 00000", "@sid"])
def test_undo_restores_an_old_value_that_starts_with_a_formula_sign(
    synced: Session, fake_sheet: FakeSheet, old: str
) -> None:
    """Re-review MEDIUM: an old "-" or "+91…" goes back as text, never as a formula."""
    row = fake_sheet.find_row(POC, col("name"), "Alex Search")
    fake_sheet.write_cell(POC, row, col("meeting_status"), as_literal(old))
    assert fake_sheet.read_cell(POC, row, col("meeting_status")) == old
    item = cell_write(synced, "sid", "Alex Search", "meeting_status", "booked")
    push_pending(fake_sheet, now=now_ist())
    undo(synced, "sid", item["write_id"])
    fake_sheet.calls.clear()

    stats = push_pending(fake_sheet, now=now_ist())

    assert (stats.done, stats.failed) == (1, 0)
    assert fake_sheet.read_cell(POC, row, col("meeting_status")) == old
    assert as_literal(old) == "'" + old and as_literal("booked") == "booked"


def test_a_new_write_still_refuses_a_formula_sign_in_the_worker(
    synced: Session, fake_sheet: FakeSheet
) -> None:
    outbox = _force_outbox(synced, "Alex Search", "meeting_status", "-x")
    assert push_pending(fake_sheet, now=now_ist()).failed == 1
    assert (
        synced.execute(
            text("select state from sync_outbox where id = :i"), {"i": outbox}
        ).scalar_one()
        == "failed"
    )


def test_a_restore_retry_is_done_when_the_cell_already_holds_the_old_value(
    synced: Session, fake_sheet: FakeSheet
) -> None:
    """H6 d: a lost read-back or note must not turn a good restore into a failure."""
    row = fake_sheet.find_row(POC, col("name"), "Alex Search")
    old = fake_sheet.read_cell(POC, row, col("meeting_status"))
    item = cell_write(synced, "sid", "Alex Search", "meeting_status", "booked")
    push_pending(fake_sheet, now=now_ist())
    undo(synced, "sid", item["write_id"])
    fake_sheet.write_cell(POC, row, col("meeting_status"), str(old))  # the restore landed
    fake_sheet.calls.clear()

    stats = push_pending(fake_sheet, now=now_ist())

    assert (stats.done, stats.failed) == (1, 0)
    assert not [c for c in fake_sheet.calls if c[0] == "write_cell"]
    state = synced.execute(text("select state from sync_outbox where op = 'restore'")).scalar_one()
    assert state == "done"


# ------------------------------------------------------------------ M1 busy lock


def test_a_busy_cell_is_a_409_not_a_500(
    client: TestClient, synced: Session, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(act_module, "LOCK_TIMEOUT", "200ms")
    alex = contact_id(synced, "Alex Search")
    key = synced.execute(
        text("select sheet_row_key from contact where id = :i"), {"i": alex}
    ).scalar_one()
    with engine.connect() as holder:
        with holder.begin():
            holder.execute(
                text("select pg_advisory_xact_lock(hashtext(:k))"),
                {"k": f"poc{key}meeting_status"},
            )
            busy = post_act(client, alex, "meeting_status", "booked")
        assert busy.status_code == 409
        assert busy.json() == {"detail": "Busy. Try again."}
    assert count(synced, "decision") == 0
    assert post_act(client, alex, "meeting_status", "booked").status_code == 200


def test_busy_states_are_409_and_other_database_errors_are_not_hidden() -> None:
    class Orig(Exception):
        def __init__(self, state: str) -> None:
            super().__init__(state)
            self.sqlstate = state

    class Conn:
        def __init__(self, state: str) -> None:
            self.state = state

        def begin(self) -> Any:
            raise DBAPIError("select 1", {}, Orig(self.state))

    for state in ("55P03", "40001", "40P01"):
        with pytest.raises(HTTPException) as busy:
            act_module.call_function(Conn(state), "select 1", {})  # type: ignore[arg-type]
        assert busy.value.status_code == 409 and busy.value.detail == w.ERR_BUSY
    with pytest.raises(DBAPIError):
        act_module.call_function(Conn("23505"), "select 1", {})  # type: ignore[arg-type]


# ------------------------------------------------------------------ M2 body size


def test_a_big_body_is_413_before_it_is_parsed(client: TestClient) -> None:
    big = "x" * (MAX_BODY_BYTES + 1)
    declared = client.post("/api/act", content=big, headers=H)
    assert declared.status_code == 413
    assert declared.json() == {"detail": "The request is too large."}
    assert declared.headers["cache-control"] == "no-store"  # the security headers still apply

    def chunks() -> Iterator[bytes]:  # no Content-Length: counted while it is read
        for _ in range(MAX_BODY_BYTES // 1024 + 2):
            yield b"x" * 1024

    streamed = client.post("/api/act", content=chunks(), headers=H)
    assert streamed.status_code == 413
    assert streamed.json() == {"detail": "The request is too large."}


def test_a_normal_body_and_every_get_are_not_limited(client: TestClient, synced: Session) -> None:
    alex = contact_id(synced, "Alex Search")
    assert post_act(client, alex, "meeting_status", "booked").status_code == 200
    assert client.get("/api/status").status_code == 200
    padded = {"action": "write", "target_ids": [alex], "payload": {"role": "x", "value": "y" * 200}}
    assert len(json.dumps(padded)) < MAX_BODY_BYTES
    assert client.post("/api/act", json=padded, headers=H).status_code in (200, 403, 422)


# ------------------------------------------------------------------ M4 contact mirror


def test_a_pushed_task_does_not_come_back_before_the_next_sync(
    client: TestClient, synced: Session, fake_sheet: FakeSheet
) -> None:
    def li_confirm_names() -> list[str]:
        items = client.get("/api/today?user=sid").json()["my_tasks"]["items"]
        # R5 T3: a tick inside its undo window stays as a finished row (`done`); only an OPEN
        # task must not come back.
        return [i["name"] for i in items if i["kind"] == "li_confirm" and not i["done"]]

    assert "Sshubam Verma" in li_confirm_names()
    task = next(
        i
        for i in client.get("/api/today?user=sid").json()["my_tasks"]["items"]
        if i["kind"] == "li_confirm" and i["name"] == "Sshubam Verma"
    )
    done = client.post(
        task["action"]["endpoint"], json=task["action"]["body"], headers={**H, "X-Saley-As": "sid"}
    )
    assert done.status_code == 200
    assert "Sshubam Verma" not in li_confirm_names()  # hidden while the write is open

    assert push_pending(fake_sheet, now=now_ist()).done == 1
    assert "Sshubam Verma" not in li_confirm_names()  # still hidden: the contact row was mirrored
    stored = synced.execute(
        text("select li_connected_date from contact where name = 'Sshubam Verma'")
    ).scalar_one()
    assert stored == today_ist()


def test_mirror_value_follows_the_sync_field_map(synced: Session) -> None:
    from saley.core.models import Contact

    contact = synced.query(Contact).filter_by(name="Ro One").one()
    mirror_value(contact, "first_contact", "VR")
    assert (contact.first_contact, contact.contacted) == ("VR", True)
    mirror_value(contact, "first_contact", "FALSE")
    assert (contact.first_contact, contact.contacted) == ("FALSE", False)
    mirror_value(contact, "sid_li_added", "Connected")
    assert (contact.li_status, contact.connected, contact.contacted) == ("Connected", True, True)
    mirror_value(contact, "sid_li_added", "Sent")
    assert (contact.connected, contact.contacted) == (False, False)
    mirror_value(contact, "meeting_date", "2026-10-19")
    assert contact.meeting_date == date(2026, 10, 19)
    mirror_value(contact, "meeting_date", "")
    assert contact.meeting_date is None
    mirror_value(contact, "meeting_date", "not a date")  # ignored, never a crash
    assert contact.meeting_date is None
    mirror_value(contact, "meeting_status", "booked")
    mirror_value(contact, "first_contact_type", "LinkedIn")
    assert (contact.meeting_status, contact.first_contact_type) == ("booked", "LinkedIn")
    mirror_value(contact, "email", "a@b.co")
    assert (contact.email, contact.email_status) == ("a@b.co", "known")
    mirror_value(contact, "email", "")
    assert (contact.email, contact.email_status) == (None, None)
    before = contact.cells
    mirror_value(contact, "company", "Nope")  # a role the sync does not mirror
    assert contact.cells == before


# ------------------------------------------------------------------ M7 dev host


def test_dev_api_refuses_a_host_with_another_port(client: TestClient) -> None:
    ok = client.get("/api/status", headers={"Host": "localhost"})  # test server port is 80
    assert ok.status_code == 200
    assert client.get("/api/status", headers={"Host": "localhost:80"}).status_code == 200
    for host in ("localhost:9999", "127.0.0.1:8000"):
        refused = client.get("/api/status", headers={"Host": host})
        assert refused.status_code == 403, host
        assert refused.json() == {"detail": w.ERR_HOST}
    assert client.get("/api/status", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/healthz", headers={"Host": "localhost:9999"}).status_code == 200


def test_the_host_port_is_the_port_the_server_listens_on(client: TestClient) -> None:
    class Req:
        def __init__(self, host: str, port: int) -> None:
            self.headers = {"host": host}
            self.scope = {"server": ("127.0.0.1", port)}

    from saley.web.guards import _bad_host

    assert _bad_host(Req("localhost:8123", 8123)) is False  # `saley dev --port 8123`
    assert _bad_host(Req("localhost:8000", 8123)) is True
    assert _bad_host(Req("localhost", 8123)) is True
    assert _bad_host(Req("localhost", 80)) is False


# ------------------------------------------------------------------ M8 indexes


def test_new_indexes_exist(db: Session) -> None:
    rows = dict(
        db.execute(
            text(
                "select indexname, indexdef from pg_indexes "
                "where schemaname = 'public' and indexname like any (array["
                "'ix_decision_task_done','ix_outbox_row_open','ix_reminder_commitment',"
                "'ix_reminder_for_user','ix_decision_proposal','ix_decision_undo_of'])"
            )
        ).all()
    )
    assert len(rows) == 6
    assert "(row_key, role)" in rows["ix_decision_task_done"]
    assert (
        "task_done" in rows["ix_decision_task_done"]
        and "undone_at IS NULL" in rows["ix_decision_task_done"]
    )
    assert "(row_key)" in rows["ix_outbox_row_open"] and "pending" in rows["ix_outbox_row_open"]
    assert "(commitment_id)" in rows["ix_reminder_commitment"]
    assert "(for_user)" in rows["ix_reminder_for_user"]
    assert "(proposal_id)" in rows["ix_decision_proposal"]


# ------------------------------------------------------------------ M11 commitment signature


def _commitment(db: Session, owner: str = "sid") -> int:
    cid = db.execute(
        text(
            "insert into commitment(owner, text, due) "
            "select id, 'I will send the deck.', :d from users where short = :o returning id"
        ),
        {"d": today_ist(), "o": owner},
    ).scalar_one()
    db.execute(
        text(
            "insert into reminder(for_user, text, fire_at, commitment_id, kind, state) "
            "select id, 'Due today: x', now(), :c, 'due_day', 'pending' from users where short = :o"
        ),
        {"c": cid, "o": owner},
    )
    db.commit()
    return cid


SID = {**H, "X-Saley-As": "sid"}  # D155: only the owner (sid) marks a commitment done


def test_commitment_done_is_signed_and_can_be_undone(client: TestClient, synced: Session) -> None:
    cid = _commitment(synced)
    done = client.post(f"/api/commitment/{cid}/done", headers=SID)
    assert done.status_code == 200, done.text
    body = done.json()
    row = synced.execute(
        text(
            "select d.kind, d.surface, u.short, d.state, d.undo_until > now() "
            "from decision d join users u on u.id = d.user_id where d.id = :i"
        ),
        {"i": body["write_id"]},
    ).one()
    assert tuple(row) == ("commitment_done", "dashboard", "sid", "active", True)
    assert body["undo_until"] and body["already"] is False
    assert synced.execute(text("select state from reminder")).scalar_one() == "skipped"

    again = client.post(f"/api/commitment/{cid}/done", headers=SID).json()
    assert again["already"] is True and again["write_id"] == body["write_id"]
    assert count(synced, "decision", "kind = 'commitment_done'") == 1

    listed = next(
        i for i in client.get("/api/decisions").json()["items"] if i["id"] == body["write_id"]
    )
    assert (listed["what"], listed["can_undo"], listed["user_short"]) == (
        "Commitment done",
        True,
        "sid",
    )

    undone = client.post(f"/api/undo/{body['write_id']}", headers=SID)
    assert undone.status_code == 200 and undone.json()["cancelled"] is False
    assert synced.execute(text("select state from commitment")).scalar_one() == "open"
    assert synced.execute(text("select state from reminder")).scalar_one() == "pending"
    assert client.post(f"/api/undo/{body['write_id']}", headers=SID).status_code == 409
    # It can be done again, as a new signed decision.
    second = client.post(f"/api/commitment/{cid}/done", headers=SID).json()
    assert second["already"] is False and second["write_id"] != body["write_id"]


def test_commitment_undo_window_closes(client: TestClient, synced: Session) -> None:
    cid = _commitment(synced)
    write_id = client.post(f"/api/commitment/{cid}/done", headers=SID).json()["write_id"]
    synced.execute(text("update decision set undo_until = now() - interval '1 minute'"))
    synced.commit()
    late = client.post(f"/api/undo/{write_id}", headers=SID)
    assert late.status_code == 409 and late.json()["detail"].startswith("Undo window closed at")
    assert synced.execute(text("select state from commitment")).scalar_one() == "done"


def test_commitment_function_checks_the_surface_and_the_role(synced: Session) -> None:
    cid = _commitment(synced)
    bad = sql_call(synced, "select saley_commitment_done('sid', :i, 'email', 10)", i=cid)
    assert bad == {"ok": False, "code": "bad_request"}
    chat = sql_call(synced, "select saley_commitment_done('sid', :i, 'chat', 10)", i=cid)
    assert chat["ok"] and chat["already"] is False
    assert synced.execute(text("select surface from decision")).scalar_one() == "chat"


# ------------------------------------------------------------------ LOW items


def test_validation_errors_do_not_echo_the_input(client: TestClient) -> None:
    secret = "sk-secret-value-123"
    bad = client.post("/api/act", json={"action": secret, "target_ids": [1]}, headers=H)
    assert bad.status_code == 422
    assert bad.json() == {"detail": "The request is not valid."}
    assert secret not in bad.text
    query = client.get("/api/people", params={"limit": secret})
    assert query.status_code == 422 and secret not in query.text
    assert query.json() == {"detail": "The request is not valid."}


def test_the_header_check_runs_before_the_database_is_opened(client: TestClient) -> None:
    def broken() -> Iterator[Any]:
        raise RuntimeError("the database must not be opened for this request")
        yield  # pragma: no cover

    client.app.dependency_overrides[act_module.act_db] = broken  # type: ignore[attr-defined]
    try:
        body = {
            "action": "write",
            "target_ids": [1],
            "payload": {"role": "meeting_status", "value": "x"},
        }
        assert client.post("/api/act", json=body).status_code == 403  # no X-Saley-Act header
        assert client.post("/api/undo/1").status_code == 403
        assert client.post("/api/decision/1/confirm").status_code == 403
        assert client.post("/api/proposal/1/dismiss").status_code == 403
        assert client.post("/api/commitment/1/done").status_code == 403
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def test_task_dates_cut_a_datetime_down_to_the_day() -> None:
    from saley.intel.tasks import _as_date

    assert _as_date(datetime(2026, 10, 19, 23, 30)) == date(2026, 10, 19)
    assert type(_as_date(datetime(2026, 10, 19, 23, 30))) is date
    assert _as_date(date(2026, 10, 19)) == date(2026, 10, 19)
    assert _as_date("2026-10-19T10:00:00") == date(2026, 10, 19)
    assert _as_date(today_ist()) - timedelta(days=0) == today_ist()


def test_act_js_shows_the_server_line_and_undone() -> None:
    """M3: the page uses the fixed server text for refusals and says "Undone" after an Undo.
    Since the cut-over (D224) the page is the app in web/: api.ts reads the server's `detail`."""
    from pathlib import Path

    root = Path(act_module.__file__).parents[3] / "web" / "src"
    api = (root / "api.ts").read_text(encoding="utf-8")
    assert ".detail" in api and "MAX_LINE" in api
    assert 'undoneLine: "Undone."' in (root / "words.ts").read_text(encoding="utf-8")
