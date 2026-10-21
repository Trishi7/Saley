"""GET routes of the team views: Inbox, Commitments, Decisions (R2, docs/08 views 9, 10, 11).

Why: these three lists read the decision, proposal, ask, reminder and commitment tables through
views, with the read-only role. Every list has limit and offset, a closed set of filter values
(anything else is a 422), and a response model that lists its fields. Actions are the POST
routes in `act.py`; this module never writes.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from sqlalchemy import text

from saley.core.clock import day_label
from saley.intel.decisions import list_decisions
from saley.web import mine, queries, schemas
from saley.web import words as w
from saley.web.app import Db, Limit, Offset, User, meta
from saley.web.queries import DEFAULT_LIMIT

router = APIRouter()

ShortName = Annotated[str | None, Query(max_length=20, pattern=r"^[a-z]+$")]
INBOX_FILTER = {"card": "proposal", "confirm": "confirm", "ask": "ask", "reminder": "reminder"}
SOURCE_LABELS = mine.SOURCE_LABELS


REASON_WORDS = (
    ("cell changed", w.SYNC_REASON_CELL),
    ("formula", w.SYNC_REASON_FORMULA),
    ("not found in the sheet", w.SYNC_REASON_ROW),
    ("name column was not found", w.SYNC_REASON_ROW),
    ("not in the database", w.SYNC_REASON_ROW),
    ("read-only", w.SYNC_REASON_LOCKED),
    ("restricted roles", w.SYNC_REASON_LOCKED),
    ("has no column", w.SYNC_REASON_LOCKED),
    ("signature note", w.SYNC_REASON_NOTE),
)


def sync_reason(error: str | None, state: str | None) -> str | None:
    """The fixed reason for a failed write (H5). The stored error text is never sent as it is."""
    if state != "failed":
        return None
    low = (error or "").casefold()
    for needle, words in REASON_WORDS:
        if needle in low:
            return words
    return w.SYNC_REASON_OTHER


@router.get("/api/inbox", response_model=schemas.InboxOut)
def inbox(
    user: User,
    conn: Db,
    type: Literal["card", "confirm", "ask", "reminder"] | None = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> dict[str, Any]:
    kind = INBOX_FILTER.get(type) if type else None
    where = "(cast(:kind as text) is null or item_kind = :kind)"
    total = conn.execute(
        text(f"select count(*) from v_inbox where {where}"), {"kind": kind}
    ).scalar_one()
    found = queries.rows(
        conn,
        f"select item_kind, id, type, ref, payload, at, for_user from v_inbox where {where} "
        "order by (item_kind = 'proposal') desc, rank, at nulls last, id "
        "limit :limit offset :offset",
        kind=kind,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [item for r in found if (item := mine.inbox_item(r)) is not None],
        "total": int(total),
        "empty": w.INBOX_EMPTY,
        **meta(conn),
    }


@router.get("/api/commitments", response_model=schemas.CommitmentsOut)
def commitments(
    user: User,
    conn: Db,
    owner: ShortName = None,
    state: Literal["open", "done", "dropped", "all"] = "open",
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> dict[str, Any]:
    params = {"owner": owner, "state": None if state == "all" else state}
    where = (
        "(cast(:owner as text) is null or owner_short = :owner) "
        "and (cast(:state as text) is null or state = :state)"
    )
    total = conn.execute(
        text(f"select count(*) from v_commitment where {where}"), params
    ).scalar_one()
    found = queries.rows(
        conn,
        "select id, owner_short, owner_name, text, due, source_type, state, reminders_sent, "
        f"last_reminded_on, in_pack, source_removed from v_commitment where {where} "
        "order by due nulls last, id limit :limit offset :offset",
        **params,
        limit=limit,
        offset=offset,
    )
    for row in found:
        row["source"] = SOURCE_LABELS.get(row["source_type"], w.SRC_OTHER)
        if row["source_removed"]:
            row["source"] = w.fill(w.SRC_REMOVED, source=row["source"])
        row["due_label"] = day_label(row["due"]) if row["due"] else None
    return {"items": found, "total": int(total), **meta(conn)}


@router.get("/api/decisions", response_model=schemas.DecisionsOut)
def decisions(
    user: User,
    conn: Db,
    person: ShortName = None,
    surface: Literal["dashboard", "chat", "card"] | None = None,
    undo: Literal["open", "undone", "undo_failed", "closed"] | None = None,
    state: Literal["proposed", "confirmed", "active"] | None = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> dict[str, Any]:
    found, total = list_decisions(
        conn, person=person, surface=surface, undo=undo, state=state, limit=limit, offset=offset
    )
    for row in found:
        row["label"] = row.pop("choice")
        row["text"] = row.pop("reason")
        row["what"] = w.ROLE_LABELS.get(row["role"] or "", None) or (
            w.TASK_LABEL if (row["role"] or "").startswith("task:") else None
        )
        if row["kind"] == "commitment_done":
            row["what"] = w.COMMITMENT_LABEL
        row["value"] = row.pop("after")
        row["sync_reason"] = sync_reason(row.pop("sync_error"), row["sync_state"])
        row.pop("sync_op")
        # L6: the same kinds the undo SQL accepts; `undo_state` is "open" inside UNDO_MINUTES.
        is_write = row["kind"] in ("column_write", "task_done", "commitment_done", "row_add")
        row["can_confirm"] = row["kind"] == "strategy_decision" and row["state"] == "proposed"
        row["can_undo"] = is_write and row["undo_state"] == "open"
    return {"items": found, "total": total, **meta(conn)}
