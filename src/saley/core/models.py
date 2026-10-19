"""Typed SQLAlchemy models. Names follow docs/01 §12; extra fields are noted where they appear.

Why: docs/01 wins on names and tables. Migrations (not `create_all`) create the schema; these
models are the Python view of it and must match migrations 0001 and 0002.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from saley.core.db import Base

TZ = DateTime(timezone=True)


def _now() -> Any:
    return mapped_column(TZ, server_default=func.now(), nullable=False)


# ------------------------------------------------------------------ operations (0001)


class User(Base):
    """People who use Saley. Email stays empty until Google sign-in (R4) sets it."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role in ('viewer','approver','admin')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    short: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text, unique=True)
    role: Mapped[str] = mapped_column(Text, server_default="viewer")
    discord_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class LlmCall(Base):
    """One row per LLM call or cache hit. Feeds the daily cap (S41) and the health view."""

    __tablename__ = "llm_calls"
    __table_args__ = (Index("ix_llm_calls_at", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, server_default="0")
    cached: Mapped[bool] = mapped_column(Boolean, server_default="false")
    ok: Mapped[bool] = mapped_column(Boolean, server_default="true")
    error: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = _now()


class LlmCache(Base):
    """Parsed LLM output by input hash, kept LLM_CACHE_DAYS (docs/01 §10)."""

    __tablename__ = "llm_cache"
    __table_args__ = (Index("ix_llm_cache_expires_at", "expires_at"),)

    input_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    model: Mapped[str] = mapped_column(Text)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(TZ)


class JobRun(Base):
    """One row per job run. The top bar and the health view read it."""

    __tablename__ = "job_runs"
    __table_args__ = (
        CheckConstraint("status in ('running','ok','failed','skipped')", name="ck_job_runs_status"),
        Index("ix_job_runs_job_started", "job", text("started_at desc")),
        Index(
            "ix_job_runs_ok_finished",
            "job",
            text("finished_at desc"),
            postgresql_where=text("status = 'ok'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="running")
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(TZ)
    note: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class JobLock(Base):
    """A DB lock per job name. Expires after 15 min so a dead worker never blocks a job."""

    __tablename__ = "job_locks"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    holder: Mapped[str] = mapped_column(Text)
    acquired_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(TZ)


class EgressLog(Base):
    """Calls and errors per host per day (docs/01 §12.4)."""

    __tablename__ = "egress_log"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    host: Mapped[str] = mapped_column(Text, primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, server_default="0")
    errors: Mapped[int] = mapped_column(Integer, server_default="0")


class ParityCheck(Base):
    """Hourly comparison of a dashboard number with the same number from the sheet."""

    __tablename__ = "parity_checks"
    __table_args__ = (
        Index("ix_parity_checks_at", "at"),
        Index("ix_parity_checks_run_id", "run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = _now()
    metric: Mapped[str] = mapped_column(Text)
    sheet_value: Mapped[Decimal | None] = mapped_column(Numeric)
    view_value: Mapped[Decimal | None] = mapped_column(Numeric)
    ok: Mapped[bool] = mapped_column(Boolean)
    note: Mapped[str | None] = mapped_column(Text)


# ------------------------------------------------------------------ sales (0002)


class Strategy(Base):
    """A motion from the Sept2026_Sales Strategy tab."""

    __tablename__ = "strategy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sheet_row_key: Mapped[str] = mapped_column(Text, unique=True)
    sr_no: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    icp: Mapped[str | None] = mapped_column(Text)
    icp_per_month: Mapped[int | None] = mapped_column(Integer)
    orgs_per_month: Mapped[int | None] = mapped_column(Integer)
    collaterals: Mapped[str | None] = mapped_column(Text)
    ready: Mapped[bool | None] = mapped_column(Boolean)
    pending: Mapped[str | None] = mapped_column(Text)
    timeline: Mapped[str | None] = mapped_column(Text)
    metric_text: Mapped[str | None] = mapped_column(Text)
    model_abc: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = _now()
    removed_at: Mapped[datetime | None] = mapped_column(TZ)


class Goal(Base):
    """A Q4 goal. `code` is G1…G5 by row order; `need` is the number parsed from Q4 Target."""

    __tablename__ = "goal"
    __table_args__ = (Index("ix_goal_strategy_id", "strategy_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sheet_row_key: Mapped[str] = mapped_column(Text, unique=True)
    code: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    value_text: Mapped[str | None] = mapped_column(Text)
    value_min: Mapped[int | None] = mapped_column(Integer)
    value_max: Mapped[int | None] = mapped_column(Integer)
    metric: Mapped[str | None] = mapped_column(Text)
    target: Mapped[str | None] = mapped_column(Text)
    need: Mapped[int | None] = mapped_column(Integer)
    owner: Mapped[str | None] = mapped_column(Text)
    dependencies: Mapped[str | None] = mapped_column(Text)
    total_leads: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    due: Mapped[date | None] = mapped_column(Date)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategy.id"))
    updated_at: Mapped[datetime] = _now()
    removed_at: Mapped[datetime | None] = mapped_column(TZ)


class Account(Base):
    """A company. From Master Pipeline, or from the company column of Outreach PoCs."""

    __tablename__ = "account"
    __table_args__ = (
        Index("ix_account_goal_id", "goal_id"),
        Index("ix_account_motion_id", "motion_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    name_key: Mapped[str] = mapped_column(Text, unique=True)
    sheet_row_key: Mapped[str] = mapped_column(Text, unique=True)
    in_master: Mapped[bool] = mapped_column(Boolean, server_default="false")
    industry: Mapped[str | None] = mapped_column(Text)
    geo: Mapped[str | None] = mapped_column(Text)
    funding: Mapped[str | None] = mapped_column(Text)
    outreach_line: Mapped[str | None] = mapped_column(Text)
    tier: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    motion_id: Mapped[int | None] = mapped_column(ForeignKey("strategy.id"))
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goal.id"))
    apollo_org_id: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = _now()
    removed_at: Mapped[datetime | None] = mapped_column(TZ)


class Contact(Base):
    """A person from Outreach PoCs. Step cells are mirrored here; `stage` is derived (D2)."""

    __tablename__ = "contact"
    __table_args__ = (
        Index("ix_contact_account_id", "account_id"),
        Index("ix_contact_goal_id", "goal_id"),
        Index("ix_contact_motion_id", "motion_id"),
        CheckConstraint(
            "stage in ('Listed','Contacted','Connected','Engaged','Meeting',"
            "'Qualified','Parked','Won','Lost')",
            name="ck_contact_stage",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"))
    sheet_row_key: Mapped[str] = mapped_column(Text, unique=True)
    sr_no: Mapped[str | None] = mapped_column(Text)
    row_number: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text)
    name_key: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    email_status: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    li_url: Mapped[str | None] = mapped_column(Text)
    paper_url: Mapped[str | None] = mapped_column(Text)
    x_handle: Mapped[str | None] = mapped_column(Text)
    about: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(Text)
    application_bucket: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str | None] = mapped_column(Text)
    role_in_deal: Mapped[str | None] = mapped_column(Text)
    connected_with_vr: Mapped[bool] = mapped_column(Boolean, server_default="false")
    stage: Mapped[str] = mapped_column(Text, server_default="Listed")
    owner: Mapped[str | None] = mapped_column(Text)
    owner_source: Mapped[str | None] = mapped_column(Text)
    motion_id: Mapped[int | None] = mapped_column(ForeignKey("strategy.id"))
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goal.id"))
    first_contact: Mapped[str | None] = mapped_column(Text)
    first_contact_type: Mapped[str | None] = mapped_column(Text)
    first_contact_date: Mapped[date | None] = mapped_column(Date)
    li_status: Mapped[str | None] = mapped_column(Text)
    li_connected_date: Mapped[date | None] = mapped_column(Date)
    next_step: Mapped[str | None] = mapped_column(Text)
    email1_sent: Mapped[bool] = mapped_column(Boolean, server_default="false")
    email1_date: Mapped[date | None] = mapped_column(Date)
    email2_sent: Mapped[bool] = mapped_column(Boolean, server_default="false")
    email2_date: Mapped[date | None] = mapped_column(Date)
    email3_sent: Mapped[bool] = mapped_column(Boolean, server_default="false")
    email3_date: Mapped[date | None] = mapped_column(Date)
    li_dm_sent: Mapped[bool] = mapped_column(Boolean, server_default="false")
    li_dm_date: Mapped[date | None] = mapped_column(Date)
    meeting_date: Mapped[date | None] = mapped_column(Date)
    meeting_status: Mapped[str | None] = mapped_column(Text)
    reconnect_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    contacted: Mapped[bool] = mapped_column(Boolean, server_default="false")
    connected: Mapped[bool] = mapped_column(Boolean, server_default="false")
    emailed: Mapped[bool] = mapped_column(Boolean, server_default="false")
    cells: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    synced_at: Mapped[datetime] = _now()
    removed_at: Mapped[datetime | None] = mapped_column(TZ)


class Touch(Base):
    """One contact step. Sheet touches are unique per (contact, type); other sources repeat."""

    __tablename__ = "touch"
    __table_args__ = (
        Index("ix_touch_contact_date", "contact_id", "date"),
        Index(
            "uq_touch_sheet_step",
            "contact_id",
            "type",
            unique=True,
            postgresql_where=text("source = 'sheet'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(Text)
    date: Mapped[date | None] = mapped_column(Date)
    template_id: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, server_default="sheet")
    created_at: Mapped[datetime] = _now()


class StageLog(Base):
    """A stage change with its reason. History for velocity (docs/06 §4.1)."""

    __tablename__ = "stage_log"
    __table_args__ = (Index("ix_stage_log_contact_from", "contact_id", "from_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(Text)
    from_at: Mapped[datetime] = mapped_column(TZ)
    reason: Mapped[str | None] = mapped_column(Text)


class Package(Base):
    """A sales package from the Sales Packages tab."""

    __tablename__ = "package"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sheet_row_key: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str | None] = mapped_column(Text)
    use_case: Mapped[str | None] = mapped_column(Text)
    size: Mapped[str | None] = mapped_column(Text)
    pct: Mapped[int | None] = mapped_column(Integer)
    ready: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = _now()
    removed_at: Mapped[datetime | None] = mapped_column(TZ)


class Opportunity(Base):
    """Commercial cells of a contact row. Exists only when one of them is filled."""

    __tablename__ = "opportunity"
    __table_args__ = (
        Index("ix_opportunity_goal_id", "goal_id"),
        Index("ix_opportunity_package_id", "package_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contact.id", ondelete="CASCADE"), unique=True
    )
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goal.id"))
    package_id: Mapped[int | None] = mapped_column(ForeignKey("package.id"))
    package_text: Mapped[str | None] = mapped_column(Text)
    size_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    prob: Mapped[int | None] = mapped_column(Integer)
    size_usd: Mapped[int | None] = mapped_column(Integer)
    deal_status: Mapped[str | None] = mapped_column(Text)
    champion: Mapped[str | None] = mapped_column(Text)
    budget_owner: Mapped[str | None] = mapped_column(Text)
    risk: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = _now()


class Deliverable(Base):
    """A Deliverables Checklist item. `blocks_*` come from the goal and motion text at sync."""

    __tablename__ = "deliverable"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sheet_row_key: Mapped[str] = mapped_column(Text, unique=True)
    sr_no: Mapped[str | None] = mapped_column(Text)
    item: Mapped[str] = mapped_column(Text)
    dependency: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str | None] = mapped_column(Text)
    due: Mapped[date | None] = mapped_column(Date)
    timelines: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    remarks: Mapped[str | None] = mapped_column(Text)
    done: Mapped[date | None] = mapped_column(Date)
    link: Mapped[str | None] = mapped_column(Text)
    reminder_freq: Mapped[str | None] = mapped_column(Text)
    blocks_goal_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), server_default="{}")
    blocks_strategy_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), server_default="{}")
    updated_at: Mapped[datetime] = _now()
    removed_at: Mapped[datetime | None] = mapped_column(TZ)


class Event(Base):
    """A row of AI Events & Summits."""

    __tablename__ = "event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sheet_row_key: Mapped[str] = mapped_column(Text, unique=True)
    sr_no: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    start: Mapped[date | None] = mapped_column(Date)
    end: Mapped[date | None] = mapped_column(Date)
    url: Mapped[str | None] = mapped_column(Text)
    timings: Mapped[str | None] = mapped_column(Text)
    key_people: Mapped[str | None] = mapped_column(Text)
    reg_deadline: Mapped[date | None] = mapped_column(Date)
    registered: Mapped[bool | None] = mapped_column(Boolean)
    attended: Mapped[bool | None] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = _now()
    removed_at: Mapped[datetime | None] = mapped_column(TZ)


class Alias(Base):
    """A human-approved other name for an account (C09, S69). Sync never adds rows here."""

    __tablename__ = "alias"
    __table_args__ = (Index("ix_alias_account_id", "account_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(Text, unique=True)
    approved_by: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = _now()


class MetricSnapshot(Base):
    """One metric value per day and scope (S30). scope_id 0 means the whole team."""

    __tablename__ = "metric_snapshot"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    scope_type: Mapped[str] = mapped_column(Text, primary_key=True)
    scope_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    metric: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Decimal | None] = mapped_column(Numeric)


class DataQuality(Base):
    """A bad or doubtful cell seen by the sync (S33). Resolved when the sync no longer sees it."""

    __tablename__ = "data_quality"
    __table_args__ = (
        UniqueConstraint("tab", "row_key", "column_role", "problem", name="uq_data_quality_cell"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tab: Mapped[str] = mapped_column(Text)
    row_key: Mapped[str] = mapped_column(Text)
    column_role: Mapped[str] = mapped_column(Text)
    problem: Mapped[str] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)
    first_seen: Mapped[datetime] = _now()
    last_seen: Mapped[datetime] = _now()
    resolved_at: Mapped[datetime | None] = mapped_column(TZ)


# ------------------------------------------------------------------ sheet snapshots (0004)


class SheetSnapshot(Base):
    """One distinct read of the sheet. Its id is the sync_id the API returns (D40)."""

    __tablename__ = "sheet_snapshot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = _now()
    raw_hash: Mapped[str] = mapped_column(Text)
    tabs_ok: Mapped[int] = mapped_column(Integer, server_default="0")
    tabs_stale: Mapped[int] = mapped_column(Integer, server_default="0")


class SheetTabStatus(Base):
    """Per tab: ok or stale, which headers are missing or new, last good snapshot (D41)."""

    __tablename__ = "sheet_tab_status"
    __table_args__ = (
        CheckConstraint("status in ('ok','stale')", name="ck_sheet_tab_status_status"),
    )

    tab: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    missing_headers: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    new_headers: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    last_good_sync_id: Mapped[int | None] = mapped_column(BigInteger)
    stale_since: Mapped[datetime | None] = mapped_column(TZ)
    reason: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = _now()
