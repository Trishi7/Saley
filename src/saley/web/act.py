"""POST routes: act, undo, confirm, dismiss (R2, D24, D45, D48).

Why: an instruction from an approver IS the approval, so a click writes at once, with a
signature and an undo window. The web holds no sheet credential and `saley_ro` has no table
grant. Each route checks the column lock in plain code, then calls ONE SECURITY DEFINER SQL
function (`saley_act`, `saley_undo`, `saley_confirm`, `saley_dismiss`,
`saley_commitment_done`) on its own short
transaction (`act_db`). The function writes the decision and the outbox row; the worker
pushes to the sheet. This module never imports the sheet writer (tests/test_writer.py).

R3 adds two actions to `/api/act`: `row_add` (add a person to Outreach PoCs, D76) calls the same
`saley_act` function, and `find_people` (R11, D77) calls `saley_request_search`.

Every POST needs the header `X-Saley-Act: 1` and a same-origin request (the /api middleware
refuses cross-site calls before a route runs; `_cross_site` is checked here again).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import Path as PathParam
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from saley.core.clock import today_ist
from saley.core.db import ro_engine
from saley.core.logging import get_logger
from saley.core.settings import get_settings
from saley.intel.search import profile_url
from saley.sales.columns import POC_ROLES, Resolution, WritesRefused, assert_writable
from saley.sales.parse import norm_name
from saley.sales.vocab import NoDoneValue, done_cells
from saley.web import features, schemas
from saley.web import words as w
from saley.web.app import current_user
from saley.web.auth import LOOPBACK, SignedIn, client_host, provider
from saley.web.guards import _cross_site

log = get_logger("saley.web.act")
router = APIRouter()

SURFACE = "dashboard"
LOCK_TIMEOUT = "5s"
DATE_ROLES = frozenset({"first_contact_date", "li_connected_date", "meeting_date"})
FIRST_CONTACT_WORDS = frozenset({"TRUE", "FALSE", "VR", "SID"})
# D169: ASCII only, a dot in the domain, no list ("a@b.c;d@e.f"), no invisible character.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", re.ASCII)
EMAIL_MAX = 200  # D169: the cell limit.
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# H1: Google runs a text that starts with one of these as a formula (USER_ENTERED).
FORMULA = re.compile(r"^[=+@-]")
# The cells a human may give for a new row. Sr No and Company are set by Saley (docs/10).
NEW_ROW_FIELDS = frozenset(
    {
        "name",
        "designation",
        "about_poc",
        "industry",
        "based",
        "paper_links",
        "li_url",
        "email",
        "x_handle",
    }
)
# Lock wait over (55P03), deadlock (40P01) and serialization failure (40001): try again later.
BUSY_STATES = frozenset({"55P03", "40P01", "40001"})
AS_NAME = re.compile(r"[a-z]{1,20}")  # fullmatch only: "$" would accept a trailing newline
ROW_ID = Annotated[int, PathParam(ge=1, le=2**62)]

# The static column map: every role has a column, so only the role locks decide here. The
# worker re-resolves the real headers and checks again before it writes (columns.py).
_ALL_MAPPED = Resolution({r.name: i for i, r in enumerate(POC_ROLES)}, (), ())


def act_db() -> Iterator[Connection]:
    """A connection of the web role used only to call the saley_* functions."""
    with ro_engine().connect() as conn:
        yield conn


ActDb = Annotated[Connection, Depends(act_db)]
User = Annotated[SignedIn, Depends(current_user)]


def require_act_header(request: Request) -> None:
    """D24 CSRF layer: a form or a cross-site page cannot set this header."""
    if request.headers.get("x-saley-act") != "1":
        raise HTTPException(status_code=403, detail=w.ERR_ACT_HEADER)
    if _cross_site(request):
        raise HTTPException(status_code=403, detail=w.ERR_CROSS_SITE)


ActHeader = Annotated[None, Depends(require_act_header)]


def actor_short(request: Request, user: SignedIn) -> str:
    """Who acts: the signed-in user. X-Saley-As (D50) picks another person with mock only.

    Why: "View as" is a test aid. With Google (D116) the header is refused, so nobody can act
    as a colleague by sending a header.
    """
    as_user = request.headers.get("x-saley-as")
    if as_user is None:
        return user.short
    settings = get_settings()
    if (
        provider(settings) == "mock"
        and user.provider == "mock"
        and settings.is_dev
        and client_host(request) in LOOPBACK
        and AS_NAME.fullmatch(as_user)
    ):
        return as_user
    raise HTTPException(status_code=403, detail=w.ERR_AS_HEADER)


def call_function(conn: Connection, sql: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run one saley_* function in its own transaction. The commit is the write (D45).

    The web role starts read-only. `set transaction read write` is the first statement; the
    role still has no table grant, so only the definer function can write.
    """
    try:
        with conn.begin():
            conn.execute(text("set transaction read write"))
            conn.execute(text(f"set local lock_timeout = '{LOCK_TIMEOUT}'"))
            return conn.execute(text(sql), params).scalar_one()
    except DBAPIError as exc:
        state = getattr(exc.orig, "sqlstate", None)
        if state not in BUSY_STATES:
            raise
        log.warning("act_busy", sqlstate=state)
        raise HTTPException(status_code=409, detail=w.ERR_BUSY) from None


