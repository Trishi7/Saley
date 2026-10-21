"""Fetch one source through the egress client and turn the answer into feed items.

Why: every outbound call goes through `saley.core.egress` (allowlist, per-host rate limit,
timeout, retries). A fetch sends the honest User-Agent (docs/05 §5) and, on a feed, a
conditional GET so a 304 costs nothing. Only title, summary, link and outlet are kept. Google
article links are stored and never fetched. This module never raises: a failure is an outcome
the registry turns into a back-off.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import feedparser
import httpx

from saley.core.clock import today_ist
from saley.core.egress import BodyTooLarge, EgressBlocked, request_capped
from saley.core.logging import get_logger
from saley.core.models import NewsSource
from saley.news.clean import MAX_SUMMARY, clean_title, strip_html
from saley.news.sources import SourceOutcome, short_error

log = get_logger("saley.news.fetch")

MAX_BODY_BYTES = 1_500_000  # docs/05 §5: body cap 1.5 MB
OPENALEX_WINDOW_DAYS = 14
OPENALEX_AUTHORS = 5
OPENALEX_POC_PER_PAGE = 5
# arXiv keeps about 30 of 500 a day (docs/05 §3.6): only papers that name one of these.
PAPER_KEYWORDS = (
    "speech",
    "voice",
    "spoken",
    "audio",
    "evaluation",
    "benchmark",
    "annotation",
    "human feedback",
    "preference",
    "rlhf",
    "red team",
    "reward model",
    "llm-as-a-judge",
    "llm judge",
    "multilingual",
    "low-resource",
    "indic",
    "hindi",
    "synthetic data",
    "data quality",
)


@dataclass(frozen=True)
class FeedItem:
    """One item as the clean step needs it."""

    title: str
    link: str
    summary: str = ""
    outlet: str = ""
    published: datetime | None = None
    kind: str = "news"


@dataclass
class FetchResult:
    """Items plus the outcome that updates the source. `filtered` items never reach the store."""

    outcome: SourceOutcome
    items: list[FeedItem] = field(default_factory=list)
    filtered: int = 0
    bytes: int = 0


def _failure(message: str, status: int | None = None) -> FetchResult:
    return FetchResult(SourceOutcome("error", http_status=status, error=message))


def _entry_time(entry: Any) -> datetime | None:
    stamp = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(stamp), UTC) if stamp else None


def _headers(source: NewsSource, user_agent: str, conditional: bool) -> dict[str, str]:
    headers = {"User-Agent": user_agent, "Accept": "application/rss+xml, application/atom+xml, */*"}
    if conditional and source.etag:
        headers["If-None-Match"] = source.etag
    if conditional and source.last_modified:
        headers["If-Modified-Since"] = source.last_modified
    return headers


def _get(
    client: httpx.Client,
    source: NewsSource,
    url: str,
    *,
    user_agent: str,
    conditional: bool = False,
    params: dict[str, Any] | None = None,
) -> httpx.Response | FetchResult:
    """One GET. A response that is not a usable answer comes back as a failed FetchResult."""
    # httpx replaces the query string when `params` is given: merge the two ourselves.
    base = httpx.URL(url)
    merged = base.params.merge(params) if params else base.params
    try:
        response = request_capped(
            client,
            "GET",
            str(base.copy_with(query=None)),
            max_bytes=MAX_BODY_BYTES,
            headers=_headers(source, user_agent, conditional),
            params=merged,
        )
    except BodyTooLarge:
        return _failure("body too large")
    except EgressBlocked as exc:
        return _failure(f"egress blocked: {exc}")
    except httpx.HTTPError as exc:
        return _failure(short_error(exc))
    if response.status_code == 304:
        return FetchResult(SourceOutcome("304", http_status=304))
    if response.status_code >= 400:
        return _failure(f"http {response.status_code}", response.status_code)
    return response


def _ok(response: httpx.Response, items: list[FeedItem], filtered: int = 0) -> FetchResult:
    outcome = SourceOutcome(
        "ok",
        http_status=response.status_code,
        etag=response.headers.get("etag"),
        last_modified=response.headers.get("last-modified"),
    )
    return FetchResult(outcome, items, filtered, len(response.content))


def parse_feed(content: bytes, *, default_outlet: str, kind: str = "news") -> list[FeedItem]:
    """RSS or Atom bytes to items. Raises ValueError when the text is not a feed at all."""
    parsed = feedparser.parse(content)
    if not parsed.entries and (parsed.bozo or not parsed.version):
        raise ValueError("not a feed")
    items = []
    for entry in parsed.entries:
        link = str(entry.get("link") or "").strip()
        outlet = strip_html((entry.get("source") or {}).get("title", "")) or default_outlet
        title = clean_title(str(entry.get("title") or ""), outlet)
        if not title or not link:
            continue
        summary = strip_html(str(entry.get("summary") or ""))
        if summary.lower().startswith(title.lower()[:40]):  # Google repeats the headline
            summary = ""
        items.append(FeedItem(title, link, summary[:MAX_SUMMARY], outlet, _entry_time(entry), kind))
    return items


def fetch_feed(
    client: httpx.Client, source: NewsSource, *, user_agent: str, limit: int, conditional: bool
) -> FetchResult:
    """Google News lanes and plain RSS / Atom feeds."""
    response = _get(client, source, source.url, user_agent=user_agent, conditional=conditional)
    if isinstance(response, FetchResult):
        return response
    try:
        items = parse_feed(response.content, default_outlet=source.name)
    except ValueError as exc:
        return _failure(str(exc), response.status_code)
    return _ok(response, items[:limit])


def matches_keywords(item: FeedItem, keywords: Sequence[str] = PAPER_KEYWORDS) -> bool:
    text = f"{item.title} {item.summary}".lower()
    return any(k in text for k in keywords)


def fetch_arxiv(
    client: httpx.Client, source: NewsSource, *, user_agent: str, limit: int, conditional: bool
) -> FetchResult:
    """The arXiv API, newest first. The keyword filter runs before anything is stored."""
    response = _get(
        client,
        source,
        source.url,
        user_agent=user_agent,
        conditional=conditional,
        params={"max_results": limit},
    )
    if isinstance(response, FetchResult):
        return response
    try:
        items = parse_feed(response.content, default_outlet="arXiv", kind="paper")
    except ValueError as exc:
        return _failure(str(exc), response.status_code)
    kept = [i for i in items if matches_keywords(i)][:limit]
    return _ok(response, kept, filtered=len(items) - len(kept))


def abstract_text(inverted: dict[str, list[int]] | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}. Put the words back in order."""
    if not inverted:
        return ""
    slots = {pos: word for word, positions in inverted.items() for pos in positions}
    return " ".join(slots[i] for i in sorted(slots))


