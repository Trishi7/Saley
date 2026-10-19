"""Decide if the bot answers a message. Pure function, so tests need no Discord.

Why: decision D27 — until message shapes are agreed, the bot answers only `@Saley ping`,
only in the TEST channel. The CLI posts test messages with the bot's own token, so a bot
message is accepted only when it carries the `[TEST]` tag and mentions the bot. Our own
replies never mention the bot, so the bot cannot answer itself in a loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TEST_TAG = "[TEST]"
_AS = re.compile(r"\[as:(\d+)\]")
_MENTION = re.compile(r"<@!?(\d+)>")


@dataclass(frozen=True)
class Incoming:
    channel_id: int
    author_id: int
    author_is_bot: bool
    content: str
    bot_user_id: int


@dataclass(frozen=True)
class Reply:
    text: str
    as_user: int | None


def _command(content: str, bot_user_id: int) -> str | None:
    """Text after the @Saley mention, or None when Saley is not addressed."""
    body = _AS.sub(" ", content.replace(TEST_TAG, " ")).strip()
    mention = re.match(rf"<@!?{bot_user_id}>\s*", body)
    if mention:
        return body[mention.end() :].strip()
    plain = re.match(r"@saley\b\s*", body, re.IGNORECASE)
    if plain:
        return body[plain.end() :].strip()
    return None


def _accept_author(msg: Incoming) -> bool:
    if msg.author_id == msg.bot_user_id:
        return msg.content.startswith(TEST_TAG) and str(msg.bot_user_id) in _MENTION.findall(
            msg.content
        )
    return not msg.author_is_bot


def decide(msg: Incoming, *, test_channel_id: int, test_mode: bool) -> Reply | None:
    """'pong' for '@Saley ping' in the test channel. None for everything else."""
    if msg.channel_id != test_channel_id or not _accept_author(msg):
        return None
    command = _command(msg.content, msg.bot_user_id)
    if command is None or command.casefold() != "ping":
        return None
    as_match = _AS.search(msg.content)
    prefix = f"{TEST_TAG} " if test_mode else ""
    return Reply(f"{prefix}pong", int(as_match.group(1)) if as_match else None)
