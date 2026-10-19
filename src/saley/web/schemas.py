"""Response models: each endpoint returns only the fields its view shows (R1.1 ticket C).

Why: a query may select more than a page needs. These models drop every extra field on the
way out (`extra="ignore"`), so raw cells, env values or ids never leak by accident.
Every response carries the snapshot it was read from (`sync_id`, D40).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Evidence(Out):
    row: str | None = None


class Meta(Out):
    sync_id: int | None
    last_computed: str | None
    last_computed_label: str
    stale: bool


class Link(Out):
    view: str
    filter: str | None = None


class Tile(Out):
    key: str
    label: str
    value: int | str | None
    detail: str
    source: str
    link: Link


class TodayOut(Meta):
    greeting: str
    date_label: str
    tiles: list[Tile]


class StatusOut(Meta):
    state: str
    message: str
    updated: str
    sheet: str
    news: str
    sheet_mode: str
    late_total: int


class TeamMember(Out):
    short: str
    name: str
    role: str


class MeOut(Out):
    email: str
    role: str
    dev: bool
    people: list[TeamMember]


# ------------------------------------------------------------------ goals


class GoalPerson(Out):
    id: int
    name: str
    company: str | None
    stage: str
    row: str | None


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
    blockers: list[GoalBlocker]
    blocker_count: int
    rag: str
    why: str
    evidence: Evidence


class GoalsOut(Meta):
    items: list[GoalItem]
    total: int
    linked_by: str


# ------------------------------------------------------------------ accounts and people


class Industry(Out):
    industry: str
    people: int
    contacted: int
    pct: int | None


class AccountItem(Out):
    id: int
    name: str
    industry: str | None
    geo: str | None
    funding: str | None
    in_master: bool
    goal_codes: list[str]
    people: int
    contacted: int
    connected: int
    emailed: int
    p1_count: int
    threads: int
    coverage_pct: int | None
    warmth: str
    evidence: Evidence


class AccountsOut(Meta):
    items: list[AccountItem]
    total: int
    filter: str
    with_people: int
    in_master: int
    industries: list[Industry]


class TouchBrief(Out):
    type: str
    date: date | None


class PersonRow(Out):
    id: int
    name: str
    company: str | None
    account_id: int
    title: str | None
    stage: str
    owner: str | None
    priority: str | None
    is_p1: bool
    city: str | None
    has_email: bool
    next_step: str | None
    about: str | None
    industry: str | None
    li_status: str | None
    last_touches: list[TouchBrief]
    evidence: Evidence


class PeopleCounts(Out):
    people: int
    with_notes: int
    with_paper: int
    backlog: int
    p1: int
    p1_waiting: int
    in_conversation: int
    reconnect_due: int


class PeopleOut(Meta):
    items: list[PersonRow]
    total: int
    filter: str
    counts: PeopleCounts


class Touch(Out):
    type: str
    date: date | None
    outcome: str | None
    source: str | None


class StageEntry(Out):
    stage: str
    from_at: datetime
    reason: str | None
    name: str | None = None


class Opportunity(Out):
    package_text: str | None
    status: str | None
    prob: int | None
    size_usd: int | None
    size_text: str | None
    deal_status: str | None


class PersonDetail(Out):
    id: int
    name: str
    title: str | None
    account_id: int
    company: str | None
    industry: str | None
    stage: str
    owner: str | None
    owner_source: str | None
    priority: str | None
    city: str | None
    email: str | None
    email_href: str | None
    li_href: str | None
    site_text: str | None
    site_href: str | None
    paper_href: str | None
    next_step: str | None
    li_status: str | None
    about: str | None
    notes: str | None
    touches: list[Touch]
    stage_history: list[StageEntry]
    opportunity: Opportunity | None
    evidence: Evidence


class PersonOut(Meta):
    item: PersonDetail


class AccountPerson(Out):
    id: int
    name: str
    title: str | None
    stage: str
    evidence: Evidence


class AccountTouch(Out):
    name: str
    type: str
    date: date | None


class AccountDetail(AccountItem):
    people_list: list[AccountPerson]
    touches: list[AccountTouch]
    stage_history: list[StageEntry]


class AccountOut(Meta):
    item: AccountDetail


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


class ScoreRow(Out):
    industry: str
    people: int
    contacted: int
    connect_pct: int | None
    reply_pct: int | None
    reply_note: str
    meetings: int
    deals: int


class ScorecardOut(Meta):
    items: list[ScoreRow]
    total: int


# ------------------------------------------------------------------ health


class Job(Out):
    job: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    note: str | None


class ParityRow(Out):
    metric: str
    sheet_value: int | None
    view_value: int | None
    ok: bool
    note: str | None


class Quality(Out):
    problem: str
    n: int


class EgressRow(Out):
    host: str
    calls: int
    errors: int


class Llm(Out):
    calls: int
    cached: int


class NewsStep(Out):
    step: str
    n: int | None


class TabStatus(Out):
    tab: str
    title: str | None
    status: str
    missing_headers: list[str]
    new_headers: list[str]
    stale_since: datetime | None
    reason: str | None


class HealthOut(Meta):
    jobs: list[Job]
    parity: list[ParityRow]
    quality: list[Quality]
    missing_industry: int
    egress: list[EgressRow]
    llm: Llm
    llm_cap: int
    news: list[NewsStep]
    news_note: str
    sheet_mode: str
    tabs: list[TabStatus]


# ------------------------------------------------------------------ search


class Hit(Out):
    type: str
    id: int | str | None
    name: str
    context: str
    pill: str | None
    owner: str | None
    match: list[int] | None  # [start, length] in `name`, for bold text


class SearchGroup(Out):
    items: list[Hit]
    total: int


class SearchOut(Meta):
    q: str
    groups: dict[str, SearchGroup]
    total: int
    decisions_available: bool


def strip(model: type[Out], data: dict[str, Any]) -> dict[str, Any]:
    """Validate and drop extra keys (used in tests to check field sets)."""
    return model.model_validate(data).model_dump()
