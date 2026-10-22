"""R4 ticket 7: one door for a card (Do it, Edit, Skip), and the 'discord' surface in the act functions.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-20

Why (docs/plans/R4.md §3, D24, D48, D129, D135):
- 0018 let `decision.surface` hold 'discord', but the four act functions still refused it. The
  bot calls them with surface 'discord' (a Discord instruction or a modal save). This migration
  reads the live text of `saley_act`, `saley_undo`, `saley_confirm` and `saley_dismiss` with
  `pg_get_functiondef` and adds 'discord' to the surface check. No older migration text is copied,
  so a later fix to those bodies is not lost.
- `saley_act` takes `select ... for update` on the proposal row as soon as it reads a
  `proposal_id`. A click on the dashboard "Do it" (which calls `saley_act`) and a Discord save now
  wait for each other on the same row. Then it reads the state it just locked (D129): a card that
  is 'dismissed', or 'acted' by a write with another instruction key, returns
  `{ok: false, code: 'already', by, at}` and writes nothing. The same key is still the D48
  "already done" no-op, so a double click stays harmless.
- `saley_proposal_resolve(user, proposal, choice, text, reason, surface, cooldown, undo, email)`
  is the one door for a card. It locks the proposal row (`for update`). If the state is no longer
  'open' it writes nothing and returns `{ok: false, code: 'already', by, at}` with the NAME and
  the IST time of the first saver (D129). Else:
  - `do`: writes the card cell through `saley_act`;
  - `edit`: the same, with the edited text as the cell value;
  - `skip`: dismisses through `saley_dismiss` and keeps the reason.
  The instruction key is built here (action, row id, role=value) with the same text as
  `saley.web.act.instruction_key`, so chat and dashboard are one write (D48). The value rules are
  the ones of `saley.web.act` and are checked here too: no leading = + @ - (H1), no control or
  bidi character, a date cell is a real ISO date, First Contact is one of TRUE, FALSE, VR, SID and
  TRUE / FALSE are upper case (one key for chat and dashboard). Meeting Status and the LinkedIn
  cell take free text on the dashboard, so no value list is added here.

Owner (R6, Q-R6-4): migrations run as saley_admin and never switch role. saley_admin is a
member of `saley` (D244), so it may replace or drop the saley_* functions that `saley` owns,
and the MOVE_OWNER step gives new functions to `saley` at the end. saley_admin owns every
table and view (docs/01 §11)."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PATCHED = (
    "saley_act(text, text, text, jsonb, jsonb, text, integer, integer, boolean)",
    "saley_undo(text, bigint, text)",
    "saley_confirm(text, bigint, text)",
    "saley_dismiss(text, bigint, text)",
)
RESOLVE_SIG = (
    "saley_proposal_resolve(text, bigint, text, text, text, text, integer, integer, boolean)"
)


SURFACE_OLD = "('dashboard','chat','card')"
SURFACE_NEW = "('dashboard','chat','card','discord')"
LOCK_OLD = "if not exists (select 1 from proposal where id = v_proposal) then"
LOCK_NEW = (
    "select state into v_pstate from proposal where id = v_proposal for update;\n"
    "    if not found then"
)
DECL_OLD = "  v_proposal bigint;\n"
DECL_NEW = "  v_proposal bigint;\n  v_pstate text;\n  v_pfirst record;\n"
CHECK_OLD = "select jsonb_agg(jsonb_build_object('contact_id', c.id,"
# D129: the state is read AFTER the lock. A card that Discord skipped, or that another
# instruction already wrote, must not write again from a stale dashboard click.
CHECK_NEW = (
    "if v_proposal is not null and v_pstate in ('dismissed','acted') then\n"
    "    if v_pstate = 'dismissed' or not exists (\n"
    "         select 1 from decision d\n"
    "          where d.proposal_id = v_proposal and d.kind = 'column_write'\n"
    "            and split_part(d.instruction_key, '#', 1) = encode(sha256(convert_to(\n"
    "                  p_key || '|' || d.row_key || '|' || d.role, 'UTF8')), 'hex')) then\n"
    "      select d.at, u.name into v_pfirst\n"
    "        from decision d left join users u on u.id = d.user_id\n"
    "       where d.proposal_id = v_proposal\n"
    "         and d.kind in ('column_write','task_done','dismiss')\n"
    "       order by d.at, d.id limit 1;\n"
    "      if found then\n"
    "        return jsonb_build_object('ok', false, 'code', 'already', 'by', v_pfirst.name,\n"
    "          'at', to_char(v_pfirst.at at time zone 'Asia/Kolkata', 'HH24:MI'));\n"
    "      end if;\n"
    "    end if;\n"
    "  end if;\n\n"
    "  " + CHECK_OLD
)
SWAPS = [
    (SURFACE_OLD, SURFACE_NEW, False),
    (DECL_OLD, DECL_NEW, True),
    (LOCK_OLD, LOCK_NEW, True),
    (CHECK_OLD, CHECK_NEW, True),
]


def _patch(sigs: Sequence[str], swaps: Sequence[tuple[str, str, bool]]) -> str:
    """A DO block that rewrites each live function text. A missing pattern stops the migration.

    Each swap is (old, new, only_saley_act)."""
    lines = [
        "do $$",
        "declare",
        "  s text;",
        "  def text;",
        "  fixed text;",
        "begin",
    ]
    for sig in sigs:
        lines.append(f"  s := '{sig}';")
        lines.append("  def := pg_get_functiondef(s::regprocedure);")
        lines.append("  fixed := def;")
        for old, new, only_act in swaps:
            if only_act and not sig.startswith("saley_act("):
                continue
            quoted_old = old.replace("'", "''")
            quoted_new = new.replace("'", "''")
            lines.append(
                f"  if position('{quoted_old}' in fixed) = 0 then "
                f"raise exception '0022: pattern not found in %', s; end if;"
            )
            lines.append(f"  fixed := replace(fixed, '{quoted_old}', '{quoted_new}');")
        lines.append("  execute fixed;")
    lines.append("end $$;")
    return "\n".join(lines)


def _unpatch() -> str:
    """The same rewrite in reverse. A missing pattern stops the downgrade, as in upgrade."""
    return _patch(PATCHED, [(new, old, only_act) for old, new, only_act in reversed(SWAPS)])


SALEY_PROPOSAL_RESOLVE = r"""
create or replace function saley_proposal_resolve(
  p_user text, p_proposal_id bigint, p_choice text, p_text text, p_reason text,
  p_surface text, p_cooldown integer default 2, p_undo_minutes integer default 10,
  p_allow_email boolean default false)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
  v_user users%rowtype;
  v_p proposal%rowtype;
  v_first record;
  v_cell jsonb;
  v_role text;
  v_value text;
  v_res jsonb;
