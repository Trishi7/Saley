"""Display side of "my tasks", the Inbox and "working on": rows in, API dicts out (R2).

Why: the rules live in `saley.intel` and know no words. This module reads the rows the web
role may see (views only), calls the rules, and adds the text from `words.py`. Names and
companies come from the sheet and are shown as data. Nothing here writes.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from sqlalchemy import Connection

from saley.core.clock import day_label, hhmm
from saley.core.logging import get_logger
from saley.intel import tasks as rules
from saley.web import queries
from saley.web import words as w

log = get_logger("saley.web.mine")

TASKS_PAGE = 10  # fixed page size of My tasks (R5 T3); the client cannot change it
WHY_MAX = 300  # a decision text can be long; the Decisions view shows all of it

SOURCE_LABELS = {"meeting_note": w.SRC_NOTE, "mail": w.SRC_MAIL}
WORKING_SOURCES = {"about_poc": w.SRC_ABOUT, "paper_link": w.SRC_PAPER}
PERSON_KINDS = frozenset({"sid", "vaishnavi"})  # the people whose lists need sheet rows


def _role_label(role: str) -> str:
    if role.startswith("task:"):
        return w.TASK_LABEL
    return w.ROLE_LABELS.get(role, role)


def _title_why(t: rules.Task) -> tuple[str, str | None]:
    """The title and the reason line of a task, from the word templates."""
    name = t.name or "—"
    p = t.params
    day = day_label(p["day"]) if p.get("day") else None
    table: dict[str, Callable[[], tuple[str, str | None]]] = {
        "li_confirm": lambda: (w.fill(w.TASK_LI_CONFIRM, name=name), w.WHY_LI_CONFIRM),
        "li_first": lambda: (w.fill(w.TASK_LI_FIRST, name=name), w.WHY_LI_FIRST),
        "reconnect": lambda: (
            w.fill(w.TASK_RECONNECT, name=name),
            w.fill(w.WHY_RECONNECT, day=day) if day else None,
        ),
        "meeting": lambda: (w.fill(w.TASK_MEETING, name=name), w.WHY_MEETING),
        "status_check": lambda: (
            w.fill(w.TASK_STATUS, name=name),
            w.fill(w.WHY_STATUS, day=day, status=p.get("status") or "—"),
        ),
        "add_email": lambda: (
            w.fill(w.TASK_ADD_EMAIL, name=name),
            w.fill(w.WHY_ADD_EMAIL, n=p.get("n")),
        ),
        "email1": lambda: (w.fill(w.TASK_EMAIL1, name=name), w.fill(w.WHY_EMAIL_STEP, day=day)),
        "email2": lambda: (w.fill(w.TASK_EMAIL2, name=name), w.fill(w.WHY_EMAIL_STEP, day=day)),
        "email3": lambda: (w.fill(w.TASK_EMAIL3, name=name), w.fill(w.WHY_EMAIL_STEP, day=day)),
        "decision": lambda: (
            w.fill(w.TASK_DECISION, id=p.get("id")),
            w.fill(w.WHY_DECISION_BY, who=p["who"], day=day) if p.get("who") and day else None,
        ),
        "inbox_cards": lambda: (w.fill(w.TASK_INBOX_CARDS, n=p.get("n")), None),
        "review_write": lambda: (
            w.fill(
                w.TASK_REVIEW_WRITE,
                who=p.get("who"),
                what=_role_label(str(p.get("role"))),
                name=name,
            ),
            w.fill(w.WHY_REVIEW, t=hhmm(p.get("until"))),
        ),
        "commitment": lambda: (
            w.fill(w.TASK_COMMITMENT, text=p.get("text")),
            w.fill(w.WHY_COMMITMENT, source=SOURCE_LABELS.get(p.get("source_type"), w.SRC_OTHER)),
        ),
    }
    return table[t.kind]()


def task_dict(t: rules.Task) -> dict[str, Any]:
    title, why = _title_why(t)
    link = {"view": "asks"} if t.kind == "inbox_cards" else None  # the Inbox page id
    if t.ticked:  # a finished task: the reason line is gone, the row offers Undo
        why = None
    return {
        "key": t.key,
        "kind": t.kind,
        "person": t.person,
        "title": title,
        "why": why,
        "contact_id": t.contact_id,
        "name": t.name,
        "company": t.company,
        "due": t.due,
        "due_label": day_label(t.due) if t.due else w.TASK_NO_DATE,  # D180
        "action": {"endpoint": t.action.endpoint, "body": t.action.body} if t.action else None,
        "undo_id": t.undo_id,
        "undo_ids": list(t.undo_ids),
        "undo_until": t.undo_until,
        "done": t.ticked,
        "input_role": t.input_role,
        "soon": t.soon,
        "link": link,
    }


def _window_writes(conn: Connection, person: str | None, viewer: str) -> list[dict[str, Any]]:
    """Kushal's review needs everyone's writes; a ticked row needs the viewer's own."""
    if not person:
        return []
    found: dict[int, dict[str, Any]] = {}
    if person == "kushal":
        found.update({r["id"]: r for r in queries.writes_in_undo_window(conn)})
    if viewer == person:
        found.update({r["id"]: r for r in queries.writes_in_undo_window(conn, person)})
    return sorted(found.values(), key=lambda r: r["id"], reverse=True)