# ------------------------------------------------------------------ values


def _clean_value(role: str, raw: str, today: date) -> str:
    """The text that goes in the cell. Dates are ISO; 'today' is replaced here (clock)."""
    value = raw.strip()
    if CONTROL.search(value) or len(value) > 200 or FORMULA.match(value):
        raise HTTPException(status_code=422, detail=w.ERR_VALUE)
    if role in DATE_ROLES:
        if value.casefold() == "today":
            return today.isoformat()
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=w.ERR_VALUE) from exc
    if role == "first_contact":
        if value.upper() not in FIRST_CONTACT_WORDS:
            raise HTTPException(status_code=422, detail=w.ERR_VALUE)
        return value.upper() if value.upper() in ("TRUE", "FALSE") else value
    if role == "email" and not valid_email(value):
        raise HTTPException(status_code=422, detail=w.ERR_VALUE)
    if not value:
        raise HTTPException(status_code=422, detail=w.ERR_VALUE)
    return value


def valid_email(value: str) -> bool:
    """D154, D169: one ASCII address, at most 200 characters, no empty part between dots.

    Why: `a@.com`, `a@b..c`, a list with commas and a zero-width or bidi character pass a loose
    pattern but are not addresses. A bad value is refused here, so it never reaches the outbox.
    This only checks the shape; Saley never guesses an address.
    """
    if len(value) > EMAIL_MAX or not EMAIL_RE.fullmatch(value):
        return False
    local = value.rsplit("@", 1)[0]
    return not (local.startswith(".") or local.endswith(".") or ".." in local)


def _refuse_filled_email(body: schemas.ActIn, conn: Connection) -> None:
    """D154, docs/10: Email is written only when the cell is blank. One person per request.

    The sheet value is stale until the next sync, so a live earlier add (not undone, push not
    failed or cancelled, under a day old) counts as filled. The SQL guard repeats this behind
    the cell lock; this check gives the early answer.
    """
    if len(body.target_ids) != 1:
        raise HTTPException(status_code=422, detail=w.ERR_BAD_ACT)
    try:
        filled = conn.execute(
            text(
                "select btrim(coalesce(t.email, '')) <> '' or exists (select 1 from v_decision d "
                "where d.contact_id = t.contact_id and d.kind = 'column_write' "
                "and d.role = 'email' and d.undone_at is null "
                "and coalesce(d.sync_state, '') not in ('failed', 'cancelled') "
                "and d.at > now() - interval '1 day') "
                "from v_task_inputs t where t.contact_id = :i"
            ),
            {"i": body.target_ids[0]},
        ).scalar_one_or_none()
    finally:
        conn.rollback()  # the read began a transaction; call_function starts its own
    if filled:
        raise HTTPException(status_code=409, detail=w.ERR_EMAIL_FILLED)


def _raw_cells(body: schemas.ActIn) -> list[tuple[str, str]]:
    """The (role, value) pairs the request asks for. Empty for a task Done."""
    payload = body.payload
    if body.action == "task_done":
        if payload is None or payload.task is None:
            raise HTTPException(status_code=422, detail=w.ERR_BAD_ACT)
        return []
    if payload is None:
        raise HTTPException(status_code=422, detail=w.ERR_BAD_ACT)
    raw: list[tuple[str, str]] = [(c.role, c.value) for c in payload.cells or []]
    if payload.role is not None and payload.value is not None:
        raw.insert(0, (payload.role, payload.value))
    if not raw or (payload.cells and payload.role is not None):
        raise HTTPException(status_code=422, detail=w.ERR_BAD_ACT)
    return raw


