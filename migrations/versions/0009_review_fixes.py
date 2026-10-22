"""R2 review fixes: value checks, undo rules, write state in views, commitment signature.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-19

Why (pipeline stage 5, findings H1, H5, H6, M8, M11):
- H1 `saley_act` refuses a text that starts with `=`, `+`, `-` or `@` (Google would run it as a
  formula under USER_ENTERED). It also checks the date shape and the First Contact words, so the
  SQL door is as strict as the web layer (defence in depth).
- H6 `saley_undo` refuses `superseded` when a later live write sits on the same cell. The
  `already` shortcut in `saley_act` no longer matches a write whose outbox row failed: a new
  write is queued instead.
- H5 `v_decision` gets `sync_state`, `sync_error`, `sync_op` (the newest outbox row of the
  write or of its undo) and an `undo_failed` undo state. `v_pending_write` keeps failed rows
  visible until the cell has a newer good write, or (for a write) the decision is undone.
- M11 `saley_commitment_done` signs: it writes `decision(kind='commitment_done')` with a
  surface and an undo window. `saley_undo` reopens the commitment and its skipped reminders.
- M8 indexes for the hot lookups and the foreign keys.
Migrations 0006 to 0008 are not edited (only their downgrade steps, for H4).

Owner (R6, Q-R6-4): migrations run as saley_admin and never switch role. saley_admin is a
member of `saley` (D244), so it may replace or drop the saley_* functions that `saley` owns,
and the MOVE_OWNER step gives new functions to `saley` at the end. saley_admin owns every
table and view (docs/01 §11)."""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HEAD = """
language plpgsql
security definer
set search_path = public, pg_temp
"""

NEW_SIG = "saley_commitment_done(text, bigint, text, integer)"
OLD_SIG = "saley_commitment_done(text, bigint)"
ALL_SIGS = (
    "saley_act(text, text, text, jsonb, jsonb, text, integer, integer, boolean)",
    "saley_undo(text, bigint, text)",
    "saley_confirm(text, bigint, text)",
    "saley_dismiss(text, bigint, text)",
)


# ------------------------------------------------------------------ views

V_DECISION = r"""
select d.id, d.kind, d.choice, d.reason, d.at, d.surface, d.tab, d.row_key, d.role,
       d.before, d.after, d.state, d.undo_until, d.undone_at, d.proposal_id, d.undo_of,
       u.short as user_short, u.name as user_name,
       ub.name as undone_by_name,
       cb.name as confirmed_by_name, d.confirmed_at, d.confirmed_surface,
       c.id as contact_id, c.name as person,
       case when d.undone_at is not null and ob.op = 'restore' and ob.state = 'failed'
                 then 'undo_failed'
            when d.undone_at is not null then 'undone'
            when d.undo_until is not null and d.undo_until > now() then 'open'
            else 'closed' end as undo_state,
       ob.state as sync_state, ob.error as sync_error, ob.op as sync_op
from decision d
left join users u on u.id = d.user_id
left join users ub on ub.id = d.undone_by
left join users cb on cb.id = d.confirmed_by
left join contact c on c.sheet_row_key = d.row_key
left join lateral (
  select o.state, o.error, o.op from sync_outbox o
   where o.decision_id = d.id
      or o.decision_id in (select x.id from decision x where x.undo_of = d.id)
   order by o.id desc limit 1) ob on true
"""

V_PENDING_WRITE = r"""
select o.id as outbox_id, o.decision_id, o.op, o.tab, o.row_key, o.role, o.value, o.state,
       o.state in ('pending','held') as open, o.created_at, u.short as user_short
from sync_outbox o
join decision d on d.id = o.decision_id
left join users u on u.id = d.user_id
where o.state in ('pending','held')
   or (o.state = 'failed'
       and (o.op = 'restore' or d.undone_at is null)
       and not exists (select 1 from sync_outbox n
                        where n.tab = o.tab and n.row_key = o.row_key and n.role = o.role
                          and n.id > o.id and n.state = 'done'))
"""

# ------------------------------------------------------------------ functions

CELL_RULES = r"""
      -- H1: a leading = + - @ makes Google run the text as a formula.
      if ltrim(v_value) ~ '^[=+@-]' then
        return jsonb_build_object('ok', false, 'code', 'bad_value');
      end if;
      if v_role in ('first_contact_date','li_connected_date','meeting_date')
         and v_value !~ '^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$' then
        return jsonb_build_object('ok', false, 'code', 'bad_value');
      end if;
      if v_role = 'first_contact' and upper(v_value) not in ('TRUE','FALSE','VR','SID') then
        return jsonb_build_object('ok', false, 'code', 'bad_value');
      end if;
"""


