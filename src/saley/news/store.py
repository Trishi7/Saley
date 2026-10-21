"""Store step: seen check, fingerprint merge, gate, new story (docs/04 §2, D72, D73).

Why: the order is fixed. A URL in `news_seen` is skipped. A fingerprint seen in the last 36 h
adds an outlet to the first story instead of making a second one (two sources, one story).
Then the flag and the AI gate. Every item leaves a row in `news_seen`, so the next batch
knows it. Counters go to `news_source_runs` so the funnel is counted, not guessed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from saley.core.logging import get_logger
from saley.core.models import (
    NewsDropped,
    NewsSeen,
    NewsSent,
    NewsSource,
    NewsSourceRun,
    NewsStory,
    Signal,
    UserSeen,
)
from saley.news.clean import NameIndex, fingerprint, judge, url_hash
from saley.news.fetch import FeedItem
from saley.news.topic import topic_for

log = get_logger("saley.news.store")

FINGERPRINT_WINDOW = timedelta(hours=36)
SEEN_DAYS = 14
DROPPED_DAYS = 14
RUN_DAYS = 90  # as long as stories, so an old funnel day still has its runs (A6)
STORY_DAYS = 90
UNSCORED_AFTER = timedelta(hours=24)


@dataclass
class IngestCounts:
    """The per-run counters (docs/04 §7). `seen` counts every item the feed held."""

    seen: int = 0
    new: int = 0
    dropped_seen: int = 0
    dropped_gate: int = 0
    dropped_copy: int = 0

    def add(self, other: IngestCounts) -> None:
        self.seen += other.seen
        self.new += other.new
        self.dropped_seen += other.dropped_seen
        self.dropped_gate += other.dropped_gate
        self.dropped_copy += other.dropped_copy


def _recent_story(session: Session, fp: str, now: datetime) -> NewsStory | None:
    # Two editions at once must not each make a story for one headline: take a lock per
    # fingerprint until the transaction ends (B3).
    session.execute(text("select pg_advisory_xact_lock(hashtext(:fp))"), {"fp": fp})
    return session.scalars(
        select(NewsStory)
        .where(NewsStory.fingerprint == fp, NewsStory.first_seen_at >= now - FINGERPRINT_WINDOW)
        .order_by(NewsStory.id)
        .limit(1)
    ).first()


def _merge_outlet(story: NewsStory, outlet: str) -> None:
    if outlet and outlet not in story.outlets:
        story.outlets = [*story.outlets, outlet]
    story.outlet_count = max(len(story.outlets), 1)


def _remember(session: Session, key: str, fp: str, story_id: int | None, now: datetime) -> bool:
    """Write the 14-day memory row. False when another run wrote this URL first (B3)."""
    created = session.execute(
        insert(NewsSeen.__table__)
        .values(url_hash=key, fingerprint=fp, story_id=story_id, seen_at=now)
        .on_conflict_do_nothing(index_elements=["url_hash"])
        .returning(NewsSeen.__table__.c.url_hash)
    ).first()
    return created is not None


def ingest_items(
    session: Session,
    source: NewsSource,
    items: Iterable[FeedItem],
    *,
    now: datetime,
    names: NameIndex | None = None,
    edition_id: int | None = None,
) -> IngestCounts:
    """Clean and store the items of one fetch. Returns the counters of the run."""
    counts = IngestCounts()
    for item in items:
        counts.seen += 1
        key = url_hash(item.link)
        if session.get(NewsSeen, key) is not None:
            counts.dropped_seen += 1
            continue
        real_fp = fingerprint(item.title)
        # No real word in the title (A1): never merge on it. The URL hash stands in.
        fp = real_fp or f"url:{key}"
        outlet = item.outlet or source.name
        story = _recent_story(session, fp, now) if real_fp else None
        if story is not None:
            if not _remember(session, key, fp, story.id, now):
                counts.dropped_seen += 1
                continue
            _merge_outlet(story, outlet)
            counts.dropped_copy += 1
            continue
        verdict = judge(item.title, item.summary, ai_only=source.ai_only, names=names)
        if not verdict.passes:
            if not _remember(session, key, fp, None, now):
                counts.dropped_seen += 1
                continue
            session.add(
                NewsDropped(
                    title=item.title,
                    url=item.link,
                    source_id=source.id,
                    reason=verdict.reason,
                    seen_at=now,
                )
            )
            counts.dropped_gate += 1
            continue
        if not _remember(session, key, fp, None, now):
            counts.dropped_seen += 1
            continue
        story = NewsStory(
            title=item.title,
            summary=item.summary,
            url=item.link,
            url_hash=key,
            fingerprint=fp,
            kind=item.kind,
            outlets=[outlet],
            outlet_count=1,
            first_source_id=source.id,
            edition_id=edition_id,
            flag=verdict.flag,
            name_kind=verdict.name_kind,
            names=list(verdict.names),
            topic=topic_for(item.title, item.summary, item.kind),
            published_at=item.published,
            first_seen_at=now,
        )
        session.add(story)
        session.flush()
        session.execute(
            text("update news_seen set story_id = :sid where url_hash = :h"),
            {"sid": story.id, "h": key},
        )
        counts.new += 1
    session.flush()
    return counts


def cleanup(session: Session, now: datetime) -> dict[str, int]:
    """Nightly clean (03:00): the retention in docs/04 §9. Returns rows removed per table.

    `stories_unscored` and `stories_old` split the story count for the job note. A story with a
    signal a person acted on (`in_action`, `dismissed`) is kept, so that choice is not erased
    without a trace (B7). `news_seen` expires only by its own 14-day rule: it outlives its story.
    """
    removed = {
        "news_seen": session.execute(
            delete(NewsSeen).where(NewsSeen.seen_at < now - timedelta(days=SEEN_DAYS))
        ).rowcount,
        "news_dropped": session.execute(
            delete(NewsDropped).where(NewsDropped.seen_at < now - timedelta(days=DROPPED_DAYS))
        ).rowcount,
        "news_source_runs": session.execute(
            delete(NewsSourceRun).where(NewsSourceRun.started_at < now - timedelta(days=RUN_DAYS))
        ).rowcount,
    }
    acted = select(Signal.story_id).where(Signal.state != "new")
    old = NewsStory.first_seen_at < now - timedelta(days=STORY_DAYS)
    removed["stories_old"] = session.execute(
        delete(NewsStory).where(
            old, NewsStory.id.not_in(select(NewsSent.story_id)), NewsStory.id.not_in(acted)
        )
    ).rowcount
    # About three editions pass in 24 h. An unscored, unflagged story older than that is noise.
    stale = NewsStory.scored_at.is_(None) & NewsStory.flag.is_(False)
    stale &= NewsStory.first_seen_at < now - UNSCORED_AFTER
    stale &= NewsStory.id.not_in(select(NewsSent.story_id))
    stale &= NewsStory.id.not_in(acted)  # LOW4: same guard as `stories_old`
    removed["stories_unscored"] = session.execute(delete(NewsStory).where(stale)).rowcount
    if removed["stories_unscored"]:
        # Silent failure 1: unscored stories are normally scored in a few hours. Say so.
        log.warning("cleanup_deleted_unscored", count=removed["stories_unscored"])
    removed["news_stories"] = removed["stories_old"] + removed["stories_unscored"]
    removed["user_seen"] = prune_user_seen(session, now)
    return removed


def prune_user_seen(session: Session, now: datetime) -> int:
    """Remove "seen" marks whose story or signal is gone, once older than the story retention."""
    cutoff = now - timedelta(days=STORY_DAYS)
    gone_story = ~select(NewsStory.id).where(NewsStory.id == UserSeen.item_id).exists()
    gone_signal = ~select(Signal.id).where(Signal.id == UserSeen.item_id).exists()
    total = 0
    for item_type, gone in (("story", gone_story), ("signal", gone_signal)):
        total += session.execute(
            delete(UserSeen).where(UserSeen.item_type == item_type, UserSeen.seen_at < cutoff, gone)
        ).rowcount
    return total
