"""The weekly pack: Monday 14:00 IST, plain code, once a week (D117, D118, D130, D132, D134).

Why: the team wants one calm weekly look, not many pings. The pack holds the team numbers with
"last week N", the Scorecard per industry with deltas, the late items that block goals, the
reminders the politeness budget moved here (C05) and the next tasks per person. No LLM writes a
word of it; rules and templates from `web/words.py` do (CLAUDE.md).

Rules:
- One pack per week. `weekly_pack.week_start` is unique, so a second run, a retry after a
  misfire or two workers build nothing twice and queue nothing twice.
- "last week N" is the snapshot of the day 7 days before (D118). At 14:00 on a Monday that is
  last Monday, so both sides count the week to date. A missing snapshot is None, never 0.
- The Scorecard rows are the rows of `/api/scorecard` (`metrics.live_scorecard` and
  `metrics.merge_last_week` are the shared code), so the two never differ (R4-P01).
- Two `bot_post` rows are queued here and sent by the bot later (ticket 5): the summary, due at
  14:00, and the blocker note, due `BLOCKER_NOTE_DELAY_MINUTES` later (D130). Both mention nobody,
  so they count for nobody in the caps (D132). With no late items there is no blocker note.
- The channel is `test` while TEST_MODE=true, else `sales`. Nothing else differs (CLAUDE.md).
- A pack is stored once and never rebuilt, so it is never built from stale data: with no good
  sheet sync in SYNC_MAX_AGE the build raises `PackStale` and the job run fails (it retries on
  the next run). It is never built before Monday 14:00, and a late build posts at once, not
  with a due time in the past.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from saley.core.clock import day_label, now_ist, tz, week_start
from saley.core.logging import get_logger
from saley.core.models import BotPost, WeeklyPack
from saley.core.settings import get_settings
from saley.intel import metrics
from saley.intel import tasks as rules
from saley.web import mine
from saley.web import words as w

log = get_logger("saley.intel.pack")

PACK_WEEKDAY = "mon"  # D134
PACK_HOUR = 14  # 14:00 IST (D7, D21, D134)
LATE_CAP = 5  # D130: the blocker note has at most 5 lines
TASKS_PER_PERSON = 10
SUMMARY_MAX_LINES = 11  # docs/09: 12 lines in all, the link is the twelfth
SYNC_MAX_AGE_MINUTES = 120  # same limit as metrics_snapshot (SNAPSHOT_MAX_AGE)
PACKED_CAP = 50  # the page shows the total and this many
ITEM_NAME_MAX = 40
TEAM_METRICS = (
    ("first_contacts_week", w.PACK_M_CONTACTED),
    ("orgs_touched_week", w.PACK_M_ORGS),
    ("connect_rate_pct", w.PACK_M_CONNECT),
    ("meetings_per_100", w.PACK_M_MEETINGS),
)


class PackStale(RuntimeError):
    """No good sheet sync in SYNC_MAX_AGE_MINUTES: a pack built now would be stored stale."""


@dataclass
class PackStats:
    built: bool
    week: date
    posts: int = 0
    late: int = 0
    not_yet: bool = False
    no_baseline: bool = False

    def note(self) -> str:
        if self.not_yet:
            return f"pack for {self.week} not before 14:00"
        if not self.built:
            return f"pack for {self.week} exists"
        note = f"pack for {self.week} built · {self.posts} posts queued · {self.late} late"
        return note + (" · no last-week snapshot" if self.no_baseline else "")


def short_item(item: str) -> str:
    """The part of a deliverable name before ' - ', cut to a short line."""
    head = item.split(" - ", 1)[0].strip()
    return head if len(head) <= ITEM_NAME_MAX else head[: ITEM_NAME_MAX - 1].rstrip() + "…"


def _show(value: int | float | None) -> str:
    return w.PACK_NO_VALUE if value is None else f"{value:g}"


def _last_week_label(value: int | float | None) -> str:
    return w.NO_SNAPSHOT_LAST_WEEK if value is None else w.fill(w.LAST_WEEK, n=_show(value))


# ------------------------------------------------------------------ the body


def _team_numbers(session: Session, today: date, last_day: date) -> list[dict[str, Any]]:
    now = {m.metric: m.value for m in metrics.team_metrics(session, today)}
    before = {
        r.metric: r.value
        for r in session.execute(
            text(
                "select metric, value from metric_snapshot "
                "where day = :d and scope_type = 'all' and scope_id = 0"
            ),
            {"d": last_day},
        )
    }
    if not before:
        log.warning("pack_no_last_week_snapshot", day=str(last_day), scope="team")
    out = []
    for key, label in TEAM_METRICS:
        last = metrics.plain_number(before.get(key))
        out.append(
            {
                "key": key,
                "label": label,
                "value": metrics.plain_number(now.get(key)),
                "last_week": last,
                "last_week_note": w.NO_SNAPSHOT_LAST_WEEK if last is None else None,
            }
        )
    return out


def _scorecard(session: Session, last_day: date) -> list[dict[str, Any]]:
    snapshots = {
        r["industry"]: dict(r)
        for r in session.execute(
            text(
                "select industry, people, contacted, connect_pct, reply_pct, meetings, deals "
                "from scorecard_snapshot where day = :d"
            ),
            {"d": last_day},
        ).mappings()
    }
    if not snapshots:
        log.warning("pack_no_last_week_snapshot", day=str(last_day), scope="scorecard")
    live = metrics.live_scorecard(session.connection())
    # Reply % has no source until R2 (same note as /api/scorecard).
    return metrics.merge_last_week([{**r, "reply_note": w.REPLY_NOTE} for r in live], snapshots)


def _late_items(session: Session) -> tuple[int, list[dict[str, Any]]]:
    total = session.execute(
        text("select count(*) from v_deliverable_late where cardinality(blocks_goals) > 0")
    ).scalar_one()
    found = session.execute(
        text(
            "select item, owner, due, late_by_days, blocks_goals from v_deliverable_late "
            "where cardinality(blocks_goals) > 0 "
            "order by late_by_days desc, item, id limit :n"
        ),
        {"n": LATE_CAP},
    ).mappings()
    items = [
        {
            "item": short_item(r["item"]),
            "owner": r["owner"],
            "blocks": list(r["blocks_goals"]),
            "due": r["due"].isoformat() if r["due"] else None,
            "late_by_days": r["late_by_days"],
        }
        for r in found
    ]
    return int(total), items


def _packed_reminders(session: Session) -> tuple[int, list[dict[str, Any]]]:
    """Open commitments whose reminders are used up (`in_pack`): the pack carries them now."""
    total = session.execute(
        text("select count(*) from commitment where state = 'open' and in_pack")
    ).scalar_one()
    found = session.execute(
        text(
            "select u.short as owner, c.text, c.due from commitment c "
            "join users u on u.id = c.owner where c.state = 'open' and c.in_pack "
            "order by c.due nulls last, c.id limit :n"
        ),
        {"n": PACKED_CAP},
    ).mappings()
    items = [
        {"owner": r["owner"], "text": r["text"], "due": r["due"].isoformat() if r["due"] else None}
        for r in found
    ]
    return int(total), items


def _next_tasks(session: Session, today: date) -> list[dict[str, Any]]:
    """The first tasks of each person, from the same rules as the Today page (D31)."""
    email_write = get_settings().email_write_allowed
    conn = session.connection()
    out = []
    for person in rules.TEAM:
        found = mine.my_tasks(conn, person, today, email_write=email_write)
        items = [
            {"title": t["title"], "due_label": t["due_label"], "kind": t["kind"]}
            for t in found["items"][:TASKS_PER_PERSON]
            if not t["done"]  # a task already ticked is not "next"
        ]
        out.append({"person": person, "total": found["total"], "items": items})
    return out


def build_body(session: Session, week: date, today: date) -> dict[str, Any]:
    # "last week" = the same weekday 7 days back, as /api/scorecard computes it (D118). A
    # catch-up run on a Tuesday compares Tuesday with Tuesday, never with the Monday snapshot.
    last_day = today - timedelta(days=metrics.LAST_WEEK_DAYS)
    late_total, late = _late_items(session)
    packed_total, packed = _packed_reminders(session)
    return {
        "week_start": week.isoformat(),
        "last_week_day": last_day.isoformat(),
        "as_of": today.isoformat(),
        "team": _team_numbers(session, today, last_day),
        "scorecard": _scorecard(session, last_day),
        "late_total": late_total,
        "late": late,
        "packed_total": packed_total,
        "packed_reminders": packed,
        "tasks": _next_tasks(session, today),
    }


# ------------------------------------------------------------------ the two posts


def dashboard_link() -> str:
    """The pack page address. It uses the host of the sign-in redirect URL; no new setting."""
    parts = urlsplit(get_settings().google_oauth_redirect_url)
    return f"{parts.scheme}://{parts.netloc}/#pack"


def summary_text(body: dict[str, Any], week: date) -> str:
    """At most 12 lines, then the link (docs/09 weekly pack summary)."""
    lines = [w.fill(w.PACK_TITLE, day=day_label(week))]
    for n in body["team"]:
        lines.append(
            w.fill(
                w.PACK_TEAM_LINE,
                label=n["label"],
                value=_show(n["value"]),
                last=_last_week_label(n["last_week"]),
            )
        )
    lines.append(w.fill(w.PACK_LATE_COUNT, n=body["late_total"]))
    lines.append(w.fill(w.PACK_PACKED_COUNT, n=body["packed_total"]))
    counts = [f"{t['person'].capitalize()} {len(t['items'])}" for t in body["tasks"] if t["items"]]
    lines.append(w.fill(w.PACK_TASKS_LINE, who=" · ".join(counts)) if counts else w.PACK_TASKS_NONE)
    lines.append(w.fill(w.PACK_SCORE_LINE, n=len(body["scorecard"])))
    lines = lines[:SUMMARY_MAX_LINES]
    lines.append(w.fill(w.PACK_LINK, url=dashboard_link()))
    return "\n".join(lines)


def blocker_text(late: list[dict[str, Any]]) -> str:
    """The head and at most 5 lines, each with owner and what it blocks (D130)."""
    lines = [w.BLOCKER_NOTE_HEAD]
    for r in late[:LATE_CAP]:
        lines.append(
            w.fill(
                w.BLOCKER_NOTE_LINE,
                item=r["item"],
                owner=r["owner"] or w.PACK_NO_OWNER,
                goals=", ".join(r["blocks"]),
            )
        )
    return "\n".join(lines)


def _queue(
    session: Session, pack_id: int, shape: str, body: str, due_at: datetime, channel: str
) -> None:
    """One queued row. No mentions and no `for_user`: the post counts for nobody (D132)."""
    session.execute(
        insert(BotPost).values(
            kind=shape,
            shape=shape,
            channel=channel,
            for_user=None,
            mentions=[],
            body=body,
            rank=0,
            due_at=due_at,
            state="queued",
            item_type="weekly_pack",
            item_id=pack_id,
            is_meeting=False,
            is_reconnect=False,
        )
    )


def _refuse_stale(session: Session) -> None:
    """The DB clock decides the age, so a mocked build time cannot hide old data."""
    fresh = session.scalar(
        text(
            "select max(finished_at) > now() - make_interval(mins => :m) from job_runs "
            "where job = 'sheet_sync' and status = 'ok'"
        ),
        {"m": SYNC_MAX_AGE_MINUTES},
    )
    if not fresh:
        raise PackStale(f"no good sheet sync in {SYNC_MAX_AGE_MINUTES} min; pack not built")


def build_weekly_pack(session: Session, now: datetime | None = None) -> PackStats:
    """Build this week's pack and queue its posts. A pack that exists is left alone."""
    settings = get_settings()
    moment = (now or now_ist()).astimezone(tz())
    today = moment.date()
    week = week_start(today)
    due = datetime.combine(week, time(PACK_HOUR), tzinfo=tz())
    if moment < due:
        return PackStats(False, week, not_yet=True)
    exists = session.execute(text("select 1 from weekly_pack where week_start = :w"), {"w": week})
    if exists.first():
        return PackStats(False, week)
    _refuse_stale(session)
    body = build_body(session, week, today)
    pack_id = session.execute(
        insert(WeeklyPack)
        .values(week_start=week, body=body)
        .on_conflict_do_nothing(index_elements=["week_start"])
        .returning(WeeklyPack.id)
    ).scalar()
    if pack_id is None:  # another worker built it between the check and the insert
        return PackStats(False, week)
    channel = "test" if settings.test_mode else "sales"
    post_at = max(due, moment)  # a late build posts now, not at a time already gone
    _queue(session, pack_id, "pack_summary", summary_text(body, week), post_at, channel)
    queued = 1
    if body["late"]:
        later = post_at + timedelta(minutes=settings.blocker_note_delay_minutes)
        _queue(session, pack_id, "blocker_note", blocker_text(body["late"]), later, channel)
        queued += 1
    no_baseline = all(n["last_week"] is None for n in body["team"])
    log.info("weekly_pack_built", week=str(week), posts=queued, late=body["late_total"])
    return PackStats(True, week, posts=queued, late=body["late_total"], no_baseline=no_baseline)
