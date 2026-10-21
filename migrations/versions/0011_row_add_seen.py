"""R3 signals, suggestions, per-user seen, find-people and add-to-pipeline (`row_add`).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-19

Why (docs/plans/R3.md §3, D75, D76, D77):
- D75: seen is per person. `saley_mark_seen` is a SECURITY DEFINER function, so the web role
  still has no table grant. `v_user_seen` lets the list queries hide what a person saw.
- D77: "Find people" is a human click (R11). `saley_request_search` queues one request per
  company; the worker job `search_requests` runs it. `v_search_hit` and `v_search_request` show
  the result.
- D76: `saley_act` gets a `row_add` branch. It writes one `decision(kind='row_add')` and one
  `sync_outbox(op='append')` row. Same rules as every write: a role check, the formula guard
  (H1) on every cell, a duplicate check (company + name), the LinkedIn URL only when a search
  returned it (docs/10), and an undo window. `saley_undo` knows `row_add` too: the worker
  deletes the row only when nobody changed it.
- Views for Events, the four Suggestions lists, "needs a look" and "Saley suggests" read by
  `saley_ro` only. The weights live in `saley.intel.suggest` (plain code), not in SQL.

Function owner: same pattern as 0009. `create or replace` needs the owner, so the steps switch
to the owner role (`set local role`) when the migrating role is a member of it.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HEAD = """
language plpgsql
security definer
set search_path = public, pg_temp
"""

ACT_SIG = "saley_act(text, text, text, jsonb, jsonb, text, integer, integer, boolean)"
UNDO_SIG = "saley_undo(text, bigint, text)"
SEEN_SIG = "saley_mark_seen(text, text, bigint[], text)"
SEARCH_SIG = "saley_request_search(text, bigint, text)"
NEW_SIGS = (SEEN_SIG, SEARCH_SIG)

AS_OWNER = r"""
do $$
declare
  o name;
begin
  select proowner::regrole::name into o from pg_proc
   where proname = 'saley_act' and pronamespace = 'public'::regnamespace limit 1;
  if o is not null and o <> current_user and pg_has_role(current_user, o, 'member') then
    execute format('set local role %I', o);
  end if;
end $$;
"""

# ------------------------------------------------------------------ views

DECISION_ROLE = (
    r"c.title ~* '\m(head|chief|vp|vice president|director|founder|co-founder|ceo|cto|coo|"
    r"president|principal|lead|manager)\M'"
)

VIEWS: dict[str, str] = {
    "v_user_seen": r"""
select u.short as user_short, s.item_type, s.item_id, s.seen_at, s.query_id
from user_seen s join users u on u.id = s.user_id
""",
    "v_search_hit": r"""
select h.id, h.request_id, h.account_id, h.url, h.title, h.engine, h.found_at
from search_hit h
""",
    "v_search_request": r"""
select r.id, r.account_id, r.state, r.requested_at, r.finished_at, r.hits, r.error,
       u.short as user_short
from search_request r left join users u on u.id = r.user_id
""",
    "v_event_targets": r"""
select e.id as event_id, e.sheet_row_key, e.name, e.city, e.start as start_date,
       e."end" as end_date, e.url, e.reg_deadline, e.registered, e.attended,
       (coalesce(e."end", e.start) - saley_today()) as days_left,
       (coalesce(e."end", e.start) is null
        or coalesce(e."end", e.start) >= saley_today()) as upcoming,
       coalesce(c.people, 0) as people_in_city,
       coalesce(c.connected, 0) as connected,
       coalesce(c.p1, 0) as p1,
       null::integer as speaker_matches
from event e
left join lateral (
  select count(*) as people,
         count(*) filter (where s.connected) as connected,
         count(*) filter (where s.is_p1) as p1
  from v_contact_stage s
  where coalesce(e.city, '') <> '' and coalesce(s.city, '') <> ''
    and (position(lower(s.city) in lower(e.city)) > 0
         or position(lower(e.city) in lower(s.city)) > 0)) c on true
where e.removed_at is null
""",
    "v_suggest_warm": (
        r"""
