"""Cards about one person or one company: M5, M6, M7, M10, M11.

Why one module: each card is about one sheet row, links to that row's page, and its buttons
call a door with that row's id. The door arguments are fixed here, from the row; the presser
gives only a modal value or a pick, which `saley_card_press` checks again.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from saley.bot import components as c
from saley.bot import links
from saley.bot import words as bw

MODAL_TEXT_MAX = 300
MODAL_DATE_MAX = 20
QUESTIONS_MAX = 3
TOUCHES_MAX = 3


def _page(d: Mapping[str, Any], card_id: str) -> dict[str, Any]:
    """The link to the person (or company) page, named after them (D261)."""
    if d.get("contact_id"):
        return c.link(
            c.say(bw.LINK_PAGE, name=d["name"]), links.url("people", d["contact_id"], card_id)
        )
    return c.link(
        c.say(bw.LINK_PAGE, name=d["company"]), links.url("accounts", d["account_id"], card_id)
    )


def _text_field(label: str, *, paragraph: bool = True) -> dict[str, Any]:
    return {
        "id": "text",
        "label": label,
        "style": "paragraph" if paragraph else "short",
        "max": MODAL_TEXT_MAX,
    }


DATE_FIELD = {
    "id": "date",
    "label": bw.MODAL_DATE_LABEL,
    "placeholder": bw.MODAL_DATE_HINT,
    "style": "short",
    "max": MODAL_DATE_MAX,
}


def m5_meeting_brief(d: Mapping[str, Any], card_id: str) -> dict[str, Any]:
    """The meeting brief, Components v2 (docs/09 M5).

    d: who, name, company, title, stage, day, contact_id, thumbnail, working_on,
    touches [{label, day}], package, questions [str], risk."""
    touches = [f"• {c.clean(t['label'], 60)} · {c.clean(t['day'], 20)}" for t in d["touches"]]
    questions = [f"• {c.clean(q, 200)}" for q in d.get("questions") or []][:QUESTIONS_MAX]
    head = c.lines(
        c.say(bw.MEET_HEAD, who=d["who"], name=d["name"], day=d["day"]), bw.MEET_HEAD_MORE
    )
    title = f"**{c.say(bw.MEET_TITLE, company=d['company'], name=d['name'])}**"
    sub = " · ".join(c.clean(v, 60) for v in (d.get("title"), d.get("stage")) if v)
    parts: list[dict[str, Any]] = [
        c.v2_text(head),
        c.v2_section(c.lines(title, sub), d.get("thumbnail")),
        c.v2_separator(),
    ]
    if d.get("working_on"):
        parts.append(c.v2_text(c.clean(d["working_on"], 300)))
    if touches:
        parts.append(c.v2_text(c.lines(f"**{bw.MEET_TOUCHES}**", *touches[:TOUCHES_MAX])))
    parts.append(c.v2_separator())
    if d.get("package"):
        parts.append(c.v2_text(c.say(bw.MEET_PACKAGE, package=d["package"])))
    if questions:
        parts.append(c.v2_text(c.lines(f"**{bw.MEET_QUESTIONS}**", *questions)))
    if d.get("risk"):
        parts.append(c.v2_text(c.say(bw.MEET_RISK, risk=d["risk"])))
    parts.append(c.v2_text(bw.CLOSING))
    note = {
        "door": "fact",
        "args": {"kind": "note", "entity_type": "contact", "entity_id": int(d["contact_id"])},
        "modal": {
            "title": c.say(bw.MODAL_NOTE, name=d["name"]),
            "fields": [_text_field(bw.MODAL_NOTE_LABEL)],
        },
    }
    return c.card(
        card_type="M5",
        card_id=card_id,
        v2=c.v2_container(parts, "meeting"),
        rows=[
            c.row(
                c.button(bw.BTN_NOTE, card_id, "note", "primary"),
                c.link(bw.LINK_BRIEF, links.url("people", d["contact_id"], card_id)),
            )
        ],
        acts={"note": note},
    )


def m6_after_meeting(d: Mapping[str, Any], card_id: str) -> dict[str, Any]:
    """ "How did it go?" after a meeting (docs/09 M6, 12b example).

    d: who, name, company, contact_id, row_key, statuses [the Meeting Status values the sheet
    uses], no_show (the sheet's "no show" value, or None), account_id."""
    statuses = [str(s) for s in d["statuses"]]
    targets = [int(d["contact_id"])]
    next_step = {
        "door": "next_step",
        "args": {"targets": targets, "values": statuses},
        "modal": {
            "title": c.say(bw.MODAL_NEXT, name=d["name"]),
            "fields": [
                _text_field(bw.MODAL_NEXT_LABEL, paragraph=False),
                DATE_FIELD,
                {"id": "status", "label": bw.MODAL_STATUS_LABEL, "options": statuses},
            ],
        },
    }
    acts: dict[str, Any] = {"next": next_step}
    buttons = [c.button(bw.BTN_NEXT, card_id, "next", "success")]
    if d.get("no_show"):
        acts["no_show"] = {
            "door": "act",
            "args": {
                "targets": targets,
                "role": "meeting_status",
                "values": [d["no_show"]],
                "value": d["no_show"],
            },
        }
        buttons.append(c.button(bw.BTN_NO_SHOW, card_id, "no_show"))
    buttons.append(
        c.link(
            c.say(bw.LINK_PAGE, name=d["company"]), links.url("people", d["contact_id"], card_id)
        )
    )
    return c.card(
        card_type="M6",
        card_id=card_id,
        embeds=[
            c.embed(
                colour="meeting",
                title=c.say(bw.AFTER_HEAD, company=d["company"], who=d["who"]),
                description=c.closing(c.lines(bw.AFTER_ASK, bw.AFTER_OFFER)),
            )
        ],
        rows=[c.row(*buttons)],
        acts=acts,
        keys=[f"poc:{d['row_key']}:meeting_status"],
    )


def m7_reconnect(d: Mapping[str, Any], card_id: str) -> dict[str, Any]:
    """A reconnect that is due (docs/09 M7; weekend allowed).

    d: who, name, company, contact_id, last_touch, reason, done {action, targets, payload, key,
    role, row_key}: the same write as the dashboard's Done on this task (worker-built)."""
    done = d["done"]
    body = c.lines(
        c.say(bw.RECONNECT_LAST, touch=d["last_touch"]) if d.get("last_touch") else None,
        c.say(bw.RECONNECT_WHY, reason=d["reason"]) if d.get("reason") else None,
        bw.RECONNECT_ASK,
    )
    args = {k: done[k] for k in ("action", "targets", "payload", "key")}
    return c.card(
        card_type="M7",
        card_id=card_id,
        embeds=[
            c.embed(
                colour="grey",
                title=c.say(bw.RECONNECT_HEAD, who=d["who"], name=d["name"], company=d["company"]),
                description=c.closing(body),
            )
        ],
        rows=[
            c.row(
                c.button(bw.BTN_DONE, card_id, "done", "success"),
                c.button(bw.BTN_WEEK, card_id, "later"),
                _page(d, card_id),
            )
        ],
        acts={
            "done": {"door": "act", "args": args},
            "later": {"door": "later", "args": {"days": 7}},
        },
        keys=[f"poc:{done['row_key']}:{done['role']}"],
    )


IDEA_WORDS = {
    "li_status": (bw.IDEA_LI, bw.BTN_YES_CONNECTED),
    "meeting_status": (bw.IDEA_MEETING, bw.BTN_YES_HAPPENED),
}


def m10_idea(d: Mapping[str, Any], card_id: str) -> dict[str, Any]:
    """One of Saley's own ideas, one press (docs/09 M10, D24: shown once).

    d: who, proposal_id, type, name, company, contact_id, writes (the cell and value it sets)."""
    head, yes = IDEA_WORDS.get(str(d["type"]), (bw.IDEA_OTHER, bw.BTN_YES))
    pid = str(d["proposal_id"])
    return c.card(
        card_type="M10",
        card_id=card_id,
        embeds=[
            c.embed(
                colour="suggestion",
                author=bw.IDEA_AUTHOR,
                title=f"{c.clean(d['name'], 60)} · {c.clean(d['company'], 60)}",
                description=c.closing(c.say(head, who=d["who"], name=d["name"])),
                fields=[c.field(bw.FIELD_WRITES, d["writes"])] if d.get("writes") else [],
            )
        ],
        rows=[
            c.row(
                c.button(yes, card_id, "do", "success"),
                c.button(bw.BTN_NOT_YET, card_id, "skip"),
                _page(d, card_id),
            )
        ],
        acts={
            "do": {"door": "resolve", "args": {"choice": "do", "proposal_id": pid}},
            "skip": {"door": "resolve", "args": {"choice": "skip", "proposal_id": pid}},
        },
        keys=[f"proposal:{pid}"],
    )


def m11_new_company(d: Mapping[str, Any], card_id: str) -> dict[str, Any]:
    """A company new on the sheet this week (docs/09 M11). Take it = a fact, not a sheet write.

    d: account_id, company, fit, known, first_step, takers [(short, first name)]."""
    takers = [(str(s), str(n)) for s, n in d["takers"]]
    take = {
        "door": "fact",
        "args": {
            "kind": "owner_claim",
            "entity_type": "account",
            "entity_id": int(d["account_id"]),
            "values": [s for s, _ in takers],
        },
    }
    return c.card(
        card_type="M11",
        card_id=card_id,
        content=c.say(bw.NEWCO_HEAD, company=d["company"]),
        embeds=[
            c.embed(
                colour="news",
                author=bw.NEWCO_AUTHOR,
                title=d["company"],
                description=c.closing(""),
                fields=[
                    c.field(bw.FIELD_FIT, d["fit"], inline=False),
                    c.field(bw.FIELD_KNOWN, d.get("known") or bw.NOBODY_YET),
                    c.field(bw.FIELD_FIRST, d["first_step"]),
                ],
            )
        ],
        rows=[
            c.select(card_id, "take", bw.SELECT_TAKE, [(n, s) for s, n in takers]),
            c.row(
                c.button(bw.BTN_SKIP, card_id, "skip"), _page({**d, "contact_id": None}, card_id)
            ),
        ],
        acts={"take": take, "skip": {"door": "close", "args": {}}},
    )
