"""The source registry: load registry.yaml, seed news_sources, name lanes, back-off.

Why: clocks and lanes live in data, not in code (docs/05 §2). Seeding updates the settings of a
source and never its state, so a re-run cannot reset a back-off or an ETag. Google name lanes
are rebuilt from the sheet: 15 names per query (docs/plans/R3.md §3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from saley.core.models import Account, Contact, NewsSource
from saley.news.clean import NameIndex

REGISTRY_FILE = Path(__file__).with_name("registry.yaml")
NAMES_PER_QUERY = 15
NAME_LANE_PREFIX = "Google News · names · IN · "
GOOGLE_EDITIONS = {
    "IN": ("en-IN", "IN", "IN:en"),
    "GB": ("en-GB", "GB", "GB:en"),
    "US": ("en-US", "US", "US:en"),
}
BACKOFF_STEPS = (
    timedelta(seconds=60),
    timedelta(hours=1),
    timedelta(hours=4),
    timedelta(hours=24),
)
CONFIG_FIELDS = (
    "kind",
    "extractor",
    "lane",
    "url",
    "tier",
    "area",
    "categories",
    "trusted",
    "ai_only",
    "active",
    "class_",
    "poll_active_min",
    "poll_idle_min",
    "notes",
)
FAMILY_NEWS = "news"
FAMILY_ARXIV = "arxiv"
FAMILY_OPENALEX = "openalex"
FAMILIES = (FAMILY_NEWS, FAMILY_ARXIV, FAMILY_OPENALEX)


_URL_IN_TEXT = re.compile(r"https?://\S+")
ERROR_TEXT_CHARS = 120


def short_error(exc: BaseException) -> str:
    """`ClassName: first 120 characters` of an error. A link in the text is cut to its host,
    so a query string (a mailto, a key) never reaches the database or the dashboard."""
    text = _URL_IN_TEXT.sub(
        lambda m: "<" + (urlsplit(m.group(0)).hostname or "url") + ">", str(exc)
    )
    text = " ".join(text.split())[:ERROR_TEXT_CHARS]
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def google_news_url(query: str, edition: str = "IN") -> str:
    """`news.google.com/rss/search?q=…&hl=en-IN&gl=IN&ceid=IN:en` (docs/plans/R3.md §3)."""
    hl, gl, ceid = GOOGLE_EDITIONS[edition]
    return "https://news.google.com/rss/search?" + urlencode(
        {"q": query, "hl": hl, "gl": gl, "ceid": ceid}
    )


def family_for(extractor: str | None) -> str:
    """Which scheduled job fetches a source with this extractor (plain value, for views)."""
    return extractor if extractor in (FAMILY_ARXIV, FAMILY_OPENALEX) else FAMILY_NEWS


def family_of(source: NewsSource) -> str:
    """Which scheduled job fetches the source: news editions, the arXiv job or OpenAlex."""
    return family_for(source.extractor)


def load_registry(path: Path = REGISTRY_FILE) -> list[dict[str, Any]]:
    """The registry entries with defaults applied and Google URLs built."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults", {})
    entries = []
    for item in raw.get("sources", []):
        entry = {**defaults, **item}
        if entry.get("kind") == "google":
            entry["url"] = google_news_url(entry["query"], entry.get("edition", "IN"))
        if "url" not in entry or "name" not in entry:
            raise ValueError(f"registry entry needs a name and a url: {item!r}")
        entries.append(entry)
    return entries


def _apply(row: NewsSource, entry: dict[str, Any]) -> None:
    for name in CONFIG_FIELDS:
        key = "class" if name == "class_" else name
        if key in entry:
            setattr(row, name, entry[key])


def seed_registry(session: Session, path: Path = REGISTRY_FILE) -> tuple[int, int]:
    """Insert new sources, update the settings of existing ones. Returns (added, updated)."""
    added = updated = 0
    existing = {s.name: s for s in session.scalars(select(NewsSource))}
    for entry in load_registry(path):
        row = existing.get(entry["name"])
        if row is None:
            row = NewsSource(name=entry["name"], kind=entry["kind"], url=entry["url"])
            session.add(row)
            added += 1
        else:
            updated += 1
        _apply(row, entry)
    session.flush()
    return added, updated


def sheet_names(session: Session) -> tuple[list[str], list[str]]:
    """(company names, person names) of the live sheet rows."""
    companies = session.scalars(
        select(Account.name).where(Account.removed_at.is_(None)).order_by(Account.name)
    ).all()
    people = session.scalars(
        select(Contact.name).where(Contact.removed_at.is_(None)).order_by(Contact.name)
    ).all()
    return list(companies), list(people)


def name_index(session: Session) -> NameIndex:
    companies, people = sheet_names(session)
    return NameIndex(companies=tuple(companies), people=tuple(people))


def rebuild_name_lanes(session: Session) -> int:
    """One Google lane per 15 company names. Old lanes with no names left are switched off."""
    companies, _ = sheet_names(session)
    chunks = [companies[i : i + NAMES_PER_QUERY] for i in range(0, len(companies), NAMES_PER_QUERY)]
    existing = {
        s.name: s for s in session.scalars(select(NewsSource).where(NewsSource.lane == "names"))
    }
    wanted: set[str] = set()
    for number, chunk in enumerate(chunks, start=1):
        name = f"{NAME_LANE_PREFIX}{number}"
        wanted.add(name)
        query = " OR ".join(f'"{n}"' for n in chunk) + " when:1d"
        row = existing.get(name)
        if row is None:
            row = NewsSource(name=name, kind="google", url="")
            session.add(row)
        row.url = google_news_url(query)
        row.lane = "names"
        row.active = True
        row.class_ = "LANE"
        row.area = "pipeline"
        row.categories = ["A", "E"]
    for name, row in existing.items():
        if name not in wanted:
            row.active = False
    session.flush()
    return len(chunks)


def due_sources(session: Session, family: str, now: datetime) -> list[NewsSource]:
    """Active sources of a family that are not in back-off. An edition pulls all of them."""
    rows = session.scalars(
        select(NewsSource)
        .where(NewsSource.active.is_(True))
        .where((NewsSource.backoff_until.is_(None)) | (NewsSource.backoff_until <= now))
        .order_by(NewsSource.id)
    ).all()
    return [s for s in rows if family_of(s) == family]


def backoff_delay(fail_streak: int) -> timedelta:
    """60 s, then 1 h, 4 h, 24 h and stay there (docs/05 §5). Health, never removal."""
    index = min(max(fail_streak, 1), len(BACKOFF_STEPS)) - 1
    return BACKOFF_STEPS[index]


@dataclass(frozen=True)
class SourceOutcome:
    """What one fetch did to the source's state."""

    status: str  # ok | 304 | error
    http_status: int | None = None
    error: str | None = None
    etag: str | None = None
    last_modified: str | None = None


def record_outcome(source: NewsSource, outcome: SourceOutcome, now: datetime) -> None:
    """Update the state of the source: validators on success, back-off on failure."""
    source.last_status = outcome.status
    if outcome.status == "error":
        source.fail_streak += 1
        source.last_error = (outcome.error or "error")[:300]
        source.backoff_until = now + backoff_delay(source.fail_streak)
        return
    source.fail_streak = 0
    source.last_error = None
    source.last_ok_at = now
    source.backoff_until = None
    if outcome.status == "ok":
        source.etag = outcome.etag
        source.last_modified = outcome.last_modified
