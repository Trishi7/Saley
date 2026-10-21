"""A view of the scorecard snapshots by day, for "last week N" (R4 ticket 3).

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-20

Why (D118): "last week N" is the snapshot of the day 7 days before TODAY. `v_scorecard_delta`
(0018) joins the LATEST snapshot day to the day 7 before it. The job writes at 23:50, so during
the day the latest snapshot is yesterday's and that view would show 8 days back. The web code
needs one day by name, so this view lists the snapshots as they are and the query asks for
`today - 7`. A missing day gives no row, and the API shows a dash, never 0.

`saley_ro` reads views only (0005); it gets SELECT on this view and nothing on the table. The
view is made by the owner of the table, as in 0018.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AS_OWNER = r"""
do $$
declare
  o name;
begin
  select relowner::regrole::name into o from pg_class
   where oid = 'scorecard_snapshot'::regclass;
  if o is not null and o <> current_user and pg_has_role(current_user, o, 'member') then
    execute format('set local role %I', o);
  end if;
end $$;
"""

VIEW = r"""
create or replace view v_scorecard_history as
select s.day, s.industry, s.people, s.contacted, s.connect_pct, s.reply_pct, s.meetings, s.deals
from scorecard_snapshot s
"""

GRANT = (
    "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
    "grant select on v_scorecard_history to saley_ro; end if; end $$"
)


def upgrade() -> None:
    op.execute(AS_OWNER)
    op.execute(VIEW)
    op.execute(GRANT)
    op.execute("reset role")


def downgrade() -> None:
    op.execute(AS_OWNER)
    op.execute("drop view if exists v_scorecard_history")
    op.execute("reset role")
