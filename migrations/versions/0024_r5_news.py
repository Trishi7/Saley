"""R5 news: the story topic (T7, D156) and the news-run request (T8, D150, D151).

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-21

Why (D156): the A-L letters are source groups. They do not map 1:1 to the names a person
reads (Hiring, Funding, Product launch, Research, Partnership, Event). `news_stories.topic`
holds one of those six names or Other. Plain Python code sets it (`saley.news.topic`): when a
story is stored, and for old rows through the one-shot job `news_topic_backfill`. It is not done
in SQL, so the rules live in one place. The column stays null until a row is classified.

Run news now (T8, D150, D151): `news_run_request` holds one click of an admin. A partial unique
index lets only ONE row be open (queued or running) at a time. `saley_request_news_run` is a
SECURITY DEFINER function (`set search_path = public, pg_temp`): admin only (`users.role`), one
open request, a 10-minute cooldown since the last request. It first closes an open row that
nobody took in 30 minutes (queued) or that a dead worker left running, so one lost process never
blocks the button for ever. It returns `{ok, code}` like the other saley_* functions. The web
role (`saley_ro`) reads the view `v_news_run_request` and runs the function; it gets no grant on
the table. The job `news_requests` (worker side) takes the oldest queued row with
`FOR UPDATE SKIP LOCKED`.

The two Signals views get `topic` as the LAST column (`create or replace view` keeps the column
order and the grants of `saley_ro`). The downgrade cannot drop that view column (other views
read `v_signal_linked`), so it turns it into a constant null and removes the table column.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TOPICS = ("Hiring", "Funding", "Product launch", "Research", "Partnership", "Event", "Other")
CHECK = "ck_news_stories_topic"
INDEX = "ix_news_stories_topic"
SIGNAL_VIEW_NAMES = ("v_signal_all", "v_signal_linked")
LANE_COLUMN = "fs.lane as lane"

AS_OWNER = r"""
do $$
declare
  o name;
begin
  select relowner::regrole::name into o from pg_class where oid = 'news_stories'::regclass;
  if o is not null and o <> current_user and pg_has_role(current_user, o, 'member') then
    execute format('set local role %I', o);
  end if;
end $$;
"""

GRANT_VIEWS = (
    "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
    + " ".join(
        f"grant select on {name} to saley_ro;" for name in (*SIGNAL_VIEW_NAMES, "v_needs_look")
    )
    + " end if; end $$"
)


REQUEST_TABLE = "news_run_request"
REQUEST_INDEX = "ux_news_run_request_open"
REQUEST_VIEW = "v_news_run_request"
REQUEST_SIG = "saley_request_news_run(text)"
COOLDOWN_MINUTES = 10
OPEN_STALE_MINUTES = 30

CREATE_REQUEST_TABLE = f"""
create table if not exists {REQUEST_TABLE} (
  id bigserial primary key,
  requested_by bigint not null references users(id),
  requested_at timestamptz not null default now(),
  state text not null default 'queued',
  started_at timestamptz,
  finished_at timestamptz,
  note text,
  constraint ck_news_run_request_state check (state in ('queued','running','done','failed'))
)
"""
# One constant expression and a filter: at most one row can be queued or running.
CREATE_REQUEST_INDEX = (
    f"create unique index if not exists {REQUEST_INDEX} on {REQUEST_TABLE} ((true)) "
    "where state in ('queued','running')"
)
CREATE_REQUEST_VIEW = f"""
create or replace view {REQUEST_VIEW} as
select r.id, u.short as requested_by, r.requested_at, r.state, r.started_at, r.finished_at, r.note
from {REQUEST_TABLE} r join users u on u.id = r.requested_by
"""

SALEY_REQUEST_NEWS_RUN = f"""
create or replace function saley_request_news_run(p_user text)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
  v_user users%rowtype;
  v_open {REQUEST_TABLE}%rowtype;
  v_last timestamptz;
  v_id bigint;
