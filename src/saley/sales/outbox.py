"""The outbox: push committed writes to the sheet, in the worker only (D45).

Why: the dashboard commits a `decision` and a `sync_outbox` row at once (D24, "writes at
once"). This module drains the outbox. Each push:
1. finds the tab title and re-resolves the headers, so a column that moved is found again;
2. asks `columns.assert_writable` again (locks by ROLE, CLAUDE.md), whatever the web checked;
3. finds the person's row (never trusts a stored row number alone);
4. reads the cell (`before`), writes, reads it back, appends the signature line to the
   Name note (D47), and records an audit row.
Undo is the op `restore`: it writes `before` back only if the cell still holds `after`.
The op `append` adds a whole new row (R3, D76, `rowadd.py`); its undo is a `restore` that
deletes that row, only when nobody changed it.
`SHEET_WRITE_ENABLED=false` keeps rows `held`; nothing touches the sheet.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from dataclasses import field as dc_field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from saley.core.clock import now_ist
from saley.core.db import session_scope
from saley.core.logging import get_logger, scrub_text
from saley.core.models import Contact, Decision, SheetTabStatus, SyncAudit, SyncOutbox, User
from saley.sales import rowadd
from saley.sales.columns import POC_ROLES, Resolution, WritesRefused, assert_writable
from saley.sales.parse import clean_text, is_truthy
from saley.sales.sheet import find_header
from saley.sales.stage import is_li_connected
from saley.sales.writer import (
    FormulaRefused,
    SheetPort,
    append_note,
    as_literal,
    norm_value,
    refuse_formula,
    undo_line,
    write_line,
)

log = get_logger("saley.sales.outbox")

MAX_TRIES = 5
BACKOFF_SECONDS = 5
BACKOFF_CAP_SECONDS = 600
PUSH_BATCH = 25
IDLE_NOTE = "idle"  # a quiet job (registry) deletes its job_runs row when it did nothing
OPEN_STATES = ("pending", "held")
NO_NOTE = "row added without its signature note"  # A8: shown as a failed write (H5)

# Sheet role → the contact fields the sync mirrors from that cell. While a write for the cell
# is open, the sync keeps the stored value, so a sync never reverts a pending write.
PENDING_FIELDS: dict[str, tuple[str, ...]] = {
    "first_contact": ("first_contact", "contacted"),
    "first_contact_type": ("first_contact_type",),
    "first_contact_date": ("first_contact_date",),
    "sid_li_added": ("li_status", "connected", "contacted"),
    "li_connected_date": ("li_connected_date",),
    "meeting_date": ("meeting_date",),
    "meeting_status": ("meeting_status",),
    "email": ("email", "email_status"),
}


class RowFailed(RuntimeError):
    """This row can never succeed (retrying would not help)."""


class PushCancelled(Exception):
    """An undo took the row while the worker had let go of it to save the plan. Nothing is
    written and the outbox row keeps the state the undo gave it."""


class ReadBackMismatch(RuntimeError):
    """The cell did not hold the written value on read-back. A retry may help."""


Scope = Callable[[], AbstractContextManager[Session]]


@dataclass
class PushStats:
    done: int = 0
    failed: int = 0
    retry: int = 0

    def note(self) -> str:
        if not (self.done or self.failed or self.retry):
            return IDLE_NOTE
        return f"pushed {self.done} · failed {self.failed} · retry {self.retry}"


# ------------------------------------------------------------------ read side (sync, health)


def open_cells(session: Session) -> dict[str, set[str]]:
    """row_key → roles with an open (pending or held) write on the Outreach PoCs tab."""
    found: dict[str, set[str]] = {}
    rows = session.execute(
        select(SyncOutbox.row_key, SyncOutbox.role).where(
            SyncOutbox.tab == "poc", SyncOutbox.state.in_(OPEN_STATES)
        )
    )
    for row_key, role in rows:
        found.setdefault(row_key, set()).add(role)
    return found


def keep_open_cells(values: dict[str, Any], contact: Contact, roles: set[str]) -> None:
    """Replace the synced values of cells with an open write by what the database holds now."""
    for role in roles:
        for field in PENDING_FIELDS.get(role, ()):
            values[field] = getattr(contact, field)
        cells = values.get("cells")
        if isinstance(cells, dict) and contact.cells and role in contact.cells:
            cells[role] = contact.cells[role]


DATE_FIELDS = {
    "first_contact_date": "first_contact_date",
    "li_connected_date": "li_connected_date",
    "meeting_date": "meeting_date",
}


def mirror_value(contact: Contact, role: str, value: str) -> None:
    """Copy a pushed cell into the contact row the way the sync reads it (M4).

    Why: once the push is `done` the sync no longer keeps the stored value, and the next sync
    may be a minute away. Without this copy a task the person just ticked would show again.
    The next sync overwrites the same fields from the sheet, so the two always agree.
    """
    shown = clean_text(value)
    if role == "first_contact":
        contact.first_contact = shown
        contact.contacted = is_truthy(value) or contact.connected
    elif role == "first_contact_type":
        contact.first_contact_type = shown
    elif role in DATE_FIELDS:
        try:
            parsed = date.fromisoformat(value) if value else None
        except ValueError:
            return
        setattr(contact, DATE_FIELDS[role], parsed)
    elif role == "sid_li_added":
        contact.li_status = shown
        contact.connected = is_li_connected(shown)
        contact.contacted = is_truthy(contact.first_contact) or contact.connected
    elif role == "meeting_status":
        contact.meeting_status = shown
    elif role == "email":
        contact.email = shown
        contact.email_status = "known" if shown and "@" in shown else None
    else:
        return
    if isinstance(contact.cells, dict):
        contact.cells = {**contact.cells, role: shown}


def hold_pending(session: Session) -> int:
    """SHEET_WRITE_ENABLED=false: pending rows become `held`. The sheet is not touched."""
    result = session.execute(
        update(SyncOutbox)
        .where(SyncOutbox.state == "pending")
        .values(state="held", updated_at=now_ist())
    )
    return result.rowcount or 0


SCRATCH_KEY_PREFIX = "saley-test|"  # the T9 live-case rows; see roundtrip_cases.KEY_PREFIX


def count_due(session: Session, moment: datetime) -> int:
    return session.scalar(select(func.count()).select_from(_due_query(moment).subquery())) or 0


def _due_query(moment: datetime) -> Any:
    """Open rows that are due. T9 test rows (`saley-test|`) are never due for the worker."""
    return select(SyncOutbox).where(
        SyncOutbox.state.in_(OPEN_STATES),
        SyncOutbox.row_key.not_like(SCRATCH_KEY_PREFIX + "%"),
        or_(SyncOutbox.next_at.is_(None), SyncOutbox.next_at <= moment),
    )


# ------------------------------------------------------------------ push


@dataclass
class _Run:
    """What one push run shares between rows: the sheet, tab titles and header reads."""

    sheet: SheetPort
    titles: dict[str, str | None]
    email_write_allowed: bool
    moment: datetime
    headers: dict[str, tuple[Resolution, list[Any]]]
    header_rows: dict[str, int] = dc_field(default_factory=dict)

    def header(self, title: str) -> tuple[Resolution, list[Any]]:
        if title not in self.headers:
            rows = self.sheet.read_header(title)
            index, resolution = find_header(rows, POC_ROLES)
            self.headers[title] = (resolution, rows[index] if rows else [])
            self.header_rows[title] = index + 1  # the 1-based sheet row of the header (L4)
        return self.headers[title]


def _same(left: object, right: object) -> bool:
    return (
        " ".join(norm_value("", left).split()).casefold()
        == " ".join(norm_value("", right).split()).casefold()
    )


def _locate(run: _Run, title: str, resolution: Resolution, contact: Contact) -> int:
    """The sheet row of this person. The stored row number is a hint, never trusted alone."""
    name_col = resolution.columns.get("name")
    if name_col is None:
        raise RowFailed("the Name column was not found")
    sr_col = resolution.columns.get("sr_no")

    def matches(row: int) -> bool:
        if not _same(run.sheet.read_cell(title, row, name_col), contact.name):
            return False
        if sr_col is not None and contact.sr_no:
            return _same(run.sheet.read_cell(title, row, sr_col), contact.sr_no)
        return True

    if contact.row_number and matches(contact.row_number):
        return contact.row_number
    after = 0
    while (row := run.sheet.find_row(title, name_col, contact.name, after)) is not None:
        if matches(row):
            return row
        after = row
    raise RowFailed("the row was not found in the sheet")


def _commit_and_retake(session: Session, row: SyncOutbox, decision: Decision) -> None:
    """Commit what was set on the decision in its own short transaction, then take the locks
    again, outbox row first (the undo's order). An undo that got in between cancelled this push."""
    session.commit()
    session.refresh(row, with_for_update=True)
    session.refresh(decision, with_for_update=True)
    if row.state not in OPEN_STATES or decision.undone_at is not None:
        raise PushCancelled


def _push_append(session: Session, run: _Run, row: SyncOutbox) -> tuple[str, str]:
    """Add a new person row (R3, D76). The plan is saved before the write, so a retry is safe."""
    decision = session.get(Decision, row.decision_id, with_for_update=True)
    if decision is None:
        raise RowFailed("the decision is missing")
    user = session.get(User, decision.user_id) if decision.user_id else None
    title = run.titles.get(row.tab)
    if not title:
        raise RowFailed("the sheet tab was never read")
    resolution, header_row = run.header(title)
    try:
        cells = json.loads(row.value or "")
    except ValueError as exc:
        raise RowFailed("the new row is not valid") from exc
    if not isinstance(cells, dict):
        raise RowFailed("the new row is not valid")
    if str(cells.get("company", "")).startswith(rowadd.QA_NAME_PREFIX):
        # The browser check adds a person to a made-up company. It must never reach the sheet.
        raise RowFailed("a QA test row is never written to the sheet")

    def save_plan(plan: rowadd.Plan) -> None:
        # Round 4 (1): the plan is COMMITTED before the append, in its own short transaction.
        # A crash after the append then still leaves the plan, so an undo can find the row.
        # The commit frees the locks, so both are taken again, outbox row first (the undo's
        # order). If an undo got in between, it cancelled this push: write nothing.
        decision.before = plan.dumps()  # the first plan only; the final one replaces it below
        _commit_and_retake(session, row, decision)

    sign = {
        "user": user.name if user else "?",
        "surface": decision.surface or "dashboard",
        "when": decision.at,
        "decision_id": decision.id,
    }

    def sign_now(sheet_row: int) -> None:
        # Round 5: first the "appended" mark, committed at once. If the notes API is down too,
        # the retry and the undo still know this row is ours and can sign it.
        mark = rowadd.Plan.loads(decision.before)
        if mark is not None:
            decision.before = mark.appended(sheet_row, run.moment.isoformat()).dumps()
            _commit_and_retake(session, row, decision)
        # M1, L4: sign right after the append, before the read-back. If the read-back or the
        # network fails next, the row in the sheet is already signed and an undo can remove it.
        rowadd.sign_added(run.sheet, title, resolution, sheet_row, **sign)

    def sign_retry(sheet_row: int) -> None:
        if not rowadd.sign_added(run.sheet, title, resolution, sheet_row, **sign):
            raise rowadd.SignFailed("the notes API is down; the row is signed on the next try")

    placed = rowadd.append_person(
        run.sheet,
        title,
        resolution,
        len(header_row),
        cells,
        rowadd.Plan.loads(decision.before),
        on_plan=save_plan,
        header_row=run.header_rows.get(title, 0),
        on_written=sign_now,
        decision_id=decision.id,
        sign_adopted=sign_retry,
    )
    marked = rowadd.Plan.loads(decision.before)
    decision.before = replace(placed, appended_at=marked.appended_at if marked else None).dumps()
    # A8: the note is the proof of who added the row. One more try, then say it out loud: the
    # row stays in the sheet, but the write is `failed`, never quietly done.
    signed = rowadd.is_signed(
        run.sheet, title, resolution, placed.row or 0, decision.id
    ) or rowadd.sign_added(run.sheet, title, resolution, placed.row or 0, **sign)
    session.add(
        SyncAudit(
            outbox_id=row.id, before="", after=f"row {placed.row}", by=user.short if user else None
        )
    )
    if not signed:
        raise RowFailed(NO_NOTE)
    return "", f"row {placed.row}"


def _push_remove(
    session: Session, run: _Run, row: SyncOutbox, decision: Decision, original: Decision
) -> tuple[str, str]:
    """Undo of a row add: delete the row only when it still holds exactly what Saley wrote."""
    user = session.get(User, decision.user_id) if decision.user_id else None
    title = run.titles.get(row.tab)
    if not title:
        raise RowFailed("the sheet tab was never read")
    resolution, _header = run.header(title)
    plan = rowadd.read_plan(original.before)
    who = session.get(User, original.user_id) if original.user_id else None

    def sign_first(sheet_row: int) -> None:
        # Round 5: the add worked but its signing was lost. Sign it (as the person who added it),
        # then the removal goes on under the usual checks.
        done = rowadd.sign_added(
            run.sheet,
            title,
            resolution,
            sheet_row,
            user=who.name if who else "?",
            surface=original.surface or "dashboard",
            when=original.at,
            decision_id=original.id,
        )
        if not done:
            raise rowadd.SignFailed("the notes API is down; the undo is tried again")

    removed = rowadd.remove_added(
        run.sheet,
        title,
        resolution,
        plan,
        header_row=run.header_rows.get(title, 0),
        signed_by=original.id,
        sign=sign_first,
    )
    gone = f"row {plan.row} removed" if removed else "row already gone"
    session.add(
        SyncAudit(
            outbox_id=row.id, before=f"row {plan.row}", after=gone, by=user.short if user else None
        )
    )
    return f"row {plan.row}", gone


def _push_one(session: Session, run: _Run, row: SyncOutbox) -> tuple[str, str]:
    """Do one write or restore. Returns (before, after) as read from the sheet."""
    if row.op == "append":
        return _push_append(session, run, row)
    target = row.value or ""
    if row.op != "restore":
        try:
            refuse_formula(target)  # H1: also checked in the web and in SQL; never sent as is
        except FormulaRefused as exc:
            raise RowFailed(str(exc)) from exc
    decision = session.get(Decision, row.decision_id, with_for_update=True)
    if decision is None:
        raise RowFailed("the decision is missing")
    user = session.get(User, decision.user_id) if decision.user_id else None
    if row.op == "restore" and decision.undo_of:
        first = session.get(Decision, decision.undo_of)
        if first is not None and first.kind == "row_add":
            return _push_remove(session, run, row, decision, first)
    contact = session.scalar(select(Contact).where(Contact.sheet_row_key == row.row_key))
    if contact is None:
        raise RowFailed("the person is not in the database")
    title = run.titles.get(row.tab)
    if not title:
        raise RowFailed("the sheet tab was never read")
    resolution, header_row = run.header(title)
    column = assert_writable(resolution, row.role, email_write=run.email_write_allowed)
    sheet_row = _locate(run, title, resolution, contact)

    current = norm_value(row.role, run.sheet.read_cell(title, sheet_row, column))
    original: Decision | None = None
    if row.op == "restore":
        original = session.get(Decision, decision.undo_of) if decision.undo_of else None
        if original is not None and current == norm_value(row.role, target):
            # A retry after a lost read-back or note: the cell is already restored (H6 d).
            log.info("restore_already_done", outbox=row.id, role=row.role)
            return current, current
        if original is None or current != norm_value(row.role, original.after):
            log.warning("restore_refused", outbox=row.id, role=row.role, reason="cell changed")
            raise RowFailed("cell changed since, not restored")
    elif decision.before is None:
        decision.before = current  # the first read only: a retry must not overwrite it
        session.flush()

    # A restored old value goes back as literal text (H1 holds: no formula ever leaves).
    run.sheet.write_cell(title, sheet_row, column, as_literal(target))
    back = norm_value(row.role, run.sheet.read_cell(title, sheet_row, column))
    if back != norm_value(row.role, target):
        raise ReadBackMismatch("the cell did not hold the value on read-back")

    label = str(header_row[column]) if column < len(header_row) else row.role
    _sign(run, title, sheet_row, resolution, decision, original, user, label, target)
    session.add(
        SyncAudit(outbox_id=row.id, before=current, after=back, by=user.short if user else None)
    )
    mirror_value(contact, row.role, target)
    return current, back


def _sign(
    run: _Run,
    title: str,
    sheet_row: int,
    resolution: Resolution,
    decision: Decision,
    original: Decision | None,
    user: User | None,
    label: str,
    target: str,
) -> None:
    """Append the D47 line to the Name note. A note failure never undoes the cell write."""
    name_col = resolution.columns.get("name")
    if name_col is None:
        return
    who = user.name if user else "?"
    surface = decision.surface or "dashboard"
    if original is not None:
        line = undo_line(original.id, who, surface, decision.at)
    else:
        line = write_line(label, target, who, surface, decision.at, decision.id)
    try:
        existing = run.sheet.read_note(title, sheet_row, name_col)
        run.sheet.write_note(title, sheet_row, name_col, append_note(existing, line))
    except Exception as exc:  # the value is written; say so and go on
        log.warning("note_failed", error=type(exc).__name__, detail=scrub_text(str(exc)))


def _handle(
    session: Session, run: _Run, row: SyncOutbox, stats: PushStats, *, scratch: bool = False
) -> None:
    if row.row_key.startswith(SCRATCH_KEY_PREFIX) and not scratch:
        # Second guard (T9): only `push_selected` may push a test row, and only to the scratch tab.
        log.error("scratch_row_refused", outbox=row.id)
        return
    try:
        _push_one(session, run, row)
    except PushCancelled:
        log.info("push_cancelled_by_undo", outbox=row.id)
        return
    except (WritesRefused, RowFailed, rowadd.RowAddRefused) as exc:
        row.state, row.error = "failed", str(exc)[:300]
        stats.failed += 1
        log.warning("write_refused", outbox=row.id, role=row.role, reason=str(exc)[:120])
    except SQLAlchemyError:
        raise
    except Exception as exc:  # a sheet or network error: try again later, up to MAX_TRIES
        row.tries += 1
        row.error = (scrub_text(f"{type(exc).__name__}: {exc}") or "")[:300]
        if row.tries >= MAX_TRIES:
            row.state = "failed"
            stats.failed += 1
        else:
            row.state = "pending"
            wait = min(BACKOFF_SECONDS * 2**row.tries, BACKOFF_CAP_SECONDS)
            row.next_at = run.moment + timedelta(seconds=wait)
            stats.retry += 1
        log.warning("push_error", outbox=row.id, tries=row.tries, error=type(exc).__name__)
    else:
        row.state, row.error = "done", None
        stats.done += 1
    row.updated_at = run.moment


def push_pending(
    sheet: SheetPort,
    *,
    now: datetime | None = None,
    email_write_allowed: bool = False,
    scope: Scope = session_scope,
    limit: int = PUSH_BATCH,
) -> PushStats:
    """Push due rows one by one. One transaction per row, so one bad row blocks no other."""
    moment = now or now_ist()
    with scope() as session:
        titles = dict(session.execute(select(SheetTabStatus.tab, SheetTabStatus.title)).all())
    run = _Run(sheet, titles, email_write_allowed, moment, {})
    stats = PushStats()
    for _ in range(limit):
        with scope() as session:
            row = session.scalars(
                _due_query(moment)
                .order_by(SyncOutbox.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).first()
            if row is None:
                break
            _handle(session, run, row, stats)
    return stats


def push_selected(
    sheet: SheetPort,
    ids: list[int],
    *,
    titles: dict[str, str | None],
    now: datetime | None = None,
    email_write_allowed: bool = False,
    scope: Scope = session_scope,
) -> PushStats:
    """Push only these outbox rows, to the tab titles given (R5-W01..W04).

    Why: the live sheet cases (`saley sheet roundtrip --case`) must push their own rows to the
    SALEY_TEST tab and touch no other pending row. `push_pending` takes the next due row of
    anybody and finds the tab title in the database. Here the caller names both. A row that is
    not open any more is skipped. The same `_handle` runs, so the locks, the read-back and the
    signature are the ones the worker uses.
    """
    moment = now or now_ist()
    run = _Run(sheet, titles, email_write_allowed, moment, {})
    stats = PushStats()
    for outbox_id in sorted(ids):
        with scope() as session:
            row = session.scalars(
                select(SyncOutbox)
                .where(SyncOutbox.id == outbox_id, SyncOutbox.state.in_(OPEN_STATES))
                .with_for_update(skip_locked=True)
            ).first()
            if row is not None:
                _handle(session, run, row, stats, scratch=True)
    return stats
