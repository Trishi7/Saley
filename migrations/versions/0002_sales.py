"""Sales tables from docs/01 §12.1 plus alias, metric_snapshot and data_quality.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_quality",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tab", sa.Text(), nullable=False),
        sa.Column("row_key", sa.Text(), nullable=False),
        sa.Column("column_role", sa.Text(), nullable=False),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column(
            "first_seen",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tab", "row_key", "column_role", "problem", name="uq_data_quality_cell"
        ),
    )
    op.create_table(
        "deliverable",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sheet_row_key", sa.Text(), nullable=False),
        sa.Column("sr_no", sa.Text(), nullable=True),
        sa.Column("item", sa.Text(), nullable=False),
        sa.Column("dependency", sa.Text(), nullable=True),
        sa.Column("priority", sa.Text(), nullable=True),
        sa.Column("due", sa.Date(), nullable=True),
        sa.Column("timelines", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("done", sa.Date(), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("reminder_freq", sa.Text(), nullable=True),
        sa.Column(
            "blocks_goal_ids", postgresql.ARRAY(sa.Integer()), server_default="{}", nullable=False
        ),
        sa.Column(
            "blocks_strategy_ids",
            postgresql.ARRAY(sa.Integer()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sheet_row_key"),
    )
    op.create_table(
        "event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sheet_row_key", sa.Text(), nullable=False),
        sa.Column("sr_no", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("start", sa.Date(), nullable=True),
        sa.Column("end", sa.Date(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("timings", sa.Text(), nullable=True),
        sa.Column("key_people", sa.Text(), nullable=True),
        sa.Column("reg_deadline", sa.Date(), nullable=True),
        sa.Column("registered", sa.Boolean(), nullable=True),
        sa.Column("attended", sa.Boolean(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sheet_row_key"),
    )
    op.create_table(
        "metric_snapshot",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=True),
        sa.PrimaryKeyConstraint("day", "scope_type", "scope_id", "metric"),
    )
    op.create_table(
        "package",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sheet_row_key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("use_case", sa.Text(), nullable=True),
        sa.Column("size", sa.Text(), nullable=True),
        sa.Column("pct", sa.Integer(), nullable=True),
        sa.Column("ready", sa.Boolean(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sheet_row_key"),
    )
    op.create_table(
        "strategy",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sheet_row_key", sa.Text(), nullable=False),
        sa.Column("sr_no", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("icp", sa.Text(), nullable=True),
        sa.Column("icp_per_month", sa.Integer(), nullable=True),
        sa.Column("orgs_per_month", sa.Integer(), nullable=True),
        sa.Column("collaterals", sa.Text(), nullable=True),
        sa.Column("ready", sa.Boolean(), nullable=True),
        sa.Column("pending", sa.Text(), nullable=True),
        sa.Column("timeline", sa.Text(), nullable=True),
        sa.Column("metric_text", sa.Text(), nullable=True),
        sa.Column("model_abc", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sheet_row_key"),
    )
    op.create_table(
        "goal",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sheet_row_key", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_min", sa.Integer(), nullable=True),
        sa.Column("value_max", sa.Integer(), nullable=True),
        sa.Column("metric", sa.Text(), nullable=True),
        sa.Column("target", sa.Text(), nullable=True),
        sa.Column("need", sa.Integer(), nullable=True),
        sa.Column("owner", sa.Text(), nullable=True),
        sa.Column("dependencies", sa.Text(), nullable=True),
        sa.Column("total_leads", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("due", sa.Date(), nullable=True),
        sa.Column("strategy_id", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["strategy.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sheet_row_key"),
    )
    op.create_table(
        "account",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("sheet_row_key", sa.Text(), nullable=False),
        sa.Column("in_master", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("industry", sa.Text(), nullable=True),
        sa.Column("geo", sa.Text(), nullable=True),
        sa.Column("funding", sa.Text(), nullable=True),
        sa.Column("outreach_line", sa.Text(), nullable=True),
        sa.Column("tier", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("motion_id", sa.Integer(), nullable=True),
        sa.Column("goal_id", sa.Integer(), nullable=True),
        sa.Column("apollo_org_id", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["goal_id"],
            ["goal.id"],
        ),
        sa.ForeignKeyConstraint(
            ["motion_id"],
            ["strategy.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name_key"),
        sa.UniqueConstraint("sheet_row_key"),
    )
    op.create_table(
        "alias",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.Text(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["account_id"], ["account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alias"),
    )
    op.create_table(
        "contact",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("sheet_row_key", sa.Text(), nullable=False),
        sa.Column("sr_no", sa.Text(), nullable=True),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("email_status", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("li_url", sa.Text(), nullable=True),
        sa.Column("paper_url", sa.Text(), nullable=True),
        sa.Column("x_handle", sa.Text(), nullable=True),
        sa.Column("about", sa.Text(), nullable=True),
        sa.Column("industry", sa.Text(), nullable=True),
        sa.Column("application_bucket", sa.Text(), nullable=True),
        sa.Column("priority", sa.Text(), nullable=True),
        sa.Column("role_in_deal", sa.Text(), nullable=True),
        sa.Column("connected_with_vr", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("stage", sa.Text(), server_default="Listed", nullable=False),
        sa.Column("owner", sa.Text(), nullable=True),
        sa.Column("owner_source", sa.Text(), nullable=True),
        sa.Column("motion_id", sa.Integer(), nullable=True),
        sa.Column("goal_id", sa.Integer(), nullable=True),
        sa.Column("first_contact", sa.Text(), nullable=True),
        sa.Column("first_contact_type", sa.Text(), nullable=True),
        sa.Column("first_contact_date", sa.Date(), nullable=True),
        sa.Column("li_status", sa.Text(), nullable=True),
        sa.Column("li_connected_date", sa.Date(), nullable=True),
        sa.Column("next_step", sa.Text(), nullable=True),
        sa.Column("email1_sent", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("email1_date", sa.Date(), nullable=True),
        sa.Column("email2_sent", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("email2_date", sa.Date(), nullable=True),
        sa.Column("email3_sent", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("email3_date", sa.Date(), nullable=True),
        sa.Column("li_dm_sent", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("li_dm_date", sa.Date(), nullable=True),
        sa.Column("meeting_date", sa.Date(), nullable=True),
        sa.Column("meeting_status", sa.Text(), nullable=True),
        sa.Column("reconnect_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("contacted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("connected", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("emailed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("cells", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account.id"],
        ),
        sa.ForeignKeyConstraint(
            ["goal_id"],
            ["goal.id"],
        ),
        sa.ForeignKeyConstraint(
            ["motion_id"],
            ["strategy.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sheet_row_key"),
    )
    op.create_index("ix_contact_account_id", "contact", ["account_id"], unique=False)
    op.create_table(
        "opportunity",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("goal_id", sa.Integer(), nullable=True),
        sa.Column("package_id", sa.Integer(), nullable=True),
        sa.Column("package_text", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("prob", sa.Integer(), nullable=True),
        sa.Column("size_usd", sa.Integer(), nullable=True),
        sa.Column("deal_status", sa.Text(), nullable=True),
        sa.Column("champion", sa.Text(), nullable=True),
        sa.Column("budget_owner", sa.Text(), nullable=True),
        sa.Column("risk", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["contact_id"], ["contact.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["goal_id"],
            ["goal.id"],
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["package.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contact_id"),
    )
    op.create_table(
        "stage_log",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("from_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["contact_id"], ["contact.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stage_log_contact_from", "stage_log", ["contact_id", "from_at"], unique=False
    )
    op.create_table(
        "touch",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("template_id", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), server_default="sheet", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["contact_id"], ["contact.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_touch_contact_date", "touch", ["contact_id", "date"], unique=False)
    # One touch per step cell for the sheet; other sources (mail, chat) may repeat.
    op.execute(
        "create unique index uq_touch_sheet_step on touch (contact_id, type) where source = 'sheet'"
    )
    op.create_check_constraint(
        "ck_contact_stage",
        "contact",
        "stage in ('Listed','Contacted','Connected','Engaged','Meeting','Qualified',"
        "'Parked','Won','Lost')",
    )
    # Every foreign key gets an index (house rule).
    for table, column in (
        ("contact", "goal_id"),
        ("contact", "motion_id"),
        ("account", "goal_id"),
        ("account", "motion_id"),
        ("goal", "strategy_id"),
        ("opportunity", "goal_id"),
        ("opportunity", "package_id"),
        ("alias", "account_id"),
    ):
        op.create_index(f"ix_{table}_{column}", table, [column], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_table("touch")
    op.drop_table("stage_log")
    op.drop_table("opportunity")
    op.drop_table("contact")
    op.drop_table("alias")
    op.drop_table("account")
    op.drop_table("goal")
    op.drop_table("strategy")
    op.drop_table("package")
    op.drop_table("metric_snapshot")
    op.drop_table("event")
    op.drop_table("deliverable")
    op.drop_table("data_quality")
