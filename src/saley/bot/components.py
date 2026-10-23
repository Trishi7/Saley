"""Card rules and Discord JSON builders, in one place (docs/09 §5 and §9, D260, D261).

Why: every card follows the same rules, so they live here once and a test checks them for every
card type:
- at most BOT_LIST_MAX items in the card; the rest go to one post in a thread under the card;
- one row of at most 4 buttons, in the order act, act, dismiss, link; more choices use a select
  menu;
- a link button names the thing ("Sujith's page"), never "Open", "View" or "Click";
- the closing line on every card except M15 health and A2 (docs/12b rule 4);
- the colour of the card type.
The builders return Discord REST JSON as is (embeds, components, Components v2). discord.py
already speaks this format; nothing here wraps it. Text from rows goes through `clean` (the
answer engine's cleaner): no mention, no markdown, no control character can come from a row.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from typing import Any

from saley.bot import words as bw
from saley.bot.answer import _clean as clean
from saley.core.settings import get_settings

COLOURS = {
    "news": 0x3B82F6,
    "event": 0x8B5CF6,
    "blocker": 0xEF4444,
    "suggestion": 0x22C55E,
    "meeting": 0xEAB308,
    "grey": 0x6B7280,
    "ok": 0x22C55E,
    "cannot": 0xEF4444,
}
BUTTONS_MAX = 4  # D261: one row, act, act, dismiss, link
ROWS_MAX = 5  # Discord: five action rows a message
PREFIX = "c7"  # custom_id of a card control: c7:<card_id>:<action>
CUSTOM_ID_MAX = 100
CARD_ID_MAX = 80  # leaves room for the prefix and the action inside CUSTOM_ID_MAX
COMPONENTS_V2 = 1 << 15  # message flag IS_COMPONENTS_V2
TITLE_MAX, DESC_MAX, FOOTER_MAX = 256, 4096, 2048
FIELD_NAME_MAX, FIELD_VALUE_MAX, FIELDS_MAX = 256, 1024, 25
LABEL_MAX, OPTIONS_MAX, CONTENT_MAX = 80, 25, 1990
BAD_LINK_WORDS = ("open", "view", "click")
STYLE = {"primary": 1, "secondary": 2, "success": 3, "danger": 4, "link": 5}
BUTTON, ROW, SELECT = 2, 1, 3
V2_SECTION, V2_TEXT, V2_THUMB, V2_GALLERY, V2_SEPARATOR, V2_CONTAINER = 9, 10, 11, 12, 14, 17


class CardRuleError(ValueError):
    """A renderer broke a card rule. A bug, never shown to a person."""


def list_max() -> int:
    return get_settings().bot_list_max


def split(items: Sequence[Any], limit: int | None = None) -> tuple[list[Any], list[Any]]:
    """The items in the card and the rest for the thread. Nothing is dropped (D260)."""
    cut = list_max() if limit is None else limit
    return list(items[:cut]), list(items[cut:])


def custom_id(card_id: str, action: str) -> str:
    value = f"{PREFIX}:{card_id}:{action}"
    if len(card_id) > CARD_ID_MAX or len(value) > CUSTOM_ID_MAX:
        raise CardRuleError(f"custom_id too long: {value}")
    return value


def _label(text: str) -> str:
    return clean(text, LABEL_MAX)


def button(
    label: str, card_id: str, action: str, style: str = "secondary", *, emoji: str | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "type": BUTTON,
        "style": STYLE[style],
        "label": _label(label),
        "custom_id": custom_id(card_id, action),
    }
    if emoji:
        out["emoji"] = {"name": emoji}
    return out


def link(label: str, url: str) -> dict[str, Any]:
    """A link button. D261: its text names the thing."""
    words = label.casefold().split()
    if not words or words[0] in BAD_LINK_WORDS:
        raise CardRuleError(f"link text must name the thing: {label!r}")
    return {"type": BUTTON, "style": STYLE["link"], "label": _label(label), "url": url}


def disabled(label: str, card_id: str, action: str) -> dict[str, Any]:
    return {**button(label, card_id, action), "disabled": True}


def select(
    card_id: str, action: str, placeholder: str, options: Iterable[tuple[str, str]]
) -> dict[str, Any]:
    """A select menu: (label, value) pairs. The value is what the door checks."""
    opts = [{"label": _label(lbl), "value": str(val)} for lbl, val in options][:OPTIONS_MAX]
    if not opts:
        raise CardRuleError("a select menu needs at least one option")
    return {
        "type": ROW,
        "components": [
            {
                "type": SELECT,
                "custom_id": custom_id(card_id, action),
                "placeholder": _label(placeholder),
                "options": opts,
                "min_values": 1,
                "max_values": 1,
            }
        ],
    }


def row(*items: dict[str, Any]) -> dict[str, Any]:
    if not 1 <= len(items) <= BUTTONS_MAX:
        raise CardRuleError(f"a row holds 1 to {BUTTONS_MAX} buttons, not {len(items)}")
    return {"type": ROW, "components": list(items)}


def field(name: str, value: str, inline: bool = True) -> dict[str, Any]:
    return {
        "name": clean(name, FIELD_NAME_MAX) or "—",
        # Each line is cleaned on its own: the renderer's line breaks stay.
        "value": "\n".join(clean(x) for x in value.splitlines())[:FIELD_VALUE_MAX] or "—",
        "inline": inline,
    }


def embed(
    *,
    colour: str,
    title: str | None = None,
    description: str | None = None,
    author: str | None = None,
    fields: Sequence[dict[str, Any]] = (),
    footer: str | None = None,
    thumbnail: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """One embed. `description` may hold line breaks the renderer made; values were cleaned."""
    if len(fields) > FIELDS_MAX:
        raise CardRuleError("too many fields")
    out: dict[str, Any] = {"color": COLOURS[colour]}
    if title:
        out["title"] = clean(title, TITLE_MAX)
    if description:
        out["description"] = description[:DESC_MAX]
    if author:
        out["author"] = {"name": clean(author, TITLE_MAX)}
    if fields:
        out["fields"] = list(fields)
    if footer:
        out["footer"] = {"text": footer[:FOOTER_MAX]}
    if thumbnail and thumbnail.startswith("https://"):
        out["thumbnail"] = {"url": thumbnail}
    if url and url.startswith("https://"):
        out["url"] = url
    return out


VALUE_MAX = 120  # one row value inside a sentence


def say(template: str, **values: object) -> str:
    """A words.py template filled with cleaned row values (numbers stay as they are)."""
    return template.format(
        **{k: v if isinstance(v, int) else clean(v, VALUE_MAX) for k, v in values.items()}
    )


def overflow_note(rest: Sequence[Any]) -> str | None:
    """'2 more are in the thread under this card.', or None when nothing is left over."""
    if not rest:
        return None
    return bw.THREAD_NOTE_ONE if len(rest) == 1 else say(bw.THREAD_NOTE, n=len(rest))


def lines(*parts: str | None) -> str:
    """Join the parts that are there, one per line."""
    return "\n".join(p for p in parts if p)


def closing(text: str) -> str:
    """docs/12b rule 4: every card ends with the closing line."""
    return f"{text}\n\n{bw.CLOSING}" if text else bw.CLOSING


# ------------------------------------------------------------------ Components v2 (M5, M12)


def v2_text(content: str) -> dict[str, Any]:
    return {"type": V2_TEXT, "content": content[:DESC_MAX]}


def v2_section(content: str, thumbnail: str | None = None) -> dict[str, Any]:
    """A text block with a thumbnail on the right; a plain text block without one."""
    if not (thumbnail and thumbnail.startswith("https://")):
        return v2_text(content)
    return {
        "type": V2_SECTION,
        "components": [v2_text(content)],
        "accessory": {"type": V2_THUMB, "media": {"url": thumbnail}},
    }


def v2_separator() -> dict[str, Any]:
    return {"type": V2_SEPARATOR, "divider": True, "spacing": 1}


def v2_container(children: Sequence[dict[str, Any]], colour: str) -> dict[str, Any]:
    return {"type": V2_CONTAINER, "accent_color": COLOURS[colour], "components": list(children)}


# ------------------------------------------------------------------ the card itself


def _check_rows(rows: Sequence[dict[str, Any]]) -> None:
    if len(rows) > ROWS_MAX:
        raise CardRuleError("too many rows")
    for r in rows:
        buttons = [c for c in r.get("components", []) if c.get("type") == BUTTON]
        if len(buttons) > BUTTONS_MAX:
            raise CardRuleError("more than 4 buttons in a row")


def card(
    *,
    card_type: str,
    card_id: str,
    content: str = "",
    embeds: Sequence[dict[str, Any]] = (),
    v2: dict[str, Any] | None = None,
    rows: Sequence[dict[str, Any]] = (),
    acts: dict[str, Any] | None = None,
    keys: Sequence[str] = (),
    thread: Sequence[str] = (),
    thread_name: str = "",
    pin: bool = False,
) -> dict[str, Any]:
    """The bot_post payload: the Discord body plus what the bot and the door need.

    `acts` maps each button action to its door and fixed arguments (saley_card_press);
    `keys` are the items the card is about (the sync trigger finds the card by them);
    `thread` is the overflow, posted once under the card in the same order.
    """
    _check_rows(rows)
    if v2 is not None:
        if content or embeds:
            raise CardRuleError("a Components v2 card has no content and no embeds")
        message: dict[str, Any] = {"flags": COMPONENTS_V2, "components": [v2, *rows]}
    else:
        message = {
            "content": content[:CONTENT_MAX],
            "embeds": list(embeds),
            "components": list(rows),
        }
    payload = {
        "card_type": card_type,
        "card_id": card_id,
        "message": message,
        "acts": acts or {},
        "keys": list(keys),
        "thread": {
            "name": clean(thread_name, 90) or card_id,
            "content": "\n".join(thread),
            "posts": posts(thread),
        }
        if thread
        else None,
        "pin": pin,
    }
    payload["hash"] = rendered_hash(message)
    return payload


def posts(text_lines: Sequence[str]) -> list[str]:
    """Thread lines as Discord posts of at most CONTENT_MAX characters. No line is dropped."""
    out: list[str] = []
    for line in text_lines:
        line = line[:CONTENT_MAX]
        if out and len(out[-1]) + 1 + len(line) <= CONTENT_MAX:
            out[-1] += "\n" + line
        else:
            out.append(line)
    return out


def rendered_hash(message: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(message, sort_keys=True).encode()).hexdigest()


def buttons_of(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Every button and select of a message, for tests and for the done edit."""
    out: list[dict[str, Any]] = []
    for r in message.get("components", []):
        if r.get("type") == ROW:
            out += r.get("components", [])
    return out
