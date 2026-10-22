"""GET routes of news and suggestions, and POST /api/seen (R3, docs/08 views 5, 6, 8, 14).

Why: Signals (All and Linked), Events and the four Suggestions lists read views with the
read-only role. Every list states its time window, hides what this person already saw, says how
many it left out (D75), and shows when it was computed (D22). Only declared query parameters are
accepted (unknown ones are a 422); response models list their fields. The only write is
`POST /api/seen`, which calls one SECURITY DEFINER function (`saley_mark_seen`).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from sqlalchemy import Connection, text

from saley.core.clock import day_label, hhmm, now_ist, today_ist, tz
from saley.core.logging import get_logger
from saley.intel import suggest as rules
from saley.web import events_model, newsfresh, queries, schemas, seen_tabs
from saley.web import words as w
from saley.web.act import ActDb, ActHeader, actor_short, call_function
from saley.web.app import Db, Limit, Offset, User, https_link, meta
from saley.web.newsfresh import apollo_health, news_health, search_health
from saley.web.queries import DEFAULT_LIMIT

__all__ = ["apollo_health", "news_health", "router", "search_health"]  # health: see newsfresh

log = get_logger("saley.web.signals")
router = APIRouter()

RANGES = {
    "day": (1, w.WINDOW_DAY),
    "week": (7, w.WINDOW_WEEK),
    "month": (30, w.WINDOW_MONTH),
    "year": (365, w.WINDOW_YEAR),
}
HITS_PER_COMPANY = 5
MAX_DAY_YEARS = 5  # A2: a `day` filter more than 5 years from today is a 422, never a 500
SUGGEST_RANK_CAP = 1000  # A5: rows ranked per Suggestions request; see `suggest`
LOOK_SHOWN = 8  # lines on Today; the full count is `total`
LOOK_DAYS = 7  # v_needs_look reads linked stories of the last 7 days
SUGGESTS_SHOWN = 3
REASON_PARTS = 3
SEEN_TYPE = {"all": "story", "linked": "signal"}
SUGGEST_VIEWS = {
    "warm": "v_suggest_warm",
    "best": "v_suggest_best",
    "newco": "v_suggest_newco",
    "res": "v_suggest_res",
}
PART_WORDS = {
    "connections": w.PART_CONNECTIONS,
    "email": w.PART_EMAIL,
    "decision_role": w.PART_DECISION_ROLE,
    "p1": w.PART_P1,
    "paper": w.PART_PAPER,
    "role_budget": w.PART_ROLE_BUDGET,
    "role_research": w.PART_ROLE_RESEARCH,
    "role_data": w.PART_ROLE_DATA,
    "role_user": w.PART_ROLE_USER,
    "goal_fit": w.PART_GOAL_FIT,
    "funding": w.PART_FUNDING,
    "news": w.PART_NEWS,
    "top_story": w.PART_TOP_STORY,
    "res_paper": w.PART_PAPER,
    "topic": w.PART_TOPIC,
    "recent": w.PART_RECENT,
    "city_event": w.PART_CITY_EVENT,
}
PART_ONE = {  # the singular template of a part that shows a count
    "goal_fit": w.PART_GOAL_FIT_ONE,
    "news": w.PART_NEWS_ONE,
    "recent": w.PART_RECENT_ONE,
}
EVENT_SEEN = (
    "exists (select 1 from v_user_seen u where u.user_short = :me "
    "and u.item_type = 'event' and u.item_id = t.event_id)"
)
SEARCH_WORDS = {
    "pending": w.SEARCH_PENDING,
    "running": w.SEARCH_RUNNING,
    "done": w.SEARCH_DONE,
    "failed": w.SEARCH_FAILED,
}
Tag = Literal["Opportunity", "People", "Risk", "Market", "Tender", "Discover", "Ours"]
EntityText = Annotated[str | None, Query(max_length=queries.MAX_Q)]
SourceText = Annotated[str | None, Query(max_length=120)]


# ------------------------------------------------------------------ shared helpers


def when_label(moment: datetime | None) -> str:
    """'Fri 17 Oct' in IST, or an em dash."""
    return day_label(moment.astimezone(tz())) if moment else "—"


def news_meta(conn: Connection) -> dict[str, Any]:
    """Meta for a news list: 'last computed' is the last good news run, not the sheet sync.

    Stale when ANY family (editions, arXiv, OpenAlex) is past its own limit (A1): one busy
    family must not hide a frozen one.
    """
    base = meta(conn)
    last = newsfresh.last_good(conn)
    return {
        **base,
        "last_computed": last.isoformat() if last else None,
        "last_computed_label": hhmm(last),
        "stale": last is None or any(f["stale"] for f in newsfresh.families(conn)),
    }


def checked_day(day: date | None) -> date | None:
    """A `day` filter must lie within 5 years of today (A2). Else the date maths overflows."""
    if day is None:
        return None
    if abs((day - today_ist()).days) > MAX_DAY_YEARS * 366:
        raise HTTPException(status_code=422, detail=w.ERR_DAY)
    return day


def range_window(
    range_: str, day: date | None, now: datetime
) -> tuple[dict[str, str], datetime, datetime]:
    """The window of a news list: a day (IST) or the last N days. Always stated (D22)."""
    if day is not None:
        start = datetime.combine(day, time.min, tzinfo=tz())
        end = start + timedelta(days=1)
        return (
            {"from": start.isoformat(), "to": end.isoformat(), "label": day_label(day)},
            start,
            end,
        )
    days, label = RANGES[range_]
    start = now - timedelta(days=days)
    return {"from": start.isoformat(), "to": now.isoformat(), "label": label}, start, now


def seen_ids(conn: Connection, me: str, item_type: str) -> set[int]:
    found = conn.execute(
        text("select item_id from v_user_seen where user_short = :me and item_type = :t"),
        {"me": me, "t": item_type},
    )
    return {int(r[0]) for r in found}


# ------------------------------------------------------------------ Signals


def _signal_filters(
    tab: str,
    *,
    cat: str | None,
    src: str | None,
    tag: str | None,
    entity: str | None,
    min_importance: int | None,
    linked_only: bool,
) -> tuple[list[str], dict[str, Any]]:
    cond = ["t.first_seen_at >= :start", "t.first_seen_at < :end"]
    params: dict[str, Any] = {}
    if cat:
        cond.append("t.topic = :cat")  # D156: a topic name, never a source letter
        params["cat"] = cat
    if src:
        cond.append("(t.source_name = :src or :src = any(t.outlets))")
        params["src"] = src
    if tag:
        cond.append("t.tag = :tag")
        params["tag"] = tag
    if min_importance:
        cond.append("t.importance >= :minimp")
        params["minimp"] = min_importance
    pattern = queries.like(entity)
    if pattern:
        params["entity"] = pattern
        if tab == "linked":
            cond.append("t.entity_name ilike :entity escape '\\'")
        else:
            cond.append(
                "exists (select 1 from v_signal_linked l where l.story_id = t.story_id "
                "and l.entity_name ilike :entity escape '\\')"
            )
    if tab == "linked":
        cond.append("t.state <> 'dismissed'")
    elif linked_only:
        cond.append("t.linked_count > 0")
    return cond, params


def _list_signals(
    conn: Connection, tab: str, me: str, start: datetime, end: datetime, f: dict[str, Any]
) -> tuple[list[dict[str, Any]], int, int]:
    """(rows of the page, shown total, new total, seen total)."""
    view, idcol = ("v_signal_all", "story_id") if tab == "all" else ("v_signal_linked", "signal_id")
    cond, params = _signal_filters(
        tab,
        cat=f["cat"],
        src=f["src"],
        tag=f["tag"],
        entity=f["entity"],
        min_importance=f["min_importance"],
        linked_only=f["linked_only"],
    )
    params.update(start=start, end=end, me=me, stype=SEEN_TYPE[tab])
    seen = (
        "exists (select 1 from v_user_seen u where u.user_short = :me "
        f"and u.item_type = :stype and u.item_id = t.{idcol})"
    )
    where = " and ".join(cond)
    pl = f["plan"]
    new_n, seen_n = seen_tabs.counts(conn, f"{view} t", where, params, seen)
    order = (
        "(t.linked_count > 0 or t.flag) desc, t.impact desc, t.importance desc, "
        "t.outlet_count desc, coalesce(t.published_at, t.first_seen_at) desc, t.story_id desc"
        if tab == "all"
        else "t.impact desc, t.importance desc, t.confidence desc, t.outlet_count desc, "
        "coalesce(t.published_at, t.first_seen_at) desc, t.signal_id desc"
    )
    rows = queries.rows(
        conn,
        f"select t.*, {seen} as seen from {view} t where {where}{pl.clause(seen)} "
        f"order by {order} limit :limit offset :offset",
        **params,
        limit=f["limit"],
        offset=f["offset"],
    )
    return rows, pl.total(new_n, seen_n), new_n, seen_n


def signal_item(row: dict[str, Any]) -> dict[str, Any]:
    shown = row["published_at"] or row["first_seen_at"]
    return {
        "id": row["story_id"],
        "title": row["title"],
        "href": https_link(row["url"]),
        "outlets": row["outlets"],
        "outlet_count": row["outlet_count"],
        "source": row["source_name"],
        "categories": [row["topic"]] if row["topic"] else [],  # D156: a name, never a letter
        "kind": row["kind"],
        "tag": row["tag"],
        "importance": row["importance"],
        "impact": row["impact"],
        "why": row["why"],
        "published_at": row["published_at"],
        "published_label": when_label(shown),
        "linked_count": row["linked_count"],
        "ours": bool(row["linked_count"]) or bool(row["flag"]),
        "seen": bool(row["seen"]),
    }


def linked_item(row: dict[str, Any]) -> dict[str, Any]:
    shown = row["published_at"] or row["first_seen_at"]
    return {
        "id": row["signal_id"],
        "story_id": row["story_id"],
        "title": row["title"],
        "href": https_link(row["url"]),
        "outlets": row["outlets"],
        "source": row["source_name"],
        "entity_type": row["entity_type"],
        "entity_id": row["entity_id"],
        "entity": row["entity_name"],
        "account": row["account_name"],
        "match_kind": row["match_kind"],
        "confidence": row["confidence"],
        "verify": row["verify"],
        "verify_note": w.VERIFY_NOTE if row["verify"] else None,
        "action_hint": row["action_hint"] or w.NO_HINT_YET,  # D79
        "state": row["state"],
        "tag": row["tag"],
        "importance": row["importance"],
        "impact": row["impact"],
        "why": row["why"],
        "published_at": row["published_at"],
        "published_label": when_label(shown),
        "seen": bool(row["seen"]),
    }


@router.get("/api/signals", response_model=schemas.SignalsOut)
def signals(
    request: Request,
    user: User,
    conn: Db,
    tab: Literal["all", "linked"] = "all",
    range: Literal["day", "week", "month", "year"] = "week",
    day: date | None = None,
    cat: seen_tabs.CatParam = None,
    src: SourceText = None,
    tag: Tag | None = None,
    entity: EntityText = None,
    min_importance: Annotated[int | None, Query(ge=1, le=5)] = None,
    linked_only: bool = False,
    view: seen_tabs.ViewParam = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> dict[str, Any]:
    me = actor_short(request, user)
    pl = seen_tabs.plan(view)
    limit, offset = pl.page(limit, offset)
    window, start, end = range_window(range, checked_day(day), now_ist())
    filters = {
        "cat": seen_tabs.topic_filter(cat),
        "src": src,
        "tag": tag,
        "entity": entity,
        "min_importance": min_importance,
        "linked_only": linked_only,
        "plan": pl,
        "limit": limit,
        "offset": offset,
    }
    rows, total, new_n, seen_n = _list_signals(conn, tab, me, start, end, filters)
    build = signal_item if tab == "all" else linked_item
    return {
        "tab": tab,
        "items": [build(r) for r in rows],
        "total": total,
        "window": window,
        "left_out_seen": pl.left_out(seen_n),
        **pl.fields(new_n, seen_n),
        **news_meta(conn),
    }


# ------------------------------------------------------------------ Events


def days_text(days: int | None) -> str:
    if days is None:
        return w.EVENT_NO_DATE
    if days == 0:
        return w.EVENT_TODAY
    if days > 0:
        return w.fill_count(w.EVENT_IN_DAYS_ONE, w.EVENT_IN_DAYS, days)
    return w.fill_count(w.EVENT_AGO_ONE, w.EVENT_AGO, -days)


def event_item(row: dict[str, Any]) -> dict[str, Any]:
    start, end = row["start_date"], row["end_date"]
    label = w.EVENT_NO_DATE
    if start is not None:
        label = (
            day_label(start)
            if not end or end == start
            else f"{day_label(start)} to {day_label(end)}"
        )
    return {
        "id": row["event_id"],
        "name": row["name"],
        "city": events_model.city_of(row["city"]),
        "venue": events_model.venue_of(row["city"]),
        "start": start,
        "end": end,
        "date_label": label,
        "days_left": row["days_left"],
        "days_text": days_text(row["days_left"]),
        "registered": row["registered"] is True,
        "people_in_city": row["people_in_city"],
        "connected": row["connected"],
        "p1": row["p1"],
        "speaker_matches": w.NOT_BUILT,  # S12 is out of scope for R3
        "speaker_soon": events_model.SPEAKER_FEATURE,  # the "coming soon" badge key (D164)
        "href": https_link(row["url"]),
        "seen": bool(row["seen"]),
    }


@router.get("/api/events", response_model=schemas.EventsOut)
def events(
    request: Request,
    user: User,
    conn: Db,
    when: Literal["upcoming", "past"] = "upcoming",
    view: seen_tabs.ViewParam = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> dict[str, Any]:
    me = actor_short(request, user)
    pl = seen_tabs.plan(view)
    limit, offset = pl.page(limit, offset)
    order = events_model.ORDER_UPCOMING if when == "upcoming" else events_model.ORDER_PAST
    params: dict[str, Any] = {"up": when == "upcoming", "me": me}
    # A5: the seen filter, the count and the page are all SQL; no event list is built in Python.
    new_n, seen_n = seen_tabs.counts(
        conn, "v_event_targets t", "t.upcoming = :up", params, EVENT_SEEN
    )
    page = queries.rows(
        conn,
        "select t.event_id, t.name, t.city, t.start_date, t.end_date, t.url, t.registered, "
        "t.days_left, t.people_in_city, t.connected, t.p1, "
        f"{EVENT_SEEN} as seen from v_event_targets t "
        f"where t.upcoming = :up{pl.clause(EVENT_SEEN)} "
        f"order by {order} limit :limit offset :offset",
        **params,
        limit=limit,
        offset=offset,
    )
    base = meta(conn)
    today = today_ist()
    window = {
        "from": today.isoformat() if when == "upcoming" else None,
        "to": None if when == "upcoming" else today.isoformat(),
        "label": w.WINDOW_UPCOMING if when == "upcoming" else w.WINDOW_PAST,
    }
    return {
        "when": when,
        "items": [event_item(r) for r in page],
        "total": pl.total(new_n, seen_n),
        "window": window,
        "left_out_seen": pl.left_out(seen_n),
        **pl.fields(new_n, seen_n),
        **base,
    }


# ------------------------------------------------------------------ Suggestions


def part_label(part: rules.Part) -> str:
    if part.n is None:  # an unknown count is "—", never 0 (CLAUDE.md; step 10 review)
        return w.fill(PART_WORDS.get(part.code, w.PART_OTHER), n="—")
    n = part.n
    if part.code in PART_ONE:  # A12: "1 story", not "1 stories"
        return w.fill_count(PART_ONE[part.code], PART_WORDS[part.code], n)
    return w.fill(PART_WORDS.get(part.code, w.PART_OTHER), n=n)


def search_state(row: dict[str, Any]) -> dict[str, Any]:
    state = row.get("search_state")
    return {
        "state": state,
        "message": SEARCH_WORDS.get(state or "", w.SEARCH_NONE),
        # No search yet: the count is unknown (None, shown as "—"), never 0 (step 10 review).
        "hits": None if row.get("hits") is None else int(row["hits"]),
    }


def suggestion_item(
    s: rules.Suggestion, seen: set[int], hits: dict[int, list[dict[str, Any]]]
) -> dict[str, Any]:
    parts = [
        {"code": p.code, "label": part_label(p), "points": p.points}
        for p in sorted(s.parts, key=lambda p: -p.points)
    ]
    via = s.evidence.get("via")
    newco = s.kind == "newco"
    state = search_state(dict(s.raw)) if newco else None
    evidence = {k: v for k, v in s.evidence.items() if k != "via"}
    return {
        "kind": s.kind,
        "id": s.ref_id,
        "contact_id": s.contact_id,
        "account_id": s.account_id,
        "subject": s.name,
        "company": s.company,
        "title": s.title,
        "reason": " · ".join(p["label"] for p in parts[:REASON_PARTS]),
        "score": s.score,
        "parts": parts,
        "via": w.fill(w.SUGGEST_VIA, names=via) if via else None,
        "evidence": evidence,
        "link": {"view": "accounts" if newco else "people"},
        "can_find_people": newco and (state or {}).get("state") not in ("pending", "running"),
        "search": state,
        "hits": hits.get(s.account_id or 0, []) if newco else [],
        "seen": s.ref_id in seen,
    }


def _hits_for(
    conn: Connection, suggestions: list[rules.Suggestion]
) -> dict[int, list[dict[str, Any]]]:
    """Search hits per company, with the Add form filled from sheet values and the hit (D76)."""
    ids = [s.account_id for s in suggestions if s.kind == "newco" and s.account_id]
    if not ids:
        return {}
    found = queries.rows(
        conn,
        "select id, account_id, url, title, engine from v_search_hit "
        "where account_id = any(:ids) order by found_at desc, id desc",
        ids=ids,
    )
    by_account = {s.account_id: s for s in suggestions if s.kind == "newco"}
    out: dict[int, list[dict[str, Any]]] = {}
    for hit in found:
        bucket = out.setdefault(hit["account_id"], [])
        if len(bucket) >= HITS_PER_COMPANY:
            continue
        owner = by_account[hit["account_id"]]
        industry = owner.evidence.get("industry")
        bucket.append(
            {
                "id": hit["id"],
                "title": hit["title"],
                "href": hit["url"],
                "engine": hit["engine"],
                "form": rules.add_form(owner.name, industry, hit["url"], hit["title"]),
            }
        )
    return out


@router.get("/api/suggest/{kind}", response_model=schemas.SuggestOut)
def suggest(
    kind: Annotated[Literal["warm", "best", "newco", "res"], Path()],
    request: Request,
    user: User,
    conn: Db,
    view: seen_tabs.ViewParam = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> dict[str, Any]:
    me = actor_short(request, user)
    pl = seen_tabs.plan(view)
    limit, offset = pl.page(limit, offset)
    source = SUGGEST_VIEWS[kind]
    # A5: the rank is plain code over the rows, so it cannot be a SQL page. The rows ranked are
    # capped (SUGGEST_RANK_CAP, by id) so one request never ranks an unbounded table. Cost: one
    # SELECT of at most the cap, O(n log n) scoring in Python, then a slice. Today's views hold a
    # few hundred rows; a request that hits the cap is logged and `total` is the capped number.
    found = queries.rows(
        conn,
        "select kind, ref_id, account_id, contact_id, name, company, title, parts, evidence "
        f"from {source} order by ref_id limit :cap",
        cap=SUGGEST_RANK_CAP + 1,
    )
    capped = len(found) > SUGGEST_RANK_CAP
    if capped:
        log.warning("suggest_rank_capped", kind=kind, cap=SUGGEST_RANK_CAP)
        found = found[:SUGGEST_RANK_CAP]
    ranked = rules.rank(kind, found)
    seen = effective_seen(conn, me, kind)
    is_seen = lambda s: s.ref_id in seen  # noqa: E731
    new_n, seen_n = seen_tabs.split_counts(ranked, is_seen)
    shown = pl.pick(ranked, is_seen)
    page = shown[offset : offset + limit]
    base = meta(conn)
    hits = _hits_for(conn, page)
    # Late import: today_model imports this module. R5 T15: each card states in one line what
    # it suggests, the same checked words as Today's "Saley suggests" (R5 T5).
    from saley.web.today_model import checked_line

    items = [suggestion_item(s, seen, hits) for s in page]
    return {
        "kind": kind,
        "items": [{**item, "line": checked_line(item)} for item in items],
        "total": len(shown),
        "empty": w.fill(w.EMPTY_LIST, t=base["last_computed_label"]),
        "window": sheet_window(base),
        "left_out_seen": pl.left_out(seen_n),
        **pl.fields(new_n, seen_n),
        "capped": capped,  # silent failure 6: the page says the list is cut
        "capped_note": w.fill(w.SUGGEST_CAPPED, n=SUGGEST_RANK_CAP) if capped else None,
        **base,
    }


def sheet_window(base: dict[str, Any]) -> dict[str, Any]:
    """The window of a list that reads the sheet: the last good sync (D22)."""
    last = base["last_computed"]
    label = w.fill(w.WINDOW_SHEET, t=base["last_computed_label"]) if last else w.WINDOW_NO_SYNC
    return {"from": last, "to": last, "label": label}


def effective_seen(conn: Connection, me: str, kind: str) -> set[int]:
    """Ids of a Suggestions list this person saw and that did not change since (D75, A10).

    A company on the New companies list is unseen again when a search found a new hit after the
    person saw it: the new people are news, the earlier "seen" mark was about the old state.
    """
    if kind != "newco":
        return seen_ids(conn, me, f"suggest_{kind}")
    found = conn.execute(
        text(
            "select u.item_id from v_user_seen u where u.user_short = :me "
            "and u.item_type = 'suggest_newco' and not exists ("
            "select 1 from v_search_hit h where h.account_id = u.item_id "
            "and h.found_at > u.seen_at)"
        ),
        {"me": me},
    )
    return {int(r[0]) for r in found}


def needs_look(conn: Connection, limit: int = 20, kind: str | None = None) -> list[dict[str, Any]]:
    """Today · needs a look: linked stories rated 4 or 5, and events in 3 days or less.

    With `kind` ("signal" or "event") the limit applies after the kind filter, so one kind
    cannot starve the other (D173).
    """
    return queries.rows(
        conn,
        "select kind, ref_id, title, subject, importance, verify, at, url from v_needs_look "
        "where (cast(:k as text) is null or kind = :k) order by at desc nulls last limit :n",
        n=limit,
        k=kind,
    )


def look_rows(conn: Connection, per_kind: int = LOOK_SHOWN) -> list[dict[str, Any]]:
    """One read per kind, `per_kind` each, newest first. Today reads this once and reuses it."""
    found = needs_look(conn, per_kind, "signal") + needs_look(conn, per_kind, "event")
    dated = sorted((r for r in found if r["at"]), key=lambda r: r["at"], reverse=True)
    return dated + [r for r in found if not r["at"]]


def _ranked_suggests(conn: Connection) -> list[rules.Suggestion]:
    found = queries.rows(
        conn,
        "select kind, ref_id, account_id, contact_id, name, company, title, parts, evidence "
        "from v_saley_suggests",
    )
    ranked: list[rules.Suggestion] = []
    for kind in ("newco", "warm"):
        ranked += rules.rank(kind, [r for r in found if r["kind"] == kind])
    ranked.sort(key=lambda s: (-s.score, s.name, s.ref_id))
    return ranked


def _suggest_pick(conn: Connection, count: int, me: str | None) -> tuple[list[dict[str, Any]], int]:
    """The top `count` suggestions this person has not seen, and how many seen ones it skipped."""
    ranked = _ranked_suggests(conn)
    if me is None:
        return [suggestion_item(s, set(), {}) for s in ranked[:count]], 0
    seen = {kind: effective_seen(conn, me, kind) for kind in ("newco", "warm")}
    shown = [s for s in ranked if s.ref_id not in seen[s.kind]]
    return [suggestion_item(s, set(), {}) for s in shown[:count]], len(ranked) - len(shown)


def saley_suggests(conn: Connection, count: int = 3, me: str | None = None) -> list[dict[str, Any]]:
    """Today · Saley suggests: the top few new companies and warm people, best score first.

    With `me` the person's seen set applies (D75): what they saw is left out and counted.
    """
    return _suggest_pick(conn, count, me)[0]


def look_item(row: dict[str, Any], today: date) -> dict[str, Any]:
    """One line of Today · needs a look, with its words and the page it opens."""
    moment: datetime | None = row["at"]
    if row["kind"] == "event":
        left = (moment.astimezone(tz()).date() - today).days if moment else None
        when = days_text(left)
        city = row["subject"]
        return {
            "kind": "event",
            "id": row["ref_id"],
            "title": row["title"],
            "detail": w.fill(w.LOOK_EVENT, city=city, when=when) if city else when,
            "verify_note": None,
            "href": https_link(row["url"]),
            "at_label": when_label(moment),
            "link": {"view": "events", "filter": None},
        }
    return {
        "kind": "signal",
        "id": row["ref_id"],
        "title": row["title"],
        "detail": w.fill(w.LOOK_SIGNAL, who=row["subject"] or "—", n=row["importance"]),
        "verify_note": w.VERIFY_NOTE if row["verify"] else None,
        "href": https_link(row["url"]),
        "at_label": when_label(moment),
        "link": {"view": "signals", "filter": "linked"},
    }


def needs_look_block(
    conn: Connection,
    empty: str,
    replies_note: str | None,
    shown: int = LOOK_SHOWN,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Today · needs a look. Only `shown` lines are read; `total` is the true count (A3).

    Nothing here is hidden by "seen": a strong signal or a near event stays until its state
    changes, so `left_out_seen` is always 0 and says so.
    """
    total = conn.execute(text("select count(*) from v_needs_look")).scalar_one()
    found = rows[:shown] if rows is not None else needs_look(conn, limit=shown)
    today = today_ist()
    now = now_ist()
    return {
        "items": [look_item(r, today) for r in found],
        "total": int(total),
        "empty": empty,
        "replies_note": replies_note,
        "window": {
            "from": (now - timedelta(days=LOOK_DAYS)).isoformat(),
            "to": now.isoformat(),
            "label": w.WINDOW_LOOK,
        },
        "left_out_seen": 0,
    }


