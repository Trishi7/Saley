"""R2 writes: decision, outbox, proposals, facts, commitments, and the act functions.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-19

Why: D24 says an instruction from an approver IS the approval, so the dashboard writes at
once with a signature (D45). Only the worker holds the sheet credential, and the web logs in
as `saley_ro`, which has no table grant. So the web calls four SECURITY DEFINER functions:
`saley_act`, `saley_undo`, `saley_confirm`, `saley_dismiss`. Each function:
- pins `search_path = public, pg_temp`;
- is revoked from PUBLIC and granted to `saley_ro` only;
- checks the user's role in `users` (approver or admin);
- takes `pg_advisory_xact_lock(hashtext(tab || row_key || role))` for each cell it writes;
- accepts a fixed list of actions and column roles (docs/10 locks, by ROLE, never by letter);
- writes only its own rows: decision, sync_outbox, touch (task_done), proposal.
`saley_ro` keeps zero table grants and gets EXECUTE on these four functions only.

Function owner: the owner runs every function with its own rights, so `saley` (the read-write
role) is the better owner. The migration moves the functions to `saley` when the migrating
role may do that (`pg_has_role(current_user, 'saley', 'member')`). On this laptop `saley_admin`
is not a member of `saley`, so the owner stays `saley_admin`, the owner of the tables.
`deploy/sql/roles.sql` repeats the step as superuser (the pattern of 0005).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = r"""
create table proposal (
  id bigserial primary key,
  type text not null,
  card_key text not null,
  entity_type text,
  entity_id integer,
  payload jsonb not null default '{}'::jsonb,
  evidence jsonb not null default '{}'::jsonb,
  rank integer not null default 0,
  due_at timestamptz,
  state text not null default 'open',
  shown_at timestamptz,
  created_at timestamptz not null default now(),
  constraint uq_proposal_card_key unique (card_key),
  constraint ck_proposal_state check (state in ('open','acted','dismissed','expired'))
);
create index ix_proposal_state_rank on proposal (state, rank);

create table decision (
  id bigserial primary key,
  proposal_id bigint references proposal(id),
  kind text not null,
  choice text,
  reason text,
  user_id integer references users(id),
  at timestamptz not null default now(),
  surface text,
  tab text,
  row_key text,
  role text,
  before text,
  after text,
  instruction_key text,
  state text not null default 'active',
  undo_until timestamptz,
  undone_at timestamptz,
  undone_by integer references users(id),
  undo_of bigint references decision(id),
  confirmed_by integer references users(id),
  confirmed_at timestamptz,
  confirmed_surface text,
  constraint ck_decision_kind check (
    kind in ('column_write','task_done','dismiss','strategy_decision','undo')),
  constraint ck_decision_state check (state in ('proposed','confirmed','active')),
  constraint ck_decision_surface check (
    surface is null or surface in ('dashboard','chat','card'))
);
create index ix_decision_user_at on decision (user_id, at desc);
create index ix_decision_cell on decision (tab, row_key, role, at desc);
-- D48: one live write per instruction. An undone write frees its key.
create unique index uq_decision_active_instruction on decision (instruction_key)
  where undone_at is null and kind in ('column_write','task_done');

create table sync_outbox (
  id bigserial primary key,
  decision_id bigint not null references decision(id),
  op text not null default 'write',
  tab text not null,
  row_key text not null,
  role text not null,
  value text,
  state text not null default 'pending',
  tries integer not null default 0,
  error text,
  next_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint ck_outbox_op check (op in ('write','restore')),
  constraint ck_outbox_state check (state in ('pending','done','failed','held','cancelled'))
);
create index ix_outbox_pending on sync_outbox (state, next_at);
create index ix_outbox_decision on sync_outbox (decision_id);
create index ix_outbox_cell on sync_outbox (tab, row_key, role) where state in ('pending','held');

