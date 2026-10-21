"""Commitment Done and task cycles (R2 phase 7, fixes A and B).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-19

Why:
- Fix B. The Commitments view has a "Mark done" button. The web role has no table grant, so it
  needs one more SECURITY DEFINER function, `saley_commitment_done`, built like the four of
  migration 0006: search_path pinned, execute revoked from PUBLIC and granted to `saley_ro`,
  a role check on the first line. It changes `commitment.state` and skips the reminders still
  waiting for that commitment. Nothing else.
- Fix A. A tick on a task must not hide the task for ever. `v_task_inputs` gets `done_on`: for
  each live `task_done` role, the newest day it was ticked (team time). The rules hide a task
  only when it was ticked on or after the day of its cycle (the due day or the anchor date).
  The column is added at the end, so the view keeps its other columns and its grant.
Migrations 0006 and 0007 are not edited.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIGNATURE = "saley_commitment_done(text, bigint)"

# The view of migration 0007, plus `done_on` at the end.
TASK_INPUTS = r"""
select c.id as contact_id, c.sheet_row_key, c.name, c.company, c.industry, c.owner, c.stage,
       c.priority, c.is_p1, c.has_email, c.email, c.contacted, c.connected, c.li_requested,
       c.li_status, c.first_contact, c.first_contact_date, c.li_connected_date,
       c.email1_date, c.email2_date, c.email3_date, c.li_dm_date, c.last_step_date,
       c.meeting_date, c.meeting_status, c.reconnect_due, c.reconnect_due_on, c.is_p1_waiting,
       coalesce((select array_agg(distinct o.role) from sync_outbox o
                 where o.row_key = c.sheet_row_key and o.state in ('pending','held')),
                '{}'::text[]) as open_roles,
       coalesce((select array_agg(distinct d.role) from decision d
                 where d.row_key = c.sheet_row_key and d.kind = 'task_done'
                   and d.undone_at is null),
                '{}'::text[]) as done_tasks
       , ct.application_bucket, c.owner_source, ct.email1_sent, ct.email2_sent,
       ct.email3_sent, ct.li_dm_sent
"""

DONE_ON = r"""
       , coalesce((select jsonb_object_agg(x.role, x.day)
                   from (select d.role,
                                max((d.at at time zone 'Asia/Kolkata')::date) as day
                         from decision d
                         where d.row_key = c.sheet_row_key and d.kind = 'task_done'
                           and d.undone_at is null
                         group by d.role) x),
                  '{}'::jsonb) as done_on
"""

FROM = "from v_contact_stage c join contact ct on ct.id = c.id"

COMMITMENT_DONE = r"""
create or replace function saley_commitment_done(p_user text, p_commitment_id bigint)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
  v_user users%rowtype;
  v_c commitment%rowtype;
begin
  select * into v_user from users where short = p_user and role in ('approver','admin');
  if not found then
    return jsonb_build_object('ok', false, 'code', 'not_allowed');
  end if;
  select * into v_c from commitment where id = p_commitment_id for update;
  if not found then
    return jsonb_build_object('ok', false, 'code', 'not_found');
  end if;
  if v_c.state = 'done' then
    return jsonb_build_object('ok', true, 'commitment_id', v_c.id, 'already', true);
  end if;
  if v_c.state <> 'open' then
    return jsonb_build_object('ok', false, 'code', 'not_open');
  end if;
  update commitment set state = 'done' where id = v_c.id;
  update reminder set state = 'skipped' where commitment_id = v_c.id and state = 'pending';
  return jsonb_build_object('ok', true, 'commitment_id', v_c.id, 'already', false);
end
$fn$
"""

GRANT_RO = (
    r"""
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'saley_ro') then
    grant select on v_task_inputs to saley_ro;
    grant execute on function """
    + SIGNATURE
    + r""" to saley_ro;
  end if;
end $$;
"""
)

# Same step as migration 0006: `saley` owns the function when this run may make that so.
MOVE_OWNER = (
    r"""
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'saley')
     and current_user <> 'saley'
     and pg_has_role(current_user, 'saley', 'member') then
    alter function """
    + SIGNATURE
    + r""" owner to saley;
  else
    raise notice 'commitment function stays with %: run deploy/sql/roles.sql', current_user;
  end if;
end $$;
"""
)


def upgrade() -> None:
    op.execute(f"create or replace view v_task_inputs as {TASK_INPUTS}{DONE_ON}{FROM}")
    op.execute(COMMITMENT_DONE)
    op.execute(f"revoke all on function {SIGNATURE} from public")
    op.execute(MOVE_OWNER)
    op.execute(GRANT_RO)


# H4: drop as the function owner when the migrator is a member of it (see migration 0006).
DROP_FUNCTION = (
    r"""
do $$
declare
  o name;
begin
  select proowner::regrole::name into o from pg_proc
   where proname = 'saley_commitment_done' and pronamespace = 'public'::regnamespace limit 1;
  if o is not null and o <> current_user and pg_has_role(current_user, o, 'member') then
    execute format('set local role %I', o);
  end if;
  drop function if exists """
    + SIGNATURE
    + r""";
  reset role;
exception when insufficient_privilege then
  raise exception 'run this downgrade as the owner of the saley_* functions (deploy/sql/roles.sql)';
end $$;
"""
)


def downgrade() -> None:
    op.execute(DROP_FUNCTION)
    # A view cannot lose a column with `create or replace`: drop it and make it again.
    op.execute("drop view v_task_inputs")
    op.execute(f"create view v_task_inputs as {TASK_INPUTS}{FROM}")
    op.execute(
        "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
        "grant select on v_task_inputs to saley_ro; end if; end $$"
    )
