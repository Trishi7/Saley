"""`saley discord roundtrip --test --case pack|answer` (R4-P03, R4-Q02).

Why: the gate must prove that the two R4 shapes reach Discord intact, without a live channel
and without leaving anything behind. Each case builds its text from the database with the
same code the bot uses, posts it once to the TEST channel, reads it back by id, compares it,
and deletes it. One post per case keeps far from Discord rate limits.

The database work runs in a transaction that is rolled back: the answer case writes an ask
log row and seen marks, and the pack case never inserts a `weekly_pack` row. The roundtrip
leaves the database as it found it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import answer
from saley.bot.cli_discord import API, MAX_CONTENT, DiscordRefused, _guard, _headers, _json
from saley.bot.ping import TEST_TAG
from saley.core import egress
from saley.core.clock import now_ist, week_start
from saley.core.db import session_scope
from saley.core.logging import get_logger
from saley.intel import pack

log = get_logger("saley.bot.roundtrip")

CASES = ("pack", "answer")
ANSWER_USER = "kushal"  # an approver on DISCORD_INSTRUCT_IDS (D16, D141)
ANSWER_QUESTION = "what's due this week"
GONE = (httpx.codes.NO_CONTENT, httpx.codes.NOT_FOUND)


@dataclass(frozen=True)
class RoundtripResult:
    case: str
    message_id: str
    matched: bool
    deleted: bool

    @property
    def ok(self) -> bool:
        return self.matched and self.deleted


def pack_text(session: Session, now: datetime) -> str:
    """This week's pack summary, as the Monday post shows it (D117, D130)."""
    today = now.date()
    week = week_start(today)
    return pack.summary_text(pack.build_body(session, week, today), week)


def answer_text(session: Session, now: datetime) -> str:
    """The answer the gateway gives to a fixed question from a team member (D131)."""

    def write(sql: str, params: dict[str, Any]) -> Any:
        return session.execute(text(sql), params).scalar_one()

    reply = answer.respond(
        session.connection(),
        write,
        user=ANSWER_USER,
        raw=ANSWER_QUESTION,
        surface="discord",
        now=now,
    )
    return reply.text


BUILDERS = {"pack": pack_text, "answer": answer_text}


def build(case: str, now: datetime | None = None) -> str:
    """The text for one case. The transaction is rolled back, so nothing stays in the DB."""
    if case not in BUILDERS:
        raise DiscordRefused(f"--case must be one of: {', '.join(CASES)}")
    moment = now or now_ist()
    with session_scope() as session:
        try:
            return BUILDERS[case](session, moment)
        finally:
            session.rollback()


def run(
    case: str, client: httpx.Client | None = None, now: datetime | None = None
) -> RoundtripResult:
    """Post the case text to the TEST channel, read it back by id, then delete it."""
    token, channel = _guard()
    content = f"{TEST_TAG} {build(case, now)}".strip()
    if len(content) > MAX_CONTENT:
        raise DiscordRefused(f"the {case} text has {len(content)} characters; Discord takes 2000")
    url = f"{API}/channels/{channel}/messages"
    with client or egress.make_client() as http:
        body = {"content": content, "allowed_mentions": {"parse": []}}
        try:
            sent = _json(egress.request(http, "POST", url, headers=_headers(token), json=body))
        except httpx.HTTPError as exc:
            # The POST is never retried; Discord may still have taken it.
            raise DiscordRefused(
                f"post state unknown ({exc.__class__.__name__}): check the TEST channel"
            ) from exc
        message_id = str(sent["id"])
        try:
            got = _json(egress.request(http, "GET", f"{url}/{message_id}", headers=_headers(token)))
            matched = str(got.get("content", "")).strip() == content
        finally:
            deleted = _delete(http, f"{url}/{message_id}", token)
    return RoundtripResult(case, message_id, matched, deleted)


def _delete(http: httpx.Client, url: str, token: str) -> bool:
    """True when the post is gone. A network error is logged with the id, never raised, so a
    read-back error is not hidden and the post can be found and removed by hand."""
    try:
        gone = egress.request(http, "DELETE", url, headers=_headers(token))
    except httpx.HTTPError as exc:
        log.warning(
            "roundtrip_delete_failed", url_tail=url.rsplit("/", 1)[-1], error=exc.__class__.__name__
        )
        return False
    # 404: a retried DELETE whose first try already removed the post.
    return gone.status_code in GONE
