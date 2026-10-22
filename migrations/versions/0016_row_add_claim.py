"""R3 gate review notes: one live row add per company and person, and an index for news cleanup.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-20

Why (gate step 10 review notes, fixed in R3 on the human's instruction, D111):
- `row_add_claim` has the primary key (account_id, name_key): the company and the normalised
  name (the sync key, D76/A9). `saley_act(row_add)` inserts the claim with the decision. A second
  add of the same person in the same company hits the key and gets `duplicate` with `by` (the
  short code of the person who added it first); the web says "Already added by <who>". The
  check in the function stays; the key is the guard that a race cannot pass.
- The advisory lock of a row add is on the name only. The duplicate check looks at the person
  in ANY company (A9), so a lock per company let two adds of one person in two companies pass.
- A claim ends when its add can no longer reach the sheet: the decision is undone, or its
  append is cancelled, or it failed before the row was written (`decision.before` empty). A row
  add that failed on its signature note is in the sheet (D86): its claim stays until the undo.
  Triggers do this, so the worker, the undo and any later path free the claim the same way.
- `ix_news_seen_story_id`: `news_seen.story_id` is `on delete set null` (0013). The nightly story
  cleanup scanned `news_seen` once per deleted story.

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

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"_m_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _swap(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:  # pragma: no cover - guards a changed 0014 text
        raise RuntimeError(f"0014 function text changed; update 0016 ({old[:40]!r})")
    return text.replace(old, new)


TABLE = r"""
create table row_add_claim (
  account_id integer not null references account (id) on delete cascade,
  name_key text not null,
  decision_id bigint not null unique references decision (id) on delete cascade,
  user_id integer not null references users (id),
  created_at timestamptz not null default now(),
  constraint pk_row_add_claim primary key (account_id, name_key)
);
create index ix_row_add_claim_name on row_add_claim (name_key);
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'saley_ro') then
    revoke all on row_add_claim from saley_ro;
  end if;
end $$;
"""

# Live row adds made before 0016: not undone, push not cancelled, not failed before the write.
BACKFILL = r"""
insert into row_add_claim (account_id, name_key, decision_id, user_id, created_at)
select split_part(d.reason, ':', 1)::integer,
       substring(d.reason from position(':' in d.reason) + 1),
       d.id, d.user_id, d.at
from decision d
where d.kind = 'row_add' and d.undone_at is null and d.reason ~ '^[0-9]+:.'
  and d.user_id is not null
  and exists (select 1 from account a where a.id = split_part(d.reason, ':', 1)::integer)
  and not exists (select 1 from sync_outbox o
                   where o.decision_id = d.id
                     and (o.state = 'cancelled' or (o.state = 'failed' and d.before is null)))
order by d.id
on conflict do nothing;
"""

RELEASE_FN = r"""
create or replace function saley_row_add_release() returns trigger
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  if tg_table_name = 'decision' then
    delete from row_add_claim where decision_id = new.id;
  elsif new.state = 'cancelled'
     or not exists (select 1 from decision d
                     where d.id = new.decision_id and d.before is not null) then
    -- D86: a failed add with a saved plan may be in the sheet; its claim stays until the undo.
    delete from row_add_claim where decision_id = new.decision_id;
  end if;
  return null;
end $$;
revoke all on function saley_row_add_release() from public;
"""

TRIGGERS = {
    "decision": (
        "create trigger trg_row_add_undone after update of undone_at on decision for each row "
        "when (new.kind = 'row_add' and new.undone_at is not null and old.undone_at is null) "
        "execute function saley_row_add_release()",
        "trg_row_add_undone",
    ),
    "sync_outbox": (
        "create trigger trg_row_add_push_ended after update of state on sync_outbox for each row "
        "when (new.op = 'append' and new.state in ('failed','cancelled') "
        "and old.state is distinct from new.state) "
        "execute function saley_row_add_release()",
        "trg_row_add_push_ended",
    ),
}

DECLARE_OLD = "      v_scope text;\n"
DECLARE_NEW = "      v_scope text;\n      v_by text;\n"

LOCK_OLD = "      perform pg_advisory_xact_lock(hashtext('poc' || 'row_add' || v_scope));\n"
LOCK_NEW = (
    "      -- D111: the lock is on the name; the duplicate check covers every company (A9).\n"
    "      perform pg_advisory_xact_lock(hashtext('poc' || 'row_add' || v_nkey));\n"
)

DUP_RETURN_OLD = "        return jsonb_build_object('ok', false, 'code', 'duplicate');\n"
DUP_RETURN_NEW = r"""        select u.short into v_by from row_add_claim c join users u on u.id = c.user_id
         where c.name_key = v_nkey order by c.created_at desc limit 1;
        return jsonb_strip_nulls(jsonb_build_object('ok', false, 'code', 'duplicate', 'by', v_by));
"""

INSERT_OLD = r"""      insert into decision (kind, choice, reason, user_id, surface, tab, role, after,
                            instruction_key, state, undo_until)
      values ('row_add', 'write', v_scope, v_user.id, p_surface, 'poc', 'row_add',
              v_new_cells ->> 'name', v_ikey2, 'active', now() + make_interval(mins => v_mins))
      returning id, undo_until into v_new, v_until2;
"""
INSERT_NEW = r"""      -- D111: the claim key (company, name) is the guard a race cannot pass.
      begin
        insert into decision (kind, choice, reason, user_id, surface, tab, role, after,
                              instruction_key, state, undo_until)
        values ('row_add', 'write', v_scope, v_user.id, p_surface, 'poc', 'row_add',
                v_new_cells ->> 'name', v_ikey2, 'active', now() + make_interval(mins => v_mins))
        returning id, undo_until into v_new, v_until2;
        insert into row_add_claim (account_id, name_key, decision_id, user_id)
        values (v_acc.id, v_nkey, v_new, v_user.id);
      exception when unique_violation then
        select u.short into v_by from row_add_claim c join users u on u.id = c.user_id
         where c.account_id = v_acc.id and c.name_key = v_nkey;
        return jsonb_strip_nulls(jsonb_build_object('ok', false, 'code', 'duplicate', 'by', v_by));
      end;
"""


def _new_act() -> str:
    body = _load("0014_r3_web_fixes.py")._new_act()
    body = _swap(body, DECLARE_OLD, DECLARE_NEW)
    body = _swap(body, LOCK_OLD, LOCK_NEW)
    body = _swap(body, DUP_RETURN_OLD, DUP_RETURN_NEW)
    return _swap(body, INSERT_OLD, INSERT_NEW)


def upgrade() -> None:
    op.execute("create index ix_news_seen_story_id on news_seen (story_id)")

    op.execute(TABLE)
    op.execute(BACKFILL)
    op.execute(RELEASE_FN)
    op.execute(_new_act())
    for create, _name in TRIGGERS.values():
        op.execute(create)


def downgrade() -> None:
    for table, (_create, name) in TRIGGERS.items():
        op.execute(f"drop trigger if exists {name} on {table}")
    op.execute(_load("0014_r3_web_fixes.py")._new_act())
    op.execute("drop function if exists saley_row_add_release()")
    op.execute("drop table if exists row_add_claim")

    op.execute("drop index if exists ix_news_seen_story_id")
