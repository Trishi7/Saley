"""One bot_post per brief, health line or pack shape (R4 ticket 5 review).

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-20

Why: the brief and the health line checked "already queued today" with a select and then an
insert. Two workers could both pass the select. A partial unique index makes the second insert
a no-op (`on conflict do nothing`). `shape` is in the key because the weekly pack queues two
rows (two shapes) with the same item_id. `coalesce(for_user, 0)` lets a post for nobody (health
line, pack) take part in the key.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "create unique index if not exists uq_bot_post_once on bot_post "
        "(item_type, item_id, shape, coalesce(for_user, 0)) "
        "where item_type in ('daily_brief', 'health_line', 'weekly_pack')"
    )


def downgrade() -> None:
    op.execute("drop index if exists uq_bot_post_once")
