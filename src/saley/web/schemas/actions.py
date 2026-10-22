"""Request and response models of writes (`/api/act`, undo, confirm), plus the Inbox,
commitments and decisions.

Why: every body that changes state, and every list of such changes, lives in one place.
Request bodies refuse unknown fields.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field

from saley.web.schemas.common import Evidence, Meta, Out, StrictIn
from saley.web.schemas.today import TaskAction

# ------------------------------------------------------------------ act (R2, D24, D45)


class ActCell(StrictIn):
    role: str = Field(min_length=1, max_length=40, pattern=r"^[a-z_0-9]+$")
    value: str = Field(max_length=200)


class ActPayload(StrictIn):
    """One cell as role + value, or up to 5 cells, or a task code. Never anything else."""

    role: str | None = Field(default=None, max_length=40, pattern=r"^[a-z_0-9]+$")
    value: str | None = Field(default=None, max_length=200)
    cells: list[ActCell] | None = Field(default=None, max_length=5)
    # row_add (D76): the cells of the new row, as role + value. Up to 12, never more.
    fields: list[ActCell] | None = Field(default=None, max_length=12)
    task: (
        Literal["research", "email1", "email2", "email3", "li_dm", "reconnect", "status_check"]
        | None
    ) = None
    # A column-write Done names the task only; the server picks the value (D153).
    done: Literal["meeting", "status_check", "li_confirm", "li_first"] | None = None
    cycle: date | None = None  # the due day of the task round; part of the instruction key
    proposal_id: int | None = Field(default=None, ge=1, le=2**62)


class ActIn(StrictIn):
    action: Literal["write", "email_write", "task_done", "row_add", "find_people"]
    target_ids: list[Annotated[int, Field(ge=1, le=2**31 - 1)]] = Field(min_length=1, max_length=50)
    payload: ActPayload | None = None


class ActItem(Out):
    write_id: int
    role: str
    value: str
    already: bool
    undo_until: datetime | None
    label: str


class ActOut(Out):
    ok: bool
    already: bool
    write_id: int
    undo_until: datetime | None
    label: str
    items: list[ActItem]


class UndoOut(Out):
    ok: bool
    write_id: int
    cancelled: bool


class ConfirmOut(Out):
    ok: bool
    decision_id: int
    already: bool


class DismissOut(Out):
    ok: bool
    proposal_id: int
    already: bool


class ProposalEditIn(StrictIn):
    """The inline editor of an action card sends the edited draft text (D129)."""

    text: str = Field(min_length=1, max_length=200, pattern=r"\S")


class ProposalEditOut(Out):
    ok: bool
    proposal_id: int
    already: bool
    write_id: int
    undo_until: datetime | None
    label: str


class CommitmentDoneOut(Out):
    ok: bool
    commitment_id: int
    already: bool
    write_id: int | None = None
    undo_until: datetime | None = None


# ------------------------------------------------------------------ inbox, commitments, decisions


class InboxItem(Out):
    kind: Literal["card", "confirm", "ask", "reminder"]
    id: int
    type: str
    title: str
    why: str | None
    person: str | None
    for_user: str | None
    at: datetime | None
    button: str | None
    action: TaskAction | None
    dismiss: str | None
    evidence: Evidence


class InboxOut(Meta):
    items: list[InboxItem]
    total: int
    empty: str


class CommitmentItem(Out):
    id: int
    owner_short: str
    owner_name: str
    text: str
    due: date | None
    due_label: str | None
    source: str
    source_removed: bool = False
    state: str
    reminders_sent: int
    last_reminded_on: date | None
    in_pack: bool
    # R5 T16a: the signed-in person's own Done still inside its window (a reload keeps Undo).
    undo_id: int | None = None
    undo_until: datetime | None = None


class CommitmentsOut(Meta):
    items: list[CommitmentItem]
    total: int


class DecisionItem(Out):
    id: int
    kind: str
    label: str | None
    text: str | None
    what: str | None
    value: str | None
    state: str
    undo_state: str
    undo_until: datetime | None
    at: datetime
    surface: str | None
    user_short: str | None
    user_name: str | None
    person: str | None
    contact_id: int | None
    confirmed_by_name: str | None
    confirmed_at: datetime | None
    can_confirm: bool
    can_undo: bool
    sync_state: str | None
    sync_reason: str | None


class DecisionsOut(Meta):
    items: list[DecisionItem]
    total: int
