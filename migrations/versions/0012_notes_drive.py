"""R2 meeting notes from Google Drive: file tracking and the source health row (D81).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-19

Why (D81, docs/03 §3):
- `notes_file` keeps one row per Drive file: the file id and the modifiedTime Saley parsed. A
  file is parsed again only when its modifiedTime differs, so each change is read once. A file
  that left the folder (or went to the bin) is marked `gone`; its commitments stay.
- `notes_sync_state` is one row with the last result: ok, folder not shared, API off or error.
  The health line reads it, so a 403 shows as a line and never as a crash.
- `v_notes_source` shows the state row. `v_commitment` gets `source_removed` (last column,
  because `create or replace view` keeps the old column order): true when the commitment came
  from a Drive file that is now gone.
- The web role (`saley_ro`) reads views only. It gets no grant on the two tables.

Table owner (R6, Q-R6-4): the tables stay with the migrating role (saley_admin on the server,
docs/01 §11). The worker writes them through the row grants of `saley` (grants.sql, and the
default privileges of saley_admin). Before R6 they moved to `saley`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = r"""
create table notes_file (
  file_id text primary key,
  name text not null,
  path text not null,
  mime text not null,
  modified_time timestamptz not null,
  parsed_modified timestamptz,
  parsed_at timestamptz,
  state text not null default 'active',
  problem text,
  first_seen timestamptz not null default now(),
  last_seen timestamptz not null default now(),
  gone_at timestamptz,
  constraint ck_notes_file_state check (state in ('active','gone'))
);
create index ix_notes_file_state on notes_file (state);

create table notes_sync_state (
  key text primary key,
  folder_id text not null,
  state text not null,
  detail text,
  files integer not null default 0,
  last_change timestamptz,
  checked_at timestamptz not null,
  last_ok_at timestamptz,
  constraint ck_notes_sync_state check (state in ('ok','folder_not_shared','api_disabled','error'))
);
"""

V_NOTES_SOURCE = """
select s.key, s.folder_id, s.state, s.detail, s.files, s.last_change, s.checked_at, s.last_ok_at
from notes_sync_state s
"""

V_COMMITMENT_NEW = r"""
select m.id, u.short as owner_short, u.name as owner_name, m.text, m.due, m.source_type,
       m.source_ref, m.state, m.reminders_sent, m.last_reminded_on, m.in_pack,
       exists (select 1 from notes_file f
                where f.state = 'gone'
                  and split_part(m.source_ref, '#', 1) = 'drive:' || f.file_id) as source_removed
from commitment m join users u on u.id = m.owner
"""

V_COMMITMENT_OLD = r"""
select m.id, u.short as owner_short, u.name as owner_name, m.text, m.due, m.source_type,
       m.source_ref, m.state, m.reminders_sent, m.last_reminded_on, m.in_pack
from commitment m join users u on u.id = m.owner
"""

GRANT_VIEW = (
    "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
    "grant select on v_notes_source to saley_ro; end if; end $$"
)

# The worker role writes both tables; the web role has no table grant (views only, 0005).
MOVE_OWNER = r"""
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'saley_ro') then
    revoke all on notes_file, notes_sync_state from saley_ro;
  end if;
  if exists (select 1 from pg_roles where rolname = 'saley') and current_user <> 'saley' then
    grant select, insert, update, delete on notes_file, notes_sync_state to saley;
  end if;
end $$;
"""


def upgrade() -> None:
    op.execute(TABLES)
    op.execute(f"create or replace view v_notes_source as {V_NOTES_SOURCE}")
    op.execute(f"create or replace view v_commitment as {V_COMMITMENT_NEW}")
    op.execute(GRANT_VIEW)
    op.execute(MOVE_OWNER)


def downgrade() -> None:
    op.execute("drop view if exists v_notes_source")
    op.execute("drop view v_commitment")
    op.execute(f"create view v_commitment as {V_COMMITMENT_OLD}")
    op.execute(
        "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
        "grant select on v_commitment to saley_ro; end if; end $$"
    )
    op.execute("drop table if exists notes_sync_state")
    op.execute("drop table if exists notes_file")
