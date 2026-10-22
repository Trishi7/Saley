"""Response models of Accounts and People: the lists, the detail pages and their timelines.

Why: an account and its people share the touch, stage and timeline shapes.
"""

from __future__ import annotations

from datetime import date, datetime

from saley.web.schemas.common import Evidence, Meta, Out

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
    stage_counts: dict[str, int] = {}
    people_label: str = ""
    tags: list[str] = []
    evidence: Evidence


class TagLegend(Out):
    tag: str
    meaning: str
    source: str


class AccountsOut(Meta):
    items: list[AccountItem]
    total: int
    filter: str
    with_people: int
    in_master: int
    industries: list[Industry]
    tags_legend: list[TagLegend] = []


class TouchBrief(Out):
    type: str
    date: date | None


class WorkingOn(Out):
    """What a person works on, with its source and date. Else "None on file"."""

    found: bool
    text: str
    source: str | None
    date: date | None
    href: str | None


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
    working_on: WorkingOn
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


class TimelineTouch(Out):
    day: date
    type: str
    person: str | None


class TimelineMonth(Out):
    month: str
    label: str
    touches: list[TimelineTouch]


class FollowUpBy(Out):
    """Who follows up, by rule D31. `name` is None when the sheet names no owner."""

    name: str | None
    source: str | None
    rule: str


class Paper(Out):
    text: str
    href: str


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
    working_on: WorkingOn
    follow_up_by: FollowUpBy | None = None
    papers: list[Paper] = []
    papers_label: str = ""
    timeline: list[TimelineMonth] = []
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
    timeline: list[TimelineMonth] = []
    evidence_hidden: bool = False  # the old page still reads `evidence`; the new one hides it
    stage_history: list[StageEntry]


class AccountOut(Meta):
    item: AccountDetail
