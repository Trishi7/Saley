"""Add-to-pipeline (`row_add`, D76): the SQL door, the worker, undo, the scratch-tab round trip.

Writes run only against a FakeSheet. The real gspread writer is never called here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from saley.cli import app
from saley.core.clock import now_ist
from saley.core.models import Account, SearchHit
from saley.core.settings import get_settings, set_process
from saley.sales import roundtrip, rowadd, writer
from saley.sales.columns import NEW_ROW_ROLES, POC_ROLES, WritesRefused, assert_writable, resolve
from saley.sales.outbox import push_pending
from saley.sales.writer import FakeSheet
from saley.web import words as w
from saley.web.app import create_app
from tests.conftest import (
    POC_HEADERS,
    POC_ROLE_ORDER,
    SignedClient,
    contact_id,
    sql_call,
    sql_row_add,
)

POC = "Outreach PoCs"
H = {"X-Saley-Act": "1"}
LINK = "https://www.linkedin.com/in/priya-sharma-1a2b3c"


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


def col(role: str) -> int:
    return POC_ROLE_ORDER.index(role)


def add(db: Session, company: int, name: str = "Priya Sharma", **extra: str) -> dict[str, Any]:
    return sql_row_add(db, "sid", company, {"name": name, "designation": "Head of Data", **extra})


def undo(db: Session, user: str, write_id: int) -> dict[str, Any]:
    return sql_call(db, "select saley_undo(:u, :id, 'dashboard')", u=user, id=write_id)


def names(sheet: FakeSheet) -> list[str]:
    return [str(r[col("name")]) for r in sheet.tabs[POC][1:] if len(r) > col("name")]


# ------------------------------------------------------------------ the SQL door


def test_the_function_writes_one_decision_and_one_append_row(synced: Session, company: int) -> None:
    result = add(synced, company, li_url=LINK)
    assert result["ok"] and result["items"][0]["already"] is False
    decision = synced.execute(
        text(
            "select kind, role, row_key, after, surface, undo_until > now(), tab "
            "from decision where kind = 'row_add'"
        )
    ).one()
    write_id = result["items"][0]["write_id"]
    assert tuple(decision) == (
        "row_add",
        "row_add",
        f"new:{write_id}",
        "Priya Sharma",
        "dashboard",
        True,
        "poc",
    )
    outbox = synced.execute(text("select op, state, role, value from sync_outbox")).one()
    assert outbox.op == "append" and outbox.state == "pending"
    import json

    assert json.loads(outbox.value) == {
        "name": "Priya Sharma",
        "designation": "Head of Data",
        "li_url": LINK,
        "company": "AI4Bharat",
    }


def test_the_same_click_twice_is_one_row(synced: Session, company: int) -> None:
    first = sql_row_add(synced, "sid", company, {"name": "Priya Sharma"}, key="k" * 20)
    again = sql_row_add(synced, "sid", company, {"name": "Priya Sharma"}, key="k" * 20)
    assert again["items"][0]["already"] is True
    assert again["items"][0]["write_id"] == first["items"][0]["write_id"]
    assert synced.scalar(text("select count(*) from sync_outbox")) == 1


def test_a_duplicate_name_and_company_is_refused(synced: Session, company: int) -> None:
    assert add(synced, company)["ok"]
    twin = add(synced, company, name="  PRIYA   sharma ")
    assert twin == {"ok": False, "code": "duplicate", "by": "sid"}  # D111: who added it
    dinesh = synced.scalar(select(Account.id).where(Account.name == "Google DeepMind"))
    assert add(synced, dinesh, name="Dinesh Tewari")["code"] == "duplicate"  # already in the sheet
    # A9: the sync key is global, so the same name at another company is refused too.
    assert add(synced, dinesh, name="Priya Sharma")["code"] == "duplicate"
    assert add(synced, dinesh, name="Priya Other")["ok"] is True
    assert synced.scalar(text("select count(*) from sync_outbox")) == 2


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"name": "=HYPERLINK(1)"}, "bad_value"),
        ({"name": "+91 99"}, "bad_value"),
        ({"name": "-x"}, "bad_value"),
        ({"name": "@sum"}, "bad_value"),
        ({"name": "A", "designation": " =1+1"}, "bad_value"),
        ({"name": "A\u0007B"}, "bad_value"),
        ({"name": ""}, "bad_value"),
        ({"name": "x" * 201}, "bad_value"),
        ({"designation": "No name"}, "bad_value"),
        ({"name": "A", "li_url": "https://evil.example/in/x"}, "bad_value"),
        ({"name": "A", "li_url": "https://www.linkedin.com/in/not-from-a-search"}, "bad_value"),
        ({"name": "A", "li_url": "https://www.linkedin.com/company/ai4bharat"}, "bad_value"),
        ({"name": "A", "sr_no": "99"}, "locked"),
        ({"name": "A", "company": "Other Co"}, "locked"),
        ({"name": "A", "meeting_status": "booked"}, "locked"),
        ({"name": "A", "connected_with_vr": "x"}, "locked"),
        ({"name": "A", "next_steps": "x"}, "locked"),
        ({"name": "A", "deal_size": "USD 5"}, "locked"),
        ({"name": "A", "email": "a@b.example"}, "locked"),  # EMAIL_WRITE_ALLOWED is off
    ],
)
def test_the_function_refuses_bad_cells(
    synced: Session, company: int, fields: dict[str, str], code: str
) -> None:
    result = sql_row_add(synced, "sid", company, fields)
    assert result["ok"] is False and result["code"] == code, result
    assert synced.scalar(text("select count(*) from sync_outbox")) == 0
    assert synced.scalar(text("select count(*) from decision")) == 0


def test_email_on_a_new_row_needs_the_email_flag(synced: Session, company: int) -> None:
    result = sql_row_add(
        synced, "sid", company, {"name": "A", "email": "a@b.example"}, allow_email=True
    )
    assert result["ok"] is True


def test_the_function_refuses_odd_requests(synced: Session, company: int) -> None:
    assert sql_row_add(synced, "ghost", company, {"name": "A"})["code"] == "not_allowed"
    assert sql_row_add(synced, "sid", 999999, {"name": "A"})["code"] == "no_target"
    dup = sql_call(
        synced,
        "select saley_act('sid', 'dashboard', 'row_add', cast(:t as jsonb), "
        "cast(:p as jsonb), :k, 2, 10, false)",
        t=f"[{company}]",
        p='{"fields": [{"role": "name", "value": "A"}, {"role": "name", "value": "B"}]}',
        k="k" * 20,
    )
    assert dup["code"] == "bad_value"
    for targets, fields in (
        (f"[{company}, {company + 1}]", '[{"role": "name", "value": "A"}]'),
        (f"[{company}]", "[]"),
        (f"[{company}]", '"text"'),
        (f"[{company}]", "[" + ",".join(['{"role": "name", "value": "A"}'] * 13) + "]"),
        ('["1 or 1=1"]', '[{"role": "name", "value": "A"}]'),
    ):
        result = sql_call(
            synced,
            "select saley_act('sid', 'dashboard', 'row_add', cast(:t as jsonb), "
            "cast(:p as jsonb), :k, 2, 10, false)",
            t=targets,
            p='{"fields": ' + fields + "}",
            k="k" * 20,
        )
        assert result["code"] in ("bad_request", "bad_value"), (targets, result)
    assert synced.scalar(text("select count(*) from sync_outbox")) == 0


def test_text_with_sql_in_a_name_is_only_data(synced: Session, company: int) -> None:
    result = add(synced, company, name="Robert'); DROP TABLE contact;--")
    assert result["ok"] is True
    assert synced.scalar(text("select count(*) from contact")) == 7


# ------------------------------------------------------------------ the worker


def test_sr_no_is_the_highest_plus_one_at_push_time(
    synced: Session, fake_sheet: FakeSheet, company: int
) -> None:
    add(synced, company)
    fake_sheet.write_cell(POC, 3, col("sr_no"), "40")  # a human renumbered while we waited
    push_pending(fake_sheet, now=now_ist())
    row = fake_sheet.find_row(POC, col("name"), "Priya Sharma")
    assert row is not None and fake_sheet.read_cell(POC, row, col("sr_no")) == 41


def test_an_unmapped_locked_header_stops_the_add(
    synced: Session, fake_sheet: FakeSheet, company: int
) -> None:
    add(synced, company)
    fake_sheet.tabs[POC][0][col("deal_size")] = "Renamed by a human"
    stats = push_pending(fake_sheet, now=now_ist())
    assert stats.failed == 1 and names(fake_sheet).count("Priya Sharma") == 0


def test_the_worker_refuses_a_twin_a_human_added(
    synced: Session, fake_sheet: FakeSheet, company: int
) -> None:
    add(synced, company)
    row = [""] * len(POC_HEADERS)
    row[col("company")], row[col("name")], row[col("sr_no")] = "AI4Bharat", "priya sharma", 50
    fake_sheet.tabs[POC].append(row)
    stats = push_pending(fake_sheet, now=now_ist())
    assert stats.failed == 1
    error = synced.scalar(text("select error from sync_outbox"))
    assert "already in the sheet" in error
    assert names(fake_sheet).count("priya sharma") == 1


def test_a_formula_in_the_outbox_never_reaches_the_sheet(
    synced: Session, fake_sheet: FakeSheet, company: int
) -> None:
    add(synced, company)
    synced.execute(
        text("update sync_outbox set value = replace(value, 'Priya Sharma', '=HYPERLINK(1)')")
    )
    synced.commit()
    before = [list(r) for r in fake_sheet.tabs[POC]]
    assert push_pending(fake_sheet, now=now_ist()).failed == 1
    assert fake_sheet.tabs[POC] == before


def test_a_failed_read_back_retries_without_a_second_row(
    synced: Session, fake_sheet: FakeSheet, company: int
) -> None:
    class Lossy(FakeSheet):
        broken = True

        def read_row(self, tab: str, row: int) -> list[Any]:
            values = super().read_row(tab, row)
            if self.broken and row > 8 and any(values):  # M2: the empty row is read first
                self.broken = False
                return values[:2]  # the first read-back is short
            return values

    sheet = Lossy(fake_sheet.raw())
    add(synced, company)
    first = push_pending(sheet, now=now_ist())
    assert first.retry == 1 and names(sheet).count("Priya Sharma") == 1
    synced.execute(text("update sync_outbox set next_at = now() - interval '1 minute'"))
    synced.commit()
    second = push_pending(sheet, now=now_ist())
    assert second.done == 1 and names(sheet).count("Priya Sharma") == 1  # adopted, not repeated


# ------------------------------------------------------------------ undo


def test_undo_before_the_push_cancels_and_the_sheet_is_untouched(
    synced: Session, fake_sheet: FakeSheet, company: int
) -> None:
    write_id = add(synced, company)["items"][0]["write_id"]
    assert undo(synced, "sid", write_id) == {
        "ok": True,
        "write_id": write_id,
        "undo_id": None,
        "cancelled": True,
    }
    assert push_pending(fake_sheet, now=now_ist()).done == 0
    assert "Priya Sharma" not in names(fake_sheet)
    assert add(synced, company)["ok"] is True  # the name is free again


def test_undo_after_the_push_deletes_an_unchanged_row(
    synced: Session, fake_sheet: FakeSheet, company: int
) -> None:
    write_id = add(synced, company)["items"][0]["write_id"]
    push_pending(fake_sheet, now=now_ist())
    rows = len(fake_sheet.tabs[POC])
    assert undo(synced, "kushal", write_id)["ok"] is True
    stats = push_pending(fake_sheet, now=now_ist())
    assert stats.done == 1 and len(fake_sheet.tabs[POC]) == rows - 1
    assert "Priya Sharma" not in names(fake_sheet)
    assert [fake_sheet.notes.get((POC, r, col("name"))) for r in range(2, rows)] == [None] * (
        rows - 2
    )


@pytest.mark.parametrize("change", ["name", "designation", "extra", "sr_no"])
def test_undo_keeps_a_row_a_human_changed(
    synced: Session, fake_sheet: FakeSheet, company: int, change: str
) -> None:
    write_id = add(synced, company)["items"][0]["write_id"]
    push_pending(fake_sheet, now=now_ist())
    row = fake_sheet.find_row(POC, col("name"), "Priya Sharma")
    assert row is not None
    if change == "extra":
        fake_sheet.write_cell(POC, row, col("next_steps"), "Call on Monday")
    else:
        fake_sheet.write_cell(POC, row, col(change), "999" if change == "sr_no" else "Edited")
    undo(synced, "sid", write_id)
    stats = push_pending(fake_sheet, now=now_ist())
    if change in ("name", "sr_no"):
        # The row no longer looks like ours: Saley cannot find it, so it removes nothing.
        assert stats.done == 1 and len(fake_sheet.tabs[POC]) == 9
    else:
        assert stats.failed == 1 and len(fake_sheet.tabs[POC]) == 9
        assert "changed" in synced.scalar(text("select error from sync_outbox order by id desc"))


def test_undo_after_the_window_is_refused(synced: Session, company: int) -> None:
    write_id = add(synced, company)["items"][0]["write_id"]
    synced.execute(text("update decision set undo_until = now() - interval '1 minute'"))
    synced.commit()
    assert undo(synced, "sid", write_id)["code"] == "window_closed"


def test_the_next_sync_removes_a_person_whose_row_was_deleted(
    synced: Session, fake_sheet: FakeSheet, company: int
) -> None:
    from saley.sales.sheet import build_tabs
    from saley.sales.sync import run_sync

    write_id = add(synced, company)["items"][0]["write_id"]
    push_pending(fake_sheet, now=now_ist())
    run_sync(synced, build_tabs(fake_sheet.raw()), now_ist())
    synced.commit()
    assert contact_id(synced, "Priya Sharma")
    undo(synced, "sid", write_id)
    push_pending(fake_sheet, now=now_ist())
    run_sync(synced, build_tabs(fake_sheet.raw()), now_ist())
    synced.commit()
    assert synced.scalar(
        text("select removed_at is not null from contact where name = 'Priya Sharma'")
    )


# ------------------------------------------------------------------ the web route


def post(client: TestClient, company: int, fields: list[dict[str, str]], **extra: Any) -> Any:
    body = {"action": "row_add", "target_ids": [company], "payload": {"fields": fields}, **extra}
    return client.post("/api/act", json=body, headers=H)


def test_the_route_adds_a_row_and_a_second_click_is_the_same_write(
    client: TestClient, synced: Session, company: int
) -> None:
    fields = [
        {"role": "name", "value": "Priya Sharma"},
        {"role": "designation", "value": "Head of Data"},
        {"role": "li_url", "value": LINK},
    ]
    first = post(client, company, fields)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["label"] == "Saved: Person added" and body["already"] is False
    assert body["items"][0]["value"] == "Priya Sharma" and body["undo_until"]
    assert post(client, company, fields).json()["already"] is True
    assert synced.scalar(text("select count(*) from sync_outbox")) == 1
    twin = post(client, company, [{"role": "name", "value": "priya sharma"}])
    assert twin.status_code == 409 and twin.json()["detail"] == "Already added by trishi"  # D111
    undone = client.post(f"/api/undo/{body['write_id']}", headers=H)
    assert undone.status_code == 200 and undone.json()["cancelled"] is True


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ([{"role": "name", "value": "=1+1"}], 422),
        ([{"role": "name", "value": "a\nb"}], 422),
        ([{"role": "name", "value": ""}], 422),
        ([{"role": "name", "value": "A"}, {"role": "name", "value": "B"}], 422),
        ([{"role": "designation", "value": "No name"}], 422),
        (
            [{"role": "name", "value": "A"}, {"role": "li_url", "value": "https://evil.example"}],
            422,
        ),
        ([{"role": "name", "value": "A"}, {"role": "li_url", "value": "javascript:alert(1)"}], 422),
        (
            [
                {"role": "name", "value": "A"},
                {"role": "li_url", "value": "https://www.linkedin.com/in/not-a-hit"},
            ],
            422,
        ),
        ([{"role": "name", "value": "A"}, {"role": "email", "value": "a@b.example"}], 403),
        ([{"role": "name", "value": "A"}, {"role": "email", "value": "not-an-email"}], 422),
        ([{"role": "name", "value": "A"}, {"role": "meeting_status", "value": "booked"}], 403),
        ([{"role": "name", "value": "A"}, {"role": "sr_no", "value": "99"}], 403),
        ([{"role": "name", "value": "A"}, {"role": "connected_with_vr", "value": "x"}], 403),
        ([{"role": "name", "value": "A"}, {"role": "Bad Role!", "value": "x"}], 422),
        ([{"role": "name", "value": "A"}] * 13, 422),
        ([], 422),
    ],
)
def test_the_route_refuses_bad_rows(
    client: TestClient, synced: Session, company: int, fields: list[dict[str, str]], code: int
) -> None:
    assert post(client, company, fields).status_code == code
    assert synced.scalar(text("select count(*) from decision")) == 0


def test_the_route_refuses_odd_bodies(client: TestClient, synced: Session, company: int) -> None:
    fields = [{"role": "name", "value": "A"}]
    assert post(client, company, fields, extra="x").status_code == 422
    assert (
        client.post(
            "/api/act", json={"action": "row_add", "target_ids": [company]}, headers=H
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/act",
            json={
                "action": "row_add",
                "target_ids": [company, company + 1],
                "payload": {"fields": fields},
            },
            headers=H,
        ).status_code
        == 422
    )
    assert post(client, 999999, fields).status_code == 404
    assert (
        client.post(
            "/api/act",
            json={"action": "row_add", "target_ids": [company], "payload": {"fields": fields}},
        ).status_code
        == 403
    )
    cross = {**H, "Sec-Fetch-Site": "cross-site"}
    assert (
        client.post(
            "/api/act",
            json={"action": "row_add", "target_ids": [company], "payload": {"fields": fields}},
            headers=cross,
        ).status_code
        == 403
    )
    away = {**H, "X-Saley-As": "ghost"}
    assert (
        client.post(
            "/api/act",
            json={"action": "row_add", "target_ids": [company], "payload": {"fields": fields}},
            headers=away,
        ).status_code
        == 403
    )
    assert synced.scalar(text("select count(*) from decision")) == 0


# ------------------------------------------------------------------ columns and the port


def test_new_row_roles_follow_docs_10() -> None:
    letters = {r.name for r in POC_ROLES}
    assert letters >= NEW_ROW_ROLES
    resolution = resolve(POC_HEADERS)
    for role in NEW_ROW_ROLES:
        assert_writable(resolution, role, existing_row=False)
    for locked in (
        "meeting_status",
        "connected_with_vr",
        "outreach_step",
        "deal_size",
        "reconnect_date",
        "email_1_sent",
        "next_steps",
    ):
        with pytest.raises(WritesRefused):
            assert_writable(resolution, locked, existing_row=False)
    # an existing row is still locked by the old rule
    with pytest.raises(WritesRefused):
        assert_writable(resolution, "name")


def test_fake_sheet_append_read_and_delete_rows() -> None:
    sheet = FakeSheet({"T": [["Name", "N"], ["a", 1], ["", ""], ["b", 2], ["", ""]]})
    assert sheet.append_row("T", 0, ["c", "3"]) == 5  # below the last keyed row, never in a gap
    assert sheet.read_row("T", 5) == ["c", 3] and sheet.read_row("T", 99) == []
    assert sheet.read_column("T", 0) == ["Name", "a", "", "b", "c"]
    sheet.write_note("T", 5, 0, "five")
    sheet.write_note("T", 4, 0, "four")
    sheet.delete_row("T", 4)
    assert sheet.read_column("T", 0) == ["Name", "a", "", "c"]
    assert sheet.notes == {("T", 4, 0): "five"}
    with pytest.raises(IndexError):
        sheet.delete_row("T", 40)
    assert FakeSheet({"E": []}).append_row("E", 0, ["x"]) == 1


def test_gspread_sheet_appends_below_the_last_row_and_adds_grid_when_needed() -> None:
    class Ws:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []
            self.row_count = 3

        def col_values(self, index: int, **_k: Any) -> list[str]:
            self.calls.append(("col_values", index))
            return ["Name", "a", "", "b"]

        def get(self, rng: str, **_k: Any) -> list[list[Any]]:
            self.calls.append(("get", rng))
            # M2: the row below the last person is empty until the update wrote it.
            return [["x", 1]] if any(c[0] == "update" for c in self.calls) else []

        def update(self, values: Any, rng: str, **_k: Any) -> None:
            self.calls.append(("update", rng, values))

        def add_rows(self, count: int) -> None:
            self.calls.append(("add_rows", count))
            self.row_count += count

        def delete_rows(self, row: int) -> None:
            self.calls.append(("delete_rows", row))

    class Book:
        def __init__(self) -> None:
            self.ws = Ws()

        def worksheet(self, _title: str) -> Ws:
            return self.ws

    book = Book()
    sheet = writer.GspreadSheet(book, "SALEY_TEST")
    assert sheet.append_row("Outreach PoCs", 2, ["1", "Co", "Ann"]) == 5
    sheet.delete_row("Outreach PoCs", 5)
    assert sheet.read_row("Outreach PoCs", 5) == ["x", 1]
    assert sheet.read_column("Outreach PoCs", 2) == ["Name", "a", "", "b"]
    calls = book.ws.calls
    assert ("add_rows", 2) in calls and ("update", "A5:C5", [["1", "Co", "Ann"]]) in calls
    assert ("delete_rows", 5) in calls and ("get", "A5:ZZ5") in calls
    with pytest.raises(writer.FormulaRefused):
        sheet.append_row("Outreach PoCs", 2, ["=1"])


def test_the_signature_line_is_in_the_documented_form() -> None:
    line = writer.row_added_line("Sid", "dashboard", now_ist(), 42)
    assert line.startswith("Saley · row added · Sid · dashboard · ")
    assert line.endswith(" IST · d42")
    assert rowadd.Plan.loads("not json") is None
    assert rowadd.Plan.loads(rowadd.Plan(3, {"name": "A"}, 9).dumps()) == rowadd.Plan(
        3, {"name": "A"}, 9
    )
    with pytest.raises(rowadd.RowAddRefused):
        rowadd.read_plan(None)


# ------------------------------------------------------------------ the scratch-tab round trip


def test_the_row_add_roundtrip_passes_and_deletes_the_tab() -> None:
    sheet = FakeSheet({"Outreach PoCs": [["Name"], ["Ann"]]})
    report = roundtrip.run_roundtrip(sheet, "SALEY_TEST", row_add=True)
    assert report.ok, report.render()
    assert [line[0] for line in report.lines] == [
        "header row found",
        "locked columns mapped",
        "new row below the last person",
        "Sr No is the highest plus one",
        "row read back equals what was written",
        "name note is the signature",
        "same name and company is refused",
        "a step column is not written on a new row",
        "undo removes the row",
        "row is gone",
        "the first person row is untouched",
        "scratch tab deleted",
    ]
    assert list(sheet.tabs) == ["Outreach PoCs"]
    assert sheet.tabs["Outreach PoCs"] == [["Name"], ["Ann"]]
    assert all(c[1] == "SALEY_TEST" for c in sheet.calls if c[0].startswith(("write", "append")))


def test_the_row_add_roundtrip_deletes_the_tab_when_a_step_fails() -> None:
    class Broken(FakeSheet):
        def append_row(self, tab: str, key_col: int, values: list[str]) -> int:
            raise RuntimeError("quota exceeded, Bearer ya29.SECRET")

    sheet = Broken()
    report = roundtrip.run_roundtrip(sheet, "SALEY_TEST", row_add=True)
    assert not report.ok and sheet.tabs == {}
    failed = next(line for line in report.lines if line[0] == "every step runs")
    assert "ya29.SECRET" not in failed[2]
    assert report.lines[-1][:3] == ("scratch tab deleted", "yes", "yes")


def test_the_row_add_roundtrip_command(set_env: Callable[..., None]) -> None:
    runner = CliRunner()
    set_env(SHEET_WRITE_ENABLED="false")
    try:
        result = runner.invoke(app, ["sheet", "roundtrip", "--test-tab", "--row-add"])
    finally:
        set_process("unknown")
    assert result.exit_code == 3 and "NOT CONFIGURED: Q-R2-1" in result.output
    lines: list[str] = []
    set_env(SHEET_WRITE_ENABLED="true")
    assert roundtrip.execute(lines.append, FakeSheet, row_add=True) == 0
    assert lines[-1] == "PASS" and any("name note is the signature" in x for x in lines)
    assert get_settings().sheet_test_tab == "SALEY_TEST"


# ------------------------------------------------------------------ D111 claim (company, name)


def claims(db: Session) -> list[tuple[int, str]]:
    return [tuple(r) for r in db.execute(text("select account_id, name_key from row_add_claim"))]


def test_d111_a_second_add_says_who_added_the_person(
    synced: Session, company: int, client: TestClient
) -> None:
    assert add(synced, company)["ok"]
    assert claims(synced) == [(company, "priya sharma")]
    body = {
        "action": "row_add",
        "target_ids": [company],
        "payload": {"fields": [{"role": "name", "value": "Priya  SHARMA"}]},
    }
    refused = client.post("/api/act", json=body, headers=H)
    assert refused.status_code == 409
    assert (
        refused.json()["detail"] == w.fill(w.ERR_DUPLICATE_BY, who="sid") == "Already added by sid"
    )
    # A person who is only in the sheet has no claim: the old text stays.
    dinesh = synced.scalar(select(Account.id).where(Account.name == "Google DeepMind"))
    body["target_ids"] = [dinesh]
    body["payload"]["fields"][0]["value"] = "Dinesh Tewari"
    sheet_only = client.post("/api/act", json=body, headers=H)
    assert sheet_only.status_code == 409 and sheet_only.json()["detail"] == w.ERR_DUPLICATE


def test_d111_the_key_holds_when_the_check_is_passed(synced: Session, company: int) -> None:
    # A claim the in-function check cannot see (its decision is no row add) stands for a race:
    # the key still refuses the add, rolls back the decision, and names who holds the claim.
    other = synced.execute(
        text(
            "insert into decision (kind, user_id, state) "
            "select 'column_write', id, 'active' from users where short = 'kushal' returning id"
        )
    ).scalar_one()
    synced.execute(
        text(
            "insert into row_add_claim (account_id, name_key, decision_id, user_id) "
            "select :a, 'priya sharma', :d, id from users where short = 'kushal'"
        ),
        {"a": company, "d": other},
    )
    synced.commit()
    assert add(synced, company) == {"ok": False, "code": "duplicate", "by": "kushal"}
    assert synced.scalar(text("select count(*) from decision where kind = 'row_add'")) == 0
    assert synced.scalar(text("select count(*) from sync_outbox")) == 0
    with pytest.raises(IntegrityError):
        synced.execute(
            text(
                "insert into row_add_claim (account_id, name_key, decision_id, user_id) "
                "select :a, 'priya sharma', :d, id from users where short = 'sid'"
            ),
            {"a": company, "d": other},
        )
    synced.rollback()


def test_d111_undo_and_a_failed_or_cancelled_push_free_the_claim(
    synced: Session, company: int
) -> None:
    first = add(synced, company)["items"][0]["write_id"]
    assert undo(synced, "sid", first)["ok"]
    assert claims(synced) == []
    second = add(synced, company)["items"][0]["write_id"]
    synced.execute(
        text("update sync_outbox set state = 'failed' where decision_id = :d"), {"d": second}
    )
    synced.commit()
    assert claims(synced) == []  # failed before the write: nothing reached the sheet
    third = add(synced, company)["items"][0]["write_id"]
    synced.execute(text("update decision set before = '{}' where id = :d"), {"d": third})
    synced.execute(
        text("update sync_outbox set state = 'failed' where decision_id = :d"), {"d": third}
    )
    synced.commit()
    assert claims(synced) == [(company, "priya sharma")]  # D86: may be in the sheet, keep it
    synced.execute(
        text("update sync_outbox set state = 'cancelled' where decision_id = :d"), {"d": third}
    )
    synced.commit()
    assert claims(synced) == []


def test_d111_the_row_add_lock_is_on_the_name_not_the_company(
    synced: Session, company: int
) -> None:
    dinesh = synced.scalar(select(Account.id).where(Account.name == "Google DeepMind"))
    bind = synced.get_bind()
    with bind.connect() as holder:
        holder.execute(
            text("select pg_advisory_xact_lock(hashtext('poc' || 'row_add' || 'priya sharma'))")
        )
        synced.execute(text("set local lock_timeout = '300ms'"))
        with pytest.raises(OperationalError, match="lock timeout"):
            add(synced, dinesh)  # another company, the same name: it waits for the lock
        synced.rollback()
        holder.rollback()
    assert add(synced, company)["ok"] is True


def test_d111_news_seen_has_an_index_on_story_id(synced: Session) -> None:
    found = synced.scalar(
        text(
            "select indexdef from pg_indexes "
            "where tablename = 'news_seen' and indexname = 'ix_news_seen_story_id'"
        )
    )
    assert found is not None and "(story_id)" in found
