"""The sheet writer, the outbox and the write guards (R2 phase 2).

Writes run only against a FakeSheet. The real gspread writer is never called here.
"""

from __future__ import annotations

import ast
import random
import re
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import structlog
import structlog.testing
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from saley.core.clock import now_ist, today_ist
from saley.core.settings import Settings, get_settings, set_process
from saley.sales import roundtrip, writer
from saley.sales.columns import POC_ROLES
from saley.sales.outbox import push_pending
from saley.sales.sheet import build_tabs
from saley.sales.sync import run_sync
from saley.sales.writer import FakeSheet, append_note, norm_value
from saley.web.app import create_app
from tests.conftest import POC_ROLE_ORDER, SignedClient, contact_id, sql_act, sql_call

ROOT = Path(__file__).resolve().parents[1]
POC = "Outreach PoCs"
ACT_HEADERS = {"X-Saley-Act": "1"}


def col(role: str) -> int:
    return POC_ROLE_ORDER.index(role)


def sheet_row(sheet: FakeSheet, name: str) -> int:
    row = sheet.find_row(POC, col("name"), name)
    assert row is not None
    return row


def undo(db: Session, user: str, write_id: int) -> dict[str, Any]:
    return sql_call(db, "select saley_undo(:u, :id, 'dashboard')", u=user, id=write_id)


@pytest.fixture
def client(synced: Session) -> Iterator[TestClient]:
    with SignedClient(create_app()) as c:
        yield c


@pytest.fixture
def setting(monkeypatch: pytest.MonkeyPatch):
    """Change one setting for one test; the cache is cleared on both sides."""

    def apply(name: str, value: str) -> None:
        monkeypatch.setenv(name, value)
        get_settings.cache_clear()

    yield apply
    monkeypatch.undo()
    get_settings.cache_clear()


# ------------------------------------------------------------------ R2-W01


def test_write_round_trip_cell_decision_and_note(synced: Session, fake_sheet: FakeSheet) -> None:
    """R2-W01: a write reaches the cell, the decision row and the Name note; it reads back equal."""
    alex = contact_id(synced, "Alex Search")
    today = today_ist().isoformat()
    result = sql_act(synced, "sid", [{"role": "li_connected_date", "value": today}], [alex])
    assert result["ok"] and result["items"][0]["already"] is False
    write_id = result["items"][0]["write_id"]

    stats = push_pending(fake_sheet, now=now_ist())

    assert stats.done == 1 and stats.failed == 0
    row = sheet_row(fake_sheet, "Alex Search")
    cell = fake_sheet.read_cell(POC, row, col("li_connected_date"))
    assert norm_value("li_connected_date", cell) == today
    decision = synced.execute(
        text("select before, after, surface, state from decision where id = :i"), {"i": write_id}
    ).one()
    assert tuple(decision) == ("", today, "dashboard", "active")
    outbox = synced.execute(
        text("select state, error from sync_outbox where decision_id = :i"), {"i": write_id}
    ).one()
    assert tuple(outbox) == ("done", None)
    audit = synced.execute(text("select before, after, by from sync_audit")).one()
    assert tuple(audit) == ("", today, "sid")
    note = fake_sheet.read_note(POC, row, col("name"))
    day = today_ist()
    assert note.startswith(
        f"Saley · LI Connected Date = {day.day} {day:%b %Y} · Sid · dashboard · "
    )
    assert note.endswith(f" IST · d{write_id}")


# ------------------------------------------------------------------ R3-S01


def test_row_add_with_signature_visible_after_sync(synced: Session, fake_sheet: FakeSheet) -> None:
    """R3-S01: a row add lands with the signature note and shows after one sync."""
    from saley.core.models import Account, SearchHit
    from tests.conftest import sql_row_add

    company = synced.scalar(select(Account.id).where(Account.name == "Sarvam AI"))
    link = "https://www.linkedin.com/in/priya-sharma-1a2b3c"
    synced.add(
        SearchHit(query="q", engine="duckduckgo", url=link, title="Priya", account_id=company)
    )
    synced.commit()
    result = sql_row_add(
        synced,
        "sid",
        company,
        {"name": "Priya Sharma", "designation": "Head of Data", "li_url": link},
    )
    assert result["ok"], result
    write_id = result["items"][0]["write_id"]
    rows_before = len(fake_sheet.tabs[POC])

    stats = push_pending(fake_sheet, now=now_ist())

    assert stats.done == 1 and stats.failed == 0
    row = sheet_row(fake_sheet, "Priya Sharma")
    assert row == rows_before + 1
    cells = {r: fake_sheet.read_cell(POC, row, col(r)) for r in POC_ROLE_ORDER[:10]}
    assert cells["sr_no"] == 8 and cells["company"] == "Sarvam AI"
    assert cells["designation"] == "Head of Data" and cells["li_url"] == link
    note = fake_sheet.read_note(POC, row, col("name"))
    when = synced.scalar(text("select at from decision where id = :i"), {"i": write_id})
    assert note == writer.row_added_line("Sid", "dashboard", when, write_id)
    assert note.startswith("Saley · row added · Sid · dashboard · ")
    assert note.endswith(f" IST · d{write_id}")
    state = synced.execute(
        text("select o.state, d.kind from sync_outbox o join decision d on d.id = o.decision_id")
    ).one()
    assert tuple(state) == ("done", "row_add")

    run_sync(synced, build_tabs(fake_sheet.raw()), now_ist())
    synced.commit()
    person = synced.execute(
        text("select name, title, li_url, sr_no from contact where name = 'Priya Sharma'")
    ).one()
    assert tuple(person) == ("Priya Sharma", "Head of Data", link, "8")
    # The sync read the row we wrote: same row number, and the Name cell still has its note.
    assert synced.scalar(text("select row_number from contact where name = 'Priya Sharma'")) == row
    assert fake_sheet.read_note(POC, row, col("name")) == note
    assert (
        synced.scalar(
            text(
                "select count(*) from contact c join account a on a.id = c.account_id "
                "where c.name = 'Priya Sharma' and a.name = 'Sarvam AI'"
            )
        )
        == 1
    )


