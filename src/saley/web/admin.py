"""Admin routes: Run news now (R5 T8, D150, D151).

Why: on the laptop no news job ran, so Signals said "No news job has run yet." An admin can ask
for one edition. The click calls `saley_request_news_run` (SECURITY DEFINER, admin only, one open
request, a 10-minute cooldown). The worker job `news_requests` runs it under the `news_edition`
lock. The web role only calls the function and reads `v_news_run_request`.
Like every act: session, header `X-Saley-Act: 1` and a same-origin request.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text

from saley.core.logging import get_logger
from saley.web import words as w
from saley.web.act import ActDb, ActHeader, actor_short, call_function
from saley.web.app import Db, SignedIn, current_user
from saley.web.schemas import Out

log = get_logger("saley.web.admin")
router = APIRouter()

SECONDS_PER_MINUTE = 60


class NewsRunOut(Out):
    ok: bool
    request_id: int
    label: str


class NewsRunRow(Out):
    id: int
    requested_by: str
    requested_at: datetime
    state: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    note: str | None = None


class NewsRunState(Out):
    latest: NewsRunRow | None
    open: bool
    label: str | None = None


def require_admin_session(user: Annotated[SignedIn, Depends(current_user)]) -> SignedIn:
    """403 with the news line for any signed-in person who is not an admin (D150)."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail=w.ERR_NEWS_ADMIN)
    return user


Admin = Annotated[SignedIn, Depends(require_admin_session)]


def _refuse(result: dict[str, Any]) -> None:
    """Map a function code to a status and a fixed line: 403, 409 or 429."""
    code = result.get("code")
    if code == "not_allowed":
        raise HTTPException(status_code=403, detail=w.ERR_NEWS_ADMIN)
    if code == "cooldown":
        minutes = max(1, math.ceil(int(result.get("wait_seconds") or 0) / SECONDS_PER_MINUTE))
        raise HTTPException(
            status_code=429,
            detail=w.fill(w.ERR_NEWS_COOLDOWN, minutes=minutes),
            headers={"Retry-After": str(int(result.get("wait_seconds") or 0))},
        )
    raise HTTPException(status_code=409, detail=w.ERR_NEWS_BUSY)  # busy, or a code we do not know


@router.post("/api/admin/news/run", response_model=NewsRunOut)
def run_news_now(request: Request, user: Admin, _h: ActHeader, conn: ActDb) -> dict[str, Any]:
    """Queue one news edition. Admin only; one open request at a time; 10 min cooldown."""
    result = call_function(
        conn, "select saley_request_news_run(:u)", {"u": actor_short(request, user)}
    )
    if not result.get("ok"):
        log.info("news_run_refused", user=user.short, code=result.get("code"))
        _refuse(result)
    log.info("news_run_queued", user=user.short, request=result["request_id"])
    return {"ok": True, "request_id": result["request_id"], "label": w.NEWS_RUN_QUEUED}


@router.get("/api/admin/news/run", response_model=NewsRunState)
def news_run_state(user: Admin, conn: Db) -> dict[str, Any]:
    """The newest request, for Sources and health. `latest` is null before the first click."""
    row = (
        conn.execute(
            text(
                "select id, requested_by, requested_at, state, started_at, finished_at, note "
                "from v_news_run_request order by id desc limit 1"
            )
        )
        .mappings()
        .first()
    )
    return {
        "latest": dict(row) if row else None,
        "open": bool(row and row["state"] in ("queued", "running")),
        "label": w.NEWS_RUN_IDLE if row is None else None,
    }
