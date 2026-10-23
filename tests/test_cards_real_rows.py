"""Hotfix (23 Oct, server): cards from the real view rows, not from sample dicts.

`saley worker --once cards_plan` failed on the server with KeyError 'ref_id': the M3 producer
passed one key and the renderer read another, and the tests only rendered sample dicts. Each
test here runs the real producer on the real views (v_suggest_warm, v_signal_linked, event,
account + stage_log, v_deliverable_late) and renders what it queued.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import health as bot_health
from saley.core import clock
from saley.intel import pack
from saley.worker import cards_plan, jobs
from tests.conftest import default_sheet, poc_row


@pytest.fixture
def raw_sheet(sheet_today: date) -> dict[str, list[list[Any]]]:
    """The default sheet plus one warm person: Listed, not contacted, at a company where a
    colleague is connected (Dinesh Tewari at Google DeepMind), so v_suggest_warm has a row."""
    sheet = default_sheet(sheet_today)
    sheet["Outreach PoCs"].append(
        poc_row(
            sr_no=8,
            company="Google DeepMind",
            name="Wanda Warm",
            industry="AI Labs",
            designation="Head of Research",
            first_contact=False,
        )
    )
    return sheet


def _week_day(weekday: int, hour: int = 16) -> datetime:
    today = clock.today_ist()
    day = clock.week_start(today) + timedelta(days=weekday)
    return datetime.combine(day, time(hour), tzinfo=clock.tz())


def _cards(db: Session, card_type: str) -> list[dict[str, Any]]:
    rows = db.execute(
        text("select payload from bot_post where card_type = :t"), {"t": card_type}
    ).scalars()
    return list(rows)


def test_m3_from_the_suggestions_view(synced: Session) -> None:
    db = synced
    view = db.execute(text("select ref_id, name from v_suggest_warm")).all()
    assert [r.name for r in view] == ["Wanda Warm"]
    stats = cards_plan.PlanStats()
    cards_plan.suggestions(db, _week_day(1), cards_plan.team(db), stats)  # a Tuesday
    (card,) = _cards(db, "M3")
    fields = card["message"]["embeds"][0]["fields"]
    assert fields[0]["name"] == "Wanda Warm · Google DeepMind"
    ref = str(view[0].ref_id)
    assert card["acts"]["do"]["args"]["ref"] == ref and ref in card["acts"]["do"]["args"]["texts"]
    assert card["acts"]["skip"]["args"] == {"item_type": "suggest_warm", "ids": [int(ref)]}


def test_m2_from_the_linked_signals_view(synced: Session) -> None:
    from tests.test_signals import add_story, link_all

    db = synced
    add_story(db, "Sarvam AI opens an evals call", "Sarvam AI wants partners.", importance=5)
    link_all(db)
    cards_plan.needs_a_look(db, clock.now_ist(), cards_plan.team(db), cards_plan.PlanStats())
    cards = _cards(db, "M2")
    assert cards and cards[0]["message"]["embeds"][0]["title"] == "Sarvam AI opens an evals call"


def test_m4_from_the_event_table(synced: Session) -> None:
    db = synced
    wednesday = _week_day(2)
    db.execute(
        text(
            "insert into event (sheet_row_key, name, city, start, registered) "
            "values ('Events:9', 'Evals Meetup', 'Bangalore', :d, true)"
        ),
        {"d": wednesday.date() + timedelta(days=3)},
    )
    db.commit()
    cards_plan.events(db, wednesday, cards_plan.team(db), cards_plan.PlanStats())
    (card,) = _cards(db, "M4")
    assert card["message"]["embeds"][0]["fields"][0]["name"] == "Evals Meetup"


def test_m11_from_accounts_and_stage_log(synced: Session) -> None:
    db = synced
    cards_plan.new_companies(db, _week_day(2), cards_plan.team(db), cards_plan.PlanStats())
    cards = _cards(db, "M11")
    assert cards, "the synced accounts entered the sheet this week"
    names = {c["message"]["embeds"][0]["title"] for c in cards}
    known = set(db.execute(text("select name from account")).scalars())
    assert names <= known


def test_m13_from_the_late_deliverables_view(synced: Session) -> None:
    db = synced
    late = db.execute(
        text("select count(*) from v_deliverable_late where cardinality(blocks_goals) > 0")
    ).scalar()
    card, _owners = pack.blocker_card(db, "m13-real")
    text_ = card["message"]["embeds"][0]["description"]
    assert late and text_.count("• ") == min(late, 5)


def test_a_skipped_producer_is_counted_in_the_health_line(
    synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = synced

    def broken(*_: object) -> None:
        raise KeyError("ref_id")

    monkeypatch.setattr(cards_plan, "PRODUCERS", (broken, cards_plan.asks))
    db.execute(
        text(
            "insert into ask (to_user, question) "
            "select id, 'Who signs?' from users where short = 'sid'"
        )
    )
    db.commit()
    monkeypatch.setattr(jobs, "session_scope", _scope(db))
    note = jobs.cards_plan()
    assert note.endswith("broken skipped")
    db.execute(
        text(
            "insert into job_runs (job, status, started_at, finished_at, note) "
            "values ('cards_plan', 'ok', now(), now(), :n)"
        ),
        {"n": note},
    )
    db.commit()
    assert bot_health.skipped_producers(db) == 1
    assert "Card runs with a skipped part in 24 h: 1" in bot_health.health_text(db, clock.now_ist())


def _scope(db: Session) -> Any:
    from contextlib import contextmanager

    @contextmanager
    def scope() -> Any:
        yield db
        db.commit()

    return scope
