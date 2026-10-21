"""Who is talking to the bot (D135).

Why: only people in the team may get data. `DISCORD_USER_MAP` fills `users.discord_id` when
the bot starts. The allowlists are the ids already in DISCORD_APPROVER_IDS and
DISCORD_INSTRUCT_IDS (D16, D135); this module only reads them. An author with no `users` row
gets one polite line and no data. Discord ids are never logged.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from saley.bot import words as bw
from saley.core.logging import get_logger
from saley.core.settings import Settings

log = get_logger("saley.bot.identity")


@dataclass(frozen=True)
class Identity:
    short: str | None
    can_instruct: bool
    can_approve: bool


def _is_user(session: Session, short: str) -> bool:
    row = session.execute(text("select 1 from users where short = :s"), {"s": short}).first()
    return row is not None


def sync_user_map(session: Session, settings: Settings) -> int:
    """Set `users.discord_id` from DISCORD_USER_MAP. Returns the number of rows changed.

    A short name that is not a user, or an id another user already holds, is skipped and
    logged by short name only.
    """
    changed = 0
    for short, discord_id in settings.user_map.items():
        try:
            with session.begin_nested():
                result = session.execute(
                    text(
                        "update users set discord_id = :i "
                        "where short = :s and discord_id is distinct from :i"
                    ),
                    {"i": discord_id, "s": short},
                )
        except IntegrityError:
            log.warning("discord_id_conflict", person=short)
            continue
        if result.rowcount == 0 and not _is_user(session, short):
            log.warning("discord_user_unknown", person=short)
        changed += result.rowcount
    session.commit()
    return changed


def identify(session: Session, author_id: int, settings: Settings) -> Identity:
    """The team member behind a Discord id. An unknown id has no short name and no rights."""
    ident = str(author_id)
    short = session.execute(
        text("select short from users where discord_id = :i"), {"i": ident}
    ).scalar()
    if short is None:
        return Identity(None, False, False)
    return Identity(short, ident in settings.instruct_ids, ident in settings.approver_ids)


def reply_for(identity: Identity) -> str | None:
    """The one polite line for an unknown author. None for a team member."""
    return bw.UNKNOWN_AUTHOR if identity.short is None else None