create table sync_audit (
  id bigserial primary key,
  outbox_id bigint not null references sync_outbox(id),
  before text,
  after text,
  at timestamptz not null default now(),
  by text
);
create index ix_sync_audit_outbox on sync_audit (outbox_id);

create table fact (
  id bigserial primary key,
  entity_type text not null,
  entity_id integer not null,
  kind text not null,
  value jsonb not null default '{}'::jsonb,
  source_type text not null,
  source_ref text,
  author text,
  observed_at timestamptz not null default now(),
  confidence smallint,
  expires_at timestamptz,
  superseded_by bigint references fact(id)
);
create index ix_fact_entity on fact (entity_type, entity_id, kind, observed_at desc);

create table commitment (
  id bigserial primary key,
  owner integer not null references users(id),
  text text not null,
  due date,
  source_type text,
  source_ref text,
  state text not null default 'open',
  reminders_sent integer not null default 0,
  last_reminded_on date,
  in_pack boolean not null default false,
  created_at timestamptz not null default now(),
  constraint ck_commitment_state check (state in ('open','done','dropped'))
);
create index ix_commitment_owner on commitment (owner, state, due);

create table reminder (
  id bigserial primary key,
  for_user integer not null references users(id),
  text text not null,
  fire_at timestamptz not null,
  entity_id integer,
  commitment_id bigint references commitment(id),
  kind text not null default 'due_day',
  state text not null default 'pending',
  constraint ck_reminder_kind check (kind in ('before','due_day')),
  constraint ck_reminder_state check (state in ('pending','sent','skipped'))
);
create index ix_reminder_fire on reminder (state, fire_at);

create table ask (
  id bigserial primary key,
  to_user integer not null references users(id),
  question text not null,
  entity_id integer,
  asked_at timestamptz not null default now(),
  answered_at timestamptz,
  answer_fact_id bigint references fact(id),
  msg_id text
);
create index ix_ask_open on ask (to_user) where answered_at is null;

create table user_seen (
  user_id integer not null references users(id),
  item_type text not null,
  item_id bigint not null,
  seen_at timestamptz not null default now(),
  query_id text,
  primary key (user_id, item_type, item_id)
);
"""

DROP_TABLES = (
    "user_seen",
    "ask",
    "reminder",
    "commitment",
    "fact",
    "sync_audit",
    "sync_outbox",
    "decision",
    "proposal",
)

VIEWS = {
    "v_decision": r"""
select d.id, d.kind, d.choice, d.reason, d.at, d.surface, d.tab, d.row_key, d.role,
       d.before, d.after, d.state, d.undo_until, d.undone_at, d.proposal_id, d.undo_of,
       u.short as user_short, u.name as user_name,
       ub.name as undone_by_name,
       cb.name as confirmed_by_name, d.confirmed_at, d.confirmed_surface,
       c.id as contact_id, c.name as person,
       case when d.undone_at is not null then 'undone'
            when d.undo_until is not null and d.undo_until > now() then 'open'
            else 'closed' end as undo_state
from decision d
left join users u on u.id = d.user_id
left join users ub on ub.id = d.undone_by
left join users cb on cb.id = d.confirmed_by
left join contact c on c.sheet_row_key = d.row_key
""",
    "v_inbox": r"""
select 'proposal'::text as item_kind, p.id, p.type, p.card_key as ref, p.payload, p.rank,
       p.due_at as at, null::text as for_user
from proposal p where p.state = 'open'
union all
select 'confirm', d.id, d.kind, d.choice, jsonb_build_object('reason', d.reason), 0,
       d.at, null
from decision d where d.state = 'proposed'
union all
select 'ask', a.id, 'ask', a.question, '{}'::jsonb, 0, a.asked_at, u.short
from ask a join users u on u.id = a.to_user where a.answered_at is null
""",
    "v_commitment": r"""
