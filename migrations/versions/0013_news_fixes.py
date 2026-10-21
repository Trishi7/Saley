"""R3 review fixes: URL memory outlives stories, LLM rescore flag, search reason, indexes.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-19

Why (pipeline stage 5, findings A2, A5, A6, B6, B9, B10):
- A5 `news_seen.story_id` is `on delete set null`. The 14-day URL memory must survive the
  cleanup of its story; a seen row now expires only by the 14-day rule.
- A2 `news_stories.rescore_due`: a story scored by `rules` only because the LLM was out is
  scored by the LLM once when it is back. A partial index finds the few rows quickly.
- A4 `news_editions.status` may also be `partial` (some sources failed, some worked).
- B9 `search_request.reason` and `http_status` keep the fixed failure text of a request.
- B10 `notes_sync_state.state` may also be `partial` (the folder listing was cut).
- B6 indexes for the hot lookups and the foreign keys, and two expression indexes on the IST
  day, so `v_news_funnel_day where day = :d` reads one day and not the whole table.
  `ix_user_seen_item` is dropped: the primary key (user, type, id) serves every list query.
Migrations 0010 to 0012 are not edited. (The `drop view v_commitment` in the 0012 downgrade has
no `if exists`; a downgrade that stops there is re-run after `drop view if exists`.)

Table owner: like 0009, the steps switch to the role that owns the table (`set local role`)
when the migrating role is a member of it, and switch back at the end.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _as_owner_of(relation: str) -> str:
    return rf"""
do $$
declare
  o name;
begin
  select relowner::regrole::name into o from pg_class where oid = '{relation}'::regclass;
  if o is not null and o <> current_user and pg_has_role(current_user, o, 'member') then
    execute format('set local role %I', o);
  end if;
end $$;
"""


IST_DAY = "((%s at time zone 'Asia/Kolkata')::date)"

NEW_INDEXES = (
    "create index ix_search_request_account on search_request (account_id, id desc)",
    "create index ix_news_dropped_source on news_dropped (source_id)",
    "create index ix_search_hit_request on search_hit (request_id)",
    "create index ix_news_stories_first_source on news_stories (first_source_id)",
    "create index ix_news_stories_edition on news_stories (edition_id)",
    "create index ix_news_stories_rescore on news_stories (id) where rescore_due",
    f"create index ix_news_stories_day_ist on news_stories ({IST_DAY % 'first_seen_at'})",
    f"create index ix_news_source_runs_day_ist on news_source_runs ({IST_DAY % 'started_at'})",
)
INDEX_NAMES = (
    "ix_search_request_account",
    "ix_news_dropped_source",
    "ix_search_hit_request",
    "ix_news_stories_first_source",
    "ix_news_stories_edition",
    "ix_news_stories_rescore",
    "ix_news_stories_day_ist",
    "ix_news_source_runs_day_ist",
)


def upgrade() -> None:
    op.execute(_as_owner_of("news_stories"))
    op.execute("alter table news_seen drop constraint news_seen_story_id_fkey")
    op.execute(
        "alter table news_seen add constraint news_seen_story_id_fkey "
        "foreign key (story_id) references news_stories(id) on delete set null"
    )
    op.execute("alter table news_stories add column rescore_due boolean not null default false")
    op.execute("alter table news_editions drop constraint ck_news_editions_status")
    op.execute(
        "alter table news_editions add constraint ck_news_editions_status "
        "check (status in ('running','ok','partial','failed'))"
    )
    op.execute("alter table search_request add column reason text")
    op.execute("alter table search_request add column http_status integer")
    for statement in NEW_INDEXES:
        op.execute(statement)
    op.execute("drop index if exists ix_user_seen_item")
    op.execute("reset role")

    op.execute(_as_owner_of("notes_sync_state"))
    op.execute("alter table notes_sync_state drop constraint ck_notes_sync_state")
    op.execute(
        "alter table notes_sync_state add constraint ck_notes_sync_state "
        "check (state in ('ok','partial','folder_not_shared','api_disabled','error'))"
    )
    op.execute("reset role")


def downgrade() -> None:
    op.execute(_as_owner_of("notes_sync_state"))
    op.execute("update notes_sync_state set state = 'error' where state = 'partial'")
    op.execute("alter table notes_sync_state drop constraint ck_notes_sync_state")
    op.execute(
        "alter table notes_sync_state add constraint ck_notes_sync_state "
        "check (state in ('ok','folder_not_shared','api_disabled','error'))"
    )
    op.execute("reset role")

    op.execute(_as_owner_of("news_stories"))
    op.execute("create index if not exists ix_user_seen_item on user_seen (item_type, item_id)")
    for name in INDEX_NAMES:
        op.execute(f"drop index if exists {name}")
    op.execute("alter table search_request drop column if exists http_status")
    op.execute("alter table search_request drop column if exists reason")
    op.execute("update news_editions set status = 'ok' where status = 'partial'")
    op.execute("alter table news_editions drop constraint ck_news_editions_status")
    op.execute(
        "alter table news_editions add constraint ck_news_editions_status "
        "check (status in ('running','ok','failed'))"
    )
    op.execute("alter table news_stories drop column if exists rescore_due")
    # Rows the old cascade would have removed with their story are not recreated.
    op.execute("alter table news_seen drop constraint news_seen_story_id_fkey")
    op.execute(
        "alter table news_seen add constraint news_seen_story_id_fkey "
        "foreign key (story_id) references news_stories(id) on delete cascade"
    )
    op.execute("reset role")
