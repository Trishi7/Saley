"""Profile-link search: SearXNG, then DuckDuckGo. Public search only (D77, R11, CLAUDE.md).

Why: a human clicks "Find people" for a company. This module asks a public search engine for
`site:linkedin.com/in "<company>" <role words>` and keeps only the result URL and title. Rules:
- linkedin.com is NEVER fetched. A result link is data. The DuckDuckGo `uddg` redirect is decoded
  here and not followed. The egress policy blocks linkedin.com as a second line of defence.
- search results are DATA: no part of a title or snippet is read as an instruction;
- at most one call per SEARCH_MIN_GAP_SECONDS and SEARCH_DAILY_CAP requests a day;
- a block, a 429 or a captcha is "search unavailable" (D82). One retry after 60 s, then the
  request fails and no real call is made for 1 hour. There is no retry storm;
- an empty answer is only "found nobody" when the engine really said so. If SearXNG
  failed and DuckDuckGo found nothing, or a DuckDuckGo page held no result links at all,
  the request is failed ("search unavailable"), never cached as "found nobody" (B9).
- no paid search API (CLAUDE.md).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urlsplit

import httpx
from sqlalchemy import func, select, update

from saley.core.clock import now_ist
from saley.core.db import session_scope
from saley.core.egress import EgressBlocked, host_matches, make_client, request
from saley.core.logging import get_logger
from saley.core.models import Account, SearchHit, SearchRequest
from saley.core.settings import Settings, get_settings

log = get_logger("saley.intel.search")

DDG_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = "SaleyBot/3.0 (+https://membrane.social; contact news@membrane.social)"
ROLE_WORDS = ("research", "data", "director", "head")
MAX_COMPANY = 80
MAX_HITS = 10
MAX_TITLE = 200
BLOCK_PAUSE = timedelta(hours=1)  # Q-R3-3: after a failed retry, no request for 1 hour
RETRY_SECONDS = 60  # Q-R3-3: exactly one retry, 60 s after a block, 429 or 5xx
STUCK_AFTER = timedelta(minutes=30)
UNAVAILABLE = "search unavailable"
PAUSED = "search unavailable (paused)"
CAP_ERROR = "daily search cap reached"
CACHED = "from cache"
ENGINE_SEARX = "searxng"
ENGINE_DDG = "duckduckgo"
BLOCK_STATUSES = frozenset({202, 403, 429})
# Parts of the DuckDuckGo check page. A plain word like "anomaly" can be in a real result.
BLOCK_MARKERS = ("anomaly-modal", "g-recaptcha", "captcha-form", "unusual traffic from")
# DuckDuckGo marks a page that really found nothing with an element of this class; any other
# page with no links is a page we do not understand (a new layout, a check page). The class is
# read from the HTML tree, so the words in a title or a script do not count (silent failure 9).
NO_RESULT_CLASS = "no-results"
QUIET = "idle"  # same word as the worker registry's quiet note
FIND_PEOPLE_OFF = "find people is off"  # D164: pending rows stay pending


class SearchUnavailable(RuntimeError):
    """A block, a 429, a captcha, an unreadable answer or a host the policy refuses.

    `status` is the HTTP status when there was one. The text is a fixed short phrase.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Found:
    """One result: the profile URL and the title text. Nothing else is kept."""

    url: str
    title: str


@dataclass
class SearchResult:
    query: str
    engine: str
    hits: list[Found] = field(default_factory=list)
    calls: int = 0


# ------------------------------------------------------------------ query and URLs


def build_query(company: str, role_words: tuple[str, ...] = ROLE_WORDS) -> str:
    """`site:linkedin.com/in "<company>" (research OR data ...)`. Quotes and controls are cut."""
    clean = "".join(ch for ch in company if ch.isprintable() and ch not in '"\\').strip()
    clean = " ".join(clean.split())[:MAX_COMPANY]
    roles = " OR ".join(role_words)
    return f'site:linkedin.com/in "{clean}" ({roles})'