select 'warm'::text as kind, c.id as ref_id, c.account_id, c.id as contact_id, c.name,
       c.company, c.title,
       jsonb_build_object(
         'connections', (select count(*) from v_contact_stage k
                          where k.account_id = c.account_id and k.connected),
         'has_email', c.has_email,
         'decision_role', """
        + DECISION_ROLE
        + r""",
         'p1', c.is_p1,
         'has_paper', coalesce(c.paper_url, '') <> '') as parts,
       jsonb_build_object(
         'row', c.sheet_row_key,
         'via', (select string_agg(k.name, ', ' order by k.name) from v_contact_stage k
                  where k.account_id = c.account_id and k.connected)) as evidence
from v_contact_stage c
where not c.contacted and c.stage = 'Listed'
  and exists (select 1 from v_contact_stage k where k.account_id = c.account_id and k.connected)
"""
    ),
    "v_suggest_best": (
        r"""
select 'best'::text as kind, c.id as ref_id, c.account_id, c.id as contact_id, c.name,
       c.company, c.title,
       jsonb_build_object(
         'role_class', case
           when c.title ~* '\m(head|chief|vp|vice president|director|founder|ceo|cto|coo|"""
        r"""president|general manager)\M' then 'budget'
           when c.title ~* '(research|scientist|professor|phd|fellow|lecturer)' then 'research'
           when c.title ~* '(data|annotation|labell?ing|ml ?ops|dataset)' then 'data'
           when c.title ~* '(product|engineer|developer|program|platform|applied)' then 'user'
           else 'other' end,
         'decision_role', """
        + DECISION_ROLE
        + r""",
         'has_email', c.has_email,
         'p1', c.is_p1,
         'has_paper', coalesce(c.paper_url, '') <> '') as parts,
       jsonb_build_object('row', c.sheet_row_key) as evidence
from v_contact_stage c
where not c.contacted and c.stage = 'Listed'
"""
    ),
    "v_suggest_newco": r"""
select 'newco'::text as kind, a.id as ref_id, a.id as account_id, null::integer as contact_id,
       a.name, a.name as company, null::text as title,
       jsonb_build_object(
         'funding', a.funding,
         'goal_fit', coalesce(cardinality(a.goal_codes), 0),
         'signals_30d', coalesce(s.n, 0),
         'top_importance', s.top,
         'search_state', r.state,
         'hits', coalesce(h.n, 0)) as parts,
       jsonb_build_object('row', a.sheet_row_key, 'signals', coalesce(s.ids, '{}'::bigint[]),
                          'industry', a.industry, 'geo', a.geo) as evidence
from v_account_coverage a
left join lateral (
  select count(*) as n, max(g.importance) as top, array_agg(g.signal_id) as ids
  from v_signal_linked g
  where g.entity_type = 'account' and g.entity_id = a.id and g.state <> 'dismissed'
    and coalesce(g.published_at, g.first_seen_at) > now() - interval '30 days') s on true
left join lateral (
  select x.state from search_request x where x.account_id = a.id
  order by x.id desc limit 1) r on true
left join lateral (
  select count(*) as n from search_hit y where y.account_id = a.id) h on true
where a.people = 0
""",
    "v_suggest_res": r"""
select 'res'::text as kind, c.id as ref_id, c.account_id, c.id as contact_id, c.name,
       c.company, c.title,
       jsonb_build_object(
         'has_paper', true,
         'fact_days', (saley_today() - w.observed_at::date),
         'topic_hit', (coalesce(c.about, '') || ' ' || coalesce(w.text, ''))
                      ~* '(speech|voice|evaluation|annotation|benchmark|multilingual|indic|hindi|llm)',
         'city_event', exists (select 1 from v_event_targets e
                                where e.upcoming and coalesce(e.city, '') <> ''
                                  and coalesce(c.city, '') <> ''
                                  and position(lower(c.city) in lower(e.city)) > 0),
         'has_email', c.has_email) as parts,
       jsonb_build_object('row', c.sheet_row_key, 'paper', c.paper_url) as evidence
from v_contact_stage c
left join v_fact_working_on w on w.entity_type = 'contact' and w.entity_id = c.id
where lower(coalesce(c.industry, '')) like '%academ%' and coalesce(c.paper_url, '') <> ''
  and not c.contacted and c.stage = 'Listed'
""",
    "v_saley_suggests": r"""
select kind, ref_id, account_id, contact_id, name, company, title, parts, evidence
from v_suggest_newco
union all
select kind, ref_id, account_id, contact_id, name, company, title, parts, evidence
from v_suggest_warm
""",
    "v_needs_look": r"""
select 'signal'::text as kind, g.signal_id as ref_id, g.title, g.entity_name as subject,
       g.importance::integer as importance, g.verify, coalesce(g.published_at, g.first_seen_at) as at,
       g.url
from v_signal_linked g
where g.importance >= 4 and g.state = 'new'
  and coalesce(g.published_at, g.first_seen_at) > now() - interval '7 days'
union all
select 'event', e.event_id, e.name, e.city, null::integer, false,
       (e.start_date::timestamp at time zone 'Asia/Kolkata'), e.url
from v_event_targets e
where e.start_date is not null and e.start_date - saley_today() between 0 and 3
""",
}
VIEW_ORDER = tuple(VIEWS)

