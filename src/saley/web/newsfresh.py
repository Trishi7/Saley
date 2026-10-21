"""Sources and health, news side: funnel, scoring, stale sources, freshness per family (A1).

Why: part 1 of the news fixes counts these in Python on a worker session. The web role reads
views only, so this module reads `v_news_scorer`, `v_news_run_summary` and `v_news_family`, then
runs the SAME rules (`saley.news.health`, `saley.news.scorer.scorer_block`) on those rows. The
top bar uses the freshness of each family (news editions, arXiv, OpenAlex), never "any news job":
a busy edition must not hide a frozen arXiv job.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import Connection, text

from saley.core.clock import day_label, hhmm, now_ist, today_ist
from saley.core.logging import scrub_text
from saley.core.settings import get_settings
from saley.intel import search
from saley.news import health as news_health_rules
from saley.news.scorer import scorer_block
from saley.news.sources import FAMILIES, FAMILY_NEWS
from saley.web import queries
from saley.web import words as w

FAMILY_NAMES = {
    "news": w.NEWS_FAMILY_NEWS,
    "arxiv": w.NEWS_FAMILY_ARXIV,
    "openalex": w.NEWS_FAMILY_OPENALEX,
}
PERCENT = 100
FLAG_TEXT = {
    news_health_rules.NO_NEW: w.NEWS_STALE_NO_NEW,
    news_health_rules.NOT_MODIFIED: w.NEWS_STALE_304,
}


def families(conn: Connection, now: datetime | None = None) -> list[dict[str, Any]]:
    """One entry per family, in a fixed order, with its own `stale` flag and a line to show."""
    now = now or now_ist()
    rows = queries.rows(
        conn,
        "select family, last_edition_at, last_edition_status, last_good_at, last_story_at "
        "from v_news_family",
    )
    found = news_health_rules.family_from_rows(rows, now)
    out = []
    for family in FAMILIES:
        item = found[family]
        name = FAMILY_NAMES[family]
        good = item["last_good_at"]
        out.append(
            {
                "family": family,
                "name": name,
                "stale": item["stale"],
                "last_good_at": good,
                "last_story_at": item["last_story_at"],
                "last_edition_status": item["last_edition_status"],
                "line": (
                    w.fill(w.NEWS_FAMILY_NEVER, name=name)
                    if good is None
                    else w.fill(w.NEWS_FAMILY_OLD, name=name, t=hhmm(good))
                )
                if item["stale"]
                else None,
            }
        )
    return out


def scorer(conn: Connection) -> dict[str, Any]:
    """Scoring in the last 24 h: the share done by the `rules` backend, and a paused LLM."""
    row = (
        conn.execute(
            text(
                "select scored_24h, rules_24h, rescore_due, last_backend, llm_ok_today "
                "from v_news_scorer"
            )
        )
        .mappings()
        .one()
    )
    cap = get_settings().llm_daily_call_cap
    block = scorer_block(
        int(row["scored_24h"]),
        int(row["rules_24h"]),
        row["last_backend"],
        int(row["rescore_due"]),
        int(row["llm_ok_today"]) >= cap,
    )
    share = block["rules_share_24h"]
    percent = None if share is None else round(share * PERCENT)
    block["rules_percent"] = percent
    block["line"] = (
        w.NEWS_SCORER_NONE if percent is None else w.fill(w.NEWS_SCORER_RULES, p=percent)
    )
    block["paused_text"] = w.NEWS_LLM_PAUSED if block["paused"] else None
    block["rescore_line"] = (
        w.fill(w.NEWS_RESCORE, n=block["rescore_due"]) if block["rescore_due"] else None
    )
    return block


def stale_sources(conn: Connection, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or now_ist()
    rows = queries.rows(
        conn,
        "select source_id, name, extractor, first_run, last_new, last_ok, last_304 "
        "from v_news_run_summary",
    )
    out = []
    for item in news_health_rules.stale_from_rows(rows, now):
        out.append(
            {
                "name": item.name,
                "family": item.family,
                "flags": list(item.flags),
                "quiet_days": item.quiet_days,
                "lines": [
                    w.fill(FLAG_TEXT[flag], name=item.name, n=item.quiet_days)
                    for flag in item.flags
                ],
            }
        )
    return out


def watch_lines(
    scored: dict[str, Any], stale: list[dict[str, Any]], fams: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """The short list the page shows under the failed sources: what needs a look, in words."""
    lines = [{"kind": "scorer", "text": scored["line"]}]
    if scored["paused_text"]:
        lines.append({"kind": "paused", "text": scored["paused_text"]})
    if scored["rescore_line"]:
        lines.append({"kind": "rescore", "text": scored["rescore_line"]})
    lines += [{"kind": "family", "text": f["line"]} for f in fams if f["line"]]
    for source in stale:
        lines += [{"kind": "stale", "text": line} for line in source["lines"]]
    return lines


def top_bar(conn: Connection, now: datetime | None = None) -> tuple[str, list[str]]:
    """The 'news' part of the top bar, and the names of the families that are old.

    The time is the last good news edition. Each old family adds its own '<name> old', so a
    frozen arXiv job shows even when the editions are fresh (A1). A family that never ran is a
    dash, not "old": the Sources page says "no good run yet" for it.
    """
    fams = families(conn, now)
    main = next(f for f in fams if f["family"] == FAMILY_NEWS)
    last = hhmm(main["last_good_at"]) if main["last_good_at"] else "—"
    parts = [w.fill(w.BAR_NEWS_OLD, t=last) if main["stale"] and main["last_good_at"] else last]
    aged = [f for f in fams if f["stale"] and f["last_good_at"]]
    parts += [w.fill(w.BAR_FAMILY_OLD, name=f["name"]) for f in aged if f is not main]
    return " · ".join(parts), [f["name"] for f in aged]


def last_good(conn: Connection) -> datetime | None:
    """When any family last ran well: the 'last computed' time of the news lists."""
    return conn.execute(text("select max(last_good_at) from v_news_family")).scalar()


# ------------------------------------------------------------------ funnel and health

REASON_SHOWN = 80


def funnel_numbers(conn: Connection, day: date) -> dict[str, int]:
    row = (
        conn.execute(text("select * from v_news_funnel_day where day = :d"), {"d": day})
        .mappings()
        .first()
    )
    keys = [
        "tried",
        "answered",
        "fetched",
        "dropped_seen",
        "dropped_gate",
        "dropped_copy",
        "deduped",
        "scored",
        "scored_4",
        "linked",
        "sent",
    ]
    return {k: int(row[k]) if row else 0 for k in keys}


def _signals_href(day: date, **extra: str) -> str:
    query = {"tab": "all", "day": day.isoformat(), "include_seen": "true", **extra}
    return "#signals?" + urlencode(query)


def _sources_href(key: str, day: date) -> str:
    """The Sources and health page, per-source counts of one funnel step."""
    return "#sources?" + urlencode({"news": key, "day": day.isoformat()})


def funnel_steps(numbers: dict[str, int], day: date) -> list[dict[str, Any]]:
    """The funnel in docs/08 view 14. A number with a list opens that list (D73)."""
    specs: list[tuple[str, str, str]] = [
        ("tried", w.NEWS_TRIED, _sources_href("tried", day)),
        ("answered", w.NEWS_ANSWERED, _sources_href("answered", day)),
        ("fetched", w.NEWS_FETCHED, _sources_href("fetched", day)),
        ("dropped_seen", w.NEWS_DROP_SEEN, _sources_href("dropped_seen", day)),
        ("dropped_copy", w.NEWS_DROP_COPY, _sources_href("dropped_copy", day)),
        ("dropped_gate", w.NEWS_DROP_GATE, _sources_href("dropped_gate", day)),
        ("deduped", w.NEWS_DEDUPED, _sources_href("deduped", day)),
        ("scored", w.NEWS_SCORED, _signals_href(day)),
        ("scored_4", w.NEWS_SCORED_4, _signals_href(day, min_importance="4")),
        ("linked", w.NEWS_LINKED, _signals_href(day, linked_only="true")),
    ]
    steps = [
        {"key": key, "step": label, "n": numbers[key], "href": href, "note": None}
        for key, label, href in specs
    ]
    steps.append(
        {
            "key": "sent",
            "step": w.NEWS_SENT,
            "n": None,
            "href": _sources_href("sent", day),
            "note": w.NEWS_SENT_NOTE,
        }
    )
    return steps


def source_row(row: dict[str, Any]) -> dict[str, Any]:
    status = row["last_status"]
    problem = None
    if status == "error" or row["fail_streak"]:
        problem = scrub_text(row["last_error"] or "")[:120] or w.fill(
            w.SOURCE_PROBLEM, status=status or "—"
        )
    return {
        "name": row["name"],
        "kind": row["kind"],
        "lane": row["lane"],
        "active": row["active"],
        "last_ok_at": row["last_ok_at"],
        "last_status": status,
        "problem": problem,
        "fail_streak": row["fail_streak"],
        "runs_24h": row["runs_24h"],
        "seen_24h": row["seen_24h"],
        "new_24h": row["new_24h"],
        "errors_24h": row["errors_24h"],
    }


def news_health(conn: Connection, day: date | None = None) -> dict[str, Any]:
    """The `news` block of /api/health/full: funnel, failed sources and per-source counts."""
    day = day or today_ist()
    rows = queries.rows(
        conn,
        "select name, kind, lane, active, last_ok_at, last_status, last_error, fail_streak, "
        "runs_24h, seen_24h, new_24h, errors_24h from v_news_source_health "
        "order by (fail_streak > 0) desc, name",
    )
    sources = [source_row(r) for r in rows]
    scored, old, fams = scorer(conn), stale_sources(conn), families(conn)
    return {
        "day": day,
        "label": w.fill(w.NEWS_DAY_LABEL, day=day_label(day)),
        "steps": funnel_steps(funnel_numbers(conn, day), day),
        "failed_sources": [s for s in sources if s["problem"] and s["active"]],
        "sources": sources,
        "scorer": scored,
        "stale_sources": old,
        "families": fams,
        "watch": watch_lines(scored, old, fams),
    }


def failure_line(failure: Any) -> str | None:
    """Why the last search failed: the fixed reason text the worker stored, and the HTTP status.

    The cap and the pause are not failures of the engine; they show as `last_error` only.
    """
    reason = scrub_text(failure["reason"] or "")[:REASON_SHOWN] if failure else ""
    if not reason:
        return None
    if failure["http_status"]:
        return w.fill(w.SEARCH_FAIL_STATUS, reason=reason, status=failure["http_status"])
    return w.fill(w.SEARCH_FAIL, reason=reason)


def search_health(conn: Connection) -> dict[str, Any]:
    settings = get_settings()
    configured = bool(settings.searxng_url.strip())
    counted = conn.execute(
        text(
            "select count(*) from v_search_request where state in ('done','failed') "
            "and coalesce(error, '') not in (:cap, :paused, :cached) and finished_at >= :start"
        ),
        {
            "start": now_ist().replace(hour=0, minute=0, second=0, microsecond=0),
            # A request answered from the cache made no call: it is not counted (silent 7).
            "cap": search.CAP_ERROR,
            "paused": search.PAUSED,
            "cached": search.CACHED,
        },
    ).scalar_one()
    failure = (
        conn.execute(
            text(
                "select error, reason, http_status from v_search_request "
                "where error is not null and error <> :cached order by id desc limit 1"
            ),
            {"cached": search.CACHED},
        )
        .mappings()
        .first()
    )
    last_error = failure["error"] if failure else None
    blocked = conn.execute(
        text(
            "select max(finished_at) from v_search_request "
            "where state = 'failed' and error = 'search unavailable'"
        )
    ).scalar()
    until = blocked + search.BLOCK_PAUSE if blocked else None
    paused = until is not None and until > now_ist()
    return {
        "unavailable_until": hhmm(until) if paused else None,
        "searxng": "configured" if configured else "not configured",
        "message": w.SEARXNG_SET if configured else w.SEARXNG_NOT_SET,
        "engine": "SearXNG" if configured else "DuckDuckGo",
        "requests_today": int(counted),
        "daily_cap": settings.search_daily_cap,
        "last_error": last_error,
        "last_failure": failure_line(failure),
        "last_http_status": failure["http_status"] if failure else None,
    }


APOLLO_PIPELINE_BUILT = False  # D78: the Apollo pipeline (docs/02 Part E) is not built in R3


def apollo_health() -> dict[str, str]:
    """D78: Apollo runs only with the flag, a cap above 0 and the pipeline. R3 has no pipeline."""
    settings = get_settings()
    on = (
        settings.apollo_enabled and settings.apollo_monthly_credit_cap > 0 and APOLLO_PIPELINE_BUILT
    )
    return {"state": "on" if on else "off", "message": w.APOLLO_OFF}
