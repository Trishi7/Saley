"""A partial index on `decision (act_txid)` (R5 review follow-up, R6).

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-22

Why: `saley_undo_group` (0023) finds the cells of one act by `act_txid`. The decision table only
grows, so without an index that lookup reads the whole table on every grouped Undo. Old rows
have a null `act_txid` (no group) and never match, so the index leaves them out.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_decision_act_txid"

AS_OWNER = r"""
do $$
declare
  o name;
begin
  select relowner::regrole::name into o from pg_class where oid = 'decision'::regclass;
  if o is not null and o <> current_user and pg_has_role(current_user, o, 'member') then
    execute format('set local role %I', o);
  end if;
end $$;
"""


def upgrade() -> None:
    op.execute(AS_OWNER)
    op.execute(
        f"create index if not exists {INDEX} on decision (act_txid) where act_txid is not null"
    )
    op.execute("reset role")


def downgrade() -> None:
    op.execute(AS_OWNER)
    op.execute(f"drop index if exists {INDEX}")
    op.execute("reset role")
