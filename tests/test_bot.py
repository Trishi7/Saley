"""saley.bot: the ping rule and the Discord test-loop CLI functions (no network)."""

from __future__ import annotations

import json

import httpx
import pytest

from saley.bot import cli_discord
from saley.bot.ping import Incoming, decide
from saley.core import egress
from saley.core.settings import get_settings

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
