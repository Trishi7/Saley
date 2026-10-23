"""R7 T1: card columns on bot_post, bot_interaction, the Discord writers, one press door, card sync.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-23

Why (docs/17 §4, D257, D259):
- `bot_post` gets the card columns. `card_id` is the key of the dashboard item, unique, so one
  insert per item is the "one card per notification" rule (D259). `payload` is the Discord body
  the worker rendered, plus `keys` (the items the card is about) and `acts` (for each button, the
  door to call and its fixed arguments). The `done_*` columns are the state of the card: who
  acted, when, where, and the write that an undo reverts.
- `users.discord_writer`: the bot sets it from DISCORD_WRITE_IDS at start (D257). The press door
  checks it. A press by anyone else writes nothing.
- `saley_card_press(card, user, action, input, surface)` only maps a press to the doors that exist
  (`saley_act`, `saley_proposal_resolve`, `saley_commitment_done`, `saley_mark_seen`,
  `saley_undo_group`). The arguments come from the card the worker wrote, never from the presser;
  the presser gives only a modal value, which is checked here. Every call leaves one
  `bot_interaction` row. It answers `ok`, `already` (with who and when), `refused` or `error`.
- `saley_commitment_done` refused the 'discord' surface (0023 replaced it after 0022 had patched
  the other doors). Its live text is patched with `pg_get_functiondef`, as 0022 did.
- `saley_card_sync` (trigger on `decision` insert and undo) marks every card about the same item
  done (or open again after an undo) and sends `NOTIFY saley_card, <card_id>`. The bot listens
  and edits the card within 5 s, whichever surface acted first (D259).

Owner model (D256): saley_admin owns the new table; the SECURITY DEFINER function goes to saley
with PUBLIC revoked (grants.sql does the same on every deploy). The trigger function is not a
definer: it runs as the door that wrote the decision.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRESS_SIG = "saley_card_press(text, text, text, jsonb, text)"
DONE_SIG = "saley_commitment_done(text, bigint, text, integer)"
SURFACE_OLD = "p_surface not in ('dashboard','chat','card')"
SURFACE_NEW = "p_surface not in ('dashboard','chat','card','discord')"

TABLES = r"""
alter table bot_post
  add column card_type text,
  add column priority integer,
  add column held_reason text,
  add column discord_message_id bigint,
  add column discord_channel_id bigint,
  add column thread_id bigint,
  add column card_id text,
  add column payload jsonb,
  add column rendered_hash text,
  add column pinned boolean not null default false,
  add column edited_at timestamptz,
  add column done_by text,
  add column done_at timestamptz,
  add column done_surface text,
  add column done_write_id bigint,
  add column undo_until timestamptz;
alter table bot_post add constraint uq_bot_post_card unique (card_id);
alter table bot_post add constraint ck_bot_post_card_type check (
  card_type is null or card_type in ('M1','M2','M3','M4','M5','M6','M7','M8','M9','M10','M11',
                                     'M12','M13','M14','M15','A1','A2'));
alter table bot_post add constraint ck_bot_post_card_id check (
  card_id is null or card_id ~ '^[a-z0-9:-]{3,100}$');
alter table bot_post drop constraint ck_bot_post_state;
alter table bot_post add constraint ck_bot_post_state check (
  state in ('queued','ready','sent','deferred','dropped','gone'));
create index ix_bot_post_keys on bot_post using gin ((payload -> 'keys'));
create index ix_bot_post_edited on bot_post (edited_at) where card_id is not null;

alter table users add column discord_writer boolean not null default false;

create table bot_interaction (
  id bigserial primary key,
  card_id text not null,
  user_id integer references users (id),
  action text not null,
  surface text not null,
  result text not null check (result in ('ok','already','refused','error')),
  code text,
  detail jsonb not null default '{}'::jsonb,
  latency_ms integer,
  at timestamptz not null default now()
);
create index ix_bot_interaction_card on bot_interaction (card_id, at desc);
create index ix_bot_interaction_at on bot_interaction (at);
"""

CARD_PRESS = r"""
create or replace function saley_card_press(
  p_card_id text, p_user text, p_action text, p_payload jsonb, p_surface text)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
  v_user users%rowtype;
  v_card bot_post%rowtype;
  v_in jsonb := coalesce(p_payload, '{}'::jsonb);
  v_act jsonb;
  v_door text;
  v_args jsonb;
  v_res jsonb := '{}'::jsonb;
  v_result text;
  v_code text;
  v_value text;
  v_text text;
  v_day date;
  v_id bigint;
  v_write bigint;
  v_until timestamptz;
  v_by text;
  v_at text;
  v_t0 timestamptz := clock_timestamp();
  v_undo integer := 5;
  v_cool integer := 2;
  v_needs_writer boolean := true;