# Source name and category letters ride along, so the Signals filters `src` and `cat` need no
# table grant. New columns go last: `create or replace view` keeps the old column order.
SIGNAL_VIEWS: dict[str, str] = {
    "v_signal_all": r"""
select s.id as story_id, s.title, s.url, s.summary, s.kind, s.outlets, s.outlet_count,
       s.importance, s.impact, s.tag, s.why, s.flag, s.name_kind, s.names,
       s.published_at, s.first_seen_at, s.scored_at, s.scorer_backend,
       (select count(*) from signal g where g.story_id = s.id) as linked_count,
       fs.name as source_name, fs.categories as categories, fs.lane as lane
from news_stories s
left join news_sources fs on fs.id = s.first_source_id
where s.scored_at is not null
""",
    "v_signal_linked": r"""
select g.id as signal_id, g.story_id, g.entity_type, g.entity_id,
       coalesce(a.name, c.name) as entity_name,
       coalesce(a.name, ca.name) as account_name,
       g.match_kind, g.confidence, g.verify, g.action_hint, g.state,
       g.tag, g.importance, g.impact, g.created_at,
       s.title, s.url, s.outlets, s.outlet_count, s.why, s.kind,
       s.published_at, s.first_seen_at,
       fs.name as source_name, fs.categories as categories, fs.lane as lane
from signal g
join news_stories s on s.id = g.story_id
left join news_sources fs on fs.id = s.first_source_id
left join account a on g.entity_type = 'account' and a.id = g.entity_id
left join contact c on g.entity_type = 'contact' and c.id = g.entity_id
left join account ca on ca.id = c.account_id
""",
}


GRANT_VIEWS = (
    "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
    + " ".join(f"grant select on {name} to saley_ro;" for name in VIEWS)
    + " end if; end $$"
)

# ------------------------------------------------------------------ saley_act: row_add

ROW_ADD_ROLES = (
    "'name','designation','about_poc','industry','based','paper_links','li_url','email','x_handle'"
)

