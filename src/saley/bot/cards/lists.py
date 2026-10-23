"""Cards that hold a list: M2 needs a look, M3 suggestions, M4 events, M13 blockers.

Why one module: the four share the list rule (D260): at most BOT_LIST_MAX items in the card,
the rest in one thread post in the same order, and a select menu when more than one item can
be acted on.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from saley.bot import components as c
from saley.bot import links
from saley.bot import words as bw

COMMIT_DAYS = 2  # M3 Do it: the commitment of the presser is due in 2 days


def _thread(rest: Sequence[str]) -> list[str]:
    return [bw.THREAD_HEAD, *(f"• {c.clean(line, 300)}" for line in rest)] if rest else []


def m2_needs_a_look(d: Mapping[str, Any], card_id: str) -> dict[str, Any]:
    """News that matters to a person: one blue embed per story (docs/09 M2).

    d: who, stories [{story_id, title, company, why, source, day, thumbnail}], page_label,
    page_url (the owner's or company's page; else the Signals page)."""
    stories = list(d["stories"])
    shown, rest = c.split(stories)
    embeds = []
    for i, s in enumerate(shown):
        last = i == len(shown) - 1
        why = c.clean(s["why"], 300)
        embeds.append(
            c.embed(
                colour="news",
                author=c.say(bw.LOOK_AUTHOR, company=s["company"]),
                title=s["title"],
                description=c.closing(c.lines(why, c.overflow_note(rest))) if last else why,
                fields=[
                    c.field(bw.FIELD_COMPANY, s["company"]),
                    c.field(bw.FIELD_SOURCE, f"{s['source']} · {s['day']}"),
                ],
                thumbnail=s.get("thumbnail"),
            )
        )
    head = bw.LOOK_HEAD_ONE if len(stories) == 1 else bw.LOOK_HEAD
    ids = [int(s["story_id"]) for s in stories]
    seen = {"door": "mark_seen", "args": {"item_type": "story", "ids": ids}}
    page = d.get("page_url") or links.url("signals", card=card_id)
    label = d.get("page_label") or bw.LINK_SIGNALS
    return c.card(
        card_type="M2",
        card_id=card_id,
        content=c.say(head, who=d["who"], n=len(stories)),
        embeds=embeds,
        rows=[
            c.row(
                c.button(bw.BTN_SEEN, card_id, "seen", "primary"),
                c.button(bw.BTN_NOT_USEFUL, card_id, "not_useful"),
                c.link(label, page),
            )
        ],
        acts={"seen": seen, "not_useful": seen},
        thread=_thread([f"{s['title']} · {s['company']} · {s['source']}" for s in rest]),
        thread_name=bw.LINK_SIGNALS,
    )


def m3_suggestions(d: Mapping[str, Any], card_id: str) -> dict[str, Any]:
    """Saley suggests people to reach (docs/09 M3, Q-R7-1).

    Do it: the presser takes on the move (a commitment, due in COMMIT_DAYS). Skip: the shown
    people count as seen for the presser, as on the Suggestions page.
    d: who, people [{ref_id, name, company, reason, step}] (step: the checked Suggestions line).
    """
    people = list(d["people"])
    shown, rest = c.split(people)
    fields = [
        c.field(
            f"{p['name']} · {p['company']}",
            c.say(bw.SUGGEST_LINE, reason=p["reason"], step=p["step"]),
            inline=False,
        )
        for p in shown
    ]
    body = c.embed(
        colour="suggestion",
        author=bw.SUGGEST_AUTHOR,
        description=c.closing(c.overflow_note(rest) or ""),
        fields=fields,
    )
    ids = [str(p["ref_id"]) for p in shown]
    do_args: dict[str, Any] = {
        "texts": {str(p["ref_id"]): c.clean(p["step"], 300) for p in shown},
        "days": COMMIT_DAYS,
    }
    rows: list[dict[str, Any]] = []
    first: list[dict[str, Any]] = []
    if len(shown) > 1:
        options = [(f"{p['name']} · {p['company']}", p["ref_id"]) for p in shown]
        rows.append(c.select(card_id, "do", bw.SELECT_DO, options))
    else:
        do_args["ref"] = ids[0]
        first = [c.button(bw.BTN_DO_IT, card_id, "do", "success")]
    rows.append(
        c.row(
            *first,
            c.button(bw.BTN_SKIP, card_id, "skip"),
            c.disabled(bw.BTN_FIND_SOON, card_id, "find"),
            c.link(bw.LINK_SUGGESTIONS, links.url("suggestions", card=card_id)),
        )
    )
    head = bw.SUGGEST_HEAD_ONE if len(people) == 1 else bw.SUGGEST_HEAD
    seen = {"item_type": "suggest_warm", "ids": [int(i) for i in ids]}
    return c.card(
        card_type="M3",
        card_id=card_id,
        content=c.say(head, who=d["who"], n=len(people)),
        embeds=[body],
        rows=rows,
        acts={
            "do": {"door": "commit", "args": do_args},
            "skip": {"door": "mark_seen", "args": seen},
        },
        thread=_thread([f"{p['name']} · {p['company']} · {p['reason']}" for p in rest]),
        thread_name=bw.LINK_SUGGESTIONS,
    )


def m4_events(d: Mapping[str, Any], card_id: str) -> dict[str, Any]:
    """Events coming up, registered first (docs/09 M4).

    d: who, events [{event_id, name, city, day, who_goes, registered}]."""
    events = sorted(d["events"], key=lambda e: not e["registered"])  # stable: registered first
    shown, rest = c.split(events)

    def value(e: Mapping[str, Any]) -> str:
        line = c.say(bw.EVENT_LINE, city=e["city"] or "—", day=e["day"])
        if e["registered"]:
            line += f" · {bw.EVENT_REGISTERED}"
        return c.lines(line, c.say(bw.EVENT_WHO, who=e["who_goes"]) if e.get("who_goes") else None)

    body = c.embed(
        colour="event",
        author=bw.EVENTS_AUTHOR,
        description=c.closing(c.overflow_note(rest) or ""),
        fields=[c.field(e["name"], value(e), inline=False) for e in shown],
    )
    ids = [str(e["event_id"]) for e in shown]
    interest: dict[str, Any] = {"kind": "interest", "entity_type": "event", "text": "interest"}
    rows: list[dict[str, Any]] = []
    first: list[dict[str, Any]] = []
    if len(shown) > 1:
        options = [(f"{e['name']} · {e['day']}", e["event_id"]) for e in shown]
        rows.append(c.select(card_id, "interest", bw.SELECT_INTEREST, options))
        interest["values"] = ids
    else:
        interest["entity_id"] = int(ids[0])
        first = [c.button(bw.BTN_INTEREST, card_id, "interest", "primary")]
    rows.append(
        c.row(
            *first,
            c.button(bw.BTN_SKIP, card_id, "skip"),
            c.link(bw.LINK_EVENTS, links.url("events", card=card_id)),
        )
    )
    head = bw.EVENTS_HEAD_ONE if len(events) == 1 else bw.EVENTS_HEAD
    return c.card(
        card_type="M4",
        card_id=card_id,
        content=c.say(head, who=d["who"], n=len(events)),
        embeds=[body],
        rows=rows,
        acts={
            "interest": {"door": "fact", "args": interest},
            "skip": {"door": "close", "args": {}},
        },
        thread=_thread([f"{e['name']} · {e['city'] or '—'} · {e['day']}" for e in rest]),
        thread_name=bw.LINK_EVENTS,
    )


def m13_blockers(d: Mapping[str, Any], card_id: str) -> dict[str, Any]:
    """Late items that block goals (docs/09 M13). Never the word "overdue" (C05).

    d: items [{owner, item, goal}]."""
    items = list(d["items"])
    shown, rest = c.split(items)

    def line(i: Mapping[str, Any]) -> str:
        return c.say(bw.BLOCKER_LINE, owner=i["owner"], item=i["item"], goal=i["goal"])

    body = c.embed(
        colour="blocker",
        description=c.closing(c.lines(*(f"• {line(i)}" for i in shown), c.overflow_note(rest))),
    )
    return c.card(
        card_type="M13",
        card_id=card_id,
        content=bw.BLOCKER_HEAD,
        embeds=[body],
        rows=[c.row(c.link(bw.LINK_BLOCKERS, links.url("blockers", card=card_id)))],
        thread=_thread([line(i) for i in rest]),
        thread_name=bw.LINK_BLOCKERS,
    )
