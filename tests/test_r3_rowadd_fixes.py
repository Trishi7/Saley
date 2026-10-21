"""R3 pipeline stage 5, part 2: add-to-pipeline fixes (A7 undo, A8 signature, A9 duplicates).

The sheet is a FakeSheet; the real gspread writer is never called.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from saley.core.clock import now_ist
from saley.core.models import Account, SearchHit
from saley.sales import rowadd
from saley.sales.outbox import NO_NOTE, push_pending
from saley.sales.parse import norm_name
from saley.sales.writer import FakeSheet
from saley.web import team
from saley.web import words as w
from saley.web.app import create_app
from tests.conftest import SignedClient, sql_row_add
from tests.test_rowadd import LINK, POC, add, col, names, undo

H = {"X-Saley-Act": "1"}


@pytest.fixture
def client(synced: Session) -> Iterator[TestClient]:
    with SignedClient(create_app()) as c:
        yield c


@pytest.fixture
def company(synced: Session) -> int:
    found = synced.scalar(select(Account.id).where(Account.name == "AI4Bharat"))
    assert found is not None
    synced.add(SearchHit(query="q", engine="duckduckgo", url=LINK, title="P", account_id=found))
    synced.commit()
    return found


def other_company(db: Session) -> int:
    found = db.scalar(select(Account.id).where(Account.name == "Sarvam AI"))
    assert found is not None
    return found


def state_of(db: Session, write_id: int) -> tuple[str, str | None]:
    db.expire_all()
    row = db.execute(
        text("select state, error from sync_outbox where decision_id = :d"), {"d": write_id}
    ).one()
    return row[0], row[1]


# ------------------------------------------------------------------ A9 duplicates


def test_the_same_person_in_another_company_is_refused(synced: Session, company: int) -> None:
    assert add(synced, company)["ok"]
    assert add(synced, other_company(synced))["code"] == "duplicate"  # the key is global


def test_a_person_the_sheet_already_has_elsewhere_is_refused(synced: Session, company: int) -> None:
    result = add(synced, company, name="Dinesh Tewari")  # he sits at Google DeepMind
    assert result == {"ok": False, "code": "duplicate"}


def test_the_key_is_casefold_like_the_sheet_sync(
    synced: Session, client: TestClient, company: int
) -> None:
    synced.execute(
        text("update contact set name = 'Straße Weber', name_key = :k where name = 'Ro One'"),
        {"k": norm_name("Straße Weber")},
    )
    synced.commit()
    assert norm_name("Straße Weber") == "strasse weber"  # casefold, not lower
    # SQL on its own (the chat bot's door): the sharp s is matched too.
    assert add(synced, company, name="STRASSE WEBER")["code"] == "duplicate"
    assert add(synced, company, name="Straße  Weber")["code"] == "duplicate"
    # The web route sends the Python key: same answer.
    body = {
        "action": "row_add",
        "target_ids": [company],
        "payload": {"fields": [{"role": "name", "value": "Strasse WEBER"}]},
    }
    refused = client.post("/api/act", json=body, headers=H)
    assert refused.status_code == 409 and refused.json()["detail"] == w.ERR_DUPLICATE
    body["payload"]["fields"][0]["value"] = "Strasse Weber Junior"
    assert client.post("/api/act", json=body, headers=H).status_code == 200


def test_a_failed_or_cancelled_add_does_not_block_the_name(synced: Session, company: int) -> None:
    key = "k" * 20
    first = sql_row_add(synced, "sid", company, {"name": "Priya Sharma"}, key=key)
    write_id = first["items"][0]["write_id"]
    assert add(synced, other_company(synced))["code"] == "duplicate"  # waiting to be added
    synced.execute(
        text("update sync_outbox set state = 'failed' where decision_id = :d"), {"d": write_id}
    )
    synced.commit()
    again = sql_row_add(synced, "sid", company, {"name": "Priya Sharma"}, key=key)
    assert again["ok"] is True and again["items"][0]["already"] is False  # not "already"
    assert again["items"][0]["write_id"] != write_id

    synced.execute(
        text("update sync_outbox set state = 'cancelled' where decision_id = :d"),
        {"d": again["items"][0]["write_id"]},
    )
    synced.commit()
    assert add(synced, other_company(synced))["ok"] is True  # a cancelled add counts for nothing


def test_a_live_add_still_blocks_a_twin(synced: Session, company: int) -> None:
    write_id = add(synced, company)["items"][0]["write_id"]
    assert add(synced, other_company(synced))["code"] == "duplicate"
    assert undo(synced, "sid", write_id)["ok"] is True
    assert add(synced, other_company(synced))["ok"] is True


# ------------------------------------------------------------------ A8 signature note


class NoteDown(FakeSheet):
    """The notes API fails on every call (or only on the first `fail` calls)."""

    def __init__(self, tabs: dict[str, list[list[Any]]], fail: int = 99) -> None:
        super().__init__(tabs)
        self.fail = fail
        self.attempts = 0

    def write_note(self, tab: str, row: int, col: int, text: str) -> None:
        self.attempts += 1
        if self.attempts <= self.fail:
            raise RuntimeError("notes API is down")
        super().write_note(tab, row, col, text)


def test_a_note_that_fails_twice_marks_the_add_failed_and_visible(
    synced: Session, client: TestClient, fake_sheet: FakeSheet, company: int
) -> None:
    sheet = NoteDown(fake_sheet.raw())
    write_id = add(synced, company)["items"][0]["write_id"]
    stats = push_pending(sheet, now=now_ist())
    assert (stats.done, stats.failed) == (0, 1)
    assert sheet.attempts == 2  # tried once, then once more
    assert state_of(synced, write_id) == ("failed", NO_NOTE)
    assert "Priya Sharma" in names(sheet)  # the person is in the sheet
    assert synced.scalar(text("select count(*) from sync_audit")) == 1
    status = client.get("/api/status").json()
    assert status["failed"] == 1 and "failed" in status["write_note"]
    assert team.sync_reason(NO_NOTE, "failed") == w.SYNC_REASON_NOTE


def test_a_note_that_works_on_the_second_try_is_done(
    synced: Session, fake_sheet: FakeSheet, company: int
) -> None:
    sheet = NoteDown(fake_sheet.raw(), fail=1)
    write_id = add(synced, company)["items"][0]["write_id"]
    stats = push_pending(sheet, now=now_ist())
    assert (stats.done, stats.failed) == (1, 0) and sheet.attempts == 2
    assert state_of(synced, write_id) == ("done", None)
    row = sheet.find_row(POC, col("name"), "Priya Sharma")
    assert "row added" in sheet.read_note(POC, row, col("name"))


def test_undo_of_an_unsigned_row_is_refused_by_the_worker(
    synced: Session, fake_sheet: FakeSheet, company: int
) -> None:
    """L4: a row without the Saley signature note is never deleted, even by its own undo."""
    sheet = NoteDown(fake_sheet.raw())
    write_id = add(synced, company)["items"][0]["write_id"]
    push_pending(sheet, now=now_ist())
    result = undo(synced, "sid", write_id)
    assert result["ok"] is True and result["cancelled"] is False
    # Round 5: the plan carries the "appended" mark, so the undo tries to sign first. The notes
    # API is down: it is tried again later, nothing is deleted.
    stats = push_pending(sheet, now=now_ist())
    assert (stats.done, stats.failed, stats.retry) == (0, 0, 1)
    # Without the mark (D105) the row is not provably ours: refused for good.
    synced.execute(
        text(
            "update decision set before = (cast(before as jsonb) - 'appended_at')::text "
            "where kind = 'row_add'"
        )
    )
    synced.execute(text("update sync_outbox set next_at = now() - interval '1 minute'"))
    synced.commit()
    stats = push_pending(sheet, now=now_ist() + timedelta(hours=1))
    assert (stats.done, stats.failed) == (0, 1)
    assert "signature" in state_of_restore(synced, write_id)
    assert "Priya Sharma" in names(sheet)
    assert not [c for c in sheet.calls if c[0] == "delete_row"]


def test_undo_of_an_add_that_never_wrote_a_row_stays_quiet(
    synced: Session, fake_sheet: FakeSheet, company: int
) -> None:
    """A failed add with no plan (refused before any write) has nothing to remove."""
    write_id = add(synced, company)["items"][0]["write_id"]
    synced.execute(
        text("update sync_outbox set state = 'failed', error = 'refused' where decision_id = :d"),
        {"d": write_id},
    )
    synced.commit()
    assert undo(synced, "sid", write_id)["ok"] is True
    assert push_pending(fake_sheet, now=now_ist()).done == 0  # no restore was queued


# ------------------------------------------------------------------ A7 undo and delete


def test_undo_is_busy_while_the_worker_holds_the_row(
    synced: Session, client: TestClient, engine: Engine, company: int
) -> None:
    write_id = add(synced, company)["items"][0]["write_id"]
    with engine.connect() as worker:
        worker.execute(
            text("select id from sync_outbox where decision_id = :d for update"), {"d": write_id}
        )
        assert undo(synced, "sid", write_id) == {"ok": False, "code": "busy"}
        r = client.post(f"/api/undo/{write_id}", headers=H)
        assert r.status_code == 409 and r.json() == {"detail": "Busy. Try again."}
        worker.rollback()
    assert synced.scalar(
        text("select undone_at is null from decision where id = :d"), {"d": write_id}
    )
    assert undo(synced, "sid", write_id)["ok"] is True  # the worker let go: the undo works


class Racing(FakeSheet):
    """After the first check of the row, a person inserts a row above it (the row moves down)."""

    armed = False

    def read_row(self, tab: str, row: int) -> list[Any]:
        values = super().read_row(tab, row)
        if self.armed:
            self.armed = False
            self.tabs[tab].insert(1, [""] * len(self.tabs[tab][0]))
        return values


def test_the_row_is_checked_again_just_before_it_is_deleted(
    synced: Session, fake_sheet: FakeSheet, company: int
) -> None:
    sheet = Racing(fake_sheet.raw())
    write_id = add(synced, company)["items"][0]["write_id"]
    push_pending(sheet, now=now_ist())
    rows = len(sheet.tabs[POC])
    undo(synced, "sid", write_id)
    sheet.armed = True
    stats = push_pending(sheet, now=now_ist())
    assert (stats.done, stats.failed) == (0, 1)
    assert "moved or changed" in state_of_restore(synced, write_id)
    assert len(sheet.tabs[POC]) == rows + 1  # only the other person's new row was added
    assert "Priya Sharma" in names(sheet)
    assert not [c for c in sheet.calls if c[0] == "delete_row"]


def state_of_restore(db: Session, write_id: int) -> str:
    db.expire_all()
    return db.scalar(
        text(
            "select o.error from sync_outbox o join decision d on d.id = o.decision_id "
            "where d.undo_of = :w"
        ),
        {"w": write_id},
    )


def test_remove_added_deletes_when_nothing_moved(
    synced: Session, fake_sheet: FakeSheet, company: int
) -> None:
    add(synced, company)
    push_pending(fake_sheet, now=now_ist())
    plan_text = synced.scalar(text("select before from decision where kind = 'row_add'"))
    plan = rowadd.read_plan(plan_text)
    from saley.sales.columns import POC_ROLES
    from saley.sales.sheet import find_header

    header, resolution = find_header(fake_sheet.read_header(POC), POC_ROLES)
    assert header == 0
    assert rowadd.remove_added(fake_sheet, POC, resolution, plan) is True
    assert [c for c in fake_sheet.calls if c[0] == "delete_row"] == [
        ("delete_row", POC, str(plan.row))
    ]
    assert rowadd.remove_added(fake_sheet, POC, resolution, plan) is False  # already gone
