"""Sheet snapshots (sync_id), tab status, deal size text, money blocked, honest goal numbers.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-19

Why: R1.1 ticket B and C (decisions D40, D41, D42).
- `sheet_snapshot`: one row per distinct sheet read. Its id is the `sync_id` every API
  response carries; it changes only when the sheet changed.
- `sheet_tab_status`: per tab ok / stale, missing and new headers, last good snapshot.
- `opportunity.size_text`: the deal-size cell as typed (a band like "USD 30–50k").
- `v_money_blocked`: deals blocked by a late deliverable (Blockers page, D42).
- `v_goal_status`: weighted pipeline is null when no deal has a size and probability
  (never 0 for unknown); `blockers` lists every open blocking deliverable with its owner
  and days late (negative = due in N days); `blocker_count` stays the late count.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

V_CONTACT_STAGE = r"""
create or replace view v_contact_stage as
with base as (
  select
    c.*,
    a.name as company,
    a.industry as account_industry,
    upper(trim(coalesce(c.priority, ''))) like 'P1%' as is_p1,
    coalesce(c.email, '') like '%@%' as has_email,
    greatest(c.first_contact_date, c.li_connected_date, c.email1_date, c.email2_date,
             c.email3_date, c.li_dm_date, c.meeting_date) as last_step_date
  from contact c
  join account a on a.id = c.account_id
  where c.removed_at is null
)
select
  b.id, b.sheet_row_key, b.sr_no, b.row_number, b.name, b.title, b.account_id, b.company,
  coalesce(nullif(b.industry, ''), b.account_industry) as industry,
  b.industry as sheet_industry,
  b.stage, saley_stage_rank(b.stage) as stage_rank, b.owner, b.owner_source, b.priority, b.is_p1,
  b.city, b.email, b.has_email, b.email_status, b.next_step, b.li_status, b.li_url, b.paper_url,
  b.about, b.notes, b.contacted, b.connected, b.emailed, b.connected_with_vr,
  b.first_contact, b.first_contact_type, b.first_contact_date, b.li_connected_date,
  b.email1_date, b.email2_date, b.email3_date, b.li_dm_date,
  b.meeting_date, b.meeting_status, b.reconnect_date, b.last_step_date,
  saley_li_requested(b.li_status) as li_requested,
  saley_goal_codes(coalesce(nullif(b.industry, ''), b.account_industry)) as goal_codes,
  b.stage = 'Connected' as is_backlog,
  -- D37
  (b.is_p1 and saley_li_requested(b.li_status) and not b.connected
     and b.stage in ('Listed', 'Contacted')) as is_p1_waiting,
  b.stage in ('Engaged', 'Meeting', 'Qualified') as in_conversation,
  -- D14 / D36: explicit date, else meeting + 45 days, else latest step + 45 days.
  case when b.stage in ('Listed', 'Won', 'Lost') then null
       else coalesce(b.reconnect_date, b.meeting_date + 45, b.last_step_date + 45) end
    as reconnect_due_on,
  coalesce(
    case when b.stage in ('Listed', 'Won', 'Lost') then null
         else coalesce(b.reconnect_date, b.meeting_date + 45, b.last_step_date + 45) end
      <= saley_today(), false) as reconnect_due,
  b.first_contact_date >= date_trunc('week', saley_today())::date
    and b.first_contact_date <= saley_today() as contacted_this_week,
  b.first_contact_date >= (date_trunc('week', saley_today()) - interval '7 days')::date
    and b.first_contact_date < date_trunc('week', saley_today())::date as contacted_last_week,
  b.synced_at,
  b.x_handle  -- R1.1: website / X link for the person drawer (D39)