ROW_ADD_BLOCK = (
    r"""  if p_action = 'row_add' then
    -- D76: one new row in Outreach PoCs. The target is the company (account id).
    declare
      v_acc account%rowtype;
      v_fields jsonb := p_payload -> 'fields';
      v_f jsonb;
      v_frole text;
      v_fvalue text;
      v_new_cells jsonb := '{}'::jsonb;
      v_nkey text;
      v_rkey text;
      v_ikey2 text;
      v_scope text;
      v_old record;
      v_new bigint;
      v_until2 timestamptz;
    begin
      if jsonb_array_length(p_targets) <> 1 or (p_targets ->> 0) !~ '^[0-9]{1,9}$'
         or jsonb_typeof(v_fields) is distinct from 'array'
         or jsonb_array_length(v_fields) not between 1 and 12 then
        return jsonb_build_object('ok', false, 'code', 'bad_request');
      end if;
      select * into v_acc from account
       where id = (p_targets ->> 0)::integer and removed_at is null;
      if not found then
        return jsonb_build_object('ok', false, 'code', 'no_target');
      end if;
      for v_f in select e from jsonb_array_elements(v_fields) e loop
        v_frole := v_f ->> 'role';
        v_fvalue := v_f ->> 'value';
        if jsonb_typeof(v_f -> 'value') is distinct from 'string' or v_frole is null
           or length(v_fvalue) > 200 or v_fvalue ~ '[[:cntrl:]]' or btrim(v_fvalue) = '' then
          return jsonb_build_object('ok', false, 'code', 'bad_value');
        end if;
        -- docs/10: the new-row column only. Email needs EMAIL_WRITE_ALLOWED like every email.
        if v_frole not in ("""
    + ROW_ADD_ROLES
    + r""") or (v_frole = 'email' and not coalesce(p_allow_email, false)) then
          return jsonb_build_object('ok', false, 'code', 'locked', 'role', v_frole);
        end if;
        -- H1: a leading = + - @ makes Google run the text as a formula.
        if ltrim(v_fvalue) ~ '^[=+@-]' or v_new_cells ? v_frole then
          return jsonb_build_object('ok', false, 'code', 'bad_value');
        end if;
        v_new_cells := v_new_cells || jsonb_build_object(v_frole, btrim(v_fvalue));
      end loop;
      if not (v_new_cells ? 'name') then
        return jsonb_build_object('ok', false, 'code', 'bad_value');
      end if;
      -- docs/10: LI Url only when a public search returned a linkedin.com/in link.
      if v_new_cells ? 'li_url' and (
           v_new_cells ->> 'li_url' !~ '^https://([a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9%._-]+/?$'
           or not exists (select 1 from search_hit h
                           where h.account_id = v_acc.id and h.url = v_new_cells ->> 'li_url')) then
        return jsonb_build_object('ok', false, 'code', 'bad_value');
      end if;
      v_new_cells := v_new_cells || jsonb_build_object('company', v_acc.name);

      v_nkey := lower(regexp_replace(btrim(v_new_cells ->> 'name'), '\s+', ' ', 'g'));
      v_scope := v_acc.id || ':' || v_nkey;
      perform pg_advisory_xact_lock(hashtext('poc' || 'row_add' || v_scope));
      v_ikey2 := encode(sha256(convert_to(p_key || '|row_add|' || v_scope, 'UTF8')), 'hex');
      select d.id, d.undo_until, d.row_key into v_old from decision d
       where d.kind = 'row_add' and d.instruction_key = v_ikey2 and d.undone_at is null;
      if found then
        return jsonb_build_object('ok', true, 'items', jsonb_build_array(jsonb_build_object(
          'write_id', v_old.id, 'row_key', v_old.row_key, 'role', 'row_add',
          'value', v_new_cells ->> 'name', 'undo_until', v_old.undo_until, 'already', true)));
      end if;
      -- D76: the same company and name is refused: in the sheet, or waiting to be added.
      if exists (select 1 from contact c
                  where c.account_id = v_acc.id and c.name_key = v_nkey and c.removed_at is null)
         or exists (select 1 from decision d
                     where d.kind = 'row_add' and d.reason = v_scope and d.undone_at is null) then
        return jsonb_build_object('ok', false, 'code', 'duplicate');
      end if;
      insert into decision (kind, choice, reason, user_id, surface, tab, role, after,
                            instruction_key, state, undo_until)
      values ('row_add', 'write', v_scope, v_user.id, p_surface, 'poc', 'row_add',
              v_new_cells ->> 'name', v_ikey2, 'active', now() + make_interval(mins => v_mins))
      returning id, undo_until into v_new, v_until2;
      v_rkey := 'new:' || v_new;
      update decision set row_key = v_rkey where id = v_new;
      insert into sync_outbox (decision_id, op, tab, row_key, role, value, state, next_at)
      values (v_new, 'append', 'poc', v_rkey, 'row_add', v_new_cells::text, 'pending', now());
      return jsonb_build_object('ok', true, 'items', jsonb_build_array(jsonb_build_object(
        'write_id', v_new, 'row_key', v_rkey, 'role', 'row_add',
        'value', v_new_cells ->> 'name', 'undo_until', v_until2, 'already', false)));
    end;
  end if;

"""
)


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"_m_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _swap(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:  # pragma: no cover - guards a changed 0009 text
        raise RuntimeError(f"0009 function text changed; update 0011 ({old[:40]!r})")
    return text.replace(old, new)


