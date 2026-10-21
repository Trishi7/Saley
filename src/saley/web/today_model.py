"""Today read model (R5 T5): funnel, blockers line, needs-look cards, one-line suggestions.

Why a module of its own: app.py and today.py must stay under the 800-line ceiling. Every value
in a line comes from stored facts. The suggestion line passes the value check of
`saley.intel.guard` (CLAUDE.md: never invent a value); a line that fails is dropped and logged.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, datetime
from typing import Any

from sqlalchemy import Connection

from saley.core.clock import now_ist, today_ist
from saley.core.logging import get_logger
from saley.intel import guard
from saley.web import queries, signals
from saley.web import words as w

log = get_logger(__name__)

LINE_VERB = "Add "  # the first word of the SUGGEST_LINE templates
REASON_MAX = 40  # D-R5 brief: the reason of a suggestion line is cut at 40 characters
BLOCKER_CARDS = 3  # D158: the top 3 late items that block a goal
CARDS_PER_KIND = signals.LOOK_SHOWN  # news, event and waiting cards: as many as the old list
WAITING_HOURS = 24  # D158: an Inbox ask older than this waits
TITLE_MAX = 60

LINK_BLOCKERS = {"view": "blockers", "filter": None}  # copy per card, never share

# (key, label, people filter). A person counts in a bar when at this stage or past it.
FUNNEL_STEPS = (
    ("listed", w.FUNNEL_LISTED, "all"),
    ("contacted", w.FUNNEL_CONTACTED, "reached_contacted"),
    ("connected", w.FUNNEL_CONNECTED, "reached_connected"),
    ("talking", w.FUNNEL_TALKING, "reached_talking"),
    ("met", w.FUNNEL_MET, "reached_met"),
    ("won", w.FUNNEL_WON, "stage:Won"),
)
FUNNEL_SQL = (
    "select count(*) as listed, "
    "count(*) filter (where stage_rank >= 2) as contacted, "
    "count(*) filter (where stage_rank >= 3) as connected, "
    "count(*) filter (where stage_rank >= 4) as talking, "
    "count(*) filter (where stage_rank >= 5) as met, "
    "count(*) filter (where stage = 'Won') as won from v_contact_stage"
)


def funnel(conn: Connection, known: bool) -> list[dict[str, Any]]:
    """In the list → Contacted → Connected → Talking now → Met → Won, from v_contact_stage.

    A count of 0 is a real 0 from rows. With no sheet read yet the count is None (unknown).
    """
    counts = queries.rows(conn, FUNNEL_SQL)[0] if known else {}
    return [
        {
            "key": key,
            "label": label,
            "n": counts.get(key) if known else None,
            "link": {"view": "people", "filter": people_filter},
        }
        for key, label, people_filter in FUNNEL_STEPS
    ]


def blockers_line(late_blockers: int | None) -> dict[str, Any]:
    """One line: how many late items block goals, and where the list is."""
    if late_blockers is None:
        return {"count": None, "label": w.NO_DATA, "link": dict(LINK_BLOCKERS)}
    if late_blockers == 0:
        label = w.BLOCKERS_LINE_NONE
    else:
        label = w.fill_count(w.BLOCKERS_LINE_ONE, w.BLOCKERS_LINE, late_blockers)
    return {"count": late_blockers, "label": label, "link": dict(LINK_BLOCKERS)}


# ------------------------------------------------------------------ needs a look


def cut(text: str, limit: int = TITLE_MAX) -> str:
    """At most `limit` characters; a longer text ends with "…", never a bare cut."""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _card(kind: str, title: str, why: str, link: dict[str, Any]) -> dict[str, Any]:
    return {"kind": kind, "title": cut(title), "why": cut(why), "link": link}


def _blocker_cards(conn: Connection, short: Callable[[str], str]) -> list[dict[str, Any]]:
    found = queries.rows(
        conn,
        "select item, late_by_days, blocks_goals from v_deliverable_late "
        "where cardinality(blocks_goals) > 0 order by late_by_days desc, item, id limit :n",
        n=BLOCKER_CARDS,
    )
    return [
        _card(
            "blocker",
            short(r["item"]),
            w.fill_count(
                w.LOOK_BLOCKER_WHY_ONE,
                w.LOOK_BLOCKER_WHY,
                r["late_by_days"],
                goals=", ".join(r["blocks_goals"]),
            ),
            dict(LINK_BLOCKERS),
        )
        for r in found
    ]


def _waiting_cards(conn: Connection, now: datetime, viewer: str | None) -> list[dict[str, Any]]:
    """Asks to the viewer only (D172). With no viewer there is no card, never all asks."""
    if viewer is None:
        return []
    found = queries.rows(
        conn,
        "select ref, at from v_inbox where item_kind = 'ask' and for_user = :viewer "
        "and at < now() - make_interval(hours => :h) order by at, id limit :n",
        viewer=viewer,
        h=WAITING_HOURS,
        n=CARDS_PER_KIND,
    )
    return [
        _card(
            "waiting",
            r["ref"] or "",
            w.fill(w.LOOK_WAITING_WHY, n=int((now - r["at"]).total_seconds() // 3600)),
            {"view": "asks", "filter": None},  # the Inbox page id (rail #asks)
        )
        for r in found
    ]


def look_cards(
    conn: Connection,
    short: Callable[[str], str],
    viewer: str | None,
    today: date | None = None,
    rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Cards for Today · needs a look (D158): news ≥ 4, events ≤ 3 days, blockers, asks.

    Each card opens a page with its own list. A card that only points to another page and
    holds no item of its own is not made. `rows` is the shared read of the page; without it
    the rows are read per kind, so one kind cannot starve the other (D173).
    """
    day = today or today_ist()
    if rows is None:
        rows = signals.look_rows(conn, CARDS_PER_KIND)
    cards: list[dict[str, Any]] = []
    for kind in ("signal", "event"):
        for row in [r for r in rows if r["kind"] == kind][:CARDS_PER_KIND]:
            item = signals.look_item(row, day)
            cards.append(
                _card(
                    "news" if kind == "signal" else "event",
                    item["title"],
                    item["detail"],
                    item["link"],
                )
            )
    cards += _blocker_cards(conn, short)
    cards += _waiting_cards(conn, now_ist(), viewer)
    return cards


