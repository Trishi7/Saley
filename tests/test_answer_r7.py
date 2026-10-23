"""R7-A01: the answer upgrade on 30 fixtures (tests/fixtures/answers_r7.yaml).

The intent step names a lookup (the worker drops any word that is not in the question), the
grammar answers when the step gives nothing, chat lines are cited as [Name, Tue 12:40] with
personal data masked, a long answer splits into a thread, and the guard refuses an invented
value. Every answer goes through the real engine on the test database.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import answer, intent, memory
from saley.core import clock
from saley.core.settings import get_settings
from saley.worker import chat_jobs
from tests.conftest import TEAM_IDS, FixtureLLM

CASES = yaml.safe_load(
    (Path(__file__).parent / "fixtures" / "answers_r7.yaml").read_text(encoding="utf-8")
)["cases"]
TEST_CH = 100000000000000001
ASKER = "vaishnavi"


def _last_tuesday() -> datetime:
    today = clock.today_ist()
    day = today - timedelta(days=(today.weekday() - 1) % 7 or 7)
    return datetime.combine(day, time(12, 40), tzinfo=clock.tz())


@pytest.fixture
def chat(crew: Session) -> Session:
    tue = _last_tuesday()
    lines = [
        (1, "vaishnavi", "ARTPARK wants the NDA signed first", tue),
        (2, "kushal", "NDA draft is with legal", tue + timedelta(minutes=15)),
        (
            3,
            "sid",
            "The Google pilot starts in December, "
            "mail sid.personal@gmail.com or call +91 98765 43210",
            tue + timedelta(minutes=25),
        ),
    ]
    for mid, who, body, at in lines:
        memory.capture(
            crew,
            get_settings(),
            memory.Line(TEST_CH, 7000 + mid, None, int(TEAM_IDS[who]), False, body, None, at),
        )
    crew.commit()
    answer.forget_last()
    return crew


def _respond(db: Session, question: str, parsed: answer.Parsed | None) -> answer.Answer:
    def write(sql: str, params: dict[str, Any]) -> Any:
        return db.execute(text(sql), params).scalar_one()

    reply = answer.respond(
        db.connection(),
        write,
        user=ASKER,
        raw=question,
        surface="discord",
        intent=parsed,
        channel=TEST_CH,
    )
    db.commit()
    return reply


def _intent_for(case: dict[str, Any]) -> answer.Parsed | None:
    """What the bot gets: the LLM answer, through the worker's word filter, then to_parsed."""
    if case.get("intent") is None:
        return None
    filtered = chat_jobs._from_question(chat_jobs.IntentOut(**case["intent"]), case["question"])
    return intent.to_parsed(filtered, clock.today_ist())


