"""The channel guard: one function decides where a bot post goes (D127, D132, CLAUDE.md).

Why: a laptop must never post to a live place. The row says what the producer wanted (`test`,
`sales` or `dm`); this function says where it goes.
- `test` always goes to the test channel. A row never moves up to a live place.
- `sales` goes to the test channel while TEST_MODE=true, else to the sales channel.
- `dm` goes to the Discord id of the person (a DM) only when TEST_MODE=false and not in dev.
- docs/09: the test channel gets everything while TEST_MODE=true. So under TEST_MODE=true or
  SALEY_ENV=dev a DM is redirected to the test channel with the short name of the person in the
  text. The person gets nothing. Redirect, not refuse: it is as safe (the text reaches only the
  test channel and carries no mention) and the human can still read what the DM would say.
- Under SALEY_ENV=dev everything except the test channel is refused and logged.
Ids are never written to the log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from saley.core.logging import get_logger
from saley.core.settings import Settings

log = get_logger("saley.bot.guard")
_ID = re.compile(r"[0-9]{17,20}")


class ChannelRefused(RuntimeError):
    """The post may not go where it asked to go."""


@dataclass(frozen=True)
class Target:
    channel_id: str | None  # None for a real DM (the channel is opened at send time)
    dm_user_id: str | None = None
    redirected_for: str | None = None  # a dev DM sent to the test channel for this person


def _test_channel(settings: Settings) -> str:
    channel = settings.discord_test_channel_id.strip()
    if not _ID.fullmatch(channel):
        raise ChannelRefused("DISCORD_TEST_CHANNEL_ID must be a Discord id (digits only).")
    if channel == settings.discord_sales_channel_id.strip():
        raise ChannelRefused("DISCORD_TEST_CHANNEL_ID equals the live sales channel.")
    return channel


def _refuse(why: str, channel: str) -> ChannelRefused:
    log.warning("bot_channel_refused", channel=channel, why=why)
    return ChannelRefused(why)


def resolve(
    channel: str, settings: Settings, *, discord_id: str | None = None, short: str | None = None
) -> Target:
    """Where a post with this `channel` goes. Raises ChannelRefused when it may not go."""
    test_channel = _test_channel(settings)
    if channel == "test" or (channel == "sales" and settings.test_mode):
        return Target(test_channel)
    if channel == "sales":
        if settings.is_dev:
            raise _refuse("SALEY_ENV=dev posts to the test channel only", channel)
        sales = settings.discord_sales_channel_id.strip()
        if not _ID.fullmatch(sales):
            raise _refuse("DISCORD_SALES_CHANNEL_ID is not a Discord id", channel)
        return Target(sales)
    if channel != "dm":
        raise _refuse("unknown channel", channel)
    if settings.is_dev or settings.test_mode:
        log.info("bot_dm_redirected", person=short)
        return Target(test_channel, redirected_for=short or "?")
    if not discord_id or not _ID.fullmatch(discord_id.strip()):
        raise _refuse("the person has no Discord id", channel)
    return Target(None, dm_user_id=discord_id.strip())
