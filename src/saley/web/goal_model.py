"""Q4 goals read model: the progress ring and the one-line note of each active person (R5 T5).

Why a module of its own: app.py is near the 800-line ceiling. Nothing here guesses: a ring
without a goal need has no percent (D159), and a note is a stored next step or the stage.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection

from saley.web import queries
from saley.web import words as w

NOTE_MAX = 60  # characters of the one-line note, "…" included


def progress(won: int, need: int | None) -> dict[str, Any]:
    """The ring: won of need. Without a need (missing or 0) there is no percent (D159)."""
    if not need or need < 0:
        return {"won": won, "need": need or None, "pct": None}
    return {
        "won": won,
        "need": need,
        "pct": (200 * won + need) // (2 * need),
    }  # half-up, not banker's


def cut(text: str, limit: int = NOTE_MAX) -> str:
    """One line, at most `limit` characters. A cut ends with "…"."""
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def next_steps(conn: Connection, ids: list[int]) -> dict[int, str]:
    """The stored Next Step text of each person. A blank cell is left out."""
    if not ids:
        return {}
    found = queries.rows(
        conn, "select id, next_step from v_contact_stage where id = any(:ids)", ids=ids
    )
    return {r["id"]: r["next_step"] for r in found if (r["next_step"] or "").strip()}


def decorate(conn: Connection, items: list[dict[str, Any]]) -> None:
    """Add `progress` to each goal and `note` to each active person, in place."""
    steps = next_steps(conn, [p["id"] for g in items for p in g["active"]])
    for g in items:
        g["progress"] = progress(g["won"], g["need"])
        for p in g["active"]:
            step = steps.get(p["id"])
            p["note"] = cut(step if step else w.fill(w.GOAL_NOTE_STAGE, stage=p["stage"]))
