"""GET /api/today: the Today page (R1, R5 T3).

Why a router of its own: app.py reached the 800-line ceiling. The route and its URL are
unchanged. `viewer` is the signed-in person (D50); `person` is whose list is shown. Ticked rows
with an Undo go to the viewer's own list only.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import text

from saley.core.clock import now_ist, today_ist
from saley.core.settings import get_settings
from saley.web import mine, queries, schemas, signals, today_model
from saley.web import words as w
from saley.web.act import actor_short
from saley.web.app import TILE_TABS, Db, User, greeting, mail_state, meta, short_label, tiles
from saley.web.auth import provider

router = APIRouter()

TASKS_OFFSET_MAX = 1000  # R5 T3: the page of My tasks cannot start beyond this


@router.get("/api/today", response_model=schemas.TodayOut)
def today(
    request: Request,
    user: User,
    conn: Db,
    user_short: Annotated[
        str | None, Query(alias="user", max_length=20, pattern=r"^[a-z]+$")
    ] = None,
    tasks_offset: Annotated[int, Query(ge=0, le=TASKS_OFFSET_MAX)] = 0,
) -> dict[str, Any]:
    now = now_ist()
    name = None
    if provider(get_settings()) != "mock":
        user_short = None  # D206, D207: with Google "Your tasks" is the signed-in person
    if user_short:
        name = conn.execute(
            text("select name from v_team where short = :s"), {"s": user_short}
        ).scalar()
    m = meta(conn)
    empty = w.fill(w.EMPTY_LIST, t=m["last_computed_label"])
    # D50 (validated by validate_as_header): dev only until R4 sign-in
    viewer = actor_short(request, user)
    person = user_short or viewer
    numbers = queries.today_numbers(conn)
    synced = m["last_computed"] is not None
    unread = queries.tab_states(conn)[1]
    replies_off = mail_state(conn)["state"] == "not_configured"
    look_rows = signals.look_rows(conn)  # one read for the list and the cards
    look = signals.needs_look_block(
        conn, empty, w.REPLIES_NOT_CONFIGURED if replies_off else None, rows=look_rows
    )
    suggests = signals.saley_suggests_block(conn, empty, viewer)
    return {
        "greeting": f"{greeting(now)}, {name}" if name else greeting(now),
        "date_label": f"{now:%A} {now.day} {now:%B}",
        "tiles": tiles(numbers, synced, unread),
        "funnel": today_model.funnel(conn, synced and "poc" not in unread),
        "blockers_line": today_model.blockers_line(
            numbers["late_blockers"] if synced and not TILE_TABS["late"] & unread else None
        ),
        "my_tasks": mine.my_tasks(
            conn,
            person,
            today_ist(),
            email_write=get_settings().email_write_allowed,
            offset=tasks_offset,
            viewer=viewer,
        ),
        "needs_look": {
            **look,
            "cards": today_model.look_cards(conn, short_label, viewer, rows=look_rows),
            "soon": "mail_replies" if replies_off else None,
        },
        "saley_suggests": {**suggests, "items": today_model.with_lines(suggests["items"])},
        **m,
    }
