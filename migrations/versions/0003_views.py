"""Dashboard SQL views (docs/08: one view = one endpoint) and helper functions.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-18

Why: the web-api reads only views, with the read-only role. Rules here mirror
saley.sales.stage and docs/06; tests check the views against the Python rules.
Goal links come from industry (docs/06 §2.3) until the Goal column exists (D6); the UI
says "by industry". Dates use saley_today() = today in Asia/Kolkata (SALEY_TZ).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNCTIONS = r"""
create or replace function saley_today() returns date
language sql stable as $$ select (now() at time zone 'Asia/Kolkata')::date $$;

-- Warmth order; matches saley.sales.stage.WARMTH_RANK.
create or replace function saley_stage_rank(stage text) returns int
language sql immutable as $$
  select case stage
    when 'Won' then 7 when 'Qualified' then 6 when 'Meeting' then 5 when 'Engaged' then 4
    when 'Connected' then 3 when 'Contacted' then 2 when 'Parked' then 1 when 'Lost' then 0
    else 0 end
$$;

-- Default goal per industry, docs/06 §2.3 (D30). Replaced by the Goal column after D6.
create or replace function saley_goal_codes(industry text) returns text[]
language sql immutable as $$
  select case
    when lower(coalesce(industry, '')) like '%academ%' then array['G5']
    when coalesce(industry, '') ~* '\mlabs?\M' then array['G1', 'G2', 'G4']
    when lower(coalesce(industry, '')) like '%voice%' then array['G3']
    else array[]::text[] end
$$;

-- Same words as saley.sales.stage.li_requested.
create or replace function saley_li_requested(status text) returns boolean
language sql immutable as $$
  select lower(trim(coalesce(status, ''))) not in
    ('', 'no', 'not sent', 'false', '-', 'n/a', 'na', 'couldn''t find', 'could not find', 'not found')
$$;

create or replace function saley_is_done(status text) returns boolean
language sql immutable as $$
  -- 'Completed', 'Done ✅', 'Closed' are done; 'Not done', 'Incomplete' are not.
  select lower(trim(coalesce(status, ''))) ~ '^(completed?|done|closed)\M'
$$;
"""

V_CONTACT_STAGE = r"""
create view v_contact_stage as
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
  b.synced_at
from base b
"""

V_ACCOUNT_COVERAGE = r"""
create view v_account_coverage as
select
  a.id, a.sheet_row_key, a.name, a.industry, a.geo, a.funding, a.in_master,
  saley_goal_codes(a.industry) as goal_codes,
  count(c.id) as people,
  count(c.id) filter (where c.contacted) as contacted,
  count(c.id) filter (where c.connected) as connected,
  count(c.id) filter (where c.emailed) as emailed,
  count(c.id) filter (where c.is_p1) as p1_count,
  count(c.id) filter (where c.stage_rank >= 3 and c.stage not in ('Parked', 'Lost')) as threads,
  case when count(c.id) > 0
       then round(100.0 * count(c.id) filter (where c.contacted) / count(c.id))::int end
    as coverage_pct,
  coalesce((array_agg(c.stage order by c.stage_rank desc, c.stage)
            filter (where c.id is not null))[1],
           'Listed') as warmth
from account a
left join v_contact_stage c on c.account_id = a.id
where a.removed_at is null
group by a.id
"""

V_DELIVERABLE_LATE = r"""
create view v_deliverable_late as
select
  d.id, d.sheet_row_key, d.sr_no, d.item, d.dependency as owner, d.priority, d.due,
  (saley_today() - d.due) as late_by_days, d.status, d.remarks, d.link,
  coalesce((select array_agg(g.code order by g.position) from goal g
            where g.id = any(d.blocks_goal_ids) and g.removed_at is null), array[]::text[])
    as blocks_goals,
  coalesce((select array_agg(s.name order by s.id) from strategy s
            where s.id = any(d.blocks_strategy_ids) and s.removed_at is null), array[]::text[])
    as blocks_motions,
  (select sum(g.value_max) from goal g
    where g.id = any(d.blocks_goal_ids) and g.removed_at is null)::bigint as money_blocked_usd
from deliverable d
where d.removed_at is null
  and d.due is not null and d.due < saley_today()
  and d.done is null and not saley_is_done(d.status)