def _load_old(name: str) -> ModuleType:
    """Import an earlier migration file by path, to put its function bodies back."""
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"_old_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _new_act() -> str:
    old = _load_old("0006_writes.py").SALEY_ACT
    after_lock = """        return jsonb_build_object('ok', false, 'code', 'locked', 'role', v_role);
      end if;
    end loop;"""
    if after_lock not in old:  # pragma: no cover - guards a changed 0006 text
        raise RuntimeError("0006 saley_act text changed; update 0009")
    body = old.replace(
        after_lock, after_lock.replace("    end loop;", "") + CELL_RULES + "    end loop;"
    )
    shortcut = """    select d.id, d.undo_until,
           not exists (select 1 from decision l
                        where l.tab = d.tab and l.row_key = d.row_key and l.role = d.role
                          and l.id > d.id and l.undone_at is null
                          and l.kind in ('column_write','task_done')) as latest
      into v_prior from decision d"""
    fixed = """    select d.id, d.undo_until,
           not exists (select 1 from decision l
                        where l.tab = d.tab and l.row_key = d.row_key and l.role = d.role
                          and l.id > d.id and l.undone_at is null
                          and l.kind in ('column_write','task_done')) as latest,
           exists (select 1 from sync_outbox f
                    where f.decision_id = d.id and f.state = 'failed') as failed
      into v_prior from decision d"""
    if shortcut not in body or "if found and v_prior.latest then" not in body:  # pragma: no cover
        raise RuntimeError("0006 saley_act text changed; update 0009")
    body = body.replace(shortcut, fixed)
    return body.replace(
        "if found and v_prior.latest then",
        "if found and v_prior.latest and not v_prior.failed then",
    )


def _new_undo() -> str:
    old = _load_old("0006_writes.py").SALEY_UNDO
    kinds = "where id = p_write_id and kind in ('column_write','task_done');"
    window = """      'at', to_char(v_d.undo_until at time zone 'Asia/Kolkata', 'HH24:MI'));
  end if;
"""
    done_branch = """  elsif v_d.kind = 'task_done' then"""
    for needle in (kinds, window, done_branch):
        if needle not in old:  # pragma: no cover - guards a changed 0006 text
            raise RuntimeError("0006 saley_undo text changed; update 0009")
    superseded = r"""
  -- H6: a later live write sits on this cell, so putting the old value back would erase it.
  if v_d.kind in ('column_write','task_done') and exists (
       select 1 from decision l
        where l.tab = v_d.tab and l.row_key = v_d.row_key and l.role = v_d.role
          and l.id > v_d.id and l.undone_at is null
          and l.kind in ('column_write','task_done')
          and not exists (select 1 from sync_outbox f
                           where f.decision_id = l.id and f.state in ('failed','cancelled'))) then
    return jsonb_build_object('ok', false, 'code', 'superseded');
  end if;
"""
    reopen = r"""  elsif v_d.kind = 'commitment_done' then
    -- M11: reopen the promise and the reminders this Done skipped.
    update commitment set state = 'open'
     where id = substring(v_d.row_key from 12)::bigint and state = 'done';
    update reminder set state = 'pending'
     where state = 'skipped'
       and id = any(string_to_array(coalesce(v_d.before, ''), ',')::bigint[]);
"""
    body = old.replace(
        kinds,
        "where id = p_write_id\n     and kind in ('column_write','task_done','commitment_done');",
    )
    body = body.replace(window, window + superseded)
    return body.replace(done_branch, reopen + done_branch)


