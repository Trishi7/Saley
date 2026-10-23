"""R7-K02: the D260 card order, the held line, the weekend rule and the hours window."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import caps, queue
from saley.bot import words as bw
from saley.core import clock
from saley.core.settings import Settings, get_settings

ORDER = ["M5", "M8", "M7", "M2", "M3", "M11", "M4"]


def _settings(**kw: object) -> Settings:
    base = {"bot_post_gap_minutes": 0, "user_hours": "sid:14:00-24:00", "dm_enabled": False}
    return Settings(_env_file=None, **{**base, **kw})


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    """A moment in Oct 2026 in IST. 23 Oct 2026 is a Friday."""
    return datetime(2026, 10, day, hour, minute, tzinfo=clock.tz())


def _post(i: int, card_type: str | None, due: datetime, **kw: object) -> caps.Post:
    return caps.Post(id=i, channel="sales", people=("sid",), due_at=due, card_type=card_type, **kw)


def test_cards_compete_in_the_d260_order_and_the_rest_is_held() -> None:
    now = _at(23, 15)  # Friday
    posts = [_post(i, t, now) for i, t in enumerate(reversed(ORDER), start=1)]
    plan = caps.plan(posts, now, {}, _settings())
    by_id = {p.id: p.card_type for p in posts}
    assert [by_id[i] for i in plan.ready] == ORDER[:4]
    assert {by_id[i] for i in plan.deferred} == set(ORDER[4:])
    count, until = plan.held["sid"]
    assert count == 3 and until.weekday() == 0  # Friday: the next slot is Monday 14:00
    assert caps.held_line(count, until, now) == "I am holding 3 more for Monday."


def test_held_for_tomorrow_on_a_weekday() -> None:
    now = _at(21, 15)  # Wednesday
    assert caps.held_line(2, _at(22, 14), now) == bw.HELD_TOMORROW.format(n=2)


def test_the_brief_and_the_pack_never_count() -> None:
    now = _at(23, 15)
    sends = {"sid": [now - timedelta(minutes=m) for m in (1, 2, 3, 4)]}  # the cap is reached
    posts = [_post(1, "M1", now), _post(2, "M12", now), _post(3, "M2", now)]
    plan = caps.plan(posts, now, sends, _settings(bot_post_gap_minutes=120))
    assert plan.ready == (1, 2) and 3 in plan.deferred


def test_r4_shapes_come_after_the_cards() -> None:
    now = _at(23, 15)
    posts = [_post(1, None, now, is_meeting=True), _post(2, "M4", now)]
    plan = caps.plan(posts, now, {}, _settings(bot_posts_per_person_per_day=1))
    assert plan.ready == (2,)


def test_weekend_only_meetings_and_reconnects() -> None:
    saturday = _at(24, 15)
    posts = [
        _post(1, "M5", saturday, is_meeting=True),
        _post(2, "M7", saturday, is_reconnect=True),
        _post(3, "M2", saturday),
    ]
    plan = caps.plan(posts, saturday, {}, _settings())
    assert set(plan.ready) == {1, 2}
    assert plan.deferred[3].weekday() == 0


@pytest.mark.parametrize(("hour", "minute", "ready"), [(23, 55, True), (0, 10, False)])
def test_the_hours_window(hour: int, minute: int, ready: bool) -> None:
    """Hours end at 24:00: 23:55 sends; 00:10 waits for 14:00 (docs/17 §8)."""
    now = _at(22, hour, minute)
    plan = caps.plan([_post(1, "M2", now)], now, {}, _settings())
    assert (plan.ready == (1,)) is ready
    if not ready:
        assert plan.deferred[1] == _at(22, 14)


def _queued(db: Session, card_id: str, card_type: str, due: datetime, sid: int) -> None:
    db.execute(
        text(
            "insert into bot_post (kind, shape, channel, mentions, body, rank, due_at, state, "
            "card_type, priority, card_id, payload, rendered_hash) values ('c', 'c', 'sales', "
            ":m, 'b', 0, :d, 'queued', :t, :p, :c, cast(:j as jsonb), 'h')"
        ),
        {
            "m": [sid],
            "d": due,
            "t": card_type,
            "p": caps.PRIORITY.get(card_type),
            "c": card_id,
            "j": json.dumps({"keys": [], "acts": {}}),
        },
    )


def test_the_last_card_of_the_day_carries_the_held_line(
    db: Session, set_env: Callable[..., None]
) -> None:
    set_env(BOT_POST_GAP_MINUTES="0", USER_HOURS="sid:00:00-24:00")
    sid = db.execute(text("select id from users where short = 'sid'")).scalar_one()
    now = _at(21, 15)  # Wednesday
    for i, t in enumerate(ORDER):
        _queued(db, f"m-t{i}", t, now - timedelta(minutes=5), sid)
    stats = queue.plan_queue(db, now)
    db.commit()
    assert stats.ready == 4 and stats.deferred == 3
    rows = dict(
        db.execute(text("select card_type, held_reason from bot_post where state = 'ready'")).all()
    )
    assert rows["M2"] == "I am holding 3 more for tomorrow."
    assert rows["M5"] is None
    assert get_settings().bot_post_gap_minutes == 0


def test_a_card_done_before_it_is_sent_is_dropped(db: Session) -> None:
    sid = db.execute(text("select id from users where short = 'sid'")).scalar_one()
    now = clock.now_ist()
    _queued(db, "m8-done", "M8", now - timedelta(minutes=1), sid)
    db.execute(
        text("update bot_post set done_at = now(), done_by = 'Sid' where card_id = 'm8-done'")
    )
    db.commit()
    queue.plan_queue(db, now)
    db.commit()
    row = db.execute(
        text("select state, held_reason from bot_post where card_id = 'm8-done'")
    ).one()
    assert tuple(row) == ("dropped", queue.DONE_BEFORE_SEND)
