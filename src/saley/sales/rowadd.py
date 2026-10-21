"""Add a person to Outreach PoCs, and take the row away on undo (R3, D76, docs/10 "New row").

Why: a row add is the one write that makes a whole row. The worker runs these steps, with the
same care as a cell write:
1. the headers are read again and each role must map to a column (locks by ROLE, never by
   letter); only the roles of docs/10 "New row" are written (`NEW_ROW_ROLES`);
2. the formula guard (H1) refuses any cell that starts with `=`, `+`, `-` or `@`;
3. the same company and name already in the sheet is refused (a human may have added it);
4. Sr No is the highest number in the column plus one, read now, not when the click came;
5. the row goes below the last named row in one call, then it is read back cell by cell;
6. the Name cell gets the note `Saley · row added · <user> · <surface> · <time> · d<id>`.
Undo deletes the row, but only when it still holds exactly what Saley wrote and nothing else.
`Plan` is stored in `decision.before` before the first write, so a retry finds its own row and
never adds a second one.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from saley.core.logging import get_logger, scrub_text
from saley.sales.columns import Resolution, assert_writable
from saley.sales.parse import norm_name
from saley.sales.writer import (
    NOTE_PREFIX,
    FormulaRefused,
    RowOccupied,
    SheetPort,
    append_note,
    norm_value,
    refuse_formula,
    row_added_line,
)

log = get_logger("saley.sales.rowadd")

MAX_CELL = 200
QA_NAME_PREFIX = "QA Seed"  # the browser check's company: the worker never writes a row for it


class RowAddRefused(RuntimeError):
    """This add can never succeed (retrying would not help). The text says why."""


class SignFailed(RuntimeError):
    """The signature note could not be written. NOT a refusal: the push is tried again later."""


class ReadBackFailed(RuntimeError):
    """The row did not hold the written values on read-back. A retry finds the row again."""


@dataclass(frozen=True)
class Plan:
    """What the worker means to write, and where it went. Kept in `decision.before`."""

    sr_no: int
    cells: dict[str, str]
    row: int | None = None
    # Round 5: set (and committed) right after the append worked. A plan with this mark owns the
    # row it finds by Sr No and name, even when the signature note is still missing.
    appended_at: str | None = None

    def dumps(self) -> str:
        return json.dumps(
            {
                "sr_no": self.sr_no,
                "cells": self.cells,
                "row": self.row,
                "appended_at": self.appended_at,
            }
        )

    @classmethod
    def loads(cls, text: str | None) -> Plan | None:
        try:
            raw = json.loads(text or "")
            return cls(
                int(raw["sr_no"]), dict(raw["cells"]), raw.get("row"), raw.get("appended_at")
            )
        except (ValueError, KeyError, TypeError):
            return None

    def placed(self, row: int) -> Plan:
        return Plan(self.sr_no, self.cells, row, self.appended_at)

    def appended(self, row: int, at: str) -> Plan:
        return Plan(self.sr_no, self.cells, row, at)


def check_cells(resolution: Resolution, cells: Mapping[str, str]) -> dict[str, str]:
    """Every role must be a new-row role with a column. Every value must be safe text."""
    clean: dict[str, str] = {}
    for role, value in cells.items():
        assert_writable(resolution, role, existing_row=False)  # raises WritesRefused
        text = str(value).strip()
        if not text or len(text) > MAX_CELL or any(ord(ch) < 32 for ch in text):
            raise RowAddRefused(f"the value for {role!r} is empty, long or has control characters")
        try:
            refuse_formula(text)  # H1
        except FormulaRefused as exc:
            raise RowAddRefused(str(exc)) from exc
        clean[role] = text
    for needed in ("name", "company"):
        if needed not in clean:
            raise RowAddRefused(f"the new row has no {needed}")
    if "li_url" in clean and "linkedin.com/in/" not in clean["li_url"]:
        raise RowAddRefused("li_url must be a linkedin.com/in/ link from a search")
    return clean


def next_sr_no(sheet: SheetPort, title: str, resolution: Resolution) -> int:
    """Highest number in the Sr No column plus one. Text and blanks are ignored."""
    column = resolution.columns.get("sr_no")
    if column is None:
        raise RowAddRefused("the Sr No column was not found")
    numbers = [
        int(v)
        for v in sheet.read_column(title, column)
        if isinstance(v, int | float) and not isinstance(v, bool) and float(v).is_integer()
    ]
    return max(numbers, default=0) + 1


def _same(left: object, right: object) -> bool:
    return norm_name(str(left or "")) == norm_name(str(right or ""))


def rows_for(
    sheet: SheetPort, title: str, resolution: Resolution, name: str, company: str
) -> list[int]:
    """Rows with this name and company already in the sheet."""
    name_col, company_col = resolution.columns["name"], resolution.columns["company"]
    found: list[int] = []
    after = 0
    while (row := sheet.find_row(title, name_col, name, after)) is not None:
        if _same(sheet.read_cell(title, row, company_col), company):
            found.append(row)
        after = row
    return found


def _values(resolution: Resolution, header_width: int, cells: Mapping[str, str]) -> list[str]:
    width = max([header_width, *(i + 1 for i in resolution.columns.values())])
    values = [""] * width
    for role, value in cells.items():
        values[resolution.columns[role]] = value
    return values


def full_cells(plan: Plan) -> dict[str, str]:
    return {**plan.cells, "sr_no": str(plan.sr_no)}


def verify_row(
    sheet: SheetPort, title: str, resolution: Resolution, row: int, cells: Mapping[str, str]
) -> list[str]:
    """Roles whose cell does not hold the value, and (as '*') extra text in other columns."""
    held = sheet.read_row(title, row)
    wrong = []
    by_column = {resolution.columns[role]: role for role in cells}
    for column, value in enumerate(held):
        role = by_column.get(column)
        if role is None:
            if norm_value("", value):
                wrong.append("*")
        elif norm_value(role, value) != norm_value(role, cells[role]):
            wrong.append(role)
    for column, role in by_column.items():
        if column >= len(held) and cells[role]:
            wrong.append(role)
    return wrong


def append_person(
    sheet: SheetPort,
    title: str,
    resolution: Resolution,
    header_width: int,
    cells: Mapping[str, str],
    plan: Plan | None = None,
    on_plan: Callable[[Plan], None] | None = None,
    header_row: int = 0,
    on_written: Callable[[int], None] | None = None,
    decision_id: int | None = None,
    sign_adopted: Callable[[int], None] | None = None,
) -> Plan:
    """Add the row once. With a plan from an earlier try, adopt its row when it is there.

    `on_plan` is called with the plan just before the write, so the caller can save it first.
    `on_written` is called with the row number right after the append and BEFORE the read-back,
    so the caller can sign the row at once: a row that is signed can always be undone (M1, L4).
    """
    assert_writable(resolution, "sr_no", existing_row=False)  # the Sr No column must exist
    clean = check_cells(resolution, {k: v for k, v in cells.items() if k != "sr_no"})
    name, company = clean["name"], clean["company"]
    if plan is not None:
        # M1: a retry looks for its own row first (by Sr No and name), so a lost read-back or a
        # network error after the append never adds a second row.
        mine = locate(sheet, title, resolution, plan, header_row=header_row)
        for row in dict.fromkeys(
            [*([mine] if mine else []), *rows_for(sheet, title, resolution, name, company)]
        ):
            if verify_row(sheet, title, resolution, row, full_cells(plan)):
                continue
            # Round 4 (7): a row is ours only with the signature of THIS decision. A matching
            # row without it may be a human's: never adopted, never signed.
            if decision_id is not None and not is_signed(
                sheet, title, resolution, row, decision_id
            ):
                if plan.appended_at is None or row != mine or sign_adopted is None:
                    raise RowAddRefused(
                        "a matching row exists without Saley's signature; not added"
                    )
                # Round 5: the plan says THIS push appended it (found by Sr No + name, verified
                # above with no other text): the signing was lost, so sign it now.
                sign_adopted(row)
            log.info("row_add_adopted", row=row)
            return plan.placed(row)
    if rows_for(sheet, title, resolution, name, company):
        raise RowAddRefused("that person and company are already in the sheet")
    plan = Plan(next_sr_no(sheet, title, resolution), clean)
    if on_plan is not None:
        on_plan(plan)
    return _write_row(sheet, title, resolution, header_width, plan, on_written)


def _write_row(
    sheet: SheetPort,
    title: str,
    resolution: Resolution,
    header_width: int,
    plan: Plan,
    on_written: Callable[[int], None] | None = None,
) -> Plan:
    values = _values(resolution, header_width, full_cells(plan))
    try:
        row = sheet.append_row(title, resolution.columns["name"], values)
    except RowOccupied as exc:  # M2: never over text in another column
        raise RowAddRefused(f"not added: {exc}") from exc
    if on_written is not None:
        on_written(row)
    placed = plan.placed(row)
    wrong = verify_row(sheet, title, resolution, row, full_cells(placed))
    if wrong:
        raise ReadBackFailed(f"row {row} does not hold the written values: {', '.join(wrong)}")
    return placed


def sign_added(
    sheet: SheetPort,
    title: str,
    resolution: Resolution,
    row: int,
    *,
    user: str,
    surface: str,
    when: datetime,
    decision_id: int,
) -> bool:
    """Put the signature on the Name cell. A note failure never undoes the row."""
    name_col = resolution.columns["name"]
    line = row_added_line(user, surface, when, decision_id)
    try:
        existing = sheet.read_note(title, row, name_col)
        sheet.write_note(title, row, name_col, append_note(existing, line))
    except Exception as exc:  # the row is written; say so and go on
        log.warning("note_failed", error=type(exc).__name__, detail=scrub_text(str(exc)))
        return False
    return True


def locate(
    sheet: SheetPort,
    title: str,
    resolution: Resolution,
    plan: Plan,
    *,
    header_row: int = 0,
    signed_by: int | None = None,
) -> int | None:
    """The row Saley added: the planned row if it still holds our name and Sr No, else a search.

    L4: a row at or above the header row is never ours. With `signed_by` (the decision id) the
    Name cell must also carry the Saley signature note `d<id>`; the delete path sets it, so a
    human row with the same name and number is never deleted.
    """
    name_col, sr_col = resolution.columns["name"], resolution.columns["sr_no"]
    wanted_name, wanted_sr = plan.cells["name"], str(plan.sr_no)

    def ours(row: int) -> bool:
        if row <= header_row:
            return False
        if signed_by is not None and not _signed(sheet, title, row, name_col, signed_by):
            return False
        return _same(sheet.read_cell(title, row, name_col), wanted_name) and (
            norm_value("sr_no", sheet.read_cell(title, row, sr_col)) == wanted_sr
        )

    if plan.row and ours(plan.row):
        return plan.row
    after = 0
    while (row := sheet.find_row(title, name_col, wanted_name, after)) is not None:
        if ours(row):
            return row
        after = row
    return None


def _signed(sheet: SheetPort, title: str, row: int, name_col: int, decision_id: int) -> bool:
    """The Name note holds the line `Saley · row added · … · d<id>` (L4)."""
    head, tail = f"{NOTE_PREFIX}row added", f" d{decision_id}"
    notes = sheet.read_note(title, row, name_col).splitlines()
    return any(line.startswith(head) and line.endswith(tail) for line in notes)


def is_signed(
    sheet: SheetPort, title: str, resolution: Resolution, row: int, decision_id: int
) -> bool:
    """True when the Name note of `row` already carries the signature of this add.

    A read error is NOT "unsigned" (round 5): it propagates, so the push stays pending and is
    tried again, and a row of ours is never refused only because the notes API was down.
    """
    return _signed(sheet, title, row, resolution.columns["name"], decision_id)


def remove_added(
    sheet: SheetPort,
    title: str,
    resolution: Resolution,
    plan: Plan,
    *,
    header_row: int = 0,
    signed_by: int | None = None,
    sign: Callable[[int], None] | None = None,
) -> bool:
    """Undo: delete the row if nobody changed it. Returns False when it was already gone.

    The row is found and checked, then found and checked AGAIN right before the delete (A7).
    A person may sort the sheet or insert a row between the first check and the delete; a delete
    by row number would then remove another person's row. If the second look finds the row
    elsewhere or changed, nothing is deleted.
    """
    name_col = resolution.columns["name"]
    row = locate(sheet, title, resolution, plan, header_row=header_row)
    if row is None:
        log.info("row_add_undo_already_gone", sr_no=plan.sr_no)
        return False
    # L4: never delete a row without the Saley signature note of THIS add.
    if signed_by is not None and not _signed(sheet, title, row, name_col, signed_by):
        # Round 5: the plan says the append worked and only the signing was lost. Sign first,
        # but only for a row that matches the plan exactly; then it is removed like any other.
        if (
            plan.appended_at is None
            or sign is None
            or verify_row(sheet, title, resolution, row, full_cells(plan))
        ):
            raise RowAddRefused("row has no Saley signature note, not removed")
        sign(row)
    if verify_row(sheet, title, resolution, row, full_cells(plan)):
        raise RowAddRefused("row changed since, not removed")
    again = locate(sheet, title, resolution, plan, header_row=header_row)
    if (
        again != row
        or (signed_by is not None and not _signed(sheet, title, row, name_col, signed_by))
        or verify_row(sheet, title, resolution, row, full_cells(plan))
    ):
        raise RowAddRefused("row moved or changed while removing, not removed")
    sheet.delete_row(title, row)
    return True


def read_plan(raw: Any) -> Plan:
    plan = Plan.loads(raw if isinstance(raw, str) else None)
    if plan is None:
        raise RowAddRefused("the plan of the added row is missing")
    return plan