def _new_act() -> str:
    body = _load("0009_review_fixes.py")._new_act()
    body = _swap(
        body,
        "p_action not in ('write','email_write','task_done')",
        "p_action not in ('write','email_write','task_done','row_add')",
    )
    anchor = "  if jsonb_array_length(p_targets) not between 1 and 50"
    return _swap(body, anchor, ROW_ADD_BLOCK + anchor)


def _new_undo() -> str:
    body = _load("0009_review_fixes.py")._new_undo()
    body = _swap(
        body,
        "and kind in ('column_write','task_done','commitment_done');",
        "and kind in ('column_write','task_done','commitment_done','row_add');",
    )
    body = _swap(body, "and op = 'write' for update;", "and op in ('write','append') for update;")
    return _swap(
        body,
        "if v_d.kind = 'column_write' and v_has_outbox then",
        "if v_d.kind in ('column_write','row_add') and v_has_outbox then",
    )


SALEY_MARK_SEEN = (
    r"""
create or replace function saley_mark_seen(
  p_user text, p_item_type text, p_ids bigint[], p_query_id text default null)
returns jsonb
"""
    + HEAD
    + r"""
as $fn$
declare
  v_user users%rowtype;
  v_n integer;
begin
  if p_item_type is null
     or p_item_type not in ('story','signal','event','suggest_warm','suggest_best',
                            'suggest_newco','suggest_res')
     or p_ids is null or cardinality(p_ids) not between 1 and 200
     or (p_query_id is not null and length(p_query_id) > 64)
     or exists (select 1 from unnest(p_ids) i where i is null or i < 1) then
    return jsonb_build_object('ok', false, 'code', 'bad_request');
  end if;
  select * into v_user from users where short = p_user;
  if not found then
    return jsonb_build_object('ok', false, 'code', 'not_allowed');
  end if;
  -- Idempotent: the first time a person saw an item stays on record.
  insert into user_seen (user_id, item_type, item_id, query_id)
  select v_user.id, p_item_type, i, p_query_id from (select distinct unnest(p_ids) as i) x
  on conflict do nothing;
  get diagnostics v_n = row_count;
  return jsonb_build_object('ok', true, 'marked', v_n);
end
$fn$
"""
)

SALEY_REQUEST_SEARCH = (
    r"""
create or replace function saley_request_search(
  p_user text, p_account_id bigint, p_surface text default 'dashboard')
returns jsonb
"""
    + HEAD
    + r"""
as $fn$
declare
  v_user users%rowtype;
  v_open bigint;
  v_id bigint;
begin
  if p_surface is null or p_surface not in ('dashboard','chat','card') or p_account_id is null then
    return jsonb_build_object('ok', false, 'code', 'bad_request');
  end if;
  -- R11: a human click is the yes. Only an approver or admin may ask.
  select * into v_user from users where short = p_user and role in ('approver','admin');
  if not found then
    return jsonb_build_object('ok', false, 'code', 'not_allowed');
  end if;
  if not exists (select 1 from account where id = p_account_id and removed_at is null) then
    return jsonb_build_object('ok', false, 'code', 'no_target');
  end if;
  perform pg_advisory_xact_lock(hashtext('search' || p_account_id));
  select id into v_open from search_request
   where account_id = p_account_id and state in ('pending','running')
   order by id desc limit 1;
  if found then
    return jsonb_build_object('ok', true, 'request_id', v_open, 'already', true);
  end if;
  if (select count(*) from search_request where state in ('pending','running')) >= 20 then
    return jsonb_build_object('ok', false, 'code', 'too_many');
  end if;
  insert into search_request (user_id, account_id, state) values (v_user.id, p_account_id, 'pending')
  returning id into v_id;
  return jsonb_build_object('ok', true, 'request_id', v_id, 'already', false);
end
$fn$
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
    + ", ".join(f"'{sig}'" for sig in NEW_SIGS)
    + r"""] loop
      execute format('alter function %s owner to saley', s);
    end loop;
  else
    raise notice 'new functions stay with %: run deploy/sql/roles.sql', current_user;
  end if;