def load_inputs(
    conn: Connection, person: str | None, today: date, viewer: str | None = None
) -> rules.Inputs:
    """Read only what this person's rules need. `viewer` None means the person's own list."""
    viewer = viewer or person or ""
    contacts = (
        [rules.ContactRow.from_row(r) for r in queries.task_inputs(conn)]
        if person in PERSON_KINDS
        else []
    )
    kushal = person == "kushal"
    return rules.Inputs(
        contacts=contacts,
        proposed=[
            rules.ProposedDecision(r["id"], r["choice"], r["user_name"], r["at"].date())
            for r in (queries.proposed_decisions(conn) if kushal else [])
        ],
        open_cards=queries.open_card_count(conn) if kushal else 0,
        recent_writes=[
            rules.RecentWrite(
                r["id"],
                r["user_short"] or "",
                r["user_name"] or "",
                r["role"] or "",
                r["contact_id"],
                r["person"],
                r["undo_until"],
            )
            # Their own ticks stay on the page with an Undo, for the signer only (R5 T3).
            for r in _window_writes(conn, person, viewer)
        ],
        commitments=[
            rules.CommitmentRow(
                r["id"], r["owner_short"], r["text"], r["due"], r["source_type"], r["state"]
            )
            for r in queries.commitments_due(conn, today)
        ],
    )


def my_tasks(
    conn: Connection,
    person: str | None,
    today: date,
    *,
    email_write: bool,
    offset: int = 0,
    viewer: str | None = None,
) -> dict[str, Any]:
    """The Today block for one person: one page of TASKS_PAGE tasks.

    `total` is the full count. `more` is what is left after this page, never below 0.
    """
    found = (
        rules.tasks_for(
            person,
            load_inputs(conn, person, today, viewer),
            today,
            email_write=email_write,
            viewer=viewer,
        )
        if person
        else []
    )
    page = found[offset : offset + TASKS_PAGE]
    more = max(len(found) - len(page) - offset, 0)
    return {
        "person": person,
        "items": [task_dict(t) for t in page],
        "total": len(found),
        "offset": offset,
        "more": more,
        "more_label": w.fill(w.TASKS_MORE, n=more) if more else None,
        "empty": w.MY_TASKS_EMPTY,
    }


# ------------------------------------------------------------------ working on


def working_on_block(raw: dict[str, Any] | None, href: Callable[[str | None], str | None]) -> dict:
    """text + source + date, or the words 'None on file' (never a guess)."""
    if not raw or not (raw.get("text") or raw.get("url")):
        return {"found": False, "text": w.NONE_ON_FILE, "source": None, "date": None, "href": None}
    seen = raw.get("observed_at")
    if isinstance(seen, str):
        seen = datetime.fromisoformat(seen)
    return {
        "found": True,
        "text": raw.get("text") or w.WORKING_PAPER,
        "source": WORKING_SOURCES.get(str(raw.get("source_type")), str(raw.get("source_type"))),
        "date": seen.date() if isinstance(seen, datetime) else None,
        "href": href(raw.get("url")),
    }


# ------------------------------------------------------------------ inbox

CARD_TEXT = {
    "meeting_status": (w.CARD_MARK_COMPLETED, w.CARD_MARK_COMPLETED_WHY, w.BTN_MARK_COMPLETED),
    "li_status": (w.CARD_LI_CONNECTED, w.CARD_LI_CONNECTED_WHY, w.BTN_MARK_CONNECTED),
}
INBOX_KINDS = {"proposal": "card", "confirm": "confirm", "ask": "ask", "reminder": "reminder"}


def _card(row: dict[str, Any]) -> dict[str, Any]:
    p = row["payload"]
    title_t, why_t, button = CARD_TEXT.get(row["type"], (row["type"], None, None))
    day = date.fromisoformat(p["day"]) if p.get("day") else None
    body = {
        "action": "write",
        "target_ids": [p["contact_id"]],
        "payload": {"cells": p["cells"], "proposal_id": row["id"]},
    }
    return {
        "title": w.fill(title_t, name=p.get("name") or "—") if "{name}" in title_t else title_t,
        "why": w.fill(why_t, day=day_label(day), status=p.get("status") or "—")
        if why_t and day
        else None,
        "person": p.get("name"),
        "button": button,
        "action": {"endpoint": "/api/act", "body": body},
        "dismiss": f"/api/proposal/{row['id']}/dismiss",
        "evidence": {"row": p.get("row")},
    }


def inbox_item(row: dict[str, Any]) -> dict[str, Any] | None:
    """One v_inbox row as the Inbox shows it. None for a card with a damaged payload (log)."""
    kind = INBOX_KINDS[row["item_kind"]]
    base: dict[str, Any] = {
        "kind": kind,
        "id": row["id"],
        "type": row["type"],
        "at": row["at"],
        "for_user": row["for_user"],
        "person": None,
        "why": None,
        "button": None,
        "action": None,
        "dismiss": None,
        "evidence": {"row": None},
    }
    if kind == "card":
        try:
            return {**base, **_card(row)}
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            # Silent failure 10: one bad proposal must not turn the whole Inbox into a 500.
            log.warning("inbox_card_skipped", id=row.get("id"), error=type(exc).__name__)
            return None
    if kind == "confirm":
        reason = (row["payload"].get("reason") or "")[:WHY_MAX]
        return {
            **base,
            "title": w.fill(w.INBOX_CONFIRM, id=row["ref"] or f"#{row['id']}"),
            "why": reason or None,
            "action": {"endpoint": f"/api/decision/{row['id']}/confirm", "body": None},
        }
    return {**base, "title": row["ref"]}