def _done_value_cells(
    body: schemas.ActIn, who: str, conn: Connection, today: date
) -> list[dict[str, str]]:
    """The cells of a column-write Done. The client names the task; the SERVER picks the values.

    D153: a value comes from what the column already holds (`sales.vocab`). A request that also
    carries a role, a value, cells or a task is refused, so no client value can get through.
    With no fitting value the answer is 409 and nothing is written.
    """
    payload = body.payload
    if payload is None or payload.done is None or body.action != "write":
        raise HTTPException(status_code=422, detail=w.ERR_BAD_ACT)
    extras = (payload.role, payload.value, payload.cells, payload.fields, payload.task)
    if len(body.target_ids) != 1 or any(x is not None for x in extras):
        raise HTTPException(status_code=422, detail=w.ERR_BAD_ACT)
    try:
        cells = done_cells(conn, payload.done, today)
    except NoDoneValue:
        raise HTTPException(status_code=409, detail=w.ERR_NO_DONE_VALUE) from None
    finally:
        conn.rollback()  # the read began a transaction; call_function starts its own
    _refuse_locked(who, [c["role"] for c in cells], body.action)
    for cell in cells:  # the value is the sheet's own text, but it still goes to a cell
        value = cell["value"]
        if CONTROL.search(value) or len(value) > 200 or FORMULA.match(value):
            log.warning("done_value_unsafe", role=cell["role"])
            raise HTTPException(status_code=422, detail=w.ERR_VALUE)
    return cells


def _refuse_locked(user: str, roles: list[str], action: str) -> None:
    """Locks by ROLE (CLAUDE.md, docs/10). A refusal is a 403 and a `write_refused` log line."""
    email_write = action == "email_write" and get_settings().email_write_allowed
    for role in roles:
        if action == "email_write" and role != "email":
            _locked(user, role, w.ERR_LOCKED)
        if action == "email_write" and not email_write:
            _locked(user, role, w.ERR_EMAIL_OFF)
        try:
            assert_writable(_ALL_MAPPED, role, email_write=email_write)
        except WritesRefused:
            _locked(user, role, w.ERR_LOCKED)


def _locked(user: str, role: str, message: str) -> None:
    log.warning("write_refused", user=user, role=role, surface=SURFACE)
    raise HTTPException(status_code=403, detail=message)


