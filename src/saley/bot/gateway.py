"""discord.py gateway: log in, log the guild and test channel names, answer ping.

Note: the gateway is a websocket that discord.py opens itself, so it cannot use the httpx
egress transport. discord.com is on the allowlist; REST calls from the CLI use egress.
"""

from __future__ import annotations

import discord

from saley.bot.ping import Incoming, decide
from saley.core.logging import get_logger
from saley.core.settings import get_settings

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
        reply = decide(
            Incoming(
                channel_id=message.channel.id,
                author_id=message.author.id,
                author_is_bot=message.author.bot,
                content=message.content,
                bot_user_id=self.user.id,
            ),
            test_channel_id=self.test_channel_id,
            test_mode=self.test_mode,
        )
        if reply is None:
            return
        log.info("bot_ping", channel=message.channel.id, as_user=reply.as_user)
        await message.reply(reply.text, mention_author=False)


def run() -> None:
    settings = get_settings()
    settings.require("bot")
    if not settings.test_mode:
        raise SystemExit("R1 bot runs only with TEST_MODE=true (docs/11).")
    if settings.discord_test_channel_id.strip() == settings.discord_sales_channel_id.strip():
        raise SystemExit("DISCORD_TEST_CHANNEL_ID equals the live sales channel. Refused.")
    SaleyBot().run(settings.discord_bot_token, log_handler=None)
