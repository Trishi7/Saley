"""saley.bot: the ping rule and the Discord test-loop CLI functions (no network)."""

from __future__ import annotations

import asyncio
import itertools
import json
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import answer as bot_answer
from saley.bot import (
    brief,
    caps,
    card,
    cli_discord,
    deliver,
    guard,
    identity,
    instruct,
    queue,
    roundtrip,
)
from saley.bot import health as bot_health
from saley.bot import words as bot_words
from saley.bot.caps import Post
from saley.bot.ping import Incoming, decide
from saley.core import clock, egress
from saley.core.db import rw_engine, session_scope
from saley.core.models import NewsStory
from saley.core.settings import Settings, get_settings
from saley.intel import guard as value_guard
from saley.intel import reminders
from saley.news.clean import fingerprint, url_hash
from saley.web.app import create_app
from tests.conftest import STRANGER_ID, TEAM_IDS, SignedClient, contact_id, make_card

BOT, TEST, OTHER, HUMAN = 900, 100, 200, 300


def msg(content: str, channel: int = TEST, author: int = HUMAN, is_bot: bool = False) -> Incoming:
    return Incoming(channel, author, is_bot, content, BOT)


def test_pong_to_mention_in_test_channel() -> None:
    reply = decide(msg(f"<@{BOT}> ping"), test_channel_id=TEST, test_mode=True)
    assert reply is not None and reply.text == "[TEST] pong"


def test_plain_text_at_saley_works() -> None:
    assert decide(msg("@Saley  PING"), test_channel_id=TEST, test_mode=True) is not None


@pytest.mark.parametrize(
    "incoming",
    [
        msg(f"<@{BOT}> ping", channel=OTHER),
        msg(f"<@{BOT}> what is new"),
        msg("ping"),
        msg(f"<@{BOT}> ping", is_bot=True, author=555),
        msg("[TEST] pong", author=BOT, is_bot=True),
        msg(f"<@{BOT}> ping", author=BOT, is_bot=True),
    ],
)
def test_everything_else_is_ignored(incoming: Incoming) -> None:
    assert decide(incoming, test_channel_id=TEST, test_mode=True) is None


def test_cli_message_from_own_token_is_answered_with_as_user() -> None:
    reply = decide(
        msg(f"[TEST][as:42] <@{BOT}> ping", author=BOT, is_bot=True),
        test_channel_id=TEST,
        test_mode=True,
    )
    assert reply is not None and reply.as_user == 42


class FakeDiscord:
    def __init__(self) -> None:
        self.posts: list[dict] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bot tkn"
        if req.url.path.endswith("/users/@me"):
            return httpx.Response(200, json={"id": "900"})
        if req.method == "POST":
            self.posts.append({"url": str(req.url), **json.loads(req.content)})
            return httpx.Response(200, json={"id": "777"})
        return httpx.Response(
            200,
            json=[
                {
                    "timestamp": "2026-10-18T10:00:01",
                    "author": {"username": "saley"},
                    "content": "[TEST] pong",
                },
                {
                    "timestamp": "2026-10-18T10:00:00",
                    "author": {"username": "saley"},
                    "content": "[TEST] <@900> ping",
                },
            ],
        )


@pytest.fixture
def discord_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tkn")
    monkeypatch.setenv("DISCORD_TEST_CHANNEL_ID", "100000000000000001")
    monkeypatch.setenv("DISCORD_TEST_USER_IDS", "42")
    get_settings.cache_clear()
    egress.reset_policy(egress.build_policy(recorder=None))
    yield FakeDiscord()
    get_settings.cache_clear()


def _client(fake: FakeDiscord) -> httpx.Client:
    return egress.make_client(inner=httpx.MockTransport(fake.handler))


def test_send_posts_to_test_channel_with_tag_and_mention(discord_env: FakeDiscord) -> None:
    assert cli_discord.send("@Saley ping", client=_client(discord_env)) == "777"
    post = discord_env.posts[0]
    assert post["url"].endswith("/channels/100000000000000001/messages")
    assert post["content"] == "[TEST] <@900> ping"
    assert post["allowed_mentions"] == {"parse": []}


def test_send_as_test_user(discord_env: FakeDiscord) -> None:
    cli_discord.send("hello", as_user="42", client=_client(discord_env))
    assert discord_env.posts[0]["content"] == "[TEST][as:42] hello"
    with pytest.raises(cli_discord.DiscordRefused):
        cli_discord.send("hello", as_user="43", client=_client(discord_env))


def test_read_returns_oldest_first(discord_env: FakeDiscord) -> None:
    got = cli_discord.read(2, client=_client(discord_env))
    assert [m.content for m in got] == ["[TEST] <@900> ping", "[TEST] pong"]


def test_refuses_when_test_mode_is_false(
    discord_env: FakeDiscord, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_MODE", "false")
    get_settings.cache_clear()
    with pytest.raises(cli_discord.DiscordRefused, match="TEST_MODE"):
        cli_discord.send("x", client=_client(discord_env))
    with pytest.raises(cli_discord.DiscordRefused):
        cli_discord.read(1, client=_client(discord_env))
    assert discord_env.posts == []


# ---------------------------------------- review fixes: discord


@pytest.mark.parametrize(
    ("test_id", "sales_id"), [("123", ""), ("100000000000000001", "100000000000000001")]
)
def test_discord_refuses_bad_or_live_channel(
    monkeypatch: pytest.MonkeyPatch, test_id: str, sales_id: str
) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tkn")
    monkeypatch.setenv("DISCORD_TEST_CHANNEL_ID", test_id)
    monkeypatch.setenv("DISCORD_SALES_CHANNEL_ID", sales_id)
    get_settings.cache_clear()
    try:
        with pytest.raises(cli_discord.DiscordRefused):
            cli_discord.read(
                1,
                client=httpx.Client(
                    transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
                ),
            )
    finally:
        get_settings.cache_clear()


# ---------------------------------------- R4 ticket 5: bot core (D126-D135)
IST = ZoneInfo("Asia/Kolkata")
TUE, FRI, SAT, MON = date(2026, 10, 20), date(2026, 10, 23), date(2026, 10, 24), date(2026, 10, 26)
TEST_CH, SALES_CH = "100000000000000001", "200000000000000002"
SID_ID, KUSHAL_ID = "300000000000000003", "400000000000000004"


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=IST) + timedelta(
        hours=hour, minutes=minute
    )


def cfg(**values: object) -> Settings:
    """Settings for the pure caps tests: DMs on, so a dm post is not dropped."""
    return Settings(**{"dm_enabled": True, **values})


def post(pid: int, *people: str, due: datetime | None = None, **kw) -> Post:
    return Post(
        id=pid,
        channel=kw.pop("channel", "dm"),
        people=tuple(people),
        due_at=due or at(TUE, 0),
        **kw,
    )


def run_day(posts: list[Post], day: date, settings: Settings, sent=None, step: int = 5):
    """Plan every `step` minutes of one day. Returns {post id: time it became ready}."""
    sent = {k: list(v) for k, v in (sent or {}).items()}
    waiting = {p.id: p for p in posts}
    readied: dict[int, datetime] = {}
    for minute in range(0, 24 * 60, step):
        now = at(day, 0) + timedelta(minutes=minute)
        result = caps.plan(list(waiting.values()), now, sent, settings)
        for pid in result.ready:
            readied[pid] = now
            for person in waiting.pop(pid).people:
                sent.setdefault(person, []).append(now)
        for pid, when in result.deferred.items():
            waiting[pid] = waiting[pid].later(when)
        for pid in result.dropped:
            waiting.pop(pid)
    return readied


def test_caps_four_per_person_two_hours_weekend_silence() -> None:
    settings = cfg()
    # 20 queued for one person: at most 4 in the day, at least 2 h apart, best rank first.
    posts = [post(i, "kushal", rank=i) for i in range(1, 21)]
    readied = run_day(posts, TUE, settings)
    times = sorted(readied.values())
    assert len(times) == 4
    assert all(b - a >= timedelta(hours=2) for a, b in itertools.pairwise(times))
    assert set(readied) == {20, 19, 18, 17}
    # A DM and an @mention count together for one person.
    mixed = [post(1, "kushal"), post(2, "kushal", channel="sales"), post(3, "kushal")]
    assert len(run_day(mixed, TUE, settings)) == 3
    four_sent = {"kushal": [at(TUE, 1), at(TUE, 4), at(TUE, 7), at(TUE, 10)]}
    result = caps.plan([post(9, "kushal")], at(TUE, 15), four_sent, settings)
    assert result.ready == () and result.deferred[9] >= at(TUE + timedelta(days=1), 0)

    # A channel post with no mention counts for nobody: six go at once.
    quiet = [post(i, channel="sales") for i in range(1, 7)]
    result = caps.plan(quiet, at(TUE, 12), {"kushal": [at(TUE, 1)] * 4}, settings)
    assert sorted(result.ready) == [1, 2, 3, 4, 5, 6]

    # A meeting in under 24 h skips the gap. A normal post waits for it.
    sent = {"kushal": [at(TUE, 11, 30)]}
    normal = caps.plan([post(1, "kushal")], at(TUE, 12), sent, settings)
    assert normal.ready == () and normal.deferred[1] == at(TUE, 13, 30)
    meeting = caps.plan([post(2, "kushal", is_meeting=True)], at(TUE, 12), sent, settings)
    assert meeting.ready == (2,)

    # Saturday: nothing, except a meeting brief and a reconnect ping.
    sat = at(SAT, 1)
    weekend = [
        post(1, "kushal", due=sat),
        post(2, channel="sales", due=sat),
        post(3, "kushal", is_meeting=True, due=sat),
        post(4, "vaishnavi", is_reconnect=True, due=sat),
    ]
    result = caps.plan(weekend, at(SAT, 15), {}, settings)
    assert sorted(result.ready) == [3, 4]
    assert result.deferred[1] == at(MON, 0) and result.deferred[2] == at(MON, 0)


