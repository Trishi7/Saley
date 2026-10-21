"""Review fixes for bot delivery (R4 ticket 5): poison rows, at-most-once, auth errors,
stale ready rows, mentions in test mode, Unicode digits, idempotent inserts.

Discord is an httpx MockTransport. Nothing here opens a socket.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from saley.bot import brief, deliver, guard, queue
from saley.bot import health as bot_health
from saley.core import clock, egress
from saley.core.settings import Settings
from saley.worker import jobs
from tests import test_bot as tb
from tests.test_bot import KUSHAL_ID, SALES_CH, SID_ID, TEST_CH, TUE, at, queue_row, states

# Fixtures of tests/test_bot.py, re-bound here so pytest finds them in this module.
bot_env = tb.bot_env
clean_posts = tb.clean_posts

Action = Callable[[httpx.Request], httpx.Response]


class Scripted:
    """A Discord double. Each message POST takes the next action; channel opens always work."""

    def __init__(self, *actions: Action | int | Exception | dict | str) -> None:
        self.actions = list(actions)
        self.requests: list[tuple[str, dict]] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else {}
        self.requests.append((req.url.path, body))
        if req.url.path.endswith("/users/@me/channels"):
            return httpx.Response(200, json={"id": "500000000000000005"})
        step = self.actions.pop(0) if self.actions else 200
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(req)
        if isinstance(step, dict):
            return httpx.Response(step.pop("status", 200), **step)
        if isinstance(step, str):  # a raw body with status 200
            return httpx.Response(200, content=step.encode())
        if step == 200:
            return httpx.Response(200, json={"id": f"m{len(self.requests)}"})
        return httpx.Response(step, json={})

    def client(self) -> httpx.Client:
        return egress.make_client(inner=httpx.MockTransport(self.handler))

    @property
    def messages(self) -> int:
        return sum(1 for path, _ in self.requests if path.endswith("/messages"))


def run(fake: Scripted):
    return deliver.deliver_ready(client=fake.client(), now=at(TUE, 12))


def msg_ids(db: Session) -> dict[int, str | None]:
    db.expire_all()
    return dict(db.execute(text("select id, msg_id from bot_post order by id")).all())


# ---------------------------------------- 1. poison rows


def test_poison_row_does_not_block_later_rows(bot_env, clean_posts: Session) -> None:
    no_id = queue_row(clean_posts)
    not_json = queue_row(clean_posts)
    good = queue_row(clean_posts)
    clean_posts.commit()
    fake = Scripted({"json": {"other": 1}}, "not json at all")
    stats = run(fake)
    assert states(clean_posts) == {no_id: "sent", not_json: "sent", good: "sent"}
    assert msg_ids(clean_posts)[no_id] is None and msg_ids(clean_posts)[not_json] is None
    assert msg_ids(clean_posts)[good] is not None
    assert stats.sent == 3 and fake.messages == 3  # nobody is sent twice


def test_unexpected_error_on_one_row_counts_failed_and_moves_on(
    bot_env, clean_posts: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = queue_row(clean_posts, body="BAD")
    good = queue_row(clean_posts)
    clean_posts.commit()
    real = deliver._send_one

    def flaky(http, settings, row, ids):
        if row.body == "BAD":
            raise KeyError("boom")
        return real(http, settings, row, ids)

    monkeypatch.setattr(deliver, "_send_one", flaky)
    stats = run(Scripted())
    assert (stats.failed, stats.sent) == (1, 1)
    assert states(clean_posts) == {bad: "ready", good: "sent"}


# ---------------------------------------- 2. at most once


def test_5xx_stays_ready_and_429_stops_the_batch(bot_env, clean_posts: Session) -> None:
    first = queue_row(clean_posts)
    second = queue_row(clean_posts)
    third = queue_row(clean_posts)
    clean_posts.commit()
    fake = Scripted(503, {"status": 429, "json": {}, "headers": {"Retry-After": "7"}}, 200)
    stats = run(fake)
    assert states(clean_posts) == {first: "ready", second: "ready", third: "ready"}
    assert fake.messages == 2  # the third row was not tried
    assert stats.failed == 2 and stats.halted


def test_ambiguous_timeout_marks_sent_with_no_message_id(bot_env, clean_posts: Session) -> None:
    row = queue_row(clean_posts)
    clean_posts.commit()
    fake = Scripted(httpx.ReadTimeout("slow"))
    stats = run(fake)
    assert states(clean_posts)[row] == "sent" and msg_ids(clean_posts)[row] is None
    assert stats.sent == 1 and fake.messages == 1
    assert run(Scripted()).sent == 0  # never sent again


@pytest.mark.parametrize(
    "error",
    [httpx.ReadError("x"), httpx.RemoteProtocolError("x"), httpx.WriteError("x")],
)
def test_other_ambiguous_errors_are_not_retried(
    bot_env, clean_posts: Session, error: Exception
) -> None:
    row = queue_row(clean_posts)
    clean_posts.commit()
    run(Scripted(error))
    assert states(clean_posts)[row] == "sent"


@pytest.mark.parametrize("error", [httpx.ConnectError("x"), httpx.ConnectTimeout("x")])
def test_certain_failures_stay_ready(bot_env, clean_posts: Session, error: Exception) -> None:
    row = queue_row(clean_posts)
    clean_posts.commit()
    assert run(Scripted(error)).failed == 1
    assert states(clean_posts)[row] == "ready"


# ---------------------------------------- 3. auth errors


@pytest.mark.parametrize("status", [401, 403])
def test_401_and_plain_403_keep_rows_and_stop_the_batch(
    bot_env, clean_posts: Session, status: int
) -> None:
    a, b = queue_row(clean_posts), queue_row(clean_posts)
    clean_posts.commit()
    fake = Scripted(status)
    stats = run(fake)
    assert states(clean_posts) == {a: "ready", b: "ready"}
    assert stats.dropped == 0 and stats.halted and fake.messages == 1


def test_403_with_code_50007_drops_only_that_row(bot_env, clean_posts: Session) -> None:
    a, b = queue_row(clean_posts), queue_row(clean_posts)
    clean_posts.commit()
    fake = Scripted({"status": 403, "json": {"code": 50007}}, 200)
    stats = run(fake)
    assert states(clean_posts) == {a: "dropped", b: "sent"}
    assert stats.dropped == 1 and not stats.halted


@pytest.mark.parametrize("status", [400, 404])
def test_400_and_404_drop_the_row(bot_env, clean_posts: Session, status: int) -> None:
    row = queue_row(clean_posts)
    clean_posts.commit()
    assert run(Scripted(status)).dropped == 1
    assert states(clean_posts)[row] == "dropped"


# ---------------------------------------- 4. stale ready rows


def test_stale_ready_row_is_dropped_and_the_run_fails(bot_env, clean_posts: Session) -> None:
    now = clock.now_ist()
    stale = queue_row(clean_posts, not_before=now - timedelta(hours=3))
    fresh = queue_row(clean_posts, not_before=now - timedelta(minutes=5))
    clean_posts.commit()
    with pytest.raises(RuntimeError, match="stale"):
        jobs.bot_plan()
    assert states(clean_posts) == {stale: "dropped", fresh: "ready"}  # the drop is committed


def test_ready_over_30_minutes_fails_the_run_and_shows_in_the_note(
    bot_env, clean_posts: Session
) -> None:
    waiting = queue_row(clean_posts, not_before=at(TUE, 11, 0))
    clean_posts.commit()
    stats = queue.plan_queue(clean_posts, now=at(TUE, 11, 40))
    assert stats.waiting == 1 and stats.stale == 0 and "1 waiting over 30 min" in stats.note()
    assert states(clean_posts)[waiting] == "ready"
    line = bot_health.health_text(clean_posts, at(TUE, 23, 45))
    assert "dropped in 24 h" in line and "waiting over 30 min" in line


def test_health_line_is_too_old_after_one_day(bot_env, clean_posts: Session) -> None:
    friday = queue_row(clean_posts, shape="health_line", state="queued", due_at=at(TUE, 23, 45))
    other = queue_row(clean_posts, shape="x", state="queued", due_at=at(TUE, 23, 45))
    clean_posts.commit()
    queue.plan_queue(clean_posts, now=at(TUE, 23, 45) + timedelta(days=1, hours=2))
    assert states(clean_posts)[friday] == "dropped"
    assert states(clean_posts)[other] != "dropped"  # other shapes keep the 3 day limit


# ---------------------------------------- 5. mentions


def _sales_mention_row(db: Session) -> int:
    db.execute(text("update users set discord_id = :i where short = 'kushal'"), {"i": KUSHAL_ID})
    kushal = db.execute(text("select id from users where short='kushal'")).scalar_one()
    return kushal


def test_redirected_dm_carries_no_mention(bot_env, clean_posts: Session) -> None:
    bot_env(TEST_MODE="false")  # dev: the DM goes to the test channel
    kushal = _sales_mention_row(clean_posts)
    queue_row(clean_posts, "kushal", channel="dm", mentions=[kushal], body="Hi")
    clean_posts.commit()
    fake = Scripted()
    run(fake)
    path, body = fake.requests[0]
    assert path == f"/api/v10/channels/{TEST_CH}/messages"
    assert "<@" not in body["content"] and body["allowed_mentions"] == {"parse": []}


def test_test_mode_channel_post_in_prod_pings_nobody(bot_env, clean_posts: Session) -> None:
    bot_env(SALEY_ENV="prod", TEST_MODE="true")
    kushal = _sales_mention_row(clean_posts)
    queue_row(clean_posts, channel="sales", mentions=[kushal], body="Send the deck")
    clean_posts.commit()
    fake = Scripted()
    run(fake)
    path, body = fake.requests[0]
    assert path == f"/api/v10/channels/{TEST_CH}/messages"
    assert "<@" not in body["content"] and KUSHAL_ID not in body["content"]
    assert "@kushal" in body["content"] and body["allowed_mentions"] == {"parse": []}


def test_live_mode_still_pings(bot_env, clean_posts: Session) -> None:
    bot_env(SALEY_ENV="prod", TEST_MODE="false")
    kushal = _sales_mention_row(clean_posts)
    queue_row(clean_posts, channel="sales", mentions=[kushal], body="Send the deck")
    clean_posts.commit()
    fake = Scripted()
    run(fake)
    path, body = fake.requests[0]
    assert path == f"/api/v10/channels/{SALES_CH}/messages"
    assert body["content"].startswith(f"<@{KUSHAL_ID}> ")
    assert body["allowed_mentions"] == {"users": [KUSHAL_ID]}


# ---------------------------------------- low items


def test_unicode_digits_are_not_discord_ids() -> None:
    arabic = "١" * 18
    assert guard._ID.fullmatch(arabic) is None
    assert guard._ID.fullmatch(SID_ID) is not None
    with pytest.raises(ValueError):
        Settings(discord_user_map=f"sid:{arabic}")


def test_duplicate_brief_insert_is_ignored(bot_env, clean_posts: Session) -> None:
    insert = text(
        "insert into bot_post (kind, shape, channel, for_user, mentions, body, rank, due_at, "
        "state, item_type, item_id) select 'daily_brief', 'daily_brief', 'dm', id, '{}', 'b', 0, "
        ":t, 'queued', 'daily_brief', 20261020 from users where short = 'sid' "
        "on conflict do nothing"
    )
    first = clean_posts.execute(insert, {"t": at(TUE, 9)})
    second = clean_posts.execute(insert, {"t": at(TUE, 9)})
    assert (first.rowcount, second.rowcount) == (1, 0)
    count = clean_posts.execute(text("select count(*) from bot_post")).scalar_one()
    assert count == 1


def test_the_index_allows_the_two_pack_shapes_and_refuses_a_plain_duplicate(
    bot_env, clean_posts: Session
) -> None:
    for shape in ("pack_a", "pack_b"):
        queue_row(clean_posts, shape=shape, state="queued", item_type="weekly_pack", item_id=5)
    clean_posts.commit()
    with pytest.raises(IntegrityError):
        queue_row(clean_posts, shape="pack_a", state="queued", item_type="weekly_pack", item_id=5)
    clean_posts.rollback()


def test_health_line_twice_queues_one(
    bot_env, clean_posts: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    moment = at(TUE, 23, 45)
    monkeypatch.setattr(clock, "now_ist", lambda: moment)
    assert bot_health.queue_health_line(clean_posts).queued == 1
    assert bot_health.queue_health_line(clean_posts).queued == 0
    assert clean_posts.execute(text("select count(*) from bot_post")).scalar_one() == 1


def test_brief_module_uses_conflict_safe_insert() -> None:
    assert "on conflict do nothing" in __import__("inspect").getsource(brief.queue_briefs)