def profile_url(url: str) -> str | None:
    """One shape for a LinkedIn profile link, or None when it is not a /in/ profile.

    Only the text of the link is read. The link is never opened.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not (
        host == "linkedin.com" or host.endswith(".linkedin.com")
    ):
        return None
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2 or segments[0] != "in":
        return None
    slug = segments[1]
    if not slug or len(slug) > 100 or not all(c.isalnum() or c in "%._-" for c in slug):
        return None
    return f"https://www.linkedin.com/in/{slug}"


def _found(url: str, title: str) -> Found | None:
    canonical = profile_url(url)
    if canonical is None:
        return None
    return Found(canonical, " ".join(title.split())[:MAX_TITLE])


def _unique(found: list[Found | None]) -> list[Found]:
    seen: set[str] = set()
    out = []
    for item in found:
        if item is not None and item.url not in seen:
            seen.add(item.url)
            out.append(item)
    return out[:MAX_HITS]


# ------------------------------------------------------------------ parsers


def parse_searx(payload: object) -> list[Found]:
    """Results from SearXNG JSON. Anything that is not a LinkedIn profile is dropped."""
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise SearchUnavailable("unreadable SearXNG answer")
    return _unique(
        [
            _found(str(r.get("url") or ""), str(r.get("title") or ""))
            for r in results
            if isinstance(r, dict)
        ]
    )


def decode_uddg(href: str) -> str | None:
    """The target of a DuckDuckGo redirect link, decoded here. The redirect is not followed."""
    if href.startswith("//"):
        href = "https:" + href
    try:
        parts = urlsplit(href)
    except ValueError:
        return None
    if host_matches(parts.hostname or "", "duckduckgo.com"):
        target = parse_qs(parts.query).get("uddg")
        return target[0] if target else None
    return href


class _ResultLinks(HTMLParser):
    """Collects `<a class="result__a" href=...>title</a>` from the DuckDuckGo html page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []
        self.no_results = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if NO_RESULT_CLASS in (values.get("class") or "").split():
            self.no_results = True
        if tag != "a":
            return
        if "result__a" in (values.get("class") or "").split() and values.get("href"):
            self._href, self._text = values["href"], []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, "".join(self._text)))
            self._href = None


def parse_ddg(page: str) -> list[Found]:
    low = page.lower()
    if any(marker in low for marker in BLOCK_MARKERS):
        raise SearchUnavailable("DuckDuckGo asked for a check")
    parser = _ResultLinks()
    parser.feed(page)
    if not parser.links and page.strip() and not parser.no_results:
        raise SearchUnavailable("DuckDuckGo page has no result links")
    found = []
    for href, title in parser.links:
        target = decode_uddg(href)
        found.append(_found(target, title) if target else None)
    return _unique(found)


# ------------------------------------------------------------------ the calls


def _get(client: httpx.Client, url: str, **kwargs: object) -> httpx.Response:
    """One call. A block is never retried here: the one retry after 60 s (D82) is made by
    `_search_with_one_retry`, so a block cannot turn into a retry storm.
    """
    try:
        response = request(
            client, "GET", url, idempotent=False, headers={"User-Agent": USER_AGENT}, **kwargs
        )
    except EgressBlocked as exc:
        raise SearchUnavailable(f"egress refused {urlsplit(url).hostname}") from exc
    except httpx.HTTPError as exc:
        raise SearchUnavailable(type(exc).__name__) from exc
    if response.status_code in BLOCK_STATUSES or response.status_code >= 400:
        raise SearchUnavailable(f"http {response.status_code}", response.status_code)
    return response


def search_searx(client: httpx.Client, base: str, query: str) -> list[Found]:
    response = _get(client, f"{base.rstrip('/')}/search", params={"q": query, "format": "json"})
    try:
        payload = response.json()
    except ValueError as exc:
        raise SearchUnavailable("SearXNG answer is not JSON") from exc
    return parse_searx(payload)


