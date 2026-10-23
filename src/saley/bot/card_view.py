"""The message a card shows now: the rendered payload plus its state (docs/09 §6).

Why a pure function: the first send, a press, a dashboard act and an undo all show the same
card, so one function builds the message from the row. Edits keep the embed (docs/09 §6): only
the footer, the held line and the buttons change. After an act the buttons go and the footer
says who did it, where and when, with the undo time left; an undo brings the buttons back.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from saley.bot import components as c
from saley.bot import words as bw
from saley.core import clock

SECONDS_PER_MINUTE = 60


def done_line(row: Any, now: datetime) -> str | None:
    """'Done by Sid in Discord at 11:24 · Undo 4 min left', or None while the card is open."""
    if row.done_at is None:
        return None
    template = bw.DONE_DISCORD if row.done_surface == "discord" else bw.DONE_DASHBOARD
    line = template.format(who=c.clean(row.done_by or "?", 40), t=clock.hhmm(row.done_at))
    left = undo_minutes_left(row, now)
    return f"{line} · {bw.UNDO_LEFT.format(n=left)}" if left else line


def undo_minutes_left(row: Any, now: datetime) -> int:
    """Whole minutes of the undo window that are left (rounded up), 0 when it is closed."""
    if row.undo_until is None or row.done_write_id is None or row.undo_until <= now:
        return 0
    return -(-int((row.undo_until - now).total_seconds()) // SECONDS_PER_MINUTE)


def _links(message: dict[str, Any]) -> list[dict[str, Any]]:
    return [b for b in c.buttons_of(message) if b.get("style") == c.STYLE["link"]]


def _done_rows(message: dict[str, Any], row: Any, now: datetime) -> list[dict[str, Any]]:
    """After an act: Undo (while the window is open) and the links. Nothing to press twice."""
    keep: list[dict[str, Any]] = []
    if undo_minutes_left(row, now):
        keep.append(c.button(bw.BTN_UNDO, row.card_id, "undo"))
    keep += _links(message)[: c.BUTTONS_MAX - len(keep)]
    return [c.row(*keep)] if keep else []


def view(row: Any, *, head: str, now: datetime | None = None) -> dict[str, Any]:
    """The message body for POST or PATCH. `head` is the tag and the mentions (deliver.py)."""
    moment = now or clock.now_ist()
    payload = row.payload
    message = copy.deepcopy(payload["message"])
    state = done_line(row, moment)
    extra = [x for x in (state, row.held_reason) if x]
    v2 = message.get("flags") == c.COMPONENTS_V2
    rows = [x for x in message["components"] if x.get("type") == c.ROW]
    blocks = [x for x in message["components"] if x.get("type") != c.ROW]
    if state:
        rows = _done_rows(message, row, moment)
    if v2:
        top = [c.v2_text(head.strip())] if head.strip() else []
        tail = [c.v2_text(x) for x in extra]
        return {"flags": c.COMPONENTS_V2, "components": [*top, *blocks, *tail, *rows]}
    embeds = message.get("embeds", [])
    content = message.get("content") or ""
    if state and embeds:
        embeds[-1]["footer"] = {"text": state}
        extra = [x for x in extra if x != state]
    body = c.lines(content, *extra)
    return {
        "content": (head + body)[: c.CONTENT_MAX],
        "embeds": embeds,
        "components": rows,
    }