# ------------------------------------------------------------------ Saley suggests


def _scalars(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for inner in value.values():
            yield from _scalars(inner)
    elif isinstance(value, list | tuple | set):
        for inner in value:
            yield from _scalars(inner)
    elif value is not None:
        yield str(value)


def suggest_facts(item: dict[str, Any]) -> list[str]:
    """The stored facts of one suggestion: name, company, title, score parts and evidence.

    The reason is not a fact: it is built from the part labels, so it is what we check.
    """
    facts = [item.get("subject"), item.get("company"), item.get("title")]
    facts += [p.get("label") for p in item.get("parts", [])]
    facts += list(_scalars(item.get("evidence", {})))
    return [f for f in facts if f]


def cut_reason(reason: str, limit: int = REASON_MAX) -> str:
    """At most `limit` characters, cut at a word end. A cut ends with "…"."""
    if len(reason) <= limit:
        return reason
    head = reason[: limit - 1]
    if " " in head and reason[limit - 1] != " ":
        head = head.rsplit(" ", 1)[0]
    return head.rstrip(" ·,;:-") + "…"


def suggest_line(item: dict[str, Any]) -> str:
    """ "Add {name} ({company}) to pipeline — {reason}" from the stored facts of the item."""
    reason = cut_reason(item["reason"])
    name, company = item["subject"], item.get("company")
    if company and company != name:
        return cut(w.fill(w.SUGGEST_LINE, name=name, company=company, reason=reason))
    return cut(w.fill(w.SUGGEST_LINE_NO_COMPANY, name=name, reason=reason))


def checked_line(item: dict[str, Any]) -> str | None:
    """The one line of a Suggestions page card (R5 T15), or None when the line would hold a
    value that is not in the item's facts (the card then shows its name and company only)."""
    kept = with_lines([item])
    return kept[0]["line"] if kept else None


def with_lines(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each item gets a `line`. An item whose line holds a value not in its facts is dropped."""
    kept: list[dict[str, Any]] = []
    for item in items:
        line = suggest_line(item)
        # The verb of the template is not a value: "Add AI" must not read as a name.
        found = guard.find_violations(line.removeprefix(LINE_VERB), suggest_facts(item))
        if found:
            log.warning(
                "suggest_line_dropped",
                id=item.get("id"),
                kind=item.get("kind"),
                values=[f"{v.kind}:{v.value}" for v in found[:5]],
            )
            continue
        kept.append({**item, "line": line})
    return kept
