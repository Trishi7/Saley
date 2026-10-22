"""The seventh story topic, Opportunity, and a fresh topic for every news story (D239).

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-22

Why (D239): the human reviewed the D177 keyword sets. Funding, Hiring and Product launch got more
words, and a new topic Opportunity (a call for proposals, a tender, a grant) is checked just
before Event. The check on `news_stories.topic` takes the new name.

The new words can move an old story to another topic, so the topic of every story that is not a
paper goes back to null. The news process job `news_topic_backfill` sets it again with the new
rules within 10 minutes (D240). Papers are always Research, so they keep theirs. The rules stay
in Python (`saley.news.topic`), never in SQL, so this migration does not copy them.

The downgrade puts the 0024 check back. Stories tagged Opportunity go back to null first, so the
old check holds; the old code then tags them again with its own rules.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TOPICS = ("Hiring", "Funding", "Product launch", "Research", "Partnership", "Event", "Other")
TOPICS = (*OLD_TOPICS[:5], "Opportunity", *OLD_TOPICS[5:])
CHECK = "ck_news_stories_topic"
PAPER_KIND = "paper"


def _check(topics: tuple[str, ...]) -> str:
    values = ", ".join(f"'{t}'" for t in topics)
    return f"alter table news_stories add constraint {CHECK} check (topic is null or topic in ({values}))"


def upgrade() -> None:
    op.execute(f"alter table news_stories drop constraint if exists {CHECK}")
    op.execute(_check(TOPICS))
    # Only rows that have a topic: an untagged row needs no new row version (DB review).
    op.execute(
        "update news_stories set topic = null "
        f"where kind is distinct from '{PAPER_KIND}' and topic is not null"
    )


def downgrade() -> None:
    op.execute("update news_stories set topic = null where topic = 'Opportunity'")
    op.execute(f"alter table news_stories drop constraint if exists {CHECK}")
    op.execute(_check(OLD_TOPICS))