def saley_suggests_block(conn: Connection, empty: str, me: str | None = None) -> dict[str, Any]:
    items, hidden = _suggest_pick(conn, SUGGESTS_SHOWN, me)
    return {
        "items": items,
        "empty": empty,
        "window": sheet_window(meta(conn)),
        "left_out_seen": hidden,
    }


@router.get("/api/news/funnel", response_model=schemas.FunnelOut)
def news_funnel(user: User, conn: Db, day: date | None = None) -> dict[str, Any]:
    chosen = checked_day(day) or today_ist()
    block = news_health(conn, chosen)
    return {
        "day": chosen,
        "label": block["label"],
        "steps": block["steps"],
        **news_meta(conn),
    }


# ------------------------------------------------------------------ seen (D75)


@router.post("/api/seen", response_model=schemas.SeenOut)
def mark_seen(
    body: schemas.SeenIn, request: Request, user: User, _h: ActHeader, conn: ActDb
) -> dict[str, Any]:
    """The page posts the ids it showed. Idempotent: the first time a person saw it stays."""
    who = actor_short(request, user)
    result = call_function(
        conn,
        "select saley_mark_seen(:u, :t, cast(:ids as bigint[]), :q)",
        {
            "u": who,
            "t": body.item_type,
            "ids": "{" + ",".join(str(i) for i in sorted(set(body.ids))) + "}",
            "q": body.query_id,
        },
    )
    if not result.get("ok"):
        if result.get("code") == "not_allowed":
            raise HTTPException(status_code=403, detail=w.ERR_NOT_APPROVER)
        raise HTTPException(status_code=422, detail=w.ERR_BAD_ACT)
    return {"ok": True, "marked": int(result["marked"])}