COMMITMENT_DONE = (
    r"""
create or replace function saley_commitment_done(
  p_user text, p_commitment_id bigint, p_surface text default 'dashboard',
  p_undo_minutes integer default 10)
returns jsonb
"""
    + HEAD
    + r"""
as $fn$
declare
  v_user users%rowtype;
  v_c commitment%rowtype;
  v_prior record;
  v_id bigint;
  v_until timestamptz;
  v_skipped text;
  v_mins integer := least(greatest(coalesce(p_undo_minutes, 10), 1), 60);
begin
  if p_surface is null or p_surface not in ('dashboard','chat','card') then
    return jsonb_build_object('ok', false, 'code', 'bad_request');
  end if;
  select * into v_user from users where short = p_user and role in ('approver','admin');
  if not found then
    return jsonb_build_object('ok', false, 'code', 'not_allowed');
  end if;
  select * into v_c from commitment where id = p_commitment_id for update;
  if not found then
    return jsonb_build_object('ok', false, 'code', 'not_found');
  end if;
  if v_c.state = 'done' then
    select d.id, d.undo_until into v_prior from decision d
     where d.kind = 'commitment_done' and d.row_key = 'commitment:' || v_c.id
       and d.undone_at is null order by d.id desc limit 1;
    return jsonb_build_object('ok', true, 'commitment_id', v_c.id, 'already', true,
                              'write_id', v_prior.id, 'undo_until', v_prior.undo_until);
  end if;
  if v_c.state <> 'open' then
    return jsonb_build_object('ok', false, 'code', 'not_open');
  end if;
  select string_agg(id::text, ',') into v_skipped
    from reminder where commitment_id = v_c.id and state = 'pending';
  update commitment set state = 'done' where id = v_c.id;
  update reminder set state = 'skipped' where commitment_id = v_c.id and state = 'pending';
  insert into decision (kind, choice, user_id, surface, tab, row_key, role, before, state,
                        undo_until)
  values ('commitment_done', 'done', v_user.id, p_surface, 'commitment',
          'commitment:' || v_c.id, 'done', v_skipped, 'active',
          now() + make_interval(mins => v_mins))
  returning id, undo_until into v_id, v_until;
  return jsonb_build_object('ok', true, 'commitment_id', v_c.id, 'already', false,
                            'write_id', v_id, 'undo_until', v_until);
end
$fn$
"""
)

MOVE_OWNER = (
    r"""
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'saley')
     and current_user <> 'saley'
     and pg_has_role(current_user, 'saley', 'member') then
    alter function """
    + NEW_SIG
    + r""" owner to saley;
  else
    raise notice 'commitment function stays with %: run deploy/sql/roles.sql', current_user;
  end if;
end $$;
"""
)

GRANT_RO = (
    r"""
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'saley_ro') then
    grant execute on function """
    + NEW_SIG
    + r""" to saley_ro;
  end if;
end $$;
"""
)

REGRANT_VIEW = (
    "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
    "grant select on v_decision to saley_ro; end if; end $$"
)

KIND_NEW = (
    "kind in ('column_write','task_done','dismiss','strategy_decision','undo','commitment_done')"
)
KIND_OLD = "kind in ('column_write','task_done','dismiss','strategy_decision','undo')"

INDEXES = (
    "create index ix_decision_task_done on decision (row_key, role) "
    "where kind = 'task_done' and undone_at is null",
    "create index ix_outbox_row_open on sync_outbox (row_key) where state in ('pending','held')",
    "create index ix_reminder_commitment on reminder (commitment_id)",
    "create index ix_reminder_for_user on reminder (for_user)",
    "create index ix_decision_proposal on decision (proposal_id)",
    "create index ix_decision_undo_of on decision (undo_of)",
)
INDEX_NAMES = (
    "ix_decision_task_done",
    "ix_outbox_row_open",
    "ix_reminder_commitment",
    "ix_reminder_for_user",
    "ix_decision_proposal",
    "ix_decision_undo_of",
)


def upgrade() -> None:
    op.execute("alter table decision drop constraint ck_decision_kind")
    op.execute(f"alter table decision add constraint ck_decision_kind check ({KIND_NEW})")
    for statement in INDEXES:
        op.execute(statement)
    op.execute(f"create or replace view v_decision as {V_DECISION}")
    op.execute(f"create or replace view v_pending_write as {V_PENDING_WRITE}")

    op.execute(_new_act())
    op.execute(_new_undo())
    op.execute(COMMITMENT_DONE)
    op.execute(f"drop function if exists {OLD_SIG}")
    op.execute(f"revoke all on function {NEW_SIG} from public")
    op.execute(MOVE_OWNER)
    op.execute(GRANT_RO)


def downgrade() -> None:
    old6 = _load_old("0006_writes.py")
    old8 = _load_old("0008_commitment_done_cycle.py")
    op.execute("delete from decision where kind = 'commitment_done'")
    op.execute("alter table decision drop constraint ck_decision_kind")
    op.execute(f"alter table decision add constraint ck_decision_kind check ({KIND_OLD})")
    for name in INDEX_NAMES:
        op.execute(f"drop index if exists {name}")
    op.execute("drop view v_decision")
    op.execute(f"create view v_decision as {old6.VIEWS['v_decision']}")
    op.execute(REGRANT_VIEW)
    op.execute(f"create or replace view v_pending_write as {old6.VIEWS['v_pending_write']}")

    op.execute(f"drop function if exists {NEW_SIG}")
    op.execute(old6.SALEY_ACT)
    op.execute(old6.SALEY_UNDO)
    op.execute(old8.COMMITMENT_DONE)
    op.execute(f"revoke all on function {OLD_SIG} from public")
    op.execute(
        "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
        f"grant execute on function {OLD_SIG} to saley_ro; end if; end $$"
    )