begin
  -- D150: only an admin may ask. The web layer checks the session too.
  select * into v_user from users where short = p_user and role = 'admin';
  if not found then
    return jsonb_build_object('ok', false, 'code', 'not_allowed');
  end if;
  perform pg_advisory_xact_lock(hashtext('{REQUEST_TABLE}'));
  update {REQUEST_TABLE} set state = 'failed', finished_at = now(), note = 'no worker took it'
   where state = 'queued' and requested_at < now() - interval '{OPEN_STALE_MINUTES} minutes';
  update {REQUEST_TABLE} set state = 'failed', finished_at = now(),
         note = 'worker stopped during the run'
   where state = 'running' and started_at < now() - interval '{OPEN_STALE_MINUTES} minutes';
  select * into v_open from {REQUEST_TABLE} where state in ('queued','running')
   order by id desc limit 1;
  if found then
    return jsonb_build_object('ok', false, 'code', 'busy', 'request_id', v_open.id,
                              'state', v_open.state);
  end if;
  select max(requested_at) into v_last from {REQUEST_TABLE};
  if v_last is not null and v_last > now() - interval '{COOLDOWN_MINUTES} minutes' then
    return jsonb_build_object(
      'ok', false, 'code', 'cooldown',
      'wait_seconds', ceil(extract(epoch from
        (v_last + interval '{COOLDOWN_MINUTES} minutes' - now())))::int);
  end if;
  insert into {REQUEST_TABLE} (requested_by) values (v_user.id) returning id into v_id;
  return jsonb_build_object('ok', true, 'request_id', v_id, 'state', 'queued');
exception when unique_violation then
  return jsonb_build_object('ok', false, 'code', 'busy');
end
$fn$
"""

MOVE_REQUEST_OWNER = f"""
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'saley')
     and current_user <> 'saley'
     and pg_has_role(current_user, 'saley', 'member') then
    alter function {REQUEST_SIG} owner to saley;
  else
    raise notice 'new function stays with %: run deploy/sql/roles.sql', current_user;
  end if;
end $$;
"""
GRANT_REQUEST = (
    "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
    f"grant select on {REQUEST_VIEW} to saley_ro; "
    f"grant execute on function {REQUEST_SIG} to saley_ro; end if; end $$"
)


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"_m_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _with_topic(body: str) -> str:
    if body.count(LANE_COLUMN) != 1:  # pragma: no cover - guards a changed 0011 text
        raise RuntimeError("0011 view text changed; update 0024")
    return body.replace(LANE_COLUMN, f"{LANE_COLUMN}, s.topic as topic")


def upgrade() -> None:
    op.execute(AS_OWNER)
    op.execute("alter table news_stories add column if not exists topic text")
    values = ", ".join(f"'{t}'" for t in TOPICS)
    op.execute(
        f"alter table news_stories add constraint {CHECK} "
        f"check (topic is null or topic in ({values}))"
    )
    op.execute(f"create index if not exists {INDEX} on news_stories (topic)")
    older = _load("0011_row_add_seen.py")
    for name in SIGNAL_VIEW_NAMES:
        op.execute(f"create or replace view {name} as {_with_topic(older.SIGNAL_VIEWS[name])}")
    op.execute(GRANT_VIEWS)
    op.execute(CREATE_REQUEST_TABLE)
    op.execute(CREATE_REQUEST_INDEX)
    op.execute(CREATE_REQUEST_VIEW)
    op.execute(SALEY_REQUEST_NEWS_RUN)
    op.execute(f"revoke all on function {REQUEST_SIG} from public")
    op.execute(MOVE_REQUEST_OWNER)
    op.execute(GRANT_REQUEST)
    op.execute("reset role")


def downgrade() -> None:
    """Put the 0011 shape back as far as Postgres allows.

    `create or replace view` cannot remove a column, and other views (v_suggest_newco,
    v_saley_suggests, v_needs_look) read v_signal_linked, so the views are not dropped. The
    `topic` column of both Signals views becomes a constant null of the same type, which frees the
    table column. No code of 0023 reads it. A new upgrade replaces it with the real column.
    """
    older = _load("0011_row_add_seen.py")
    op.execute(AS_OWNER)
    op.execute(f"drop function if exists {REQUEST_SIG}")
    op.execute(f"drop view if exists {REQUEST_VIEW}")
    op.execute(f"drop table if exists {REQUEST_TABLE}")
    for name in SIGNAL_VIEW_NAMES:
        body = older.SIGNAL_VIEWS[name].replace(LANE_COLUMN, f"{LANE_COLUMN}, null::text as topic")
        op.execute(f"create or replace view {name} as {body}")
    op.execute(f"drop index if exists {INDEX}")
    op.execute(f"alter table news_stories drop constraint if exists {CHECK}")
    op.execute("alter table news_stories drop column if exists topic")
    op.execute("reset role")
