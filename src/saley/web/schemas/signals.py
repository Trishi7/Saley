"""Response models of Signals, Events, Suggestions, the seen marks and Ask.

Why: these are the R3 lists that share the window and the New / Seen earlier tabs.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from saley.web.schemas.common import Link, Meta, NewsStep, Out, StrictIn, TabbedMeta, Window

# ------------------------------------------------------------------ signals and suggestions (R3)


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
    hits: int | None  # None: no search yet (unknown, never 0)


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


class SaleySuggests(Out):
    items: list[SuggestionItem]
    empty: str
    window: Window
    left_out_seen: int


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
