"""Live round trip in the scratch tab (R2-W02, `saley sheet roundtrip --test-tab`).

Why: the unit tests prove the write path on a fake sheet. This proves it on the real Google
sheet COPY, without touching one real row: it makes a tab named SHEET_TEST_TAB, writes a header
and one fake person, writes one cell and the D47 note, reads both back, undoes, reads back, and
deletes the tab. The delete runs in `finally`, also when a step fails.

Safety rules, in the order they act:
- The worker process only (the write client is built only there, D23, D46) and the sheet copy
  only (D53). `SheetPort` lets a Google tab be added or deleted only under the scratch name.
- A tab with the scratch name that already exists is deleted only when its first cell holds
  our marker (a leftover of a failed run). Any other tab with that name is left alone and the
  run fails: it is not ours.
- Nothing here reads or writes the database, Discord, mail or an LLM.

`--row-add` runs the R3 flow instead (R3-S02): it adds one person row through the worker's own
`rowadd` code, checks the signature note and the read-back, refuses a duplicate, undoes the add
(the row is deleted), and deletes the tab. The same safety rules hold.

A reason only a human can fix (writes off, missing settings, no Editor right on the sheet) is
"NOT CONFIGURED: Q-R2-1" and exit 3, which the matrix reads as PENDING (docs/16 §3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

from saley.core.clock import now_ist, today_ist
from saley.core.logging import scrub_text
from saley.core.settings import MissingSettings, get_settings
from saley.sales import rowadd
from saley.sales.columns import POC_ROLES, WritesRefused, assert_writable
from saley.sales.sheet import SheetNotCopy, find_header
from saley.sales.writer import (
    SheetPort,
    WriteCredentialRefused,
    append_note,
    build_write_sheet,
    norm_value,
    row_added_line,
    undo_line,
    write_line,
)

QUESTION = "Q-R2-1"
MARKER = "Saley scratch tab. Safe to delete."
MARKER_ROW, HEADER_ROW, PERSON_ROW = 1, 2, 3  # 1-based sheet rows
ROLE = "li_connected_date"
COLUMN_LABEL = "LI Connected Date"
OLD_VALUE = "2026-01-05"
TEST_USER = "Saley test"
TEST_DECISION = 0
TAB_ROWS = 10
EXIT_PASS, EXIT_FAIL, EXIT_PENDING = 0, 1, 3


class NotConfigured(RuntimeError):
    """Only a human can fix this (docs/16 §3). The message names the reason."""


class TabNotOurs(RuntimeError):
    """A tab with the scratch name exists and has no marker. Saley leaves it alone."""


@dataclass
class Report:
    lines: list[tuple[str, str, str, bool]] = field(default_factory=list)
    wrote: bool = False  # True once the first write (making the scratch tab) succeeded

    def check(self, metric: str, expected: object, got: object) -> bool:
        ok = str(expected) == str(got)
        self.lines.append((metric, _one_line(expected), _one_line(got), ok))
        return ok

    @property
    def ok(self) -> bool:
        return bool(self.lines) and all(line[3] for line in self.lines)

    def render(self) -> list[str]:
        out = [f"{m} · {e} · {g} · {'OK' if ok else 'DIFF'}" for m, e, g, ok in self.lines]
        return [*out, "PASS" if self.ok else "FAIL"]


def _one_line(value: object) -> str:
    return " / ".join(str(value).splitlines()) if value is not None else ""


def permission_refused(exc: BaseException) -> bool:
    """True for a Google 403 (no Editor right). Duck typed, so no gspread import is needed."""
    return getattr(getattr(exc, "response", None), "status_code", None) == 403


def header_labels() -> list[str]:
    """One header cell per role, in sheet order: the writer needs every locked role mapped."""
    return [role.aliases[0].title() for role in POC_ROLES]


def person_row() -> list[str]:
    cells = {"sr_no": "1", "company": "Saley Test Co", "name": "Test Person"}
    cells[ROLE] = OLD_VALUE
    return [cells.get(role.name, "") for role in POC_ROLES]


def claim_tab(sheet: SheetPort, tab: str, report: Report) -> None:
    """Make room for the scratch tab. Delete a leftover only when it carries our marker."""
    if not sheet.tab_exists(tab):
        return
    if str(sheet.read_cell(tab, MARKER_ROW, 0) or "").strip() != MARKER:
        raise TabNotOurs(f"tab {tab!r} exists and is not ours; nothing was touched")
    sheet.delete_tab(tab)
    report.wrote = True
    report.check("leftover scratch tab removed", "yes", "yes")


def _steps(sheet: SheetPort, tab: str, report: Report, moment: datetime, today: date) -> None:
    sheet.add_tab(tab, rows=TAB_ROWS, cols=len(POC_ROLES) + 1)
    report.wrote = True
    sheet.write_cell(tab, MARKER_ROW, 0, MARKER)
    sheet.write_row(tab, HEADER_ROW, header_labels())
    sheet.write_row(tab, PERSON_ROW, person_row())

    rows = sheet.read_header(tab, rows=PERSON_ROW)
    index, resolution = find_header(rows, POC_ROLES)
    report.check("header row found", HEADER_ROW, index + 1)
    report.check("locked columns mapped", True, resolution.writes_allowed)
    column = assert_writable(resolution, ROLE)
    name_col = resolution.columns["name"]
    row = sheet.find_row(tab, name_col, "Test Person")
    report.check("person row found", PERSON_ROW, row)
    if row is None:
        return

    before = norm_value(ROLE, sheet.read_cell(tab, row, column))
    report.check("cell before write", OLD_VALUE, before)
    new_value = today.isoformat()
    sheet.write_cell(tab, row, column, new_value)
    report.check("cell after write", new_value, norm_value(ROLE, sheet.read_cell(tab, row, column)))
    line = write_line(COLUMN_LABEL, new_value, TEST_USER, "dashboard", moment, TEST_DECISION)
    note = append_note(sheet.read_note(tab, row, name_col), line)
    sheet.write_note(tab, row, name_col, note)
    report.check("name note after write", note, sheet.read_note(tab, row, name_col).strip())

    # Undo: put the old value back only while the cell still holds what we wrote (D47).
    held = norm_value(ROLE, sheet.read_cell(tab, row, column))
    report.check("cell still holds the write", new_value, held)
    sheet.write_cell(tab, row, column, before)
    report.check("cell after undo", before, norm_value(ROLE, sheet.read_cell(tab, row, column)))
    undone = append_note(note, undo_line(TEST_DECISION, TEST_USER, "dashboard", moment))
    sheet.write_note(tab, row, name_col, undone)
    report.check("name note after undo", undone, sheet.read_note(tab, row, name_col).strip())


ROW_ADD_CELLS = {
    "company": "Saley Test Co",
    "name": "Row Add Test",
    "designation": "Test role",
    "industry": "Test",
}


def _row_add_steps(sheet: SheetPort, tab: str, report: Report, moment: datetime) -> None:
    """The R3 steps: add a person row, read it back, sign it, refuse a twin, undo, check."""
    sheet.add_tab(tab, rows=TAB_ROWS, cols=len(POC_ROLES) + 1)
    report.wrote = True
    sheet.write_cell(tab, MARKER_ROW, 0, MARKER)
    sheet.write_row(tab, HEADER_ROW, header_labels())
    sheet.write_row(tab, PERSON_ROW, person_row())
    rows = sheet.read_header(tab, rows=PERSON_ROW)
    index, resolution = find_header(rows, POC_ROLES)
    report.check("header row found", HEADER_ROW, index + 1)
    report.check("locked columns mapped", True, resolution.writes_allowed)

    plan = rowadd.append_person(sheet, tab, resolution, len(rows[index]), ROW_ADD_CELLS)
    report.check("new row below the last person", PERSON_ROW + 1, plan.row)
    report.check("Sr No is the highest plus one", 2, plan.sr_no)
    row = plan.row or 0
    wrong = rowadd.verify_row(sheet, tab, resolution, row, rowadd.full_cells(plan))
    report.check(
        "row read back equals what was written", "no differences", wrong or "no differences"
    )
    rowadd.sign_added(
        sheet,
        tab,
        resolution,
        row,
        user=TEST_USER,
        surface="dashboard",
        when=moment,
        decision_id=TEST_DECISION,
    )
    name_col = resolution.columns["name"]
    expected = append_note("", row_added_line(TEST_USER, "dashboard", moment, TEST_DECISION))
    report.check(
        "name note is the signature", expected, sheet.read_note(tab, row, name_col).strip()
    )
    try:
        rowadd.append_person(sheet, tab, resolution, len(rows[index]), ROW_ADD_CELLS)
        twin = "added a second row"
    except rowadd.RowAddRefused:
        twin = "refused"
    report.check("same name and company is refused", "refused", twin)
    try:
        assert_writable(resolution, "meeting_status", existing_row=False)
        locked = "written"
    except WritesRefused:
        locked = "refused"
    report.check("a step column is not written on a new row", "refused", locked)

    removed = rowadd.remove_added(
        sheet, tab, resolution, plan, header_row=index + 1, signed_by=TEST_DECISION
    )
    report.check("undo removes the row", True, removed)
    report.check("row is gone", None, sheet.find_row(tab, name_col, ROW_ADD_CELLS["name"]))
    report.check(
        "the first person row is untouched",
        "Test Person",
        sheet.read_cell(tab, PERSON_ROW, name_col),
    )


def _tear_down(sheet: SheetPort, tab: str, report: Report) -> None:
    """Delete the scratch tab and check it is gone. Never raises: the report says what happened."""
    try:
        if sheet.tab_exists(tab):
            sheet.delete_tab(tab)
        gone = not sheet.tab_exists(tab)
    except Exception as exc:  # the run must end with a clear line, not a traceback
        report.check("scratch tab deleted", "yes", f"no: {type(exc).__name__}")
        return
    report.check("scratch tab deleted", "yes", "yes" if gone else "no")


def run_roundtrip(
    sheet: SheetPort,
    tab: str,
    moment: datetime | None = None,
    today: date | None = None,
    *,
    row_add: bool = False,
) -> Report:
    """Run all steps in the scratch tab. The tab is deleted in `finally` (also on failure)."""
    report = Report()
    created = False
    try:
        claim_tab(sheet, tab, report)
        created = True  # from here a tab may exist: always try the delete
        if row_add:
            _row_add_steps(sheet, tab, report, moment or now_ist())
        else:
            _steps(sheet, tab, report, moment or now_ist(), today or today_ist())
    except TabNotOurs as exc:
        created = False
        report.check("scratch tab is free", "yes", str(exc))
    except Exception as exc:
        if permission_refused(exc) and not report.wrote:
            raise NotConfigured(
                "Google refused the write (403): the service account needs Editor"
            ) from exc
        # A 403 after the first write worked is a real failure, not a missing right (M9).
        report.check("every step runs", "no error", f"{type(exc).__name__}: {scrub_text(str(exc))}")
    finally:
        if created:
            _tear_down(sheet, tab, report)
    return report


def execute(
    echo: Callable[[str], None],
    sheet_factory: Callable[[], SheetPort] = build_write_sheet,
    *,
    row_add: bool = False,
    case: str | None = None,
    n: int = 20,
    seed: int = 20261021,
) -> int:
    """The command body. Returns the exit code: 0 PASS, 1 FAIL, 3 NOT CONFIGURED.

    `case` (R5 T9) runs one live case of `roundtrip_cases` instead of the plain round trip.
    """
    settings = get_settings()
    try:
        if not settings.sheet_write_enabled:
            raise NotConfigured("SHEET_WRITE_ENABLED is false")
        sheet = _build(sheet_factory)
        if case:
            from saley.sales.roundtrip_cases import run_case

            report = run_case(sheet, settings.sheet_test_tab, case, n=n, seed=seed)
        else:
            report = run_roundtrip(sheet, settings.sheet_test_tab, row_add=row_add)
    except NotConfigured as exc:
        echo(f"NOT CONFIGURED: {QUESTION}")
        echo(f"reason: {exc}")
        return EXIT_PENDING
    except (SheetNotCopy, WriteCredentialRefused) as exc:
        echo(f"Refused: {exc}")
        return EXIT_FAIL
    for line in report.render():
        echo(line)
    return EXIT_PASS if report.ok else EXIT_FAIL


def _build(sheet_factory: Callable[[], SheetPort]) -> SheetPort:
    try:
        return sheet_factory()
    except MissingSettings as exc:
        raise NotConfigured(f"missing settings: {exc}") from exc
    except Exception as exc:
        if permission_refused(exc) or type(exc).__name__ == "SpreadsheetNotFound":
            raise NotConfigured("the sheet copy is not open to the service account") from exc
        raise
