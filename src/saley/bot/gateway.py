"""discord.py gateway: log in, log the guild and test channel names, answer ping and questions.

Note: the gateway is a websocket that discord.py opens itself, so it cannot use the httpx
egress transport. discord.com is on the allowlist; REST calls from the CLI use egress.
"""

from __future__ import annotations

import asyncio

import discord
from discord.ext import tasks

from saley.bot import words as bw
from saley.bot.answer import discord_reply
from saley.bot.card import CardButton
from saley.bot.deliver import deliver_ready
from saley.bot.identity import sync_user_map
from saley.bot.instruct import instruct_reply
from saley.bot.ping import TEST_TAG, Incoming, Question, decide, question
from saley.core.db import session_scope
from saley.core.logging import get_logger
from saley.core.settings import get_settings

MAX_REPLY = 1990  # Discord allows 2000 characters in a message
DELIVER_SECONDS = 15  # how often ready bot_post rows are sent (D132: the worker only plans)

log = get_logger("saley.bot")


class SaleyBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        settings = get_settings()
        self.test_channel_id = int(settings.discord_test_channel_id)
        self.guild_id = int(settings.discord_guild_id)
        self.test_mode = settings.test_mode

    async def setup_hook(self) -> None:
        """Map Discord ids to people once, register the card buttons, start the delivery loop.

        The buttons are registered by pattern (`card:<id>:<act>`), so a card posted before a
        restart still works (D129). Never runs in tests.
        """
        await asyncio.to_thread(self._sync_identity)
        self.add_dynamic_items(CardButton)
        self.deliver_loop.start()

    @staticmethod
    def _sync_identity() -> None:
        with session_scope() as session:
            changed = sync_user_map(session, get_settings())
        log.info("bot_user_map", changed=changed)

    @tasks.loop(seconds=DELIVER_SECONDS)
    async def deliver_loop(self) -> None:
        """Send ready rows. A failure is logged and the loop keeps going."""
        try:
            stats = await asyncio.to_thread(deliver_ready)
        except Exception:
            log.exception("bot_deliver_failed")
            return
        if stats.sent or stats.dropped or stats.failed:
            log.info("bot_delivered", sent=stats.sent, dropped=stats.dropped, failed=stats.failed)

    @deliver_loop.before_loop
    async def _before_deliver(self) -> None:
        await self.wait_until_ready()

    async def on_ready(self) -> None:
        guild = self.get_guild(self.guild_id)
        channel = self.get_channel(self.test_channel_id)
        log.info(
            "bot_ready",
            user=str(self.user),
            guild=guild.name if guild else "not found",
            test_channel=getattr(channel, "name", "not found"),
            test_mode=self.test_mode,
        )
        if channel is None:
            log.error("bot_test_channel_missing", channel_id=self.test_channel_id)
            await self.close()

    async def on_message(self, message: discord.Message) -> None:
        if self.user is None:
            return
        incoming = Incoming(
            channel_id=message.channel.id,
            author_id=message.author.id,
            author_is_bot=message.author.bot,
            content=message.content,
            bot_user_id=self.user.id,
        )
        reply = decide(incoming, test_channel_id=self.test_channel_id, test_mode=self.test_mode)
        if reply is not None:
            log.info("bot_ping", channel=message.channel.id, as_user=reply.as_user)
            await message.reply(reply.text, mention_author=False)
            return
        asked = question(incoming, test_channel_id=self.test_channel_id)
        if asked is not None:
            await self._answer(message, asked)

    async def _answer(self, message: discord.Message, asked: Question) -> None:
        """Answer in the same channel (the test channel: `question` checks it). Never raises.

        No mention is allowed in the reply: a title or a note in the rows could hold `@everyone`.
        """
        try:
            body = await asyncio.to_thread(self._reply_text, asked)
        except Exception:
            log.exception("bot_answer_failed")
            body = bw.ANSWER_FAILED
        prefix = f"{TEST_TAG} " if self.test_mode else ""
        log.info("bot_answer", channel=message.channel.id)
        await message.reply(
            (prefix + body)[:MAX_REPLY],
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @staticmethod
    def _reply_text(asked: Question) -> str:
        """An instruction (docs/09) first, then the question engine (R4 ticket 7).

        `instruct_reply` returns None for a message that is not an instruction and for an author
        nobody knows; both go on to the question path, which answers a stranger with one line.
        """
        settings = get_settings()
        with session_scope() as session:
            done = instruct_reply(session, asked.author_id, asked.text, settings)
            if done is not None:
                return done
            return discord_reply(session, asked.author_id, asked.text, settings)


def run() -> None:
    settings = get_settings()
    settings.require("bot")
    if not settings.test_mode:
        raise SystemExit("R1 bot runs only with TEST_MODE=true (docs/11).")
    if settings.discord_test_channel_id.strip() == settings.discord_sales_channel_id.strip():
        raise SystemExit("DISCORD_TEST_CHANNEL_ID equals the live sales channel. Refused.")
    SaleyBot().run(settings.discord_bot_token, log_handler=None)
