"""The intent step in front of the R4 grammar (docs/09 §8, docs/17 §5 "Answer").

Why: people ask in their own words ("who do we know at ARTPARK and where are we?"). The LLM
reads the question and names the lookup and its words; plain code does everything else (the
SQL, the lines, the citations, `intel.guard`). The bot holds no LLM key (docs/17 §3), so it
writes the question to `answer_intent` and the worker (`worker/chat_jobs.py`) writes the
intent back in a fixed schema. The bot waits at most BOT_INTENT_TIMEOUT_S; then, or when the
intent is unknown or does not fit, the fixed grammar answers. A slow or missing worker makes an
answer slower, never different in kind.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy import text

from saley.bot.answer import MAX_QUESTION, IntentOf, Parsed, _tidy, chat_topic, parse_day
from saley.core import clock
from saley.core.db import session_scope
from saley.core.logging import get_logger
from saley.core.settings import Settings

log = get_logger("saley.bot.intent")

POLL_SECONDS = 0.25
WORDS_MAX = 80
FINAL = ("done", "none", "failed")


def request(user: str, question: str) -> int:
    with session_scope() as session:
        return int(
            session.execute(
                text(
                    "insert into answer_intent (user_short, question) values (:u, :q) returning id"
                ),
                {"u": user, "q": question[:MAX_QUESTION]},
            ).scalar_one()
        )


def read(rid: int) -> tuple[str, dict[str, Any] | None]:
    with session_scope() as session:
        row = session.execute(
            text("select state, intent from answer_intent where id = :i"), {"i": rid}
        ).one()
    return str(row.state), row.intent


def wait_for(
    rid: int,
    timeout: float,
    reader: Callable[[int], tuple[str, dict[str, Any] | None]] = read,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any] | None:
    """The intent the worker wrote, or None (no LLM, a failure, or `timeout` seconds passed)."""
    end = monotonic() + timeout
    while True:
        state, found = reader(rid)
        if state in FINAL:
            return found if state == "done" else None
        if monotonic() >= end:
            log.info("answer_intent_timeout", seconds=timeout)
            return None
        sleep(POLL_SECONDS)


def _word(intent: dict[str, Any], key: str) -> str:
    value = _tidy(str(intent.get(key) or ""))
    return value if len(value) <= WORDS_MAX else ""


def to_parsed(intent: dict[str, Any] | None, today: date) -> Parsed | None:
    """The intent as a grammar lookup, or None when it names no lookup we can run."""
    if not intent:
        return None
    kind = str(intent.get("kind") or "")
    company, person, topic = (
        _word(intent, "company"),
        _word(intent, "person"),
        _word(intent, "topic"),
    )
    if kind == "who" and company and topic:
        return Parsed("who", (("company", company), ("topic", topic)))
    if kind == "working" and person:
        return Parsed("working", (("person", person),))
    if kind == "news":
        day = parse_day(_word(intent, "window"), today)
        return Parsed("news", (("day", day.isoformat()),)) if day else None
    if kind in ("p1", "due"):
        return Parsed(kind)
    if kind == "chat" and topic:
        return Parsed("chat", (("topic", chat_topic(topic)), ("who", person)))
    return None


def intent_of(settings: Settings) -> IntentOf:
    """The step the gateway passes to `answer.discord_reply`."""

    def step(user: str, question: str) -> Parsed | None:
        try:
            rid = request(user, question)
            return to_parsed(wait_for(rid, settings.bot_intent_timeout_s), clock.today_ist())
        except Exception:  # the grammar still answers
            log.exception("answer_intent_failed")
            return None

    return step
