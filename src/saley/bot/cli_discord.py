"""`saley discord send/read --test`: the Claude Code test loop over Discord REST.

Why: CLAUDE.md lets Claude Code post to and read from the TEST channel only. Both commands
use DISCORD_TEST_CHANNEL_ID, refuse when TEST_MODE is false, and go through egress.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from saley.bot.ping import TEST_TAG
from saley.core import egress
from saley.core.settings import get_settings

API = "https://discord.com/api/v10"
MAX_READ = 100
MAX_CONTENT = 2000


class DiscordRefused(RuntimeError):
    """The command would leave the test loop, or Discord said no."""


@dataclass(frozen=True)
class ReadMessage:
    at: str
    author: str
    content: str


def _guard() -> tuple[str, str]:
    settings = get_settings()
    if not settings.test_mode:
        raise DiscordRefused("TEST_MODE is false. Discord commands work only in test mode.")
    settings.require("discord")
    channel = settings.discord_test_channel_id.strip()
    if not re.fullmatch(r"\d{17,20}", channel):
        raise DiscordRefused("DISCORD_TEST_CHANNEL_ID must be a Discord id (digits only).")
    if channel == settings.discord_sales_channel_id.strip():
        raise DiscordRefused("DISCORD_TEST_CHANNEL_ID equals the live sales channel. Refused.")
    return settings.discord_bot_token, channel


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bot {token}", "User-Agent": "saley-v2 (test loop)"}


def _json(response: httpx.Response) -> Any:
    if response.status_code >= 400:
        raise DiscordRefused(f"Discord answered HTTP {response.status_code}")
    return response.json()


def replace_mention(text: str, bot_id: str) -> str:
    """'@Saley ping' → '<@123> ping', so the bot sees a real mention."""
    out, low, i = [], text.casefold(), 0
    while (j := low.find("@saley", i)) != -1:
        out += [text[i:j], f"<@{bot_id}>"]
        i = j + len("@saley")
    return "".join(out) + text[i:]


def send(text: str, as_user: str | None = None, client: httpx.Client | None = None) -> str:
    """Post '[TEST] <text>' to the test channel. Returns the message id."""
    token, channel = _guard()
    allowed = get_settings().test_user_ids
    if as_user is not None and as_user not in allowed:
        raise DiscordRefused("--as must be one of DISCORD_TEST_USER_IDS")
    with client or egress.make_client() as http:
        if "@saley" in text.casefold():
            me = _json(egress.request(http, "GET", f"{API}/users/@me", headers=_headers(token)))
            text = replace_mention(text, str(me["id"]))
        tag = TEST_TAG + (f"[as:{as_user}]" if as_user else "")
        body = {"content": f"{tag} {text}"[:MAX_CONTENT], "allowed_mentions": {"parse": []}}
        url = f"{API}/channels/{channel}/messages"
        sent = _json(egress.request(http, "POST", url, headers=_headers(token), json=body))
    return str(sent["id"])


def read(last: int, client: httpx.Client | None = None) -> list[ReadMessage]:
    """Last N messages of the test channel, oldest first."""
    token, channel = _guard()
    limit = max(1, min(last, MAX_READ))
    with client or egress.make_client() as http:
        items = _json(
            egress.request(
                http,
                "GET",
                f"{API}/channels/{channel}/messages",
                headers=_headers(token),
                params={"limit": limit},
            )
        )
    return [
        ReadMessage(
            str(m.get("timestamp", ""))[:19],
            str(m.get("author", {}).get("username", "?")),
            str(m.get("content", "")),
        )
        for m in reversed(items)
    ]
