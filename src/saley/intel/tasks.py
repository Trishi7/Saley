"""My tasks per person: pure rules, no database, no words (R2, plan 3.5, D31).

Why: the LLM never picks a person and never sets a stage; plain rules decide (CLAUDE.md).
These functions take plain rows and a date and return `Task` values. The web layer reads the
rows from views, calls `tasks_for`, and adds the display text from `web/words.py`.

Who gets what (docs/08 view 1):
- Sid: LinkedIn connections to confirm (LI request sent, no connected date), the first
  LinkedIn picks (R05, top 5), reconnects due (R14, D14/D36), meetings today, and a
  meeting that is still "booked" after its date (D51).
- Vaishnavi: add an email (R06, D154) and the email steps (R13, anchor D51). D31 keeps
  these on Sid's list when First Contact names Sid.
- Kushal: decisions to confirm, Inbox cards, writes by others inside their undo window.
- Everyone: commitments due today.

Each task carries the call that "Done" makes. A column write goes to POST /api/act with the
task name only (`payload.done`): the server picks the value from the column's own cells (D153).
A STEP-block task is `task_done`: it never writes the sheet (D49).
No date is guessed: a rule with no anchor date gives no task.

A tick hides a task for its own cycle only. The cycle is the task's due day (reconnect, email
steps). A tick made on or after that day hides the task; when the sheet moves the
anchor and a later due day comes, the task shows again, and its Done call carries the new cycle
so the server sees a new instruction (D48) and not "already done".

Order (D152, D180): `sort_key` gives (due day, importance, PoC Priority, company, name).
A tick of the person's own stays on the list, as an Undo, until its undo window closes
(`ticked_tasks`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any

TEAM = ("sid", "vaishnavi", "kushal")
PICK_COUNT = 5  # R05: five first LinkedIn picks per post
ADD_EMAIL_AFTER_DAYS = 3  # R06: connected more than 3 days ago, no email
DECISION_CAP = 10  # many decisions can wait; the Decisions view lists the rest
# R13 timers: email 1 two days after the anchor, email 2 seven days after, email 3 three days.
EMAIL_GAPS: dict[str, int] = {"email1": 2, "email2": 7, "email3": 3}
EMAIL_STAGES = frozenset({"Connected", "Engaged"})
OPEN_MEETING = frozenset({"", "booked", "scheduled", "confirmed", "tbd"})
BOOKED = frozenset({"booked", "scheduled", "confirmed"})
ACT = "/api/act"

# Importance of a kind, 1 first (D152). Same due day: lower number comes first.
# meeting and status_check 1 · reconnect 2 · email step 3 · LinkedIn 4 · commitment 5 · rest 6.
# add_email is the old "research" task (R06). It has no Done: it ends when an email is saved (D154).
IMPORTANCE: dict[str, int] = {
    "meeting": 1,
    "status_check": 1,
    "reconnect": 2,
    "add_email": 3,
    "email1": 3,
    "email2": 3,
    "email3": 3,
    "li_confirm": 4,
    "li_first": 4,
    "commitment": 5,
    "decision": 6,
    "review_write": 6,
    "inbox_cards": 6,
}
DEFAULT_IMPORTANCE = 6
# PoC Priority (column AJ): P1 before P2 before P3 before blank (D152).
PRIORITY_RANK = {"P1": 1, "P2": 2, "P3": 3}
BLANK_PRIORITY_RANK = 4
# Roles of a Done tick (task_done) and the cell writes of the column tasks. They let a task
# stay on the page, with its Undo, while its tick is inside the undo window (R5 T3).
TICK_ROLE_KIND = {
    "task:reconnect": "reconnect",
    "task:email1": "email1",
    "task:email2": "email2",
    "task:email3": "email3",
}
CELL_ROLE_KIND = {
    "li_connected_date": "li_confirm",
    "first_contact": "li_first",
    "first_contact_type": "li_first",
    "first_contact_date": "li_first",
}
# Kinds whose Done is a cell write the SERVER fills from the column's own values (D153).
DONE_VALUE_KINDS = frozenset({"meeting", "status_check", "li_confirm", "li_first"})


@dataclass(frozen=True)
class Action:
    """The HTTP call a Done tick makes. `body` is None for a call with no body."""

    endpoint: str
    body: dict[str, Any] | None = None


@dataclass(frozen=True)
class Task:
    key: str
    kind: str
    person: str
    contact_id: int | None = None
    name: str | None = None
    company: str | None = None
    due: date | None = None
    action: Action | None = None
    undo_id: int | None = None  # a write by someone else that this person can undo
    undo_ids: tuple[int, ...] = ()  # every write of one Done tick (li_first writes 3 cells)
    undo_until: datetime | None = None  # when the undo window of that tick closes
    ticked: bool = False  # this person's own Done, still inside its undo window
    priority: str | None = None  # PoC Priority (AJ), for the order only
    input_role: str | None = None  # the column the person may type a value for (email_write)
    soon: str | None = None  # a feature that is off: the field shows "coming soon" (D154)
    params: Mapping[str, Any] = field(default_factory=dict)  # values the text templates use


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):  # a datetime is also a date: cut it down to the day
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


@dataclass(frozen=True)
class ContactRow:
    """The columns of v_task_inputs that the rules read."""

    contact_id: int
    name: str
    company: str | None = None
    owner: str | None = None
    owner_source: str | None = None
    stage: str = "Listed"
    is_p1: bool = False
    priority: str | None = None
    application_bucket: str | None = None
    has_email: bool = False
    contacted: bool = False
    connected: bool = False
    li_requested: bool = False
    li_connected_date: date | None = None
    first_contact_date: date | None = None
    last_step_date: date | None = None
    email_dates: tuple[date | None, date | None, date | None] = (None, None, None)
    email_sent: tuple[bool, bool, bool] = (False, False, False)
    li_dm_date: date | None = None
    li_dm_sent: bool = False
    meeting_date: date | None = None
    meeting_status: str | None = None
    reconnect_due: bool = False
    reconnect_due_on: date | None = None
    open_roles: frozenset[str] = frozenset()
    email_added: bool = False  # an email_write decision is live: pending, held or written (D154)
    done_tasks: frozenset[str] = frozenset()
    done_on: Mapping[str, date] = field(default_factory=dict)  # role → newest tick day

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> ContactRow:
        return cls(
            contact_id=row["contact_id"],
            name=row["name"],
            company=row.get("company"),
            owner=row.get("owner"),
            owner_source=row.get("owner_source"),
            stage=row.get("stage") or "Listed",
            is_p1=bool(row.get("is_p1")),
            priority=row.get("priority"),
            application_bucket=row.get("application_bucket"),
            has_email=bool(row.get("has_email")),
            contacted=bool(row.get("contacted")),
            connected=bool(row.get("connected")),
            li_requested=bool(row.get("li_requested")),
            li_connected_date=row.get("li_connected_date"),
            first_contact_date=row.get("first_contact_date"),
            last_step_date=row.get("last_step_date"),
            email_dates=(row.get("email1_date"), row.get("email2_date"), row.get("email3_date")),
            email_sent=(
                bool(row.get("email1_sent")),
                bool(row.get("email2_sent")),
                bool(row.get("email3_sent")),
            ),
            li_dm_date=row.get("li_dm_date"),
            li_dm_sent=bool(row.get("li_dm_sent")),
            meeting_date=row.get("meeting_date"),
            meeting_status=row.get("meeting_status"),
            reconnect_due=bool(row.get("reconnect_due")),
            reconnect_due_on=row.get("reconnect_due_on"),
            open_roles=frozenset(row.get("open_roles") or ()),
            email_added=bool(row.get("email_added")),
            done_tasks=frozenset(row.get("done_tasks") or ()),
            done_on={k: _as_date(v) for k, v in (row.get("done_on") or {}).items()},
        )


@dataclass(frozen=True)
class ProposedDecision:
    id: int
    label: str | None
    by: str | None = None
    at: date | None = None


@dataclass(frozen=True)
class RecentWrite:
    """A write inside its undo window."""

    id: int
    user_short: str
    user_name: str
    role: str
    contact_id: int | None
    person: str | None
    undo_until: Any = None


@dataclass(frozen=True)
class CommitmentRow:
    id: int
    owner_short: str
    text: str
    due: date | None
    source_type: str | None = None
    state: str = "open"


@dataclass(frozen=True)
class Inputs:
    contacts: Sequence[ContactRow] = ()
    proposed: Sequence[ProposedDecision] = ()
    open_cards: int = 0
    recent_writes: Sequence[RecentWrite] = ()
    commitments: Sequence[CommitmentRow] = ()


# ------------------------------------------------------------------ helpers


def done_value_action(contact_id: int, kind: str) -> Action:
    """A column-write Done. It names the task and no value: the server picks the value from
    the cells the column already holds (D153), so a client can never send a new word."""
    return Action(ACT, {"action": "write", "target_ids": [contact_id], "payload": {"done": kind}})


def done_action(contact_id: int, task: str, cycle: date | None = None) -> Action:
    """A STEP-block task: logged and touched, never written to the sheet (D49).

    `cycle` is the due day of this round of the task. It goes into the instruction key, so a
    tick in the next round is a new instruction and not the old one again.
    """
    payload: dict[str, Any] = {"task": task}
    if cycle is not None:
        payload["cycle"] = cycle.isoformat()
    return Action(ACT, {"action": "task_done", "target_ids": [contact_id], "payload": payload})


def is_done(row: ContactRow, task: str, cycle: date | None) -> bool:
    """True when this task was ticked for this cycle (a tick on or after the cycle day).

    A tick without a known day (old rows) or a task without a cycle day counts as done: the
    safe side is to ask nothing twice.
    """
    role = f"task:{task}"
    if role not in row.done_tasks and role not in row.done_on:
        return False
    ticked = row.done_on.get(role)
    return cycle is None or ticked is None or ticked >= cycle


def _status(row: ContactRow) -> str:
    return (row.meeting_status or "").strip().casefold()


def _bucket_rank(bucket: str | None) -> int:
    """A-F sort first, in letter order. Anything else sorts last."""
    first = (bucket or "").strip()[:1].upper()
    return ord(first) - ord("A") if first in "ABCDEF" and first else 99


def _step_assignee(row: ContactRow) -> str:
    """D31: First Contact text 'Sid' keeps the step on Sid's list. Else Vaishnavi."""
    if row.owner == "sid" and row.owner_source == "first_contact":
        return "sid"
    return "vaishnavi"


