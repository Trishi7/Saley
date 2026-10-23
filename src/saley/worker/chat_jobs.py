"""Nightly chat summary and retention (D262, docs/09 §8, docs/17 §4).

Why in the worker: the summary needs the LLM and the bot holds no LLM key (docs/17 §3).

One LLM_MID call per channel and day (batched: all lines of that day in one call). Every line
enters the model inside a DATA block (CLAUDE.md); the answer is parsed to a fixed schema. Plain
code then checks it: a fact must name a message of that day, a company or person on our sheet
that the message itself names, and pass `intel.guard` against that message. Anything else is
dropped. The LLM never picks a person: it only says which message names which company.

Retention: lines older than CHAT_RETENTION_DAYS are deleted every night, with their facts
(cascade) and their summaries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.core import clock, llm
from saley.core.logging import get_logger
from saley.core.settings import get_settings, is_missing
from saley.core.text import norm_name
from saley.intel.guard import clean_or_empty

log = get_logger("saley.worker.chat_jobs")

JOB = "chat_summary"
DAYS_PER_RUN = 7  # a backlog is summarised over a few nights, not in one burst
LINES_PER_CALL = 300
LINE_MAX = 600
SUMMARY_MAX = 1000
FACT_MAX = 300
SYSTEM = (
    "You summarise one day of a sales team's chat channel. Write short, plain sentences. "
    "Then list facts: each fact says what one message tells about one company or one person. "
    "Name the company or person exactly as the message writes it. Give the message id."
)
INSTRUCTION = "Summarise this day and list the facts, from the DATA blocks only."


class ChatFactOut(BaseModel):
    message_id: int
    company: str | None = Field(default=None, max_length=120)
    person: str | None = Field(default=None, max_length=120)
    text: str = Field(max_length=FACT_MAX)


class ChatDayOut(BaseModel):
    summary: str = Field(max_length=SUMMARY_MAX)
    facts: list[ChatFactOut] = Field(default_factory=list, max_length=40)


@dataclass
class ChatStats:
    days: int = 0
    facts: int = 0
    dropped: int = 0
    deleted: int = 0
    stopped: str | None = None
    notes: list[str] = field(default_factory=list)

    def note(self) -> str:
        line = (
            f"{self.days} channel-days · {self.facts} facts · {self.dropped} dropped · "
            f"{self.deleted} old lines deleted"
        )
        return f"{line} · stopped: {self.stopped}" if self.stopped else line


def retention(session: Session, now: datetime) -> int:
    """Delete lines older than CHAT_RETENTION_DAYS (facts go with them), old summaries, and
    answer intents older than a day."""
    cut = now - timedelta(days=get_settings().chat_retention_days)
    gone = session.execute(text("delete from chat_message where at < :c"), {"c": cut}).rowcount
    session.execute(text("delete from chat_summary where day < :d"), {"d": cut.date()})
    # T8 review: one intent row per question; nobody reads one after its answer.
    session.execute(text("delete from answer_intent where created_at < now() - interval '1 day'"))
    return int(gone or 0)


def pending_days(session: Session, today: date) -> list[tuple[int, date]]:
    """Channel-days before today with lines and no summary yet, oldest first."""
    rows = session.execute(
        text(
            "select distinct m.channel_id, (m.at at time zone 'Asia/Kolkata')::date as day "
            "from chat_message m where (m.at at time zone 'Asia/Kolkata')::date < :today "
            "and not exists (select 1 from chat_summary s where s.channel_id = m.channel_id "
            "and s.day = (m.at at time zone 'Asia/Kolkata')::date) order by 2, 1 limit :n"
        ),
        {"today": today, "n": DAYS_PER_RUN},
    ).all()
    return [(int(r[0]), r[1]) for r in rows]


def _lines(session: Session, channel: int, day: date) -> list[Any]:
    start = datetime.combine(day, time.min, tzinfo=clock.tz())
    return session.execute(
        text(
            "select message_id, author_short, text, at from chat_message "
            "where channel_id = :c and at >= :a and at < :b order by at, message_id limit :n"
        ),
        {"c": channel, "a": start, "b": start + timedelta(days=1), "n": LINES_PER_CALL},
    ).all()


def _names(session: Session) -> tuple[dict[str, int], dict[str, int]]:
    """Name keys of the sheet: company -> account id, person -> contact id."""
    accounts = {
        r.name_key: r.id
        for r in session.execute(text("select id, name_key from account where removed_at is null"))
    }
    people = {
        r.name_key: r.id
        for r in session.execute(text("select id, name_key from contact where removed_at is null"))
    }
    return accounts, people


def checked_facts(
    answer: ChatDayOut, lines: list[Any], accounts: dict[str, int], people: dict[str, int]
) -> tuple[list[dict[str, Any]], int]:
    """The facts that hold, and how many were dropped. Plain code decides (CLAUDE.md)."""
    by_id = {int(r.message_id): r for r in lines}
    kept, dropped = [], 0
    for fact in answer.facts:
        line = by_id.get(fact.message_id)
        said = norm_name(line.text) if line else ""
        company = norm_name(fact.company) if fact.company else ""
        person = norm_name(fact.person) if fact.person else ""
        company_id = accounts.get(company) if company and company in said else None
        contact_id = people.get(person) if person and person in said else None
        words = clean_or_empty(
            fact.text.strip(),
            [line.text, line.author_short, fact.company, fact.person] if line else [],
        )
        if line is None or not (company_id or contact_id) or not words:
            dropped += 1
            continue
        kept.append(
            {
                "message_id": int(line.message_id),
                "company_id": company_id,
                "contact_id": contact_id,
                "text": words,
                "author_short": line.author_short,
                "at": line.at,
            }
        )
    return kept, dropped


def summarise_day(
    session: Session,
    channel: int,
    day: date,
    accounts: dict[str, int],
    people: dict[str, int],
    client_factory: Any = llm.make_anthropic_client,
) -> tuple[int, int]:
    """One channel-day: one call, the checked facts, the summary row. Returns (facts, dropped)."""
    settings = get_settings()
    lines = _lines(session, channel, day)
    blocks = [
        llm.DataBlock(
            source=f"message {r.message_id} · {r.author_short or 'someone'} · {clock.hhmm(r.at)}",
            text=r.text[:LINE_MAX],
        )
        for r in lines
    ]
    answer = llm.complete(
        settings.llm_mid,
        SYSTEM,
        blocks,
        ChatDayOut,
        job=JOB,
        instruction=INSTRUCTION,
        client_factory=client_factory,
    )
    facts, dropped = checked_facts(answer, lines, accounts, people)
    summary = clean_or_empty(
        answer.summary.strip(), [r.text for r in lines] + [r.author_short for r in lines]
    )
    for f in facts:
        session.execute(
            text(
                "insert into chat_fact (channel_id, message_id, company_id, contact_id, text, "
                "author_short, at) values (:ch, :m, :co, :ct, :t, :a, :at)"
            ),
            {
                "ch": channel,
                "m": f["message_id"],
                "co": f["company_id"],
                "ct": f["contact_id"],
                "t": f["text"],
                "a": f["author_short"],
                "at": f["at"],
            },
        )
    session.execute(
        text(
            "insert into chat_summary (channel_id, day, summary, facts, model) "
            "values (:c, :d, :s, :n, :m) on conflict (channel_id, day) do nothing"
        ),
        {"c": channel, "d": day, "s": summary, "n": len(facts), "m": settings.llm_mid},
    )
    return len(facts), dropped


def run_chat_summary(
    session: Session, now: datetime | None = None, client_factory: Any = llm.make_anthropic_client
) -> ChatStats:
    """The nightly job: retention first, then the days that wait for a summary."""
    moment = now or clock.now_ist()
    stats = ChatStats(deleted=retention(session, moment))
    accounts, people = _names(session)
    for channel, day in pending_days(session, moment.date()):
        try:
            with session.begin_nested():
                facts, dropped = summarise_day(
                    session, channel, day, accounts, people, client_factory
                )
        except llm.LLMBadOutput:
            stats.dropped += 1
            continue
        except llm.LLMError as exc:  # no key, the cap, the API: the days wait for the next night
            stats.stopped = type(exc).__name__
            break
        stats.days += 1
        stats.facts += facts
        stats.dropped += dropped
    log.info("chat_summary", **{k: v for k, v in vars(stats).items() if k != "notes"})
    return stats


# ------------------------------------------------------------------ the answer intent step

INTENT_JOB = "answer_intent"
INTENT_BATCH = 10
INTENT_SYSTEM = (
    "You read one question a sales teammate asked. Name the lookup it needs and the words of "
    "the question it needs them with. Lookups: who (people at a company who work on a topic), "
    "news (news since a day), working (what one person works on), p1 (P1 people with no "
    "email), due (what is due this week), chat (what the team said in chat about a topic), "
    "unknown. Copy words from the question only; never add a name, a day or a company."
)
INTENT_INSTRUCTION = "Name the lookup and its words for the question in the DATA block."


class IntentOut(BaseModel):
    kind: Literal["who", "news", "working", "p1", "due", "chat", "unknown"]
    company: str | None = Field(default=None, max_length=80)
    person: str | None = Field(default=None, max_length=80)
    window: str | None = Field(default=None, max_length=40)
    topic: str | None = Field(default=None, max_length=80)


def _from_question(intent: IntentOut, question: str) -> dict[str, Any]:
    """Words the question does not hold are dropped: the LLM never adds a value (CLAUDE.md)."""
    said = question.casefold()
    out: dict[str, Any] = {"kind": intent.kind}
    for key in ("company", "person", "window", "topic"):
        value = (getattr(intent, key) or "").strip()
        out[key] = value if value and value.casefold() in said else None
    return out


def run_answer_intents(
    session: Session,
    client_factory: Any = llm.make_anthropic_client,
    llm_ready: bool | None = None,
) -> int:
    """Answer the questions that wait (bot/intent.py). Returns how many were answered.

    No LLM key: each waits no longer, it is marked `none` at once and the grammar answers. A
    question older than twice the bot's wait is stale: nobody waits for it any more."""
    settings = get_settings()
    stale = timedelta(seconds=2 * settings.bot_intent_timeout_s)
    session.execute(
        text(
            "update answer_intent set state = 'failed', done_at = now() "
            "where state = 'waiting' and created_at < now() - :s"
        ),
        {"s": stale},
    )
    session.commit()  # a stale question is closed even when nothing else waits
    rows = session.execute(
        text(
            "select id, question from answer_intent where state = 'waiting' "
            "order by id limit :n for update skip locked"
        ),
        {"n": INTENT_BATCH},
    ).all()
    ready = not is_missing(settings.anthropic_api_key) if llm_ready is None else llm_ready
    for r in rows:
        state, found = "none", None
        if ready:
            try:
                answer = llm.complete(
                    settings.llm_small,
                    INTENT_SYSTEM,
                    [llm.DataBlock("question", r.question)],
                    IntentOut,
                    job=INTENT_JOB,
                    instruction=INTENT_INSTRUCTION,
                    client_factory=client_factory,
                )
                state, found = "done", _from_question(answer, r.question)
            except llm.LLMError as exc:
                state = "failed"
                log.warning("answer_intent_failed", error=type(exc).__name__)
        session.execute(
            text(
                "update answer_intent set state = :s, intent = cast(:j as jsonb), "
                "done_at = now() where id = :i"
            ),
            {"s": state, "j": json.dumps(found) if found else None, "i": r.id},
        )
        session.commit()  # the bot polls this row: it must see the answer now
    return len(rows)
