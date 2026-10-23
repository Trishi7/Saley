"""R7-K03: cards_plan makes one card per dashboard item, and a second run adds nothing (D259)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import caps
from saley.core import clock
from saley.web.act import instruction_key
from saley.worker import cards_plan
from tests.conftest import contact_id, make_card


def _wednesday() -> datetime:
    today = clock.today_ist()
    return datetime.combine(
        clock.week_start(today) + timedelta(days=2), time(16), tzinfo=clock.tz()
    )


def _cards(db: Session) -> dict[str, dict[str, Any]]:
    rows = db.execute(
        text("select card_id, card_type, priority, payload, mentions, state, due_at from bot_post")
    ).mappings()
    return {r["card_id"]: dict(r) for r in rows}


@pytest.fixture
def seeded(synced: Session) -> tuple[Session, datetime]:
    db, now = synced, _wednesday()
    alex, ro = contact_id(db, "Alex Search"), contact_id(db, "Ro One")
    db.execute(
        text("update contact set meeting_date = :d where id = :c"),
        {"d": now.date() + timedelta(days=1), "c": alex},
    )
    db.execute(
        text("update contact set meeting_date = :d, next_step = null where id = :c"),
        {"d": now.date() - timedelta(days=1), "c": ro},
    )
    sid = db.execute(text("select id from users where short = 'sid'")).scalar_one()
    commitment = db.execute(
        text(
            "insert into commitment (owner, text, due, state) "
            "values (:u, 'Send the deck', :d, 'open') returning id"
        ),
        {"u": sid, "d": now.date() + timedelta(days=1)},
    ).scalar_one()
    db.execute(
        text(
            "insert into reminder (for_user, text, fire_at, commitment_id, kind) values "
            "(:u, 'r1', :t, :c, 'before'), (:u, 'call Sid', :t, null, 'before')"
        ),
        {"u": sid, "t": now - timedelta(hours=1), "c": commitment},
    )
    db.execute(text("insert into ask (to_user, question) values (:u, 'Who signs?')"), {"u": sid})
    db.execute(
        text(
            "insert into event (sheet_row_key, name, city, start, registered) values "
            "('Events:1', 'Evals Meetup', 'Bangalore', :d, true)"
        ),
        {"d": now.date() + timedelta(days=5)},
    )
    db.commit()
    make_card(db)
    return db, now


def test_one_card_per_item_and_a_second_run_adds_nothing(seeded: tuple[Session, datetime]) -> None:
    db, now = seeded
    first = cards_plan.plan_cards(db, now)
    db.commit()
    cards = _cards(db)
    types = {c["card_type"] for c in cards.values()}
    assert {"M4", "M5", "M6", "M7", "M8", "M9", "M10", "M11", "M14"} <= types, first.note()
    second = cards_plan.plan_cards(db, now)
    db.commit()
    assert second.queued == {} and len(_cards(db)) == len(cards)
    for card in cards.values():
        payload = card["payload"]
        assert payload["card_id"] == card["card_id"] and payload["card_type"] == card["card_type"]
        assert card["priority"] == caps.PRIORITY.get(card["card_type"], caps.OTHER_CARD)
        assert card["state"] == "queued" and "message" in payload and "acts" in payload


def test_reminders_become_m8_and_m9_and_are_marked_sent(seeded: tuple[Session, datetime]) -> None:
    db, now = seeded
    cards_plan.plan_cards(db, now)
    db.commit()
    kinds = {c["card_type"] for c in _cards(db).values() if c["card_id"].startswith(("m8-", "m9-"))}
    assert kinds == {"M8", "M9"}
    states = db.execute(text("select distinct state from reminder")).scalars().all()
    assert states == ["sent"]


def test_m7_done_is_the_dashboard_write(seeded: tuple[Session, datetime]) -> None:
    db, now = seeded
    cards_plan.plan_cards(db, now)
    db.commit()
    pat = contact_id(db, "Pat Park")
    m7 = next(c for c in _cards(db).values() if c["card_type"] == "M7")
    args = m7["payload"]["acts"]["done"]["args"]
    due = db.execute(
        text("select reconnect_due_on from v_task_inputs where contact_id = :c"), {"c": pat}
    ).scalar()
    assert args["key"] == instruction_key("task_done", [pat], [], "reconnect", due.isoformat())
    assert args["payload"] == {"task": "reconnect"} and args["targets"] == [pat]
    assert m7["payload"]["keys"][0].endswith(":task:reconnect")
    vaishnavi = db.execute(text("select id from users where short = 'vaishnavi'")).scalar_one()
    assert m7["mentions"] == [vaishnavi]


def test_m5_is_due_the_day_before_and_m6_after_the_meeting(
    seeded: tuple[Session, datetime],
) -> None:
    db, now = seeded
    cards_plan.plan_cards(db, now)
    db.commit()
    by_type = {c["card_type"]: c for c in _cards(db).values()}
    m5_due = by_type["M5"]["due_at"].astimezone(clock.tz())
    assert m5_due == datetime.combine(now.date(), time.min, tzinfo=clock.tz())
    m6_due = by_type["M6"]["due_at"].astimezone(clock.tz())
    assert m6_due == datetime.combine(now.date(), time(3), tzinfo=clock.tz())  # + 3 h (Q-R7-1)
    statuses = by_type["M6"]["payload"]["acts"]["next"]["args"]["values"]
    assert "completed" in statuses


def test_one_producer_that_fails_does_not_stop_the_others(
    seeded: tuple[Session, datetime], monkeypatch: pytest.MonkeyPatch
) -> None:
    db, now = seeded

    def broken(*_: object) -> None:
        raise ValueError("boom")

    monkeypatch.setattr(cards_plan, "PRODUCERS", (broken, cards_plan.asks))
    stats = cards_plan.plan_cards(db, now)  # hotfix: one failing producer is skipped
    assert stats.skipped == ["broken"] and "broken skipped" in stats.note()
    assert any(c["card_type"] == "M14" for c in _cards(db).values())
    monkeypatch.setattr(cards_plan, "PRODUCERS", (broken, broken))
    with pytest.raises(RuntimeError, match="every producer failed"):
        cards_plan.plan_cards(db, now)


def test_m2_holds_the_stories_that_matter_once(synced: Session) -> None:
    from tests.test_signals import add_story, link_all

    db = synced
    add_story(db, "Sarvam AI opens an evals call", "Sarvam AI wants partners.", importance=5)
    add_story(db, "Rosebud AI hires", "Rosebud AI hires two people.", importance=3)
    link_all(db)
    now = clock.now_ist()
    cards_plan.plan_cards(db, now)
    db.commit()
    m2 = [c for c in _cards(db).values() if c["card_type"] == "M2"]
    titles = [s for c in m2 for e in c["payload"]["message"]["embeds"] for s in [e["title"]]]
    assert "Sarvam AI opens an evals call" in titles
    assert all("Rosebud AI hires" not in t for t in titles)  # rated 3, not a P1 person
    # The next run does not repeat a story that is already on a card.
    db.execute(text("delete from bot_post where card_type <> 'M2'"))
    db.commit()
    before = len(_cards(db))
    cards_plan.needs_a_look(
        db, now + timedelta(days=1), cards_plan.team(db), cards_plan.PlanStats()
    )
    db.commit()
    assert len(_cards(db)) == before