select m.id, u.short as owner_short, u.name as owner_name, m.text, m.due, m.source_type,
       m.source_ref, m.state, m.reminders_sent, m.last_reminded_on, m.in_pack
from commitment m join users u on u.id = m.owner
""",
    "v_fact_working_on": r"""
select distinct on (f.entity_type, f.entity_id)
       f.id, f.entity_type, f.entity_id, f.value ->> 'text' as text,
       f.value ->> 'url' as url, f.source_type, f.source_ref, f.author, f.observed_at,
       f.confidence
from fact f
where f.kind = 'working_on' and f.superseded_by is null
  and (f.expires_at is null or f.expires_at > now())
order by f.entity_type, f.entity_id, f.observed_at desc, f.id desc
""",
    "v_pending_write": r"""
select o.id as outbox_id, o.decision_id, o.op, o.tab, o.row_key, o.role, o.value, o.state,
       o.state in ('pending','held') as open, o.created_at, u.short as user_short
from sync_outbox o
join decision d on d.id = o.decision_id
left join users u on u.id = d.user_id
where o.state in ('pending','held','failed')
""",
    "v_task_inputs": r"""
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
from v_contact_stage c
""",
}

# Arguments are typed in full so GRANT, REVOKE and DROP name one function exactly.
SIGNATURES = {
    "saley_act": "saley_act(text, text, text, jsonb, jsonb, text, integer, integer, boolean)",
    "saley_undo": "saley_undo(text, bigint, text)",
    "saley_confirm": "saley_confirm(text, bigint, text)",
    "saley_dismiss": "saley_dismiss(text, bigint, text)",
}

FUNCTION_HEAD = """
language plpgsql
security definer
set search_path = public, pg_temp
"""

SALEY_ACT = (
    r"""
create or replace function saley_act(
  p_user text, p_surface text, p_action text, p_targets jsonb, p_payload jsonb, p_key text,
  p_cooldown integer default 2, p_undo_minutes integer default 10,
  p_allow_email boolean default false)
returns jsonb
"""
    + FUNCTION_HEAD
    + r"""
as $fn$
declare
  v_user users%rowtype;
  v_ids integer[];
  v_cells jsonb;
  v_cell jsonb;
  v_role text;
  v_value text;
  v_task text;
  v_work jsonb;
  v_w jsonb;
  v_todo jsonb := '[]'::jsonb;
  v_items jsonb := '[]'::jsonb;
  v_prior record;
  v_ikey text;
  v_kind text;
  v_id bigint;
  v_until timestamptz;
  v_proposal bigint;
  v_mins integer := least(greatest(coalesce(p_undo_minutes, 10), 1), 60);
