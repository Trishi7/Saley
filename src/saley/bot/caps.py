"""The bot caps as one pure function (D126, D132, docs/09 "Caps").

Why: a rule that decides who is pinged and when must be easy to test with a fake clock and no
database. `plan` takes the posts that are due and says which become `ready` now, which are
`deferred` (and until when), and which are `dropped`. The worker job `bot_plan` applies it.

Rules, per person ("people" of a post = the people it @mentions, plus the recipient of a DM):
- A channel post that mentions nobody counts for nobody: no cap, no gap, no hours (D132).
- At most BOT_POSTS_PER_PERSON_PER_DAY a day (IST), DMs and @mentions together. The overflow
  moves to the next day. When more posts compete, meeting first, then reconnect, then rank.
- At least BOT_POST_GAP_MINUTES between two posts for one person. A meeting post skips the gap
  (its brief goes out under 24 h before the meeting, so it cannot wait).
- A person's posts wait for the start of their USER_HOURS window (D126).
- Weekend (Sat, Sun IST): silent, except meeting briefs and reconnect pings (D10).
- A DM is dropped while DM_ENABLED=false or when the person is not in DM_USERS (D128).
- A post older than MAX_AGE_DAYS is dropped: a ping that old would only confuse.
Answers to questions never pass through here, so they are never capped.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from saley.core import clock
from saley.core.settings import MINUTES_PER_DAY, Settings

MAX_AGE_DAYS = 3
WEEKEND_FROM = 5  # datetime.weekday(): Saturday
SEARCH_DAYS = 10  # enough to cross a weekend and an empty window


@dataclass(frozen=True)
class Post:
    """What the caps need to know about one bot_post row. `people` are users.short values."""

    id: int
    channel: str  # test | sales | dm
    people: tuple[str, ...]
    due_at: datetime
    rank: int = 0
    is_meeting: bool = False
    is_reconnect: bool = False
    not_before: datetime | None = None
    max_age: timedelta | None = None  # shorter than MAX_AGE_DAYS for a fast-stale post

    def later(self, when: datetime) -> Post:
        return replace(self, not_before=when)


@dataclass(frozen=True)
class Plan:
    ready: tuple[int, ...] = ()
    deferred: Mapping[int, datetime] = field(default_factory=dict)
    dropped: Mapping[int, str] = field(default_factory=dict)  # id -> why


def _midnight(day: datetime) -> datetime:
    return day.replace(hour=0, minute=0, second=0, microsecond=0)


def _at_minute(day: datetime, minute: int) -> datetime:
    """Midnight of `day` plus `minute` (1440 = the end of the day). IST has no DST."""
    return _midnight(day) + timedelta(minutes=minute)


def next_slot(
    candidate: datetime,
    people: Sequence[str],
    hours: Mapping[str, tuple[int, int]],
    *,
    weekend_ok: bool,
) -> datetime:
    """The first moment at or after `candidate` that every person's hours allow."""
    when = candidate
    windows = [hours[p] for p in people if p in hours]
    start = max((w[0] for w in windows), default=0)
    end = min((w[1] for w in windows), default=MINUTES_PER_DAY)
    for _ in range(SEARCH_DAYS):
        if not weekend_ok and when.weekday() >= WEEKEND_FROM:
            when = _midnight(when) + timedelta(days=1)
            continue
        if start >= end:  # two windows that never meet
            when = _midnight(when) + timedelta(days=1)
            continue
        opens, closes = _at_minute(when, start), _at_minute(when, end)
        when = max(when, opens)
        if when < closes:
            return when
        when = _midnight(when) + timedelta(days=1)
    return when


def _order(post: Post) -> tuple[int, int, int, datetime, int]:
    return (-int(post.is_meeting), -int(post.is_reconnect), -post.rank, post.due_at, post.id)


def _drop_reason(post: Post, now: datetime, settings: Settings) -> str | None:
    if now - post.due_at > (post.max_age or timedelta(days=MAX_AGE_DAYS)):
        return "too old"
    if post.channel != "dm":
        return None
    if not settings.dm_enabled:
        return "DMs off"
    if not post.people or any(p not in settings.dm_user_shorts for p in post.people):
        return "person not in DM_USERS"
    return None


def plan(
    posts: Sequence[Post],
    now: datetime,
    sent_today: Mapping[str, Sequence[datetime]],
    settings: Settings,
) -> Plan:
    """Decide the due posts.

    `sent_today` holds, per person, the send times of today and of the last gap (posts already
    marked ready count). Posts not yet due are left out of the plan.
    """
    zone = clock.tz()
    now = now.astimezone(zone)
    sends = {p: [t.astimezone(zone) for t in ts] for p, ts in sent_today.items()}
    gap = timedelta(minutes=settings.bot_post_gap_minutes)
    cap = settings.bot_posts_per_person_per_day
    hours = settings.user_hours_map
    ready: list[int] = []
    deferred: dict[int, datetime] = {}
    dropped: dict[int, str] = {}
    for post in sorted(posts, key=_order):
        if post.due_at > now or (post.not_before and post.not_before > now):
            continue
        if reason := _drop_reason(post, now, settings):
            dropped[post.id] = reason
            continue
        exempt = post.is_meeting or post.is_reconnect
        slot = next_slot(now, post.people, hours, weekend_ok=exempt)
        if slot > now:
            deferred[post.id] = slot
            continue
        counts = [sum(t.date() == now.date() for t in sends.get(p, ())) for p in post.people]
        if any(n >= cap for n in counts):  # D132: the rest goes to the next day, ranked
            tomorrow = _midnight(now) + timedelta(days=1)
            deferred[post.id] = next_slot(tomorrow, post.people, hours, weekend_ok=exempt)
            continue
        if not post.is_meeting:
            lasts = [max(sends[p]) for p in post.people if sends.get(p)]
            wait_until = max((t + gap for t in lasts), default=now)
            if wait_until > now:
                deferred[post.id] = next_slot(wait_until, post.people, hours, weekend_ok=exempt)
                continue
        ready.append(post.id)
        for person in post.people:
            sends.setdefault(person, []).append(now)
    return Plan(tuple(ready), deferred, dropped)
