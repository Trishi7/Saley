"""Response models of Health and global Search.

Why: both are operator views over the system itself, not over sales data.
"""

from __future__ import annotations

from datetime import date, datetime

from saley.web.schemas.common import Meta, NewsStep, Out

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
