"""Base models shared by every response model: `Out`, the meta block, links, windows.

Why: each view module imports these from one place, so no view module needs another view's file.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Evidence(Out):
    row: str | None = None


class Meta(Out):
    sync_id: int | None
    last_computed: str | None
    last_computed_label: str
    stale: bool


class Link(Out):
    view: str
    filter: str | None = None


class Tile(Out):
    key: str
    label: str
    value: int | str | None
    detail: str
    source: str
    link: Link


class NewsStep(Out):
    """One step of the news funnel. `href` opens the list behind the number (D73)."""

    key: str
    step: str
    n: int | None
    href: str
    note: str | None = None


class StrictIn(BaseModel):
    """Request bodies refuse fields they do not declare."""

    model_config = ConfigDict(extra="forbid")


class Window(Out):
    """The time window a list covers, in words and in dates (D22: every answer states it)."""

    from_: str | None = Field(alias="from")
    to: str | None
    label: str

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class ListMeta(Meta):
    """What every R3 list adds: its window, how many seen items it left out (D75; a count,
    no "left out" line since D157, the line field went at the cut-over, D224)."""

    window: Window
    left_out_seen: int


class TabbedMeta(ListMeta):
    """What Signals, Suggestions and Events add for the New / Seen earlier tabs (D157)."""

    view: Literal["new", "seen"] = "new"
    new_total: int = 0
    seen_total: int = 0
    new_empty: str | None = None


def strip(model: type[Out], data: dict[str, Any]) -> dict[str, Any]:
    """Validate and drop extra keys (used in tests to check field sets)."""
    return model.model_validate(data).model_dump()
