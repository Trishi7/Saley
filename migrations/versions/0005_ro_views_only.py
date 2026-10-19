"""The web role reads views only (R1.1 database review, HIGH).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-19

Why: `saley_ro` had SELECT on every table, with a short revoke list. Raw tables such as
`opportunity` (champion, budget owner, risk, notes) and `touch` were readable by the web role.
Views run with the owner's rights, so the web needs no table grant at all. This migration:
- adds narrow views for the raw reads the API still made (touches, stage log, deal, jobs,
  parity, data quality, calls out, LLM use, deliverables);
- revokes SELECT on every table and view from `saley_ro`, then grants it on the `v_*` views
  and on the two snapshot tables (`sheet_snapshot`, `sheet_tab_status`: no personal data);
- stops the default grant of new tables to `saley_ro`. A new view needs its own grant
  (`grant_ro_views()` below); tests/test_core.py fails on any other table grant.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RO_TABLES = ("sheet_snapshot", "sheet_tab_status")

VIEWS = {
    "v_touch": "select id, contact_id, type, date, outcome, source from touch",
    "v_stage_log": "select id, contact_id, stage, from_at, reason from stage_log",
    "v_opportunity": (
        "select contact_id, package_text, status, prob, size_usd, size_text, deal_status "
        "from opportunity"
    ),
    "v_job_runs": "select id, job, status, started_at, finished_at, note from job_runs",
    "v_parity_checks": (
        "select run_id, at, metric, sheet_value, view_value, ok, note from parity_checks"
    ),
    "v_data_quality_open": (
        "select problem, count(*) as n from data_quality where resolved_at is null group by problem"
    ),
    "v_egress_today": "select host, calls, errors from egress_log where day = saley_today()",
    "v_llm_today": (
        "select count(*) filter (where not cached) as calls, "
        "count(*) filter (where cached) as cached from llm_calls "
        "where at >= saley_today()::timestamp at time zone 'Asia/Kolkata'"
    ),
    "v_deliverable": (
        "select id, item, dependency, status, due from deliverable where removed_at is null"
    ),
}

GRANT_RO_VIEWS = r"""
do $$
declare
  v text;
begin
  if not exists (select 1 from pg_roles where rolname = 'saley_ro') then
    return;
  end if;
  execute 'revoke select on all tables in schema public from saley_ro';
  for v in select viewname from pg_views where schemaname = 'public' and viewname like 'v\_%' loop
    execute format('grant select on %I to saley_ro', v);
  end loop;
  execute 'grant select on sheet_snapshot, sheet_tab_status to saley_ro';
end $$;
"""

DEFAULT_PRIVS = r"""
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'saley_ro') then
    if exists (select 1 from pg_roles where rolname = 'saley_admin') then
      execute 'alter default privileges for role saley_admin in schema public '
              'revoke select on tables from saley_ro';
    end if;
    if exists (select 1 from pg_roles where rolname = 'saley') then
      execute 'alter default privileges for role saley in schema public '
              'revoke select on tables from saley_ro';
    end if;
  end if;
exception when insufficient_privilege then
  -- Only a member of the role may change its defaults. roles.sql repeats this as superuser.
  raise notice 'default privileges for saley_ro unchanged: run deploy/sql/roles.sql';
end $$;
"""

RESTORE_0004 = r"""
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'saley_ro') then
    execute 'grant select on all tables in schema public to saley_ro';
    execute 'revoke select on users, contact, llm_cache, alembic_version, job_locks from saley_ro';
  end if;
end $$;
"""


def upgrade() -> None:
    for name, body in VIEWS.items():
        op.execute(f"create or replace view {name} as {body}")
    op.execute(GRANT_RO_VIEWS)
    op.execute(DEFAULT_PRIVS)


def downgrade() -> None:
    for name in reversed(list(VIEWS)):
        op.execute(f"drop view if exists {name}")
    op.execute(RESTORE_0004)