def test_a_row_add_for_the_qa_company_is_never_written(
    synced: Session, fake_sheet: FakeSheet
) -> None:
    """The browser check adds a person to "QA Seed Co". The worker must refuse it (safety rule)."""
    from saley.qa import seed
    from tests.conftest import sql_row_add

    info = seed.seed(synced)
    synced.commit()
    link = synced.execute(
        text("select url from v_search_hit where account_id = :a"), {"a": info.account}
    ).scalar_one()
    result = sql_row_add(synced, "sid", info.account, {"name": "QA Seed Person", "li_url": link})
    assert result["ok"], result
    before = [list(r) for r in fake_sheet.tabs[POC]]

    stats = push_pending(fake_sheet, now=now_ist())

    assert stats.done == 0 and stats.failed == 1
    assert fake_sheet.tabs[POC] == before
    assert not [c for c in fake_sheet.calls if c[0].startswith(("write", "append", "insert"))]
    seed.teardown(synced)
    synced.commit()


# ------------------------------------------------------------------ R2-W03


def test_undo_inside_window_restores(synced: Session, fake_sheet: FakeSheet) -> None:
    """R2-W03: undo inside UNDO_MINUTES puts the old value back and signs the note."""
    alex = contact_id(synced, "Alex Search")
    row = sheet_row(fake_sheet, "Alex Search")
    old = fake_sheet.read_cell(POC, row, col("meeting_status"))
    write = sql_act(synced, "sid", [{"role": "meeting_status", "value": "booked"}], [alex])
    write_id = write["items"][0]["write_id"]
    push_pending(fake_sheet, now=now_ist())
    assert fake_sheet.read_cell(POC, row, col("meeting_status")) == "booked"

    result = undo(synced, "kushal", write_id)
    assert result["ok"] and result["cancelled"] is False
    stats = push_pending(fake_sheet, now=now_ist())

    assert stats.done == 1
    assert fake_sheet.read_cell(POC, row, col("meeting_status")) == old
    lines = fake_sheet.read_note(POC, row, col("name")).splitlines()
    assert lines[-1].startswith(f"Saley · undone d{write_id} · Kushal · dashboard · ")
    kinds = synced.execute(text("select kind, undo_of from decision order by id")).all()
    assert [k for k, _ in kinds] == ["column_write", "undo"]
    assert kinds[1][1] == write_id
    assert synced.execute(
        text("select undone_at is not null from decision where id = :i"), {"i": write_id}
    ).scalar()


def test_undo_of_a_pending_write_never_touches_the_sheet(
    synced: Session, fake_sheet: FakeSheet
) -> None:
    """A write the worker has not pushed yet is cancelled. No sheet call is made."""
    alex = contact_id(synced, "Alex Search")
    write = sql_act(synced, "sid", [{"role": "meeting_status", "value": "booked"}], [alex])
    result = undo(synced, "sid", write["items"][0]["write_id"])
    assert result["ok"] and result["cancelled"] is True
    assert push_pending(fake_sheet, now=now_ist()).done == 0
    assert not [c for c in fake_sheet.calls if c[0] == "write_cell"]
    state = synced.execute(text("select state from sync_outbox")).scalar_one()
    assert state == "cancelled"


def test_restore_is_refused_when_a_human_changed_the_cell(
    synced: Session, fake_sheet: FakeSheet
) -> None:
    """Undo writes back only if the cell still holds what Saley wrote; else it fails and logs."""
    alex = contact_id(synced, "Alex Search")
    row = sheet_row(fake_sheet, "Alex Search")
    write = sql_act(synced, "sid", [{"role": "meeting_status", "value": "booked"}], [alex])
    push_pending(fake_sheet, now=now_ist())
    fake_sheet.write_cell(POC, row, col("meeting_status"), "human edit")
    undo(synced, "sid", write["items"][0]["write_id"])

    with structlog.testing.capture_logs() as logs:
        stats = push_pending(fake_sheet, now=now_ist())

    assert stats.failed == 1
    assert fake_sheet.read_cell(POC, row, col("meeting_status")) == "human edit"
    error = synced.execute(text("select error from sync_outbox where op = 'restore'")).scalar_one()
    assert error == "cell changed since, not restored"
    assert any(e["event"] == "restore_refused" for e in logs)


def test_undo_after_window_is_refused(synced: Session, fake_sheet: FakeSheet) -> None:
    """R2-W03: after the window, undo is refused with the closing time and nothing is queued."""
    alex = contact_id(synced, "Alex Search")
    write = sql_act(synced, "sid", [{"role": "meeting_status", "value": "booked"}], [alex])
    write_id = write["items"][0]["write_id"]
    push_pending(fake_sheet, now=now_ist())
    synced.execute(
        text("update decision set undo_until = now() - interval '1 minute' where id = :i"),
        {"i": write_id},
    )
    synced.commit()

    result = undo(synced, "sid", write_id)

    assert result["ok"] is False and result["code"] == "window_closed"
    assert re.fullmatch(r"\d\d:\d\d", result["at"])
    assert synced.execute(text("select count(*) from sync_outbox")).scalar_one() == 1
    assert (
        synced.execute(
            text("select undone_at from decision where id = :i"), {"i": write_id}
        ).scalar()
        is None
    )


