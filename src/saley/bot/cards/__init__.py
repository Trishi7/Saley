"""The card registry: one pure renderer per card type (docs/09 §5, docs/17 §1).

Why: the worker renders every card (the bot never builds text), and a test can render each
type from sample rows and check the shared rules (`bot/components.py`). A renderer is a plain
function `(data, card_id) -> payload`: `data` holds row values only, the payload is what
`bot_post.payload` stores (the Discord body, `acts`, `keys`, the thread, the pin flag).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from saley.bot.cards import lists, people, replies, team

Renderer = Callable[[Mapping[str, Any], str], dict[str, Any]]

RENDERERS: dict[str, Renderer] = {
    "M1": team.m1_brief,
    "M2": lists.m2_needs_a_look,
    "M3": lists.m3_suggestions,
    "M4": lists.m4_events,
    "M5": people.m5_meeting_brief,
    "M6": people.m6_after_meeting,
    "M7": people.m7_reconnect,
    "M8": team.m8_commitment,
    "M9": team.m9_reminder,
    "M10": people.m10_idea,
    "M11": people.m11_new_company,
    "M12": team.m12_pack,
    "M13": lists.m13_blockers,
    "M14": team.m14_ask,
    "M15": team.m15_health,
    "A1": replies.a1_answer,
    "A2": replies.a2_refusal,
}
# Cards that never end with the closing line (docs/12b rule 4).
NO_CLOSING = frozenset({"M15", "A2"})


def render(card_type: str, data: Mapping[str, Any], card_id: str) -> dict[str, Any]:
    return RENDERERS[card_type](data, card_id)
