"""R7-K01: every card renderer keeps the card rules (docs/09 §5, docs/12b, D260, D261).

One parametrised test per card type, so the matrix has one row per type (R7-K01-<type>).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

import pytest

from saley.bot import cards, components
from saley.bot import words as bw
from saley.bot.cards.samples import CARD_IDS, SAMPLES
from saley.core.settings import get_settings
from saley.intel import guard
from saley.qa.ste_lint import banned_words

COLOUR_OF = {
    "M1": "grey",
    "M2": "news",
    "M3": "suggestion",
    "M4": "event",
    "M5": "meeting",
    "M6": "meeting",
    "M7": "grey",
    "M8": "grey",
    "M9": "grey",
    "M10": "suggestion",
    "M11": "news",
    "M12": "grey",
    "M13": "blocker",
    "M15": "ok",
}
# docs/12b §3 "Avoid", on top of docs/ste-banned-words.txt.
VOICE_AVOID = (
    "overdue",
    "shout",
    "chase",
    "kindly",
    "please be advised",
    "executing",
    "processing",
    "click here",
    "asap",
    "urgent",
    "reply with yes",
    "choose 1 or 2",
)
LIST_CARDS = {"M2": "stories", "M3": "people", "M4": "events", "M13": "items"}


def _leaves(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for v in value.values():
            yield from _leaves(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _leaves(v)
    elif value is not None:
        yield str(value)


def _texts(message: dict[str, Any], thread: dict[str, Any] | None) -> list[str]:
    """Every piece of text a person can read: content, embeds, v2 blocks, labels, thread."""
    out = [message.get("content") or ""]
    for e in message.get("embeds", []):
        out += [
            e.get("title", ""),
            e.get("description", ""),
            (e.get("author") or {}).get("name", ""),
        ]
        out += [(e.get("footer") or {}).get("text", "")]
        out += [part for f in e.get("fields", []) for part in (f["name"], f["value"])]

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "content" in node and isinstance(node["content"], str):
                out.append(node["content"])
            for key in ("label", "placeholder"):
                if isinstance(node.get(key), str):
                    out.append(node[key])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(message.get("components", []))
    if thread:
        out.append(thread["content"])
    return [t for t in out if t]


def _render(card_type: str) -> dict[str, Any]:
    return cards.render(card_type, SAMPLES[card_type], CARD_IDS[card_type])


def test_every_type_has_a_renderer_and_a_sample() -> None:
    expected = {f"M{i}" for i in range(1, 16)} | {"A1", "A2"}
    assert set(cards.RENDERERS) == expected == set(SAMPLES)


@pytest.mark.parametrize("card_type", sorted(SAMPLES, key=lambda t: (t[0], int(t[1:]))))
def test_card_rules(card_type: str) -> None:
    payload = _render(card_type)
    message, thread = payload["message"], payload["thread"]
    texts = _texts(message, thread)
    visible = "\n".join(texts)

    # Row values only: every name, number, date, URL and email is in the sample rows or the
    # fixed words. Counts the renderer made (3 stories) are small numbers.
    facts = [*_leaves(SAMPLES[card_type]), get_settings().public_url, *map(str, range(30))]
    facts += [v for k, v in vars(bw).items() if k.isupper() and isinstance(v, str)]
    # A greeting or a button before a name ("Hi Sid", "Ask Sid") and a possessive are the
    # template's words, not new names.
    leaves = list(_leaves(SAMPLES[card_type]))
    facts += [f"{w} {x}" for x in leaves for w in ("Hi", "Ask")] + [f"{x}'s" for x in leaves]
    problems = {}
    for piece in texts:
        for line in piece.splitlines():
            if found := guard.find_violations(line, facts):
                problems[line] = [str(v) for v in found]
    assert problems == {}

    # At most BOT_LIST_MAX items in the card; the rest in the thread; nothing dropped.
    if card_type in LIST_CARDS:
        items = SAMPLES[card_type][LIST_CARDS[card_type]]
        if card_type == "M4":  # registered first, the rest in sample order
            items = sorted(items, key=lambda e: not e["registered"])
        assert len(items) == 7 and thread is not None
        firsts = [str(i.get("title") or i.get("name") or i.get("item")) for i in items]
        in_card = "\n".join(_texts(message, None))
        assert sum(f in in_card for f in firsts) == components.list_max() == 5
        assert all(f in thread["content"] for f in firsts[5:])
        assert "2 more are in the thread under this card." in in_card

    # One row of at most 4 buttons, a select only when there is more than one choice.
    for row in message["components"]:
        if row.get("type") == components.ROW:
            assert len(row["components"]) <= components.BUTTONS_MAX
    controls = components.buttons_of(message)
    links = [b for b in controls if b.get("style") == components.STYLE["link"]]
    for b in links:
        assert b["label"].split()[0].casefold() not in components.BAD_LINK_WORDS
        assert b["url"].startswith(get_settings().public_url.rstrip("/") + "/")
        assert re.search(r"[?&]card=", b["url"]) or card_type in ("A1", "A2")
    for b in controls:
        if "custom_id" in b:
            assert len(b["custom_id"]) <= components.CUSTOM_ID_MAX
            action = b["custom_id"].split(":")[-1]
            local = card_type in ("A1", "A2") or b.get("disabled")
            assert local or action in payload["acts"], f"{action} has no door"

    # The closing line, except M15 and A2 (docs/12b rule 4).
    if card_type in cards.NO_CLOSING:
        assert bw.CLOSING not in visible
    else:
        assert bw.CLOSING in visible

    # STE and the voice words.
    lower = visible.casefold()
    for word in [*banned_words(), *VOICE_AVOID]:
        assert not re.search(rf"\b{re.escape(word)}\b", lower), word

    # Colour by type (Components v2 uses the container accent).
    if card_type in COLOUR_OF:
        colours = [e["color"] for e in message.get("embeds", [])]
        colours += [c["accent_color"] for c in message["components"] if "accent_color" in c]
        assert colours and set(colours) == {components.COLOURS[COLOUR_OF[card_type]]}

    # The payload is what bot_post stores: JSON, a hash, the right type and id.
    assert payload["card_type"] == card_type and payload["card_id"] == CARD_IDS[card_type]
    assert payload["hash"] == components.rendered_hash(message)
    json.dumps(payload)


def test_components_v2_cards_have_no_content_and_carry_the_flag() -> None:
    for card_type in ("M5", "M12"):
        message = _render(card_type)["message"]
        assert message["flags"] == components.COMPONENTS_V2
        assert "content" not in message and "embeds" not in message
    assert _render("M12")["pin"] is True and _render("M5")["pin"] is False


def test_a_select_menu_when_more_than_one_choice() -> None:
    many = _render("M3")["message"]["components"]
    assert many[0]["components"][0]["type"] == components.SELECT
    assert len(many[0]["components"][0]["options"]) == 5
    one = cards.render("M3", {"who": "Sid", "people": SAMPLES["M3"]["people"][:1]}, "m3-one")
    first_row = one["message"]["components"][0]["components"]
    assert first_row[0]["label"] == bw.BTN_DO_IT
    assert one["acts"]["do"] == {
        "door": "commit",
        "args": {
            "texts": {"200": "Add Test Person One (Test Company A) to pipeline"},
            "days": 2,
            "ref": "200",
        },
    }
    assert many_acts_skip_ids() == [200, 201, 202, 203, 204]


def many_acts_skip_ids() -> list[int]:
    return _render("M3")["acts"]["skip"]["args"]["ids"]


def test_row_text_cannot_ping_or_format() -> None:
    sample = {**SAMPLES["M9"], "text": "@everyone <@123> **loud** `code`"}
    content = json.dumps(cards.render("M9", sample, "m9-x")["message"])
    assert "@everyone" not in content and "<@123>" not in content and "**loud**" not in content


@pytest.mark.parametrize(
    ("label", "ok"),
    [("Open", False), ("View all", False), ("Click here", False), ("The brief", True)],
)
def test_link_text_rule(label: str, ok: bool) -> None:
    if ok:
        assert components.link(label, "https://x.example/")["label"] == label
    else:
        with pytest.raises(components.CardRuleError):
            components.link(label, "https://x.example/")


def test_limits_raise() -> None:
    b = components.button("A", "m1-x", "a")
    with pytest.raises(components.CardRuleError):
        components.row(b, b, b, b, b)
    with pytest.raises(components.CardRuleError):
        components.custom_id("x" * 81, "do")
    with pytest.raises(components.CardRuleError):
        components.card(card_type="M5", card_id="m5-x", content="x", v2=components.v2_text("y"))


def test_a_long_overflow_is_split_never_cut() -> None:
    items = [
        {"owner": "Kushal", "item": f"Item number {i:03d} " + "x" * 60, "goal": "G1"}
        for i in range(80)
    ]
    thread = cards.render("M13", {"items": items}, "m13-long")["thread"]
    assert len(thread["posts"]) > 1
    assert all(len(p) <= components.CONTENT_MAX for p in thread["posts"])
    joined = "\n".join(thread["posts"])
    assert all(f"Item number {i:03d}" in joined for i in range(5, 80))
