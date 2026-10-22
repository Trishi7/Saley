"""Response models of the Today page, the status line and the signed-in person (`/api/me`).

Why: the Today page is the only view that nests the signal cards, so it is the only one
that imports the signals module.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from saley.web.schemas.common import Link, Meta, Out, Tile
from saley.web.schemas.signals import NeedsLook, SaleySuggests


class FunnelStep(Out):
    """One bar of the Today funnel. `n` is None while the sheet tab was never read."""

    key: str
    label: str
    n: int | None
    link: Link


class BlockersLine(Out):
    count: int | None
    label: str
    link: Link


class TaskCell(Out):
    role: str
    value: str


class TaskPayload(Out):
    cells: list[TaskCell] | None = None
    task: str | None = None
    cycle: date | None = None
    proposal_id: int | None = None
    done: str | None = None  # a column-write Done: the task name, never a value (D153)


class TaskActionBody(Out):
    action: Literal["write", "email_write", "task_done"]
    target_ids: list[int]
    payload: TaskPayload | None = None


class TaskAction(Out):
    """The call a click makes: a fixed path, and the JSON body for POST /api/act."""

    endpoint: str
    body: TaskActionBody | None = None


class TaskOut(Out):
    key: str
    kind: str
    person: str
    title: str
    why: str | None
    contact_id: int | None
    name: str | None
    company: str | None
    due: date | None
    due_label: str | None
    action: TaskAction | None
    undo_id: int | None
    undo_ids: list[int]
    undo_until: datetime | None
    done: bool  # a tick of this person that is still inside its undo window
    input_role: str | None
    soon: str | None = None  # "email_write" while that feature is off: show "coming soon" (D154)
    link: Link | None


class MyTasks(Out):
    person: str | None
    items: list[TaskOut]
    total: int
    offset: int
    more: int
    more_label: str | None
    empty: str


class TodayOut(Meta):
    """Order of the sections is the order of the page (docs/08 view 1)."""

    greeting: str
    date_label: str
    tiles: list[Tile]
    funnel: list[FunnelStep] = []
    blockers_line: BlockersLine | None = None
    my_tasks: MyTasks
    needs_look: NeedsLook
    saley_suggests: SaleySuggests


class StatusOut(Meta):
    state: str
    message: str
    updated: str
    sheet: str
    news: str
    news_old: list[str]
    sheet_mode: str
    late_total: int
    pending_writes: int
    pending: int
    held: int
    failed: int
    write_note: str | None
    write_bad: bool


class TeamMember(Out):
    short: str
    name: str
    role: str


class Features(Out):
    """On / off flags from the settings (D164). An off feature shows a "coming soon" badge."""

    find_people: bool
    email_write: bool
    speaker_match: bool
    apollo: bool
    mail_replies: bool
    dms: bool


class MeOut(Out):
    email: str
    role: str
    dev: bool
    features: Features
    people: list[TeamMember]