def _check_text(reply: str, case: dict[str, Any]) -> None:
    for part in case.get("contains", []):
        assert part in reply, f"{part!r} not in:\n{reply}"
    for part in case.get("not_contains", []):
        assert part not in reply, f"{part!r} in:\n{reply}"
    if start := case.get("last_line_starts"):
        assert reply.splitlines()[-1].startswith(start), reply


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_answer_case(case: dict[str, Any], chat: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    kind = case["kind"]
    if kind == "split":
        body = "\n".join(f"line {i}" for i in range(case["lines"]))
        head, rest = answer.split_for_thread(body, get_settings().answer_thread_lines)
        assert len(head.splitlines()) == case["head_lines"]
        assert (len(rest.splitlines()) if rest else 0) == case["rest_lines"]
        if rest:
            assert head.splitlines()[-1] == answer.bw.REST_IN_THREAD
            assert body.splitlines()[3:] == rest.splitlines()  # nothing lost, same order
        return
    if kind == "guard":
        real = answer._chat_item

        def invented(row: Any) -> answer.Item:
            item = real(row)
            return answer.Item(item.seen_type, item.key, item.line + " 4242", item.facts)

        monkeypatch.setattr(answer, "_chat_item", invented)
        _check_text(_respond(chat, case["question"], None).text, case)
        return
    if kind == "worker":
        rid = intent.request(ASKER, case["question"])
        fake = FixtureLLM([case["intent"]])
        assert chat_jobs.run_answer_intents(chat, fake.client_factory, llm_ready=True) == 1
        found = intent.wait_for(rid, 1.0)
        parsed = intent.to_parsed(found, clock.today_ist())
        assert parsed is not None and parsed.intent == "who"
        prompt = fake.calls[0]["messages"][0]["content"]
        assert "<DATA" in prompt and case["question"] in prompt
        reply = _respond(chat, case["question"], parsed)
        assert "Dinesh Tewari at Google DeepMind" in reply.text
        return
    if before := case.get("before"):
        _respond(chat, before, None)
    parsed = _intent_for(case)
    reply = _respond(chat, case["question"], parsed)
    assert reply.intent == case["used"], reply.text
    _check_text(reply.text, case)


def test_there_are_30_cases() -> None:
    assert len(CASES) == 30 and len({c["id"] for c in CASES}) == 30


def test_no_llm_means_no_wait(chat: Session) -> None:
    rid = intent.request(ASKER, "which P1 have no email")
    chat_jobs.run_answer_intents(chat, llm_ready=False)
    started = clock.now_ist()
    assert intent.wait_for(rid, 10.0) is None
    assert (clock.now_ist() - started).total_seconds() < 2


def test_the_bot_stops_waiting_after_the_timeout() -> None:
    ticks = iter(range(100))
    slept: list[float] = []
    found = intent.wait_for(
        1,
        3.0,
        reader=lambda _: ("waiting", None),
        sleep=slept.append,
        monotonic=lambda: float(next(ticks)),
    )
    assert found is None and len(slept) == 2  # reads at 0, 1 and 2; at 3 the time is up


def test_a_stale_question_is_closed_by_the_worker(chat: Session) -> None:
    rid = intent.request(ASKER, "old question")
    chat.execute(
        text("update answer_intent set created_at = now() - interval '5 minutes' where id = :i"),
        {"i": rid},
    )
    chat.commit()
    assert chat_jobs.run_answer_intents(chat, llm_ready=True) == 0
    assert intent.read(rid) == ("failed", None)


def test_the_gateway_sends_an_answer_as_a1_and_the_rest_to_a_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import discord

    from saley.bot import gateway
    from saley.bot import words as bw
    from saley.bot.ping import Question

    lines = [f"- item {i} [sheet: Test Person]" for i in range(12)]
    body = "\n".join([*lines, "Say more for the next 5.", "Window: sheet as of 12:02 · 5 shown"])
    monkeypatch.setattr(
        gateway.SaleyBot, "_reply_text", staticmethod(lambda asked, channel_id=None: body)
    )
    bot = object.__new__(gateway.SaleyBot)
    bot.test_mode = True
    thread = SimpleNamespace(add_user=AsyncMock(), send=AsyncMock())
    channel = SimpleNamespace(id=TEST_CH, create_thread=AsyncMock(return_value=thread))
    message = SimpleNamespace(
        channel=channel,
        author=SimpleNamespace(id=1, display_name="Vaishnavi"),
        reply=AsyncMock(),
    )
    asyncio.run(bot._answer(message, Question("q", 1)))
    content = message.reply.await_args.args[0]
    view = message.reply.await_args.kwargs["view"]
    assert content.splitlines()[:3] == [f"[TEST] {lines[0]}", lines[1], lines[2]]
    assert bw.REST_IN_THREAD in content and content.endswith(bw.CLOSING)
    assert [c.label for c in view.children] == [bw.BTN_MORE, bw.BTN_DISMISS]
    kwargs = channel.create_thread.await_args.kwargs
    assert kwargs["type"] is discord.ChannelType.private_thread and kwargs["invitable"] is False
    thread.add_user.assert_awaited_once_with(message.author)
    sent = thread.send.await_args.args[0]
    assert sent.startswith("[TEST] - item 3") and sent.endswith("5 shown")