def _is_free(row: ContactRow, *roles: str) -> bool:
    """No write for these roles is waiting in the outbox (the human already asked)."""
    return not set(roles) & row.open_roles


def _base(row: ContactRow, kind: str, person: str) -> dict[str, Any]:
    return {
        "key": f"{kind}:{row.contact_id}",
        "kind": kind,
        "person": person,
        "contact_id": row.contact_id,
        "name": row.name,
        "company": row.company,
        "priority": row.priority,
    }


# ------------------------------------------------------------------ Sid


def li_confirm_tasks(rows: Sequence[ContactRow]) -> list[Task]:
    """LI request sent, no connected date. Done writes LI Connected Date = today."""
    out = []
    for r in rows:
        if r.stage in ("Won", "Lost") or not r.li_requested or r.li_connected_date is not None:
            continue
        if not _is_free(r, "li_connected_date"):
            continue
        action = done_value_action(r.contact_id, "li_confirm")
        out.append(Task(**_base(r, "li_confirm", "sid"), action=action))
    return sorted(out, key=lambda t: ((t.company or "").casefold(), (t.name or "").casefold()))


def first_pick_tasks(rows: Sequence[ContactRow], count: int = PICK_COUNT) -> list[Task]:
    """R05: top picks of people with a blank First Contact. P1 first, then bucket A-F."""
    pool = [
        r
        for r in rows
        if not r.contacted
        and not r.li_requested
        and r.stage == "Listed"
        and _is_free(r, "first_contact", "first_contact_type", "first_contact_date")
    ]
    pool.sort(
        key=lambda r: (
            not r.is_p1,
            _bucket_rank(r.application_bucket),
            (r.company or "").casefold(),
            r.name.casefold(),
            r.contact_id,
        )
    )
    return [
        Task(**_base(r, "li_first", "sid"), action=done_value_action(r.contact_id, "li_first"))
        for r in pool[: max(count, 0)]
    ]