end $$;
"""
)

GRANT_FUNCTIONS = (
    "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
    + " ".join(f"grant execute on function {sig} to saley_ro;" for sig in NEW_SIGS)
    + " end if; end $$"
)

KIND_NEW = (
    "kind in ('column_write','task_done','dismiss','strategy_decision','undo',"
    "'commitment_done','row_add')"
)
KIND_OLD = (
    "kind in ('column_write','task_done','dismiss','strategy_decision','undo','commitment_done')"
)
OP_NEW = "op in ('write','restore','append')"
OP_OLD = "op in ('write','restore')"


def upgrade() -> None:
    op.execute("alter table decision drop constraint ck_decision_kind")
    op.execute(f"alter table decision add constraint ck_decision_kind check ({KIND_NEW})")
    op.execute("alter table sync_outbox drop constraint ck_outbox_op")
    op.execute(f"alter table sync_outbox add constraint ck_outbox_op check ({OP_NEW})")
    op.execute("create index ix_user_seen_item on user_seen (item_type, item_id)")
    for name, body in SIGNAL_VIEWS.items():
        op.execute(f"create or replace view {name} as {body}")
    for name, body in VIEWS.items():
        op.execute(f"create or replace view {name} as {body}")
    op.execute(GRANT_VIEWS)

    op.execute(AS_OWNER)
    op.execute(_new_act())
    op.execute(_new_undo())
    op.execute(SALEY_MARK_SEEN)
    op.execute(SALEY_REQUEST_SEARCH)
    for sig in NEW_SIGS:
        op.execute(f"revoke all on function {sig} from public")
    op.execute(MOVE_OWNER)
    op.execute(GRANT_FUNCTIONS)
    op.execute("reset role")


def downgrade() -> None:
    old = _load("0009_review_fixes.py")
    # Rows that only 0011 allows go first: the older checks would refuse them.
    op.execute(
        "delete from sync_audit where outbox_id in (select o.id from sync_outbox o "
        "join decision d on d.id = o.decision_id where o.op = 'append' "
        "or d.kind = 'row_add' or d.undo_of in (select id from decision where kind = 'row_add'))"
    )
    op.execute(
        "delete from sync_outbox where decision_id in (select id from decision "
        "where kind = 'row_add' or undo_of in (select id from decision where kind = 'row_add'))"
    )
    op.execute(
        "delete from decision where undo_of in (select id from decision where kind = 'row_add')"
    )
    op.execute("delete from decision where kind = 'row_add'")
    op.execute("alter table decision drop constraint ck_decision_kind")
    op.execute(f"alter table decision add constraint ck_decision_kind check ({KIND_OLD})")
    op.execute("alter table sync_outbox drop constraint ck_outbox_op")
    op.execute(f"alter table sync_outbox add constraint ck_outbox_op check ({OP_OLD})")
    op.execute("drop index if exists ix_user_seen_item")
    for name in reversed(VIEW_ORDER):
        op.execute(f"drop view if exists {name}")
    older = _load("0010_news_signals.py")
    for name in SIGNAL_VIEWS:
        op.execute(f"drop view if exists {name}")
        op.execute(f"create view {name} as {older.VIEWS[name]}")
    op.execute(
        "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
        + " ".join(f"grant select on {name} to saley_ro;" for name in SIGNAL_VIEWS)
        + " end if; end $$"
    )

    op.execute(AS_OWNER)
    for sig in NEW_SIGS:
        op.execute(f"drop function if exists {sig}")
    op.execute(old._new_act())
    op.execute(old._new_undo())
    op.execute("reset role")
