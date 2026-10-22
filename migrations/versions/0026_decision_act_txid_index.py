"""A partial index on `decision (act_txid)` (R5 review follow-up, R6).

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-22

Why: `saley_undo_group` (0023) finds the cells of one act by `act_txid`. The decision table only
grows, so without an index that lookup reads the whole table on every grouped Undo. Old rows
have a null `act_txid` (no group) and never match, so the index leaves them out.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_decision_act_txid"


def upgrade() -> None:
    op.execute(
        f"create index if not exists {INDEX} on decision (act_txid) where act_txid is not null"
    )


def downgrade() -> None:
    op.execute(f"drop index if exists {INDEX}")
