"""R4 decisions D139 (brief greeting) and D141 (team-only answers, "(they said)").

The fixtures live in tests/test_bot.py and are used here by name.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import answer as bot_answer
from saley.bot import brief, identity
from saley.bot import words as bw
from saley.core.settings import get_settings
from tests import test_bot as base

bot_env = base.bot_env
clean_posts = base.clean_posts
at_time = base.at_time
answers = base.answers
SID_ID, KUSHAL_ID, STRANGER_ID = base.SID_ID, base.KUSHAL_ID, 999


@pytest.fixture
def keep_users(clean_posts: Session):
    """The brief tests change name and role. Put the rows back, the table is shared."""
    saved = clean_posts.execute(text("select id, name, role from users")).all()
    yield clean_posts
    clean_posts.rollback()
    for row in saved:
        clean_posts.execute(
            text("update users set name = :n, role = :r where id = :i"),
            {"n": row.name, "r": row.role, "i": row.id},
        )
    clean_posts.commit()


# ------------------------------------------------------------------ D139 brief greeting


def _brief_body(db: Session, short: str) -> str:
    return db.execute(
        text(
            "select b.body from bot_post b join users u on u.id = b.for_user "
            "where b.shape = 'daily_brief' and u.short = :s"
        ),
        {"s": short},
    ).scalar_one()


def test_brief_greets_by_first_name_with_the_line_count(bot_env, keep_users, at_time) -> None:
    bot_env(DM_ENABLED="true", DM_USERS="sid,vaishnavi")
    base.brief_data(keep_users)
    keep_users.execute(text("update users set name = 'Siddarth Rao' where short = 'sid'"))
    keep_users.execute(text("update users set name = '' where short = 'vaishnavi'"))
    keep_users.commit()
    at_time(base.at(base.TUE, 14))
    assert brief.queue_briefs(keep_users).queued == 2
    sid = _brief_body(keep_users, "sid").splitlines()
    assert sid[0] == "Hi Siddarth. 2 things today:"  # approvals + reminder
    assert len(sid) - 2 == 2  # head and link; the other lines are the 2 things
    # An empty name falls back to the capitalized short name.
    assert _brief_body(keep_users, "vaishnavi").splitlines()[0] == "Hi Vaishnavi. 2 things today:"
    assert "Good morning" not in sid[0]


def test_brief_with_one_line_says_1_thing(bot_env, keep_users, at_time) -> None:
    bot_env(DM_ENABLED="true", DM_USERS="kushal")
    base.brief_data(keep_users)
    keep_users.execute(text("update users set name = 'Kushal Shah' where short = 'kushal'"))
    keep_users.execute(text("update users set role = 'viewer' where short = 'kushal'"))
    keep_users.commit()
    at_time(base.at(base.TUE, 14))
    brief.queue_briefs(keep_users)
    body = _brief_body(keep_users, "kushal")
    assert body.splitlines()[0] == "Hi Kushal. 1 thing today:"
    assert "Good morning" not in body


def test_brief_words_have_no_good_morning() -> None:
    assert "Good morning" not in bw.BRIEF_HEAD + bw.BRIEF_HEAD_ONE


# ------------------------------------------------------------------ D141 team-only answers


def _map(bot_env, db: Session, *, instruct: str) -> None:
    bot_env(DISCORD_USER_MAP=f"sid:{SID_ID};kushal:{KUSHAL_ID}", DISCORD_INSTRUCT_IDS=instruct)
    identity.sync_user_map(db, get_settings())


def _count(db: Session, table: str) -> int:
    return db.execute(text(f"select count(*) from {table}")).scalar_one()


QUESTIONS = (
    "who at Google works on evals",
    "what is Alex Search working on",
    "which P1 have no email",
    "news since Wednesday",
    "what is due this week",
    "more",
)


def test_known_non_team_user_gets_the_team_only_line(bot_env, answers: Session) -> None:
    _map(bot_env, answers, instruct=SID_ID)  # kushal's id is not on the list
    for question in QUESTIONS:
        reply = bot_answer.discord_reply(answers, int(KUSHAL_ID), question, get_settings())
        assert reply == bw.TEAM_ONLY == "I can answer that for the Membrane team only."
    assert _count(answers, "bot_exchange") == 0
    assert _count(answers, "user_seen") == 0


def test_team_user_still_gets_the_answer(bot_env, answers: Session) -> None:
    _map(bot_env, answers, instruct=f"{SID_ID},{KUSHAL_ID}")
    reply = bot_answer.discord_reply(answers, int(SID_ID), "which P1 have no email", get_settings())
    assert "Alex Search" in reply and bw.TEAM_ONLY not in reply
    assert _count(answers, "bot_exchange") == 1


def test_unknown_author_still_gets_the_identity_line(bot_env, answers: Session) -> None:
    _map(bot_env, answers, instruct=SID_ID)
    reply = bot_answer.discord_reply(answers, STRANGER_ID, "news since Wednesday", get_settings())
    assert reply == bw.UNKNOWN_AUTHOR


# ------------------------------------------------------------------ D141 "(they said)"


def _set_confidence(db: Session, confidence: int | None) -> None:
    db.execute(text("update fact set confidence = :c where kind = 'working_on'"), {"c": confidence})
    db.commit()


def test_low_confidence_fact_shows_they_said(answers: Session) -> None:
    _set_confidence(answers, 2)
    reply = base.ask(answers, "what is Alex Search working on").text
    line = next(ln for ln in reply.splitlines() if "Alex Search" in ln)
    assert line.count("(they said)") == 1
    assert "Evals of search agents (they said)" in line
    assert line.endswith("]")  # the citation by name stays last (D131)
    assert "[note " in line


def test_normal_and_unknown_confidence_facts_have_no_marker(answers: Session) -> None:
    for confidence in (3, None):
        _set_confidence(answers, confidence)
        bot_answer.forget_last()
        answers.execute(text("delete from user_seen"))
        answers.commit()
        reply = base.ask(answers, "what is Alex Search working on").text
        assert "Evals of search agents" in reply
        assert "(they said)" not in reply


def test_they_said_passes_the_guard_and_keeps_name_citations(answers: Session) -> None:
    _set_confidence(answers, 2)
    reply = base.ask(answers, "who at Google works on evals")
    assert reply.known and reply.text != bw.ANSWER_FAILED
    assert "(they said)" in reply.text
    for line in reply.text.splitlines():
        if line.startswith("- "):
            assert line.endswith("]")
            assert "[sheet: " in line or "[note " in line