def reconnect_tasks(rows: Sequence[ContactRow]) -> list[Task]:
    """R14: reconnect due (D14, D36). Reconnect Date is read-only, so Done is task_done."""
    out = []
    for r in rows:
        if not r.reconnect_due or is_done(r, "reconnect", r.reconnect_due_on):
            continue
        out.append(
            Task(
                **_base(r, "reconnect", "sid"),
                due=r.reconnect_due_on,
                action=done_action(r.contact_id, "reconnect", r.reconnect_due_on),
                params={"day": r.reconnect_due_on},
            )
        )
    return sorted(out, key=lambda t: (t.due or date.max, (t.name or "").casefold()))


def meeting_tasks(rows: Sequence[ContactRow], today: date) -> list[Task]:
    """Meetings today, and booked meetings past their date (D51). Done writes 'completed'."""
    out = []
    for r in rows:
        if r.meeting_date is None or not _is_free(r, "meeting_status"):
            continue
        if r.meeting_date == today and _status(r) in OPEN_MEETING:
            action = done_value_action(r.contact_id, "meeting")
            out.append(Task(**_base(r, "meeting", "sid"), due=today, action=action))
        elif r.meeting_date < today and _status(r) in BOOKED:
            action = done_value_action(r.contact_id, "status_check")
            params = {"day": r.meeting_date, "status": r.meeting_status}
            out.append(
                Task(
                    **_base(r, "status_check", "sid"),
                    due=r.meeting_date,
                    action=action,
                    params=params,
                )
            )
    return sorted(out, key=lambda t: (t.kind, (t.name or "").casefold()))


