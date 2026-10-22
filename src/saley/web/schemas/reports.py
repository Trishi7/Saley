"""Response models of Goals, Blockers, Money, the Scorecard and the weekly pack.

Why: these views show counts against the goals and the late items, so they read as one group.
"""

from __future__ import annotations

from datetime import date, datetime

from saley.web.schemas.common import Evidence, Meta, Out, Tile

# ------------------------------------------------------------------ goals


class GoalPerson(Out):
    id: int
    name: str
    company: str | None
    stage: str
    row: str | None
    note: str = ""  # one line: the stored next step, else the stage (R5 T5)


class GoalProgress(Out):
    """The ring (D159): won of need. `pct` is None without a need, and the UI shows "—"."""

    won: int
    need: int | None
    pct: int | None


class GoalBlocker(Out):
    item: str
    short: str
    owner: str | None
    due: date | None
    late_by_days: int | None
    status: str | None
    row: str | None


class GoalItem(Out):
    id: int
    code: str
    text: str
    value_text: str | None
    target: str | None
    need: int | None
    due: date | None
    due_label: str
    days_left: int | None
    active_count: int
    active: list[GoalPerson]
    weighted_usd: float | None
    weighted_note: str | None
    won: int
    progress: GoalProgress | None = None
    blockers: list[GoalBlocker]
    blocker_count: int
    rag: str
    why: str
    evidence: Evidence


class GoalsOut(Meta):
    items: list[GoalItem]
    total: int
    linked_by: str


# ------------------------------------------------------------------ blockers and money


class LateItem(Out):
    id: int
    item: str
    short: str
    owner: str | None
    due: date | None
    due_label: str
    late_by_days: int
    status: str | None
    blocks_goals: list[str]
    blocks_motions: list[str]
    evidence: Evidence


class PackageItem(Out):
    id: int
    name: str
    purpose: str | None
    pct: int | None
    status: str | None
    evidence: Evidence


class MoneySummary(Out):
    total_usd: int
    deals: int
    no_value: int


class BlockersOut(Meta):
    late: list[LateItem]
    late_total: int
    packages: list[PackageItem]
    packages_total: int
    money: MoneySummary
    money_tile: Tile


class MoneyItem(Out):
    contact_id: int
    name: str
    company: str | None
    stage: str
    size_usd: int | None
    size_text: str | None
    value_label: str
    deliverable: str
    short: str
    owner: str | None
    late_by_days: int
    more_blocking: int
    evidence: Evidence


class MoneyOut(Meta):
    items: list[MoneyItem]
    total: int
    total_usd: int
    no_value: int


# ------------------------------------------------------------------ scorecard


class ScoreLastWeek(Out):
    """The snapshot of the day 7 days before (D118). A gap inside it stays null."""

    people: int | None
    contacted: int | None
    connect_pct: int | float | None
    reply_pct: int | float | None
    meetings: int | None
    deals: int | None


class ScoreRow(Out):
    industry: str
    people: int
    contacted: int
    connect_pct: int | None
    reply_pct: int | None
    reply_note: str
    meetings: int
    deals: int
    last_week: ScoreLastWeek | None  # null: no snapshot, the page shows a dash (never 0)
    last_week_note: str | None


class ScorecardOut(Meta):
    items: list[ScoreRow]
    total: int


# ------------------------------------------------------------------ weekly pack (R4, D117)


class PackNumber(Out):
    key: str
    label: str
    value: int | float | None
    last_week: int | float | None
    last_week_note: str | None


class PackLate(Out):
    item: str
    owner: str | None
    blocks: list[str]
    due: date | None
    late_by_days: int


class PackReminder(Out):
    owner: str
    text: str
    due: date | None


class PackTask(Out):
    title: str
    due_label: str | None
    kind: str


class PackTasks(Out):
    person: str
    total: int
    items: list[PackTask]


class PackBody(Out):
    week_start: date
    last_week_day: date
    team: list[PackNumber]
    scorecard: list[ScoreRow]
    late_total: int
    late: list[PackLate]
    packed_total: int
    packed_reminders: list[PackReminder]
    tasks: list[PackTasks]


class PackOut(Meta):
    """One stored pack. `empty` is true only when no pack exists at all (then the three pack
    fields are null): a fresh server answers 200, not 404, so the page logs no error (D229).
    `prev_week` / `next_week` are the Mondays of the neighbour packs, for the week picker."""

    week_start: date | None
    built_at: datetime | None
    body: PackBody | None
    prev_week: date | None
    next_week: date | None
    empty: bool
