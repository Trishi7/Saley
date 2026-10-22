"""The column vocabulary view for the Done value (R5 ticket T3, D153).

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-21

Why (D153): a Done tick on a task writes a value the column ALREADY uses ("completed", not a
new word). The web role reads views only (0005), so it needs a view of the values the latest
Outreach PoCs read holds per role. `contact.cells` keeps the cleaned text of every mapped cell
(role -> text) for the people still in the sheet, so the view counts those.

Only the roles a Done tick can write are listed: other cells (names, notes, emails) have no
reason to reach the web role through this view.

Grouped undo (T3): a Done can write several cells (li_first writes three), and the dashboard
undoes with ONE click on one id. `decision.act_txid` holds `txid_current()` of the transaction
that inserted the row, so all cells of one act share it. `saley_undo_group` undoes every live
member of the group of one id, by calling `saley_undo` for each, all or nothing: the first
refusal rolls the whole group back (a sub-block) and is returned as it came. A group of one
(an inline single-cell edit) goes straight to `saley_undo`, so it behaves as before. For a
group of several cells only the signer may undo. Old rows keep `act_txid` null (no group).

Commitment Done is for the owner only (T4, D155). `saley_commitment_done` returns
`{ok: false, code: 'not_owner', owner_name}` when `p_user` is not the commitment owner. An admin
is not an exception. It writes nothing: no decision, no state change, no reminder change.
`saley_undo` returns `{ok: false, code: 'not_signer'}` when a commitment Done is undone by
anyone except the person who signed it. `saley_undo` is changed with `pg_get_functiondef`, as
in 0022, so a later fix to its body is not lost. The downgrade puts the 0009 body of
`saley_commitment_done` back and removes the signer check.

Email only into a blank cell (T4, D154). `saley_act` refuses an `email_write` when the current
Email cell of a target is not blank, with the code `email_present`. The web layer checks the same
before the call; this guard stops a second caller (chat, a race) that skips that check. It runs
after the cell lock and also counts a live earlier add (not undone, push not failed) and a bad
shape (`bad_email`, D169). It is
patched in with `pg_get_functiondef`, like `saley_undo`; the downgrade takes it out again.
The view is made by the owner of the table, as in 0018 and 0019.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


VIEW = r"""
create or replace view v_column_vocab as
select kv.key as role, kv.value as value, count(*)::int as n
from contact c
cross join lateral jsonb_each_text(coalesce(c.cells, '{}'::jsonb)) as kv
where c.removed_at is null
  and kv.key in ('meeting_status', 'first_contact', 'first_contact_type',
                 'first_contact_date', 'li_connected_date', 'meeting_date')
  and btrim(kv.value) <> ''
group by kv.key, kv.value
"""

ADD_COLUMN = "alter table decision add column if not exists act_txid bigint"
# The default is set AFTER the column exists, so old rows stay null and are never grouped.
SET_DEFAULT = "alter table decision alter column act_txid set default txid_current()"

UNDO_GROUP_SIG = "saley_undo_group(text, bigint, text)"
SALEY_UNDO_GROUP = r"""
create or replace function saley_undo_group(p_user text, p_write_id bigint,
                                            p_surface text default 'dashboard')
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
  v_d decision%rowtype;
  v_ids bigint[];
  v_id bigint;
  v_res jsonb;
  v_fail jsonb;
  v_cancelled boolean := false;
begin
  select * into v_d from decision
   where id = p_write_id and kind in ('column_write','task_done');
  if not found or v_d.act_txid is null then
    return saley_undo(p_user, p_write_id, p_surface);
  end if;
  -- Same txid AND same `at` (fixed per transaction): a txid reused after a restore never
  -- joins. Members already undone are skipped. A requested member that is undone itself
  -- gets the plain refusal from saley_undo. One lock order (by id) avoids deadlocks.
  if v_d.undone_at is not null then
    return saley_undo(p_user, p_write_id, p_surface);
  end if;
  select array_agg(id order by id) into v_ids from decision
   where act_txid = v_d.act_txid and at = v_d.at and undone_at is null
     and user_id is not distinct from v_d.user_id
     and kind in ('column_write','task_done');
  if cardinality(v_ids) = 1 then
    return saley_undo(p_user, p_write_id, p_surface);
  end if;
  -- A group is one act of one person: only that person may undo all of it.
  if not exists (select 1 from users where short = p_user and id = v_d.user_id) then
    return jsonb_build_object('ok', false, 'code', 'not_allowed');
  end if;
  begin
    foreach v_id in array v_ids loop
      v_res := saley_undo(p_user, v_id, p_surface);
      if not coalesce((v_res ->> 'ok')::boolean, false) then
        v_fail := v_res;
        raise exception 'group undo refused' using errcode = 'SU001';
      end if;
      v_cancelled := v_cancelled or coalesce((v_res ->> 'cancelled')::boolean, false);
    end loop;
  exception when sqlstate 'SU001' then
    return v_fail;  -- the sub-block is rolled back: no member stays half undone
  end;
  return jsonb_build_object('ok', true, 'write_id', p_write_id, 'cancelled', v_cancelled,
                            'undone', to_jsonb(v_ids));
end
$fn$
"""

MOVE_OWNER = (
    r"""
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'saley')
     and current_user <> 'saley'
     and pg_has_role(current_user, 'saley', 'member') then
    alter function """
    + UNDO_GROUP_SIG
    + r""" owner to saley;
  else
    raise notice 'new function stays with %: run deploy/sql/roles.sql', current_user;
  end if;
