"""Typed SQLAlchemy models. Names follow docs/01 §12; extra fields are noted where they appear.

Why: docs/01 wins on names and tables. Migrations (not `create_all`) create the schema; these
models are the Python view of it and must match migrations 0001 to 0006.
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
    SmallInteger,
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
        Index("uq_users_discord_id", "discord_id", unique=True),  # 0018
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
    slot: Mapped[str] = mapped_column(Text, server_default="worker:main")  # D113


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


# ------------------------------------------------------------------ writes and intelligence (0006)


class Proposal(Base):
    """A one-click idea of Saley's. `card_key` is unique: the same idea never returns (D24)."""

    __tablename__ = "proposal"
    __table_args__ = (
        UniqueConstraint("card_key", name="uq_proposal_card_key"),
        CheckConstraint(
            "state in ('open','acted','dismissed','expired')", name="ck_proposal_state"
        ),
        Index("ix_proposal_state_rank", "state", "rank"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[str] = mapped_column(Text)
    card_key: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(Text)
    entity_id: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    rank: Mapped[int] = mapped_column(Integer, server_default="0")
    due_at: Mapped[datetime | None] = mapped_column(TZ)
    state: Mapped[str] = mapped_column(Text, server_default="open")
    shown_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = _now()


class Decision(Base):
    """The signature table: who, when, where (D24). Writes, task Done, dismiss, undo."""

    __tablename__ = "decision"
    __table_args__ = (
        CheckConstraint(
            "kind in ('column_write','task_done','dismiss','strategy_decision','undo')",
            name="ck_decision_kind",
        ),
        CheckConstraint("state in ('proposed','confirmed','active')", name="ck_decision_state"),
        CheckConstraint(
            "surface is null or surface in ('dashboard','chat','card','discord')",
            name="ck_decision_surface",
        ),
        Index("ix_decision_user_at", "user_id", text("at desc")),
        Index("ix_decision_cell", "tab", "row_key", "role", text("at desc")),
        Index(
            "uq_decision_active_instruction",
            "instruction_key",
            unique=True,
            postgresql_where=text("undone_at is null and kind in ('column_write','task_done')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("proposal.id"))
    kind: Mapped[str] = mapped_column(Text)
    choice: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    at: Mapped[datetime] = _now()
    surface: Mapped[str | None] = mapped_column(Text)
    tab: Mapped[str | None] = mapped_column(Text)
    row_key: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    before: Mapped[str | None] = mapped_column(Text)
    after: Mapped[str | None] = mapped_column(Text)
    instruction_key: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default="active")
    undo_until: Mapped[datetime | None] = mapped_column(TZ)
    undone_at: Mapped[datetime | None] = mapped_column(TZ)
    undone_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    undo_of: Mapped[int | None] = mapped_column(ForeignKey("decision.id"))
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(TZ)
    confirmed_surface: Mapped[str | None] = mapped_column(Text)
    # R5 T3: txid_current() of the act, so the cells of one Done undo together (0023).
    act_txid: Mapped[int | None] = mapped_column(BigInteger, server_default=text("txid_current()"))


class SyncOutbox(Base):
    """A sheet write waiting for the worker (D45). The commit of this row is the write."""

    __tablename__ = "sync_outbox"
    __table_args__ = (
        CheckConstraint("op in ('write','restore')", name="ck_outbox_op"),
        CheckConstraint(
            "state in ('pending','done','failed','held','cancelled')", name="ck_outbox_state"
        ),
        Index("ix_outbox_pending", "state", "next_at"),
        Index("ix_outbox_decision", "decision_id"),
        Index(
            "ix_outbox_cell",
            "tab",
            "row_key",
            "role",
            postgresql_where=text("state in ('pending','held')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("decision.id"))
    op: Mapped[str] = mapped_column(Text, server_default="write")
    tab: Mapped[str] = mapped_column(Text)
    row_key: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default="pending")
    tries: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    next_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class SyncAudit(Base):
    """What the sheet cell held before and after each push."""

    __tablename__ = "sync_audit"
    __table_args__ = (Index("ix_sync_audit_outbox", "outbox_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    outbox_id: Mapped[int] = mapped_column(ForeignKey("sync_outbox.id"))
    before: Mapped[str | None] = mapped_column(Text)
    after: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = _now()
    by: Mapped[str | None] = mapped_column(Text)


class Fact(Base):
    """One statement about an entity with its source (docs/03 §3.2)."""

    __tablename__ = "fact"
    __table_args__ = (
        Index("ix_fact_entity", "entity_type", "entity_id", "kind", text("observed_at desc")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    source_type: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = _now()
    confidence: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(TZ)
    superseded_by: Mapped[int | None] = mapped_column(ForeignKey("fact.id"))


class Commitment(Base):
    """Something a person said they would do. Reminders follow the politeness budget."""

    __tablename__ = "commitment"
    __table_args__ = (
        CheckConstraint("state in ('open','done','dropped')", name="ck_commitment_state"),
        Index("ix_commitment_owner", "owner", "state", "due"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text)
    due: Mapped[date | None] = mapped_column(Date)
    source_type: Mapped[str | None] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default="open")
    reminders_sent: Mapped[int] = mapped_column(Integer, server_default="0")
    last_reminded_on: Mapped[date | None] = mapped_column(Date)
    in_pack: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = _now()


class Reminder(Base):
    """One planned reminder: before the due day, or on it."""

    __tablename__ = "reminder"
    __table_args__ = (
        CheckConstraint("kind in ('before','due_day')", name="ck_reminder_kind"),
        CheckConstraint("state in ('pending','sent','skipped')", name="ck_reminder_state"),
        Index("ix_reminder_fire", "state", "fire_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    for_user: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text)
    fire_at: Mapped[datetime] = mapped_column(TZ)
    entity_id: Mapped[int | None] = mapped_column(Integer)
    commitment_id: Mapped[int | None] = mapped_column(ForeignKey("commitment.id"))
    kind: Mapped[str] = mapped_column(Text, server_default="due_day")
    state: Mapped[str] = mapped_column(Text, server_default="pending")


class Ask(Base):
    """A question Saley put to a person. The answer becomes a fact."""

    __tablename__ = "ask"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    to_user: Mapped[int] = mapped_column(ForeignKey("users.id"))
    question: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[int | None] = mapped_column(Integer)
    asked_at: Mapped[datetime] = _now()
    answered_at: Mapped[datetime | None] = mapped_column(TZ)
    answer_fact_id: Mapped[int | None] = mapped_column(ForeignKey("fact.id"))
    msg_id: Mapped[str | None] = mapped_column(Text)


class UserSeen(Base):
    """What each person was shown (D22). Per person, never per channel."""

    __tablename__ = "user_seen"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    item_type: Mapped[str] = mapped_column(Text, primary_key=True)
    item_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    seen_at: Mapped[datetime] = _now()
    query_id: Mapped[str | None] = mapped_column(Text)


# ------------------------------------------------------------------ news and signals (0010)


class NewsSource(Base):
    """One row of the source registry (docs/05 §6). Clocks live in data, not in code."""

    __tablename__ = "news_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(Text)
    extractor: Mapped[str | None] = mapped_column(Text)
    lane: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    tier: Mapped[int] = mapped_column(SmallInteger, server_default="3")
    area: Mapped[str | None] = mapped_column(Text)
    categories: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    trusted: Mapped[bool] = mapped_column(Boolean, server_default="false")
    ai_only: Mapped[bool] = mapped_column(Boolean, server_default="false")
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    class_: Mapped[str | None] = mapped_column("class", Text)
    poll_active_min: Mapped[int] = mapped_column(Integer, server_default="120")
    poll_idle_min: Mapped[int] = mapped_column(Integer, server_default="240")
    active_from: Mapped[int | None] = mapped_column(SmallInteger)
    active_to: Mapped[int | None] = mapped_column(SmallInteger)
    weekend_min: Mapped[int | None] = mapped_column(Integer)
    depth: Mapped[int | None] = mapped_column(Integer)
    rate_7d: Mapped[Decimal | None] = mapped_column(Numeric)
    never_miss_min: Mapped[int | None] = mapped_column(Integer)
    next_due_at: Mapped[datetime | None] = mapped_column(TZ)
    last_ok_at: Mapped[datetime | None] = mapped_column(TZ)
    last_status: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    fail_streak: Mapped[int] = mapped_column(Integer, server_default="0")
    backoff_until: Mapped[datetime | None] = mapped_column(TZ)
    route: Mapped[str] = mapped_column(Text, server_default="direct")
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class NewsEdition(Base):
    """One run of the news pipeline for a time slot (06:30, 12:00, ...)."""

    __tablename__ = "news_editions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slot: Mapped[str] = mapped_column(Text)
    family: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(TZ)
    status: Mapped[str] = mapped_column(Text, server_default="running")
    scorer_calls: Mapped[int] = mapped_column(Integer, server_default="0")
    scorer_backend: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)


class NewsSourceRun(Base):
    """One fetch of one source, with the counters the funnel is built from."""

    __tablename__ = "news_source_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("news_sources.id", ondelete="CASCADE"))
    edition_id: Mapped[int | None] = mapped_column(
        ForeignKey("news_editions.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(TZ)
    status: Mapped[str] = mapped_column(Text, server_default="ok")
    http_status: Mapped[int | None] = mapped_column(Integer)
    items_seen: Mapped[int] = mapped_column(Integer, server_default="0")
    items_new: Mapped[int] = mapped_column(Integer, server_default="0")
    dropped_seen: Mapped[int] = mapped_column(Integer, server_default="0")
    dropped_gate: Mapped[int] = mapped_column(Integer, server_default="0")
    dropped_copy: Mapped[int] = mapped_column(Integer, server_default="0")
    bytes: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    route_used: Mapped[str | None] = mapped_column(Text)


class NewsStory(Base):
    """One unique story with its outlets and, once scored, its score (D72)."""

    __tablename__ = "news_stories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, server_default="")
    url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, server_default="news")
    outlets: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    outlet_count: Mapped[int] = mapped_column(Integer, server_default="1")
    first_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("news_sources.id", ondelete="SET NULL")
    )
    edition_id: Mapped[int | None] = mapped_column(
        ForeignKey("news_editions.id", ondelete="SET NULL")
    )
    flag: Mapped[bool] = mapped_column(Boolean, server_default="false")
    name_kind: Mapped[str | None] = mapped_column(Text)
    names: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    published_at: Mapped[datetime | None] = mapped_column(TZ)
    first_seen_at: Mapped[datetime] = _now()
    importance: Mapped[int | None] = mapped_column(SmallInteger)
    impact: Mapped[int | None] = mapped_column(SmallInteger)
    tag: Mapped[str | None] = mapped_column(Text)
    why: Mapped[str] = mapped_column(Text, server_default="")
    scored_at: Mapped[datetime | None] = mapped_column(TZ)
    scorer_backend: Mapped[str | None] = mapped_column(Text)
    # Scored by `rules` only because the LLM was out: score it by the LLM once more (A2, 0013).
    rescore_due: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # One of the six names or Other, set by plain code (news/topic.py, D156). Null until set.
    topic: Mapped[str | None] = mapped_column(Text)


class NewsSeen(Base):
    """The 14-day memory: URL hash and title fingerprint of every item looked at."""

    __tablename__ = "news_seen"

    url_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(Text)
    # SET NULL, not CASCADE: the URL memory outlives a cleaned-up story (A5, 0013).
    story_id: Mapped[int | None] = mapped_column(ForeignKey("news_stories.id", ondelete="SET NULL"))
    seen_at: Mapped[datetime] = _now()


class NewsDropped(Base):
    """An item a general feed carried that failed the gate. Searchable for 14 days."""

    __tablename__ = "news_dropped"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("news_sources.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(Text)
    seen_at: Mapped[datetime] = _now()


class NewsSent(Base):
    """Where a story went. Empty until the digests exist (R4)."""

    __tablename__ = "news_sent"

    story_id: Mapped[int] = mapped_column(
        ForeignKey("news_stories.id", ondelete="CASCADE"), primary_key=True
    )
    channel: Mapped[str] = mapped_column(Text, primary_key=True)
    sent_at: Mapped[datetime] = _now()
    message_link: Mapped[str | None] = mapped_column(Text)


class Signal(Base):
    """A scored story linked to an account or a person (D74)."""

    __tablename__ = "signal"
    __table_args__ = (
        UniqueConstraint("story_id", "entity_type", "entity_id", name="uq_signal_story_entity"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("news_stories.id", ondelete="CASCADE"))
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[int] = mapped_column(BigInteger)
    match_kind: Mapped[str] = mapped_column(Text)
    tag: Mapped[str | None] = mapped_column(Text)
    importance: Mapped[int] = mapped_column(SmallInteger)
    impact: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    confidence: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    verify: Mapped[bool] = mapped_column(Boolean, server_default="false")
    action_hint: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default="new")
    created_at: Mapped[datetime] = _now()


class SearchRequest(Base):
    """A human's "Find people" click (D77). The worker runs it."""

    __tablename__ = "search_request"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(Text, server_default="pending")
    requested_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(TZ)
    hits: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)  # fixed short text of a failure (B9, 0013)
    http_status: Mapped[int | None] = mapped_column(Integer)


class NewsRunRequest(Base):
    """An admin's "Run news now" click (R5 T8, D150, D151, migration 0024).

    `saley_request_news_run` writes it; the job `news_requests` takes it. A partial unique index
    allows one open row (queued or running) at a time.
    """

    __tablename__ = "news_run_request"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    requested_at: Mapped[datetime] = _now()
    state: Mapped[str] = mapped_column(Text, server_default="queued")
    started_at: Mapped[datetime | None] = mapped_column(TZ)
    finished_at: Mapped[datetime | None] = mapped_column(TZ)
    note: Mapped[str | None] = mapped_column(Text)


class SearchHit(Base):
    """A public search result: URL and title only. Never fetched."""

    __tablename__ = "search_hit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("search_request.id", ondelete="CASCADE")
    )
    query: Mapped[str] = mapped_column(Text)
    engine: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, server_default="")
    account_id: Mapped[int | None] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    found_at: Mapped[datetime] = _now()


# ------------------------------------------------------------------ R4 (0018)


class WebSession(Base):
    """A server-side sign-in (D114). Only the sha256 of the cookie token is stored."""

    __tablename__ = "web_session"
    __table_args__ = (
        CheckConstraint("provider in ('google','mock')", name="ck_web_session_provider"),
        CheckConstraint("id_hash ~ '^[0-9a-f]{64}$'", name="ck_web_session_hash"),
        Index("ix_web_session_user", "user_id"),
        Index("ix_web_session_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    id_hash: Mapped[str] = mapped_column(Text, unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    provider: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(TZ)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)


class ScorecardSnapshot(Base):
    """One row per day and industry; "last week N" reads the day 7 days back (D118)."""

    __tablename__ = "scorecard_snapshot"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    industry: Mapped[str] = mapped_column(Text, primary_key=True)
    people: Mapped[int | None] = mapped_column(Integer)
    contacted: Mapped[int | None] = mapped_column(Integer)
    connect_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    reply_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    meetings: Mapped[int | None] = mapped_column(Integer)
    deals: Mapped[int | None] = mapped_column(Integer)


class WeeklyPack(Base):
    """The Monday pack. The unique Monday key stops a double fire."""

    __tablename__ = "weekly_pack"
    __table_args__ = (
        CheckConstraint("extract(isodow from week_start) = 1", name="ck_weekly_pack_monday"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, unique=True)
    built_at: Mapped[datetime] = _now()
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    post_id: Mapped[str | None] = mapped_column(Text)
    blocker_post_id: Mapped[str | None] = mapped_column(Text)


class BotPost(Base):
    """The queue every bot message passes through. Caps count per person (D132)."""

    __tablename__ = "bot_post"
    __table_args__ = (
        CheckConstraint("channel in ('test','sales','dm')", name="ck_bot_post_channel"),
        CheckConstraint("channel <> 'dm' or for_user is not null", name="ck_bot_post_dm_user"),
        CheckConstraint(
            "state in ('queued','ready','sent','deferred','dropped')", name="ck_bot_post_state"
        ),
        Index("ix_bot_post_state_due", "state", "due_at"),
        Index("ix_bot_post_user_sent", "for_user", "sent_at"),
        Index(
            "uq_bot_post_once",
            "item_type",
            "item_id",
            "shape",
            text("coalesce(for_user, 0)"),
            unique=True,
            postgresql_where=text("item_type in ('daily_brief', 'health_line', 'weekly_pack')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str | None] = mapped_column(Text)
    shape: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text)
    for_user: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    mentions: Mapped[list[int]] = mapped_column(ARRAY(Integer), server_default=text("'{}'"))
    body: Mapped[str] = mapped_column(Text)
    rank: Mapped[int | None] = mapped_column(Integer, server_default="0")
    due_at: Mapped[datetime] = mapped_column(TZ)
    not_before: Mapped[datetime | None] = mapped_column(TZ)
    state: Mapped[str] = mapped_column(Text, server_default="queued")
    sent_at: Mapped[datetime | None] = mapped_column(TZ)
    msg_id: Mapped[str | None] = mapped_column(Text)
    item_type: Mapped[str | None] = mapped_column(Text)
    item_id: Mapped[int | None] = mapped_column(BigInteger)
    is_meeting: Mapped[bool | None] = mapped_column(Boolean, server_default="false")
    is_reconnect: Mapped[bool | None] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = _now()


class BotExchange(Base):
    """The learn log of questions and answers, written by `saley_ask_log`."""

    __tablename__ = "bot_exchange"
    __table_args__ = (
        CheckConstraint("surface in ('discord','dashboard')", name="ck_bot_exchange_surface"),
        Index("ix_bot_exchange_user_at", "user_id", text("created_at desc")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    surface: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(Text)
    refs: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    answer: Mapped[str | None] = mapped_column(Text)
    window_from: Mapped[datetime | None] = mapped_column(TZ)
    window_to: Mapped[datetime | None] = mapped_column(TZ)
    left_out: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _now()