def search_ddg(client: httpx.Client, query: str) -> list[Found]:
    response = _get(client, f"{DDG_URL}?q={quote(query)}")
    return parse_ddg(response.text)


def search_people(
    client: httpx.Client,
    company: str,
    *,
    searxng_url: str = "",
    min_gap: int = 0,
    sleep: Callable[[float], None] = time.sleep,
) -> SearchResult:
    """SearXNG first when it is set, then DuckDuckGo when SearXNG fails or finds nothing.

    The pause between the two calls keeps the gap. Raises SearchUnavailable when every call
    that was made failed.
    """
    query = build_query(company)
    result = SearchResult(query, ENGINE_DDG)
    failure: SearchUnavailable | None = None
    if searxng_url.strip():
        result.engine = ENGINE_SEARX
        result.calls += 1
        try:
            result.hits = search_searx(client, searxng_url, query)
            failure = None
        except SearchUnavailable as exc:
            log.warning("searx_unavailable", reason=str(exc)[:80])
            failure = exc
        if result.hits:
            return result
        sleep(min_gap)
    result.engine = ENGINE_DDG
    result.calls += 1
    try:
        result.hits = search_ddg(client, query)
    except SearchUnavailable as exc:
        if failure is not None or not searxng_url.strip():
            raise
        # Silent failure 8: one engine said "nothing", the other did not answer. That is not
        # "found nobody": the request fails as unavailable and is never cached as done/0.
        log.warning("ddg_unavailable_after_searx_empty", reason=str(exc)[:80])
        raise SearchUnavailable(
            "DuckDuckGo failed after SearXNG found nothing", exc.status
        ) from exc
    if not result.hits and failure is not None:
        # SearXNG was down and DuckDuckGo found nothing: nobody answered "no" (B9).
        raise SearchUnavailable("SearXNG failed and DuckDuckGo found nothing", failure.status)
    return result


# ------------------------------------------------------------------ the worker job


