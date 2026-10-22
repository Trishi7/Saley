"""Worker slot on job runs (D113): the gate's worker never counts as, or reaps, the dev worker.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-20

Why: `saley dev` refuses to start while a worker ran lately (D46), and a starting worker fails
the open runs and frees the locks of the one before it. Both looked at every job run in the
database. The gate (`worker:gate`) and the laptop or server worker (`worker:main`) are two
slots: each sees and cleans up only its own rows. Lock names stay global, so two slots on one
database still never run the same job at once. Existing rows are `worker:main`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("alter table job_runs add column slot text not null default 'worker:main'")
    op.execute(
        "alter table job_runs add constraint ck_job_runs_slot "
        "check (slot ~ '^worker:[a-z0-9_-]{1,30}$')"
    )
    op.execute("create index ix_job_runs_slot_started on job_runs (slot, started_at desc)")


def downgrade() -> None:
    op.execute("drop index if exists ix_job_runs_slot_started")
    op.execute("alter table job_runs drop constraint if exists ck_job_runs_slot")
    op.execute("alter table job_runs drop column if exists slot")
