"""News health data for the Sources and health page (B8). Plain code, no LLM, read-only.

Why: a source that answers "ok" and brings nothing for days, or that answers "304 not modified"
forever, is broken in a way `fail_streak` never shows. This module counts it from the run rows
and gives the page the numbers; the page only shows them. The scorer summary lives in
`saley.news.scorer.scorer_summary`.

- `stale_sources`: per source, "no new items for N days" (NEWS_STALE_DAYS) and "304 forever";
- `family_freshness`: when the editions, arXiv and OpenAlex last ran and last stored a story.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from saley.core.models import NewsEdition, NewsSource, NewsStory
from saley.core.settings import get_settings
from saley.news.sources import FAMILIES, FAMILY_NEWS, family_for

NO_NEW = "no_new_items"
NOT_MODIFIED = "not_modified_forever"

_RUN_SUMMARY = text(
    "select r.source_id, min(r.started_at) as first_run, "
    "max(r.started_at) filter (where r.items_new > 0) as last_new, "
    "max(r.started_at) filter (where r.status = 'ok') as last_ok, "
    "max(r.started_at) filter (where r.status = '304') as last_304 "
    "from news_source_runs r join news_sources n on n.id = r.source_id "
    "where n.active group by r.source_id"
)


@dataclass(frozen=True)
class StaleSource:
    source_id: int
    name: str
    family: str
    flags: tuple[str, ...]
    quiet_days: int  # whole days since the last new item (or since the first run)


def stale_sources(
    session: Session, now: datetime, stale_days: int | None = None
) -> list[StaleSource]:
    """Active sources that were polled for `stale_days` or more and brought nothing new.

    A source younger than the window is never flagged. `not_modified_forever` means every
    answer in the window was a 304: the feed may be frozen, or our validator may be stuck.
    """
    rows = session.execute(_RUN_SUMMARY).mappings().all()
    sources = {s.id: s for s in session.scalars(select(NewsSource).where(NewsSource.active))}
    named = [
        {
            **row,
            "name": sources[row["source_id"]].name,
            "extractor": sources[row["source_id"]].extractor,
        }
        for row in rows
        if row["source_id"] in sources
    ]
    return stale_from_rows(named, now, stale_days)


def stale_from_rows(
    rows: Iterable[Mapping[str, Any]], now: datetime, stale_days: int | None = None
) -> list[StaleSource]:
    """The same rule on plain rows (`v_news_run_summary`), for the web role: it reads views only."""
    days = stale_days or get_settings().news_stale_days
    cutoff = now - timedelta(days=days)
    found: list[StaleSource] = []
    for row in rows:
        if row["first_run"] > cutoff:
            continue
        flags = []
        if row["last_new"] is None or row["last_new"] < cutoff:
            flags.append(NO_NEW)
        quiet_since = row["last_new"] or row["first_run"]
        no_ok = row["last_ok"] is None or row["last_ok"] < cutoff
        if no_ok and row["last_304"] is not None and row["last_304"] >= cutoff:
            flags.append(NOT_MODIFIED)
        if flags:
            found.append(
                StaleSource(
                    row["source_id"],
                    row["name"],
                    family_for(row["extractor"]),
                    tuple(flags),
                    (now - quiet_since).days,
                )
            )
    return sorted(found, key=lambda s: (-s.quiet_days, s.name))


# How old the last good run of a family may be before the top bar says "old". The editions run
# at most 8.5 h apart; arXiv and OpenAlex run once a day, so a missed day is the first signal.
FAMILY_FRESH_HOURS = {FAMILY_NEWS: 10, "arxiv": 36, "openalex": 36}
GOOD_STATUSES = ("ok", "partial")


def family_from_rows(rows: Iterable[Mapping[str, Any]], now: datetime) -> dict[str, dict[str, Any]]:
    """Per family: last edition, last story and whether the last GOOD run is too old.

    Rows come from `v_news_family` (web role) or from the tables (`family_freshness`). A family
    that never ran a good edition is stale. Each family has its own limit, so a frozen arXiv job
    is not hidden behind the busy news editions (A1).
    """
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        family = row["family"]
        good = row["last_good_at"]
        story = row["last_story_at"]
        limit = timedelta(hours=FAMILY_FRESH_HOURS.get(family, FAMILY_FRESH_HOURS[FAMILY_NEWS]))
        out[family] = {
            "last_edition_at": row["last_edition_at"],
            "last_edition_status": row["last_edition_status"],
            "last_good_at": good,
            "last_story_at": story,
            "hours_since_story": round((now - story).total_seconds() / 3600, 1) if story else None,
            "stale": good is None or now - good > limit,
        }
    return out


def family_freshness(session: Session, now: datetime) -> dict[str, dict[str, Any]]:
    """Per family (news editions, arxiv, openalex): last edition and last new story.

    `last_story_at` is when a story of that family was last stored (None if never), and
    `hours_since_story` the gap in hours. The three families age differently, so one
    "last news" line would hide a frozen arXiv or OpenAlex job behind the busy editions.
    """
    rows: list[dict[str, Any]] = []
    for family in FAMILIES:
        edition = session.execute(
            select(NewsEdition.finished_at, NewsEdition.status)
            .where(NewsEdition.family == family, NewsEdition.finished_at.is_not(None))
            .order_by(NewsEdition.id.desc())
            .limit(1)
        ).first()
        good = session.scalar(
            select(func.max(NewsEdition.finished_at)).where(
                NewsEdition.family == family, NewsEdition.status.in_(GOOD_STATUSES)
            )
        )
        in_family = (
            NewsSource.extractor.is_(None) | NewsSource.extractor.not_in(("arxiv", "openalex"))
            if family == FAMILY_NEWS
            else NewsSource.extractor == family
        )
        last_story = session.scalar(
            select(func.max(NewsStory.first_seen_at))
            .join(NewsSource, NewsSource.id == NewsStory.first_source_id)
            .where(in_family)
        )
        rows.append(
            {
                "family": family,
                "last_edition_at": edition[0] if edition else None,
                "last_edition_status": edition[1] if edition else None,
                "last_good_at": good,
                "last_story_at": last_story,
            }
        )
    return family_from_rows(rows, now)