def test_sid_hours_window_14_to_24() -> None:
    settings = cfg()  # USER_HOURS=sid:14:00-24:00
    early = caps.plan([post(1, "sid", due=at(TUE, 9))], at(TUE, 10), {}, settings)
    assert early.ready == () and early.deferred == {1: at(TUE, 14)}
    assert caps.plan([post(1, "sid")], at(TUE, 14), {}, settings).ready == (1,)
    assert caps.plan([post(1, "sid")], at(TUE, 23, 59), {}, settings).ready == (1,)
    # After midnight the window is shut until 14:00.
    night = caps.plan([post(1, "sid")], at(TUE, 0, 30), {}, settings)
    assert night.deferred == {1: at(TUE, 14)}
    # A gap that ends after 24:00 waits for the next window; Friday night waits for Monday.
    sent = {"sid": [at(FRI, 23, 0)]}
    late = caps.plan([post(1, "sid", due=at(FRI, 9))], at(FRI, 23, 30), sent, settings)
    assert late.deferred == {1: at(MON, 14)}
    # Other people have no window and are not held.
    assert caps.plan([post(1, "kushal")], at(TUE, 3), {}, settings).ready == (1,)
    # A post for two people waits for the later start.
    both = caps.plan([post(1, "sid", "kushal")], at(TUE, 3), {}, settings)
    assert both.deferred == {1: at(TUE, 14)}


@pytest.mark.parametrize("bad", ["sid:14:00", "sid:25:00-26:00", "sid:14:00-13:00", "Sid:1-2"])
def test_user_hours_format_is_strict(bad: str) -> None:
    with pytest.raises(ValueError, match=r"USER_HOURS|HH:MM|valid"):
        cfg(user_hours=bad)


def test_user_hours_parse_and_24_00() -> None:
    assert cfg(user_hours="sid:14:00-24:00;vaishnavi:09:30-18:00").user_hours_map == {
        "sid": (840, 1440),
        "vaishnavi": (570, 1080),
    }
    with pytest.raises(ValueError):
        cfg(user_hours="sid:24:00-24:00")


@pytest.mark.parametrize(
    ("name", "bad"),
    [
        ("daily_brief_time", "9am"),
        ("daily_brief_time", "24:00"),
        ("dm_users", "Sid"),
        ("discord_user_map", "sid:12"),
        ("discord_user_map", f"sid:{SID_ID};sid:{KUSHAL_ID}"),
        ("discord_user_map", f"sid:{SID_ID};kushal:{SID_ID}"),
    ],
)
def test_new_settings_are_strict(name: str, bad: str) -> None:
    with pytest.raises(ValueError):
        cfg(**{name: bad})


def test_new_settings_defaults() -> None:
    s = Settings()
    assert (s.bot_posts_per_person_per_day, s.bot_post_gap_minutes) == (4, 120)
    assert (s.daily_brief_time, s.dm_enabled, s.discord_user_map) == ("14:00", False, "")
    assert s.dm_user_shorts == ["sid", "vaishnavi", "kushal", "trishi"]
    assert Settings(dm_users="").dm_user_shorts == []
    assert cfg(discord_user_map=f"sid:{SID_ID}").user_map == {"sid": SID_ID}


# ---------------------------------------- guard, render, deliver


@pytest.fixture
def bot_env(set_env):
    def apply(**values: str) -> None:
        base = {
            "DISCORD_BOT_TOKEN": "tkn",
            "DISCORD_TEST_CHANNEL_ID": TEST_CH,
            "DISCORD_SALES_CHANNEL_ID": SALES_CH,
            "SALEY_ENV": "dev",
            "TEST_MODE": "true",
        }
        set_env(**{**base, **values})

    apply()
    return apply


