"""discord.py gateway: log in, log the guild and test channel names, answer ping and questions.

Note: the gateway is a websocket that discord.py opens itself, so it cannot use the httpx
egress transport. discord.com is on the allowlist; REST calls from the CLI use egress.
"""

from __future__ import annotations

import asyncio

import discord
from discord.ext import tasks

from saley.bot import cards, intent, interact, memory, sync
from saley.bot import words as bw
from saley.bot.answer import discord_reply, split_for_thread
from saley.bot.card import CardButton
from saley.bot.cards.replies import ANSWER_CARD
from saley.bot.deliver import deliver_ready
from saley.bot.identity import sync_user_map
from saley.bot.instruct import instruct_reply
from saley.bot.ping import TEST_TAG, Incoming, Question, decide, question
from saley.core import alive
from saley.core.db import session_scope
from saley.core.logging import get_logger
from saley.core.settings import get_settings

MAX_REPLY = 1990  # Discord allows 2000 characters in a message
ANSWER_MARK = bw.FOOT_WINDOW.split("{")[0]  # every answer from rows ends with its window line
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
        self.backfill: asyncio.Task[None] | None = None  # kept: an unreferenced task may vanish

    async def setup_hook(self) -> None:
        """Map Discord ids to people once, register the card buttons, start the delivery loop
        and the card sync (LISTEN saley_card, D259).

        The R4 buttons are registered by pattern (`card:<id>:<act>`); the R7 cards (`c7:...`)
        are routed in `on_interaction`. Both work after a restart (D129). Never runs in tests.
        """
        await asyncio.to_thread(self._sync_identity)
        self.add_dynamic_items(CardButton)
        self.deliver_loop.start()
        sync.start()

    @staticmethod
    def _sync_identity() -> None:
        with session_scope() as session:
            changed = sync_user_map(session, get_settings())
        log.info("bot_user_map", changed=changed)

    @tasks.loop(seconds=DELIVER_SECONDS)
    async def deliver_loop(self) -> None:
        """Send ready rows. A failure is logged and the loop keeps going.

        D245: each turn also writes the heartbeat file; the loop waits for the gateway to be
        ready, so a fresh file means a connected bot."""
        alive.beat("bot")
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
            return
        if self.backfill is None:  # on_ready runs again after every reconnect
            self.backfill = asyncio.create_task(self._backfill())

    async def _backfill(self) -> None:
        """D262: read the last CHAT_BACKFILL_DAYS of each chat channel, once ever. Never raises."""
        try:
            if await asyncio.to_thread(self._backfill_done):
                return
            settings = get_settings()
            found = [self.get_channel(c) for c in memory.chat_channels(settings)]
            channels = [c for c in found if c is not None and hasattr(c, "history")]
            lines: list[memory.Line] = []
            await memory.backfill(
                channels,
                lambda line: lines.append(line) is None,
                settings,
                forbidden=(discord.Forbidden,),
            )
            stored = await asyncio.to_thread(self._store_backfill, lines)
            log.info("chat_backfill", read=len(lines), stored=stored)
        except Exception:
            log.exception("chat_backfill_failed")

    @staticmethod
    def _backfill_done() -> bool:
        with session_scope() as session:
            return memory.backfill_done(session)

    @staticmethod
    def _store_backfill(lines: list[memory.Line]) -> int:
        settings = get_settings()
        with session_scope() as session:
            stored = sum(memory.capture(session, settings, line) for line in lines)
            memory.mark_backfill_done(session, stored)
        return stored

    @staticmethod
    def _capture(line: memory.Line) -> None:
        with session_scope() as session:
            memory.capture(session, get_settings(), line)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """R7 cards: every control has a `c7:` custom id (bot/interact.py). Never raises."""
        custom_id = str((interaction.data or {}).get("custom_id", ""))
        if interaction.type != discord.InteractionType.component or not custom_id.startswith("c7:"):
            return
        try:
            await interact.on_card(interaction, self._answer_button)
        except Exception:
            log.exception("card_interaction_failed")

    async def _answer_button(self, interaction: discord.Interaction, action: str) -> None:
        """A1: More shows the asker's next page in private; Dismiss removes the answer."""
        if action == "dismiss":
            ref = interaction.message.reference if interaction.message else None
            asked_by = getattr(getattr(ref, "resolved", None), "author", None)
            try:
                if interaction.message and (asked_by is None or asked_by.id == interaction.user.id):
                    await interaction.message.delete()
                    await interaction.response.defer(ephemeral=True, thinking=False)
                else:
                    await interaction.response.send_message(
                        bw.CARD_NOT_ALLOWED, ephemeral=True, allowed_mentions=interact.NO_MENTIONS
                    )
            except discord.HTTPException:
                log.warning("answer_dismiss_failed", exc_info=True)
            return
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            body = await asyncio.to_thread(self._reply_text, Question("more", interaction.user.id))
            await interaction.followup.send(
                body[:MAX_REPLY], ephemeral=True, allowed_mentions=interact.NO_MENTIONS
            )
        except discord.HTTPException:
            log.warning("answer_more_failed", exc_info=True)

    async def on_message(self, message: discord.Message) -> None:
        if self.user is None:
            return
        try:  # D262: every chat line of the bot's channels; a failure never blocks a reply
            line = memory.of_message(message)
            if not line.author_is_bot and line.channel_id in memory.chat_channels(get_settings()):
                await asyncio.to_thread(self._capture, line)  # other channels: no database work
        except Exception:
            log.exception("chat_capture_failed")
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
        R7 (docs/09 §8): the answer carries More and Dismiss (A1); over ANSWER_THREAD_LINES
        lines, 3 stay in the channel and the rest goes to a private thread with the asker.
        """
        try:
            body = await asyncio.to_thread(self._reply_text, asked, message.channel.id)
        except Exception:
            log.exception("bot_answer_failed")
            body = bw.ANSWER_FAILED
        prefix = f"{TEST_TAG} " if self.test_mode else ""
        head, rest = split_for_thread(body, get_settings().answer_thread_lines)
        view: discord.ui.View | None = None
        if ANSWER_MARK in body:  # an answer from rows (A1); a refusal or "cannot" line is A2
            more = bw.MORE_HINT.split("{")[0] in body
            card = cards.render("A1", {"text": head, "more": more}, ANSWER_CARD)
            head, view = card["message"]["content"], interact.view_of(card["message"])
        log.info("bot_answer", channel=message.channel.id, thread=rest is not None)
        await message.reply(
            (prefix + head)[:MAX_REPLY],
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
            view=view or discord.utils.MISSING,
        )
        if rest:
            await self._rest_in_thread(message, prefix + rest)

    @staticmethod
    async def _rest_in_thread(message: discord.Message, rest: str) -> None:
        """The rest of a long answer in a private thread with the asker. Never raises."""
        try:
            channel = message.channel
            if isinstance(channel, discord.Thread):
                await channel.send(
                    rest[:MAX_REPLY], allowed_mentions=discord.AllowedMentions.none()
                )
                return
            name = bw.ANSWER_THREAD.format(who=message.author.display_name)[:100]
            thread = await channel.create_thread(  # type: ignore[union-attr]
                name=name, type=discord.ChannelType.private_thread, invitable=False
            )
            await thread.add_user(message.author)
            await thread.send(rest[:MAX_REPLY], allowed_mentions=discord.AllowedMentions.none())
        except (discord.HTTPException, AttributeError):
            log.warning("bot_answer_thread_failed", exc_info=True)

    @staticmethod
    def _reply_text(asked: Question, channel_id: int | None = None) -> str:
        """An instruction (docs/09) first, then the question engine (R4 ticket 7).

        `instruct_reply` returns None for a message that is not an instruction and for an author
        nobody knows; both go on to the question path, which answers a stranger with one line.
        R7: the intent step (the worker, LLM_SMALL) comes in front of the grammar.
        """
        settings = get_settings()
        with session_scope() as session:
            done = instruct_reply(session, asked.author_id, asked.text, settings)
            if done is not None:
                return done
            return discord_reply(
                session,
                asked.author_id,
                asked.text,
                settings,
                channel=channel_id,
                intent_of=intent.intent_of(settings),
            )


def run_offline() -> None:
    """D247: CI has no Discord token. The bot container checks its settings and the database,
    then only writes the heartbeat. The server smoke proves the real gateway (ping, pong)."""
    import time

    from sqlalchemy import text

    from saley.core.db import rw_engine

    with rw_engine().connect() as conn:
        conn.execute(text("select 1"))
    log.warning("bot_offline", reason="--no-gateway: no Discord connection")
    while True:
        alive.beat("bot")
        time.sleep(alive.BEAT_SECONDS)


def run() -> None:
    settings = get_settings()
    settings.require("bot")
    if not settings.test_mode:
        raise SystemExit("R1 bot runs only with TEST_MODE=true (docs/11).")
    if settings.discord_test_channel_id.strip() == settings.discord_sales_channel_id.strip():
        raise SystemExit("DISCORD_TEST_CHANNEL_ID equals the live sales channel. Refused.")
    SaleyBot().run(settings.discord_bot_token, log_handler=None)