def _day_start(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _made_a_call() -> object:
    """A finished request that made a real call (a cap or pause refusal made none)."""
    return SearchRequest.state.in_(("done", "failed")) & (
        func.coalesce(SearchRequest.error, "").not_in((CAP_ERROR, PAUSED, CACHED))
    )


def _claim(
    moment: datetime, settings_cap: int, gap: int, cache_days: int
) -> tuple[int, int, str] | str:
    """Pick the oldest pending request. Returns (id, account id, company) or a note."""
    with session_scope() as session:
        session.execute(
            update(SearchRequest)
            .where(
                SearchRequest.state == "running", SearchRequest.requested_at < moment - STUCK_AFTER
            )
            .values(state="failed", error="interrupted", finished_at=moment)
        )
        row = session.scalars(
            select(SearchRequest)
            .where(SearchRequest.state == "pending", SearchRequest.account_id.is_not(None))
            .order_by(SearchRequest.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if row is None:
            return QUIET
        account = session.get(Account, row.account_id)
        if account is None:
            row.state, row.error, row.finished_at = "failed", "company not found", moment
            return "no company"
        # Q-R3-3: the same query inside the cache window costs no request.
        fresh = session.scalar(
            select(func.count())
            .select_from(SearchRequest)
            .where(
                SearchRequest.account_id == row.account_id,
                SearchRequest.state == "done",
                SearchRequest.error.is_(None),
                SearchRequest.id != row.id,
                SearchRequest.finished_at >= moment - timedelta(days=cache_days),
            )
        )
        if fresh:
            known = session.scalar(
                select(func.count())
                .select_from(SearchHit)
                .where(SearchHit.account_id == row.account_id)
            )
            row.state, row.error, row.finished_at, row.hits = "done", CACHED, moment, known or 0
            return f"{account.name}: {CACHED}"
        today = session.scalar(
            select(func.count())
            .select_from(SearchRequest)
            .where(_made_a_call(), SearchRequest.finished_at >= _day_start(moment))
        )
        if (today or 0) >= settings_cap:
            row.state, row.error, row.finished_at = "failed", CAP_ERROR, moment
            return CAP_ERROR
        last = session.scalar(select(func.max(SearchRequest.finished_at)).where(_made_a_call()))
        blocked = session.scalar(
            select(func.max(SearchRequest.finished_at)).where(
                SearchRequest.state == "failed", SearchRequest.error == UNAVAILABLE
            )
        )
        if blocked is not None and moment - blocked < BLOCK_PAUSE:
            row.state, row.error, row.finished_at = "failed", PAUSED, moment
            return PAUSED
        if last is not None and (moment - last).total_seconds() < gap:
            return QUIET  # leave it pending: the gap is not over
        row.state = "running"
        return row.id, account.id, account.name


def run_search_requests(
    *,
    client: httpx.Client | None = None,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Run at most one pending "Find people" request. Returns the job note."""
    settings = get_settings()
    if not settings.find_people_enabled:
        return FIND_PEOPLE_OFF  # D164: off means no outbound search, also for queued requests
    moment = now or now_ist()
    claimed = _claim(
        moment,
        settings.search_daily_cap,
        settings.search_min_gap_seconds,
        settings.search_cache_days,
    )
    if isinstance(claimed, str):
        return claimed
    request_id, account_id, company = claimed
    owns = client is None
    http = client or make_client()
    try:
        result = _search_with_one_retry(http, company, settings, sleep)
    except SearchUnavailable as exc:
        _finish(
            request_id,
            now_ist() if now is None else moment,
            error=UNAVAILABLE,
            reason=str(exc)[:120],
            http_status=exc.status,
        )
        log.warning("search_unavailable", request=request_id, reason=str(exc)[:80])
        return UNAVAILABLE
    finally:
        if owns:
            http.close()
    stored = _store(request_id, account_id, result, now_ist() if now is None else moment)
    return f"{company}: {len(result.hits)} found · {stored} new · {result.engine}"


def _search_with_one_retry(
    http: httpx.Client, company: str, settings: Settings, sleep: Callable[[float], None]
) -> SearchResult:
    """Q-R3-3: one retry after 60 s on a block, 429 or 5xx. A second failure is final."""

    def once() -> SearchResult:
        return search_people(
            http,
            company,
            searxng_url=settings.searxng_url,
            min_gap=settings.search_min_gap_seconds,
            sleep=sleep,
        )

    try:
        return once()
    except SearchUnavailable as exc:
        log.warning("search_retry_in_60s", reason=str(exc)[:80])
        sleep(RETRY_SECONDS)
        return once()


def _finish(
    request_id: int,
    moment: datetime,
    *,
    error: str | None = None,
    hits: int = 0,
    reason: str | None = None,
    http_status: int | None = None,
) -> None:
    with session_scope() as session:
        row = session.get(SearchRequest, request_id)
        if row is not None:
            row.state = "failed" if error else "done"
            row.error, row.finished_at, row.hits = error, moment, hits
            row.reason, row.http_status = reason, http_status


def _store(request_id: int, account_id: int, result: SearchResult, moment: datetime) -> int:
    with session_scope() as session:
        known = set(
            session.scalars(select(SearchHit.url).where(SearchHit.account_id == account_id))
        )
        new = 0
        for hit in result.hits:
            if hit.url in known:
                continue
            session.add(
                SearchHit(
                    request_id=request_id,
                    query=result.query,
                    engine=result.engine,
                    url=hit.url,
                    title=hit.title,
                    account_id=account_id,
                    found_at=moment,
                )
            )
            new += 1
        row = session.get(SearchRequest, request_id)
        if row is not None:
            row.state, row.error, row.finished_at, row.hits = "done", None, moment, len(result.hits)
    return new