begin
  if p_surface is distinct from 'discord'
     or p_card_id is null or p_card_id !~ '^[a-z0-9:-]{3,100}$'
     or p_action is null or p_action !~ '^[a-z_]{2,24}$'
     or jsonb_typeof(v_in) is distinct from 'object' then
    return jsonb_build_object('result', 'error', 'code', 'bad_request');
  end if;
  if v_in ->> 'undo_minutes' ~ '^[0-9]{1,2}$' then
    v_undo := least(greatest((v_in ->> 'undo_minutes')::integer, 1), 60);
  end if;
  if v_in ->> 'cooldown' ~ '^[0-9]{1,3}$' then
    v_cool := (v_in ->> 'cooldown')::integer;
  end if;
  select * into v_user from users where short = p_user;
  if not found then
    return jsonb_build_object('result', 'refused', 'code', 'unknown_user');
  end if;

  select * into v_card from bot_post where card_id = p_card_id for update;
  if not found or v_card.state = 'gone' then
    v_result := 'error'; v_code := 'not_found';
  elsif p_action = 'undo' then
    v_door := 'undo';
  else
    v_act := v_card.payload -> 'acts' -> p_action;
    v_door := v_act ->> 'door';
    v_args := coalesce(v_act -> 'args', '{}'::jsonb);
    if v_door is null then
      v_result := 'error'; v_code := 'bad_action';
    end if;
  end if;

  -- D257: who may press. The door decides, never a flag the card carries. Read-only doors
  -- (seen, close) and the answer of the person who was asked need no writer.
  if v_result is null then
    v_needs_writer := v_door not in ('mark_seen', 'close')
      and not (v_door = 'fact' and v_args ->> 'kind' = 'answer');
    if v_needs_writer and not v_user.discord_writer then
      v_result := 'refused'; v_code := 'not_writer';
    elsif v_door <> 'undo' and v_card.done_at is not null then
      v_result := 'already'; v_code := 'already';
      v_by := v_card.done_by;
      v_at := to_char(v_card.done_at at time zone 'Asia/Kolkata', 'HH24:MI');
    end if;
  end if;

  if v_result is null and v_door = 'mark_seen' then
    v_res := saley_mark_seen(p_user, v_args ->> 'item_type',
      array(select jsonb_array_elements_text(v_args -> 'ids'))::bigint[],
      left('card-' || p_card_id, 64));

  elsif v_result is null and v_door = 'resolve' then
    v_value := coalesce(v_in ->> 'value', v_args ->> 'proposal_id');
    if v_value is null or v_value !~ '^[0-9]{1,15}$'
       or (v_args ? 'choices' and not (v_args -> 'choices') ? v_value) then
      v_result := 'error'; v_code := 'bad_value';
    else
      v_res := saley_proposal_resolve(p_user, v_value::bigint, v_args ->> 'choice', null, null,
        'discord', v_cool, v_undo, false);
      v_write := (v_res -> 'items' -> 0 ->> 'write_id')::bigint;
      if v_write is null and v_res ? 'decision_id' then
        v_write := (v_res ->> 'decision_id')::bigint;
      end if;
    end if;

  elsif v_result is null and v_door = 'act' then
    if v_args ? 'role' then
      -- A value the presser picked, or the fixed value of the button (M6 No show): always one
      -- of the values the card offers.
      v_value := coalesce(v_in ->> 'value', v_args ->> 'value');
      if v_value is null or not (v_args -> 'values') ? v_value then
        v_result := 'error'; v_code := 'bad_value';
      else
        v_res := saley_act(p_user, 'discord', 'write', v_args -> 'targets',
          jsonb_build_object('cells', jsonb_build_array(
            jsonb_build_object('role', v_args ->> 'role', 'value', v_value))),
          encode(sha256(convert_to('write|' || (v_args -> 'targets' ->> 0) || '|'
            || (v_args ->> 'role') || '=' || v_value || '|', 'UTF8')), 'hex'),
          v_cool, v_undo, false);
      end if;
    else
      v_res := saley_act(p_user, 'discord', v_args ->> 'action', v_args -> 'targets',
        v_args -> 'payload', v_args ->> 'key', v_cool, v_undo, false);
    end if;
    if v_result is null then
      v_write := (v_res -> 'items' -> 0 ->> 'write_id')::bigint;
    end if;

  elsif v_result is null and v_door = 'commitment_done' then
    v_res := saley_commitment_done(p_user, (v_args ->> 'commitment_id')::bigint, 'discord',
      v_undo);
    v_write := (v_res ->> 'write_id')::bigint;

  elsif v_result is null and v_door in ('commitment_due', 'next_step') then
    if v_in ->> 'date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then
      v_result := 'error'; v_code := 'bad_date';
    else
      begin
        v_day := (v_in ->> 'date')::date;
      exception when others then
        v_result := 'error'; v_code := 'bad_date';
      end;
    end if;
    if v_result is null and v_door = 'commitment_due' then
      update commitment set due = v_day, reminders_sent = 0, last_reminded_on = null
       where id = (v_args ->> 'commitment_id')::bigint and owner = v_user.id and state = 'open';
      if not found then
        v_result := 'refused'; v_code := 'not_owner';
      else
        v_res := jsonb_build_object('ok', true, 'due', v_day);
      end if;
    elsif v_result is null then
      v_text := btrim(coalesce(v_in ->> 'text', ''));
      if length(v_text) not between 1 and 300 or v_text ~ '[[:cntrl:]]' then
        v_result := 'error'; v_code := 'bad_value';
      elsif v_in ? 'status' and not (v_args -> 'values') ? (v_in ->> 'status') then
        v_result := 'error'; v_code := 'bad_value';
      else
        insert into commitment (owner, text, due, source_type, source_ref, state)
        values (v_user.id, v_text, v_day, 'discord', p_card_id, 'open') returning id into v_id;
        v_res := jsonb_build_object('ok', true, 'commitment_id', v_id);
        if v_in ? 'status' then
          v_res := saley_act(p_user, 'discord', 'write', v_args -> 'targets',
            jsonb_build_object('cells', jsonb_build_array(jsonb_build_object(
              'role', 'meeting_status', 'value', v_in ->> 'status'))),
            encode(sha256(convert_to('write|' || (v_args -> 'targets' ->> 0)
              || '|meeting_status=' || (v_in ->> 'status') || '|', 'UTF8')), 'hex'),
            v_cool, v_undo, false) || jsonb_build_object('commitment_id', v_id);
          v_write := (v_res -> 'items' -> 0 ->> 'write_id')::bigint;
        end if;
      end if;
    end if;

  elsif v_result is null and v_door = 'fact' then
    -- A pick from a select (M4 event id, M11 short name) must be one the card offers; typed
    -- text (M5 note, M14 answer) is checked for length and controls.
    v_value := v_in ->> 'value';
    v_text := btrim(coalesce(nullif(v_in ->> 'text', ''), v_args ->> 'text', v_value, ''));
    v_id := case when v_args ? 'entity_id' then (v_args ->> 'entity_id')::bigint
                 when v_value ~ '^[0-9]{1,9}$' then v_value::bigint end;
    if length(v_text) not between 1 and 300 or v_text ~ '[[:cntrl:]]' or v_id is null
       or (v_args ? 'values' and (v_value is null or not (v_args -> 'values') ? v_value)) then
      v_result := 'error'; v_code := 'bad_value';
    elsif v_args ->> 'kind' = 'answer' and not exists (
        select 1 from ask where id = (v_args ->> 'ask_id')::bigint and to_user = v_user.id
          and answered_at is null) then
      v_result := 'refused'; v_code := 'not_asked';
    else
      insert into fact (entity_type, entity_id, kind, value, source_type, source_ref, author,
                        observed_at, confidence)
      values (v_args ->> 'entity_type', v_id::integer, v_args ->> 'kind',
              jsonb_build_object('text', v_text), case when v_args ->> 'kind' = 'answer'
              then 'teammate' else 'discord' end, p_card_id, p_user, now(), 3)
      returning id into v_id;
      if v_args ->> 'kind' = 'answer' then
        update ask set answered_at = now(), answer_fact_id = v_id
         where id = (v_args ->> 'ask_id')::bigint;
      end if;
      v_res := jsonb_build_object('ok', true, 'fact_id', v_id);
    end if;

  elsif v_result is null and v_door = 'commit' then
    -- M3 Do it: the presser takes on the move the card suggests (a commitment; Q-R7-1).
    v_value := coalesce(v_in ->> 'value', v_args ->> 'ref');
    v_text := v_args -> 'texts' ->> v_value;
    if v_value is null or v_text is null or length(v_text) not between 1 and 300 then
      v_result := 'error'; v_code := 'bad_value';
    else
      insert into commitment (owner, text, due, source_type, source_ref, state)
      values (v_user.id, v_text, saley_today()
              + least(greatest(coalesce((v_args ->> 'days')::integer, 2), 0), 30),
              'discord', p_card_id, 'open')
      returning id into v_id;
      v_res := jsonb_build_object('ok', true, 'commitment_id', v_id);
    end if;

  elsif v_result is null and v_door = 'later' then
    -- M7 move a week, M9 snooze a day: the same card again, later. Its buttons, links and
    -- payload carry the new card id, so a press on the copy acts on the copy. The new id
    -- stays within the 80 characters of a `c7:<card>:<action>` button (bot/components.py).
    v_until := now() + make_interval(days => least(greatest((v_args ->> 'days')::integer, 1), 14));
    v_text := left(p_card_id, 69) || '-l' || to_char(v_until, 'YYYYMMDD');
    insert into bot_post (kind, shape, channel, for_user, mentions, body, rank, due_at, state,
                          item_type, item_id, is_meeting, is_reconnect, card_type, priority,
                          card_id, payload, rendered_hash)
    select kind, shape, channel, for_user, mentions, body, rank, v_until, 'queued', item_type,
           item_id, is_meeting, is_reconnect, card_type, priority, v_text,
           replace(payload::text, p_card_id, v_text)::jsonb, rendered_hash
      from bot_post where id = v_card.id
    on conflict (card_id) do nothing;
    v_until := null;  -- not an undo window
    v_res := jsonb_build_object('ok', true, 'card_id', v_text);

  elsif v_result is null and v_door = 'close' then
    v_res := jsonb_build_object('ok', true);

  elsif v_result is null and v_door = 'undo' then
    if v_card.done_write_id is null then
      v_result := 'error'; v_code := 'nothing_to_undo';
    elsif v_card.undo_until is null or v_card.undo_until < now() then
      v_result := 'refused'; v_code := 'window_closed';
      v_at := to_char(v_card.undo_until at time zone 'Asia/Kolkata', 'HH24:MI');
    else
      v_res := saley_undo_group(p_user, v_card.done_write_id, 'discord');
    end if;
  end if;

  -- One answer shape for every door.
  if v_result is null then
    v_code := v_res ->> 'code';
    if coalesce((v_res ->> 'ok')::boolean, false) then
      if coalesce((v_res ->> 'already')::boolean, false)
         or (jsonb_typeof(v_res -> 'items') = 'array' and jsonb_array_length(v_res -> 'items') > 0
             and not exists (select 1 from jsonb_array_elements(v_res -> 'items') i
                              where not coalesce((i ->> 'already')::boolean, false))) then
        v_result := 'already'; v_code := 'already';
      else
        v_result := 'ok';
      end if;
    elsif v_code in ('already', 'cooldown') then
      v_result := 'already';
    elsif v_code in ('not_allowed', 'not_owner', 'not_signer', 'locked') then
      v_result := 'refused';
    else
      v_result := 'error';
    end if;
    v_by := coalesce(v_by, v_res ->> 'by');
    v_at := coalesce(v_at, v_res ->> 'at');
  end if;

  if v_result = 'ok' and v_door <> 'undo' then
    v_until := case when v_write is null then null else now() + make_interval(mins => v_undo) end;
    update bot_post set done_by = v_user.name, done_at = now(), done_surface = 'discord',
                        done_write_id = v_write, undo_until = v_until, edited_at = now()
     where id = v_card.id;
  elsif v_result = 'ok' then
    -- The undo trigger opened every card of the write; this one is opened here too.
    update bot_post set done_by = null, done_at = null, done_surface = null,
                        done_write_id = null, undo_until = null, edited_at = now()
     where id = v_card.id;
  end if;
  if v_result = 'already' and v_by is null and v_card.done_at is not null then
    v_by := v_card.done_by;
    v_at := to_char(v_card.done_at at time zone 'Asia/Kolkata', 'HH24:MI');
  end if;

  insert into bot_interaction (card_id, user_id, action, surface, result, code, detail,
                               latency_ms)
  values (p_card_id, v_user.id, p_action, p_surface, v_result, v_code,
          jsonb_build_object('door', v_door, 'write_id', v_write),
          (extract(epoch from clock_timestamp() - v_t0) * 1000)::integer);
  return jsonb_build_object('result', v_result, 'code', v_code, 'by', v_by, 'at', v_at,
    'write_id', v_write, 'undo_until', v_until, 'card_id', p_card_id);