begin
  if p_surface is null or p_surface not in ('dashboard','chat','card')
     or p_action is null or p_action not in ('write','email_write','task_done')
     or p_key is null or length(p_key) not between 16 and 128
     or jsonb_typeof(p_targets) is distinct from 'array'
     or jsonb_typeof(p_payload) is distinct from 'object' then
    return jsonb_build_object('ok', false, 'code', 'bad_request');
  end if;
  select * into v_user from users where short = p_user and role in ('approver','admin');
  if not found then
    return jsonb_build_object('ok', false, 'code', 'not_allowed');
  end if;
  if jsonb_array_length(p_targets) not between 1 and 50
     or exists (select 1 from jsonb_array_elements_text(p_targets) x
                where x !~ '^[0-9]{1,9}$') then
    return jsonb_build_object('ok', false, 'code', 'bad_request');
  end if;
  select array_agg(distinct x::integer) into v_ids from jsonb_array_elements_text(p_targets) x;
  if (select count(*) from contact where id = any(v_ids) and removed_at is null)
     <> cardinality(v_ids) then
    return jsonb_build_object('ok', false, 'code', 'no_target');
  end if;

  if p_action = 'task_done' then
    v_task := p_payload ->> 'task';
    if v_task is null
       or v_task not in ('research','email1','email2','email3','li_dm','reconnect',
                         'status_check') then
      return jsonb_build_object('ok', false, 'code', 'bad_request');
    end if;
    v_cells := jsonb_build_array(
      jsonb_build_object('role', 'task:' || v_task, 'value', ''));
    v_kind := 'task_done';
  else
    if jsonb_typeof(p_payload -> 'cells') is distinct from 'array'
       or jsonb_array_length(p_payload -> 'cells') not between 1 and 5 then
      return jsonb_build_object('ok', false, 'code', 'bad_request');
    end if;
    v_cells := p_payload -> 'cells';
    v_kind := 'column_write';
    for v_cell in select e from jsonb_array_elements(v_cells) e loop
      v_role := v_cell ->> 'role';
      v_value := v_cell ->> 'value';
      if jsonb_typeof(v_cell -> 'value') is distinct from 'string' or v_role is null
         or length(v_value) > 200 or v_value ~ '[[:cntrl:]]' then
        return jsonb_build_object('ok', false, 'code', 'bad_value');
      end if;
      -- docs/10 and CLAUDE.md: the identity, step and commercial blocks are never written.
      if p_action = 'write' and v_role not in (
           'first_contact','first_contact_type','first_contact_date','sid_li_added',
           'li_connected_date','meeting_date','meeting_status') then
        return jsonb_build_object('ok', false, 'code', 'locked', 'role', v_role);
      end if;
      if p_action = 'email_write' and (v_role <> 'email' or not coalesce(p_allow_email, false))
      then
        return jsonb_build_object('ok', false, 'code', 'locked', 'role', v_role);
      end if;
    end loop;
    if (select count(distinct e ->> 'role') from jsonb_array_elements(v_cells) e)
       <> jsonb_array_length(v_cells) then
      return jsonb_build_object('ok', false, 'code', 'bad_request');
    end if;
  end if;

  if p_payload ->> 'proposal_id' ~ '^[0-9]{1,15}$' then
    v_proposal := (p_payload ->> 'proposal_id')::bigint;
    if not exists (select 1 from proposal where id = v_proposal) then
      return jsonb_build_object('ok', false, 'code', 'bad_request');
    end if;
  end if;

  select jsonb_agg(jsonb_build_object('contact_id', c.id, 'row_key', c.sheet_row_key,
                                      'role', e ->> 'role', 'value', e ->> 'value')
                   order by c.sheet_row_key, e ->> 'role')
    into v_work
    from contact c cross join jsonb_array_elements(v_cells) e
   where c.id = any(v_ids) and c.removed_at is null;

  -- Lock every cell first, in one fixed order, so two callers cannot deadlock.
  for v_w in select x from jsonb_array_elements(v_work) x loop
    perform pg_advisory_xact_lock(hashtext('poc' || (v_w ->> 'row_key') || (v_w ->> 'role')));
  end loop;

  for v_w in select x from jsonb_array_elements(v_work) x loop
    v_ikey := encode(sha256(convert_to(
      p_key || '|' || (v_w ->> 'row_key') || '|' || (v_w ->> 'role'), 'UTF8')), 'hex');
    -- D48: another person acted on this cell a moment ago.
    select d.id, d.at, u.name into v_prior
      from decision d join users u on u.id = d.user_id
     where d.tab = 'poc' and d.row_key = v_w ->> 'row_key' and d.role = v_w ->> 'role'
       and d.kind in ('column_write','task_done') and d.undone_at is null
       and d.user_id <> v_user.id
       and d.at > now() - make_interval(secs => greatest(coalesce(p_cooldown, 2), 0))
     order by d.at desc limit 1;
    if found then
      return jsonb_build_object('ok', false, 'code', 'cooldown', 'by', v_prior.name,
        'at', to_char(v_prior.at at time zone 'Asia/Kolkata', 'HH24:MI'));
    end if;
    -- D48: the same instruction again is the first write, when that write is still the
    -- latest live one on the cell. After A, B, A the second A is a new write.
    select d.id, d.undo_until,
           not exists (select 1 from decision l
                        where l.tab = d.tab and l.row_key = d.row_key and l.role = d.role
                          and l.id > d.id and l.undone_at is null
                          and l.kind in ('column_write','task_done')) as latest
      into v_prior from decision d
     where d.instruction_key = v_ikey and d.undone_at is null
       and d.kind in ('column_write','task_done');
    if found and v_prior.latest then
      v_items := v_items || jsonb_build_object('write_id', v_prior.id,
        'row_key', v_w ->> 'row_key', 'role', v_w ->> 'role', 'value', v_w ->> 'value',
        'undo_until', v_prior.undo_until, 'already', true);
    else
      v_todo := v_todo || (v_w || jsonb_build_object('ikey', v_ikey,
                                                      'supersede', v_prior.id));
    end if;
  end loop;

  for v_w in select x from jsonb_array_elements(v_todo) x loop
    if v_w ->> 'supersede' is not null then
      -- Free the key of the older write; the row stays as history.
      update decision set instruction_key = instruction_key || '#' || id
       where id = (v_w ->> 'supersede')::bigint;
    end if;
    insert into decision (proposal_id, kind, choice, user_id, surface, tab, row_key, role,
                          after, instruction_key, state, undo_until)
    values (v_proposal, v_kind, case when v_kind = 'task_done' then 'done' else 'write' end,
            v_user.id, p_surface, 'poc', v_w ->> 'row_key', v_w ->> 'role', v_w ->> 'value',
            v_w ->> 'ikey', 'active', now() + make_interval(mins => v_mins))
    returning id, undo_until into v_id, v_until;
    if v_kind = 'column_write' then
      insert into sync_outbox (decision_id, op, tab, row_key, role, value, state, next_at)
      values (v_id, 'write', 'poc', v_w ->> 'row_key', v_w ->> 'role', v_w ->> 'value',
              'pending', now());
    else
      -- D49: a task Done is logged and touched; the sheet is not written.
      insert into touch (contact_id, type, date, source)
      values ((v_w ->> 'contact_id')::integer, v_task, saley_today(), 'dashboard');
    end if;
    v_items := v_items || jsonb_build_object('write_id', v_id,
      'row_key', v_w ->> 'row_key', 'role', v_w ->> 'role', 'value', v_w ->> 'value',
      'undo_until', v_until, 'already', false);
  end loop;

  if v_proposal is not null then
    update proposal set state = 'acted' where id = v_proposal and state = 'open';
  end if;
  return jsonb_build_object('ok', true, 'items', v_items);
