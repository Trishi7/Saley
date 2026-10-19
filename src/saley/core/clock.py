"""Time in IST for every rule and label.

Why: the team works in India time (SALEY_TZ). Week starts and "today" must match the
sheet users' day, not UTC. One place to read the clock also makes tests deterministic.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from saley.core.settings import get_settings


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().saley_tz)


def now_ist() -> datetime:
    """Aware datetime in the team time zone."""
    return datetime.now(tz())


def today_ist() -> date:
    return now_ist().date()


def week_start(day: date) -> date:
    """Monday of the week that holds `day`. Weeks run Monday to Sunday."""
    return day - timedelta(days=day.weekday())


def day_label(day: date | datetime | None) -> str:
    """'Fri 17 Oct' style label used on the dashboard (docs/08 shared rules)."""
    if day is None:
        return "—"
    return f"{day:%a} {day.day} {day:%b}"


def hhmm(moment: datetime | None) -> str:
    """'14:10' in IST, or an em dash when the moment is unknown."""
    if moment is None:
        return "—"
    return moment.astimezone(tz()).strftime("%H:%M")