def instruction_key(
    action: str, targets: list[int], cells: list[dict[str, str]], task: str, cycle: str = ""
) -> str:
    """D48: action, sorted targets, role and normalised value. No user, no surface.

    A task Done also carries its cycle (the due day of this round), so ticking the same task
    in the next round is a new instruction and not "already done".
    """
    parts = [action, ",".join(str(t) for t in sorted(set(targets)))]
    parts += [f"{c['role']}={c['value']}" for c in sorted(cells, key=lambda c: c["role"])]
    parts.append(task)
    if cycle:
        parts.append(f"cycle={cycle}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


# ------------------------------------------------------------------ errors from the function


def _raise_for(result: dict[str, Any]) -> None:
    """Map the function's refusal code to an HTTP error. Fixed text only."""
    code = result.get("code")
    table: dict[str, tuple[int, str]] = {
        "not_allowed": (403, w.ERR_NOT_APPROVER),
        "locked": (403, w.ERR_LOCKED),
        "no_target": (404, w.ERR_NO_PERSON),
        "not_found": (404, w.ERR_NO_WRITE),
        "already_undone": (409, w.ERR_UNDONE),
        "superseded": (409, w.ERR_SUPERSEDED),
        "no_before": (409, w.ERR_NO_BEFORE),
        "not_open": (409, w.ERR_CARD_CLOSED),
        "no_draft": (422, w.ERR_BAD_ACT),
        "bad_value": (422, w.ERR_VALUE),
        "duplicate": (409, w.ERR_DUPLICATE),
        "too_many": (409, w.ERR_TOO_MANY),
        "busy": (409, w.ERR_BUSY),
        "email_present": (409, w.ERR_EMAIL_FILLED),  # D154: the SQL guard behind the web check
        "bad_email": (422, w.ERR_VALUE),  # D169
    }
    if code in ("cooldown", "already"):  # D48 and D129: the first saver is named
        raise HTTPException(
            status_code=409,
            detail=w.fill(
                w.ERR_COOLDOWN, who=str(result.get("by") or "?")[:30], t=result.get("at")
            ),
        )
    if code == "duplicate" and result.get("by"):
        # D111: the row add of this person and company is live; say who added it.
        raise HTTPException(
            status_code=409, detail=w.fill(w.ERR_DUPLICATE_BY, who=str(result["by"])[:30])
        )
    if code == "not_owner":  # D155: the name of the owner, from users.name
        owner = str(result.get("owner_name") or "?")[:30]
        raise HTTPException(status_code=403, detail=w.fill(w.ERR_NOT_OWNER, name=owner))
    if code == "not_signer":  # D155: only the signer undoes a commitment Done
        raise HTTPException(status_code=403, detail=w.ERR_NOT_SIGNER)
    if code == "window_closed":
        raise HTTPException(status_code=409, detail=w.fill(w.ERR_WINDOW, t=result.get("at")))
    status, detail = table.get(str(code), (422, w.ERR_BAD_ACT))
    raise HTTPException(status_code=status, detail=detail)


# ------------------------------------------------------------------ routes

ACT_SQL = "select saley_act(:u, :s, :a, cast(:t as jsonb), cast(:p as jsonb), :k, :c, :m, :e)"


def _row_add_cells(body: schemas.ActIn, who: str) -> list[dict[str, str]]:
    """The cells of a new row. A role outside the new-row set is a 403, a bad value a 422."""
    payload = body.payload
    if len(body.target_ids) != 1 or payload is None or not payload.fields:
        raise HTTPException(status_code=422, detail=w.ERR_BAD_ACT)
    for cell in payload.fields:
        try:
            assert_writable(_ALL_MAPPED, cell.role, existing_row=False)
        except WritesRefused:
            _locked(who, cell.role, w.ERR_ROW_LOCKED)
        if cell.role not in NEW_ROW_FIELDS:
            _locked(who, cell.role, w.ERR_ROW_LOCKED)
    today = today_ist()
    cells = [
        {"role": c.role, "value": _clean_value(c.role, c.value, today)} for c in payload.fields
    ]
    for cell in cells:
        if cell["role"] == "li_url" and profile_url(cell["value"]) != cell["value"]:
            raise HTTPException(status_code=422, detail=w.ERR_LINK)
    if len({c["role"] for c in cells}) != len(cells):
        raise HTTPException(status_code=422, detail=w.ERR_BAD_ACT)
    return cells


def _name_of(cells: list[dict[str, str]]) -> str:
    return next((c["value"] for c in cells if c["role"] == "name"), "")


def _row_add(body: schemas.ActIn, who: str, conn: Connection) -> dict[str, Any]:
    settings = get_settings()
    cells = _row_add_cells(body, who)
    result = call_function(
        conn,
        ACT_SQL,
        {
            "u": who,
            "s": SURFACE,
            "a": "row_add",
            "t": json.dumps(sorted(set(body.target_ids))),
            # A9: the key of the sheet sync (casefold), so SQL matches the people in the sheet.
            "p": json.dumps({"fields": cells, "name_key": norm_name(_name_of(cells))}),
            "k": instruction_key("row_add", body.target_ids, cells, ""),
            "c": settings.act_cooldown_seconds,
            "m": settings.undo_minutes,
            "e": settings.email_write_allowed,
        },
    )
    if not result.get("ok"):
        if result.get("code") == "locked":
            log.warning("write_refused", user=who, role=result.get("role"), surface=SURFACE)
        _raise_for(result)
    items = [_item(i) for i in result["items"]]
    log.info("row_add", user=who, account=body.target_ids[0], already=items[0]["already"])
    return {
        "ok": True,
        "already": items[0]["already"],
        "write_id": items[0]["write_id"],
        "undo_until": items[0]["undo_until"],
        "label": items[0]["label"],
        "items": items,
    }


def _find_people(body: schemas.ActIn, who: str, conn: Connection) -> dict[str, Any]:
    """R11: a human click is the yes. One search request per company, run by the worker."""
    features.require_find_people()  # D164: off until FIND_PEOPLE_ENABLED
    if len(body.target_ids) != 1 or body.payload is not None:
        raise HTTPException(status_code=422, detail=w.ERR_BAD_ACT)
    result = call_function(
        conn,
        "select saley_request_search(:u, :id, :s)",
        {"u": who, "id": body.target_ids[0], "s": SURFACE},
    )
    if not result.get("ok"):
        _raise_for(result)
    already = bool(result["already"])
    return {
        "ok": True,
        "request_id": result["request_id"],
        "already": already,
        "label": w.FIND_ALREADY if already else w.FIND_QUEUED,
    }


@router.post("/api/act", response_model=schemas.ActOut | schemas.FindPeopleOut)
def act(
    body: schemas.ActIn, request: Request, user: User, _h: ActHeader, conn: ActDb
) -> dict[str, Any]:
    settings = get_settings()
    who = actor_short(request, user)
    if body.action == "find_people":
        return _find_people(body, who, conn)
    if body.action == "row_add":
        return _row_add(body, who, conn)
    today = today_ist()
    if body.payload and body.payload.done:
        cells = _done_value_cells(body, who, conn, today)
    else:
        raw = _raw_cells(body)
        _refuse_locked(who, [role for role, _ in raw], body.action)  # lock first, value second
        cells = [{"role": role, "value": _clean_value(role, value, today)} for role, value in raw]
    if body.action == "email_write":
        _refuse_filled_email(body, conn)
    task = body.payload.task if body.payload and body.payload.task else ""
    cycle = body.payload.cycle.isoformat() if body.payload and body.payload.cycle else ""
    payload: dict[str, Any] = {"cells": cells} if cells else {"task": task}
    if body.payload and body.payload.proposal_id:
        payload["proposal_id"] = body.payload.proposal_id
    result = call_function(
        conn,
        ACT_SQL,
        {
            "u": who,
            "s": SURFACE,
            "a": body.action,
            "t": json.dumps(sorted(set(body.target_ids))),
            "p": json.dumps(payload),
            "k": instruction_key(body.action, body.target_ids, cells, task, cycle),
            "c": settings.act_cooldown_seconds,
            "m": settings.undo_minutes,
            "e": settings.email_write_allowed,
        },
    )
    if not result.get("ok"):
        if result.get("code") == "locked":
            log.warning("write_refused", user=who, role=result.get("role"), surface=SURFACE)
        _raise_for(result)
    items = [_item(i) for i in result["items"]]
    return {
        "ok": True,
        "already": all(i["already"] for i in items),
        "write_id": items[0]["write_id"],
        "undo_until": items[0]["undo_until"],
        "label": items[0]["label"],
        "items": items,
    }


def _item(raw: dict[str, Any]) -> dict[str, Any]:
    role = str(raw["role"])
    what = w.ROLE_LABELS.get(role, w.TASK_LABEL if role.startswith("task:") else role)
    return {
        "write_id": raw["write_id"],
        "role": role,
        "value": raw.get("value") or "",
        "already": bool(raw["already"]),
        "undo_until": raw.get("undo_until"),
        "label": w.fill(w.SAVED, what=what),
    }


@router.post("/api/undo/{write_id}", response_model=schemas.UndoOut)
def undo(write_id: ROW_ID, request: Request, user: User, _h: ActHeader, conn: ActDb) -> dict:
    result = call_function(
        conn,
        "select saley_undo_group(:u, :id, :s)",
        {"u": actor_short(request, user), "id": write_id, "s": SURFACE},
    )
    if not result.get("ok"):
        _raise_for(result)
    return {"ok": True, "write_id": result["write_id"], "cancelled": bool(result["cancelled"])}


@router.post("/api/decision/{decision_id}/confirm", response_model=schemas.ConfirmOut)
def confirm(decision_id: ROW_ID, request: Request, user: User, _h: ActHeader, conn: ActDb) -> dict:
    result = call_function(
        conn,
        "select saley_confirm(:u, :id, :s)",
        {"u": actor_short(request, user), "id": decision_id, "s": SURFACE},
    )
    if not result.get("ok"):
        if result.get("code") == "not_found":
            raise HTTPException(status_code=404, detail=w.ERR_NO_DECISION)
        _raise_for(result)
    return {"ok": True, "decision_id": result["decision_id"], "already": bool(result["already"])}


@router.post("/api/proposal/{proposal_id}/dismiss", response_model=schemas.DismissOut)
def dismiss(proposal_id: ROW_ID, request: Request, user: User, _h: ActHeader, conn: ActDb) -> dict:
    result = call_function(
        conn,
        "select saley_dismiss(:u, :id, :s)",
        {"u": actor_short(request, user), "id": proposal_id, "s": SURFACE},
    )
    if not result.get("ok"):
        if result.get("code") == "not_found":
            raise HTTPException(status_code=404, detail=w.ERR_NO_CARD)
        _raise_for(result)
    return {"ok": True, "proposal_id": result["proposal_id"], "already": bool(result["already"])}


RESOLVE_SQL = "select saley_proposal_resolve(:u, :id, :c, :t, :r, :s, :cd, :um, :e)"


@router.post("/api/proposal/{proposal_id}/edit", response_model=schemas.ProposalEditOut)
def proposal_edit(
    proposal_id: ROW_ID,
    body: schemas.ProposalEditIn,
    request: Request,
    user: User,
    _h: ActHeader,
    conn: ActDb,
) -> dict:
    """Save an edited draft of an action card, signed, with an undo window (D129).

    The inline editor and the Discord modal use the same SQL function. It locks the card, so
    the first saver wins; the other one gets 409 "Already done by X at HH:MM" and nothing is
    written twice. The value is cleaned by the same rules as a cell edit.
    """
    who = actor_short(request, user)
    value = _clean_value("card_text", body.text, today_ist())
    settings = get_settings()
    result = call_function(
        conn,
        RESOLVE_SQL,
        {
            "u": who,
            "id": proposal_id,
            "c": "edit",
            "t": value,
            "r": None,
            "s": SURFACE,
            "cd": settings.act_cooldown_seconds,
            "um": settings.undo_minutes,
            "e": settings.email_write_allowed,
        },
    )
    if not result.get("ok"):
        if result.get("code") == "not_found":
            raise HTTPException(status_code=404, detail=w.ERR_NO_CARD)
        if result.get("code") == "locked":
            log.warning("write_refused", user=who, role=result.get("role"), surface=SURFACE)
        _raise_for(result)
    if not result.get("items"):  # a write that returns no cell is a fault, not a save
        log.error("card_edit_no_items", user=who, card=proposal_id)
        raise HTTPException(status_code=500, detail=w.ERR_SERVER)
    item = _item(result["items"][0])
    log.info("card_edit", user=who, card=proposal_id, already=item["already"])
    return {
        "ok": True,
        "proposal_id": proposal_id,
        "already": item["already"],
        "write_id": item["write_id"],
        "undo_until": item["undo_until"],
        "label": item["label"],
    }


@router.post("/api/commitment/{commitment_id}/done", response_model=schemas.CommitmentDoneOut)
def commitment_done(
    commitment_id: ROW_ID, request: Request, user: User, _h: ActHeader, conn: ActDb
) -> dict:
    """Mark a promise done, signed, with an undo window (M11).

    The function also skips the reminders still waiting; undo puts them back. Only the owner
    may mark it done (D155); anyone else gets 403 with the owner's name and nothing is written.
    """
    who = actor_short(request, user)
    result = call_function(
        conn,
        "select saley_commitment_done(:u, :id, :s, :m)",
        {"u": who, "id": commitment_id, "s": SURFACE, "m": get_settings().undo_minutes},
    )
    if not result.get("ok"):
        if result.get("code") == "not_found":
            raise HTTPException(status_code=404, detail=w.ERR_NO_COMMITMENT)
        if result.get("code") == "not_owner":
            log.warning("commitment_done_refused", user=who, commitment=commitment_id)
        _raise_for(result)
    log.info("commitment_done", user=who, commitment=commitment_id, already=result["already"])
    return {
        "ok": True,
        "commitment_id": result["commitment_id"],
        "already": bool(result["already"]),
        "write_id": result.get("write_id"),
        "undo_until": result.get("undo_until"),
    }
