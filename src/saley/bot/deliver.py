"""Send `ready` bot posts through the Discord REST API (D127, D132).

Why: the worker plans, the bot process delivers (docs/09: the bot has no schedule of its own).
Everything goes through `saley.core.egress`. The client is a parameter, so tests pass an
httpx MockTransport and no socket opens. The target comes from `guard.resolve`, the single
place that keeps a laptop away from live channels.

Test mode and live use ONE render path; the only differences are the `[TEST]` tag and the
channel (CLAUDE.md). A real mention is sent only when TEST_MODE=false, the post is not a
redirected DM and SALEY_ENV is not dev. Otherwise the text carries the short name as plain
text ("@sid") and `allowed_mentions` parses nothing, so nobody is pinged (docs/09).

A row is claimed with `FOR UPDATE SKIP LOCKED` and marked sent in the same transaction, so a
second bot process cannot send it twice.

At most once beats at least once for a ping to a person. A row stays `ready` for the next loop
(15 s) only when the request certainly did not complete: a connect error, a pool timeout, 429
or 5xx. After any other network error the message may have arrived, so the row is marked
`sent` with no message id and logged as `bot_send_unconfirmed`. A 2xx without a readable
message id is marked `sent` the same way.

A 4xx drops the row only when the problem is the person: 400, 404, or 403 with Discord code
50007 ("cannot send messages to this user"). A bad token (401) or a missing permission (any
other 403) hits every row, so the batch stops and the rows stay `ready`. A 429 also stops the
batch for this tick. Any other error on one row counts as failed and the loop goes on, so one
poison row cannot block the queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import text

from saley.bot import card, card_view
from saley.bot import words as bw
from saley.bot.guard import ChannelRefused, Target, resolve
from saley.bot.ping import TEST_TAG
from saley.core import clock, egress
from saley.core.db import session_scope
from saley.core.logging import get_logger
from saley.core.settings import Settings, get_settings

log = get_logger("saley.bot.deliver")

API = "https://discord.com/api/v10"
MAX_CONTENT = 1990
BATCH = 20
TOO_MANY_REQUESTS = 429
SERVER_ERROR = 500
CANNOT_DM_CODE = 50007  # Discord: cannot send messages to this user
DROP_STATUSES = (400, 404)  # a problem of this one post or person: it can never succeed
# The request certainly did not complete, so a retry cannot send twice.
CERTAIN_FAILURES = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


@dataclass
class DeliverStats:
    sent: int = 0
    dropped: int = 0
    failed: int = 0
    halted: bool = False  # a 429, 401 or 403 stopped the batch for this tick


def render(
    body: str,
    *,
    test_mode: bool,
    mention_ids: list[str],
    redirected_for: str | None = None,
    plain_names: tuple[str, ...] = (),
) -> str:
    """The text of a post: [TEST] tag (test mode only), who it was meant for, mentions, body.

    `plain_names` are written as "@short" text, which pings nobody.
    """
    parts = []
    if test_mode and not body.startswith(TEST_TAG):
        parts.append(TEST_TAG + " ")
    if redirected_for:
        parts.append(bw.REDIRECT_PREFIX.format(who=redirected_for))
    parts.extend(f"<@{i}> " for i in mention_ids)
    parts.extend(f"@{n} " for n in plain_names)
    return ("".join(parts) + body)[:MAX_CONTENT]


def _headers(settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bot {settings.discord_bot_token}", "User-Agent": "saley-v2 (bot)"}


def _read_id(response: httpx.Response) -> str | None:
    """The `id` of a Discord reply, or None when the body is not what we expect."""
    try:
        value = response.json().get("id")
    except (ValueError, AttributeError):
        return None
    return str(value) if value else None


def _channel_for(http: httpx.Client, settings: Settings, target: Target) -> str:
    if target.channel_id:
        return target.channel_id
    response = egress.request(
        http,
        "POST",
        f"{API}/users/@me/channels",
        headers=_headers(settings),
        json={"recipient_id": target.dm_user_id},
    )
    response.raise_for_status()
    channel_id = _read_id(response)
    if channel_id is None:
        raise ValueError("Discord opened a DM channel without an id")
    return channel_id


def _as_id(value: str | None) -> int | None:
    """A Discord id as a number for the bigint columns; anything else stays None."""
    return int(value) if value and value.isdigit() else None


@dataclass(frozen=True)
class Sent:
    message_id: str | None
    channel_id: str


def _head(
    settings: Settings, target: Target, people: list[tuple[str, str | None]], body: str = ""
) -> tuple[str, list[str]]:
    """`body` with the tag, the redirect note and the mentions in front; and the ids pinged."""
    real_ping = not settings.test_mode and not settings.is_dev and not target.redirected_for
    ids = [i for _, i in people if i] if real_ping else []
    names = tuple(s for s, _ in people) if not real_ping else ()
    text_ = render(
        body,
        test_mode=settings.test_mode,
        mention_ids=ids,
        redirected_for=target.redirected_for,
        plain_names=names,
    )
    return text_, ids


def _send_one(
    http: httpx.Client, settings: Settings, row: Any, people: list[tuple[str, str | None]]
) -> Sent:
    """Send one row. The message id is None when it may have arrived (see module doc).

    `people` are (short, discord_id) pairs of the @mentions. Raises ChannelRefused or httpx
    errors that are certain not to have sent anything. A card row (R7) sends its payload.
    """
    target = resolve(row.channel, settings, discord_id=row.discord_id, short=row.short)
    if row.payload is not None:
        head, ids = _head(settings, target, people)
        payload: dict[str, Any] = card_view.view(row, head=head)
    else:
        content, ids = _head(settings, target, people, row.body)
        payload = {"content": content}
        if row.shape == card.SHAPE and row.item_type == card.ITEM_TYPE and row.item_id:
            payload["components"] = card.components(int(row.item_id))  # D129: the card's buttons
    payload["allowed_mentions"] = {"users": ids} if ids else {"parse": []}
    channel_id = _channel_for(http, settings, target)
    try:
        response = egress.request(
            http,
            "POST",
            f"{API}/channels/{channel_id}/messages",
            headers=_headers(settings),
            json=payload,
        )
    except CERTAIN_FAILURES:
        raise
    except httpx.TransportError as exc:
        log.warning("bot_send_unconfirmed", post=row.id, error=type(exc).__name__)
        return Sent(None, channel_id)
    response.raise_for_status()
    message_id = _read_id(response)
    if message_id is None:
        log.warning("bot_send_unconfirmed", post=row.id, error="no message id in reply")
    return Sent(message_id, channel_id)


def _people(session: Any, mentions: list[int] | None) -> list[tuple[str, str | None]]:
    if not mentions:
        return []
    rows = session.execute(
        text("select short, discord_id from users where id = any(:ids) order by id"),
        {"ids": list(mentions)},
    )
    return [(r[0], r[1]) for r in rows]


def deliver_ready(
    client: httpx.Client | None = None, now: datetime | None = None, limit: int = BATCH
) -> DeliverStats:
    """Send up to `limit` ready rows, oldest first. Safe to call every 15 seconds."""
    settings = get_settings()
    now = now or clock.now_ist()
    stats = DeliverStats()
    last_id = 0
    own = client is None
    http = client or egress.make_client()
    try:
        for _ in range(limit):
            last_id = _deliver_next(http, settings, now, last_id, stats)
            if last_id < 0 or stats.halted:
                break
    finally:
        if own:
            http.close()
    return stats


def _discord_code(response: httpx.Response) -> int | None:
    try:
        code = response.json().get("code")
    except (ValueError, AttributeError):
        return None
    return code if isinstance(code, int) else None


def _on_status(session: Any, post_id: int, response: httpx.Response, stats: DeliverStats) -> None:
    """Decide what an error status means for the row and for the rest of the batch."""
    status = response.status_code
    if status == TOO_MANY_REQUESTS:
        stats.failed += 1
        stats.halted = True
        log.warning(
            "bot_rate_limited", post=post_id, retry_after=response.headers.get("retry-after")
        )
    elif status in DROP_STATUSES or (status == 403 and _discord_code(response) == CANNOT_DM_CODE):
        _set(session, post_id, "dropped")
        stats.dropped += 1
        log.warning("bot_post_failed", post=post_id, status=status, action="dropped")
    elif status in (401, 403):
        stats.failed += 1
        stats.halted = True
        log.error("bot_send_halted", post=post_id, status=status, why="token or permission")
    else:
        stats.failed += 1
        log.warning("bot_post_failed", post=post_id, status=status, action="kept")


def _deliver_next(
    http: httpx.Client, settings: Settings, now: datetime, last_id: int, stats: DeliverStats
) -> int:
    """Claim and send the next ready row after `last_id`. Returns its id, or -1 when none."""
    with session_scope() as session:
        row = session.execute(
            text(
                "select b.id, b.channel, b.body, b.mentions, b.shape, b.item_type, b.item_id, "
                "b.card_id, b.card_type, b.payload, b.held_reason, b.done_at, b.done_by, "
                "b.done_surface, b.done_write_id, b.undo_until, u.short, u.discord_id "
                "from bot_post b left join users u on u.id = b.for_user "
                "where b.state = 'ready' and b.id > :last order by b.id limit 1 "
                "for update of b skip locked"
            ),
            {"last": last_id},
        ).first()
        if row is None:
            return -1
        try:
            sent = _send_one(http, settings, row, _people(session, row.mentions))
        except ChannelRefused as exc:
            _set(session, row.id, "dropped")
            stats.dropped += 1
            log.warning("bot_post_refused", post=row.id, why=str(exc))
        except httpx.HTTPStatusError as exc:
            _on_status(session, row.id, exc.response, stats)
        except (httpx.HTTPError, egress.EgressBlocked) as exc:
            stats.failed += 1
            log.warning("bot_post_failed", post=row.id, error=type(exc).__name__)
        except Exception as exc:  # one bad row must not stop the queue
            stats.failed += 1
            log.error("bot_post_error", post=row.id, error=type(exc).__name__)
        else:
            session.execute(
                text(
                    "update bot_post set state = 'sent', sent_at = :t, msg_id = :m, "
                    "discord_message_id = :mi, discord_channel_id = :c where id = :i"
                ),
                {
                    "t": now,
                    "m": sent.message_id,
                    "mi": _as_id(sent.message_id),
                    "c": _as_id(sent.channel_id),
                    "i": row.id,
                },
            )
            stats.sent += 1
            if row.payload is not None and sent.message_id:
                _after_send(http, settings, session, row, sent)
        return int(row.id)


def _set(session: Any, post_id: int, state: str) -> None:
    session.execute(
        text("update bot_post set state = :s where id = :i"), {"s": state, "i": post_id}
    )


# ------------------------------------------------------------------ R7 cards: thread, pin, edit

THREAD_NAME_MAX = 100
THREAD_ARCHIVE_MINUTES = 1440


def api(
    http: httpx.Client, settings: Settings, method: str, path: str, **kw: Any
) -> httpx.Response:
    response = egress.request(http, method, f"{API}{path}", headers=_headers(settings), **kw)
    response.raise_for_status()
    return response


def _after_send(http: httpx.Client, settings: Settings, session: Any, row: Any, sent: Sent) -> None:
    """The overflow thread and the pin. The card is already sent: a failure here is logged and
    never sends the card again (at most once, see the module doc)."""
    payload = row.payload
    try:
        if payload.get("thread"):
            thread_id = post_thread(http, settings, sent, payload["thread"])
            session.execute(
                text("update bot_post set thread_id = :t where id = :i"),
                {"t": _as_id(thread_id), "i": row.id},
            )
        if payload.get("pin"):
            pin(http, settings, session, row.id, sent)
    except (httpx.HTTPError, egress.EgressBlocked) as exc:
        log.warning("bot_card_extra_failed", post=row.id, error=type(exc).__name__)


def post_thread(http: httpx.Client, settings: Settings, sent: Sent, thread: dict[str, Any]) -> str:
    """D260: the items over BOT_LIST_MAX, in a public thread under the card, in the same order."""
    made = api(
        http,
        settings,
        "POST",
        f"/channels/{sent.channel_id}/messages/{sent.message_id}/threads",
        json={
            "name": thread["name"][:THREAD_NAME_MAX],
            "auto_archive_duration": THREAD_ARCHIVE_MINUTES,
        },
    )
    thread_id = _read_id(made)
    if thread_id is None:
        raise httpx.HTTPError("Discord made a thread without an id")
    tag = f"{TEST_TAG} " if settings.test_mode else ""
    posts = thread["posts"] if "posts" in thread else [thread["content"]]
    for post in posts:
        api(
            http,
            settings,
            "POST",
            f"/channels/{thread_id}/messages",
            json={"content": (tag + post)[:MAX_CONTENT], "allowed_mentions": {"parse": []}},
        )
    return thread_id


def pin(http: httpx.Client, settings: Settings, session: Any, post_id: int, sent: Sent) -> None:
    """docs/09 §6: only the weekly pack is pinned; the bot unpins its previous pack first."""
    old = session.execute(
        text(
            "select id, discord_channel_id, discord_message_id from bot_post "
            "where pinned and card_type = 'M12' and id <> :i"
        ),
        {"i": post_id},
    ).all()
    for o in old:
        try:
            api(
                http,
                settings,
                "DELETE",
                f"/channels/{o.discord_channel_id}/pins/{o.discord_message_id}",
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != httpx.codes.NOT_FOUND:  # a human unpinned it already
                raise
        session.execute(text("update bot_post set pinned = false where id = :i"), {"i": o.id})
    api(http, settings, "PUT", f"/channels/{sent.channel_id}/pins/{sent.message_id}")
    session.execute(text("update bot_post set pinned = true where id = :i"), {"i": post_id})


edit_sql = (
    "select b.id, b.channel, b.card_id, b.card_type, b.payload, b.held_reason, b.done_at, "
    "b.done_by, b.done_surface, b.done_write_id, b.undo_until, b.mentions, b.state, "
    "b.discord_channel_id, b.discord_message_id, u.short, u.discord_id "
    "from bot_post b left join users u on u.id = b.for_user where b.card_id = :c"
)


def edit_card(card_id: str, client: httpx.Client | None = None, now: datetime | None = None) -> str:
    """Edit a sent card in place to its current state. Returns edited, gone, skipped or failed.

    docs/17 §6: a message a human deleted answers 404; the row becomes `gone` and is never
    posted again. The bot never reposts a card to change its state (docs/09 §6).
    """
    settings = get_settings()
    own = client is None
    http = client or egress.make_client()
    try:
        with session_scope() as session:
            row = session.execute(text(edit_sql), {"c": card_id}).first()
            if row is None or row.state != "sent" or not row.discord_message_id:
                return "skipped"
            target = resolve(row.channel, settings, discord_id=row.discord_id, short=row.short)
            head, _ = _head(settings, target, _people(session, row.mentions))
            body = {**card_view.view(row, head=head, now=now), "allowed_mentions": {"parse": []}}
            path = f"/channels/{row.discord_channel_id}/messages/{row.discord_message_id}"
            try:
                api(http, settings, "PATCH", path, json=body)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == httpx.codes.NOT_FOUND:
                    session.execute(
                        text("update bot_post set state = 'gone' where id = :i"), {"i": row.id}
                    )
                    log.info("bot_card_gone", card=card_id)
                    return "gone"
                log.warning("bot_card_edit_failed", card=card_id, status=exc.response.status_code)
                return "failed"
            except (httpx.HTTPError, egress.EgressBlocked) as exc:
                log.warning("bot_card_edit_failed", card=card_id, error=type(exc).__name__)
                return "failed"
            return "edited"
    finally:
        if own:
            http.close()