# ------------------------------------------------------------------ Vaishnavi


def add_email_tasks(rows: Sequence[ContactRow], today: date, email_write: bool) -> list[Task]:
    """R06, D154: connected more than 3 days ago, no email, no DM. Needs a connected date.

    No Done button: the task ends when an email is saved. It hides while an `email_write`
    decision is live (pending, held or written) and comes back when that is undone. While
    EMAIL_WRITE_ALLOWED is off the task stays, with `soon` set so the page can show "coming soon".
    """
    out = []
    for r in rows:
        if not r.connected or r.has_email or r.li_connected_date is None:
            continue
        age = (today - r.li_connected_date).days
        if age <= ADD_EMAIL_AFTER_DAYS or r.li_dm_date or r.li_dm_sent:
            continue
        if r.email_added or not _is_free(r, "email"):
            continue
        out.append(
            Task(
                **_base(r, "add_email", _step_assignee(r)),
                due=r.li_connected_date + timedelta(days=ADD_EMAIL_AFTER_DAYS + 1),
                input_role="email",
                soon=None if email_write else "email_write",
                params={"n": age},
            )
        )
    return sorted(out, key=lambda t: (t.due or date.max, (t.name or "").casefold()))


def next_email_step(row: ContactRow) -> tuple[str, date] | None:
    """The first email not sent yet and the day it is due. None when it cannot be known.

    D51: the anchor is the latest step date, else the First Contact Date. With no anchor
    there is no due day, so there is no task.
    """
    for i, code in enumerate(("email1", "email2", "email3")):
        if row.email_sent[i] or row.email_dates[i] is not None:
            continue
        anchor = row.last_step_date or row.first_contact_date
        if anchor is None:
            return None
        return code, anchor + timedelta(days=EMAIL_GAPS[code])
    return None


def email_step_tasks(rows: Sequence[ContactRow], today: date) -> list[Task]:
    """R13: the next email for a connected person who has an address and no meeting."""
    out = []
    for r in rows:
        if not (r.connected and r.has_email) or r.stage not in EMAIL_STAGES or r.meeting_date:
            continue
        step = next_email_step(r)
        if step is None:
            continue
        code, due = step
        if due > today or is_done(r, code, due):
            continue
        out.append(
            Task(
                **_base(r, code, _step_assignee(r)),
                due=due,
                action=done_action(r.contact_id, code, due),
                params={"day": due},
            )
        )
    return sorted(out, key=lambda t: (t.due or date.max, (t.name or "").casefold()))


# ------------------------------------------------------------------ Kushal and everyone


def decision_tasks(proposed: Sequence[ProposedDecision], cap: int = DECISION_CAP) -> list[Task]:
    return [
        Task(
            key=f"decision:{d.id}",
            kind="decision",
            person="kushal",
            action=Action(f"/api/decision/{d.id}/confirm"),
            params={"id": d.label or f"#{d.id}", "who": d.by, "day": d.at},
        )
        for d in proposed[:cap]
    ]


def card_task(open_cards: int) -> list[Task]:
    if open_cards <= 0:
        return []
    return [Task(key="inbox_cards", kind="inbox_cards", person="kushal", params={"n": open_cards})]


def review_tasks(writes: Sequence[RecentWrite]) -> list[Task]:
    """Writes by others inside their undo window. The only action is Undo."""
    return [
        Task(
            key=f"review_write:{w.id}",
            kind="review_write",
            person="kushal",
            contact_id=w.contact_id,
            name=w.person,
            undo_id=w.id,
            params={"who": w.user_name, "role": w.role, "until": w.undo_until},
        )
        for w in writes
        if w.user_short != "kushal"
    ]