end
$fn$
"""
)

SALEY_UNDO = (
    r"""
create or replace function saley_undo(p_user text, p_write_id bigint,
                                      p_surface text default 'dashboard')
returns jsonb
"""
    + FUNCTION_HEAD
    + r"""
as $fn$
declare
  v_user users%rowtype;
  v_d decision%rowtype;
  v_o sync_outbox%rowtype;
  v_has_outbox boolean;
  v_undo bigint;
  v_cancelled boolean := false;
begin
  if p_surface is null or p_surface not in ('dashboard','chat','card') then
    return jsonb_build_object('ok', false, 'code', 'bad_request');
  end if;
  select * into v_user from users where short = p_user and role in ('approver','admin');
  if not found then
    return jsonb_build_object('ok', false, 'code', 'not_allowed');
  end if;
  select * into v_d from decision
   where id = p_write_id and kind in ('column_write','task_done');
  if not found then
    return jsonb_build_object('ok', false, 'code', 'not_found');
  end if;
  perform pg_advisory_xact_lock(hashtext(v_d.tab || v_d.row_key || v_d.role));
  -- Lock order: outbox row, then decision row. The worker uses the same order.
  select * into v_o from sync_outbox where decision_id = v_d.id and op = 'write' for update;
  v_has_outbox := found;
  select * into v_d from decision where id = p_write_id for update;
  if v_d.undone_at is not null then
    return jsonb_build_object('ok', false, 'code', 'already_undone');
  end if;
  if v_d.undo_until is null or now() > v_d.undo_until then
    return jsonb_build_object('ok', false, 'code', 'window_closed',
      'at', to_char(v_d.undo_until at time zone 'Asia/Kolkata', 'HH24:MI'));
  end if;

  if v_d.kind = 'column_write' and v_has_outbox then
    if v_o.state in ('pending','held') then
      update sync_outbox set state = 'cancelled', updated_at = now() where id = v_o.id;
      v_cancelled := true;
    elsif v_o.state = 'done' then
      if v_d.before is null then
        return jsonb_build_object('ok', false, 'code', 'no_before');
      end if;
      insert into decision (kind, choice, user_id, surface, tab, row_key, role, before, after,
                            state, undo_of)
      values ('undo', 'undo', v_user.id, p_surface, v_d.tab, v_d.row_key, v_d.role,
              v_d.after, v_d.before, 'active', v_d.id)
      returning id into v_undo;
      insert into sync_outbox (decision_id, op, tab, row_key, role, value, state, next_at)
      values (v_undo, 'restore', v_d.tab, v_d.row_key, v_d.role, v_d.before, 'pending', now());
    end if;
  elsif v_d.kind = 'task_done' then
    delete from touch where id = (
      select t.id from touch t join contact c on c.id = t.contact_id
       where c.sheet_row_key = v_d.row_key and t.source = 'dashboard'
         and t.type = substring(v_d.role from 6)
       order by t.id desc limit 1);
  end if;
  update decision set undone_at = now(), undone_by = v_user.id where id = v_d.id;
  return jsonb_build_object('ok', true, 'write_id', v_d.id, 'undo_id', v_undo,
                            'cancelled', v_cancelled);
