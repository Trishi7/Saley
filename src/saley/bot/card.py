"""The action card: Do it, Edit, Skip, and the link to the dashboard (R4 ticket 7, F-R4-12, D129).

Why: a card is one of Saley's own ideas (a `proposal` row). The human answers with one press:
a press IS the approval (D24), so it writes at once with a signature (user, time, surface) and an
undo window. Edit opens a Discord modal with the draft text; saving writes the edited text. Both
surfaces go through ONE SECURITY DEFINER function, `saley_proposal_resolve` (migration 0022). It
locks the proposal row, so the first saver wins and the other one sees "Already done by X at
HH:MM" (D129). The bot never writes a table itself, and never holds the sheet credential: the
function writes the decision and the outbox row, the worker pushes to the sheet.

`resolve_card` is plain code with a session and settings, so tests need no Discord. The Discord
classes at the bottom only carry a press to it. The allowlist is checked here first
(`DISCORD_APPROVER_IDS`, D135) and again in SQL (`users.role`). Card text is DATA: it is built
from the proposal payload with fixed templates and never runs as an instruction.

Cards leave through the normal `bot_post` queue (`queue_card`), so the caps, the channel guard and
the `[TEST]` tag apply as for every other post. The buttons are sent as `components` by the REST
delivery (`deliver.py`) and their clicks arrive at the gateway (`gateway.py`).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NamedTuple

import discord
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from saley.bot import identity
from saley.bot import words as bw
from saley.bot.brief import dashboard_url
from saley.core import clock
from saley.core.db import session_scope
from saley.core.logging import get_logger
from saley.core.settings import Settings, get_settings

log = get_logger("saley.bot.card")

SHAPE = "action_card"
ITEM_TYPE = "proposal"
CARD_RANK = 90  # Saley's own time-critical idea: ahead of the routine posts in the same tick
SURFACE = "discord"
MAX_TEXT = 200  # the cell limit of saley_act
# Controls, line and paragraph separators and bidi marks: the set that saley_proposal_resolve
# refuses too (migration 0022).
CONTROL = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u200e\u200f\u061c\u202a-\u202e\u2066-\u2069]"
)
FORMULA = re.compile(r"^[=+@-]")  # H1: Google would run it as a formula
MENTION = re.compile(r"<[@#&!:]|@everyone|@here", re.IGNORECASE)
BUSY_STATES = frozenset({"55P03", "40P01", "40001"})
# A press waits this long for the card lock, then says "Busy" (web.act.LOCK_TIMEOUT does the same).
lock_wait_sql = "set local lock_timeout = '2s'"
CHOICES = ("do", "edit", "skip")
ID_RE = r"card:(?P<id>[0-9]{1,15}):(?P<act>do|edit|skip)"


class CardReply(NamedTuple):
    """The words for the person, whether a write or a skip was made, and whether the card is shut.

    `saved` means this press closed the card. `closed` means an earlier answer closed it, so its
    buttons must go too (nobody can press them again).
    """

    text: str
    saved: bool = False
    closed: bool = False

    @property
    def outcome(self) -> str:
        return "saved" if self.saved else "closed" if self.closed else "refused"


@dataclass(frozen=True)
class Card:
    id: int
    type: str
    state: str
    name: str
    status: str
    value: str  # the draft: the value of the one cell the card writes


def load_card(session: Session, proposal_id: int) -> Card | None:
    row = session.execute(
        text("select id, type, state, payload from proposal where id = :i"), {"i": proposal_id}
    ).first()
    if row is None:
        return None
    payload = row.payload or {}
    cells = payload.get("cells") or [{}]
    return Card(
        id=row.id,
        type=row.type,
        state=row.state,
        name=str(payload.get("name") or "")[:60],
        status=str(payload.get("status") or "")[:60],
        value=str((cells[0] or {}).get("value") or "")[:MAX_TEXT],
    )


def render_card(card: Card) -> str:
    """Title, one why line, the draft in a code block. Fixed templates; the values are DATA."""
    title, why = {
        "meeting_status": (bw.TITLE_MEETING, bw.WHY_MEETING),
        "li_status": (bw.TITLE_LI, bw.WHY_LI),
    }.get(card.type, (bw.TITLE_OTHER, bw.WHY_OTHER))
    draft = card.value.replace("`", "'")  # a backtick could close the code block early
    return "\n".join(
        [
            bw.CARD_HEAD.format(n=card.id, title=title.format(name=card.name)),
            bw.CARD_WHY.format(why=why.format(status=card.status or "open")),
            bw.CARD_DRAFT,
            f"```\n{draft}\n```",
        ]
    )


# ------------------------------------------------------------------ the decision itself


def clean_text(raw: str) -> str | None:
    """The text to save, or None when it may not be saved (same rules as the dashboard)."""
    value = raw.strip()
    if not value or len(value) > MAX_TEXT or CONTROL.search(value) or FORMULA.match(value):
        return None
    return None if MENTION.search(value) else value


def _names(session: Session, ids: list[str], fallback: str | None = None) -> str:
    """Short names of the people behind a list of Discord ids ("Kushal, Sid and Vaishnavi")."""
    rows = session.execute(
        text("select name from users where discord_id = any(:ids) order by name"), {"ids": ids}
    )
    names = [r[0] for r in rows]
    if not names:
        return fallback or bw.NAMES_FALLBACK
    if len(names) == 1:
        return names[0]
    return bw.NAMES_JOIN.join(names[:-1]) + bw.NAMES_AND + names[-1]


def approver_names(session: Session, settings: Settings) -> str:
    """The approvers who are writers (D257): the people who may approve from Discord."""
    writers = session.execute(
        text("select discord_id from users where short = any(:w) and discord_id is not null"),
        {"w": settings.writer_shorts},
    ).scalars()
    return _names(session, [i for i in writers if i in settings.approver_ids])


def instructor_names(session: Session, settings: Settings) -> str:
    return _names(session, settings.instruct_ids)


def _moment(value: Any) -> str:
    """HH:MM in IST for the timestamp that the SQL function returns (jsonb text, with an offset).

    A timestamp with no offset is read as UTC, the zone of the database. Without this,
    `astimezone` would use the zone of the laptop and show a wrong time.
    """
    moment = value
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return "—"
    if not isinstance(moment, datetime):
        return "—"
    return clock.hhmm(moment if moment.tzinfo else moment.replace(tzinfo=UTC))


def _already(result: dict[str, Any]) -> str:
    return bw.CARD_ALREADY.format(who=str(result.get("by") or "?")[:30], t=result.get("at") or "—")


def describe(result: dict[str, Any], proposal_id: int, choice: str) -> CardReply:
    """The reply for one answer of `saley_proposal_resolve`. Fixed text, no ids of Discord."""
    n = proposal_id
    if result.get("ok"):
        if choice == "skip":
            return CardReply(bw.CARD_SKIPPED.format(n=n), True)
        items = result.get("items") or []
        if not items:  # a write that returns no cell is a fault, never "Done"
            log.warning("card_result_no_items", card=n, choice=choice)
            return CardReply(bw.CARD_FAILED)
        if all(i.get("already") for i in items):
            return CardReply(bw.CARD_ALREADY_PLAIN, closed=True)
        undo = bw.CARD_UNDO.format(t=_moment(items[0].get("undo_until")))
        return CardReply(bw.CARD_DONE.format(n=n) + "\n" + undo, True)
    code = result.get("code")
    if code == "already":  # D129: the first saver shut the card
        return CardReply(_already(result), closed=True)
    if code == "cooldown":  # D48: someone wrote this cell a moment ago; the card may stay open
        return CardReply(_already(result))
    table = {
        "not_found": CardReply(bw.CARD_NONE.format(n=n), closed=True),
        "not_open": CardReply(bw.CARD_CLOSED.format(n=n), closed=True),
        "no_draft": CardReply(bw.CARD_NO_DRAFT.format(n=n)),
        "bad_value": CardReply(bw.CARD_BAD_VALUE),
        "locked": CardReply(bw.CARD_LOCKED),
        "no_target": CardReply(bw.CARD_NO_PERSON),
        "busy": CardReply(bw.CARD_BUSY),
        "not_allowed": CardReply(bw.CARD_NOT_ALLOWED),
    }
    if str(code) not in table:
        log.warning("card_unmapped_code", card=n, code=str(code))
        return CardReply(bw.CARD_FAILED)
    return table[str(code)]


def _call_resolve(
    session: Session,
    settings: Settings,
    short: str,
    proposal_id: int,
    choice: str,
    clean: str | None,
    reason: str | None,
) -> dict[str, Any] | CardReply:
    """Run the SQL door. The answer, or the reply for a database fault (nothing is written).

    The lock wait is short: a press that cannot get the card lock says "Busy" and may try again.
    """
    try:
        session.execute(text(lock_wait_sql))
        result = session.execute(
            text("select saley_proposal_resolve(:u, :id, :c, :t, :r, :s, :cd, :um, :e)"),
            {
                "u": short,
                "id": proposal_id,
                "c": choice,
                "t": clean,
                "r": reason,
                "s": SURFACE,
                "cd": settings.act_cooldown_seconds,
                "um": settings.undo_minutes,
                "e": settings.email_write_allowed,
            },
        ).scalar_one()
        session.commit()
    except DBAPIError as exc:
        session.rollback()
        state = getattr(exc.orig, "sqlstate", None)
        log.warning("card_resolve_failed", card=proposal_id, sqlstate=state, exc_info=True)
        return CardReply(bw.CARD_BUSY if state in BUSY_STATES else bw.CARD_FAILED)
    return result


def resolve_card(
    session: Session,
    settings: Settings,
    author_id: int,
    proposal_id: int,
    choice: str,
    *,
    edited: str | None = None,
    reason: str | None = None,
) -> CardReply:
    """Do it, Edit or Skip for one card. Returns the reply. Never raises for a normal refusal.

    The caller has not checked the allowlist: this function does, so every door (chat, button,
    modal) refuses a non-approver the same way. Nothing is written for a refusal.
    """
    if choice not in CHOICES:
        raise ValueError(choice)
    who = identity.identify(session, author_id, settings)
    if who.short is None:
        return CardReply(bw.UNKNOWN_AUTHOR)
    if not who.can_approve:
        return CardReply(bw.NOT_APPROVER.format(names=approver_names(session, settings)))
    clean = None
    if choice == "edit":
        clean = clean_text(edited or "")
        if clean is None:
            return CardReply(bw.CARD_BAD_TEXT)
    result = _call_resolve(session, settings, who.short, proposal_id, choice, clean, reason)
    if isinstance(result, CardReply):
        return result
    log.info(
        "card_resolved",
        card=proposal_id,
        choice=choice,
        person=who.short,
        ok=bool(result.get("ok")),
        code=result.get("code"),
    )
    return describe(result, proposal_id, choice)


def edit_draft(
    session: Session, settings: Settings, author_id: int, proposal_id: int
) -> tuple[str | None, CardReply | None]:
    """(draft, None) when this person may edit this open card, else (None, the refusal reply).

    Read only and short: the modal must be the first answer to a press (Discord allows 3 s).
    """
    who = identity.identify(session, author_id, settings)
    if who.short is None:
        return None, CardReply(bw.UNKNOWN_AUTHOR)
    if not who.can_approve:
        return None, CardReply(bw.NOT_APPROVER.format(names=approver_names(session, settings)))
    card = load_card(session, proposal_id)
    if card is None:
        return None, CardReply(bw.CARD_NONE.format(n=proposal_id), closed=True)
    if card.state != "open":
        return None, CardReply(bw.CARD_CLOSED.format(n=proposal_id), closed=True)
    return card.value, None


# ------------------------------------------------------------------ queue and render for Discord


def queue_card(session: Session, proposal_id: int, now: datetime | None = None) -> int | None:
    """Queue ONE action card for an open proposal. Returns the bot_post id, or None.

    It goes through `bot_post`, so the caps (D132), the channel guard and the `[TEST]` tag decide
    when and where it appears. A card is queued once per proposal.
    """
    card = load_card(session, proposal_id)
    if card is None or card.state != "open":
        return None
    when = now or clock.now_ist()
    row = session.execute(
        text(
            "insert into bot_post (kind, shape, channel, for_user, mentions, body, rank, due_at, "
            "state, item_type, item_id, is_meeting, is_reconnect) "
            "select :k, :k, 'sales', null, '{}', :b, :r, :t, 'queued', :it, :i, false, false "
            "where not exists (select 1 from bot_post where item_type = :it and item_id = :i "
            "and shape = :k) returning id"
        ),
        {
            "k": SHAPE,
            "b": render_card(card),
            "r": CARD_RANK,
            "t": when,
            "it": ITEM_TYPE,
            "i": proposal_id,
        },
    ).first()
    return int(row[0]) if row else None


def components(proposal_id: int, url: str | None = None) -> list[dict[str, Any]]:
    """The button row for the REST post: Do it, Edit, Skip and the dashboard link."""
    return CardView(proposal_id, url or dashboard_url()).to_components()


# ------------------------------------------------------------------ Discord classes

STYLES = {
    "do": (discord.ButtonStyle.success, "✅", bw.BTN_DO),
    "edit": (discord.ButtonStyle.primary, "✏️", bw.BTN_EDIT),
    "skip": (discord.ButtonStyle.secondary, "❌", bw.BTN_SKIP),
}
NO_MENTIONS = discord.AllowedMentions.none()


def _press(author_id: int, proposal_id: int, act: str) -> CardReply:
    with session_scope() as session:
        return resolve_card(session, get_settings(), author_id, proposal_id, act)


def _draft_for(author_id: int, proposal_id: int) -> tuple[str | None, CardReply | None]:
    with session_scope() as session:
        return edit_draft(session, get_settings(), author_id, proposal_id)


def _save_text(author_id: int, proposal_id: int, edited: str) -> CardReply:
    with session_scope() as session:
        return resolve_card(session, get_settings(), author_id, proposal_id, "edit", edited=edited)


async def _defer(interaction: discord.Interaction, proposal_id: int) -> bool:
    """Take the press within Discord's 3 s, before any database work. False: nothing was written.

    The answer comes later as a follow-up (valid for 15 minutes), so a slow lock wait cannot
    make the person see "This interaction failed".
    """
    try:
        await interaction.response.defer(ephemeral=True)
    except discord.HTTPException:
        log.warning("card_defer_failed", card=proposal_id, exc_info=True)
        return False
    return True


async def _remove_buttons(interaction: discord.Interaction, proposal_id: int) -> None:
    if interaction.message is None:
        return
    try:  # the card is closed: nobody can press the buttons again
        await interaction.message.edit(view=None)
    except discord.HTTPException:
        log.warning("card_buttons_not_removed", card=proposal_id, exc_info=True)


async def _finish(interaction: discord.Interaction, proposal_id: int, reply: CardReply) -> None:
    """Answer a deferred press. A saved card is posted for the team; a refusal is for one person.

    A failed reply is logged with the card and the outcome and never raised: the write (or the
    refusal) already happened, and an exception here would only hide that from the log.
    """
    try:
        await interaction.followup.send(
            reply.text, ephemeral=not reply.saved, allowed_mentions=NO_MENTIONS
        )
    except discord.HTTPException:
        log.warning("card_reply_failed", card=proposal_id, outcome=reply.outcome, exc_info=True)
    if reply.saved or reply.closed:
        await _remove_buttons(interaction, proposal_id)


async def _open_modal(interaction: discord.Interaction, proposal_id: int) -> None:
    """Edit: the modal is the FIRST answer, so only a short read-only lookup comes before it."""
    draft, refusal = await asyncio.to_thread(_draft_for, interaction.user.id, proposal_id)
    try:
        if draft is None:
            reply = refusal or CardReply(bw.CARD_FAILED)
            await interaction.response.send_message(
                reply.text, ephemeral=True, allowed_mentions=NO_MENTIONS
            )
        else:
            await interaction.response.send_modal(EditModal(proposal_id, draft))
    except discord.HTTPException:
        log.warning("card_reply_failed", card=proposal_id, outcome="modal", exc_info=True)
        return
    if draft is None and refusal is not None and refusal.closed:
        await _remove_buttons(interaction, proposal_id)


class EditModal(discord.ui.Modal):
    """Edit the draft. Saving writes the edited text with surface=discord (D129)."""

    def __init__(self, proposal_id: int, draft: str) -> None:
        super().__init__(title=bw.MODAL_TITLE)
        self.proposal_id = proposal_id
        self.draft = discord.ui.TextInput(
            label=bw.MODAL_LABEL,
            style=discord.TextStyle.paragraph,
            default=draft[:MAX_TEXT],
            max_length=MAX_TEXT,
            required=True,
        )
        self.add_item(self.draft)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _defer(interaction, self.proposal_id):
            return
        try:
            reply = await asyncio.to_thread(
                _save_text, interaction.user.id, self.proposal_id, self.draft.value
            )
        except Exception:
            log.exception("card_modal_failed", card=self.proposal_id)
            reply = CardReply(bw.CARD_FAILED)
        await _finish(interaction, self.proposal_id, reply)


class CardButton(
    discord.ui.DynamicItem[discord.ui.Button],  # type: ignore[type-arg]
    template=ID_RE,
):
    """One button. The id is in the custom_id, so a press works after the bot restarts."""

    def __init__(self, proposal_id: int, act: str) -> None:
        style, emoji, label = STYLES[act]
        super().__init__(
            discord.ui.Button(
                style=style, emoji=emoji, label=label, custom_id=f"card:{proposal_id}:{act}"
            )
        )
        self.proposal_id = proposal_id
        self.act = act

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]
    ) -> CardButton:
        return cls(int(match["id"]), match["act"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.act == "edit":
            try:
                await _open_modal(interaction, self.proposal_id)
            except Exception:
                log.exception("card_press_failed", card=self.proposal_id, act=self.act)
            return
        if not await _defer(interaction, self.proposal_id):
            return
        try:
            reply = await asyncio.to_thread(_press, interaction.user.id, self.proposal_id, self.act)
        except Exception:
            log.exception("card_press_failed", card=self.proposal_id, act=self.act)
            reply = CardReply(bw.CARD_FAILED)
        await _finish(interaction, self.proposal_id, reply)


class CardView(discord.ui.View):
    """Three buttons and a link button (D129). No timeout: a card stays usable until it closes."""

    def __init__(self, proposal_id: int, url: str) -> None:
        super().__init__(timeout=None)
        for act in CHOICES:
            self.add_item(CardButton(proposal_id, act))
        self.add_item(discord.ui.Button(label=bw.BTN_DASHBOARD, url=url))
