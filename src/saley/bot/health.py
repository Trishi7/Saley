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
    ]
    if not settings.dm_enabled:
        lines.append(bw.DMS_OFF)
    return "\n".join(lines)


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
    inserted = session.execute(
        text(
            "insert into bot_post (kind, shape, channel, mentions, body, rank, due_at, state, "
            "item_type, item_id, is_meeting, is_reconnect) values "
            "(:k, :k, 'test', '{}', :b, 0, :t, 'queued', :k, :d, false, false) "
            "on conflict do nothing"
        ),
        {"k": SHAPE, "b": health_text(session, now), "t": now, "d": day_key},
    )
    stats.queued = 1 if inserted.rowcount else 0
    return stats