begin
  if p_choice is null or p_choice not in ('do','edit','skip')
     or p_surface is null or p_surface not in ('dashboard','discord')
     or (p_choice = 'edit' and (p_text is null or length(btrim(p_text)) = 0))
     or (p_reason is not null and (length(p_reason) > 300 or p_reason ~ '[[:cntrl:]]')) then
    return jsonb_build_object('ok', false, 'code', 'bad_request');
  end if;
  select * into v_user from users where short = p_user and role in ('approver','admin');
  if not found then
    return jsonb_build_object('ok', false, 'code', 'not_allowed');
  end if;

  -- D129: the first saver wins. The row lock makes a second saver wait, then see the new state.
  select * into v_p from proposal where id = p_proposal_id for update;
  if not found then
    return jsonb_build_object('ok', false, 'code', 'not_found');
  end if;
  if v_p.state <> 'open' then
    select d.at, u.name into v_first
      from decision d left join users u on u.id = d.user_id
     where d.proposal_id = v_p.id and d.kind in ('column_write','task_done','dismiss')
     order by d.at, d.id limit 1;
    if not found then
      return jsonb_build_object('ok', false, 'code', 'not_open');
    end if;
    return jsonb_build_object('ok', false, 'code', 'already', 'by', v_first.name,
      'at', to_char(v_first.at at time zone 'Asia/Kolkata', 'HH24:MI'));
  end if;

  if p_choice = 'skip' then
    v_res := saley_dismiss(p_user, v_p.id, p_surface);
    if (v_res ->> 'ok')::boolean and p_reason is not null and v_res ? 'decision_id' then
      update decision set reason = p_reason where id = (v_res ->> 'decision_id')::bigint;
    end if;
    return v_res || jsonb_build_object('choice', 'skip');
  end if;

  if v_p.entity_type is distinct from 'contact' or v_p.entity_id is null then
    return jsonb_build_object('ok', false, 'code', 'no_draft');
  end if;
  v_cell := v_p.payload -> 'cells' -> 0;
  if jsonb_typeof(v_p.payload -> 'cells') is distinct from 'array'
     or jsonb_array_length(v_p.payload -> 'cells') <> 1
     or jsonb_typeof(v_cell -> 'role') is distinct from 'string'
     or jsonb_typeof(v_cell -> 'value') is distinct from 'string' then
    return jsonb_build_object('ok', false, 'code', 'no_draft');
  end if;
  v_role := v_cell ->> 'role';
  v_value := case when p_choice = 'edit' then btrim(p_text) else v_cell ->> 'value' end;
  -- The same rules as saley.web.act._clean_value: H1 (a formula), controls, line and bidi marks.
  if length(v_value) not between 1 and 200
     or v_value ~ '^[=+@-]' or v_value ~ '[[:cntrl:]]'
     or v_value ~ '[\u2028\u2029\u200e\u200f\u061c\u202a-\u202e\u2066-\u2069]' then
    return jsonb_build_object('ok', false, 'code', 'bad_value');
  end if;
  if v_role in ('first_contact_date','li_connected_date','meeting_date') then
    if v_value !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then
      return jsonb_build_object('ok', false, 'code', 'bad_value');
    end if;
    begin
      perform v_value::date;  -- 2026-02-30 is not a date
    exception when others then
      return jsonb_build_object('ok', false, 'code', 'bad_value');
    end;
  elsif v_role = 'first_contact' then
    if upper(v_value) not in ('TRUE','FALSE','VR','SID') then
      return jsonb_build_object('ok', false, 'code', 'bad_value');
    end if;
    -- One key for chat and dashboard: web.act keeps TRUE and FALSE in upper case.
    v_value := case when upper(v_value) in ('TRUE','FALSE') then upper(v_value) else v_value end;
  end if;

  -- The key has the same text as saley.web.act.instruction_key("write", [id], cells, ""), so a
  -- chat approve and a dashboard click on the same cell and value are one write (D48).
  v_res := saley_act(
    p_user, p_surface, 'write', jsonb_build_array(v_p.entity_id),
    jsonb_build_object(
      'cells', jsonb_build_array(jsonb_build_object('role', v_role, 'value', v_value)),
      'proposal_id', v_p.id),
    encode(sha256(convert_to(
      'write|' || v_p.entity_id || '|' || v_role || '=' || v_value || '|', 'UTF8')), 'hex'),
    p_cooldown, p_undo_minutes, p_allow_email);
  return v_res || jsonb_build_object('choice', p_choice);
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
    + RESOLVE_SIG
    + r""" owner to saley;
  else
    raise notice 'new function stays with %: run deploy/sql/roles.sql', current_user;
  end if;
end $$;
"""
)

GRANT = (
    "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
    f"grant execute on function {RESOLVE_SIG} to saley_ro; end if; end $$"
)


def upgrade() -> None:
    op.execute(_patch(PATCHED, SWAPS))
    op.execute(SALEY_PROPOSAL_RESOLVE)
    op.execute(f"revoke all on function {RESOLVE_SIG} from public")
    op.execute(MOVE_OWNER)
    op.execute(GRANT)


def downgrade() -> None:
    op.execute(f"drop function if exists {RESOLVE_SIG}")
    # 'discord' rows stay in `decision` (0018 owns the table check).
    op.execute(_unpatch())
