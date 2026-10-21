"""POST /api/ask: the dashboard Ask card (R4 ticket 6, D131).

Why: Discord and the dashboard give the same answer, from the same engine (`saley.bot.answer`),
so the text, the 5-item page and the per-person seen cannot drift apart. The route accepts a
question only. It changes two things, both through SECURITY DEFINER functions: `user_seen`
(`saley_mark_seen`) and `bot_exchange` (`saley_ask_log`). The web role has no table grant, so
it cannot do more. Same guards as `/api/seen`: a session, the X-Saley-Act header (D24), the
same-origin rule and the body limit. Errors are fixed text.

The engine logs the exchange first and marks the items seen second. Each call commits on its
own here, so a failure in either step ends in a fixed-text 503; a failed log marks nothing.

Who may ask (D141): a signed-in user already passed WEB_ALLOWED_EMAILS, which is the team. So
this route does not repeat the Discord team check (`answer.discord_reply`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from saley.bot import answer
from saley.bot import words as bw
from saley.core.logging import get_logger
from saley.web import schemas
from saley.web.act import ActDb, ActHeader, actor_short, call_function
from saley.web.app import Db, User

router = APIRouter()
log = get_logger("saley.web.ask")


@router.post("/api/ask", response_model=schemas.AskOut)
def ask(
    body: schemas.AskIn, request: Request, user: User, _h: ActHeader, read: Db, act: ActDb
) -> schemas.AskOut:
    """Answer one question for the signed-in person. `surface` is always `dashboard`."""
    who = actor_short(request, user)

    def write(sql: str, params: dict[str, Any]) -> Any:
        return call_function(act, sql, params)

    try:
        reply = answer.respond(read, write, user=who, raw=body.text, surface="dashboard")
    except (RuntimeError, SQLAlchemyError) as exc:
        # No detail leaves: the text of the error can hold SQL or a connection string.
        log.error("ask_failed", kind=type(exc).__name__)
        raise HTTPException(status_code=503, detail=bw.ANSWER_FAILED) from None
    return schemas.AskOut(
        text=reply.text,
        known=reply.known,
        shown=reply.shown,
        more=reply.more,
        left_out=reply.left_out,
    )
