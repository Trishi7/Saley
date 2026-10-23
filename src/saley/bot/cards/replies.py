"""Replies, not queued cards: A1 answer and A2 refusal (docs/09 §5, §8).

Why: an answer and a refusal go straight back to the asker, so they never pass the caps and
have no `bot_post` row. Their buttons are handled by the bot itself (More, Dismiss, Ask Sid);
none of them writes, so none goes through `saley_card_press`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from saley.bot import components as c
from saley.bot import words as bw

ANSWER_CARD = "a1"  # the card id of every answer's buttons (handled in the bot, no row)


def a1_answer(d: Mapping[str, Any], card_id: str = ANSWER_CARD) -> dict[str, Any]:
    """The answer text (answer first, citations by name, the window line last) and two buttons.

    d: text (built by the answer engine and checked by intel.guard), more (bool)."""
    buttons = [c.button(bw.BTN_DISMISS, card_id, "dismiss")]
    if d.get("more"):
        buttons.insert(0, c.button(bw.BTN_MORE, card_id, "more", "primary"))
    return c.card(
        card_type="A1",
        card_id=card_id,
        content=c.closing(str(d["text"])),
        rows=[c.row(*buttons)],
    )


def a2_refusal(d: Mapping[str, Any], card_id: str) -> dict[str, Any]:
    """A press from someone who may not write (D257): one kind line and Ask buttons.

    d: writers [(short, first name)]; card_id is the card that was pressed."""
    writers = [(str(s), str(n)) for s, n in d["writers"]]
    names = " and ".join(n for _, n in writers)
    return c.card(
        card_type="A2",
        card_id=card_id,
        content=c.lines(c.say(bw.REFUSE_WRITE, names=names), bw.REFUSE_OFFER),
        rows=[
            c.row(
                *(c.button(c.say(bw.BTN_ASK, name=n), card_id, f"ask_{s}") for s, n in writers[:4])
            )
        ],
    )
