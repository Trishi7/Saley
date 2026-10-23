"""Cards for the team's own day: M1 brief, M8 commitment, M9 reminder, M12 pack, M14 ask, M15
health.

Why one module: none of them is about a sheet row; they carry the person's own items, the
week's numbers or the system's health. M1 and M12 do not count against the caps (docs/09 §4).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from saley.bot import components as c
from saley.bot import links
from saley.bot import words as bw

BRIEF_LINES = 3  # docs/09 M1: at most 3 lines; the rest is a count
PACK_TOP = 3
RAG = {"green": "🟢", "amber": "🟡", "red": "🔴"}


def m1_brief(d: Mapping[str, Any], card_id: str) -> dict[str, Any]:
    """The daily brief DM (docs/09 M1, D128). d: name, day, lines [str], more."""
    shown = [f"• {c.clean(x, 200)}" for x in d["lines"][:BRIEF_LINES]]
    more = int(d.get("more") or 0) + max(len(d["lines"]) - BRIEF_LINES, 0)
    head = bw.BRIEF_HI_ONE if len(shown) == 1 else bw.BRIEF_HI
    return c.card(
        card_type="M1",
        card_id=card_id,
        content=c.say(head, name=d["name"], n=len(shown)),
        embeds=[
            c.embed(
                colour="grey",
                author=c.say(bw.BRIEF_AUTHOR, day=d["day"]),
                description=c.closing(
                    c.lines(*shown, c.say(bw.BRIEF_MORE_PAGE, n=more) if more else None)
                ),
            )
        ],
        rows=[c.row(c.link(bw.LINK_TODAY, links.url("today", card=card_id)))],
    )


def m8_commitment(d: Mapping[str, Any], card_id: str) -> dict[str, Any]:
    """A commitment nudge, the day before and on the due day (docs/09 M8, C05).

    d: who, commitment_id, text, day (None when there is no due day)."""
    cid = int(d["commitment_id"])
    text = (
        c.say(bw.NUDGE, text=d["text"], day=d["day"])
        if d.get("day")
        else c.say(bw.NUDGE_NO_DAY, text=d["text"])
    )
    move = {
        "door": "commitment_due",
        "args": {"commitment_id": cid},
        "modal": {
            "title": bw.MODAL_DUE,
            "fields": [
                {
                    "id": "date",
                    "label": bw.MODAL_DATE_LABEL,
                    "placeholder": bw.MODAL_DATE_HINT,
                    "style": "short",
                    "max": 20,
                }
            ],
        },
    }
    return c.card(
        card_type="M8",
        card_id=card_id,
        embeds=[c.embed(colour="grey", description=c.closing(text))],
        rows=[
            c.row(
                c.button(bw.BTN_DONE, card_id, "done", "success"),
                c.button(bw.BTN_MOVE_DATE, card_id, "move"),
                c.link(bw.LINK_COMMITMENTS, links.url("commitments", card=card_id)),
            )
        ],
        acts={
            "done": {"door": "commitment_done", "args": {"commitment_id": cid}},
            "move": move,
        },
        keys=[f"commitment:{cid}"],
    )


def m9_reminder(d: Mapping[str, Any], card_id: str) -> dict[str, Any]:
    """A reminder the person set (docs/09 M9). d: who, reminder_id, text."""
    return c.card(
        card_type="M9",
        card_id=card_id,
        content=c.say(bw.REMIND_HEAD, who=d["who"]),
        embeds=[
            c.embed(
                colour="grey",
                description=bw.CLOSING,
                fields=[c.field(bw.FIELD_REMINDER, d["text"], inline=False)],
            )
        ],
        rows=[
            c.row(
                c.button(bw.BTN_DONE, card_id, "done", "success"),
                c.button(bw.BTN_SNOOZE, card_id, "later"),
            )
        ],
        acts={
            "done": {"door": "close", "args": {}},
            "later": {"door": "later", "args": {"days": 1}},
        },
    )


def m12_pack(d: Mapping[str, Any], card_id: str) -> dict[str, Any]:
    """The weekly pack, Components v2, pinned (docs/09 M12, D130).

    d: day, week (the pack page id, e.g. 2026-W43), lines [{rag, label, value, last}], funnel,
    top [str], idea."""

    def score(x: Mapping[str, Any]) -> str:
        line = c.say(
            bw.PACK_LINE, rag=RAG.get(str(x["rag"]), "⚪"), label=x["label"], value=x["value"]
        )
        return f"{line} {c.say(bw.PACK_LAST, n=x['last'])}" if x.get("last") is not None else line

    parts = [
        c.v2_text(bw.PACK_HEAD),
        c.v2_text(f"**{c.say(bw.PACK_TITLE, day=d['day'])}**"),
        c.v2_separator(),
        c.v2_text(c.lines(*(score(x) for x in d["lines"]))),
    ]
    if d.get("funnel"):
        parts.append(c.v2_text(c.say(bw.PACK_FUNNEL, delta=d["funnel"])))
    top = [f"• {c.clean(t, 200)}" for t in d.get("top") or []][:PACK_TOP]
    if top:
        parts += [c.v2_separator(), c.v2_text(c.lines(f"**{bw.PACK_TOP}**", *top))]
    if d.get("idea"):
        parts.append(c.v2_text(c.say(bw.PACK_IDEA, idea=d["idea"])))
    parts.append(c.v2_text(bw.CLOSING))
    return c.card(
        card_type="M12",
        card_id=card_id,
        v2=c.v2_container(parts, "grey"),
        rows=[c.row(c.link(bw.LINK_PACK, links.url("pack", d["week"], card_id)))],
        pin=True,
    )


def m14_ask(d: Mapping[str, Any], card_id: str) -> dict[str, Any]:
    """The worker needs a fact from a person (docs/09 M14). The answer is a teammate fact.

    d: who, ask_id, question, entity_type, entity_id."""
    args = {
        "kind": "answer",
        "ask_id": int(d["ask_id"]),
        "entity_type": d["entity_type"],
        "entity_id": int(d["entity_id"]),
    }
    answer = {
        "door": "fact",
        "args": args,
        "modal": {
            "title": bw.MODAL_ANSWER,
            "fields": [{"id": "text", "label": bw.BTN_ANSWER, "style": "paragraph", "max": 300}],
        },
    }
    return c.card(
        card_type="M14",
        card_id=card_id,
        content=c.closing(c.say(bw.ASK_LINE, who=d["who"], question=d["question"])),
        rows=[c.row(c.button(bw.BTN_ANSWER, card_id, "answer", "primary"))],
        acts={"answer": answer},
    )


def m15_health(d: Mapping[str, Any], card_id: str) -> dict[str, Any]:
    """The nightly health post to the test channel (docs/09 M15, D133). No closing line.

    d: day, jobs_ok, jobs_failed, llm, cap, egress, sent, held, failed, presses {ok, already,
    refused}, listen (the last card sync, or "—"), dms_off."""
    good = not d["jobs_failed"] and not d["failed"]
    presses = d["presses"]
    fields = [
        c.field(
            bw.FIELD_JOBS, c.say(bw.FIELD_JOBS_VALUE, ok=d["jobs_ok"], failed=d["jobs_failed"])
        ),
        c.field(bw.FIELD_LLM, c.say(bw.FIELD_LLM_VALUE, n=d["llm"], cap=d["cap"])),
        c.field(bw.FIELD_EGRESS, str(d["egress"])),
        c.field(
            bw.FIELD_CARDS,
            c.say(bw.FIELD_CARDS_VALUE, sent=d["sent"], held=d["held"], failed=d["failed"]),
        ),
        c.field(
            bw.FIELD_PRESSES,
            c.say(
                bw.FIELD_PRESSES_VALUE,
                ok=presses["ok"],
                already=presses["already"],
                refused=presses["refused"],
            ),
        ),
        c.field(bw.FIELD_LISTEN, str(d["listen"])),
    ]
    if d.get("dms_off"):
        fields.append(c.field(bw.FIELD_DMS, bw.DMS_OFF))
    return c.card(
        card_type="M15",
        card_id=card_id,
        embeds=[
            c.embed(
                colour="ok" if good else "cannot",
                author=c.say(bw.HEALTH_AUTHOR, day=d["day"]),
                fields=fields,
            )
        ],
        rows=[c.row(c.link(bw.LINK_HEALTH, links.url("sources", card=card_id)))],
    )
