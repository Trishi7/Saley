"""The health line: one post a day to the test channel (D133).

Why: until the server exists the team reads health in the test channel. Plain numbers only:
jobs ok and failed in the last 24 h, LLM calls against the daily cap, egress errors, and
"DMs off" while DM_ENABLED=false (D128). It mentions nobody, so it counts for nobody in the
caps. It is skipped on weekends: the weekend is silent, and a line that waited until Monday
would describe the wrong days.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import cards, queue
from saley.bot import words as bw
from saley.bot.caps import WEEKEND_FROM
from saley.bot.queue import count_waiting
from saley.core import clock
from saley.core.settings import get_settings
from saley.web import words as w

SHAPE = "health_line"


@dataclass
class HealthStats:
    queued: int = 0

    def note(self) -> str:
        return f"{self.queued} health line queued"


skipped_sql = (
    "select count(*) from job_runs where job = 'cards_plan' "
    "and started_at >= now() - interval '24 hours' and note like '%skipped%'"
)


def skipped_producers(session: Session) -> int:
    """cards_plan runs of the last 24 h in which a producer failed and was skipped."""
    return int(session.execute(text(skipped_sql)).scalar_one())


def health_text(session: Session, now: datetime) -> str:
    """The lines of the health post."""
    settings = get_settings()
    jobs = {
        status: n
        for status, n in session.execute(
            text(
                "select status, count(*) from job_runs "
                "where started_at >= now() - interval '24 hours' "
                "and status in ('ok', 'failed') group by status"
            )
        )
    }
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    calls = session.execute(
        text("select count(*) from llm_calls where at >= :t and not cached"), {"t": midnight}
    ).scalar_one()
    errors = session.execute(
        text("select coalesce(sum(errors), 0) from egress_log where day = :d"),
        {"d": now.date()},
    ).scalar_one()
    dropped = session.execute(
        text("select count(*) from bot_post where state = 'dropped' and created_at >= :t"),
        {"t": now - timedelta(hours=24)},
    ).scalar_one()
    lines = [
        w.fill(bw.HEALTH_HEAD, day=clock.day_label(now)),
        w.fill(bw.HEALTH_JOBS, ok=jobs.get("ok", 0), failed=jobs.get("failed", 0)),
        w.fill(bw.HEALTH_LLM, n=calls, cap=settings.llm_daily_call_cap),
        w.fill(bw.HEALTH_EGRESS, n=errors),
        w.fill(bw.HEALTH_POSTS, dropped=dropped, waiting=count_waiting(session, now)),
        w.fill(bw.HEALTH_SKIPPED, n=skipped_producers(session)),
    ]
    if not settings.dm_enabled:
        lines.append(bw.DMS_OFF)
    return "\n".join(lines)


# docs/17 §10: the same row as Sources & health · Discord and `saley bot status`.
discord_sql = (
    "select sent_today, held, failed_today, presses_ok, presses_already, presses_refused, "
    "last_sync from v_bot_health"
)


def health_data(session: Session, now: datetime) -> dict[str, object]:
    """The numbers of the M15 card (docs/09 M15): the health line plus the Discord block."""
    settings = get_settings()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    jobs = {
        status: n
        for status, n in session.execute(
            text(
                "select status, count(*) from job_runs "
                "where started_at >= now() - interval '24 hours' "
                "and status in ('ok', 'failed') group by status"
            )
        )
    }
    bot = session.execute(text(discord_sql)).one()
    return {
        "day": clock.day_label(now),
        "jobs_ok": jobs.get("ok", 0),
        "jobs_failed": jobs.get("failed", 0),
        "llm": session.execute(
            text("select count(*) from llm_calls where at >= :t and not cached"), {"t": midnight}
        ).scalar_one(),
        "cap": settings.llm_daily_call_cap,
        "egress": session.execute(
            text("select coalesce(sum(errors), 0) from egress_log where day = :d"),
            {"d": now.date()},
        ).scalar_one(),
        "sent": bot.sent_today,
        "held": bot.held,
        "failed": bot.failed_today,
        "skipped": skipped_producers(session),
        "presses": {
            "ok": bot.presses_ok,
            "already": bot.presses_already,
            "refused": bot.presses_refused,
        },
        "listen": clock.hhmm(bot.last_sync) if bot.last_sync else "—",
        "dms_off": not settings.dm_enabled,
    }


def queue_health_line(session: Session) -> HealthStats:
    """Queue today's health post for the test channel. One a day, weekdays only."""
    now = clock.now_ist()
    stats = HealthStats()
    if now.weekday() >= WEEKEND_FROM:
        return stats
    day_key = int(now.strftime("%Y%m%d"))
    done = session.execute(
        text("select 1 from bot_post where item_type = :t and item_id = :d"),
        {"t": SHAPE, "d": day_key},
    ).first()
    if done:
        return stats
    payload = cards.render("M15", health_data(session, now), f"m15-{day_key}")
    inserted = queue.insert_card(
        session,
        payload,
        due_at=now,
        channel="test",
        item=(SHAPE, day_key),
        shape=SHAPE,
        body=health_text(session, now),
    )
    stats.queued = 1 if inserted else 0
    return stats
