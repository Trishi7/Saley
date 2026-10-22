"""GET /api/pack: the weekly pack of one week (R4, D117, D134).

Why: the pack page (ticket 10) and the test-channel post show the same stored body. This route
only reads `v_weekly_pack` with the read-only role; the worker builds the pack. `week` must be a
Monday: the pack key is the Monday, and a loose date would hide a typo behind a 404.

No `week` and no pack at all (a fresh server) is a normal state, not an error: the answer is 200
with `empty: true` and null pack fields, so the browser logs no failed request (R5 T17, D229).
A `week` with no pack stays 404; the page asks only for the weeks in `prev_week` / `next_week`.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from saley.web import queries, schemas
from saley.web import words as w
from saley.web.app import Db, User, meta

router = APIRouter()

MONDAY = 0  # date.weekday()
NO_PACK: dict[str, Any] = {
    "week_start": None,
    "built_at": None,
    "body": None,
    "prev_week": None,
    "next_week": None,
    "empty": True,
}


@router.get("/api/pack", response_model=schemas.PackOut)
def pack(user: User, conn: Db, week: Annotated[date | None, Query()] = None) -> dict[str, Any]:
    """The pack for `week` (a Monday), or the newest pack when `week` is not given."""
    if week is not None and week.weekday() != MONDAY:
        raise HTTPException(status_code=422, detail=w.PACK_BAD_WEEK)
    found = queries.weekly_pack(conn, week)
    if found is None and week is None:
        return {**NO_PACK, **meta(conn)}
    if found is None:
        raise HTTPException(status_code=404, detail=w.PACK_NOT_FOUND)
    near = queries.pack_neighbours(conn, found["week_start"])
    return {**found, **near, "empty": False, **meta(conn)}
