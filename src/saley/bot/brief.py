"""The daily brief: one DM per person at DAILY_BRIEF_TIME on weekdays (D128, docs/09).

Why: the brief is plain code, not an LLM text. The rules pick the lines (approvals waiting,
the next meeting, a reminder due today), the count of the rest and the dashboard link. The LLM
never picks a person. Nothing is sent while DM_ENABLED=false (the health line says "DMs off"),
and only people in DM_USERS get a brief; empty DM_USERS means nobody.

Rows are `queued` DMs. The caps (`bot_plan`) decide when each one goes: it waits for the
hours of the person and obeys the 4 a day and 2 h rules like any other post.
One brief per person and day: `item_id` is the day as YYYYMMDD. A person with nothing to say
gets no brief; an empty greeting would be noise. The head greets by first name (D139).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import cards, queue
from saley.bot import words as bw
from saley.bot.caps import WEEKEND_FROM
from saley.core import clock
from saley.core.logging import get_logger
from saley.core.settings import get_settings
from saley.web import words as w

log = get_logger("saley.bot.brief")

SHAPE = "daily_brief"
BRIEF_RANK = 10
MAX_LINES = 3
MEETING_DAYS = 7
REMINDER_LOOKBACK_DAYS = 1
LINE_MAX = 60


@dataclass
class BriefStats:
    queued: int = 0
    dms_off: bool = False

    def note(self) -> str:
        return bw.DMS_OFF if self.dms_off else f"{self.queued} briefs queued"


def dashboard_url() -> str:
    """The dashboard address: the origin of the sign-in redirect URL. No new setting."""
    parts = urlsplit(get_settings().google_oauth_redirect_url)
    return f"{parts.scheme}://{parts.netloc}/"


def _cut(value: str) -> str:
    return value if len(value) <= LINE_MAX else value[: LINE_MAX - 1].rstrip() + "…"


def _approvals(session: Session, role: str) -> str | None:
    if role not in ("approver", "admin"):
        return None
    n = session.execute(text("select count(*) from proposal where state = 'open'")).scalar_one()
    if n == 0:
        return None
    return w.fill_count(bw.BRIEF_APPROVALS_ONE, bw.BRIEF_APPROVALS, n)


def _meetings(session: Session, short: str, today: datetime) -> list[str]:
    rows = session.execute(
        text(
            "select c.name, a.name as company, c.meeting_date from contact c "
            "left join account a on a.id = c.account_id "
            "where c.owner = :s and c.removed_at is null "
            "and c.meeting_date between :d0 and :d1 order by c.meeting_date, c.id"
        ),
        {"s": short, "d0": today.date(), "d1": today.date() + timedelta(days=MEETING_DAYS)},
    ).all()
    lines = []
    for r in rows:
        template = bw.BRIEF_MEETING if r.company else bw.BRIEF_MEETING_NO_COMPANY
        lines.append(
            _cut(
                w.fill(
                    template,
                    who=r.name,
                    company=r.company or "",
                    day=clock.day_label(r.meeting_date),
                )
            )
        )
    return lines


def _reminders(session: Session, user_id: int, now: datetime) -> list[str]:
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = session.execute(
        text(
            "select text from reminder where for_user = :u and state = 'pending' "
            "and fire_at >= :lo and fire_at < :hi order by fire_at, id"
        ),
        {
            "u": user_id,
            "lo": midnight - timedelta(days=REMINDER_LOOKBACK_DAYS),
            "hi": midnight + timedelta(days=1),
        },
    ).all()
    return [_cut(r.text) for r in rows]


def first_name(session: Session, user_id: int, short: str) -> str:
    """The first word of users.name; the capitalized short name when the name is empty (D139)."""
    name = session.execute(text("select name from users where id = :u"), {"u": user_id}).scalar()
    words = (name or "").split()
    return words[0] if words else short.capitalize()


def brief_parts(
    session: Session, user_id: int, short: str, role: str, now: datetime
) -> tuple[list[str], int]:
    """The brief lines (approvals, the next meeting, a reminder) and the count of the rest."""
    approvals = _approvals(session, role)
    meetings = _meetings(session, short, now)
    reminders = _reminders(session, user_id, now)
    lines = [*([approvals] if approvals else []), *meetings[:1], *reminders[:1]]
    return lines, max(len(meetings) - 1, 0) + max(len(reminders) - 1, 0)


def build_brief(session: Session, user_id: int, short: str, role: str, now: datetime) -> str | None:
    """The brief text for one person, or None when there is nothing to say."""
    lines, rest = brief_parts(session, user_id, short, role, now)
    if not lines:
        return None
    name = first_name(session, user_id, short)
    shown = min(len(lines), MAX_LINES)
    head = w.fill_count(bw.BRIEF_HEAD_ONE, bw.BRIEF_HEAD, shown, name=name)
    out = [head, *lines[:MAX_LINES]]
    if rest:
        out.append(w.fill(bw.BRIEF_REST, n=rest))
    out.append(w.fill(bw.BRIEF_LINK, url=dashboard_url()))
    return "\n".join(out)


def queue_briefs(session: Session) -> BriefStats:
    """Queue one DM brief for each person in DM_USERS. The caller commits."""
    settings = get_settings()
    stats = BriefStats()
    if not settings.dm_enabled:
        stats.dms_off = True
        return stats
    now = clock.now_ist()
    if now.weekday() >= WEEKEND_FROM or now.time() < settings.brief_time:
        return stats
    day_key = int(now.strftime("%Y%m%d"))
    for short in settings.dm_user_shorts:
        user = session.execute(
            text("select id, role from users where short = :s"), {"s": short}
        ).first()
        if user is None:
            log.warning("brief_user_unknown", person=short)
            continue
        done = session.execute(
            text("select 1 from bot_post where item_type = :t and item_id = :d and for_user = :u"),
            {"t": SHAPE, "d": day_key, "u": user.id},
        ).first()
        body = None if done else build_brief(session, user.id, short, user.role, now)
        if body is None:
            continue
        lines, rest = brief_parts(session, user.id, short, user.role, now)
        data = {
            "name": first_name(session, user.id, short),
            "day": clock.day_label(now),
            "lines": lines,
            "more": rest,
        }
        payload = cards.render("M1", data, f"m1-{short}-{day_key}")
        # uq_bot_post_once (0021) turns a second insert of the day into a no-op, even in a race.
        inserted = queue.insert_card(
            session,
            payload,
            due_at=now,
            channel="dm",
            for_user=user.id,
            item=(SHAPE, day_key),
            shape=SHAPE,
            body=body,
        )
        stats.queued += 1 if inserted else 0
    return stats
