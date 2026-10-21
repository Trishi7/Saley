"""Inbox view lists reminders (R2 phase 6).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-19

Why: R2 shows reminders in the Inbox only (no Discord until R4). `v_inbox` gets one more
branch for pending reminders. A reminder leaves the Inbox when its commitment is closed or the
due day has passed: after the due day the weekly pack carries the item, never a new reminder.
The view keeps its columns, so the `saley_ro` grant stays. Migration 0006 is not edited.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASE = r"""
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
"""

REMINDERS = r"""
union all
select 'reminder', r.id, r.kind, r.text,
       jsonb_build_object('commitment_id', r.commitment_id, 'due', c.due), 0, r.fire_at,
       u.short
from reminder r
join users u on u.id = r.for_user
join commitment c on c.id = r.commitment_id
where r.state = 'pending' and c.state = 'open'
  and (c.due is null or c.due >= saley_today())
"""


TASK_INPUTS_HEAD = r"""
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
"""

# R05 ranks by the Application Bucket (A-F); R13 needs the sent flags when a date is missing;
# D31 says a First Contact text of "Sid" keeps the email steps on Sid's list.
TASK_INPUTS_TAIL = r"""
       , ct.application_bucket, c.owner_source, ct.email1_sent, ct.email2_sent,
       ct.email3_sent, ct.li_dm_sent
"""

TASK_INPUTS_FROM = "from v_contact_stage c"
TASK_INPUTS_JOIN = "from v_contact_stage c join contact ct on ct.id = c.id"


def upgrade() -> None:
    op.execute(f"create or replace view v_inbox as {BASE}{REMINDERS}")
    op.execute(
        f"create or replace view v_task_inputs as {TASK_INPUTS_HEAD}{TASK_INPUTS_TAIL}"
        f"{TASK_INPUTS_JOIN}"
    )


def downgrade() -> None:
    op.execute(f"create or replace view v_inbox as {BASE}")
    # A view cannot lose columns with `create or replace`: drop it and make it again.
    op.execute("drop view v_task_inputs")
    op.execute(f"create view v_task_inputs as {TASK_INPUTS_HEAD}{TASK_INPUTS_FROM}")
    op.execute(
        "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
        "grant select on v_task_inputs to saley_ro; end if; end $$"
    )
