"""R7-I04: chat memory (D262, docs/09 §8, docs/17 §4-§6).

Capture keeps chat lines of the chat channels only; the backfill reads 14 days once and a
second start skips it; the nightly summary writes chat_summary and chat_fact (facts checked
against their message); retention deletes day 91; `chat forget` removes a line and its facts.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import memory
from saley.core import clock
from saley.core.settings import get_settings
from saley.worker import chat_jobs
from tests.conftest import TEAM_IDS, FixtureLLM

TEST_CH = 100000000000000001
OTHER_CH = 900000000000000009


def _line(
    mid: int,
    body: str,
    *,
    at: datetime | None = None,
    channel: int = TEST_CH,
    author: str = "vaishnavi",
    bot: bool = False,
) -> memory.Line:
    return memory.Line(
        channel_id=channel,
        message_id=mid,
        thread_id=None,
        author_id=int(TEAM_IDS[author]),
        author_is_bot=bot,
        text=body,
        reply_to=None,
        at=at or clock.now_ist(),
    )


def _store(db: Session, *lines: memory.Line) -> list[bool]:
    out = [memory.capture(db, get_settings(), line) for line in lines]
    db.commit()
    return out


def test_capture_keeps_chat_of_the_chat_channels_only(crew: Session) -> None:
    stored = _store(
        crew,
        _line(1, "ARTPARK wants the evals plan"),
        _line(2, "card text", bot=True),
        _line(3, "elsewhere", channel=OTHER_CH),
        _line(4, "   "),
        _line(1, "the same message again"),
    )
    assert stored == [True, False, False, False, False]
    row = crew.execute(text("select author_short, text from chat_message")).one()
    assert tuple(row) == ("vaishnavi", "ARTPARK wants the evals plan")


class FakeChannel:
    def __init__(self, cid: int, messages: list[Any], forbidden: bool = False) -> None:
        self.id, self.messages, self.forbidden = cid, messages, forbidden
        self.asked: dict[str, Any] = {}

    def history(
        self, *, after: datetime, limit: int | None, oldest_first: bool
    ) -> AsyncIterator[Any]:
        self.asked = {"after": after, "limit": limit, "oldest_first": oldest_first}

        async def gen() -> AsyncIterator[Any]:
            if self.forbidden:
                raise PermissionError("no access")
            for m in self.messages:
                yield m

        return gen()


def _msg(mid: int, body: str, author: str = "sid") -> Any:
    return SimpleNamespace(
        id=mid,
        content=body,
        created_at=clock.now_ist() - timedelta(days=2),
        reference=None,
        channel=SimpleNamespace(id=TEST_CH, parent_id=None),
        author=SimpleNamespace(id=int(TEAM_IDS[author]), bot=False),
    )


def test_backfill_reads_14_days_once(crew: Session) -> None:
    now = clock.now_ist()
    good = FakeChannel(TEST_CH, [_msg(10, "Sujith asked for the deck"), _msg(11, "on it")])
    locked = FakeChannel(OTHER_CH, [], forbidden=True)

    def store(line: memory.Line) -> bool:
        return memory.capture(crew, get_settings(), line)

    stored = asyncio.run(
        memory.backfill([good, locked], store, get_settings(), now, (PermissionError,))
    )
    assert stored == 2
    assert good.asked == {"after": now - timedelta(days=14), "limit": None, "oldest_first": True}
    assert not memory.backfill_done(crew)
    memory.mark_backfill_done(crew, stored)
    crew.commit()
    assert memory.backfill_done(crew)  # a second start skips the backfill


def _day(days_back: int, hour: int = 12) -> datetime:
    day = clock.today_ist() - timedelta(days=days_back)
    return datetime.combine(day, datetime.min.time(), tzinfo=clock.tz()) + timedelta(hours=hour)


def test_the_summary_writes_a_summary_and_checked_facts(crew: Session) -> None:
    company = crew.execute(text("select name from account order by id limit 1")).scalar_one()
    when = _day(1)
    _store(
        crew,
        _line(20, f"{company} wants the pilot proposal by Friday", at=when),
        _line(21, "I will send it", at=when + timedelta(minutes=5), author="sid"),
    )
    answer = {
        "summary": f"{company} wants the pilot proposal.",
        "facts": [
            {"message_id": 20, "company": company, "text": f"{company} wants the pilot proposal"},
            {"message_id": 21, "company": company, "text": "invented"},  # 21 does not name it
            {"message_id": 99, "company": company, "text": "no such message"},
            {"message_id": 20, "company": "Nowhere Ltd", "text": "not on the sheet"},
        ],
    }
    fake = FixtureLLM([answer])
    stats = chat_jobs.run_chat_summary(crew, client_factory=fake.client_factory)
    crew.commit()
    assert (stats.days, stats.facts, stats.dropped) == (1, 1, 3), stats.note()
    summary = crew.execute(text("select summary, facts, model from chat_summary")).one()
    assert summary[0] == f"{company} wants the pilot proposal." and summary[1] == 1
    fact = crew.execute(text("select message_id, author_short, text from chat_fact")).one()
    assert tuple(fact) == (20, "vaishnavi", f"{company} wants the pilot proposal")
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "<DATA" in prompt and f"{company} wants the pilot proposal by Friday" in prompt
    assert len(fake.calls) == 1  # one call per channel-day
    again = chat_jobs.run_chat_summary(crew, client_factory=fake.client_factory)
    assert again.days == 0 and len(fake.calls) == 1


def test_no_llm_leaves_the_days_for_the_next_night(crew: Session) -> None:
    _store(crew, _line(30, "hello", at=_day(1)))

    def broken() -> Any:
        from saley.core import llm

        raise llm.LLMNotConfigured("no key")

    stats = chat_jobs.run_chat_summary(crew, client_factory=broken)
    assert stats.stopped == "LLMNotConfigured" and stats.days == 0
    assert crew.execute(text("select count(*) from chat_summary")).scalar() == 0


def test_retention_deletes_day_91_and_keeps_day_89(crew: Session) -> None:
    _store(crew, _line(40, "old", at=_day(91)), _line(41, "kept", at=_day(89)))
    chat_jobs.run_chat_summary(
        crew, client_factory=FixtureLLM([{"summary": "", "facts": []}]).client_factory
    )
    crew.commit()
    left = crew.execute(text("select message_id from chat_message order by 1")).scalars().all()
    assert left == [41]


def test_forget_removes_a_line_and_its_facts(crew: Session) -> None:
    _store(crew, _line(50, "x", at=_day(1)))
    crew.execute(
        text(
            "insert into chat_fact (channel_id, message_id, text, at) values (:c, 50, 'f', now())"
        ),
        {"c": TEST_CH},
    )
    crew.commit()
    assert memory.forget(crew, 50) is True
    crew.commit()
    assert crew.execute(text("select count(*) from chat_fact")).scalar() == 0
    assert memory.forget(crew, 50) is False


def test_a_thread_message_belongs_to_its_channel() -> None:
    msg = SimpleNamespace(
        id=60,
        content="in a thread",
        created_at=clock.now_ist(),
        reference=SimpleNamespace(message_id=59),
        channel=SimpleNamespace(id=777000000000000007, parent_id=TEST_CH),
        author=SimpleNamespace(id=1, bot=False),
    )
    line = memory.of_message(msg)
    assert (line.channel_id, line.thread_id, line.reply_to) == (TEST_CH, 777000000000000007, 59)


def test_saley_chat_forget_on_the_command_line(crew: Session) -> None:
    from typer.testing import CliRunner

    from saley.cli import app

    _store(crew, _line(70, "remove me", at=_day(1)))
    runner = CliRunner()
    gone = runner.invoke(app, ["chat", "forget", "70"])
    assert gone.exit_code == 0 and "forgotten: 1 line" in gone.output
    missing = runner.invoke(app, ["chat", "forget", "70"])
    assert missing.exit_code == 1 and "no such line" in missing.output