end
$fn$
"""
)

SALEY_CONFIRM = (
    r"""
create or replace function saley_confirm(p_user text, p_decision_id bigint,
                                         p_surface text default 'dashboard')
returns jsonb
"""
    + FUNCTION_HEAD
    + r"""
as $fn$
declare
  v_user users%rowtype;
  v_d decision%rowtype;
begin
  if p_surface is null or p_surface not in ('dashboard','chat','card') then
    return jsonb_build_object('ok', false, 'code', 'bad_request');
  end if;
  select * into v_user from users where short = p_user and role in ('approver','admin');
  if not found then
    return jsonb_build_object('ok', false, 'code', 'not_allowed');
  end if;
  select * into v_d from decision
   where id = p_decision_id and kind = 'strategy_decision' for update;
  if not found then
    return jsonb_build_object('ok', false, 'code', 'not_found');
  end if;
  if v_d.state <> 'proposed' then
    return jsonb_build_object('ok', true, 'decision_id', v_d.id, 'already', true);
  end if;
  update decision
     set state = 'active', confirmed_by = v_user.id, confirmed_at = now(),
         confirmed_surface = p_surface
   where id = v_d.id;
  return jsonb_build_object('ok', true, 'decision_id', v_d.id, 'already', false);
end
$fn$
"""
)

SALEY_DISMISS = (
    r"""
create or replace function saley_dismiss(p_user text, p_proposal_id bigint,
                                         p_surface text default 'dashboard')
returns jsonb
"""
    + FUNCTION_HEAD
    + r"""
as $fn$
declare
  v_user users%rowtype;
  v_p proposal%rowtype;
  v_id bigint;