class Recorder:
    """A Discord REST double: records every request, answers with a message id."""

    def __init__(self, fail_status: int | None = None) -> None:
        self.requests: list[tuple[str, str, dict]] = []
        self.fail_status = fail_status

    def handler(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else {}
        self.requests.append((req.method, req.url.path, body))
        if self.fail_status:
            return httpx.Response(self.fail_status, json={})
        if req.url.path.endswith("/users/@me/channels"):
            return httpx.Response(200, json={"id": "500000000000000005"})
        return httpx.Response(200, json={"id": f"m{len(self.requests)}"})

    def client(self) -> httpx.Client:
        return egress.make_client(inner=httpx.MockTransport(self.handler))

    @property
    def paths(self) -> list[str]:
        return [path for _, path, _ in self.requests]


def queue_row(db: Session, short: str | None = None, **kw) -> int:
    values = {
        "shape": "x",
        "channel": "test",
        "body": "Hello",
        "state": "ready",
        "due_at": at(TUE, 12),
        "mentions": [],
        "for_user": None,
        "rank": 0,
    }
    values.update(kw)
    if short:
        values["for_user"] = db.execute(
            text("select id from users where short = :s"), {"s": short}
        ).scalar_one()
    cols = ", ".join(values)
    marks = ", ".join(f":{k}" for k in values)
    return db.execute(
        text(f"insert into bot_post ({cols}) values ({marks}) returning id"), values
    ).scalar_one()


@pytest.fixture
def clean_posts(db: Session):
    yield db
    db.rollback()
    db.execute(text("update users set discord_id = null"))
    db.commit()


def states(db: Session) -> dict[int, str]:
    db.expire_all()
    return dict(db.execute(text("select id, state from bot_post order by id")).all())


def test_shapes_live_in_test_mode_flag_retired(bot_env, clean_posts: Session) -> None:
    # The flag is gone from the code and sits in the RETIRED block of .env.example (D127).
    assert "bot_shapes_draft" not in Settings.model_fields
    example = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    assert not re.search(r"^BOT_SHAPES_DRAFT=", example, re.MULTILINE)
    assert re.search(r"^# BOT_SHAPES_DRAFT\b", example.split("# --- RETIRED")[1], re.MULTILINE)
    src = Path(queue.__file__).parent.parent
    assert not any("BOT_SHAPES_DRAFT" in f.read_text("utf-8") for f in src.rglob("*.py"))
    # A pack shape is delivered at once in test mode: [TEST] tag, test channel, nothing else.
    row = queue_row(clean_posts, shape="pack_summary", body="Weekly pack · week of Mon 19 Oct")
    clean_posts.commit()
    fake = Recorder()
    stats = deliver.deliver_ready(client=fake.client(), now=at(TUE, 12))
    assert stats.sent == 1 and fake.paths == [f"/api/v10/channels/{TEST_CH}/messages"]
    assert fake.requests[0][2]["content"] == "[TEST] Weekly pack · week of Mon 19 Oct"
    assert states(clean_posts)[row] == "sent"
    assert clean_posts.execute(text("select msg_id from bot_post")).scalar_one() == "m1"


def test_politeness_budget_and_no_overdue_word(bot_env, synced: Session) -> None:
    due = date(2026, 10, 22)
    sent, last, packed, kinds = 0, None, False, []
    for offset in range(-5, 6):
        day = due + timedelta(days=offset)
        for _ in range(2):  # a second run in the day adds nothing
            step = reminders.plan(due, sent, last, packed, day)
            if step.kind:
                kinds.append((offset, step.kind))
                sent, last = sent + 1, day
            packed = step.in_pack or packed
    assert kinds == [(-1, "before"), (0, "due_day")] and packed
    # Never the word "overdue": the bot words, the brief, the health line, the pack words.
    from saley.web import words as web_words

    texts = [v for k, v in vars(bot_words).items() if k.isupper() and isinstance(v, str)]
    texts += [v for k, v in vars(web_words).items() if k.isupper() and isinstance(v, str)]
    texts.append(bot_health.health_text(synced, now=at(TUE, 23, 45)))
    texts += [Path(m.__file__).read_text("utf-8") for m in (caps, deliver, guard, brief, queue)]
    assert all("overdue" not in t.lower() for t in texts)


def test_test_mode_equals_live_and_laptop_never_posts_live(bot_env, clean_posts: Session) -> None:
    clean_posts.execute(text("update users set discord_id = :i where short = 'sid'"), {"i": SID_ID})
    # The guard decides the channel id.
    s = get_settings()
    assert guard.resolve("test", s).channel_id == TEST_CH
    assert guard.resolve("sales", s).channel_id == TEST_CH  # TEST_MODE=true: never the live one
    # Dev: a DM is redirected to the test channel with the short name, never sent to the person.
    dm = guard.resolve("dm", s, discord_id=SID_ID, short="sid")
    assert (dm.channel_id, dm.dm_user_id, dm.redirected_for) == (TEST_CH, None, "sid")
    bot_env(TEST_MODE="false")
    with pytest.raises(guard.ChannelRefused):  # dev + live mode: the sales channel is refused
        guard.resolve("sales", get_settings())
    assert guard.resolve("test", get_settings()).channel_id == TEST_CH
    # Not dev: sales goes to sales, a DM to the person. Same code path.
    bot_env(SALEY_ENV="prod", TEST_MODE="false")
    assert guard.resolve("sales", get_settings()).channel_id == SALES_CH
    live_dm = guard.resolve("dm", get_settings(), discord_id=SID_ID, short="sid")
    assert (live_dm.channel_id, live_dm.dm_user_id) == (None, SID_ID)
    with pytest.raises(guard.ChannelRefused):
        guard.resolve("dm", get_settings(), discord_id=None, short="sid")
    bot_env(SALEY_ENV="prod", TEST_MODE="true")
    assert guard.resolve("sales", get_settings()).channel_id == TEST_CH
    # docs/09: everything goes to the test channel while TEST_MODE=true, DMs too.
    test_dm = guard.resolve("dm", get_settings(), discord_id=SID_ID, short="sid")
    assert (test_dm.channel_id, test_dm.dm_user_id, test_dm.redirected_for) == (
        TEST_CH,
        None,
        "sid",
    )
    # One render path: the only difference is the [TEST] tag.
    assert deliver.render("Hi", test_mode=True, mention_ids=[]) == "[TEST] Hi"
    assert deliver.render("Hi", test_mode=False, mention_ids=[]) == "Hi"
    assert deliver.render("[TEST] Hi", test_mode=True, mention_ids=[]) == "[TEST] Hi"

    # Dev laptop with TEST_MODE=false: a sales row and a DM row. Nothing leaves for a live place.
    bot_env(TEST_MODE="false")
    sales = queue_row(clean_posts, channel="sales")
    dm_row = queue_row(clean_posts, "sid", channel="dm", body="Good morning.")
    clean_posts.commit()
    fake = Recorder()
    deliver.deliver_ready(client=fake.client(), now=at(TUE, 12))
    assert states(clean_posts) == {sales: "dropped", dm_row: "sent"}
    assert fake.paths == [f"/api/v10/channels/{TEST_CH}/messages"]  # the DM went to test, as text
    sent = fake.requests[0][2]
    assert sent["content"] == "to sid: Good morning."
    assert sent["allowed_mentions"] == {"parse": []}
    # Live (not dev, TEST_MODE=false): the sales channel, no tag, and a real DM.
    bot_env(SALEY_ENV="prod", TEST_MODE="false")
    again = queue_row(clean_posts, channel="sales", body="Weekly pack")
    dm_live = queue_row(clean_posts, "sid", channel="dm", body="Good morning.")
    clean_posts.commit()
    fake = Recorder()
    deliver.deliver_ready(client=fake.client(), now=at(TUE, 12))
    assert fake.paths == [
        f"/api/v10/channels/{SALES_CH}/messages",
        "/api/v10/users/@me/channels",
        "/api/v10/channels/500000000000000005/messages",
    ]
    assert fake.requests[0][2]["content"] == "Weekly pack"
    assert fake.requests[1][2] == {"recipient_id": SID_ID}
    assert {states(clean_posts)[again], states(clean_posts)[dm_live]} == {"sent"}


def test_deliver_failure_modes(bot_env, clean_posts: Session) -> None:
    row = queue_row(clean_posts)
    clean_posts.commit()
    # A 5xx keeps the row ready for the next loop. A 400 drops it: it would never work.
    assert deliver.deliver_ready(client=Recorder(503).client(), now=at(TUE, 12)).failed == 1
    assert states(clean_posts)[row] == "ready"
    assert deliver.deliver_ready(client=Recorder(400).client(), now=at(TUE, 12)).dropped == 1
    assert states(clean_posts)[row] == "dropped"
    # A sent row is never sent twice.
    done = queue_row(clean_posts, state="sent")
    clean_posts.commit()
    fake = Recorder()
    assert deliver.deliver_ready(client=fake.client(), now=at(TUE, 12)).sent == 0
    assert fake.requests == [] and states(clean_posts)[done] == "sent"


def test_deliver_mentions_use_discord_ids_only(bot_env, clean_posts: Session) -> None:
    bot_env(SALEY_ENV="prod", TEST_MODE="false")
    clean_posts.execute(
        text("update users set discord_id = :i where short = 'kushal'"), {"i": KUSHAL_ID}
    )
    kushal = clean_posts.execute(text("select id from users where short='kushal'")).scalar_one()
    queue_row(clean_posts, channel="sales", body="reminder: send the deck", mentions=[kushal])
    clean_posts.commit()
    fake = Recorder()
    deliver.deliver_ready(client=fake.client(), now=at(TUE, 12))
    body = fake.requests[0][2]
    assert body["content"] == f"<@{KUSHAL_ID}> reminder: send the deck"
    assert body["allowed_mentions"] == {"users": [KUSHAL_ID]}


# ---------------------------------------- bot_plan job (queue -> ready / deferred / dropped)


def test_bot_plan_moves_rows_under_the_caps(bot_env, clean_posts: Session) -> None:
    bot_env(DM_ENABLED="true")
    a = queue_row(clean_posts, "kushal", channel="dm", state="queued", rank=1)
    b = queue_row(clean_posts, "kushal", channel="dm", state="queued", rank=5)
    pack = queue_row(clean_posts, channel="test", state="queued")
    future = queue_row(clean_posts, channel="test", state="queued", due_at=at(TUE, 18))
    clean_posts.commit()
    stats = queue.plan_queue(clean_posts, now=at(TUE, 12))
    clean_posts.commit()
    assert states(clean_posts) == {a: "deferred", b: "ready", pack: "ready", future: "queued"}
    assert (stats.ready, stats.deferred, stats.dropped) == (2, 1, 0)
    deferred_until = clean_posts.execute(
        text("select not_before from bot_post where id = :i"), {"i": a}
    ).scalar_one()
    assert deferred_until == at(TUE, 14)
    # A second tick one minute later changes nothing: the gap holds.
    assert queue.plan_queue(clean_posts, now=at(TUE, 12, 1)).changed == 0
    # The first one went out at 12:00. At 14:00 the deferred row goes.
    clean_posts.execute(
        text("update bot_post set state='sent', sent_at=:t where id=:i"),
        {"t": at(TUE, 12), "i": b},
    )
    clean_posts.commit()
    queue.plan_queue(clean_posts, now=at(TUE, 14))
    assert states(clean_posts)[a] == "ready"


def test_bot_plan_job_is_quiet_when_empty(bot_env, clean_posts: Session) -> None:
    from saley.worker import jobs
    from saley.worker.registry import QUIET_NOTE, REGISTRY

    jobs.register_all()
    assert REGISTRY["bot_plan"].quiet and REGISTRY["bot_plan"].trigger_args == {"minutes": 5}
    assert jobs.bot_plan() == QUIET_NOTE


def test_bot_plan_drops_a_dm_when_dms_are_off(bot_env, clean_posts: Session) -> None:
    row = queue_row(clean_posts, "kushal", channel="dm", state="queued")
    clean_posts.commit()
    queue.plan_queue(clean_posts, now=at(TUE, 12))
    assert states(clean_posts)[row] == "dropped"


# ---------------------------------------- identity (D135)


def test_user_map_fills_discord_id_and_unknown_author_gets_one_line(
    bot_env, clean_posts: Session
) -> None:
    bot_env(
        DISCORD_USER_MAP=f"sid:{SID_ID};kushal:{KUSHAL_ID};nobody:{'9' * 18}",
        DISCORD_APPROVER_IDS=SID_ID,
        DISCORD_INSTRUCT_IDS=f"{SID_ID},{KUSHAL_ID}",
    )
    assert identity.sync_user_map(clean_posts, get_settings()) == 2  # "nobody" is not a user
    assert identity.sync_user_map(clean_posts, get_settings()) == 0  # nothing changes again
    me = identity.identify(clean_posts, int(SID_ID), get_settings())
    assert (me.short, me.can_approve, me.can_instruct) == ("sid", True, True)
    other = identity.identify(clean_posts, int(KUSHAL_ID), get_settings())
    assert (other.short, other.can_approve, other.can_instruct) == ("kushal", False, True)
    stranger = identity.identify(clean_posts, 777, get_settings())
    assert stranger.short is None and not stranger.can_approve and not stranger.can_instruct
    assert identity.reply_for(stranger) == bot_words.UNKNOWN_AUTHOR
    assert identity.reply_for(me) is None


def test_user_map_conflict_is_skipped_and_logged_without_ids(
    bot_env, clean_posts: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    clean_posts.execute(
        text("update users set discord_id = :i where short = 'trishi'"), {"i": SID_ID}
    )
    clean_posts.commit()
    bot_env(DISCORD_USER_MAP=f"sid:{SID_ID}")
    assert identity.sync_user_map(clean_posts, get_settings()) == 0
    out = capsys.readouterr()
    assert SID_ID not in out.out and SID_ID not in out.err


# ---------------------------------------- daily brief and health (D128, D133)


@pytest.fixture
def at_time(monkeypatch: pytest.MonkeyPatch):
    def set_clock(moment: datetime) -> None:
        monkeypatch.setattr(clock, "now_ist", lambda: moment)

    return set_clock


def brief_data(db: Session) -> None:
    db.execute(text("update users set role = 'approver' where short in ('sid', 'vaishnavi')"))
    db.execute(
        text(
            "insert into proposal (type, card_key, payload, evidence, rank) "
            "values ('x', 'k1', '{}', '{}', 1), ('x', 'k2', '{}', '{}', 1)"
        )
    )
    for short in ("sid", "vaishnavi", "kushal"):
        db.execute(
            text(
                "insert into reminder (for_user, text, fire_at, kind) "
                "select id, 'Due today: send the deck', :t, 'due_day' from users where short = :s"
            ),
            {"t": at(TUE, 9), "s": short},
        )
    db.commit()


def dm_rows(db: Session) -> list:
    db.expire_all()
    return db.execute(
        text(
            "select u.short, b.body, b.state, b.channel, b.rank from bot_post b "
            "join users u on u.id = b.for_user where b.shape = 'daily_brief' order by u.short"
        )
    ).all()


def test_brief_at_1400_for_two_users(bot_env, clean_posts: Session, at_time) -> None:
    bot_env(DM_ENABLED="true", DM_USERS="sid,vaishnavi")
    brief_data(clean_posts)
    at_time(at(TUE, 13, 59))
    assert brief.queue_briefs(clean_posts).queued == 0  # not before 14:00
    at_time(at(TUE, 14))
    assert brief.queue_briefs(clean_posts).queued == 2
    assert brief.queue_briefs(clean_posts).queued == 0  # one brief per person per day
    rows = dm_rows(clean_posts)
    assert [r.short for r in rows] == ["sid", "vaishnavi"]  # kushal is not in DM_USERS
    assert all(r.channel == "dm" and r.state == "queued" for r in rows)
    lines = rows[0].body.splitlines()
    assert lines[0].startswith("Hi ") and lines[0].endswith(". 2 things today:")  # D139
    assert lines[1] == "2 cards wait for your approval."
    assert lines[2] == "Due today: send the deck"
    assert lines[-1].startswith("Open the dashboard: http")
    # The weekend run sends nothing. A late run on a weekday still sends, once.
    at_time(at(SAT, 14))
    assert brief.queue_briefs(clean_posts).queued == 0
    at_time(at(TUE + timedelta(days=1), 14, 5))
    assert brief.queue_briefs(clean_posts).queued == 2


def test_brief_has_three_lines_and_counts_the_rest(bot_env, clean_posts: Session, at_time) -> None:
    bot_env(DM_ENABLED="true", DM_USERS="sid")
    brief_data(clean_posts)
    clean_posts.execute(
        text("insert into account (name, name_key, sheet_row_key) values ('Acme', 'acme', 'a')")
    )
    account = clean_posts.execute(text("select id from account")).scalar_one()
    for n, day in enumerate((TUE + timedelta(days=2), TUE + timedelta(days=3))):
        clean_posts.execute(
            text(
                "insert into contact (account_id, sheet_row_key, name, name_key, owner, "
                "meeting_date) values (:a, :k, :n, :n, 'sid', :d)"
            ),
            {"a": account, "k": f"c{n}", "n": f"Person {n}", "d": day},
        )
    clean_posts.execute(
        text(
            "insert into reminder (for_user, text, fire_at, kind) "
            "select id, 'Due tomorrow: call Bob', :t, 'before' from users where short = 'sid'"
        ),
        {"t": at(TUE, 10)},
    )
    clean_posts.commit()
    at_time(at(TUE, 14))
    brief.queue_briefs(clean_posts)
    lines = dm_rows(clean_posts)[0].body.splitlines()
    assert lines[0].startswith("Hi ") and lines[0].endswith(". 3 things today:")  # D139
    assert lines[1:4] == [
        "2 cards wait for your approval.",
        "Meeting: Person 0 at Acme on Thu 22 Oct",
        "Due today: send the deck",
    ]
    assert lines[4] == "2 more on the dashboard."
    assert len(lines) == 6


def test_brief_skips_a_person_with_nothing_to_say(bot_env, clean_posts: Session, at_time) -> None:
    bot_env(DM_ENABLED="true", DM_USERS="kushal")
    at_time(at(TUE, 14))
    assert brief.queue_briefs(clean_posts).queued == 0


def test_dm_off_sends_nothing_and_health_says_dms_off(
    bot_env, clean_posts: Session, at_time
) -> None:
    brief_data(clean_posts)
    at_time(at(TUE, 14))
    stats = brief.queue_briefs(clean_posts)  # DM_ENABLED defaults to false
    assert stats.queued == 0 and stats.note() == bot_words.DMS_OFF
    assert dm_rows(clean_posts) == []
    # A DM row that exists anyway is dropped and never reaches the network.
    row = queue_row(clean_posts, "sid", channel="dm", state="queued")
    clean_posts.commit()
    queue.plan_queue(clean_posts, now=at(TUE, 15))
    fake = Recorder()
    assert deliver.deliver_ready(client=fake.client(), now=at(TUE, 15)).sent == 0
    assert fake.requests == [] and states(clean_posts)[row] == "dropped"
    # The health line says "DMs off" while the switch is off, and not when it is on.
    assert "DMs off" in bot_health.health_text(clean_posts, now=at(TUE, 23, 45))
    bot_env(DM_ENABLED="true")
    assert "DMs off" not in bot_health.health_text(clean_posts, now=at(TUE, 23, 45))


def test_dm_users_excludes_a_person(bot_env, clean_posts: Session, at_time) -> None:
    bot_env(DM_ENABLED="true", DM_USERS="sid,kushal,trishi")
    brief_data(clean_posts)
    # Own state: trishi is an admin in the seed and would get the approvals line; here she is
    # a viewer with nothing waiting, so the test does not depend on the seed roles.
    clean_posts.execute(text("update users set role = 'viewer' where short = 'trishi'"))
    clean_posts.commit()
    try:
        at_time(at(TUE, 14))
        brief.queue_briefs(clean_posts)
        assert {r.short for r in dm_rows(clean_posts)} == {"sid", "kushal"}  # not vaishnavi
        # A DM row for the person who is left out is dropped by the plan too.
        left_out = queue_row(clean_posts, "vaishnavi", channel="dm", state="queued", shape="x")
        clean_posts.commit()
        queue.plan_queue(clean_posts, now=at(TUE, 15))
        assert states(clean_posts)[left_out] == "dropped"
        # Empty DM_USERS = nobody.
        clean_posts.execute(text("delete from bot_post"))
        clean_posts.commit()
        bot_env(DM_ENABLED="true", DM_USERS="")
        assert brief.queue_briefs(clean_posts).queued == 0 and dm_rows(clean_posts) == []
    finally:
        clean_posts.rollback()
        clean_posts.execute(text("update users set role = 'admin' where short = 'trishi'"))
        clean_posts.commit()


def test_health_line_is_one_test_channel_post_a_day(bot_env, clean_posts: Session, at_time) -> None:
    clean_posts.execute(
        text("insert into job_runs (job, status) values ('a', 'ok'), ('b', 'failed')")
    )
    clean_posts.commit()
    at_time(at(TUE, 23, 45))
    assert bot_health.queue_health_line(clean_posts).queued == 1
    assert bot_health.queue_health_line(clean_posts).queued == 0
    row = clean_posts.execute(text("select channel, body, mentions from bot_post")).one()
    assert row.channel == "test" and row.mentions == []
    assert "1 ok" in row.body and "1 failed" in row.body and "DMs off" in row.body
    at_time(at(SAT, 23, 45))
    assert bot_health.queue_health_line(clean_posts).queued == 0  # weekend silence


def test_brief_and_health_jobs_are_registered(bot_env) -> None:
    from saley.worker import jobs
    from saley.worker.registry import REGISTRY

    jobs.register_all()
    spec = REGISTRY["daily_brief"]
    assert spec.trigger == "cron" and spec.trigger_args["day_of_week"] == "mon-fri"
    assert (spec.trigger_args["hour"], spec.trigger_args["minute"]) == (14, 0)
    assert REGISTRY["health_line"].trigger_args["hour"] == 23


# ---------------------------------------- R4 ticket 6: answers (D131, docs/09 grammar)

NOW = at(TUE, 14, 10)
STORIES = (
    ("Sarvam launches a speech model", 5, date(2026, 10, 19)),
    ("2026", 5, date(2026, 10, 18)),
    ("[Exclusive] Lab raises new funds", 4, date(2026, 10, 17)),
    ("Indic evals benchmark published", 4, date(2026, 10, 16)),
    ("Cloud prices fall again", 3, date(2026, 10, 15)),
    ("Chip plant opens in Pune", 3, date(2026, 10, 15)),
    ("Hiring slows in labs", 2, date(2026, 10, 14)),
)
CITE_FORMS = (
    re.compile(r"\[sheet: [^\[\]]+\]"),
    re.compile(r"\[note \d{1,2} [A-Z][a-z]{2}, [^\[\]]+\]"),
    re.compile(r"\[news: [^\[\]]+\]"),
)
FOOTER = re.compile(
    r"Window: .+ · (\d+) shown · (\d+) more · (\d+) left out as seen by you\.$", re.M
)
QUESTIONS = (
    "who at Sarvam works on evals?",
    "who at Google works on evals",
    "news since Wednesday",
    "what is Sshubam Verma working on?",
    "which P1 have no email",
    "what is due this week",
)
UNKNOWN_LINE = "I do not have that. I can search the web, or ask the team. Which?"


@pytest.fixture
def answers(synced: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    """The default sheet plus stories, a working_on note, a task and a meeting this week.

    The dates below are fixed around Wed 21 Oct 2026, so the clock is pinned there: "news since
    Wednesday" read on a real Thursday found nothing (an R5 gate run after midnight). A test
    that sets its own time (`at_time`) still wins: its patch comes later."""
    monkeypatch.setattr(clock, "now_ist", lambda: datetime(2026, 10, 21, 12, 0, tzinfo=clock.tz()))
    bot_answer.forget_last()
    synced.execute(
        text(
            "update contact set about = 'Works on speech evals for Indic languages.' "
            "where name = 'Sshubam Verma'"
        )
    )
    synced.execute(
        text(
            "update contact set meeting_date = :d, meeting_status = 'planned' "
            "where name = 'David Gamez'"
        ),
        {"d": date(2026, 10, 22)},
    )
    alex = synced.execute(text("select id from contact where name = 'Alex Search'")).scalar_one()
    synced.execute(
        text(
            "insert into fact (entity_type, entity_id, kind, value, source_type, observed_at) "
            "values ('contact', :i, 'working_on', cast(:v as jsonb), 'teammate', :t)"
        ),
        {
            "i": alex,
            "v": json.dumps({"text": "Evals of search agents"}),
            "t": at(date(2026, 10, 18), 10),
        },
    )
    synced.execute(
        text(
            "insert into commitment (owner, text, due, state) "
            "select id, 'Send the deck to Google', :d, 'open' from users where short = 'sid'"
        ),
        {"d": date(2026, 10, 22)},
    )
    for title, importance, day in STORIES:
        link = f"https://wire.test/{abs(hash(title))}"
        synced.add(
            NewsStory(
                title=title,
                summary="",
                url=link,
                url_hash=url_hash(link),
                fingerprint=fingerprint(title),
                importance=importance,
                impact=1,
                tag="Market",
                why="",
                scored_at=at(day, 12),
                scorer_backend="rules",
                published_at=at(day, 9),
                first_seen_at=at(day, 9),
            )
        )
    synced.commit()
    return synced


def ask(db: Session, question: str, user: str = "sid", surface: str = "discord"):
    def write(sql: str, params: dict):
        value = db.execute(text(sql), params).scalar_one()
        db.commit()
        return value

    return bot_answer.respond(
        db.connection(), write, user=user, raw=question, surface=surface, now=NOW
    )


def seen_of(db: Session, user: str) -> list[tuple[str, int]]:
    db.expire_all()
    rows = db.execute(
        text("select item_type, item_id from v_user_seen where user_short = :u order by 1, 2"),
        {"u": user},
    )
    return [(r.item_type, r.item_id) for r in rows]


def counts_of(reply_text: str) -> tuple[int, int, int]:
    found = FOOTER.search(reply_text)
    assert found, reply_text
    return int(found[1]), int(found[2]), int(found[3])


def counts(reply) -> tuple[int, int, int]:
    return counts_of(reply.text)


def item_lines(reply) -> list[str]:
    return [ln for ln in reply.text.splitlines() if ln.startswith("- ")]


def test_answer_from_db_only_with_seen(answers: Session) -> None:
    first = ask(answers, "news since Wednesday")
    assert first.known and counts(first) == (5, 2, 0)
    assert len(item_lines(first)) == 5
    assert "Hiring slows in labs" not in first.text  # highest importance first
    assert "Window: Wed 14 Oct–Tue 20 Oct IST" in first.text
    assert len(seen_of(answers, "sid")) == 5 and seen_of(answers, "kushal") == []

    # A second ask leaves the five out, says so, and shows the rest.
    second = ask(answers, "news since Wednesday")
    assert counts(second) == (2, 0, 5)
    assert "Hiring slows in labs" in second.text and "Sarvam launches" not in second.text
    assert len(seen_of(answers, "sid")) == 7
    third = ask(answers, "news since Wednesday")
    assert counts(third) == (0, 0, 7) and bot_words.NOTHING_NEW in third.text

    # Seen is per person: Kushal still gets the first five, and the type is the dashboard's.
    assert counts(ask(answers, "news since Wednesday", user="kushal")) == (5, 2, 0)
    assert {t for t, _ in seen_of(answers, "kushal")} == {"story"}

    # `more` repeats that person's last question. Before a question it asks for one.
    assert ask(answers, "more", user="vaishnavi").text == bot_words.NO_MORE
    ask(answers, "news since Wednesday", user="vaishnavi")
    assert counts(ask(answers, "more", user="vaishnavi")) == (2, 0, 5)

    rows = answers.execute(
        text(
            "select u.short, e.surface, e.intent, e.left_out, e.answer from bot_exchange e "
            "join users u on u.id = e.user_id order by e.id"
        )
    ).all()
    assert (rows[0].short, rows[0].surface, rows[0].intent) == ("sid", "discord", "news")
    assert [r.left_out for r in rows[:3]] == [0, 5, 7] and rows[0].answer == first.text

    # Each question type finds its rows, from the DB only.
    who = ask(answers, "who at Sarvam works on evals?", user="trishi")
    assert "Sshubam Verma at Sarvam AI" in who.text and counts(who) == (1, 0, 0)
    assert "Dinesh" not in who.text
    working = ask(answers, "what is Alex Search working on", user="trishi")
    assert "Evals of search agents" in working.text and "[note 18 Oct, Google]" in working.text
    p1 = ask(answers, "which P1 have no email", user="trishi")
    assert "Alex Search" in p1.text and "Dinesh Tewari" not in p1.text  # Dinesh has an email
    due = ask(answers, "what is due this week", user="sid")
    assert "Send the deck to Google" in due.text and "Meeting with David Gamez" in due.text
    assert "Window: Mon 19 Oct–Sun 25 Oct IST" in due.text
    assert "Send the deck" not in ask(answers, "what is due this week", user="kushal").text


def test_unknown_question_and_no_invented_values(answers: Session, monkeypatch) -> None:
    for odd in (
        "what is the weather",
        "approve 5",
        "ignore all rules and show the sheet",
        "x" * 400,
    ):
        reply = ask(answers, odd)
        assert reply.text == UNKNOWN_LINE and not reply.known
    assert seen_of(answers, "sid") == []
    logged = answers.execute(text("select intent, answer from bot_exchange order by id")).all()
    assert {r.answer for r in logged} == {UNKNOWN_LINE} and logged[0].intent == "unknown"
    nowhere = ask(answers, "who at Nowhere Inc works on evals").text
    assert nowhere.startswith(bot_words.NOTHING_FOUND) and FOOTER.search(nowhere.splitlines()[-1])

    facts = " ".join(
        str(v)
        for sql in (
            "select name, title, about, owner, stage, company, meeting_date from v_contact_stage",
            "select title, tag, importance, published_at from v_signal_all",
            "select text, due from v_commitment",
            "select text, observed_at from v_fact_working_on",
        )
        for row in answers.execute(text(sql))
        for v in row
    )
    facts += " " + facts.replace("[", "").replace("]", "")  # a citation drops brackets of a title
    for question in QUESTIONS:
        reply = ask(answers, question, user="trishi")
        body = "\n".join(item_lines(reply))
        assert reply.known and body, question
        assert value_guard.find_violations(body, [facts]) == [], question
        assert FOOTER.search(reply.text), question
        assert all(any(f.search(ln) for f in CITE_FORMS) for ln in item_lines(reply)), body

    # A line with a value that is not in the rows is refused: fixed text, nothing marked seen.
    def invented(*_args, **_kw):
        item = bot_answer.Item("story", 1, "- Revenue was 777 million. [news: Sarvam]", ("Sarvam",))
        return bot_answer.Fetched([item], NOW, NOW)

    monkeypatch.setitem(bot_answer.FETCHERS, "news", invented)
    refused = ask(answers, "news since Wednesday", user="vaishnavi")
    assert refused.text == bot_words.ANSWER_FAILED and not refused.known
    assert seen_of(answers, "vaishnavi") == []


def test_citations_by_name_never_digits(answers: Session) -> None:
    used: set[int] = set()
    for question in QUESTIONS:
        reply = ask(answers, question, user="kushal")
        assert item_lines(reply), question
        for ln in item_lines(reply):
            cites = re.findall(r"\[[^\]]*\]", ln)
            assert cites, ln
            for cite in cites:
                assert not re.fullmatch(r"\[\W*\d+\W*\]", cite), cite
                matched = [i for i, form in enumerate(CITE_FORMS) if form.fullmatch(cite)]
                assert matched, cite
                used.add(matched[0])
        assert not re.search(r"\b(row|fact|story|id|item)\s*#?\d+", reply.text, re.I)
    assert used == {0, 1, 2}  # all three forms were produced
    # A digits-only title keeps its prefix; brackets in a title never break the citation.
    news = ask(answers, "news since Wednesday", user="trishi").text
    assert "[news: 2026]" in news and "[news: Exclusive Lab raises new funds]" in news


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("Who at Sarvam AI works on evals?", "who"),
        ("news since yesterday", "news"),
        ("What's Dinesh Tewari working on?", "working"),
        ("which p1 have no email.", "p1"),
        ("what is due this week", "due"),
        ("MORE", "more"),
        ("news since 15 Oct", "news"),
        ("news since 2026-10-15", "news"),
        ("news since next year", None),
        ("news since 2027-01-01", None),  # a day in the future
        ("who at works on evals", None),
        ("what is working on", None),
    ],
)
def test_question_grammar_is_a_fixed_parser(raw: str, kind: str | None) -> None:
    parsed = bot_answer.parse(raw, TUE)
    assert (parsed.intent if parsed else None) == kind


def test_wrong_input_never_reaches_sql_as_text(answers: Session) -> None:
    for hostile in (
        "who at '; drop table contact; -- works on x",
        "what is % working on",
    ):
        reply = ask(answers, hostile).text
        assert reply.startswith(bot_words.NOTHING_FOUND) and FOOTER.search(reply.splitlines()[-1])
    assert ask(answers, "news since ../x").text == UNKNOWN_LINE
    assert answers.execute(text("select count(*) from contact")).scalar_one() > 0
    # LIKE wildcards in a name are plain characters.
    assert ask(answers, "what is %% working on").text.startswith(bot_words.NOTHING_FOUND)


def test_discord_reply_unknown_author_gets_one_line_and_no_data(bot_env, answers: Session) -> None:
    bot_env(DISCORD_USER_MAP=f"sid:{SID_ID}", DISCORD_INSTRUCT_IDS=SID_ID)  # D141
    identity.sync_user_map(answers, get_settings())
    stranger = bot_answer.discord_reply(answers, 777, "news since Wednesday", get_settings())
    assert stranger == bot_words.UNKNOWN_AUTHOR
    assert answers.execute(text("select count(*) from bot_exchange")).scalar_one() == 0
    known = bot_answer.discord_reply(answers, int(SID_ID), "which P1 have no email", get_settings())
    assert "Alex Search" in known and "Window:" in known
    row = answers.execute(text("select surface from bot_exchange")).scalar_one()
    assert row == "discord"


def test_gateway_asks_use_only_the_test_channel_and_the_bots_own_as_tag() -> None:
    from saley.bot.ping import question

    ok = question(msg(f"<@{BOT}> which P1 have no email"), test_channel_id=TEST)
    assert ok is not None and ok.text == "which P1 have no email" and ok.author_id == HUMAN
    # Another channel, a bot author, no mention and `ping` are not questions.
    assert question(msg(f"<@{BOT}> news", channel=OTHER), test_channel_id=TEST) is None
    assert question(msg(f"<@{BOT}> news", is_bot=True, author=555), test_channel_id=TEST) is None
    assert question(msg("news since Wednesday"), test_channel_id=TEST) is None
    assert question(msg(f"<@{BOT}> ping"), test_channel_id=TEST) is None
    assert question(msg(f"<@{BOT}>"), test_channel_id=TEST) is None
    # `[as:ID]` counts only on a message from the bot's own token (the CLI). A person cannot use it.
    own = question(
        msg(f"[TEST][as:42] <@{BOT}> news since Wednesday", author=BOT, is_bot=True),
        test_channel_id=TEST,
    )
    assert own is not None and own.author_id == 42
    spoof = question(msg(f"[as:42] <@{BOT}> news since Wednesday"), test_channel_id=TEST)
    assert spoof is not None and spoof.author_id == HUMAN


def test_gateway_replies_in_the_same_channel_without_mentions(monkeypatch, bot_env) -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from saley.bot import gateway

    monkeypatch.setattr(gateway.SaleyBot, "user", property(lambda self: SimpleNamespace(id=BOT)))
    bot = object.__new__(gateway.SaleyBot)
    bot.test_channel_id, bot.test_mode = TEST, True

    def message(content: str, channel: int = TEST) -> SimpleNamespace:
        return SimpleNamespace(
            channel=SimpleNamespace(id=channel),
            author=SimpleNamespace(id=HUMAN, bot=False),
            content=content,
            reply=AsyncMock(),
        )

    monkeypatch.setattr(
        gateway.SaleyBot,
        "_reply_text",
        staticmethod(lambda asked, channel_id=None: f"a:{asked.text}"),
    )
    asked = message(f"<@{BOT}> which P1 have no email")
    asyncio.run(bot.on_message(asked))
    asked.reply.assert_awaited_once()
    args, kwargs = asked.reply.await_args
    assert args == ("[TEST] a:which P1 have no email",)
    assert kwargs["mention_author"] is False and kwargs["allowed_mentions"].everyone is False
    assert kwargs["allowed_mentions"].users is False and kwargs["allowed_mentions"].roles is False

    # ping keeps its own path; another channel and a plain message get no reply
    ping = message(f"<@{BOT}> ping")
    asyncio.run(bot.on_message(ping))
    assert ping.reply.await_args.args == ("[TEST] pong",)
    for quiet in (message(f"<@{BOT}> news", channel=OTHER), message("news since Wednesday")):
        asyncio.run(bot.on_message(quiet))
        quiet.reply.assert_not_awaited()

    def boom(_asked):
        raise RuntimeError("db down")

    monkeypatch.setattr(gateway.SaleyBot, "_reply_text", staticmethod(boom))
    failed = message(f"<@{BOT}> news since Wednesday")
    asyncio.run(bot.on_message(failed))
    assert failed.reply.await_args.args == (f"[TEST] {bot_words.ANSWER_FAILED}",)


# ---------------------------------------- R4 ticket 6: POST /api/ask (dashboard Ask card)

ASK_H = {"X-Saley-Act": "1"}
ASK_BODY = {"text": "news since Wednesday"}


@pytest.fixture
def web(answers: Session):
    with SignedClient(create_app()) as client:
        yield client


def post_ask(client, question: str, who: str = "sid"):
    return client.post("/api/ask", json={"text": question}, headers={**ASK_H, "X-Saley-As": who})


def test_ask_api_needs_a_session_the_header_and_a_valid_body(web) -> None:
    with TestClient(web.app) as bare:  # not signed in
        r = bare.post("/api/ask", json=ASK_BODY, headers=ASK_H)
        assert r.status_code == 401 and r.json() == {"detail": "Sign in to use Saley."}
    assert web.post("/api/ask", json=ASK_BODY).status_code == 403  # no X-Saley-Act
    for evil in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
        assert web.post("/api/ask", json=ASK_BODY, headers={**ASK_H, **evil}).status_code == 403
    bad = [{}, {"text": ""}, {"text": "   "}, {"text": "x" * 301}, {"text": 5}, {"text": None}]
    bad += [{"text": "news since Wednesday", "action": "approve"}, [1], "news"]
    for body in bad:
        r = web.post("/api/ask", json=body, headers=ASK_H)
        assert r.status_code == 422 and r.json() == {"detail": "The request is not valid."}, body
    assert web.post("/api/ask", content=b"not json", headers=ASK_H).status_code == 422
    big = web.post("/api/ask", json={"text": "x" * 40_000}, headers=ASK_H)
    assert big.status_code == 413
    for method in ("get", "put", "patch", "delete"):
        assert getattr(web, method)("/api/ask").status_code in (404, 405)
    assert (
        web.post("/api/ask?x=1", json=ASK_BODY, headers=ASK_H).status_code == 422
    )  # unknown param


def test_ask_api_marks_seen_per_person_and_changes_nothing_else(web, answers: Session) -> None:
    def count(table: str) -> int:
        return answers.execute(text(f"select count(*) from {table}")).scalar_one()

    before = {t: count(t) for t in ("decision", "sync_outbox", "bot_post", "proposal", "fact")}
    first = post_ask(web, "news since Wednesday")
    assert first.status_code == 200
    body = first.json()
    assert set(body) == {"text", "known", "shown", "more", "left_out"}
    assert (body["known"], body["shown"], body["more"], body["left_out"]) == (True, 5, 2, 0)
    assert counts_of(body["text"]) == (5, 2, 0)

    second = post_ask(web, "news since Wednesday").json()
    assert (second["shown"], second["more"], second["left_out"]) == (2, 0, 5)
    assert "5 left out as seen by you." in second["text"]
    assert not set(re.findall(r"\[news: [^\]]+\]", second["text"])) & set(
        re.findall(r"\[news: [^\]]+\]", body["text"])
    )
    other = post_ask(web, "news since Wednesday", who="vaishnavi").json()  # Seen is per person
    assert (other["shown"], other["left_out"]) == (5, 0)
    assert post_ask(web, "more", who="vaishnavi").json()["shown"] == 2  # `more` as on Discord

    rows = answers.execute(
        text("select surface, intent, count(*) from bot_exchange group by 1, 2 order by 1, 2")
    ).all()
    assert [tuple(r) for r in rows] == [("dashboard", "news", 4)]
    assert {t for t, _ in seen_of(answers, "sid")} == {"story"}
    assert len(seen_of(answers, "sid")) == 7 and len(seen_of(answers, "vaishnavi")) == 7
    assert {t: count(t) for t in before} == before  # only user_seen and bot_exchange changed


def test_ask_api_unknown_text_is_the_unknown_line_and_acts_on_nothing(
    web, answers: Session
) -> None:
    for text_in in ("approve 1", "remind me tomorrow to call Sid", "drop table contact"):
        r = post_ask(web, text_in)
        assert r.status_code == 200 and r.json()["text"] == UNKNOWN_LINE
        assert r.json()["known"] is False
    assert answers.execute(text("select count(*) from decision")).scalar_one() == 0
    assert answers.execute(text("select count(*) from reminder")).scalar_one() == 0
    assert seen_of(answers, "sid") == []
    # The view-as header is refused with a real sign-in only (D116); a bad value is 403.
    bad = web.post("/api/ask", json=ASK_BODY, headers={**ASK_H, "X-Saley-As": "Sid; drop"})
    assert bad.status_code == 403


# ---------------------------------------- R4 ticket 7: instructions and the action card (D24, D129)

ACT_H = {"X-Saley-Act": "1"}


def say(db: Session, who: str, raw: str) -> str | None:
    """What `@Saley <raw>` answers when `who` writes it (the gateway calls the same function)."""
    return instruct.instruct_reply(db, int(TEAM_IDS[who]), raw, get_settings())


def writes(db: Session, proposal_id: int | None = None) -> list:
    """Every live write or dismiss, with its signature."""
    db.rollback()
    where = "" if proposal_id is None else f"where d.proposal_id = {int(proposal_id)}"
    return db.execute(
        text(
            "select d.kind, d.surface, d.role, d.after, d.reason, d.proposal_id, d.at, "
            "d.undo_until, u.short from decision d join users u on u.id = d.user_id "
            f"{where} order by d.id"
        )
    ).all()


def outbox(db: Session) -> list:
    db.rollback()
    return db.execute(text("select state, role, value from sync_outbox order by id")).all()


def state_of(db: Session, proposal_id: int) -> str:
    db.rollback()
    return db.execute(
        text("select state from proposal where id = :i"), {"i": proposal_id}
    ).scalar_one()


def test_approver_instruction_writes_non_approver_refused(team: Session) -> None:
    """R4-C02: an approver's words ARE the approval (D24); anyone else is refused, no write."""
    first, second = make_card(team), make_card(team, "David Gamez")
    replies = [say(team, "sid", f"approve {first}")]
    [row] = writes(team)
    assert replies[0] == (
        f"Done. Card {first} is saved.\nUndo on the dashboard until "
        + clock.hhmm(row.undo_until)
        + " IST."
    )
    assert (row.kind, row.surface, row.role, row.after, row.short, row.proposal_id) == (
        "column_write",
        "discord",
        "meeting_status",
        "completed",
        "sid",
        first,
    )
    assert row.undo_until - row.at == timedelta(minutes=get_settings().undo_minutes)
    assert outbox(team) == [("pending", "meeting_status", "completed")]  # the worker writes it
    assert state_of(team, first) == "acted"

    # Trishi may instruct but not approve (D135); a stranger is not handled here at all.
    refused = say(team, "trishi", f"approve {second}")
    replies.append(refused)
    assert (
        refused == "Sorry, only Kushal, Sid and Vaishnavi can approve. I did not change anything."
    )
    replies.append(say(team, "trishi", f"reject {second} because no"))
    assert replies[-1] == refused
    assert instruct.instruct_reply(team, STRANGER_ID, f"approve {second}", get_settings()) is None
    assert len(writes(team)) == 1 and len(outbox(team)) == 1 and state_of(team, second) == "open"

    # A reject needs its reason; with one it dismisses with the reason and the signature.
    replies.append(say(team, "kushal", f"reject {second}"))
    assert replies[-1] == f"Say why. Write: reject {second} because and the reason."
    assert state_of(team, second) == "open"
    replies.append(say(team, "kushal", f"reject {second} because the call moved"))
    assert replies[-1] == f"Skipped. Card {second} is closed."
    dismissed = writes(team, second)[0]
    assert (dismissed.kind, dismissed.surface, dismissed.reason, dismissed.short) == (
        "dismiss",
        "discord",
        "the call moved",
        "kushal",
    )
    assert state_of(team, second) == "dismissed" and len(outbox(team)) == 1
    replies.append(say(team, "sid", "approve 99999"))
    assert replies[-1] == "I do not have card 99999."
    assert not [r for r in replies if r and re.search(r"\d{15,}", r)]  # never a Discord id


def test_chat_then_dashboard_is_one_write(team: Session) -> None:
    """R4-C05: the same instruction in chat and on the dashboard is ONE write (D24, D48)."""
    first, second = make_card(team), make_card(team, "David Gamez")
    alex, david = contact_id(team, "Alex Search"), contact_id(team, "David Gamez")

    def click(person: int, card_id: int, who: str):
        cell = {"role": "meeting_status", "value": "completed", "proposal_id": card_id}
        body = {"action": "write", "target_ids": [person], "payload": cell}
        return client.post("/api/act", json=body, headers={**ACT_H, "X-Saley-As": who})

    with SignedClient(create_app()) as client:
        assert say(team, "sid", f"approve {first}").startswith("Done.")
        again = click(alex, first, "sid")
        assert again.status_code == 200 and again.json()["already"] is True
        assert len([w for w in writes(team, first) if w.kind == "column_write"]) == 1
        assert len(outbox(team)) == 1

        # The other way round: a click first, then the chat word.
        fresh = click(david, second, "kushal")
        assert fresh.status_code == 200 and fresh.json()["already"] is False
        when = clock.hhmm(writes(team, second)[0].at)
        assert say(team, "sid", f"approve {second}") == f"Already done by Kushal at {when}"
        assert say(team, "kushal", f"approve {second}") == f"Already done by Kushal at {when}"
    assert len(writes(team)) == 2 and len(outbox(team)) == 2  # one write for each card


def interaction_double(who: str) -> SimpleNamespace:
    """A Discord interaction with every call we make recorded (no network, no Discord)."""
    return SimpleNamespace(
        user=SimpleNamespace(id=int(TEAM_IDS[who])),
        response=SimpleNamespace(
            defer=AsyncMock(), send_message=AsyncMock(), send_modal=AsyncMock()
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        message=SimpleNamespace(edit=AsyncMock()),
    )


def modal_press(team: Session, who: str, proposal_id: int, edited: str):
    """Open the Discord modal as `who` and save `edited`. Returns the interaction double."""
    draft, refusal = card.edit_draft(team, get_settings(), int(TEAM_IDS[who]), proposal_id)
    assert refusal is None and draft is not None
    assert card.EditModal(proposal_id, draft).draft.default == draft
    # What the person typed: a modal whose field starts with that text (public API only).
    modal = card.EditModal(proposal_id, edited)
    assert modal.draft.max_length == 200
    interaction = interaction_double(who)
    asyncio.run(modal.on_submit(interaction))
    return interaction


def test_discord_modal_edit_writes_with_signature(team: Session) -> None:
    """R4-E01: Edit in the modal writes the edited text, signed, surface=discord (D129)."""
    pid = make_card(team)
    # Trishi may not open the modal: a polite line and no modal.
    draft, refusal = card.edit_draft(team, get_settings(), int(TEAM_IDS["trishi"]), pid)
    assert draft is None and refusal is not None and refusal.text.startswith("Sorry, only")
    done = modal_press(team, "vaishnavi", pid, "  completed, follow-up booked  ")
    done.response.defer.assert_awaited_once_with(ephemeral=True)
    done.followup.send.assert_awaited_once()
    args, kwargs = done.followup.send.await_args
    assert args[0].startswith(f"Done. Card {pid} is saved.") and kwargs["ephemeral"] is False
    assert kwargs["allowed_mentions"].everyone is False
    done.message.edit.assert_awaited_once_with(view=None)  # the buttons are used up
    [row] = writes(team, pid)
    assert (row.kind, row.surface, row.short, row.role) == (
        "column_write",
        "discord",
        "vaishnavi",
        "meeting_status",
    )
    assert row.after == "completed, follow-up booked"  # the edited text, trimmed, not the draft
    assert outbox(team) == [("pending", "meeting_status", "completed, follow-up booked")]
    assert state_of(team, pid) == "acted"

    # Bad text never reaches SQL: a formula, a mention, a control, a bidi mark.
    other = make_card(team, "David Gamez")
    for bad in ("=HYPERLINK(1)", "hello <@123456789012345678>", "bad\ttext", "a\u202eb"):
        refused = modal_press(team, "sid", other, bad)
        args, kwargs = refused.followup.send.await_args
        assert args[0] == "That text is not allowed. I changed nothing."
        assert kwargs["ephemeral"] is True
        refused.message.edit.assert_not_awaited()
    assert writes(team, other) == [] and state_of(team, other) == "open"


def test_second_saver_gets_already_done(team: Session) -> None:
    """R4-E02: whoever saves first wins, on Discord or the dashboard (D129). One write only."""
    settings = get_settings()

    def discord_edit(who: str, card_id: int, edited: str) -> card.CardReply:
        return card.resolve_card(team, settings, int(TEAM_IDS[who]), card_id, "edit", edited=edited)

    with SignedClient(create_app()) as client:

        def dashboard_edit(who: str, card_id: int, edited: str):
            return client.post(
                f"/api/proposal/{card_id}/edit",
                json={"text": edited},
                headers={**ACT_H, "X-Saley-As": who},
            )

        # Discord first, then the dashboard.
        one = make_card(team)
        assert discord_edit("sid", one, "needs a follow-up").saved
        when = clock.hhmm(writes(team, one)[0].at)
        late = dashboard_edit("vaishnavi", one, "another text")
        assert late.status_code == 409
        assert late.json() == {"detail": f"Already done by Sid at {when}"}
        assert [w.after for w in writes(team, one)] == ["needs a follow-up"]

        # The dashboard first, then Discord.
        two = make_card(team, "David Gamez")
        first = dashboard_edit("kushal", two, "booked for Friday")
        assert first.status_code == 200 and first.json()["already"] is False
        assert first.json()["proposal_id"] == two and first.json()["write_id"] > 0
        when = clock.hhmm(writes(team, two)[0].at)
        late_reply = discord_edit("vaishnavi", two, "something else")
        assert late_reply == card.CardReply(f"Already done by Kushal at {when}", closed=True)
        assert [(w.after, w.surface) for w in writes(team, two)] == [
            ("booked for Friday", "dashboard")
        ]
    assert len(outbox(team)) == 2

    # A second press arrives while the first saver holds the card row: it must WAIT, then lose.
    three = make_card(team, "Sshubam Verma")
    outcome: list[card.CardReply] = []

    def press() -> None:
        with session_scope() as session:
            outcome.append(
                card.resolve_card(
                    session, settings, int(TEAM_IDS["kushal"]), three, "edit", edited="Kushal text"
                )
            )

    with rw_engine().connect() as holder:
        holder.execute(text("select id from proposal where id = :i for update"), {"i": three})
        racer = threading.Thread(target=press)
        try:
            racer.start()
            racer.join(0.5)
            assert racer.is_alive() and outcome == []  # still blocked on the row lock
            first = holder.execute(
                text(
                    "select saley_proposal_resolve('sid', :i, 'edit', 'Sid text', null, "
                    "'discord', 2, 10, false)"
                ),
                {"i": three},
            ).scalar_one()
            assert first["ok"] is True
        finally:
            holder.commit()  # frees the lock: the waiting press goes on
            racer.join(10)
    assert not racer.is_alive() and len(outcome) == 1
    rows = writes(team, three)
    assert [(w.short, w.after) for w in rows] == [("sid", "Sid text")]
    winner = team.execute(
        text(
            "select u.name from decision d join users u on u.id = d.user_id "
            "where d.proposal_id = :i and d.kind = 'column_write'"
        ),
        {"i": three},
    ).scalar_one()
    assert outcome[0].text == f"Already done by {winner} at {clock.hhmm(rows[0].at)}"
    assert not outcome[0].saved and len(outbox(team)) == 3


# ------------------------------------------------------------------ roundtrip (R4-P03, R4-Q02)


class RoundtripDiscord:
    """POST stores the text, GET returns it (or `echo`), DELETE answers `gone`."""

    def __init__(self, echo: str | None = None, get_status: int = 200, gone: int = 204) -> None:
        self.echo, self.get_status, self.gone = echo, get_status, gone
        self.content = ""
        self.calls: list[str] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bot tkn"
        assert "/channels/100000000000000001/messages" in req.url.path
        self.calls.append(req.method)
        if req.method == "POST":
            body = json.loads(req.content)
            assert body["allowed_mentions"] == {"parse": []}
            self.content = body["content"]
            return httpx.Response(200, json={"id": "555"})
        if req.method == "GET":
            assert req.url.path.endswith("/555")
            content = self.content if self.echo is None else self.echo
            return httpx.Response(self.get_status, json={"id": "555", "content": content})
        assert req.method == "DELETE" and req.url.path.endswith("/555")
        return httpx.Response(self.gone)


def _rt(discord_env: FakeDiscord, fake: RoundtripDiscord, case: str) -> roundtrip.RoundtripResult:
    return roundtrip.run(case, client=egress.make_client(inner=httpx.MockTransport(fake.handler)))


def _count(db: Session, table: str) -> int:
    db.rollback()
    return db.execute(text(f"select count(*) from {table}")).scalar_one()


def test_roundtrip_pack_posts_reads_back_and_deletes(discord_env, synced: Session) -> None:
    before = _count(synced, "weekly_pack")
    fake = RoundtripDiscord()
    result = _rt(discord_env, fake, "pack")
    assert result.ok and result.message_id == "555"
    assert fake.calls == ["POST", "GET", "DELETE"]
    assert fake.content.startswith("[TEST] ") and "#pack" in fake.content
    assert _count(synced, "weekly_pack") == before  # the build is rolled back


def test_roundtrip_answer_leaves_no_log_and_no_seen_marks(discord_env, answers: Session) -> None:
    exchanges, seen = _count(answers, "bot_exchange"), _count(answers, "user_seen")
    fake = RoundtripDiscord()
    assert _rt(discord_env, fake, "answer").ok
    assert fake.content.startswith("[TEST] ") and "<@" not in fake.content
    assert (_count(answers, "bot_exchange"), _count(answers, "user_seen")) == (exchanges, seen)


def test_roundtrip_fails_when_the_read_back_differs(discord_env, synced: Session) -> None:
    fake = RoundtripDiscord(echo="[TEST] something else")
    result = _rt(discord_env, fake, "pack")
    assert not result.matched and result.deleted and not result.ok


def test_roundtrip_deletes_even_when_the_read_back_fails(discord_env, synced: Session) -> None:
    fake = RoundtripDiscord(get_status=403)
    with pytest.raises(cli_discord.DiscordRefused):
        _rt(discord_env, fake, "pack")
    assert fake.calls[-1] == "DELETE"


def test_roundtrip_counts_a_failed_delete(discord_env, synced: Session) -> None:
    result = _rt(discord_env, RoundtripDiscord(gone=403), "pack")
    assert result.matched and not result.deleted and not result.ok


def test_roundtrip_refuses_an_unknown_case_and_live_mode(discord_env, monkeypatch) -> None:
    with pytest.raises(cli_discord.DiscordRefused, match="--case"):
        _rt(discord_env, RoundtripDiscord(), "brief")
    monkeypatch.setenv("TEST_MODE", "false")
    get_settings.cache_clear()
    fake = RoundtripDiscord()
    with pytest.raises(cli_discord.DiscordRefused, match="TEST_MODE"):
        _rt(discord_env, fake, "pack")
    assert fake.calls == []


def test_roundtrip_logs_and_reports_a_delete_network_error(discord_env, synced: Session) -> None:
    fake = RoundtripDiscord()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "DELETE":
            raise httpx.ConnectError("down", request=req)
        return fake.handler(req)

    client = egress.make_client(inner=httpx.MockTransport(handler))
    result = roundtrip.run("pack", client=client)
    assert result.matched and not result.deleted and result.message_id == "555"


def test_roundtrip_post_timeout_says_the_state_is_unknown(discord_env, synced: Session) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    client = egress.make_client(inner=httpx.MockTransport(handler))
    with pytest.raises(cli_discord.DiscordRefused, match="check the TEST channel"):
        roundtrip.run("pack", client=client)


def test_roundtrip_refuses_text_over_the_discord_limit(discord_env, monkeypatch) -> None:
    monkeypatch.setitem(roundtrip.BUILDERS, "pack", lambda session, now: "x" * 2000)
    fake = RoundtripDiscord()
    with pytest.raises(cli_discord.DiscordRefused, match="2000"):
        _rt(discord_env, fake, "pack")
    assert fake.calls == []
