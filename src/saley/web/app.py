"""FastAPI dashboard: read-only JSON under /api, static page at /.

Why: docs/08 is the contract. There is no write endpoint. Routes are plain `def` (FastAPI runs
them in a thread pool) on the read-only engine. Each request reads ONE sheet snapshot in one
REPEATABLE READ READ ONLY transaction and returns its `sync_id` (D40). Every response model
lists only the fields its view shows (R1.1 C). Dev mode (SALEY_ENV=dev) signs in as
DEV_USER_EMAIL, from this machine only; elsewhere every /api call gets 401 until R4.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Connection, text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from saley.core.clock import day_label, hhmm, now_ist
from saley.core.db import ro_engine
from saley.core.logging import get_logger
from saley.core.settings import get_settings
from saley.sales.parse import is_band
from saley.web import queries, schemas
from saley.web import words as w
from saley.web.queries import DEFAULT_LIMIT, MAX_LIMIT, BadFilter

STATIC = Path(__file__).parent / "static"
STALE_FACTOR = 3  # "Updated" turns red after 3 × SHEET_SYNC_SECONDS without a sync (D43)
ACTIVE_SHOWN = 8
SHORT_LIMIT = 40
log = get_logger("saley.web")
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'none'"
)
EMAIL_RE = re.compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+$")
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})
NEWS_STEPS = (
    "Sources tried",
    "Sources that answered",
    "Items pulled",
    "Dropped: seen before",
    "Dropped: duplicate story",
    "Dropped: not relevant",
    "Stored as stories",
    "Scored",
    "Scored 4 or 5",
    "About our companies or people",
    "Sent to Discord",
)


@dataclass(frozen=True)
class SignedIn:
    email: str
    role: str


class SnapshotChanged(Exception):
    """The client asked for a sync_id that is no longer current (D40)."""

    def __init__(self, current: int | None) -> None:
        self.current = current


def current_user(request: Request) -> SignedIn:
    """Dev sign-in only for SALEY_ENV=dev AND a client on this machine.

    Why: SALEY_ENV defaults to dev for laptops. A server that forgets to set it must still
    refuse strangers, so a request that came through caddy (not loopback) gets 401 too.
    """
    settings = get_settings()
    client = request.client.host if request.client else ""
    if settings.is_dev and client in LOOPBACK:
        return SignedIn(settings.dev_user_email, "admin")
    raise HTTPException(status_code=401, detail=w.ERR_SIGN_IN)


def require_admin(user: Annotated[SignedIn, Depends(current_user)]) -> SignedIn:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail=w.ERR_ADMIN)
    return user


def _query_names(dependant: Any) -> set[str]:
    """Every query parameter name a route accepts, its sub-dependencies included."""
    names = {p.alias for p in dependant.query_params}
    for sub in dependant.dependencies:
        names |= _query_names(sub)
    return names


def reject_unknown_params(request: Request) -> None:
    """Only the query parameters a route declares are allowed (R1.1 C)."""
    route = request.scope.get("route")
    if route is None or not hasattr(route, "dependant"):
        return
    allowed = _query_names(route.dependant)
    for key in request.query_params:
        if key not in allowed:
            raise HTTPException(status_code=422, detail=w.fill(w.ERR_PARAM, x=key[:40]))


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
    return "ok", w.fill(w.UPDATED, t=hhmm(good), s=hhmm(good), n="—")


def _filter_or_422(exc: BadFilter) -> HTTPException:
    return HTTPException(status_code=422, detail=w.fill(w.ERR_FILTER, x=str(exc)[:40]))


def greeting(moment: datetime) -> str:
    if moment.hour < 12:
        return "Good morning"
    return "Good afternoon" if moment.hour < 17 else "Good evening"


def days_text(days: int | None) -> str:
    if days is None:
        return w.NO_DUE_DATE
    if days > 0:
        return w.fill(w.DAYS_LEFT, n=days)
    return w.LAST_DAY if days == 0 else w.fill(w.QUARTER_ENDED, n=-days)


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


def _security_headers(response: Response) -> Response:
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return response


def _cross_site(request: Request) -> bool:
    """Only same-origin pages and direct visits may call the API. A broken Origin is refused."""
    if request.headers.get("sec-fetch-site") in ("cross-site", "same-site"):
        return True
    origin = request.headers.get("origin")
    if not origin:
        return False
    try:
        return urlsplit(origin).netloc != request.headers.get("host")
    except ValueError:
        return True


def create_app() -> FastAPI:
    app = FastAPI(
        title="Saley",
        version="2.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(reject_unknown_params)],
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

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        if request.url.path.startswith("/api/"):
            if _cross_site(request):
                response: Response = JSONResponse(
                    status_code=403, content={"detail": w.ERR_CROSS_SITE}
                )
            else:
                response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
        else:
            response = await call_next(request)
        return _security_headers(response)

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
            "people": team,
        }

    @app.get("/api/status", response_model=schemas.StatusOut)
    def status(user: User, conn: Db) -> dict[str, Any]:
        state, message = sync_state(conn)
        good = queries.last_job(conn, "sheet_sync")
        late_total = conn.execute(text("select count(*) from v_deliverable_late")).scalar_one()
        return {
            "state": state,
            "message": message,
            "updated": hhmm(good),
            "sheet": hhmm(good),
            "news": "—",
            "sheet_mode": get_settings().sheet_mode,
            "late_total": late_total,
            **meta(conn),
        }

    @app.get("/api/today", response_model=schemas.TodayOut)
    def today(
        user: User,
        conn: Db,
        user_short: Annotated[
            str | None, Query(alias="user", max_length=20, pattern=r"^[a-z]+$")
        ] = None,
    ) -> dict[str, Any]:
        now = now_ist()
        name = None
        if user_short:
            name = conn.execute(
                text("select name from v_team where short = :s"), {"s": user_short}
            ).scalar()
        m = meta(conn)
        return {
            "greeting": f"{greeting(now)}, {name}" if name else greeting(now),
            "date_label": f"{now:%A} {now.day} {now:%B}",
            "tiles": tiles(
                queries.today_numbers(conn),
                m["last_computed"] is not None,
                queries.tab_states(conn)[1],
            ),
            **m,
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
            **meta(conn),
        }

    @app.get("/api/accounts/{account_id}", response_model=schemas.AccountOut)
    def account(account_id: RowId, user: User, conn: Db) -> dict[str, Any]:
        item = queries.account(conn, account_id)
        if item is None:
            raise HTTPException(status_code=404, detail=w.ERR_NO_COMPANY)
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
        return {"items": items, "total": total, **meta(conn)}

    @app.get("/api/health/full", response_model=schemas.HealthOut)
    def health_full(user: Annotated[SignedIn, Depends(require_admin)], conn: Db) -> dict[str, Any]:
        return {
            **queries.health(conn),
            "llm_cap": get_settings().llm_daily_call_cap,
            "news": [{"step": step, "n": None} for step in NEWS_STEPS],
            "news_note": w.NEWS_NOTE,
            "sheet_mode": get_settings().sheet_mode,
            **meta(conn),
        }

    @app.get("/api/search", response_model=schemas.SearchOut)
    def search(
        user: User, conn: Db, q: Annotated[str, Query(min_length=1, max_length=queries.MAX_Q)]
    ) -> dict[str, Any]:
        return {"q": q, **queries.search(conn, q), **meta(conn)}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    if get_settings().is_dev:
        # Blocks DNS rebinding: a web page cannot reach the laptop API under its own name.
        app.add_middleware(
            TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"]
        )
    return app