def commitment_tasks(person: str, rows: Sequence[CommitmentRow], today: date) -> list[Task]:
    return [
        Task(
            key=f"commitment:{c.id}",
            kind="commitment",
            person=person,
            due=c.due,
            action=Action(f"/api/commitment/{c.id}/done"),
            params={"text": c.text, "source_type": c.source_type},
        )
        for c in rows
        if c.owner_short == person and c.state == "open" and c.due == today
    ]


# ------------------------------------------------------------------ order (D152)


def priority_rank(priority: str | None) -> int:
    """P1 < P2 < P3 < blank or anything else. Only the first two characters count."""
    return PRIORITY_RANK.get((priority or "").strip().upper()[:2], BLANK_PRIORITY_RANK)


def sort_key(task: Task, today: date) -> tuple[date, int, int, int, str, str, str]:
    """(due day, importance, PoC Priority, company, name).

    D180 (amends D152): a task with no due day does not count as today. It ranks after every
    task dated today and before tomorrow's, and shows "No date". The task key is the last part
    only so that two equal tasks keep one fixed order.
    """
    return (
        task.due or today,
        0 if task.due is not None else 1,
        IMPORTANCE.get(task.kind, DEFAULT_IMPORTANCE),
        priority_rank(task.priority),
        (task.company or "").casefold(),
        (task.name or "").casefold(),
        task.key,
    )


def _due_today(task: Task, today: date) -> Task:
    """li_confirm and li_first are due today (docs/08 Due-date rule). Others keep their day."""
    if task.due is None and task.kind in ("li_confirm", "li_first"):
        return replace(task, due=today)
    return task


# ------------------------------------------------------------------ ticked tasks (R5 T3)


def _tick_kind(write: RecentWrite, row: ContactRow, today: date) -> str | None:
    if write.role in TICK_ROLE_KIND:
        return TICK_ROLE_KIND[write.role]
    if write.role == "meeting_status":
        past = row.meeting_date is not None and row.meeting_date < today
        return "status_check" if past else "meeting"
    return CELL_ROLE_KIND.get(write.role)


def ticked_tasks(person: str, inputs: Inputs, today: date) -> list[Task]:
    """This person's own Done ticks that are still inside the undo window.

    Why: the row keeps its place and turns into an Undo until the window closes, so a reload
    does not lose the Undo. A tick outside the window is gone for good.
    """
    rows = {c.contact_id: c for c in inputs.contacts}
    groups: dict[str, list[RecentWrite]] = {}
    for write in inputs.recent_writes:
        row = rows.get(write.contact_id) if write.contact_id is not None else None
        if write.user_short != person or row is None:
            continue
        kind = _tick_kind(write, row, today)
        if kind is not None:
            groups.setdefault(f"{kind}:{row.contact_id}", []).append(write)
    out = []
    for key, writes in groups.items():
        kind, _, cid = key.partition(":")
        row = rows[int(cid)]
        due = row.reconnect_due_on if kind == "reconnect" else None
        due = row.meeting_date if kind == "status_check" else due
        until = [w.undo_until for w in writes if w.undo_until is not None]
        out.append(
            Task(
                **_base(row, kind, person),
                due=due,
                undo_id=writes[0].id,
                undo_ids=tuple(w.id for w in writes),
                undo_until=max(until) if until else None,
                ticked=True,
                params={"day": due, "status": row.meeting_status, "n": None},
            )
        )
    return out


# ------------------------------------------------------------------ entry point


def tasks_for(
    person: str,
    inputs: Inputs,
    today: date,
    *,
    email_write: bool = False,
    viewer: str | None = None,
) -> list[Task]:
    """All tasks for one person today, in the order they should be shown (D152).

    `viewer` is who looks at the list. Ticked rows (with an Undo) show only when the list is the
    viewer's own; None means the list is for its own person (the pack).
    """
    found: list[Task] = []
    rows = inputs.contacts
    if person == "sid":
        found += li_confirm_tasks(rows) + first_pick_tasks(rows) + reconnect_tasks(rows)
        found += meeting_tasks(rows, today)
    found += [
        t
        for t in add_email_tasks(rows, today, email_write) + email_step_tasks(rows, today)
        if t.person == person
    ]
    if person == "kushal":
        found += decision_tasks(inputs.proposed) + card_task(inputs.open_cards)
        found += review_tasks(inputs.recent_writes)
    found += commitment_tasks(person, inputs.commitments, today)
    open_keys = {t.key for t in found}
    if viewer is None or viewer == person:
        found += [t for t in ticked_tasks(person, inputs, today) if t.key not in open_keys]
    return sorted((_due_today(t, today) for t in found), key=lambda t: sort_key(t, today))