def work_to_item(work: dict[str, Any]) -> FeedItem | None:
    """One OpenAlex work. Authors and their affiliations go into the summary for linking."""
    title = clean_title(str(work.get("display_name") or work.get("title") or ""))
    link = str(work.get("doi") or work.get("id") or "")
    if not title or not link:
        return None
    authors = []
    for authorship in (work.get("authorships") or [])[:OPENALEX_AUTHORS]:
        name = (authorship.get("author") or {}).get("display_name")
        insts = [i.get("display_name") for i in authorship.get("institutions") or []]
        if name:
            authors.append(f"{name} ({', '.join(i for i in insts if i)})" if insts else name)
    abstract = strip_html(abstract_text(work.get("abstract_inverted_index")))
    summary = abstract[: MAX_SUMMARY - 300]
    if authors:
        summary = f"{summary}\nAuthors: {'; '.join(authors)}".strip()
    published = None
    if work.get("publication_date"):
        try:
            published = datetime.fromisoformat(str(work["publication_date"])).replace(tzinfo=UTC)
        except ValueError:
            published = None
    return FeedItem(title, link, summary, "OpenAlex", published, "paper")


def _openalex_since() -> str:
    """The window starts from the IST day, like the editions, not from the UTC day (B11)."""
    return (today_ist() - timedelta(days=OPENALEX_WINDOW_DAYS)).isoformat()


def _openalex_params(limit: int, mailto: str, extra: dict[str, Any]) -> dict[str, Any]:
    since = _openalex_since()
    params: dict[str, Any] = {"per-page": limit, "filter": f"from_publication_date:{since}"}
    params.update(extra)
    if mailto:
        params["mailto"] = mailto
    return params


def _openalex_get(
    client: httpx.Client, source: NewsSource, user_agent: str, params: dict[str, Any]
) -> tuple[list[FeedItem], FetchResult | httpx.Response]:
    response = _get(client, source, source.url, user_agent=user_agent, params=params)
    if isinstance(response, FetchResult):
        return [], response
    try:
        results = response.json().get("results", [])
    except ValueError:
        return [], _failure("bad json", response.status_code)
    items = [i for i in (work_to_item(w) for w in results) if i is not None]
    return items, response


def fetch_openalex(
    client: httpx.Client,
    source: NewsSource,
    *,
    user_agent: str,
    limit: int,
    mailto: str = "",
    poc_names: Sequence[str] = (),
    **_: Any,
) -> FetchResult:
    """OpenAlex works: one generic query, or (lane `pocs`) one request per Academia PoC name."""
    if source.lane != "pocs":
        params = _openalex_params(limit, mailto, {})
        items, response = _openalex_get(client, source, user_agent, params)
        return response if isinstance(response, FetchResult) else _ok(response, items[:limit])
    gathered: list[FeedItem] = []
    last: httpx.Response | None = None
    for name in poc_names:
        filter_ = f"raw_author_name.search:{name},from_publication_date:{_openalex_since()}"
        params = _openalex_params(OPENALEX_POC_PER_PAGE, mailto, {"filter": filter_})
        items, response = _openalex_get(client, source, user_agent, params)
        if isinstance(response, FetchResult):
            return response  # first failure ends the lane; the back-off protects the host
        gathered.extend(items)
        last = response
    if last is None:
        return FetchResult(SourceOutcome("ok", http_status=200))
    return _ok(last, gathered[:limit])


def fetch_source(
    client: httpx.Client,
    source: NewsSource,
    *,
    user_agent: str,
    limit: int,
    conditional: bool = True,
    mailto: str = "",
    poc_names: Sequence[str] = (),
) -> FetchResult:
    """Dispatch on the extractor. Never raises."""
    try:
        if source.extractor == "arxiv":
            return fetch_arxiv(
                client, source, user_agent=user_agent, limit=limit, conditional=conditional
            )
        if source.extractor == "openalex":
            return fetch_openalex(
                client,
                source,
                user_agent=user_agent,
                limit=limit,
                mailto=mailto,
                poc_names=poc_names,
            )
        return fetch_feed(
            client, source, user_agent=user_agent, limit=limit, conditional=conditional
        )
    except Exception as exc:  # a parser bug must back the source off, not stop the edition
        log.exception("news_fetch_failed", source=source.name)
        return _failure(short_error(exc))
