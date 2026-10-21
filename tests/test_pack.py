"""R4 tickets 3 and 4: scorecard snapshots, "last week N" and the weekly pack (D117, D118, D130).

No network and no LLM. Time is mocked through the pack's clock names, never slept on. A pack is
built from the synced fixture sheet; its posts are only queued here (ticket 5 delivers them).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.core.clock import now_ist, today_ist
from saley.intel import metrics, pack
from saley.web import words as w
from saley.web.app import create_app
from saley.worker import registry
from saley.worker.jobs import register_all, snapshot_day
from saley.worker.scheduler import build_scheduler
from tests.conftest import SignedClient

IST = ZoneInfo("Asia/Kolkata")
MONDAY = date(2026, 10, 19)
LAST_MONDAY = MONDAY - timedelta(days=7)


def ist(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


@pytest.fixture(autouse=True)
def _clean_tables(db: Session):
    yield
    db.rollback()
    db.execute(text("truncate weekly_pack, bot_post, scorecard_snapshot restart identity cascade"))
    db.commit()


@pytest.fixture
def client(synced: Session) -> Any:
    with SignedClient(create_app()) as c:
        yield c


def snapshot_row(
    db: Session,
    day: date,
    industry: str = "AI Labs",
    people: int | None = 5,
    contacted: int | None = 2,
    connect_pct: int | None = 40,
    meetings: int | None = 1,
    deals: int | None = 0,
) -> None:
    db.execute(
        text(
            "insert into scorecard_snapshot (day, industry, people, contacted, connect_pct, "
            "reply_pct, meetings, deals) values (:d, :i, :p, :c, :cp, null, :m, :de) "
            "on conflict (day, industry) do update set people = excluded.people"
        ),
        {
            "d": day,
            "i": industry,
            "p": people,
            "c": contacted,
            "cp": connect_pct,
            "m": meetings,
            "de": deals,
        },
    )
    db.commit()


def block_goal(db: Session, count: int, owner: str | None = "Legal") -> None:
    """`count` late deliverables that block the first goal, most late first by item number."""
    goal_id = db.execute(text("select id from goal order by id limit 1")).scalar_one()
    db.execute(text("update deliverable set blocks_goal_ids = '{}'"))
    for n in range(count):
        db.execute(
            text(
                "insert into deliverable (sheet_row_key, item, dependency, due, status, "
                "blocks_goal_ids) values (:k, :i, :o, current_date - :late, 'Not started', "
                "array[:g]::int[])"
            ),
            {
                "k": f"t-{n}",
                "i": f"Blocking item {n} - long detail text",
                "o": owner,
                "late": 10 + n,
                "g": goal_id,
            },
        )
    db.commit()


def build(db: Session, now: datetime | None = None) -> pack.PackStats:
    stats = pack.build_weekly_pack(db, now=now or ist(MONDAY, 14))
    db.commit()
    return stats


def posts(db: Session) -> dict[str, Any]:
    rows = db.execute(text("select * from bot_post order by id")).mappings().all()
    return {r["shape"]: r for r in rows}


def body_of(db: Session, week: date = MONDAY) -> dict[str, Any]:
    return db.execute(
        text("select body from weekly_pack where week_start = :w"), {"w": week}
    ).scalar_one()


# ------------------------------------------------------------------ R4-P01: the cron


def test_cron_fires_once_monday_1400_ist(synced: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    job = {j.id: j for j in build_scheduler().get_jobs()}["weekly_pack"]
    trigger = job.trigger
    assert str(trigger.timezone) == "Asia/Kolkata"
    assert job.misfire_grace_time and job.misfire_grace_time >= 3600  # a late worker still fires
    # Just before: the next fire is today at 14:00. Just after: the next Monday, not a second one.
    assert trigger.get_next_fire_time(None, ist(MONDAY, 13, 59)) == ist(MONDAY, 14)
    after = trigger.get_next_fire_time(None, ist(MONDAY, 14, 0) + timedelta(seconds=1))
    assert after == ist(MONDAY + timedelta(days=7), 14)
    tuesday = trigger.get_next_fire_time(None, ist(MONDAY + timedelta(days=1), 9))
    assert tuesday == ist(MONDAY + timedelta(days=7), 14)

    # The job itself: the mocked clock says Monday 14:00; the second run does nothing.
    block_goal(synced, 2)
    monkeypatch.setattr(pack, "now_ist", lambda: ist(MONDAY, 14))
    register_all()
    assert registry.run_job("weekly_pack") == "ok"
    assert registry.run_job("weekly_pack") == "ok"
    assert synced.execute(text("select count(*) from weekly_pack")).scalar_one() == 1
    assert synced.execute(text("select count(*) from bot_post")).scalar_one() == 2


def test_second_run_in_the_same_week_does_nothing(synced: Session) -> None:
    block_goal(synced, 1)
    first = build(synced, ist(MONDAY, 14))
    again = build(synced, ist(MONDAY + timedelta(days=1), 9))  # a misfire retry on Tuesday
    assert first.built is True and again.built is False
    assert synced.execute(text("select count(*) from weekly_pack")).scalar_one() == 1
    assert synced.execute(text("select count(*) from bot_post")).scalar_one() == 2
    assert again.note() == "pack for 2026-10-19 exists"


def test_next_week_builds_a_new_pack(synced: Session) -> None:
    build(synced, ist(MONDAY, 14))
    nxt = build(synced, ist(MONDAY + timedelta(days=7), 14))
    assert nxt.built is True
    assert synced.execute(text("select count(*) from weekly_pack")).scalar_one() == 2


# ------------------------------------------------------------------ R4-P01: same rows as Scorecard


def test_pack_matches_scorecard(
    synced: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_row(synced, LAST_MONDAY, "AI Labs", people=7)
    monkeypatch.setattr("saley.web.app.today_ist", lambda: MONDAY)
    build(synced)
    api_rows = client.get("/api/scorecard").json()["items"]
    pack_rows = body_of(synced)["scorecard"]
    assert api_rows and pack_rows == api_rows  # same industries, order, numbers and deltas
    labs = next(r for r in pack_rows if r["industry"] == "AI Labs")
    assert labs["last_week"]["people"] == 7


# ------------------------------------------------------------------ R4-P02 and R4-P04: last week


def test_last_week_is_last_monday_snapshot(synced: Session) -> None:
    snapshot_row(synced, LAST_MONDAY, "AI Labs", people=7)
    snapshot_row(synced, LAST_MONDAY - timedelta(days=1), "AI Labs", people=99)  # the Sunday
    snapshot_row(synced, LAST_MONDAY + timedelta(days=1), "AI Labs", people=98)  # the Tuesday
    synced.execute(
        text(
            "insert into metric_snapshot (day, scope_type, scope_id, metric, value) values "
            "(:m, 'all', 0, 'first_contacts_week', 3), (:s, 'all', 0, 'first_contacts_week', 50)"
        ),
        {"m": LAST_MONDAY, "s": LAST_MONDAY - timedelta(days=1)},
    )
    synced.commit()
    build(synced)
    body = body_of(synced)
    labs = next(r for r in body["scorecard"] if r["industry"] == "AI Labs")
    assert labs["last_week"]["people"] == 7 and labs["last_week_note"] is None
    other = next(r for r in body["scorecard"] if r["industry"] == "No industry")
    assert other["last_week"] is None  # no snapshot: null, never 0
    assert other["last_week_note"] == w.NO_SNAPSHOT_LAST_WEEK
    team = {n["key"]: n for n in body["team"]}
    assert team["first_contacts_week"]["last_week"] == 3
    assert team["first_contacts_week"]["last_week_note"] is None
    assert team["connect_rate_pct"]["last_week"] is None  # no snapshot of that metric
    assert team["connect_rate_pct"]["last_week_note"] == w.NO_SNAPSHOT_LAST_WEEK


def test_scorecard_delta_last_week_or_dash(
    synced: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    today = today_ist()
    snapshot_row(synced, today - timedelta(days=7), "AI Labs", people=5, deals=0)
    snapshot_row(synced, today - timedelta(days=8), "No industry", people=44)  # not 7 days back
    rows = {r["industry"]: r for r in client.get("/api/scorecard").json()["items"]}
    labs = rows["AI Labs"]
    assert labs["last_week"] == {
        "people": 5,
        "contacted": 2,
        "connect_pct": 40,
        "reply_pct": None,
        "meetings": 1,
        "deals": 0,  # a real zero stays 0
    }
    assert labs["last_week_note"] is None
    none = rows["No industry"]
    assert none["last_week"] is None and none["last_week_note"] == "no snapshot last week"
    # The words: "last week N", never a week number such as W41.
    assert w.fill(w.LAST_WEEK, n=5) == "last week 5"
    assert "W4" not in w.LAST_WEEK and "{n}" in w.LAST_WEEK


def test_snapshot_job_writes_the_scorecard_rows_and_upserts(
    synced: Session, client: TestClient
) -> None:
    register_all()
    assert registry.run_job("metrics_snapshot") == "ok"
    assert registry.run_job("metrics_snapshot") == "ok"  # twice in a day: one row each
    # The day the job stamps: before 06:00 IST a run belongs to the evening before (D118).
    synced.expire_all()
    stored = {
        r.industry: r
        for r in synced.execute(
            text("select * from scorecard_snapshot where day = :d"), {"d": snapshot_day(now_ist())}
        )
    }
    live = client.get("/api/scorecard").json()["items"]
    assert set(stored) == {r["industry"] for r in live}
    assert synced.execute(text("select count(*) from scorecard_snapshot")).scalar_one() == len(live)
    for r in live:  # the same numbers as the Scorecard
        s = stored[r["industry"]]
        assert (s.people, s.contacted, s.meetings, s.deals) == (
            r["people"],
            r["contacted"],
            r["meetings"],
            r["deals"],
        )
        assert (s.connect_pct is None) == (r["connect_pct"] is None)
        assert s.reply_pct is None  # no source until R2: null, never 0


def test_snapshot_scorecard_replaces_values_of_the_same_day(synced: Session) -> None:
    day = date(2026, 10, 18)
    snapshot_row(synced, day, "AI Labs", people=999)
    written = metrics.snapshot_scorecard(synced, day)
    synced.commit()
    people = synced.execute(
        text("select people from scorecard_snapshot where day = :d and industry = 'AI Labs'"),
        {"d": day},
    ).scalar_one()
    assert written >= 1 and people == 2  # replaced, not added


def test_snapshot_scorecard_drops_an_industry_gone_the_same_day(synced: Session) -> None:
    day = date(2026, 10, 18)
    snapshot_row(synced, day, "Gone Industry", people=5)
    metrics.snapshot_scorecard(synced, day)
    synced.commit()
    names = synced.execute(
        text("select industry from scorecard_snapshot where day = :d"), {"d": day}
    ).scalars()
    assert "Gone Industry" not in set(names)


def test_merge_last_week_never_turns_a_gap_into_zero() -> None:
    live = [{"industry": "A", "people": 3}, {"industry": "B", "people": 1}]
    snap = {"A": {"people": None, "contacted": 0}}
    merged = metrics.merge_last_week(live, snap)
    assert merged[0]["last_week"]["people"] is None and merged[0]["last_week"]["contacted"] == 0
    assert merged[1]["last_week"] is None and merged[1]["last_week_note"]
    assert live[0].get("last_week") is None  # the input rows are not changed


# ------------------------------------------------------------------ R4-P05: posts and times


def test_pack_1400_and_blocker_note_1600(synced: Session) -> None:
    block_goal(synced, 7, owner="Legal")
    build(synced, ist(MONDAY, 14))
    queued = posts(synced)
    summary, blocker = queued["pack_summary"], queued["blocker_note"]
    assert summary["due_at"] == ist(MONDAY, 14) and blocker["due_at"] == ist(MONDAY, 16)
    for post in (summary, blocker):
        assert post["state"] == "queued" and post["channel"] == "test"  # TEST_MODE=true
        assert post["mentions"] == [] and post["for_user"] is None  # counts for nobody (D132)
        assert post["item_type"] == "weekly_pack"
    lines = summary["body"].splitlines()
    assert "Open the pack" in lines[-1] and "http" in lines[-1]
    assert len(lines[:-1]) <= 12
    note = blocker["body"].splitlines()
    assert note[0] == "Late items that block goals:"
    assert 1 <= len(note[1:]) <= 5  # at most 5 lines, 7 items exist
    assert all("Legal" in line and "blocks G" in line for line in note[1:])
    assert "Blocking item 6" in note[1]  # the most late first (10 + n days)
    assert "long detail text" not in blocker["body"]  # the short name only


def test_blocker_delay_comes_from_the_setting(
    synced: Session, set_env: Callable[..., None]
) -> None:
    set_env(BLOCKER_NOTE_DELAY_MINUTES="30")
    block_goal(synced, 1)
    build(synced)
    assert posts(synced)["blocker_note"]["due_at"] == ist(MONDAY, 14, 30)


def test_live_mode_posts_to_sales(synced: Session, set_env: Callable[..., None]) -> None:
    set_env(TEST_MODE="false")
    block_goal(synced, 1)
    build(synced)
    assert {p["channel"] for p in posts(synced).values()} == {"sales"}


def test_no_blocker_post_when_nothing_blocks(synced: Session) -> None:
    block_goal(synced, 0)
    build(synced)
    assert set(posts(synced)) == {"pack_summary"}
    assert body_of(synced)["late"] == [] and body_of(synced)["late_total"] == 0
    assert "Late items that block goals: 0" in posts(synced)["pack_summary"]["body"]


def test_blocker_owner_gap_is_said_not_guessed(synced: Session) -> None:
    block_goal(synced, 1, owner=None)
    build(synced)
    assert w.PACK_NO_OWNER in posts(synced)["blocker_note"]["body"]


# ------------------------------------------------------------------ the body


def test_late_items_capped_with_owner_and_what_they_block(synced: Session) -> None:
    block_goal(synced, 8)
    build(synced)
    body = body_of(synced)
    assert body["late_total"] == 8 and len(body["late"]) == 5
    first = body["late"][0]
    assert first["owner"] == "Legal" and first["blocks"] and first["blocks"][0].startswith("G")
    assert first["item"].startswith("Blocking item 7")


def test_reminders_moved_to_the_pack_and_next_ten_tasks(synced: Session) -> None:
    sid = synced.execute(text("select id from users where short = 'sid'")).scalar_one()
    for n in range(12):  # 12 commitments due on the pack Monday: more than the cap of 10
        synced.execute(
            text("insert into commitment (owner, text, due, state) values (:o, :t, :d, 'open')"),
            {"o": sid, "t": f"Send note {n}", "d": MONDAY},
        )
    synced.execute(
        text(
            "insert into commitment (owner, text, due, state, in_pack, reminders_sent) "
            "values (:o, 'Share the deck', :d, 'open', true, 2)"
        ),
        {"o": sid, "d": MONDAY - timedelta(days=3)},
    )
    synced.commit()
    build(synced)
    body = body_of(synced)
    moved = body["packed_reminders"]
    assert [m["text"] for m in moved] == ["Share the deck"] and moved[0]["owner"] == "sid"
    tasks = {t["person"]: t for t in body["tasks"]}
    assert set(tasks) == {"sid", "vaishnavi", "kushal"}
    assert len(tasks["sid"]["items"]) == 10 and tasks["sid"]["total"] >= 12
    assert all(len(t["items"]) <= 10 for t in tasks.values())


def test_the_word_overdue_is_never_used(synced: Session) -> None:
    block_goal(synced, 3)
    build(synced)
    everything = json.dumps(body_of(synced)) + " ".join(p["body"] for p in posts(synced).values())
    assert "overdue" not in everything.lower()
    assert (
        "overdue"
        not in " ".join(
            v for k, v in vars(w).items() if k.startswith("PACK_") and isinstance(v, str)
        ).lower()
    )


def test_pack_strings_follow_the_ste_rules() -> None:
    from saley.qa.ste_lint import banned_words, check

    banned = banned_words()
    names = [k for k, v in vars(w).items() if k.startswith(("PACK_", "LAST_WEEK", "NO_SNAPSHOT"))]
    assert "PACK_BAD_WEEK" in names and "NO_SNAPSHOT_LAST_WEEK" in names
    for name in names:
        assert not check(name, getattr(w, name), banned), name


def test_no_good_sync_builds_no_pack_and_queues_nothing(db: Session) -> None:
    """A pack is stored once; built from stale or no data it would stay wrong all week."""
    with pytest.raises(pack.PackStale):
        build(db)
    db.rollback()
    assert db.execute(text("select count(*) from weekly_pack")).scalar_one() == 0
    assert db.execute(text("select count(*) from bot_post")).scalar_one() == 0


def test_an_old_sync_is_stale_by_the_db_clock(synced: Session) -> None:
    synced.execute(text("update job_runs set finished_at = now() - interval '3 hours'"))
    synced.commit()
    with pytest.raises(pack.PackStale):
        build(synced)
    synced.rollback()


def test_no_pack_before_monday_1400(synced: Session) -> None:
    early = build(synced, ist(MONDAY, 13, 59))
    assert early.built is False and early.note() == "pack for 2026-10-19 not before 14:00"
    assert synced.execute(text("select count(*) from weekly_pack")).scalar_one() == 0


def test_a_late_build_compares_the_same_weekday_and_posts_now(synced: Session) -> None:
    """A Tuesday catch-up reads last Tuesday, as /api/scorecard does (D118); it posts at once."""
    tuesday = MONDAY + timedelta(days=1)
    snapshot_row(synced, LAST_MONDAY, "AI Labs", people=7)
    snapshot_row(synced, LAST_MONDAY + timedelta(days=1), "AI Labs", people=98)
    block_goal(synced, 1)
    stats = build(synced, ist(tuesday, 10))
    assert stats.built is True
    labs = next(r for r in body_of(synced)["scorecard"] if r["industry"] == "AI Labs")
    assert labs["last_week"]["people"] == 98
    queued = posts(synced)
    assert queued["pack_summary"]["due_at"] == ist(tuesday, 10)
    assert queued["blocker_note"]["due_at"] == ist(tuesday, 12)


def test_a_pack_without_a_baseline_says_so(synced: Session) -> None:
    assert build(synced).note().endswith("· no last-week snapshot")


def test_a_late_snapshot_after_midnight_keeps_the_evening_day() -> None:
    from saley.worker.jobs import snapshot_day

    assert snapshot_day(ist(MONDAY, 23, 50)) == MONDAY
    assert snapshot_day(ist(MONDAY + timedelta(days=1), 0, 40)) == MONDAY  # misfire grace


def test_catchup_builds_a_missed_pack_once_and_is_idle_after(
    synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker down all Monday: the hourly catch-up builds on Tuesday, then stays idle."""
    from saley.worker.jobs import weekly_pack_catchup

    monkeypatch.setattr(pack, "now_ist", lambda: ist(MONDAY, 13))
    assert weekly_pack_catchup() == registry.QUIET_NOTE  # before Monday 14:00
    monkeypatch.setattr(pack, "now_ist", lambda: ist(MONDAY + timedelta(days=1), 9))
    assert weekly_pack_catchup().startswith("pack for 2026-10-19 built")
    assert weekly_pack_catchup() == registry.QUIET_NOTE
    assert synced.execute(text("select count(*) from weekly_pack")).scalar_one() == 1
