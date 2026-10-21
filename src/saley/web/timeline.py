"""The touch timeline strip of Accounts and People (R5 T6, F-R5-10, F-R5-11).

Why a module of its own: both drawers show the same strip, and app.py is near the line ceiling.
One row per month for the last 12 months, empty months included, so the strip never jumps.
Months follow the IST calendar (`today_ist`). A touch has only a sheet date, so no zone shift
can move it into another month.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Connection

from saley.core.clock import today_ist
from saley.web import queries

MONTHS = 12
MAX_TOUCHES = 200  # the whole strip, newest first


def first_of_month(day: date, back: int = 0) -> date:
    """The first day of the month `back` months before `day`."""
    index = day.year * 12 + day.month - 1 - back
    return date(index // 12, index % 12 + 1, 1)


def window_start(today: date) -> date:
    return first_of_month(today, MONTHS - 1)


def build(touches: list[dict[str, Any]], today: date | None = None) -> list[dict[str, Any]]:
    """12 month rows, newest month first. Each touch is {day, type, person}.

    Touches outside the window or without a date are dropped. At most MAX_TOUCHES are kept,
    the newest ones.
    """
    today = today or today_ist()
    starts = [first_of_month(today, back) for back in range(MONTHS)]
    rows = {
        s.strftime("%Y-%m"): {"month": s.strftime("%Y-%m"), "label": f"{s:%b %Y}", "touches": []}
        for s in starts
    }
    dated = [t for t in touches if t.get("day") and starts[-1] <= t["day"] <= today]
    dated.sort(key=lambda t: t["day"], reverse=True)
    for touch in dated[:MAX_TOUCHES]:
        rows[touch["day"].strftime("%Y-%m")]["touches"].append(touch)
    return list(rows.values())


def for_person(conn: Connection, contact_id: int) -> list[dict[str, Any]]:
    today = today_ist()
    found = queries.rows(
        conn,
        "select t.date as day, t.type, c.name as person from v_touch t "
        "join v_contact_stage c on c.id = t.contact_id "
        "where t.contact_id = :id and t.date >= :since and t.date <= :today "
        "order by t.date desc, t.id desc limit :cap",
        id=contact_id,
        since=window_start(today),
        today=today,
        cap=MAX_TOUCHES,
    )
    return build(found, today)


def for_account(conn: Connection, account_id: int) -> list[dict[str, Any]]:
    today = today_ist()
    found = queries.rows(
        conn,
        "select t.date as day, t.type, c.name as person from v_touch t "
        "join v_contact_stage c on c.id = t.contact_id "
        "where c.account_id = :id and t.date >= :since and t.date <= :today "
        "order by t.date desc, t.id desc limit :cap",
        id=account_id,
        since=window_start(today),
        today=today,
        cap=MAX_TOUCHES,
    )
    return build(found, today)