def test_undo_by_a_non_approver_is_refused(synced: Session) -> None:
    alex = contact_id(synced, "Alex Search")
    write = sql_act(synced, "sid", [{"role": "meeting_status", "value": "booked"}], [alex])
    synced.execute(text("insert into users(short, name, role) values ('vee', 'Vee', 'viewer')"))
    synced.commit()
    try:
        result = undo(synced, "vee", write["items"][0]["write_id"])
    finally:
        synced.execute(text("delete from users where short = 'vee'"))
        synced.commit()
    assert result == {"ok": False, "code": "not_allowed"}


# ------------------------------------------------------------------ R2-W04 / R2-W05


LOCKED_ROLES = [
    "connected_with_vr",  # column Q, read-only
    "reconnect_date",
    "company",
    "name",
    "outreach_step",
    "email_1_sent",
    "deal_size",
    "poc_priority",
]


@pytest.mark.parametrize("role", LOCKED_ROLES)
def test_locked_column_write_is_403_and_logged(
    client: TestClient, synced: Session, role: str
) -> None:
    """R2-W04: a write to a locked column is refused with 403 and logged; nothing is stored."""
    alex = contact_id(synced, "Alex Search")
    body = {"action": "write", "target_ids": [alex], "payload": {"role": role, "value": "x"}}
    with structlog.testing.capture_logs() as logs:
        response = client.post("/api/act", json=body, headers=ACT_HEADERS)
    assert response.status_code == 403
    assert response.json() == {"detail": "That column is read-only. Saley does not write it."}
    refused = [e for e in logs if e["event"] == "write_refused"]
    assert refused and refused[0]["role"] == role
    assert synced.execute(text("select count(*) from decision")).scalar_one() == 0
    assert synced.execute(text("select count(*) from sync_outbox")).scalar_one() == 0


def test_sql_function_refuses_locked_roles_too(synced: Session) -> None:
    """The database function repeats the lock: a bug in the web code cannot open a column."""
    alex = contact_id(synced, "Alex Search")
    result = sql_act(synced, "sid", [{"role": "connected_with_vr", "value": "TRUE"}], [alex])
    assert result == {"ok": False, "code": "locked", "role": "connected_with_vr"}


def test_worker_checks_the_lock_again(synced: Session, fake_sheet: FakeSheet) -> None:
    """A row for a locked role that got into the outbox still never reaches the sheet."""
    alex = contact_id(synced, "Alex Search")
    ok = sql_act(synced, "sid", [{"role": "meeting_status", "value": "booked"}], [alex])
    synced.execute(
        text("update sync_outbox set role = 'connected_with_vr' where decision_id = :i"),
        {"i": ok["items"][0]["write_id"]},
    )
    synced.commit()
    stats = push_pending(fake_sheet, now=now_ist())
    assert stats.failed == 1
    assert not [c for c in fake_sheet.calls if c[0] == "write_cell"]
    error = synced.execute(text("select error from sync_outbox")).scalar_one()
    assert "read-only" in error


def test_worker_stops_when_a_locked_header_is_missing(
    synced: Session, fake_sheet: FakeSheet
) -> None:
    """If a restricted role maps to no column, every write stops until it maps again."""
    alex = contact_id(synced, "Alex Search")
    sql_act(synced, "sid", [{"role": "meeting_status", "value": "booked"}], [alex])
    fake_sheet.tabs[POC][0][col("connected_with_vr")] = "Renamed by a human"
    stats = push_pending(fake_sheet, now=now_ist())
    assert stats.failed == 1
    assert not [c for c in fake_sheet.calls if c[0] == "write_cell"]
    assert "do not map" in synced.execute(text("select error from sync_outbox")).scalar_one()


def test_worker_finds_a_moved_column(synced: Session, fake_sheet: FakeSheet) -> None:
    """Locks are by role, not letter: a human moves a column and the write follows the header."""
    alex = contact_id(synced, "Alex Search")
    sql_act(synced, "sid", [{"role": "meeting_status", "value": "booked"}], [alex])
    for row in fake_sheet.tabs[POC]:
        row.insert(0, "spare")
    assert push_pending(fake_sheet, now=now_ist()).done == 1
    row = fake_sheet.find_row(POC, col("name") + 1, "Alex Search")
    assert row is not None
    assert fake_sheet.read_cell(POC, row, col("meeting_status") + 1) == "booked"


def test_email_write_refused_while_not_allowed(
    client: TestClient, synced: Session, fake_sheet: FakeSheet, setting: Any
) -> None:
    """R2-W05: Email is refused while EMAIL_WRITE_ALLOWED=false; only email_write opens it."""
    alex = contact_id(synced, "Alex Search")
    cell = {"role": "email", "value": "alex@example.com"}
    off = {"action": "email_write", "target_ids": [alex], "payload": cell}
    with structlog.testing.capture_logs() as logs:
        refused = client.post("/api/act", json=off, headers=ACT_HEADERS)
    assert refused.status_code == 403
    assert "EMAIL_WRITE_ALLOWED" in refused.json()["detail"]
    assert any(e["event"] == "write_refused" and e["role"] == "email" for e in logs)
    # The plain write action never opens Email, even when the flag is on.
    setting("EMAIL_WRITE_ALLOWED", "true")
    plain = client.post(
        "/api/act",
        json={"action": "write", "target_ids": [alex], "payload": cell},
        headers=ACT_HEADERS,
    )
    assert plain.status_code == 403
    # The worker checks the flag again, whatever the web said.
    allowed = client.post("/api/act", json=off, headers=ACT_HEADERS)
    assert allowed.status_code == 200
    row = sheet_row(fake_sheet, "Alex Search")
    assert push_pending(fake_sheet, now=now_ist(), email_write_allowed=False).failed == 1
    assert fake_sheet.read_cell(POC, row, col("email")) in ("", None)
    synced.execute(text("update sync_outbox set state = 'pending', error = null, tries = 0"))
    synced.commit()
    assert push_pending(fake_sheet, now=now_ist(), email_write_allowed=True).done == 1
    assert fake_sheet.read_cell(POC, row, col("email")) == "alex@example.com"