begin
  if p_surface is null or p_surface not in ('dashboard','chat','card') then
    return jsonb_build_object('ok', false, 'code', 'bad_request');
  end if;
  select * into v_user from users where short = p_user and role in ('approver','admin');
  if not found then
    return jsonb_build_object('ok', false, 'code', 'not_allowed');
  end if;
  select * into v_p from proposal where id = p_proposal_id for update;
  if not found then
    return jsonb_build_object('ok', false, 'code', 'not_found');
  end if;
  if v_p.state = 'dismissed' then
    return jsonb_build_object('ok', true, 'proposal_id', v_p.id, 'already', true);
  end if;
  if v_p.state <> 'open' then
    return jsonb_build_object('ok', false, 'code', 'not_open');
  end if;
  update proposal set state = 'dismissed' where id = v_p.id;
  insert into decision (proposal_id, kind, choice, user_id, surface, state)
  values (v_p.id, 'dismiss', 'dismiss', v_user.id, p_surface, 'active')
  returning id into v_id;
  return jsonb_build_object('ok', true, 'proposal_id', v_p.id, 'decision_id', v_id,
                            'already', false);
end
$fn$
"""
)

FUNCTIONS = (SALEY_ACT, SALEY_UNDO, SALEY_CONFIRM, SALEY_DISMISS)

LOCK_DOWN = "\n".join(f"revoke all on function {sig} from public;" for sig in SIGNATURES.values())

GRANT_RO = (
    r"""
do $$
declare
  v text;
begin
  if not exists (select 1 from pg_roles where rolname = 'saley_ro') then
    return;
  end if;
  foreach v in array array['v_decision','v_inbox','v_commitment','v_fact_working_on',
                           'v_pending_write','v_task_inputs'] loop
    execute format('grant select on %I to saley_ro', v);
  end loop;
"""
    + "\n".join(f"  grant execute on function {sig} to saley_ro;" for sig in SIGNATURES.values())
    + r"""
end $$;
"""
)

MOVE_OWNER = (
    r"""
do $$
declare
  s text;
begin
  if exists (select 1 from pg_roles where rolname = 'saley')
     and current_user <> 'saley'
     and pg_has_role(current_user, 'saley', 'member') then
    foreach s in array array["""
    + ", ".join(f"'{sig}'" for sig in SIGNATURES.values())
    + r"""] loop
      execute format('alter function %s owner to saley', s);
    end loop;
  else
    raise notice 'act functions stay with %: run deploy/sql/roles.sql to move them', current_user;
  end if;
end $$;
"""
)


def upgrade() -> None:
    op.execute(TABLES)
    for name, body in VIEWS.items():
        op.execute(f"create or replace view {name} as {body}")
    for function in FUNCTIONS:
        op.execute(function)
    op.execute(LOCK_DOWN)
    op.execute(MOVE_OWNER)
    op.execute(GRANT_RO)


# H4: after deploy/sql/roles.sql moved the function owner to `saley`, a plain `drop function`
# by the migrator fails. Try the owner role first (when the migrator is a member), else drop
# directly, and say clearly that the downgrade must run as the owner.
DROP_FUNCTIONS = (
    r"""
do $$
declare
  s text;
  o name;
begin
  select proowner::regrole::name into o from pg_proc
   where proname = 'saley_act' and pronamespace = 'public'::regnamespace limit 1;
  if o is not null and o <> current_user and pg_has_role(current_user, o, 'member') then
    execute format('set local role %I', o);
  end if;
  foreach s in array array["""
    + ", ".join(f"'{sig}'" for sig in SIGNATURES.values())
    + r"""] loop
    execute format('drop function if exists %s', s);
  end loop;
  reset role;
exception when insufficient_privilege then
  raise exception 'run this downgrade as the owner of the saley_* functions (deploy/sql/roles.sql)';
end $$;
"""
)


def downgrade() -> None:
    op.execute(DROP_FUNCTIONS)
    for name in reversed(list(VIEWS)):
        op.execute(f"drop view if exists {name}")
    for table in DROP_TABLES:
        op.execute(f"drop table if exists {table}")
