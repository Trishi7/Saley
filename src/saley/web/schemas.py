"""Response models: each endpoint returns only the fields its view shows (R1.1 ticket C).

Why: a query may select more than a page needs. These models drop every extra field on the
way out (`extra="ignore"`), so raw cells, env values or ids never leak by accident.
Every response carries the snapshot it was read from (`sync_id`, D40).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
    week_start: date
    built_at: datetime
    body: PackBody


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
    """One step of the news funnel. `href` opens the list behind the number (D73)."""

    key: str
    step: str
    n: int | None
    href: str
    note: str | None = None


class NewsSourceRow(Out):
    name: str
    kind: str
    lane: str | None
    active: bool
    last_ok_at: datetime | None
    last_status: str | None
    problem: str | None
    fail_streak: int
    runs_24h: int
    seen_24h: int
    new_24h: int
    errors_24h: int


class ScorerBlock(Out):
    """Scoring in the last 24 h: how much the `rules` backend did, and a paused LLM (A1)."""

    scored_24h: int
    rules_24h: int
    rules_percent: int | None
    last_backends: list[str]
    paused: bool
    paused_text: str | None
    rescore_due: int
    rescore_line: str | None
    line: str


class StaleSourceRow(Out):
    name: str
    family: str
    flags: list[str]
    quiet_days: int
    lines: list[str]


class FamilyRow(Out):
    family: str
    name: str
    stale: bool
    last_good_at: datetime | None
    last_story_at: datetime | None
    last_edition_status: str | None
    line: str | None


class WatchLine(Out):
    kind: str
    text: str


class NewsBlock(Out):
    day: date
    label: str
    steps: list[NewsStep]
    failed_sources: list[NewsSourceRow]
    sources: list[NewsSourceRow]
    scorer: ScorerBlock
    stale_sources: list[StaleSourceRow]
    families: list[FamilyRow]
    watch: list[WatchLine]


class SearchBlock(Out):
    """Profile-link search: is SearXNG set, and how many requests ran today (D77)."""

    searxng: str
    message: str
    unavailable_until: str | None
    engine: str
    requests_today: int
    daily_cap: int
    last_error: str | None
    last_failure: str | None
    last_http_status: int | None


class ApolloBlock(Out):
    """Apollo needs the flag, a cap above 0 and the pipeline. In R3 it is off (D78)."""

    state: str
    message: str


class TabStatus(Out):
    tab: str
    title: str | None
    status: str
    missing_headers: list[str]
    new_headers: list[str]
    stale_since: datetime | None
    reason: str | None


class WritesState(Out):
    """Sheet writes: on or off, and how many rows wait or failed (R2)."""

    enabled: bool
    message: str | None
    open: int
    failed: int


class MailState(Out):
    """The cc mailbox source: not configured, waiting, ok or failing (Q-R2-3)."""

    state: str
    message: str
    last_ok: datetime | None


class NotesState(Out):
    """The Drive notes source: not configured, waiting, ok, folder not shared, API off, failing."""

    state: str
    message: str
    last_ok: datetime | None


class HealthOut(Meta):
    writes: WritesState
    mail: MailState
    notes: NotesState
    jobs: list[Job]
    parity: list[ParityRow]
    quality: list[Quality]
    missing_industry: int
    egress: list[EgressRow]
    llm: Llm
    llm_cap: int
    news: NewsBlock
    news_note: str
    search: SearchBlock
    apollo: ApolloBlock
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


# ------------------------------------------------------------------ act (R2, D24, D45)


class StrictIn(BaseModel):
    """Request bodies refuse fields they do not declare."""

    model_config = ConfigDict(extra="forbid")


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


# ------------------------------------------------------------------ signals and suggestions (R3)


class Window(Out):
    """The time window a list covers, in words and in dates (D22: every answer states it)."""

    from_: str | None = Field(alias="from")
    to: str | None
    label: str

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class ListMeta(Meta):
    """What every R3 list adds: its window, how many seen items it left out (D75)."""

    window: Window
    left_out_seen: int
    left_out_note: str | None


class TabbedMeta(ListMeta):
    """What Signals, Suggestions and Events add for the New / Seen earlier tabs (D157)."""

    view: Literal["new", "seen"] = "new"
    new_total: int = 0
    seen_total: int = 0
    new_empty: str | None = None


class SignalItem(Out):
    """A row of Signals · All: one story."""

    id: int
    title: str
    href: str | None
    outlets: list[str]
    outlet_count: int
    source: str | None
    categories: list[str]
    kind: str
    tag: str | None
    importance: int | None
    impact: int | None
    why: str
    published_at: datetime | None
    published_label: str
    linked_count: int
    ours: bool
    seen: bool


class LinkedItem(Out):
    """A row of Signals · Linked: one story linked to one company or person."""

    id: int
    story_id: int
    title: str
    href: str | None
    outlets: list[str]
    source: str | None
    entity_type: str
    entity_id: int
    entity: str | None
    account: str | None
    match_kind: str
    confidence: int
    verify: bool
    verify_note: str | None
    action_hint: str
    state: str
    tag: str | None
    importance: int
    impact: int
    why: str
    published_at: datetime | None
    published_label: str
    seen: bool


class SignalsOut(TabbedMeta):
    tab: str
    items: list[SignalItem | LinkedItem]
    total: int


class EventItem(Out):
    id: int
    name: str
    city: str
    venue: str | None = None
    start: date | None
    end: date | None
    date_label: str
    days_left: int | None
    days_text: str
    registered: bool
    people_in_city: int
    connected: int
    p1: int
    speaker_matches: str
    speaker_soon: str = ""
    href: str | None
    seen: bool


class EventsOut(TabbedMeta):
    when: str
    items: list[EventItem]
    total: int


class ScorePart(Out):
    code: str
    label: str
    points: int


class HitItem(Out):
    id: int
    title: str
    href: str
    engine: str
    form: dict[str, Any]


class SearchState(Out):
    state: str | None
    message: str | None
    hits: int


class SuggestionItem(Out):
    kind: str
    id: int
    contact_id: int | None
    account_id: int | None
    subject: str
    company: str | None
    title: str | None
    reason: str
    score: int
    parts: list[ScorePart]
    via: str | None
    evidence: dict[str, Any]
    link: Link | None
    can_find_people: bool
    search: SearchState | None
    hits: list[HitItem]
    seen: bool
    line: str | None = None  # "Add {name} ({company}) to pipeline — {reason}" (R5 T5)


class SuggestOut(TabbedMeta):
    kind: str
    items: list[SuggestionItem]
    total: int
    empty: str
    capped: bool
    capped_note: str | None


class NeedsLookItem(Out):
    """One line of Today · needs a look: a strong linked story or an event close by."""

    kind: Literal["signal", "event"]
    id: int
    title: str
    detail: str
    verify_note: str | None
    href: str | None
    at_label: str
    link: Link


class LookCard(Out):
    """A card of Today · needs a look (D158). It opens a list with its own item."""

    kind: Literal["news", "event", "blocker", "waiting"]
    title: str
    why: str
    link: Link


class NeedsLook(Out):
    cards: list[LookCard] = []
    soon: str | None = None  # "mail_replies" while mail is off: the UI shows "coming soon"
    items: list[NeedsLookItem]
    total: int
    empty: str
    replies_note: str | None
    window: Window
    left_out_seen: int
    left_out_note: str | None


class SaleySuggests(Out):
    items: list[SuggestionItem]
    empty: str
    window: Window
    left_out_seen: int
    left_out_note: str | None


class FunnelOut(Meta):
    day: date
    label: str
    steps: list[NewsStep]


class SeenIn(StrictIn):
    item_type: Literal[
        "story", "signal", "event", "suggest_warm", "suggest_best", "suggest_newco", "suggest_res"
    ]
    ids: list[Annotated[int, Field(ge=1, le=2**62)]] = Field(min_length=1, max_length=200)
    query_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")


class SeenOut(Out):
    ok: bool
    marked: int


class AskIn(StrictIn):
    """The Ask card sends one question. Questions only: no field can name an action."""

    text: str = Field(min_length=1, max_length=300, pattern=r"\S")


class AskOut(Out):
    """The answer with its counts. Nothing else of the engine leaves the server."""

    text: str
    known: bool
    shown: int
    more: int
    left_out: int


class FindPeopleOut(Out):
    ok: bool
    request_id: int
    already: bool
    label: str


def strip(model: type[Out], data: dict[str, Any]) -> dict[str, Any]:
    """Validate and drop extra keys (used in tests to check field sets)."""
    return model.model_validate(data).model_dump()