end
$fn$
"""

CARD_SYNC = r"""
create or replace function saley_card_sync() returns trigger
language plpgsql
set search_path = public, pg_temp
as $fn$
declare
  v_keys text[];
  v_card text;
  v_name text;
begin
  if tg_op = 'UPDATE' then
    -- An undo: every card that this write closed is open again.
    if new.undone_at is null or old.undone_at is not null then
      return null;
    end if;
    for v_card in
      update bot_post set done_by = null, done_at = null, done_surface = null,
                          done_write_id = null, undo_until = null, edited_at = now()
       where done_write_id = new.id and card_id is not null
      returning card_id
    loop
      perform pg_notify('saley_card', v_card);
    end loop;
    return null;
  end if;
  if new.kind not in ('column_write', 'task_done', 'dismiss', 'commitment_done') then
    return null;
  end if;
  v_keys := array_remove(array[
    case when new.proposal_id is not null then 'proposal:' || new.proposal_id end,
    case when new.tab = 'poc' then 'poc:' || new.row_key || ':' || new.role end,
    case when new.tab = 'commitment' then new.row_key end], null);
  if cardinality(v_keys) = 0 then
    return null;
  end if;
  select name into v_name from users where id = new.user_id;
  for v_card in
    update bot_post set done_by = v_name, done_at = new.at, done_surface = new.surface,
                        done_write_id = new.id, undo_until = new.undo_until, edited_at = now()
     where card_id is not null and done_at is null and state in ('queued','ready','sent','deferred')
       and payload -> 'keys' ?| v_keys
    returning card_id
  loop
    perform pg_notify('saley_card', v_card);
  end loop;
  return null;
