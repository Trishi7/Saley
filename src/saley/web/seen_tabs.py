"""New and Seen earlier tabs for Signals, Suggestions and Events (R5 T7, D157).

Why: the old lists hid what a person had seen and then said "207 you saw are left out". The new
lists work like a mailbox. The New tab holds what this person has not seen. The Seen earlier tab
holds the rest. Both counts ride on every answer, so the tab labels show them. No answer in
this mode carries a "left out" line. Seen is per person (`user_seen`), never per channel.

The legacy page still sends `include_seen` and reads `left_out_note`. When `view` is absent, the
answer is what it was (new items only, or all with `include_seen`), plus the new count fields.
Both go away at the cut-over (T16).

Also here: the `cat=` check (D156). The value is one of the topic names, any case. A source
letter (A-L) or any other text is a 422.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import HTTPException, Query
from sqlalchemy import Connection, text

from saley.news.topic import category_name
from saley.web import words as w

PAGE_SIZE = 10
MAX_OFFSET = 1000

ViewParam = Annotated[Literal["new", "seen"] | None, Query()]
CatParam = Annotated[str | None, Query(max_length=40)]


@dataclass(frozen=True)
class SeenPlan:
    """Which items a request shows: `new`, `seen` or `all` (legacy `include_seen`)."""

    mode: Literal["new", "seen", "all"]
    tabbed: bool  # the caller sent `view`: pages of 10, no "left out" line

    def page(self, limit: int, offset: int) -> tuple[int, int]:
        """(limit, offset) of the page. A tabbed list is 10 a page and offset 0 to 1000."""
        if not self.tabbed:
            return limit, offset
        if offset > MAX_OFFSET:
            raise HTTPException(status_code=422, detail=w.ERR_PAGE_OFFSET)
        return min(limit, PAGE_SIZE), offset

    def clause(self, seen_sql: str) -> str:
        """The SQL condition that keeps the shown side, to append to a where clause."""
        if self.mode == "all":
            return ""
        return f" and {seen_sql}" if self.mode == "seen" else f" and not {seen_sql}"

    def pick[T](self, items: Sequence[T], is_seen: Callable[[T], bool]) -> list[T]:
        """The same choice for a list built in Python (Suggestions are ranked by plain code)."""
        if self.mode == "all":
            return list(items)
        return [i for i in items if is_seen(i) == (self.mode == "seen")]

    def total(self, new_total: int, seen_total: int) -> int:
        return {"new": new_total, "seen": seen_total, "all": new_total + seen_total}[self.mode]

    def left_out(self, seen_total: int) -> int:
        """Legacy count of hidden seen items. Always 0 for a tabbed list (D157)."""
        return seen_total if self.mode == "new" and not self.tabbed else 0

    def fields(self, new_total: int, seen_total: int) -> dict[str, Any]:
        """The fields every tabbed answer adds. `new_empty` is the words line for an empty New."""
        empty = self.mode == "new" and new_total == 0
        return {
            "view": "seen" if self.mode == "seen" else "new",
            "new_total": new_total,
            "seen_total": seen_total,
            "new_empty": w.NOTHING_NEW if empty else None,
        }


def plan(view: str | None, include_seen: bool = False) -> SeenPlan:
    if view == "seen":
        return SeenPlan("seen", True)
    if view == "new":
        return SeenPlan("new", True)
    return SeenPlan("all" if include_seen else "new", False)


def counts(
    conn: Connection, source_sql: str, where: str, params: dict[str, Any], seen_sql: str
) -> tuple[int, int]:
    """(new, seen) counts of one filtered list in one query. `source_sql` is e.g. 'v_x t'."""
    row = conn.execute(
        text(
            # Every piece is a constant of the caller; user values are bound parameters.
            f"select count(*) filter (where not {seen_sql}), count(*) filter (where {seen_sql}) "
            f"from {source_sql} where {where}"
        ),
        params,
    ).one()
    return int(row[0]), int(row[1])


def split_counts[T](items: Sequence[T], is_seen: Callable[[T], bool]) -> tuple[int, int]:
    seen = sum(1 for i in items if is_seen(i))
    return len(items) - seen, seen


def topic_filter(cat: str | None) -> str | None:
    """The canonical topic for `cat=`, or a 422 for a letter or any other text (D156)."""
    if cat is None:
        return None
    topic = category_name(cat)
    if topic is None:
        raise HTTPException(status_code=422, detail=w.ERR_CAT)
    return topic
