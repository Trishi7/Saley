"""`saley discord roundtrip --test --case pack|answer|ping` (R4-P03, R4-Q02, R6-S02).

Why: the gate must prove that the two R4 shapes reach Discord intact, without a live channel
and without leaving anything behind. Each case builds its text from the database with the
same code the bot uses, posts it once to the TEST channel, reads it back by id, compares it,
and deletes it. One post per case keeps far from Discord rate limits.

The database work runs in a transaction that is rolled back: the answer case writes an ask
log row and seen marks, and the pack case never inserts a `weekly_pack` row. The roundtrip
leaves the database as it found it.

The ping case (D250, the deploy smoke) needs the RUNNING bot: it posts `[TEST] @Saley ping` with
the bot's own token (ping.py accepts that shape), waits for the bot's `[TEST] pong` in the TEST
channel, and deletes both posts. It touches no database row.

R7 (R7-L01): one case per card type (`m1` … `m15`, `a1`, `a2`; `all` runs them in turn). Each
renders its sample (bot/cards/samples.py, test data) the way delivery does, posts it once, reads
it back (every text a person can see must be equal), edits it to "Done by Sid in Discord"
within 5 s (checked on the edit answer), then deletes it. No thread, no pin: one message a case.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import answer, card_view, cards, components
from saley.bot.cards.samples import CARD_IDS, SAMPLES
from saley.bot.cli_discord import API, MAX_CONTENT, DiscordRefused, _guard, _headers, _json
from saley.bot.ping import TEST_TAG
from saley.core import egress
from saley.core.clock import now_ist, week_start
from saley.core.db import session_scope
from saley.core.logging import get_logger
from saley.core.settings import get_settings
from saley.intel import pack

log = get_logger("saley.bot.roundtrip")

CARD_CASES = {t.lower(): t for t in SAMPLES}
CASES = ("pack", "answer", "ping", *CARD_CASES, "all")
EDIT_LIMIT_SECONDS = 5.0  # D259: a card is edited within 5 s
CALLS_PER_CASE = 4  # POST, GET, PATCH, DELETE


def case_pause_seconds() -> float:
    """`all`: wait until the egress bucket holds one case again, so the 5 s edit check times
    Discord and never our own rate limiter (EGRESS_RATE_PER_HOST_PER_MIN)."""
    return CALLS_PER_CASE * 60 / get_settings().egress_rate_per_host_per_min


PING_WAIT_SECONDS = 30  # the bot answers within a second or two when it is up
PING_POLL_SECONDS = 2
ANSWER_USER = "kushal"  # an approver on DISCORD_INSTRUCT_IDS (D16, D141)
ANSWER_QUESTION = "what's due this week"
GONE = (httpx.codes.NO_CONTENT, httpx.codes.NOT_FOUND)


@dataclass(frozen=True)
class RoundtripResult:
    case: str
    message_id: str
    matched: bool
    deleted: bool
    edited: bool = True  # card cases only: the edit arrived, read back, within 5 s

    @property
    def ok(self) -> bool:
        return self.matched and self.deleted and self.edited


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


def visible(message: dict[str, Any]) -> list[str]:
    """Every text a person can see, in order: content, embeds, Components v2 text, labels."""
    out = [str(message.get("content") or "").strip()]
    for e in message.get("embeds") or []:
        out += [
            e.get("title", ""),
            e.get("description", ""),
            (e.get("author") or {}).get("name", ""),
        ]
        out += [(e.get("footer") or {}).get("text", "")]
        out += [f"{f.get('name')}={f.get('value')}" for f in e.get("fields") or []]

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for value in node:
                walk(value)
            return
        if not isinstance(node, dict):
            return
        if node.get("type") == components.V2_TEXT:
            out.append(str(node.get("content") or "").strip())
        out.extend(str(node[k]) for k in ("label", "placeholder") if isinstance(node.get(k), str))
        walk(node.get("components") or [])
        walk(node.get("accessory") or [])

    walk(message.get("components") or [])
    return [t for t in out if t]


def card_case(
    case: str, client: httpx.Client | None = None, now: datetime | None = None
) -> RoundtripResult:
    """One card type: post, read back, edit within 5 s, read back, delete (R7-L01)."""
    token, channel = _guard()
    card_type = CARD_CASES[case]
    payload = cards.render(card_type, SAMPLES[card_type], CARD_IDS[card_type])
    row = SimpleNamespace(
        payload=payload,
        card_id=CARD_IDS[card_type],
        held_reason=None,
        done_at=None,
        done_by=None,
        done_surface=None,
        done_write_id=None,
        undo_until=None,
    )
    body = {**card_view.view(row, head=f"{TEST_TAG} ", now=now), "allowed_mentions": {"parse": []}}
    url = f"{API}/channels/{channel}/messages"
    with client or egress.make_client() as http:
        try:
            sent = _json(egress.request(http, "POST", url, headers=_headers(token), json=body))
        except httpx.HTTPError as exc:
            raise DiscordRefused(
                f"post state unknown ({exc.__class__.__name__}): check the TEST channel"
            ) from exc
        message_id = str(sent["id"])
        matched = edited = False
        try:
            got = _json(egress.request(http, "GET", f"{url}/{message_id}", headers=_headers(token)))
            matched = visible(got) == visible(body)
            if not matched:
                log.warning("roundtrip_card_differs", case=case)
            done = SimpleNamespace(**{**vars(row), "done_by": "Sid", "done_surface": "discord"})
            done.done_at = now_ist()
            started = time.monotonic()
            change = {
                **card_view.view(done, head=f"{TEST_TAG} "),
                "allowed_mentions": {"parse": []},
            }
            # The PATCH answer is the edited message; a GET right after may still be the old one.
            back = _json(
                egress.request(
                    http, "PATCH", f"{url}/{message_id}", headers=_headers(token), json=change
                )
            )
            took = time.monotonic() - started
            # A content-only card (M14) carries the line inside its text.
            edited = took < EDIT_LIMIT_SECONDS and any(
                "Done by Sid in Discord" in t for t in visible(back)
            )
        finally:
            deleted = _delete(http, f"{url}/{message_id}", token)
    return RoundtripResult(case, message_id, matched, deleted, edited)


def run(
    case: str, client: httpx.Client | None = None, now: datetime | None = None
) -> RoundtripResult:
    """Post the case text to the TEST channel, read it back by id, then delete it."""
    if case == "ping":
        return ping(client)
    if case in CARD_CASES:
        return card_case(case, client, now)
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


def ping(
    client: httpx.Client | None = None,
    wait: float = PING_WAIT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> RoundtripResult:
    """D250: `@Saley ping` in the TEST channel; the running bot must answer `[TEST] pong`."""
    token, channel = _guard()
    url = f"{API}/channels/{channel}/messages"
    with client or egress.make_client() as http:
        me = _json(egress.request(http, "GET", f"{API}/users/@me", headers=_headers(token)))
        bot_id = str(me["id"])
        body = {"content": f"{TEST_TAG} <@{bot_id}> ping", "allowed_mentions": {"parse": []}}
        try:
            sent = _json(egress.request(http, "POST", url, headers=_headers(token), json=body))
        except httpx.HTTPError as exc:
            raise DiscordRefused(
                f"post state unknown ({exc.__class__.__name__}): check the TEST channel"
            ) from exc
        message_id = str(sent["id"])
        answer_id = None
        try:
            answer_id = _wait_for_pong(http, url, token, bot_id, message_id, wait, sleep)
        finally:
            deleted = _delete(http, f"{url}/{message_id}", token)
            if answer_id is not None:
                deleted = _delete(http, f"{url}/{answer_id}", token) and deleted
    return RoundtripResult("ping", message_id, answer_id is not None, deleted)


def _wait_for_pong(
    http: httpx.Client,
    url: str,
    token: str,
    bot_id: str,
    after: str,
    wait: float,
    sleep: Callable[[float], None],
) -> str | None:
    """The id of the bot's pong after our post, or None when it did not come in time."""
    want = f"{TEST_TAG} pong"
    waited = 0.0
    while True:
        params = {"after": after, "limit": 50}  # a busy test channel must not hide the pong
        found = _json(egress.request(http, "GET", url, headers=_headers(token), params=params))
        for msg in found:
            author = str((msg.get("author") or {}).get("id", ""))
            if author == bot_id and str(msg.get("content", "")).strip() == want:
                return str(msg["id"])
        if waited >= wait:
            log.warning("roundtrip_no_pong", after=after, waited=waited)
            return None
        sleep(PING_POLL_SECONDS)
        waited += PING_POLL_SECONDS


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
