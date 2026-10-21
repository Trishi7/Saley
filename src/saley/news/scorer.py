"""Scoring of stories (D72): the LLM reads headlines and writes a score; code decides the rest.

Why: the score is one row of numbers and a tag, in a fixed schema. Stories go in DATA blocks, so
a headline that says "ignore the rules" is only data. Rules of the road:
- one cache entry per story (model + rubric version + title + summary), so a repeat is free;
- batches of at most SCORER_BATCH stories, at most SCORER_EDITION_CALL_CAP calls per edition;
- an id the model made up is dropped, a story it skipped is carried to the next edition;
- `why` must pass the validator (no value that is not in the story), else it is left empty;
- no key, cap reached or bad output after one retry: the deterministic `rules` backend
  scores instead. A network error, a 5xx, a timeout or a rate limit is NOT a reason for
  `rules`: the stories wait for the next edition (A2). Only a story older than CARRY_LIMIT
  gets `rules`, so the nightly cleanup never deletes it unscored;
- the row records the backend. A story scored by `rules` only because the LLM was out is
  marked `rescore_due`; the LLM scores it once when it is back (proposed decision, refines
  D72). After that it is final: a story is never LLM-scored twice.
The LLM never sets a stage, picks a person or writes to the sheet.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from saley.core import llm
from saley.core.logging import get_logger
from saley.core.models import NewsEdition, NewsStory, Signal
from saley.core.settings import get_settings
from saley.intel.guard import clean_or_empty

log = get_logger("saley.news.scorer")

RUBRIC_VERSION = "r3-1"
JOB = "news_score"
MAX_PENDING = 500
CARRY_LIMIT = timedelta(hours=12)  # a story waits this long for the LLM, then gets `rules`
RESCORE_WINDOW = timedelta(days=7)
MAX_RESCORE = 100
PAUSED_TEXT = "LLM paused until tomorrow"
TAGS = ("Opportunity", "People", "Risk", "Market", "Tender", "Discover")
BACKEND_LLM = "llm"
BACKEND_RULES = "rules"
SYSTEM = (
    "You score news stories for the sales team of Membrane, which sells AI data, evaluation "
    "and voice services to AI labs and AI companies. For every story in the DATA blocks give: "
    "importance 1-5 (5 = the whole market will talk about it), impact 0-3 (3 = Membrane should "
    "act today, 0 = no link to Membrane), a tag (Opportunity, People, Risk, Market, Tender or "
    "Discover) and `why`, one plain sentence of at most 200 characters. In `why` use only words "
    "and facts that are in the story: never add a name, number, date, link or email that is "
    "not there. Use the id shown in each DATA block."
)
INSTRUCTION = "Score every story. Reply with one item per story, using its id."


class ScoreItem(BaseModel):
    """The score of one story."""

    id: int
    importance: int = Field(ge=1, le=5)
    impact: int = Field(ge=0, le=3)
    tag: Literal["Opportunity", "People", "Risk", "Market", "Tender", "Discover"]
    why: str = Field(max_length=200)


class ScoreBatch(BaseModel):
    """The only shape the model may answer in."""

    items: list[ScoreItem]


@dataclass(frozen=True)
class ScoreInput:
    """A story as the prompt shows it. `position` is the id the model must use (1..n)."""

    position: int
    title: str
    summary: str
    outlets: Sequence[str]
    kind: str = "news"

    def block(self) -> llm.DataBlock:
        text = f"id: {self.position}\ntitle: {self.title}\nsummary: {self.summary}\n"
        text += f"outlets: {', '.join(self.outlets)}\nkind: {self.kind}"
        return llm.DataBlock(f"story {self.position}", text)

    def facts(self) -> list[str]:
        return [self.title, self.summary, ", ".join(self.outlets)]


@dataclass
class ScoreReport:
    """What one scoring pass did. Printed in the edition note and the health view."""

    scored_llm: int = 0
    scored_rules: int = 0
    cached: int = 0
    carried: int = 0
    unknown_ids: int = 0
    why_refused: int = 0
    calls: int = 0
    paused: bool = False
    llm_down: bool = False
    rescored: int = 0
    notes: list[str] = field(default_factory=list)

    def note(self) -> str:
        parts = [
            f"llm {self.scored_llm}",
            f"rules {self.scored_rules}",
            f"cached {self.cached}",
            f"carried {self.carried}",
            f"calls {self.calls}",
        ]
        if self.rescored:
            parts.append(f"rescored {self.rescored}")
        if self.unknown_ids:
            parts.append(f"unknown ids {self.unknown_ids}")
        if self.why_refused:
            parts.append(f"why refused {self.why_refused}")
        if self.paused:
            parts.append("paused until tomorrow (LLM cap)")
        return " · ".join(parts)


# ------------------------------------------------------------------ the model call


def request_scores(
    inputs: Sequence[ScoreInput],
    *,
    model: str,
    job: str = JOB,
    client_factory: Callable[[], anthropic.Anthropic] = llm.make_anthropic_client,
) -> ScoreBatch:
    """One LLM call for a batch. Raises LLMError (cap, key, bad output, network)."""
    return llm.complete(
        model,
        SYSTEM,
        [i.block() for i in inputs],
        ScoreBatch,
        job=job,
        instruction=INSTRUCTION,
        client_factory=client_factory,
    )


def story_cache_key(model: str, title: str, summary: str) -> str:
    payload = json.dumps(
        {"m": model, "v": RUBRIC_VERSION, "t": title, "s": summary}, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ------------------------------------------------------------------ the rules backend

TENDER_WORDS = ("tender", "rfp", "grant", "procurement", "bid ")
PEOPLE_WORDS = ("appointed", "joins", "named", "promoted", "steps down", "head of", "hires")
RISK_WORDS = ("lawsuit", "ban", "fine", "breach", "regulat", "probe", "recall", "outage")
OPPORTUNITY_WORDS = ("raises", "funding", "series ", "launch", "partner", "contract", "acquir")
FIT_WORDS = (
    "annotation",
    "evaluation",
    "human feedback",
    "rlhf",
    "voice",
    "speech",
    "benchmark",
    "red team",
    "indic",
    "data vendor",
    "tender",
)


def rules_score(story: NewsStory) -> ScoreItem:
    """Deterministic scoring from outlets, flag and keywords. No `why`: rules write no text."""
    text = f"{story.title} {story.summary}".lower()
    importance = 1 if story.kind == "paper" else 2
    importance += (story.outlet_count >= 2) + (story.outlet_count >= 4) + bool(story.flag)
    fit = any(w in text for w in FIT_WORDS)
    impact = (1 if fit else 0) + (1 if fit and story.flag else 0)
    if any(w in text for w in TENDER_WORDS):
        tag, impact = "Tender", max(impact, 2) + (1 if story.flag else 0)
    elif any(w in text for w in PEOPLE_WORDS):
        tag = "People"
    elif any(w in text for w in RISK_WORDS):
        tag = "Risk"
    elif story.kind == "paper":
        tag = "Discover"
    elif any(w in text for w in OPPORTUNITY_WORDS) or story.flag:
        tag = "Opportunity"
    else:
        tag = "Market"
    return ScoreItem(
        id=story.id, importance=min(importance, 5), impact=min(impact, 3), tag=tag, why=""
    )


# ------------------------------------------------------------------ one pass


def _flatten(batches: Sequence[Sequence[NewsStory]]) -> list[NewsStory]:
    return [story for batch in batches for story in batch]


def _inputs(stories: Sequence[NewsStory]) -> list[ScoreInput]:
    return [ScoreInput(i, s.title, s.summary, s.outlets, s.kind) for i, s in enumerate(stories, 1)]


def _apply(
    story: NewsStory,
    item: ScoreItem,
    backend: str,
    now: datetime,
    report: ScoreReport,
    *,
    replace: bool = False,
    rescore_due: bool = False,
) -> None:
    """Write the score once. A story that already has one is left alone (never twice).

    `replace` is for the one LLM rescore of a story that `rules` scored while the LLM was out.
    """
    if story.scored_at is not None and not replace:
        return
    inp = ScoreInput(0, story.title, story.summary, story.outlets, story.kind)
    why = clean_or_empty(item.why, inp.facts()) if item.why else ""
    if item.why and not why:
        report.why_refused += 1
    story.importance, story.impact, story.tag = item.importance, item.impact, item.tag
    story.why, story.scorer_backend, story.rescore_due = why, backend, rescore_due
    if not replace:
        story.scored_at = now


def _score_with_rules(
    stories: Sequence[NewsStory], now: datetime, report: ScoreReport, *, rescore_due: bool = True
) -> None:
    """`rescore_due` is False only when rules were chosen (SCORER_BACKEND) or the LLM refused."""
    for story in stories:
        _apply(story, rules_score(story), BACKEND_RULES, now, report, rescore_due=rescore_due)
        report.scored_rules += 1


def _carry_or_rules(stories: Sequence[NewsStory], now: datetime, report: ScoreReport) -> None:
    """A transient LLM error: young stories wait for the next edition, old ones get `rules`."""
    cutoff = now - CARRY_LIMIT
    old = [s for s in stories if s.first_seen_at < cutoff]
    report.carried += len(stories) - len(old)
    _score_with_rules(old, now, report)


def _count_llm(report: ScoreReport, *, rescoring: bool) -> None:
    if rescoring:
        report.rescored += 1
    else:
        report.scored_llm += 1


def _from_cache(
    stories: Sequence[NewsStory],
    model: str,
    now: datetime,
    report: ScoreReport,
    *,
    rescoring: bool = False,
) -> list[NewsStory]:
    """Apply cached scores. Return the stories that still need the model."""
    misses = []
    hits = 0
    for story in stories:
        hit = llm.cache_get(story_cache_key(model, story.title, story.summary))
        try:
            item = ScoreItem.model_validate({**(hit or {}), "id": story.id}) if hit else None
        except ValidationError:
            item = None
        if item is None:
            misses.append(story)
            continue
        _apply(story, item, BACKEND_LLM, now, report, replace=rescoring)
        hits += 1
        _count_llm(report, rescoring=rescoring)
    report.cached += hits
    if hits:
        llm.log_cache_hits(JOB, model, f"{RUBRIC_VERSION}:{hits}", hits)
    return misses


def _apply_batch(
    batch: Sequence[NewsStory],
    answer: ScoreBatch,
    model: str,
    now: datetime,
    report: ScoreReport,
    *,
    rescoring: bool = False,
) -> None:
    by_position = dict(enumerate(batch, 1))
    answered: set[int] = set()
    for item in answer.items:
        story = by_position.get(item.id)
        if story is None:
            report.unknown_ids += 1  # an id the model made up is dropped
            continue
        if item.id in answered:
            continue
        answered.add(item.id)
        _apply(story, item, BACKEND_LLM, now, report, replace=rescoring)
        _count_llm(report, rescoring=rescoring)
        stored: dict[str, Any] = item.model_dump(exclude={"id"})
        llm.cache_put(story_cache_key(model, story.title, story.summary), model, stored)
    # Silent failure 1: items the model skipped wait for the next edition while young; an old
    # one gets `rules` now, so a story the model keeps skipping is not stuck unscored.
    left = [story for position, story in by_position.items() if position not in answered]
    if rescoring:
        report.carried += len(left)
    else:
        _carry_or_rules(left, now, report)


def _ask(
    batch: Sequence[NewsStory],
    model: str,
    report: ScoreReport,
    client_factory: Callable[[], anthropic.Anthropic],
) -> ScoreBatch:
    """One call, and one more if the answer was not in the schema (A2). Each call is counted."""
    report.calls += 1
    try:
        return request_scores(_inputs(batch), model=model, client_factory=client_factory)
    except llm.LLMBadOutput:
        report.calls += 1
        return request_scores(_inputs(batch), model=model, client_factory=client_factory)


def _no_llm(
    stories: Sequence[NewsStory], now: datetime, report: ScoreReport, *, rescoring: bool
) -> None:
    """Cap reached or no key: new stories get `rules` (and wait for a rescore); due ones wait."""
    if not rescoring:
        _score_with_rules(stories, now, report)


def _run_batches(
    stories: Sequence[NewsStory],
    model: str,
    now: datetime,
    report: ScoreReport,
    client_factory: Callable[[], anthropic.Anthropic],
    *,
    rescoring: bool,
) -> None:
    settings = get_settings()
    misses = _from_cache(stories, model, now, report, rescoring=rescoring)
    size = settings.scorer_batch
    batches = [misses[i : i + size] for i in range(0, len(misses), size)]
    for number, batch in enumerate(batches):
        rest = _flatten(batches[number:])
        if report.calls >= settings.scorer_edition_call_cap:
            if not rescoring:
                _carry_or_rules(rest, now, report)  # old leftovers get `rules`, young ones wait
            report.notes.append("edition call cap reached")
            break
        if llm.cap_reached():
            report.paused = True
            _no_llm(rest, now, report, rescoring=rescoring)
            break
        try:
            answer = _ask(batch, model, report, client_factory)
        except llm.LLMCapReached:
            report.paused = True
            _no_llm(rest, now, report, rescoring=rescoring)
            break
        except llm.LLMNotConfigured as exc:
            report.llm_down = True
            report.notes.append(type(exc).__name__)
            _no_llm(rest, now, report, rescoring=rescoring)
            break
        except llm.LLMBadOutput as exc:
            log.warning("score_batch_bad_output", error=type(exc).__name__)
            report.notes.append(type(exc).__name__)
            if rescoring:
                for story in batch:  # the model cannot score these: do not ask again
                    story.rescore_due = False
            else:
                _score_with_rules(batch, now, report, rescore_due=False)
            continue
        except llm.LLMError as exc:  # network, 5xx, timeout, rate limit: the next call fails too
            log.warning("score_batch_failed", error=type(exc).__name__)
            report.llm_down = True
            report.notes.append(type(exc).__name__)
            if not rescoring:
                _carry_or_rules(rest, now, report)
            break
        _apply_batch(batch, answer, model, now, report, rescoring=rescoring)


def _sync_signals(session: Session, stories: Sequence[NewsStory]) -> None:
    """A rescore changes the story score: the signals copied it, so they follow."""
    for story in stories:
        if story.scorer_backend == BACKEND_LLM and story.importance is not None:
            session.execute(
                update(Signal)
                .where(Signal.story_id == story.id)
                .values(tag=story.tag, importance=story.importance, impact=story.impact or 0)
            )


def _rescore_due(
    session: Session,
    model: str,
    now: datetime,
    report: ScoreReport,
    client_factory: Callable[[], anthropic.Anthropic],
) -> None:
    """The LLM scores once the stories `rules` scored while it was out. Then they are final."""
    session.execute(  # too old to matter: stop asking
        update(NewsStory)
        .where(NewsStory.rescore_due.is_(True), NewsStory.first_seen_at < now - RESCORE_WINDOW)
        .values(rescore_due=False)
    )
    due = list(
        session.scalars(
            select(NewsStory)
            .where(NewsStory.rescore_due.is_(True), NewsStory.scorer_backend == BACKEND_RULES)
            .order_by(NewsStory.id.desc())
            .limit(MAX_RESCORE)
        )
    )
    if not due:
        return
    _run_batches(due, model, now, report, client_factory, rescoring=True)
    _sync_signals(session, due)


def score_pending(
    session: Session,
    *,
    now: datetime,
    backend: str | None = None,
    model: str | None = None,
    client_factory: Callable[[], anthropic.Anthropic] = llm.make_anthropic_client,
) -> ScoreReport:
    """Score every unscored story once. Safe to run again: a scored story is skipped."""
    settings = get_settings()
    backend = backend or settings.scorer_backend
    model = model or settings.llm_small
    report = ScoreReport()
    stories = list(
        session.scalars(
            select(NewsStory)
            .where(NewsStory.scored_at.is_(None))
            .order_by(NewsStory.id)
            .limit(MAX_PENDING)
        )
    )
    if backend == BACKEND_RULES:  # chosen by the owner, not a failure: no rescore
        _score_with_rules(stories, now, report, rescore_due=False)
        return report
    if stories:
        _run_batches(stories, model, now, report, client_factory, rescoring=False)
    if not (report.paused or report.llm_down):
        _rescore_due(session, model, now, report, client_factory)
    session.flush()
    return report


# ------------------------------------------------------------------ health


def scorer_summary(session: Session, now: datetime) -> dict[str, Any]:
    """What the health page shows about scoring (A2c). Plain numbers and words, no LLM.

    - `rules_share_24h`: share (0..1) of stories scored in the last 24 h by `rules`, or None;
    - `last_backends`: every backend of the last edition that scored, as a list (no None for
      a mix: ["llm", "rules"]);
    - `paused` / `paused_text`: the daily LLM cap is spent, so scoring waits for tomorrow;
    - `rescore_due`: stories waiting for their one LLM score.
    """
    since = now - timedelta(hours=24)
    scored = (
        session.scalar(
            select(func.count()).select_from(NewsStory).where(NewsStory.scored_at >= since)
        )
        or 0
    )
    by_rules = (
        session.scalar(
            select(func.count())
            .select_from(NewsStory)
            .where(NewsStory.scored_at >= since, NewsStory.scorer_backend == BACKEND_RULES)
        )
        or 0
    )
    last = session.scalar(
        select(NewsEdition.scorer_backend)
        .where(NewsEdition.scorer_backend.is_not(None))
        .order_by(NewsEdition.id.desc())
        .limit(1)
    )
    waiting = (
        session.scalar(
            select(func.count()).select_from(NewsStory).where(NewsStory.rescore_due.is_(True))
        )
        or 0
    )
    return scorer_block(scored, by_rules, last, waiting, llm.cap_reached())


def scorer_block(
    scored: int, by_rules: int, last_backend: str | None, waiting: int, paused: bool
) -> dict[str, Any]:
    """The scorer summary from plain numbers: shared by the worker session and the web views."""
    return {
        "scored_24h": scored,
        "rules_24h": by_rules,
        "rules_share_24h": round(by_rules / scored, 3) if scored else None,
        "last_backends": [b for b in (last_backend or "").split(",") if b],
        "paused": paused,
        "paused_text": PAUSED_TEXT if paused else None,
        "rescore_due": waiting,
    }