end $$;
"""
)


COMMITMENT_SIG = "saley_commitment_done(text, bigint, text, integer)"
COMMITMENT_DONE = r"""
create or replace function saley_commitment_done(
  p_user text, p_commitment_id bigint, p_surface text default 'dashboard',
  p_undo_minutes integer default 10)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
  v_user users%rowtype;
  v_c commitment%rowtype;
  v_owner_name text;
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
  -- D155: only the owner marks a promise done. An admin is not an exception. Nothing is written.
  if v_c.owner <> v_user.id then
    select name into v_owner_name from users where id = v_c.owner;
    return jsonb_build_object('ok', false, 'code', 'not_owner', 'owner_name', v_owner_name);
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

# D155: the signer check goes in front of the "already undone" answer of saley_undo.
UNDO_NEEDLE = """  if v_d.undone_at is not null then
    return jsonb_build_object('ok', false, 'code', 'already_undone');"""
UNDO_SIGNER = """  if v_d.kind = 'commitment_done' and v_d.user_id is distinct from v_user.id then
    return jsonb_build_object('ok', false, 'code', 'not_signer');
  end if;
"""


def _patch_undo(old: str, new: str) -> str:
    """Rewrite the live text of saley_undo. A missing or repeated pattern stops the migration."""
    q_old = old.replace("'", "''")
    q_new = new.replace("'", "''")
    return f"""
do $$
declare
  def text := pg_get_functiondef('saley_undo(text, bigint, text)'::regprocedure);
begin
  if position('{q_old}' in def) = 0
     or position('{q_old}' in substr(def, position('{q_old}' in def) + 1)) > 0 then
    raise exception '0023: saley_undo pattern not found once';
  end if;
  execute replace(def, '{q_old}', '{q_new}');
end $$;
"""


ACT_SIG = "saley_act(text, text, text, jsonb, jsonb, text, integer, integer, boolean)"
ACT_NEEDLE = """    perform pg_advisory_xact_lock(hashtext('poc' || (v_w ->> 'row_key') || (v_w ->> 'role')));
  end loop;
"""
# After the cell locks: a second caller waits for the first and then sees its decision. The
# contact row alone is stale until the next sheet sync, so a live earlier add counts too (D154).
# Shape check (D169): ASCII only, one dot or more in the domain, at most 200 characters.
ACT_EMAIL = r"""  if p_action = 'email_write' then
    if exists (select 1 from jsonb_array_elements(v_work) x
                where x ->> 'role' = 'email'
                  and (length(x ->> 'value') > 200 or (x ->> 'value') !~
                       '^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$')) then
      return jsonb_build_object('ok', false, 'code', 'bad_email');
    end if;
    if exists (select 1 from contact where id = any(v_ids) and removed_at is null
                  and btrim(coalesce(email, '')) <> '')
       or exists (select 1 from jsonb_array_elements(v_work) x
                    join decision d on d.tab = 'poc' and d.row_key = x ->> 'row_key'
                     and d.kind = 'column_write' and d.role = 'email'
                     and d.undone_at is null and d.at > now() - interval '1 day'
                    left join sync_outbox o on o.decision_id = d.id and o.op = 'write'
                   where x ->> 'role' = 'email'
                     and coalesce(o.state, '') not in ('failed', 'cancelled')) then
      return jsonb_build_object('ok', false, 'code', 'email_present');
    end if;
  end if;
"""


def _patch_act(old: str, new: str) -> str:
    """Rewrite the live text of saley_act. A missing or repeated pattern stops the migration."""
    q_old = old.replace("'", "''")
    q_new = new.replace("'", "''")
    return f"""
do $$
declare
  def text := pg_get_functiondef('{ACT_SIG}'::regprocedure);
begin
  if position('{q_old}' in def) = 0
     or position('{q_old}' in substr(def, position('{q_old}' in def) + 1)) > 0 then
    raise exception '0023: saley_act pattern not found once';
  end if;
  execute replace(def, '{q_old}', '{q_new}');
end $$;
"""


def _old_commitment_done() -> str:
    """The 0009 body, to put back on downgrade."""
    path = Path(__file__).with_name("0009_review_fixes.py")
    spec = importlib.util.spec_from_file_location("_old_0009_review_fixes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.COMMITMENT_DONE)


GRANT = (
    "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
    "grant select on v_column_vocab to saley_ro; end if; end $$"
)
GRANT_UNDO_GROUP = (
    "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
    f"grant execute on function {UNDO_GROUP_SIG} to saley_ro; end if; end $$"
)


def upgrade() -> None:
    op.execute(VIEW)
    op.execute(GRANT)
    op.execute(ADD_COLUMN)
    op.execute(SET_DEFAULT)
    op.execute(SALEY_UNDO_GROUP)
    op.execute(f"revoke all on function {UNDO_GROUP_SIG} from public")
    op.execute(MOVE_OWNER)
    op.execute(GRANT_UNDO_GROUP)
    # T4, D155: owner-only Done and signer-only Undo for a commitment.
    op.execute(COMMITMENT_DONE)
    op.execute(_patch_undo(UNDO_NEEDLE, UNDO_SIGNER + UNDO_NEEDLE))
    op.execute(_patch_act(ACT_NEEDLE, ACT_NEEDLE + ACT_EMAIL))


def downgrade() -> None:
    op.execute(_old_commitment_done())
    op.execute(_patch_undo(UNDO_SIGNER + UNDO_NEEDLE, UNDO_NEEDLE))
    op.execute(_patch_act(ACT_NEEDLE + ACT_EMAIL, ACT_NEEDLE))
    op.execute(f"drop function if exists {UNDO_GROUP_SIG}")
    op.execute("alter table decision drop column if exists act_txid")
    op.execute("drop view if exists v_column_vocab")
