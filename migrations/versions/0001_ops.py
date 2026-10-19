"""Operations tables: users, llm_calls, llm_cache, job_runs, job_locks, egress_log, parity_checks.

Revision ID: 0001
Revises:
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "egress_log",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("errors", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("day", "host"),
    )
    op.create_table(
        "job_locks",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("holder", sa.Text(), nullable=False),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "job_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("job", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="running", nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "status in ('running','ok','failed','skipped')", name="ck_job_runs_status"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Latest run per job (health page) and last good run (top bar), newest first.
    op.execute("create index ix_job_runs_job_started on job_runs (job, started_at desc)")
    op.execute(
        "create index ix_job_runs_ok_finished on job_runs (job, finished_at desc) "
        "where status = 'ok'"
    )
    op.create_table(
        "llm_cache",
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("input_hash"),
    )
    op.create_index("ix_llm_cache_expires_at", "llm_cache", ["expires_at"], unique=False)
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("job", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tokens_out", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cached", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("ok", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_calls_at", "llm_calls", ["at"], unique=False)
    op.create_table(
        "parity_checks",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("sheet_value", sa.Numeric(), nullable=True),
        sa.Column("view_value", sa.Numeric(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_parity_checks_at", "parity_checks", ["at"], unique=False)
    op.create_index("ix_parity_checks_run_id", "parity_checks", ["run_id"], unique=False)
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("short", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), server_default="viewer", nullable=False),
        sa.Column("discord_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role in ('viewer','approver','admin')", name="ck_users_role"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("short"),
    )

    # Seed the team (docs/08 "Who it is for", D16, D35). No emails until Google sign-in (R4).
    users = sa.table(
        "users",
        sa.column("short", sa.Text),
        sa.column("name", sa.Text),
        sa.column("role", sa.Text),
    )
    op.bulk_insert(
        users,
        [
            {"short": "vaishnavi", "name": "Vaishnavi", "role": "approver"},
            {"short": "sid", "name": "Sid", "role": "approver"},
            {"short": "kushal", "name": "Kushal", "role": "approver"},
            {"short": "trishi", "name": "Trishi", "role": "admin"},
        ],
    )


def downgrade() -> None:
    op.drop_table("users")
    op.drop_table("parity_checks")
    op.drop_table("llm_calls")
    op.drop_table("llm_cache")
    op.drop_table("job_runs")
    op.drop_table("job_locks")
    op.drop_table("egress_log")