from base b
"""

V_GOAL_STATUS = r"""
create or replace view v_goal_status as
with linked as (
  select g.id as goal_id, c.*
  from goal g
  join v_contact_stage c on g.code = any(c.goal_codes)
  where g.removed_at is null
), active as (
  select goal_id,
         count(*) as active_count,
         count(*) filter (where stage = 'Won') as won,
         jsonb_agg(jsonb_build_object('id', id, 'name', name, 'company', company,
                                      'stage', stage, 'row', sheet_row_key)
                   order by stage_rank desc, company, name) as active
  from linked
  where stage in ('Engaged', 'Meeting', 'Qualified', 'Won')
  group by goal_id
), money as (
  select l.goal_id,
         sum(o.size_usd * o.prob / 100.0)
           filter (where o.size_usd is not null and o.prob is not null) as weighted_usd
  from linked l join opportunity o on o.contact_id = l.id
  where l.stage not in ('Lost', 'Parked')
  group by l.goal_id
), open_blocking as (
  select g.id as goal_id, d.item, d.dependency as owner, d.due, d.status, d.sheet_row_key,
         (saley_today() - d.due) as late_by_days
  from goal g
  join deliverable d on g.id = any(d.blocks_goal_ids)
  where g.removed_at is null and d.removed_at is null
    and d.done is null and not saley_is_done(d.status)
), blockers as (
  select goal_id,
         jsonb_agg(jsonb_build_object('item', item, 'owner', owner, 'due', due,
                                      'late_by_days', late_by_days, 'status', status,
                                      'row', sheet_row_key)
                   order by late_by_days desc nulls last, item) as blockers,
         count(*) filter (where late_by_days > 0) as blocker_count
  from open_blocking
  group by goal_id
)
select
  g.id, g.sheet_row_key, g.code, g.position, g.text, g.value_text, g.value_min, g.value_max,
  g.metric, g.target, g.need, g.owner, g.dependencies, g.due,
  (g.due - saley_today()) as days_left,
  coalesce(a.active_count, 0) as active_count,
  coalesce(a.active, '[]'::jsonb) as active,
  m.weighted_usd::float8 as weighted_usd,
  coalesce(a.won, 0) as won,
  coalesce(b.blockers, '[]'::jsonb) as blockers,
  coalesce(b.blocker_count, 0) as blocker_count,
  case
    -- A goal that closes deals is on track only when deals are won; others count conversations.
    when (lower(g.text) like 'close%' or lower(coalesce(g.target, '')) like '%deal%')
      then case
        when coalesce(a.won, 0) >= greatest(coalesce(g.need, 1), 1) then 'on track'
        when coalesce(a.active_count, 0) = 0 and coalesce(b.blocker_count, 0) > 0 then 'blocked'
        else 'at risk' end
    -- No number in the target: Saley cannot say "on track".
    when g.need is null
      then case when coalesce(a.active_count, 0) = 0 and coalesce(b.blocker_count, 0) > 0
                then 'blocked' else 'at risk' end
    when coalesce(a.active_count, 0) >= g.need then 'on track'
    when coalesce(a.active_count, 0) = 0 and coalesce(b.blocker_count, 0) > 0 then 'blocked'
    else 'at risk'
  end as rag
from goal g
left join active a on a.goal_id = g.id
left join money m on m.goal_id = g.id
left join blockers b on b.goal_id = g.id
where g.removed_at is null
"""

# Deals = opportunity rows still in play. One row per deal, with its most-late blocking
# deliverable and the number of other late blockers (D42).
V_MONEY_BLOCKED = r"""
create view v_money_blocked as
with deals as (
  select c.id as contact_id, c.name, c.company, c.sheet_row_key, c.stage, c.goal_codes,
         o.size_usd, o.size_text
  from v_contact_stage c
  join opportunity o on o.contact_id = c.id
  where c.stage not in ('Won', 'Lost', 'Parked')
), blocking as (
  select d.contact_id, l.item, l.owner, l.late_by_days,
         row_number() over (partition by d.contact_id order by l.late_by_days desc, l.item) as rn,
         count(*) over (partition by d.contact_id) as n_blocking
  from deals d
  join v_deliverable_late l on l.blocks_goals && d.goal_codes
)
select d.contact_id, d.name, d.company, d.sheet_row_key, d.stage, d.size_usd, d.size_text,
       b.item as deliverable, b.owner, b.late_by_days, (b.n_blocking - 1) as more_blocking
from deals d
join blocking b on b.contact_id = d.contact_id and b.rn = 1
"""

GRANTS = r"""
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'saley_ro') then
    execute 'grant select on sheet_snapshot, sheet_tab_status, v_money_blocked to saley_ro';
  end if;
  if exists (select 1 from pg_roles where rolname = 'saley') then
    execute 'grant select, insert, update, delete on sheet_snapshot, sheet_tab_status to saley';
    execute 'grant usage, select on sequence sheet_snapshot_id_seq to saley';
  end if;
end $$;
"""


def upgrade() -> None:
    op.create_table(
        "sheet_snapshot",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("raw_hash", sa.Text(), nullable=False),
        sa.Column("tabs_ok", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tabs_stale", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sheet_tab_status",
        sa.Column("tab", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "missing_headers", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("new_headers", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("last_good_sync_id", sa.BigInteger(), nullable=True),
        sa.Column("stale_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status in ('ok','stale')", name="ck_sheet_tab_status_status"),
        sa.PrimaryKeyConstraint("tab"),
    )
    op.add_column("opportunity", sa.Column("size_text", sa.Text(), nullable=True))
    op.execute(V_CONTACT_STAGE)
    op.execute(V_GOAL_STATUS)
    op.execute(V_MONEY_BLOCKED)
    op.execute(GRANTS)


def _v0003_goal_status() -> str:
    """The 0003 view text, read from that migration file (its name is not importable)."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0003_views.py")
    spec = importlib.util.spec_from_file_location("migration_0003", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.V_GOAL_STATUS


def downgrade() -> None:
    op.execute("drop view if exists v_money_blocked")
    # Back to the exact 0003 view text (a replace cannot remove the new CTEs). Its column
    # list differs, so drop and create; nothing else depends on v_goal_status.
    op.execute("drop view if exists v_goal_status")
    op.execute(_v0003_goal_status())
    # v_contact_stage keeps its extra x_handle column: a view cannot drop a column in place,
    # and 0003's downgrade drops the view anyway.
    op.drop_column("opportunity", "size_text")
    op.drop_table("sheet_tab_status")
    op.drop_table("sheet_snapshot")