"""

V_PACKAGE_NOT_READY = r"""
create view v_package_not_ready as
select p.id, p.sheet_row_key, p.name, p.purpose, p.use_case, p.pct, p.status
from package p
where p.removed_at is null and p.ready is false
"""

V_GOAL_STATUS = r"""
create view v_goal_status as
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
         sum(o.size_usd * o.prob / 100.0) as weighted_usd
  from linked l join opportunity o on o.contact_id = l.id
  where l.stage not in ('Lost', 'Parked')
  group by l.goal_id
), blockers as (
  select g.id as goal_id,
         jsonb_agg(jsonb_build_object('item', d.item, 'due', d.due, 'late_by_days', d.late_by_days,
                                      'status', d.status, 'row', d.sheet_row_key)
                   order by d.due) as blockers,
         count(*) as blocker_count
  from goal g join v_deliverable_late d on g.code = any(d.blocks_goals)
  group by g.id
)
select
  g.id, g.sheet_row_key, g.code, g.position, g.text, g.value_text, g.value_min, g.value_max,
  g.metric, g.target, g.need, g.owner, g.dependencies, g.due,
  (g.due - saley_today()) as days_left,
  coalesce(a.active_count, 0) as active_count,
  coalesce(a.active, '[]'::jsonb) as active,
  coalesce(m.weighted_usd, 0)::float8 as weighted_usd,
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

V_SEGMENT_SCORECARD = r"""
create view v_segment_scorecard as
select
  coalesce(nullif(sheet_industry, ''), 'No industry') as industry,
  count(*) as people,
  count(*) filter (where contacted) as contacted,
  count(*) filter (where li_requested) as li_requested,
  count(*) filter (where connected) as connected,
  case when count(*) filter (where li_requested) > 0
       then round(100.0 * count(*) filter (where connected)
                  / count(*) filter (where li_requested))::int end as connect_pct,
  null::int as reply_pct,
  count(*) filter (where meeting_date is not null or coalesce(meeting_status, '') <> '') as meetings,
  count(*) filter (where stage = 'Won') as deals
from v_contact_stage
group by 1
"""

V_TODAY_USER = r"""
create view v_today_user as
select
  (select count(*) from v_contact_stage where stage = 'Won') as deals_closed,
  -- D33: goals that close a deal. No such goal → null, never a made-up number.
  (select nullif(count(*) filter (where lower(text) like 'close%'), 0)
     from goal where removed_at is null) as deals_target,
  (select max(due) from goal where removed_at is null) as quarter_end,
  (select max(due) - saley_today() from goal where removed_at is null) as days_left,
  (select count(*) from v_contact_stage where in_conversation) as live_conversations,
  (select count(*) from v_contact_stage where is_backlog) as backlog,
  (select count(*) from v_contact_stage where is_backlog and has_email) as backlog_with_email,
  (select count(*) from v_contact_stage where is_p1_waiting) as p1_waiting,
  (select count(*) from v_contact_stage where is_p1) as p1_total,
  (select count(*) from v_contact_stage where contacted_this_week) as contacted_this_week,
  (select count(*) from v_contact_stage where contacted_last_week) as contacted_last_week,
  (select count(*) from v_deliverable_late where cardinality(blocks_goals) > 0) as late_blockers,
  (select count(*) from v_deliverable_late) as late_total,
  -- Short names: the part before ' - ' ("NDA - to be prepared…" → "NDA").
  (select string_agg(split_part(item, ' - ', 1), ', ' order by due) from
     (select item, due from v_deliverable_late where cardinality(blocks_goals) > 0
      order by due limit 3) t) as late_names
"""

GRANTS = r"""
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'saley_ro') then
    execute 'grant usage on schema public to saley_ro';
    execute 'grant select on all tables in schema public to saley_ro';
    -- Base tables with personal or raw data stay out of reach of the web role.
    execute 'revoke select on users, contact, llm_cache, alembic_version, job_locks from saley_ro';
  end if;
  if exists (select 1 from pg_roles where rolname = 'saley') then
    execute 'grant usage on schema public to saley';
    execute 'grant select, insert, update, delete on all tables in schema public to saley';
    execute 'grant usage, select on all sequences in schema public to saley';
  end if;
end $$;
"""

V_TEAM = r"""
create view v_team as
select id, short, name, role from users
"""

VIEWS = (
    ("v_team", V_TEAM),
    ("v_contact_stage", V_CONTACT_STAGE),
    ("v_account_coverage", V_ACCOUNT_COVERAGE),
    ("v_deliverable_late", V_DELIVERABLE_LATE),
    ("v_package_not_ready", V_PACKAGE_NOT_READY),
    ("v_goal_status", V_GOAL_STATUS),
    ("v_segment_scorecard", V_SEGMENT_SCORECARD),
    ("v_today_user", V_TODAY_USER),
)


def upgrade() -> None:
    op.execute(FUNCTIONS)
    for _, sql in VIEWS:
        op.execute(sql)
    op.execute(GRANTS)


def downgrade() -> None:
    for name, _ in reversed(VIEWS):
        op.execute(f"drop view if exists {name}")
    for fn in (
        "saley_today()",
        "saley_stage_rank(text)",
        "saley_goal_codes(text)",
        "saley_li_requested(text)",
        "saley_is_done(text)",
    ):
        op.execute(f"drop function if exists {fn}")