end
$fn$;

create trigger trg_decision_card_sync
  after insert or update of undone_at on decision
  for each row execute function saley_card_sync();
"""


def _patch_done(old: str, new: str) -> str:
    """Swap the surface check in the live text of saley_commitment_done. A miss stops."""
    quoted_old, quoted_new = old.replace("'", "''"), new.replace("'", "''")
    return (
        "do $$ declare def text; begin "
        f"def := pg_get_functiondef('{DONE_SIG}'::regprocedure); "
        f"if position('{quoted_old}' in def) = 0 then "
        "raise exception '0027: surface check not found in saley_commitment_done'; end if; "
        f"execute replace(def, '{quoted_old}', '{quoted_new}'); end $$"
    )


OWNER = (
    "do $$ begin "
    "if exists (select 1 from pg_roles where rolname = 'saley') and current_user <> 'saley' "
    "and pg_has_role(current_user, 'saley', 'member') then "
    f"alter function {PRESS_SIG} owner to saley; "
    "else raise notice 'new function stays with %: run deploy/sql/roles.sql', current_user; "
    "end if; end $$"
)


def upgrade() -> None:
    op.execute(TABLES)
    op.execute(_patch_done(SURFACE_OLD, SURFACE_NEW))
    op.execute(CARD_PRESS)
    op.execute(f"revoke all on function {PRESS_SIG} from public")
    op.execute(OWNER)
    op.execute(CARD_SYNC)


def downgrade() -> None:
    op.execute("drop trigger if exists trg_decision_card_sync on decision")
    op.execute("drop function if exists saley_card_sync()")
    op.execute(f"drop function if exists {PRESS_SIG}")
    op.execute(_patch_done(SURFACE_NEW, SURFACE_OLD))
    op.execute("drop table if exists bot_interaction")
    op.execute("alter table users drop column if exists discord_writer")
    op.execute("delete from bot_post where state = 'gone'")
    op.execute("alter table bot_post drop constraint ck_bot_post_state")
    op.execute(
        "alter table bot_post add constraint ck_bot_post_state check "
        "(state in ('queued','ready','sent','deferred','dropped'))"
    )
    op.execute(
        "alter table bot_post drop constraint if exists ck_bot_post_card_id, "
        "drop constraint if exists ck_bot_post_card_type, "
        "drop constraint if exists uq_bot_post_card"
    )
    op.execute(
        "alter table bot_post "
        + ", ".join(
            f"drop column if exists {c}"
            for c in (
                "card_type",
                "priority",
                "held_reason",
                "discord_message_id",
                "discord_channel_id",
                "thread_id",
                "card_id",
                "payload",
                "rendered_hash",
                "pinned",
                "edited_at",
                "done_by",
                "done_at",
                "done_surface",
                "done_write_id",
                "undo_until",
            )
        )
    )
