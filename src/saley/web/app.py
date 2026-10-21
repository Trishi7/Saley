"""FastAPI dashboard: JSON under /api, static page at /.

Why: docs/08 is the contract. Every GET is read-only. The only POST routes live in act.py and
call fixed SQL functions (R2, D24); the web role has no table grant. Routes are plain `def`
(FastAPI runs them in a thread pool) on the read-only engine. Each request reads ONE sheet
snapshot in one REPEATABLE READ READ ONLY transaction and returns its `sync_id` (D40).
Every response model lists only the fields its view shows (R1.1 C). Since R4 (D114-D116) every
/api route needs a session from `web/auth.py` in every mode; there is no loopback bypass.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Connection, text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from saley.core.clock import day_label, hhmm, now_ist, today_ist
from saley.core.db import ro_engine
from saley.core.logging import get_logger
from saley.core.settings import get_settings
from saley.intel import mail, metrics
from saley.sales.parse import is_band
from saley.web import (
    accounts_model,
    auth,
    features,
    goal_model,
    mine,
    newsfresh,
    notes_health,
    people_model,
    queries,
    schemas,
)
from saley.web import words as w
from saley.web.auth import SignedIn, session_user
from saley.web.guards import (
    BodyLimit,
    _bad_host,
    _cross_site,
    _security_headers,
    csp_for,
    reject_unknown_params,
)
from saley.web.queries import DEFAULT_LIMIT, MAX_LIMIT, BadFilter

STATIC = Path(__file__).parent / "static"
STALE_FACTOR = 3  # "Updated" turns red after 3 × SHEET_SYNC_SECONDS without a sync (D43)
ACTIVE_SHOWN = 8
SHORT_LIMIT = 40
log = get_logger("saley.web")
EMAIL_RE = re.compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+$")


class SnapshotChanged(Exception):
    """The client asked for a sync_id that is no longer current (D40)."""

    def __init__(self, current: int | None) -> None:
        self.current = current


def current_user(request: Request) -> SignedIn:
    """The user of a valid session, else 401 (D114, D116).

    Why: no mode skips sign-in. A server that forgets SALEY_ENV still refuses strangers, and a
    laptop signs in through the mock provider like everybody else.
    """
    user = session_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail=w.ERR_SIGN_IN)
    return user


def require_admin(user: Annotated[SignedIn, Depends(current_user)]) -> SignedIn:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail=w.ERR_ADMIN)
    return user


def validate_as_header(request: Request) -> None:
    """L1: one door for X-Saley-As on EVERY /api route, GET and POST alike (D50).

    Why: a name in the header must be a person in the team. Checked here once, so a new route
    can never forget it. No session gets 401. A bad shape or a provider other than mock gets
    ERR_AS_HEADER (D116), an unknown person gets ERR_AS_UNKNOWN (403 both). No header: the
    signed-in user.
    """
    if not request.url.path.startswith("/api") or request.headers.get("x-saley-as") is None:
        return
    from saley.web.act import actor_short  # late import: act needs this module's helpers

    who = actor_short(request, current_user(request))
    with ro_engine().connect() as conn:
        found = conn.execute(text("select 1 from v_team where short = :s"), {"s": who}).first()
    if found is None:
        log.warning("as_unknown", name=who)
        raise HTTPException(status_code=403, detail=w.ERR_AS_UNKNOWN)


def db(sync_id: Annotated[int | None, Query(ge=1, le=2**62)] = None) -> Iterator[Connection]:
    """One read-only snapshot per request: every query sees the same data (D40)."""
    with ro_engine().connect() as raw:
        conn = raw.execution_options(isolation_level="REPEATABLE READ")
        with conn.begin():
            conn.execute(text("set transaction read only"))
            current = queries.current_sync_id(conn)
            if sync_id is not None and sync_id != current:
                raise SnapshotChanged(current)
            conn.info["sync_id"] = current
            yield conn


User = Annotated[SignedIn, Depends(current_user)]
Db = Annotated[Connection, Depends(db)]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
Offset = Annotated[int, Query(ge=0, le=100_000)]
Q = Annotated[str | None, Query(max_length=queries.MAX_Q)]
Filter = Annotated[str, Query(max_length=120)]
RowId = Annotated[int, PathParam(ge=1, le=2**31 - 1)]


def threshold() -> timedelta:
    return timedelta(seconds=STALE_FACTOR * get_settings().sheet_sync_seconds)


def meta(conn: Connection) -> dict[str, Any]:
    """'last computed' for every response: the last good sheet sync, and the snapshot read."""
    sheet = queries.last_job(conn, "sheet_sync")
    return {
        "sync_id": conn.info.get("sync_id"),
        "last_computed": sheet.isoformat() if sheet else None,
        "last_computed_label": hhmm(sheet),
        "stale": sheet is None or now_ist() - sheet > threshold(),
    }


def sync_state(conn: Connection) -> tuple[str, str]:
    """ok / partial / worker_down / sync_failing / never, and the top bar message (D43).

    `partial`: the sync runs but a tab is stale (D41). Its data is the last good copy, so the
    bar must not say plain "Updated": every user sees it, not only admins on Sources.
    """
    started = queries.last_started(conn, "sheet_sync")
    good = queries.last_job(conn, "sheet_sync")
    now = now_ist()
    if started is None:
        return "never", w.NEVER_SYNCED
    if now - started > threshold():
        return "worker_down", w.fill(w.WORKER_DOWN, t=hhmm(started))
    if good is None or now - good > threshold():
        return "sync_failing", w.fill(w.SYNC_FAILING, t=hhmm(good or started))
    stale, _ = queries.tab_states(conn)
    if len(stale) == 1:
        return "partial", w.fill(w.TAB_STALE, t=stale[0])
    if stale:
        return "partial", w.fill(w.TABS_STALE, n=len(stale))
    return "ok", w.fill(w.UPDATED, t=hhmm(good), s=hhmm(good), n=newsfresh.top_bar(conn)[0])


def write_counts(conn: Connection) -> dict[str, int]:
    """Outbox rows the user should know about: waiting, held (writes off) and failed (H5)."""
    row = (
        conn.execute(
            text(
                "select count(*) filter (where state = 'pending') as pending, "
                "count(*) filter (where state = 'held') as held, "
                "count(*) filter (where state = 'failed') as failed from v_pending_write"
            )
        )
        .mappings()
        .one()
    )
    return {"pending": int(row["pending"]), "held": int(row["held"]), "failed": int(row["failed"])}


def write_note(counts: dict[str, int]) -> tuple[str | None, bool]:
    """The top bar line for sheet writes, and whether it is bad news.

    A failed write is never hidden: the line stays while any row is failed (H5). Held rows say
    that writes are off, not that the sheet "updates in a few seconds".
    """
    if counts["failed"]:
        return w.fill(w.WRITE_FAILED, n=counts["failed"]), True
    if counts["held"] and not get_settings().sheet_write_enabled:
        return w.WRITES_OFF, False
    if counts["pending"] or counts["held"]:
        return w.PENDING_NOTE, False
    return None, False


def writes_state(conn: Connection) -> dict[str, Any]:
    """Sources and health: are sheet writes on, and how many rows wait or failed (R2, Q-R2-1)."""
    counts = (
        conn.execute(
            text(
                "select count(*) filter (where open) as open, "
                "count(*) filter (where state = 'failed') as failed from v_pending_write"
            )
        )
        .mappings()
        .one()
    )
    enabled = get_settings().sheet_write_enabled
    return {
        "enabled": enabled,
        "message": None if enabled else w.WRITES_OFF,
        "open": counts["open"],
        "failed": counts["failed"],
    }


def working_on(raw: dict[str, Any] | None) -> dict[str, Any]:
    """The drawer line: text, source and date, else 'None on file' (R2, docs/03 §3.2)."""
    return mine.working_on_block(raw, https_link)


def mail_state(conn: Connection) -> dict[str, Any]:
    """Sources and health: the cc mailbox is not configured, waiting, ok or failing (Q-R2-3)."""
    settings = get_settings()
    last = (
        conn.execute(
            text(
                "select status, finished_at from v_job_runs where job = 'mail_poll' "
                "order by started_at desc limit 1"
            )
        )
        .mappings()
        .first()
    )
    last_ok = queries.last_job(conn, "mail_poll")
    state = mail.state(settings, last_ok, bool(last and last["status"] == "failed"))
    messages = {
        "not_configured": w.MAIL_NOT_CONFIGURED,
        "failing": w.MAIL_FAILING,
    }
    return {
        "state": state,
        "message": messages.get(state, w.MAIL_READING),
        "last_ok": last_ok,
    }


def _filter_or_422(exc: BadFilter) -> HTTPException:
    # L2: fixed text. The caller typed the filter, so it goes to the log, not the answer.
    log.warning("bad_filter", filter=str(exc)[:40])
    return HTTPException(status_code=422, detail=w.ERR_FILTER)


def greeting(moment: datetime) -> str:
    if moment.hour < 12:
        return "Good morning"
    return "Good afternoon" if moment.hour < 17 else "Good evening"


def days_text(days: int | None) -> str:
    if days is None:
        return w.NO_DUE_DATE
    if days > 0:
        return w.fill_count(w.DAYS_LEFT_ONE, w.DAYS_LEFT, days)
    if days == 0:
        return w.LAST_DAY
    return w.fill_count(w.QUARTER_ENDED_ONE, w.QUARTER_ENDED, -days)


def _plural(word: str) -> str:
    if word.endswith("y") and not word.endswith(("ay", "ey", "oy", "uy")):
        return word[:-1] + "ies"
    return word if word.endswith("s") else word + "s"


def short_label(item: str, limit: int = SHORT_LIMIT) -> str:
    """Short name for a deliverable (R1.1 A.5a).

    The part before ' - '. A long comma list becomes 'first + N <noun>' when the rest share
    a last word: 'Client on-boarding T&Cs + 2 policies'. Else 'first + N more'.
    """
    head = item.split(" - ", 1)[0].strip()
    if len(head) <= limit:
        return head
    parts = [p.strip() for p in head.split(",") if p.strip()]
    if len(parts) < 2:
        return head[: limit - 1].rstrip() + "…"
    rest = parts[1:]
    last = {p.split()[-1].casefold() for p in rest}
    noun = "more"
    if len(last) == 1:
        word = last.pop()
        noun = word if len(rest) == 1 else _plural(word)
    return f"{parts[0]} + {len(rest)} {noun}"


def _shorten_names(names: str) -> str:
    """Late item names for the tile: short forms, ≤ 60 characters, then '…'."""
    out = ", ".join(short_label(x) for x in names.split(", ") if x)
    return out if len(out) <= 60 else out[:59].rstrip(" ,") + "…"


# The sheet tabs each number comes from. A tab never read once means the number is unknown.
TILE_TABS = {
    "deals": {"goals", "poc"},
    "live": {"poc"},
    "backlog": {"poc"},
    "p1": {"poc"},
    "week": {"poc"},
    "late": {"deliverables", "goals"},
    "money": {"poc", "deliverables"},
}

TILE_SPECS = (
    ("deals", w.TILE_DEALS, w.SRC_DEALS, {"view": "goals"}),
    ("live", w.TILE_LIVE, w.SRC_LIVE, {"view": "people", "filter": "in_conversation"}),
    ("backlog", w.TILE_BACKLOG, w.SRC_BACKLOG, {"view": "people", "filter": "backlog"}),
    ("p1", w.TILE_P1, w.SRC_P1, {"view": "people", "filter": "p1_waiting"}),
    ("week", w.TILE_WEEK, w.SRC_WEEK, {"view": "people", "filter": "contacted_this_week"}),
    ("late", w.TILE_LATE, w.SRC_LATE, {"view": "blockers"}),
)


def tiles(
    n: dict[str, Any], synced: bool, unread: frozenset[str] | set[str] = frozenset()
) -> list[dict[str, Any]]:
    """The six Today numbers (docs/08 view 1, D33). Unknown is None with a reason, never 0."""
    if not synced:
        return [
            {
                "key": k,
                "label": label,
                "value": None,
                "detail": w.NO_DATA,
                "source": src,
                "link": link,
            }
            for k, label, src, link in TILE_SPECS
        ]
    target = n["deals_target"]
    values: dict[str, tuple[Any, str]] = {
        "deals": (
            f"{n['deals_closed']} of {target if target is not None else '—'}",
            days_text(n["days_left"]),
        ),
        "live": (n["live_conversations"], w.LIVE_DETAIL),
        "backlog": (n["backlog"], w.fill(w.BACKLOG_DETAIL, n=n["backlog_with_email"])),
        "p1": (n["p1_waiting"], w.fill(w.P1_DETAIL, n=n["p1_total"])),
        "week": (n["contacted_this_week"], w.fill(w.WEEK_DETAIL, n=n["contacted_last_week"])),
        "late": (
            n["late_blockers"],
            _shorten_names(n["late_names"])
            if n["late_names"]
            else w.fill(w.LATE_DETAIL_TOTAL, n=n["late_total"]),
        ),
    }
    return [
        {
            "key": k,
            "label": label,
            "value": None if TILE_TABS[k] & unread else values[k][0],
            "detail": w.NO_TAB if TILE_TABS[k] & unread else values[k][1],
            "source": src,
            "link": link,
        }
        for k, label, src, link in TILE_SPECS
    ]


def goal_why(g: dict[str, Any]) -> str:
    """One line under the state pill that says why (R1.1 A.5a)."""
    late = g["blocker_count"]
    if g["rag"] != "on track" and late:
        return w.WHY_LATE_ONE if late == 1 else w.fill(w.WHY_LATE, n=late)
    deal_goal = g["text"].lower().startswith("close") or "deal" in (g["target"] or "").lower()
    if deal_goal:
        return w.fill(w.WHY_DEALS, won=g["won"], need=g["need"] or 1)
    if g["need"] is None:
        return w.WHY_NO_TARGET
    return w.fill(w.WHY_ACTIVE, n=g["active_count"], need=g["need"])


def https_link(value: str | None) -> str | None:
    """Only a real https URL becomes a link. Anything else (javascript:, text) stays text."""
    if not value:
        return None
    candidate = value.strip()
    if re.search(r"[\s<>\"'\x00-\x1f\x7f]", candidate):
        return None
    try:
        parts = urlsplit(candidate)
        host = parts.hostname
    except ValueError:  # 'https://[x' and other broken cells stay text, never a 500
        return None
    # No user info: 'https://google.com@evil.com' would show a trusted name for another host.
    if parts.scheme == "https" and host and parts.username is None and parts.password is None:
        return candidate
    return None


def contact_links(item: dict[str, Any]) -> dict[str, Any]:
    """Email (only a real address) as mailto, LinkedIn / paper / website as https (D39)."""
    email = (item.get("email") or "").strip()
    valid = bool(EMAIL_RE.match(email))
    site = (item.get("x_handle") or "").strip() or None
    return {
        "email": email if valid else None,
        "email_href": f"mailto:{quote(email, safe='@.+-_')}" if valid else None,
        "li_href": https_link(item.get("li_url")),
        "paper_href": https_link(item.get("paper_url")),
        "site_text": site,
        "site_href": https_link(site),
    }


def usd_short(amount: int) -> str:
    """40000 → '40k', 1750000 → '1.75M': the short form a band midpoint is shown in."""
    if amount >= 999_950:  # 999,999 is "1M", never "1000k"
        return f"{round(amount / 1_000_000, 2):g}M"
    if amount >= 1_000:
        return f"{round(amount / 1_000, 1):g}k"
    return str(amount)


def value_label(size_usd: int | None, size_text: str | None) -> str:
    """D42: a band shows its midpoint as '≈ USD 40k (USD 30–50k)'; an exact value as given."""
    if size_usd is None:
        return w.NO_VALUE
    if is_band(size_text):
        return f"≈ USD {usd_short(size_usd)} ({size_text})"
    return f"USD {size_usd:,}"


def money_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for m in items:
        m["short"] = short_label(m["deliverable"])
        m["value_label"] = value_label(m["size_usd"], m["size_text"])
    return items


def money_value(money: dict[str, int]) -> str | None:
    """USD total of known values. None when no blocked deal has a value: never 0 for unknown."""
    if money["deals"] and money["deals"] == money["no_value"]:
        return None
    return f"USD {money['total_usd']:,}"


def money_tile(
    money: dict[str, int], unread: frozenset[str] | set[str] = frozenset()
) -> dict[str, Any]:
    if TILE_TABS["money"] & unread:
        return {
            "key": "money",
            "label": w.TILE_MONEY,
            "value": None,
            "detail": w.NO_TAB,
            "source": w.SRC_MONEY,
            "link": {"view": "money"},
        }
    if money["no_value"]:
        detail = w.fill(w.MONEY_NO_VALUE, n=money["no_value"])
    else:
        detail = w.fill(w.MONEY_DETAIL, n=money["deals"])
    return {
        "key": "money",
        "label": w.TILE_MONEY,
        "value": money_value(money),
        "detail": detail,
        "source": w.SRC_MONEY,
        "link": {"view": "money"},
    }


def create_app() -> FastAPI:
    app = FastAPI(
        title="Saley",
        version="2.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(reject_unknown_params), Depends(validate_as_header)],
    )

    @app.exception_handler(Exception)
    def server_error(request: Request, exc: Exception) -> Response:
        # This answer leaves through the outer error middleware, past the header middleware
        # below: add the same headers here. Fixed text only: no trace, SQL or value.
        log.error("api_error", path=request.url.path, error=type(exc).__name__)
        response = JSONResponse(status_code=500, content={"detail": w.ERR_SERVER})
        response.headers["Cache-Control"] = "no-store"
        return _security_headers(response)

    @app.exception_handler(SnapshotChanged)
    def snapshot_changed(request: Request, exc: SnapshotChanged) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"detail": w.ERR_CHANGED, "sync_id": exc.current}
        )

    @app.exception_handler(RequestValidationError)
    def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Fixed text: the default answer repeats the input the caller sent (LOW finding).
        return JSONResponse(status_code=422, content={"detail": w.ERR_BAD_ACT})

    app.add_middleware(BodyLimit)  # inside security_headers, so a 413 still gets the headers

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        if request.url.path.startswith("/api/"):
            if _bad_host(request):
                response: Response = JSONResponse(status_code=403, content={"detail": w.ERR_HOST})
            elif _cross_site(request):
                response = JSONResponse(status_code=403, content={"detail": w.ERR_CROSS_SITE})
            else:
                response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
        else:
            response = await call_next(request)
        current = response.headers.get("content-security-policy")
        return _security_headers(response, csp_for(request.url.path, current))

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/me", response_model=schemas.MeOut)
    def me(user: User, conn: Db) -> dict[str, Any]:
        team = queries.rows(
            conn, "select short, name, role from v_team where role <> 'admin' order by id"
        )
        return {
            "email": user.email,
            "role": user.role,
            "dev": get_settings().is_dev,
            # D207: no "View as" in any mode, so no picker flag. The page shows the signed-in
            # person; tests use X-Saley-As with the mock provider (D50, D116).
            "features": features.flags(),
            "people": team,
        }

    @app.get("/api/status", response_model=schemas.StatusOut)
    def status(user: User, conn: Db) -> dict[str, Any]:
        state, message = sync_state(conn)
        good = queries.last_job(conn, "sheet_sync")
        late_total = conn.execute(text("select count(*) from v_deliverable_late")).scalar_one()
        counts = write_counts(conn)
        note, bad = write_note(counts)
        news_bar, news_old = newsfresh.top_bar(conn)
        return {
            "state": state,
            "message": message,
            "updated": hhmm(good),
            "sheet": hhmm(good),
            "news": news_bar,
            "news_old": news_old,
            "sheet_mode": get_settings().sheet_mode,
            "late_total": late_total,
            "pending_writes": counts["pending"] + counts["held"],
            **counts,
            "write_note": note,
            "write_bad": bad,
            **meta(conn),
        }

    @app.get("/api/goals", response_model=schemas.GoalsOut)
    def goals(
        user: User, conn: Db, limit: Limit = DEFAULT_LIMIT, offset: Offset = 0
    ) -> dict[str, Any]:
        items, total = queries.goals(conn, limit, offset)
        for g in items:
            g["due_label"] = day_label(g["due"])
            g["active"] = g["active"][:ACTIVE_SHOWN]  # active_count keeps the full number
            g["weighted_note"] = w.WEIGHTED_EMPTY if g["weighted_usd"] is None else None
            g["why"] = goal_why(g)
            for b in g["blockers"]:
                b["short"] = short_label(b["item"])
        goal_model.decorate(conn, items)
        return {"items": items, "total": total, "linked_by": w.LINKED_BY, **meta(conn)}

    @app.get("/api/accounts", response_model=schemas.AccountsOut)
    def accounts(
        user: User,
        conn: Db,
        filter: Filter = "all",
        q: Q = None,
        limit: Limit = DEFAULT_LIMIT,
        offset: Offset = 0,
    ) -> dict[str, Any]:
        try:
            items, total = queries.accounts(conn, filter, q, limit, offset)
        except BadFilter as exc:
            raise _filter_or_422(exc) from exc
        accounts_model.decorate(conn, items)
        counts = (
            conn.execute(
                text(
                    "select count(*) filter (where people > 0) as with_people, "
                    "count(*) filter (where in_master) as in_master from v_account_coverage"
                )
            )
            .mappings()
            .one()
        )
        return {
            "items": items,
            "total": total,
            "filter": filter,
            **dict(counts),
            "industries": queries.industry_coverage(conn),
            "tags_legend": accounts_model.legend(),
            **meta(conn),
        }

    @app.get("/api/accounts/{account_id}", response_model=schemas.AccountOut)
    def account(account_id: RowId, user: User, conn: Db) -> dict[str, Any]:
        item = queries.account(conn, account_id)
        if item is None:
            raise HTTPException(status_code=404, detail=w.ERR_NO_COMPANY)
        accounts_model.decorate_detail(conn, item)
        return {"item": item, **meta(conn)}

    @app.get("/api/people", response_model=schemas.PeopleOut)
    def people(
        user: User,
        conn: Db,
        filter: Filter = "backlog",
        q: Q = None,
        limit: Limit = DEFAULT_LIMIT,
        offset: Offset = 0,
    ) -> dict[str, Any]:
        try:
            items, total = queries.people(conn, filter, q, limit, offset)
        except BadFilter as exc:
            raise _filter_or_422(exc) from exc
        for row in items:
            row["working_on"] = working_on(row.pop("working_on_raw"))
        counts = (
            conn.execute(
                text(
                    "select count(*) as people, count(*) filter (where coalesce(notes, '') <> '' "
                    "or coalesce(about, '') <> '') as with_notes, "
                    "count(*) filter (where coalesce(paper_url, '') <> '') as with_paper, "
                    "count(*) filter (where is_backlog) as backlog, "
                    "count(*) filter (where is_p1) as p1, "
                    "count(*) filter (where is_p1_waiting) as p1_waiting, "
                    "count(*) filter (where in_conversation) as in_conversation, "
                    "count(*) filter (where reconnect_due) as reconnect_due from v_contact_stage"
                )
            )
            .mappings()
            .one()
        )
        return {
            "items": items,
            "total": total,
            "filter": filter,
            "counts": dict(counts),
            **meta(conn),
        }

    @app.get("/api/people/{contact_id}", response_model=schemas.PersonOut)
    def person(contact_id: RowId, user: User, conn: Db) -> dict[str, Any]:
        item = queries.person(conn, contact_id)
        if item is None:
            raise HTTPException(status_code=404, detail=w.ERR_NO_PERSON)
        item.update(contact_links(item))
        raw = item.pop("working_on_raw")
        item["working_on"] = working_on(raw)
        people_model.decorate(conn, item, raw)
        return {"item": item, **meta(conn)}

    @app.get("/api/blockers", response_model=schemas.BlockersOut)
    def blockers(
        user: User, conn: Db, q: Q = None, limit: Limit = DEFAULT_LIMIT, offset: Offset = 0
    ) -> dict[str, Any]:
        found = queries.blockers(conn, q, limit, offset)
        for row in found["late"]:
            row["due_label"] = day_label(row["due"])
            row["short"] = short_label(row["item"])
        money = queries.money_summary(conn)
        tile = money_tile(money, queries.tab_states(conn)[1])
        return {**found, "money": money, "money_tile": tile, **meta(conn)}

    @app.get("/api/money-blocked", response_model=schemas.MoneyOut)
    def money_blocked(
        user: User, conn: Db, limit: Limit = DEFAULT_LIMIT, offset: Offset = 0
    ) -> dict[str, Any]:
        summary = queries.money_summary(conn)
        return {
            "items": money_rows(queries.money_blocked(conn, limit, offset)),
            "total": summary["deals"],
            "total_usd": summary["total_usd"],
            "no_value": summary["no_value"],
            **meta(conn),
        }

    @app.get("/api/scorecard", response_model=schemas.ScorecardOut)
    def scorecard(
        user: User, conn: Db, limit: Limit = DEFAULT_LIMIT, offset: Offset = 0
    ) -> dict[str, Any]:
        items, total = queries.scorecard(conn, limit, offset)
        for r in items:
            r["reply_note"] = w.REPLY_NOTE  # Reply % has no source until R2: never 0
        # D118: "last week N" is the snapshot of the day 7 days before; no snapshot is null.
        last_day = today_ist() - timedelta(days=metrics.LAST_WEEK_DAYS)
        items = metrics.merge_last_week(items, queries.scorecard_last_week(conn, last_day))
        return {"items": items, "total": total, **meta(conn)}

    @app.get("/api/health/full", response_model=schemas.HealthOut)
    def health_full(user: Annotated[SignedIn, Depends(require_admin)], conn: Db) -> dict[str, Any]:
        from saley.web import signals  # late import: signals needs this module's helpers

        return {
            **queries.health(conn),
            "writes": writes_state(conn),
            "mail": mail_state(conn),
            "notes": notes_health.notes_state(conn),
            "llm_cap": get_settings().llm_daily_call_cap,
            "news": signals.news_health(conn),
            "news_note": w.NEWS_NOTE,
            "search": signals.search_health(conn),
            "apollo": signals.apollo_health(),
            "sheet_mode": get_settings().sheet_mode,
            **meta(conn),
        }

    @app.get("/api/search", response_model=schemas.SearchOut)
    def search(
        user: User, conn: Db, q: Annotated[str, Query(min_length=1, max_length=queries.MAX_Q)]
    ) -> dict[str, Any]:
        return {"q": q, **queries.search(conn, q), **meta(conn)}

    @app.get("/", include_in_schema=False)
    def index(request: Request) -> Response:
        if session_user(request) is None:
            return RedirectResponse("/signin", status_code=303)
        return FileResponse(STATIC / "index.html")

    # Late import: the routers need this module.
    from saley.web import act, admin, ask, next_app, pack, signals, team, today

    auth.install(app)
    next_app.install(app)  # before the /static mount: /static/app must match first (D145)
    app.include_router(act.router)
    app.include_router(admin.router)
    app.include_router(ask.router)
    app.include_router(team.router)
    app.include_router(pack.router)
    app.include_router(today.router)
    app.include_router(signals.router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    if get_settings().is_dev:
        # Blocks DNS rebinding: a web page cannot reach the laptop API under its own name.
        app.add_middleware(
            TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"]
        )
    return app