# ------------------------------------------------------------------ outbox behaviour


def test_writes_off_keeps_rows_held_and_health_says_why(
    client: TestClient, synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SHEET_WRITE_ENABLED=false: rows stay held, no credential is built, health: writes are off."""
    from saley.worker import jobs

    alex = contact_id(synced, "Alex Search")
    sql_act(synced, "sid", [{"role": "meeting_status", "value": "booked"}], [alex])

    def no_client() -> None:
        raise AssertionError("no write client may be built while writes are off")

    monkeypatch.setattr(jobs, "build_write_sheet", no_client)
    note = jobs.sheet_push()

    assert note == "Sheet writes are off · 1 held"
    assert synced.execute(text("select state from sync_outbox")).scalar_one() == "held"
    writes = client.get("/api/health/full").json()["writes"]
    assert writes == {
        "enabled": False,
        "message": "Sheet writes are off",
        "open": 1,
        "failed": 0,
    }


def test_held_rows_are_pushed_when_writes_turn_on(
    synced: Session, fake_sheet: FakeSheet, setting: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from saley.worker import jobs

    alex = contact_id(synced, "Alex Search")
    sql_act(synced, "sid", [{"role": "meeting_status", "value": "booked"}], [alex])
    assert jobs.sheet_push().startswith("Sheet writes are off")
    setting("SHEET_WRITE_ENABLED", "true")
    monkeypatch.setattr(jobs, "build_write_sheet", lambda: fake_sheet)
    assert jobs.sheet_push() == "pushed 1 · failed 0 · retry 0"
    assert jobs.sheet_push() == "idle"  # nothing left: a quiet run
    assert synced.execute(text("select state from sync_outbox")).scalar_one() == "done"


def test_a_sheet_error_retries_then_fails(synced: Session, fake_sheet: FakeSheet) -> None:
    alex = contact_id(synced, "Alex Search")
    sql_act(synced, "sid", [{"role": "meeting_status", "value": "booked"}], [alex])
    moment = now_ist()

    def boom(*_args: Any) -> None:
        raise ConnectionError("https://x.example/?key=AIzaSyA1234567890123456789012345678901")

    fake_sheet.write_cell = boom  # type: ignore[method-assign]
    for attempt in range(1, 6):
        stats = push_pending(fake_sheet, now=moment)
        assert (stats.retry, stats.failed) == ((1, 0) if attempt < 5 else (0, 1))
        moment += timedelta(minutes=30)
    state, tries, error = synced.execute(text("select state, tries, error from sync_outbox")).one()
    assert (state, tries) == ("failed", 5)
    assert "AIzaSyA" not in error


def test_sync_keeps_a_cell_with_an_open_write(synced: Session, raw_sheet: dict) -> None:
    """The sync skips a cell with an open outbox row, so it never reverts a pending write."""
    alex = contact_id(synced, "Alex Search")
    sql_act(synced, "sid", [{"role": "meeting_status", "value": "booked"}], [alex])
    synced.execute(text("update contact set meeting_status = 'booked' where id = :i"), {"i": alex})
    synced.commit()
    run_sync(synced, build_tabs(raw_sheet), now_ist())  # the sheet still has the old (blank) cell
    synced.commit()
    kept = synced.execute(text("select meeting_status from contact where id = :i"), {"i": alex})
    assert kept.scalar_one() == "booked"
    synced.execute(text("update sync_outbox set state = 'done'"))
    synced.commit()
    run_sync(synced, build_tabs(raw_sheet), now_ist())
    synced.commit()
    reverted = synced.execute(text("select meeting_status from contact where id = :i"), {"i": alex})
    assert reverted.scalar_one() is None


def test_row_not_in_the_sheet_fails_clearly(synced: Session, fake_sheet: FakeSheet) -> None:
    alex = contact_id(synced, "Alex Search")
    sql_act(synced, "sid", [{"role": "meeting_status", "value": "booked"}], [alex])
    row = sheet_row(fake_sheet, "Alex Search")
    fake_sheet.tabs[POC][row - 1][col("name")] = "Someone Else"
    assert push_pending(fake_sheet, now=now_ist()).failed == 1
    error = synced.execute(text("select error from sync_outbox")).scalar_one()
    assert error == "the row was not found in the sheet"


def test_a_moved_row_is_found_by_name_and_sr_no(synced: Session, fake_sheet: FakeSheet) -> None:
    alex = contact_id(synced, "Alex Search")
    sql_act(synced, "sid", [{"role": "meeting_status", "value": "booked"}], [alex])
    fake_sheet.tabs[POC].insert(1, [""] * len(POC_ROLE_ORDER))  # a human inserts a row on top
    assert push_pending(fake_sheet, now=now_ist()).done == 1
    row = sheet_row(fake_sheet, "Alex Search")
    assert fake_sheet.read_cell(POC, row, col("meeting_status")) == "booked"


# ------------------------------------------------------------------ note format (D47)


def test_note_keeps_human_text_and_the_last_ten_lines() -> None:
    existing = "Met at the summit\n" + "\n".join(f"Saley · line {i}" for i in range(10))
    out = append_note(existing, "Saley · line 10")
    lines = out.splitlines()
    assert lines[0] == "Met at the summit"
    assert next(ln for ln in lines if ln.startswith("Saley · ")) == "Saley · line 1"
    assert len([ln for ln in lines if ln.startswith("Saley · ")]) == 10
    assert lines[-1] == "Saley · line 10"
    assert append_note(None, "Saley · first") == "Saley · first"


def test_values_compare_in_one_form() -> None:
    assert norm_value("meeting_date", 46314) == "2026-10-19"
    assert norm_value("first_contact", True) == "TRUE"
    assert norm_value("first_contact", "true") == "TRUE"
    assert norm_value("meeting_status", None) == ""
    assert norm_value("meeting_status", 3.0) == "3"
    assert writer.native_value("2026-10-19") == 46314
    assert writer.native_value("FALSE") is False
    assert writer.native_value("booked") == "booked"


# ------------------------------------------------------------------ R2-G01


def _module_file(name: str) -> Path | None:
    base = ROOT / "src" / Path(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def _saley_imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name.startswith("saley")}
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("saley"):
            found.add(node.module)
            found |= {f"{node.module}.{a.name}" for a in node.names}
    return found


def web_import_closure() -> dict[str, Path]:
    todo = [f"saley.web.{p.stem}" for p in (ROOT / "src" / "saley" / "web").glob("*.py")]
    seen: dict[str, Path] = {}
    while todo:
        name = todo.pop()
        path = _module_file(name)
        if path is None or name in seen:
            continue
        seen[name] = path
        todo += sorted(_saley_imports(path))
    return seen


def test_web_process_has_no_write_credential(setting: Any) -> None:
    """R2-G01: the web has no writer import or write scope; the writer refuses in the web."""
    # 1. Import graph: nothing the web can reach builds a write client.
    closure = web_import_closure()
    assert "saley.web.act" in closure and "saley.web.app" in closure
    banned = {"saley.sales.writer", "saley.sales.outbox", "saley.worker.jobs"}
    assert not banned & set(closure), sorted(banned & set(closure))
    write_scope = re.compile(r"auth/spreadsheets(?![./\w])")
    for name, path in closure.items():
        assert not write_scope.search(path.read_text(encoding="utf-8")), name
    holders = [
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "src").rglob("*.py")
        if write_scope.search(p.read_text(encoding="utf-8"))
    ]
    assert holders == ["src/saley/sales/writer.py"]

    # 2. Web settings: the web needs the read-only database URL and no key file.
    assert Settings.model_fields["google_service_account_file"].default == ""
    from saley.core.settings import SERVICE_REQUIREMENTS

    assert SERVICE_REQUIREMENTS["web"] == ("database_url_ro",)

    # 3. Compose: the web service mounts no key file and clears the key path.
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    web = compose["services"]["web"]
    assert not web.get("volumes")
    assert web["environment"]["GOOGLE_SERVICE_ACCOUNT_FILE"] == ""
    assert "spreadsheets" not in str(web)
    assert compose["services"]["worker"]["volumes"], "the worker is the one that mounts the key"

    # 4. The writer refuses in any process that is not the worker, even with full settings.
    setting("GTM_SHEET_ID", "sheet-id")
    setting("GOOGLE_SERVICE_ACCOUNT_FILE", "C:/keys/never-read.json")
    for name in ("unknown", "web", "dev"):
        set_process(name)
        with pytest.raises(writer.WriteCredentialRefused):
            writer.build_write_sheet(lambda: pytest.fail("a client was built"))
    set_process("unknown")


def test_worker_builds_the_write_client_with_the_write_scope(setting: Any) -> None:
    """Only in the worker, only on the copy, only with the spreadsheets scope."""
    from saley.core.settings import WORKER_PROCESS, MissingSettings

    class FakeClient:
        def open_by_key(self, key: str) -> Any:
            return {"opened": key}

    setting("GTM_SHEET_ID", "sheet-id")
    setting("GOOGLE_SERVICE_ACCOUNT_FILE", "C:/keys/never-read.json")
    set_process(WORKER_PROCESS)
    try:
        sheet = writer.build_write_sheet(FakeClient)
        assert sheet.spreadsheet == {"opened": "sheet-id"}
        setting("GTM_SHEET_IS_COPY", "false")
        with pytest.raises(Exception, match="writes only the sheet copy"):
            writer.build_write_sheet(FakeClient)
        setting("GTM_SHEET_IS_COPY", "true")
        setting("GOOGLE_SERVICE_ACCOUNT_FILE", "")
        with pytest.raises(MissingSettings):
            writer.build_write_sheet(FakeClient)
    finally:
        set_process("unknown")


class _Worksheet:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def get(self, rng: str, **kwargs: Any) -> list[list[Any]]:
        self.calls.append(("get", rng, str(kwargs["value_render_option"])))
        return [["value"]]

    def update(self, values: Any, rng: str, **kwargs: Any) -> None:
        self.calls.append(("update", rng, values, str(kwargs["value_input_option"])))

    def get_notes(self, grid_range: str) -> list[list[str]]:
        self.calls.append(("get_notes", grid_range))
        return [["old note"]]

    def update_note(self, rng: str, content: str) -> None:
        self.calls.append(("update_note", rng, content))

    def col_values(self, index: int, **_kwargs: Any) -> list[str]:
        self.calls.append(("col_values", index))
        return ["Name", "Ann", "Alex Search"]


class _Spreadsheet:
    def __init__(self) -> None:
        self.ws = _Worksheet()
        self.added: list[str] = []
        self.deleted: list[Any] = []

    def worksheet(self, title: str) -> _Worksheet:
        return self.ws

    def add_worksheet(self, title: str, rows: int, cols: int) -> None:
        self.added.append(title)

    def del_worksheet(self, ws: Any) -> None:
        self.deleted.append(ws)


def test_gspread_sheet_uses_a1_cells_and_guards_tabs() -> None:
    spreadsheet = _Spreadsheet()
    sheet = writer.GspreadSheet(spreadsheet, "SALEY_TEST")
    assert sheet.read_cell("Outreach PoCs", 3, 27) == "value"
    sheet.write_cell("Outreach PoCs", 3, 27, "booked")
    assert sheet.read_note("Outreach PoCs", 3, 2) == "old note"
    sheet.write_note("Outreach PoCs", 3, 2, "text")
    assert sheet.find_row("Outreach PoCs", 2, "alex search") == 3
    assert sheet.find_row("Outreach PoCs", 2, "alex search", after=3) is None
    assert sheet.read_header("Outreach PoCs") == [["value"]]
    calls = spreadsheet.ws.calls
    assert ("get", "AB3", "UNFORMATTED_VALUE") in calls
    assert ("update", "AB3", [["booked"]], "USER_ENTERED") in calls
    assert ("update_note", "C3", "text") in calls
    sheet.add_tab("SALEY_TEST")
    sheet.delete_tab("SALEY_TEST")
    assert spreadsheet.added == ["SALEY_TEST"] and len(spreadsheet.deleted) == 1
    with pytest.raises(writer.WriteCredentialRefused):
        sheet.delete_tab("Outreach PoCs")
    with pytest.raises(writer.WriteCredentialRefused):
        sheet.add_tab("Outreach PoCs")


def test_fake_sheet_tabs_and_missing_cells() -> None:
    sheet = FakeSheet({"A": [["h"]]})
    sheet.add_tab("SALEY_TEST", rows=3, cols=2)
    sheet.write_cell("SALEY_TEST", 5, 3, "x")  # beyond the grid: it grows
    assert sheet.read_cell("SALEY_TEST", 5, 3) == "x"
    assert sheet.read_cell("SALEY_TEST", 99, 0) is None
    with pytest.raises(ValueError, match="exists"):
        sheet.add_tab("SALEY_TEST")
    sheet.delete_tab("SALEY_TEST")
    with pytest.raises(KeyError):
        sheet.read_cell("SALEY_TEST", 1, 0)


# ------------------------------------------------------------------ R2-S01


ALLOWED = {
    "first_contact": ["TRUE", "FALSE", "VR", "Sid"],
    "first_contact_type": ["LinkedIn", "Email", "Call"],
    "first_contact_date": "date",
    "sid_li_added": ["Sent", "Connected", "Not sent"],
    "li_connected_date": "date",
    "meeting_date": "date",
    "meeting_status": ["booked", "completed", "no show"],
}


def test_parity_after_random_writes_and_undos(
    synced: Session, fake_sheet: FakeSheet, client: TestClient
) -> None:
    """R2-S01: 10 random allowed writes and 5 undos, then a sync from the sheet: parity holds."""
    from saley.intel.parity import run_parity
    from saley.qa.sheet_verify import verify

    rng = random.Random(20261019)
    people = [r for r in synced.execute(text("select id from contact order by id")).scalars()]
    pairs = rng.sample([(p, role) for p in people for role in ALLOWED], 10)
    today = today_ist()
    written: list[int] = []
    for person, role in pairs:
        choice = ALLOWED[role]
        value = (
            (today - timedelta(days=rng.randint(0, 30))).isoformat()
            if choice == "date"
            else rng.choice(choice)
        )
        user = rng.choice(["sid", "vaishnavi", "kushal"])
        result = sql_act(synced, user, [{"role": role, "value": value}], [person])
        assert result["ok"], result
        written.append(result["items"][0]["write_id"])
        assert push_pending(fake_sheet, now=now_ist()).done == 1
    for write_id in rng.sample(written, 5):
        assert undo(synced, "kushal", write_id)["ok"]
        assert push_pending(fake_sheet, now=now_ist()).done == 1

    raw = fake_sheet.raw()
    run_sync(synced, build_tabs(raw), now_ist())
    synced.commit()
    results = run_parity(synced, build_tabs(raw))
    assert results and all(r.ok for r in results), [r.metric for r in results if not r.ok]
    report = verify(raw, today, lambda path: client.get(path).json())
    assert report.ok, [row for row in report.rows if not row[3]]
    states = synced.execute(text("select state, count(*) from sync_outbox group by state")).all()
    assert dict(states) == {"done": 15}


def test_matrix_rows_for_this_file_exist() -> None:
    """Every test the R2 matrix names for the writer exists with that exact name."""
    matrix = yaml.safe_load((ROOT / "tests" / "matrix" / "R2.yaml").read_text(encoding="utf-8"))
    names = {
        m.group(1)
        for row in matrix["rows"]
        for m in re.finditer(r"tests/test_writer\.py::(\w+)", row["command"])
    }
    assert names and names <= set(globals()), sorted(names - set(globals()))


def test_poc_roles_have_one_writer_lock_set() -> None:
    """The write whitelist in SQL equals columns.WRITABLE_EXISTING (one list, two layers)."""
    from saley.sales.columns import WRITABLE_EXISTING

    assert set(ALLOWED) == set(WRITABLE_EXISTING)
    assert {r.name for r in POC_ROLES} >= set(ALLOWED)


# ------------------------------------------------------------------ R2-W02 (scratch-tab round trip)


class _Refused(Exception):
    """Looks like gspread's APIError for a 403: it has `response.status_code`."""

    def __init__(self) -> None:
        super().__init__("The caller does not have permission")
        self.response = type("R", (), {"status_code": 403})()


def _real_tabs() -> FakeSheet:
    return FakeSheet({"Outreach PoCs": [["Name"], ["Ann"]]})


def test_roundtrip_passes_on_a_fake_sheet_and_deletes_the_tab() -> None:
    sheet = _real_tabs()
    report = roundtrip.run_roundtrip(sheet, "SALEY_TEST")
    assert report.ok, report.render()
    metrics = [line[0] for line in report.lines]
    assert metrics == [
        "header row found",
        "locked columns mapped",
        "person row found",
        "cell before write",
        "cell after write",
        "name note after write",
        "cell still holds the write",
        "cell after undo",
        "name note after undo",
        "scratch tab deleted",
    ]
    assert list(sheet.tabs) == ["Outreach PoCs"]  # the scratch tab is gone
    assert sheet.tabs["Outreach PoCs"] == [["Name"], ["Ann"]]  # the real tab is untouched
    assert all(call[1] == "SALEY_TEST" for call in sheet.calls if call[0].startswith("write"))
    assert report.render()[-1] == "PASS"
    assert report.render()[0].endswith("· OK")


def test_roundtrip_note_and_undo_follow_the_signature_format() -> None:
    class Spy(FakeSheet):
        last: dict[tuple[str, int, int], str]

        def delete_tab(self, title: str) -> None:
            self.last = dict(self.notes)
            super().delete_tab(title)

    sheet = Spy()
    assert roundtrip.run_roundtrip(sheet, "SALEY_TEST").ok
    (note,) = sheet.last.values()
    first, second = note.splitlines()
    assert first.startswith("Saley · LI Connected Date = ") and first.endswith("· d0")
    assert second.startswith("Saley · undone d0 · Saley test · dashboard")


def test_roundtrip_deletes_the_tab_when_a_step_fails() -> None:
    class Broken(FakeSheet):
        def write_note(self, tab: str, row: int, col: int, text: str) -> None:
            raise RuntimeError("quota exceeded, Bearer ya29.SECRET")

    sheet = Broken()
    report = roundtrip.run_roundtrip(sheet, "SALEY_TEST")
    assert not report.ok and sheet.tabs == {}
    failed = next(line for line in report.lines if line[0] == "every step runs")
    assert "RuntimeError" in failed[2] and "ya29.SECRET" not in failed[2]
    assert report.lines[-1][:3] == ("scratch tab deleted", "yes", "yes")


def test_roundtrip_reports_a_failed_delete_and_does_not_hide_it() -> None:
    class Stuck(FakeSheet):
        def delete_tab(self, title: str) -> None:
            raise RuntimeError("busy")

    report = roundtrip.run_roundtrip(Stuck(), "SALEY_TEST")
    assert not report.ok
    assert report.lines[-1][0] == "scratch tab deleted" and "RuntimeError" in report.lines[-1][2]


def test_roundtrip_leaves_a_foreign_tab_alone_and_removes_its_own_leftover() -> None:
    foreign = FakeSheet({"SALEY_TEST": [["my work"]]})
    report = roundtrip.run_roundtrip(foreign, "SALEY_TEST")
    assert not report.ok and foreign.tabs == {"SALEY_TEST": [["my work"]]}
    assert foreign.calls == [("read_cell", "SALEY_TEST", "1", "0")]  # nothing was written

    leftover = FakeSheet({"SALEY_TEST": [[roundtrip.MARKER]]})
    report = roundtrip.run_roundtrip(leftover, "SALEY_TEST")
    assert report.ok and leftover.tabs == {}
    assert report.lines[0][0] == "leftover scratch tab removed"


def test_roundtrip_a_google_403_on_the_first_write_is_not_configured() -> None:
    class Locked(FakeSheet):
        def add_tab(self, title: str, rows: int = 50, cols: int = 10) -> None:
            raise _Refused

    sheet = Locked()
    with pytest.raises(roundtrip.NotConfigured, match="Editor"):
        roundtrip.run_roundtrip(sheet, "SALEY_TEST")
    assert sheet.tabs == {}


def test_roundtrip_a_google_403_after_the_first_write_is_a_failure(setting: Any) -> None:
    """M9: the tab was made, so the right exists. A later 403 is a real fault: FAIL, exit 1."""

    class Flaky(FakeSheet):
        def write_row(self, tab: str, row: int, values: list[str]) -> None:
            raise _Refused

    setting("SHEET_WRITE_ENABLED", "true")
    sheet = Flaky()
    report = roundtrip.run_roundtrip(sheet, "SALEY_TEST")  # does not raise NotConfigured
    assert not report.ok and sheet.tabs == {}  # the scratch tab is still deleted
    failed = next(line for line in report.lines if line[0] == "every step runs")
    assert "_Refused" in failed[2]
    lines, echo = _out()
    assert roundtrip.execute(echo, Flaky) == 1 and lines[-1] == "FAIL"
    assert not any("NOT CONFIGURED" in line for line in lines)


class _Forgetful(FakeSheet):
    """Drops every write to the person row except the old value (a sheet that loses writes)."""

    def write_cell(self, tab: str, row: int, col: int, value: str) -> None:
        if row == roundtrip.PERSON_ROW and value == today_ist().isoformat():
            return
        super().write_cell(tab, row, col, value)


def test_roundtrip_shows_a_diff_when_the_cell_does_not_hold_the_value() -> None:
    report = roundtrip.run_roundtrip(_Forgetful(), "SALEY_TEST")
    assert not report.ok
    diff = next(line for line in report.lines if not line[3])
    assert diff[0] == "cell after write" and diff[1] != diff[2]
    assert report.render()[-1] == "FAIL" and "DIFF" in "".join(report.render())


def _out() -> tuple[list[str], Any]:
    lines: list[str] = []
    return lines, lines.append


def test_execute_exit_codes(setting: Any) -> None:
    from saley.core.settings import MissingSettings
    from saley.sales.sheet import SheetNotCopy

    lines, echo = _out()
    setting("SHEET_WRITE_ENABLED", "false")
    assert roundtrip.execute(echo, lambda: pytest.fail("no client when writes are off")) == 3
    assert lines[0] == "NOT CONFIGURED: Q-R2-1" and "SHEET_WRITE_ENABLED" in lines[1]

    setting("SHEET_WRITE_ENABLED", "true")

    def missing() -> Any:
        raise MissingSettings("worker needs these settings in .env: GTM_SHEET_ID")

    lines, echo = _out()
    assert roundtrip.execute(echo, missing) == 3
    assert lines[0] == "NOT CONFIGURED: Q-R2-1" and "GTM_SHEET_ID" in lines[1]

    def no_access() -> Any:
        raise _Refused

    lines, echo = _out()
    assert roundtrip.execute(echo, no_access) == 3 and "service account" in lines[1]

    def live() -> Any:
        raise SheetNotCopy("GTM_SHEET_IS_COPY is false")

    lines, echo = _out()
    assert roundtrip.execute(echo, live) == 1 and lines == ["Refused: GTM_SHEET_IS_COPY is false"]

    def broken() -> Any:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        roundtrip.execute(_out()[1], broken)

    lines, echo = _out()
    assert roundtrip.execute(echo, FakeSheet) == 0
    assert lines[-1] == "PASS" and all(line.endswith(" · OK") for line in lines[:-1])

    lines, echo = _out()
    assert roundtrip.execute(echo, _Forgetful) == 1 and lines[-1] == "FAIL"


def test_the_roundtrip_command_needs_the_flag_and_names_the_open_question(setting: Any) -> None:
    from typer.testing import CliRunner

    from saley.cli import app

    runner = CliRunner()
    refused = runner.invoke(app, ["sheet", "roundtrip"])
    assert refused.exit_code == 1 and "--test-tab" in refused.output
    setting("SHEET_WRITE_ENABLED", "false")
    try:
        pending = runner.invoke(app, ["sheet", "roundtrip", "--test-tab"])
    finally:
        set_process("unknown")
    assert pending.exit_code == 3
    assert "NOT CONFIGURED: Q-R2-1" in pending.output


def test_gspread_row_and_tab_calls() -> None:
    class Ws:
        def __init__(self, title: str) -> None:
            self.title = title
            self.calls: list[tuple[Any, ...]] = []

        def update(self, values: Any, rng: str, **kwargs: Any) -> None:
            self.calls.append((rng, values, str(kwargs["value_input_option"])))

    class Book(_Spreadsheet):
        def __init__(self) -> None:
            super().__init__()
            self.ws = Ws("SALEY_TEST")  # type: ignore[assignment]

        def worksheets(self) -> list[Any]:
            return [self.ws]

    book = Book()
    sheet = writer.GspreadSheet(book, "SALEY_TEST")
    sheet.write_row("SALEY_TEST", 2, ["a", "b", "c"])
    assert book.ws.calls == [("A2:C2", [["a", "b", "c"]], "USER_ENTERED")]  # type: ignore[attr-defined]
    assert sheet.tab_exists("SALEY_TEST") and not sheet.tab_exists("Other")


def test_fake_sheet_write_row_stores_typed_cells() -> None:
    sheet = FakeSheet()
    sheet.add_tab("T", rows=2, cols=3)
    sheet.write_row("T", 1, ["x", "2026-01-05", "TRUE"])
    assert [sheet.read_cell("T", 1, c) for c in range(3)] == ["x", 46027, True]
    assert sheet.tab_exists("T") and not sheet.tab_exists("U")


# ------------------------------------------------------------------ R5 T9 review: test rows


def test_worker_push_never_touches_a_saley_test_row_but_push_selected_does(
    synced: Session, fake_sheet: FakeSheet
) -> None:
    """The T9 rows (`saley-test|`) are never pushed by the worker, even when due."""
    from saley.sales.outbox import push_selected

    alex = contact_id(synced, "Alex Search")
    sql_act(
        synced, "sid", [{"role": "li_connected_date", "value": today_ist().isoformat()}], [alex]
    )
    synced.execute(text("update sync_outbox set row_key = 'saley-test|alex'"))
    synced.commit()
    ids = [r[0] for r in synced.execute(text("select id from sync_outbox")).all()]
    assert len(ids) == 1
    rows_before = [list(r) for r in fake_sheet.tabs[POC]]

    stats = push_pending(fake_sheet, now=now_ist() + timedelta(days=1))

    assert (stats.done, stats.failed, stats.retry) == (0, 0, 0)
    assert fake_sheet.tabs[POC] == rows_before
    synced.rollback()
    state = synced.execute(text("select state from sync_outbox")).scalar_one()
    assert state == "pending"

    selected = push_selected(fake_sheet, ids, titles={"poc": POC})
    assert selected.done + selected.failed == 1  # only the roundtrip path handles it
