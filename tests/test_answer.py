"""@Saley answer engine: review fixes of R4 ticket 6 (D131).

The base fixtures (a synced sheet, stories, a task, a meeting) and the first answer tests live
in tests/test_bot.py. This file holds the cap, footer, order, size, clock and dashboard rules.
"""

from __future__ import annotations

import ast
import inspect
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import answer as bot_answer
from saley.bot import words as bot_words
from tests import test_bot as base
from tests.test_bot import ASK_H, post_ask, seen_of

# Fixtures of tests/test_bot.py, used here by name.
answers = base.answers
web = base.web

FOOTER_LINE = re.compile(
    r"Window: .+ IST · \d+ shown · \d+\+? more · \d+ left out as seen by you\.|"
    r"Window: sheet as of \d\d:\d\d · \d+ shown · \d+\+? more · \d+ left out as seen by you\."
)
ISO_NEWS = "news since 2026-10-14"
PREFIX = "[TEST] "
DISCORD_LIMIT = 1990


def utc(day: int, hour: int, minute: int, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def ask_at(
    db: Session,
    question: str,
    user: str = "sid",
    surface: str = "discord",
    now: datetime | None = None,
    write=None,
):
    def commit_write(sql: str, params: dict):
        value = db.execute(text(sql), params).scalar_one()
        db.commit()
        return value

    return bot_answer.respond(
        db.connection(),
        write or commit_write,
        user=user,
        raw=question,
        surface=surface,
        now=now or utc(20, 8, 40),  # Tue 20 Oct 14:10 IST
    )


# ------------------------------------------------------------------ 1. cap after the seen filter


def test_cap_applies_to_unseen_rows_only(answers: Session) -> None:
    answers.execute(
        text(
            "insert into contact (account_id, sheet_row_key, name, name_key, about) "
            "select (select account_id from contact where name = 'Sshubam Verma'), "
            "'bulk-' || g, 'Bulk ' || lpad(g::text, 4, '0'), 'bulk' || g, 'bulk evals' "
            "from generate_series(1, 650) g"
        )
    )
    answers.execute(
        text(
            "insert into user_seen (user_id, item_type, item_id) "
            "select (select id from users where short = 'sid'), 'answer_who', c.id "
            "from (select id from contact where name like 'Bulk %' order by name limit 520) c"
        )
    )
    answers.commit()
    reply = ask_at(answers, "who at Sarvam works on bulk")
    assert reply.known and reply.shown == 5
    assert (reply.more, reply.left_out) == (125, 520)
    assert "Bulk 0521" in reply.text and "Bulk 0001" not in reply.text
    assert "125 more" in reply.text and "520 left out as seen by you." in reply.text
    # Everyone else still sees the top rows first.
    other = ask_at(answers, "who at Sarvam works on bulk", user="kushal")
    assert (other.shown, other.more, other.left_out) == (5, 645, 0)
    assert "Bulk 0001" in other.text


def test_a_person_who_saw_everything_gets_nothing_new(answers: Session) -> None:
    for _ in range(2):
        ask_at(answers, ISO_NEWS)
    third = ask_at(answers, ISO_NEWS)
    assert bot_words.NOTHING_NEW in third.text
    assert (third.shown, third.more, third.left_out) == (0, 0, 7)


# ------------------------------------------------------------------ 2. footer is the last line


@pytest.mark.parametrize("repeat", [1, 2, 3])
def test_footer_is_the_last_line(answers: Session, repeat: int) -> None:
    for _ in range(repeat - 1):
        ask_at(answers, ISO_NEWS)
    reply = ask_at(answers, ISO_NEWS)
    last = reply.text.splitlines()[-1]
    assert FOOTER_LINE.fullmatch(last), reply.text
    if repeat == 1:
        assert reply.more == 2
        hint = bot_words.MORE_HINT.format(n=2)
        assert reply.text.splitlines()[-2] == hint


def test_every_kind_of_answer_ends_with_the_footer(answers: Session) -> None:
    for question in (
        "who at Sarvam works on evals",
        "what is Alex Search working on",
        "which P1 have no email",
        "what is due this week",
        "who at Nowhere Inc works on evals",
    ):
        reply = ask_at(answers, question, user="trishi")
        assert FOOTER_LINE.fullmatch(reply.text.splitlines()[-1]), (question, reply.text)


# ------------------------------------------------------------------ 3. order, atomicity, size


class Recorder:
    """A write callable that records the function names and can fail one of them."""

    def __init__(self, db: Session, fail: str | None = None, result=None) -> None:
        self.db, self.fail, self.result, self.calls = db, fail, result, []

    def __call__(self, sql: str, params: dict):
        name = re.search(r"saley_\w+", sql)[0]
        self.calls.append(name)
        if name == self.fail:
            if self.result is None:
                raise RuntimeError("write failed")
            return self.result
        value = self.db.execute(text(sql), params).scalar_one()
        self.db.commit()
        return value


def test_log_runs_before_mark_seen(answers: Session) -> None:
    rec = Recorder(answers)
    ask_at(answers, ISO_NEWS, write=rec)
    assert rec.calls[0] == "saley_ask_log" and "saley_mark_seen" in rec.calls[1:]


def test_failed_log_marks_nothing_seen(answers: Session) -> None:
    with pytest.raises(RuntimeError):
        ask_at(answers, ISO_NEWS, write=Recorder(answers, fail="saley_ask_log"))
    assert seen_of(answers, "sid") == []
    with pytest.raises(RuntimeError, match="saley_ask_log"):
        ask_at(answers, ISO_NEWS, write=Recorder(answers, fail="saley_ask_log", result=0))
    assert seen_of(answers, "sid") == []


def test_long_values_fit_one_discord_message_and_the_log(answers: Session) -> None:
    long = "Z" * 400
    answers.execute(text("update account set name = :n where name ilike 'Sarvam%'"), {"n": long})
    answers.execute(
        text(
            "insert into contact (account_id, sheet_row_key, name, name_key, about) "
            "select (select account_id from contact where name = 'Sshubam Verma'), "
            "'long-' || g, repeat('N', 400) || g, 'long' || g, 'evals ' || repeat('A', 400) "
            "from generate_series(1, 8) g"
        )
    )
    answers.commit()
    reply = ask_at(answers, "who at ZZZ works on evals")
    assert reply.shown == 5 and reply.known
    assert len(PREFIX + reply.text) <= DISCORD_LIMIT
    assert FOOTER_LINE.fullmatch(reply.text.splitlines()[-1])
    assert len(reply.text) < 4000
    logged = answers.execute(text("select answer from bot_exchange")).scalar_one()
    assert logged == reply.text


def test_worst_case_lines_of_every_kind_fit(answers: Session) -> None:
    long = "Q" * 400
    answers.execute(text("update contact set name = :n, about = :n"), {"n": "evals " + long})
    answers.execute(
        text("update contact set meeting_date = date '2026-10-22', meeting_status = 'planned'")
    )
    answers.commit()
    for question in (
        "who at Sarvam works on evals",
        "what is due this week",
        "which P1 have no email",
    ):
        reply = ask_at(answers, question, user="trishi")
        assert len(PREFIX + reply.text) <= DISCORD_LIMIT, question
        assert FOOTER_LINE.fullmatch(reply.text.splitlines()[-1]), question


def test_compose_drops_lines_that_do_not_fit() -> None:
    items = tuple(
        bot_answer.Item("story", n, "- " + "x" * 600 + " [news: t]", ()) for n in range(1, 6)
    )
    fetched = bot_answer.Fetched(items, utc(20, 0, 0), utc(20, 8, 40))
    reply, kept, more = bot_answer.compose(fetched, left_out=0, fresh=5)
    assert len(PREFIX + reply) <= DISCORD_LIMIT
    assert len(kept) < 5 and more == 5 - len(kept)
    assert FOOTER_LINE.fullmatch(reply.splitlines()[-1])


# ------------------------------------------------------------------ 4. structure


def test_fetchers_share_one_signature() -> None:
    assert set(bot_answer.FETCHERS) == {"who", "working", "p1", "news", "due", "chat"}  # R7 D262
    for name, fetcher in bot_answer.FETCHERS.items():
        params = list(inspect.signature(fetcher).parameters)
        assert params == ["conn", "parsed", "now", "user"], name


def test_no_function_in_the_answer_engine_is_over_50_lines() -> None:
    source = Path(bot_answer.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef):
            assert node.end_lineno - node.lineno + 1 <= 50, node.name


def test_fetched_rows_are_tuples(answers: Session) -> None:
    parsed = bot_answer.parse(ISO_NEWS, utc(20, 8, 40).date())
    fetched = bot_answer.fetch_news(answers.connection(), parsed, utc(20, 8, 40), "sid")
    assert isinstance(fetched.items, tuple) and isinstance(fetched.items[0].facts, tuple)


# ------------------------------------------------------------------ 6. IST clock


def test_just_after_midnight_ist_is_the_new_day(answers: Session) -> None:
    # 19 Oct 19:00 UTC is Tue 20 Oct 00:30 IST.
    reply = ask_at(answers, "news since today", now=utc(19, 19, 0))
    assert "Window: Tue 20 Oct 00:00–00:30 IST" in reply.text


def test_late_evening_ist_keeps_the_day_and_the_week(answers: Session) -> None:
    # 25 Oct 18:20 UTC is Sun 25 Oct 23:50 IST.
    reply = ask_at(answers, "what is due this week", now=utc(25, 18, 20))
    assert "Window: Mon 19 Oct–Sun 25 Oct IST" in reply.text
    # 25 Oct 18:40 UTC is Mon 26 Oct 00:10 IST: a new week.
    reply = ask_at(answers, "what is due this week", now=utc(25, 18, 40), user="kushal")
    assert "Window: Mon 26 Oct–Sun 1 Nov IST" in reply.text


# ------------------------------------------------------------------ 7, 8. windows and empty answers


def test_current_state_lookups_say_sheet_as_of(answers: Session) -> None:
    for question in (
        "who at Sarvam works on evals",
        "what is Alex Search working on",
        "which P1 have no email",
    ):
        reply = ask_at(answers, question, user="trishi")
        foot = reply.text.splitlines()[-1]
        assert foot.startswith("Window: sheet as of 14:10"), foot
        assert "00:00" not in foot


def test_zero_results_is_a_real_empty_answer(answers: Session) -> None:
    reply = ask_at(answers, "who at Nowhere Inc works on evals")
    assert reply.known and reply.intent == "who"
    assert reply.text.splitlines()[0] == bot_words.NOTHING_FOUND
    assert FOOTER_LINE.fullmatch(reply.text.splitlines()[-1])
    assert (reply.shown, reply.more, reply.left_out) == (0, 0, 0)
    assert ask_at(answers, "what is the weather").text.startswith(bot_words.UNKNOWN_HAVE)
    row = answers.execute(text("select intent, answer from bot_exchange order by id")).all()
    assert row[0].intent == "who" and row[0].answer == reply.text


# ------------------------------------------------------------------ 9. more memory


def test_more_memory_is_set_only_after_a_good_fetch(answers: Session, monkeypatch) -> None:
    def boom(*_args, **_kw):
        raise RuntimeError("db down")

    monkeypatch.setitem(bot_answer.FETCHERS, "news", boom)
    with pytest.raises(RuntimeError):
        ask_at(answers, ISO_NEWS)
    assert ask_at(answers, "more").text == bot_words.NO_MORE
    assert "more" in inspect.getdoc(bot_answer).lower() and "per process" in inspect.getdoc(
        bot_answer
    )


# ------------------------------------------------------------------ 11, 12. clean text


@pytest.mark.parametrize(
    ("raw", "gone"),
    [
        ("Hi <@123456> and <#99> and <!77>", ["<@", "<#", "<!"]),
        ("**bold** _it_ ~~x~~ `code` |spoiler|", ["*", "_", "~", "`", "|"]),
        (
            "a\u200bb\u200fc\u202ed\u2066e\u2069f",
            ["\u200b", "\u200f", "\u202e", "\u2066", "\u2069"],
        ),
        ("@everyone and @here", ["@everyone", "@here"]),
    ],
)
def test_clean_strips_mentions_markdown_and_invisible_marks(raw: str, gone: list[str]) -> None:
    out = bot_answer._clean(raw)
    for piece in gone:
        assert piece not in out, (raw, out)


def test_clean_caps_length_with_an_ellipsis() -> None:
    out = bot_answer._clean("w" * 500, 50)
    assert len(out) == 50 and out.endswith("…")


def test_raw_input_is_cut_before_it_is_tidied() -> None:
    huge = "who at Sarvam works on evals " + "x " * 5_000_000
    assert len(bot_answer._tidy(huge)) <= bot_answer.MAX_QUESTION * 2
    assert bot_answer.parse(huge, utc(20, 8, 40).date()) is None


# ------------------------------------------------------------------ 5, 10. dashboard path

ASK_KEYS = {"text", "known", "shown", "more", "left_out"}


def test_ask_api_returns_the_exact_ask_out_shape(web, answers: Session) -> None:
    r = post_ask(web, ISO_NEWS)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == ASK_KEYS
    assert isinstance(body["text"], str) and body["known"] is True
    assert all(isinstance(body[k], int) for k in ("shown", "more", "left_out"))
    assert FOOTER_LINE.fullmatch(body["text"].splitlines()[-1])


@pytest.mark.parametrize("bad", ["", "   ", "\n\t ", "x" * 301])
def test_ask_api_refuses_bad_text_with_422(web, bad: str) -> None:
    r = web.post("/api/ask", json={"text": bad}, headers=ASK_H)
    assert r.status_code == 422 and r.json() == {"detail": "The request is not valid."}


def test_ask_api_logs_a_dashboard_exchange(web, answers: Session) -> None:
    post_ask(web, ISO_NEWS)
    answers.expire_all()
    row = answers.execute(
        text(
            "select u.short, e.surface, e.intent, e.left_out from bot_exchange e "
            "join users u on u.id = e.user_id"
        )
    ).one()
    assert (row.short, row.surface, row.intent, row.left_out) == ("sid", "dashboard", "news", 0)


def test_ask_api_seen_is_per_person(web, answers: Session) -> None:
    first = post_ask(web, ISO_NEWS).json()
    again = post_ask(web, ISO_NEWS).json()
    other = post_ask(web, ISO_NEWS, who="kushal").json()
    assert (first["shown"], first["left_out"]) == (5, 0)
    assert (again["shown"], again["left_out"]) == (2, 5)
    assert (other["shown"], other["left_out"]) == (5, 0)
    assert len(seen_of(answers, "sid")) == 7 and len(seen_of(answers, "kushal")) == 5


def test_more_memory_is_separate_for_each_surface(web, answers: Session) -> None:
    ask_at(answers, ISO_NEWS)  # Discord question of sid
    assert post_ask(web, "more").json()["text"] == bot_words.NO_MORE  # nothing asked on the web
    assert post_ask(web, ISO_NEWS, who="kushal").json()["shown"] == 5
    assert post_ask(web, "more", who="kushal").json()["shown"] == 2
    assert ask_at(answers, "more").shown == 2  # Discord memory of sid is still there


@pytest.mark.parametrize("failing", ["_log", "_mark_seen"])
def test_ask_api_failure_is_fixed_text_and_marks_nothing(
    web, answers: Session, monkeypatch, failing: str
) -> None:
    def boom(*_args, **_kw):
        raise RuntimeError("postgresql://saley:secret@db/saley select * from contact")

    monkeypatch.setattr(bot_answer, failing, boom)
    r = post_ask(web, ISO_NEWS)
    assert r.status_code == 503
    assert r.json() == {"detail": bot_words.ANSWER_FAILED}
    assert "secret" not in r.text
    if failing == "_log":
        assert seen_of(answers, "sid") == []
