"""The Discord side of a card press: buttons, selects, modals and ephemeral replies.

Why: `press.py` decides (plain code, tested without Discord); this module only speaks
discord.py. Every card control has a `c7:<card_id>:<action>` custom id, so a press works after
a restart, on cards the REST delivery sent (no View has to be kept in memory). Discord gives
3 s to answer: a press defers first and does the database work after, as R4 (D129).

Flows (docs/09 §6, docs/17 §5):
- button or select → defer → `press.press` → ephemeral line on already/refused/error → edit;
- a button with a modal (M5 note, M6 next step, M8 date, M14 answer) → the modal is the first
  answer; a person who may not write gets A2 at once instead of a modal (the bot check; SQL
  checks again on submit);
- A2 Ask buttons (`ask_<short>`) post the card into a thread for that writer;
- A1 buttons (`a1`): More answers the asker's next page in private; Dismiss removes the answer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import discord

from saley.bot import components as c
from saley.bot import deliver, identity, press
from saley.bot import words as bw
from saley.bot.cards.replies import ANSWER_CARD
from saley.core.db import session_scope
from saley.core.logging import get_logger
from saley.core.settings import get_settings

log = get_logger("saley.bot.interact")

NO_MENTIONS = discord.AllowedMentions.none()
BUTTON_STYLES = {
    c.STYLE["primary"]: discord.ButtonStyle.primary,
    c.STYLE["secondary"]: discord.ButtonStyle.secondary,
    c.STYLE["success"]: discord.ButtonStyle.success,
    c.STYLE["danger"]: discord.ButtonStyle.danger,
}
TEXT_STYLES = {"short": discord.TextStyle.short, "paragraph": discord.TextStyle.paragraph}


def view_of(message: dict[str, Any]) -> discord.ui.View | None:
    """A View for a reply we send through discord.py (A1, A2). Callbacks live in on_interaction."""
    controls = c.buttons_of(message)
    if not controls:
        return None
    view = discord.ui.View(timeout=None)
    for b in controls:
        if b.get("style") == c.STYLE["link"]:
            view.add_item(discord.ui.Button(label=b["label"], url=b["url"]))
        elif b.get("type") == c.BUTTON:
            view.add_item(
                discord.ui.Button(
                    label=b["label"],
                    custom_id=b["custom_id"],
                    style=BUTTON_STYLES.get(b["style"], discord.ButtonStyle.secondary),
                    disabled=bool(b.get("disabled")),
                )
            )
    return view


async def _reply(interaction: discord.Interaction, body: dict[str, Any] | None) -> None:
    """One ephemeral follow-up. A failed reply is logged, never raised: the press already ran."""
    if not body:
        return
    view = view_of(body)
    try:
        if view is None:
            await interaction.followup.send(
                body["content"], ephemeral=True, allowed_mentions=NO_MENTIONS
            )
        else:
            await interaction.followup.send(
                body["content"], ephemeral=True, allowed_mentions=NO_MENTIONS, view=view
            )
    except discord.HTTPException:
        log.warning("card_reply_failed", exc_info=True)


async def _defer(interaction: discord.Interaction) -> bool:
    try:
        await interaction.response.defer(ephemeral=True, thinking=False)
    except discord.HTTPException:
        log.warning("card_defer_failed", exc_info=True)
        return False
    return True


def _run(fn: Callable[..., press.Outcome], *args: Any) -> press.Outcome:
    with session_scope() as session:
        return fn(session, get_settings(), *args)


async def run_press(
    interaction: discord.Interaction, card_id: str, action: str, values: dict[str, str]
) -> None:
    """Carry one press to `press.press`, then reply and edit. Never raises."""
    try:
        outcome = await asyncio.to_thread(
            _run, press.press, interaction.user.id, card_id, action, values
        )
    except Exception:
        log.exception("card_press_crashed", card=card_id, action=action)
        outcome = press.Outcome("error", "crash", {"content": bw.PRESS_FAILED})
    await _reply(interaction, outcome.reply)
    if outcome.edit:
        try:
            await asyncio.to_thread(deliver.edit_card, card_id)
        except Exception:
            log.exception("card_edit_crashed", card=card_id)


class CardModal(discord.ui.Modal):
    """A modal built from the card's stored spec (text, date, a select of sheet values)."""

    def __init__(self, card_id: str, action: str, spec: dict[str, Any]) -> None:
        super().__init__(title=c.clean(spec.get("title") or bw.BTN_ANSWER, 45), timeout=600)
        self.card_id, self.action = card_id, action
        self.fields: dict[str, Any] = {}
        for f in spec.get("fields", [])[:5]:
            if f.get("options"):
                choice = discord.ui.Select(
                    options=[
                        discord.SelectOption(label=str(o)[:100], value=str(o)[:100])
                        for o in f["options"][:25]
                    ],
                    required=False,
                )
                self.add_item(discord.ui.Label(text=f["label"][:45], component=choice))
                self.fields[f["id"]] = choice
            else:
                box = discord.ui.TextInput(
                    label=f["label"][:45],
                    style=TEXT_STYLES.get(f.get("style", "short"), discord.TextStyle.short),
                    max_length=int(f.get("max", 300)),
                    placeholder=f.get("placeholder"),
                    required=True,
                )
                self.add_item(box)
                self.fields[f["id"]] = box

    def values(self) -> dict[str, str]:
        out = {}
        for key, item in self.fields.items():
            if isinstance(item, discord.ui.Select):
                out[key] = item.values[0] if item.values else ""
            else:
                out[key] = str(item.value)
        return out

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if await _defer(interaction):
            await run_press(interaction, self.card_id, self.action, self.values())


def _act_and_writer(card_id: str, action: str, user_id: int) -> tuple[dict[str, Any] | None, bool]:
    settings = get_settings()
    with session_scope() as session:
        who = identity.identify(session, user_id, settings)
        return press.act_of(session, card_id, action), who.can_write


def _refusal(card_id: str) -> dict[str, Any]:
    with session_scope() as session:
        return press.refusal(session, get_settings(), card_id)


async def on_card(interaction: discord.Interaction, answer_more: Callable[..., Any]) -> None:
    """Route one `c7:` interaction. `answer_more` is the gateway's More handler (A1)."""
    data = interaction.data or {}
    match = press.CUSTOM_ID.fullmatch(str(data.get("custom_id", "")))
    if match is None:
        return
    card_id, action = match["card"], match["act"]
    if card_id == ANSWER_CARD:
        await answer_more(interaction, action)
        return
    if action.startswith("ask_"):
        if await _defer(interaction):
            outcome = await asyncio.to_thread(
                _run, press.ask_writer, interaction.user.id, card_id, action.removeprefix("ask_")
            )
            await _reply(interaction, outcome.reply)
        return
    act, can_write = await asyncio.to_thread(_act_and_writer, card_id, action, interaction.user.id)
    if act and act.get("modal"):
        try:
            if press.needs_writer(act) and not can_write:
                body = await asyncio.to_thread(_refusal, card_id)
                await interaction.response.send_message(
                    body["content"],
                    ephemeral=True,
                    view=view_of(body) or discord.utils.MISSING,
                    allowed_mentions=NO_MENTIONS,
                )
            else:
                await interaction.response.send_modal(CardModal(card_id, action, act["modal"]))
        except discord.HTTPException:
            log.warning("card_modal_failed", card=card_id, exc_info=True)
        return
    values = {"value": str(data["values"][0])} if data.get("values") else {}
    if await _defer(interaction):
        await run_press(interaction, card_id, action, values)
