"""R4 schema: sessions, scorecard snapshots, weekly pack, bot queue and learn log.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-20

Why (docs/plans/R4.md §3, D114, D118, D128-D132):
- `web_session` (D114): server-side sessions. Only the sha256 of the random cookie token is
  stored, never the token. The web role has no table grant. It starts and ends a session through
  two SECURITY DEFINER functions and checks a cookie through `v_web_session` (it reads views
  only, 0005).
- `scorecard_snapshot` (D118): one row per day and industry. `v_scorecard_delta` joins the latest
  day to the day 7 days before. A missing snapshot is NULL, never 0.
- `weekly_pack`: one row per Monday (`week_start`). The unique key stops a double fire of the
  Monday job.
- `bot_post`: the queue every bot message goes through. `for_user` is the person the post counts
  for. A channel post with no mention counts for nobody; `mentions` lists the people it @mentions.
  The caps count DMs and @mentions per person (D132).
- `bot_exchange`: the learn log of questions and answers (`saley_ask_log`).
- `decision.surface` also allows 'discord' (D129: a Discord modal save has its own surface).
- `users.discord_id` exists since 0001. It gets a unique index so one Discord account maps to one
  person.

Owner (R6, Q-R6-4): migrations run as saley_admin and never switch role. saley_admin is a
member of `saley` (D244), so it may replace or drop the saley_* functions that `saley` owns,
and the MOVE_OWNER step gives new functions to `saley` at the end. saley_admin owns every
table and view (docs/01 §11)."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


HEAD = """
language plpgsql
security definer
set search_path = public, pg_temp
"""

SESSION_START_SIG = "saley_session_start(text, text, text, integer)"
SESSION_END_SIG = "saley_session_end(text)"
ASK_LOG_SIG = (
    "saley_ask_log(text, text, text, text, jsonb, text, timestamptz, timestamptz, integer)"
)
NEW_SIGS = (SESSION_START_SIG, SESSION_END_SIG, ASK_LOG_SIG)

TABLES = r"""
create table web_session (
  id bigserial primary key,
  id_hash text not null unique,
  user_id integer not null references users (id),
  provider text not null,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  revoked_at timestamptz,
  constraint ck_web_session_provider check (provider in ('google','mock')),
  constraint ck_web_session_hash check (id_hash ~ '^[0-9a-f]{64}$')
);
create index ix_web_session_user on web_session (user_id);
create index ix_web_session_expires on web_session (expires_at);

create table scorecard_snapshot (
  day date not null,
  industry text not null,
  people integer,
  contacted integer,
  connect_pct numeric,
  reply_pct numeric,
  meetings integer,
  deals integer,
  constraint pk_scorecard_snapshot primary key (day, industry)
);

create table weekly_pack (
  id bigserial primary key,
  week_start date not null unique,
  built_at timestamptz not null default now(),
  body jsonb not null,
  post_id text,
  blocker_post_id text,
  constraint ck_weekly_pack_monday check (extract(isodow from week_start) = 1)
);

create table bot_post (
  id bigserial primary key,
  kind text,
  shape text not null,
  channel text not null,
  for_user integer references users (id),
  mentions integer[] not null default '{}',
  body text not null,
  rank integer default 0,
  due_at timestamptz not null,
  not_before timestamptz,
  state text not null default 'queued',
  sent_at timestamptz,
  msg_id text,
  item_type text,
  item_id bigint,
  is_meeting boolean default false,
  is_reconnect boolean default false,
  created_at timestamptz not null default now(),
  constraint ck_bot_post_channel check (channel in ('test','sales','dm')),
  -- D132: a DM always counts for its recipient, so it cannot slip past the cap.
  constraint ck_bot_post_dm_user check (channel <> 'dm' or for_user is not null),
  constraint ck_bot_post_state check (
    state in ('queued','ready','sent','deferred','dropped'))
);
create index ix_bot_post_state_due on bot_post (state, due_at);
create index ix_bot_post_user_sent on bot_post (for_user, sent_at);

create table bot_exchange (
  id bigserial primary key,
  user_id integer references users (id),
  surface text,
  text text,
  intent text,
  refs jsonb,
  answer text,
  window_from timestamptz,
  window_to timestamptz,
  left_out integer,
  created_at timestamptz not null default now(),
  constraint ck_bot_exchange_surface check (surface in ('discord','dashboard'))
);
create index ix_bot_exchange_user_at on bot_exchange (user_id, created_at desc);

create unique index uq_users_discord_id on users (discord_id);
"""

NEW_TABLE_NAMES = ("web_session", "scorecard_snapshot", "weekly_pack", "bot_post", "bot_exchange")

# saley_ro reads views only (0005): take back whatever default privileges gave on the tables.
REVOKE_TABLES = (
    "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
    + " ".join(f"revoke all on {name} from saley_ro;" for name in NEW_TABLE_NAMES)
    + " end if; end $$"
)

VIEWS: dict[str, str] = {
    "v_web_session": r"""
select s.id_hash, u.short as user_short, s.provider, s.expires_at, s.revoked_at
from web_session s join users u on u.id = s.user_id
""",
    "v_weekly_pack": r"""
select p.id, p.week_start, p.built_at, p.body, p.post_id, p.blocker_post_id
from weekly_pack p
""",
    # D118: today = the latest snapshot day; last week = the snapshot of the day 7 days before.
    # A missing snapshot gives NULL (shown as a dash), never 0.
    "v_scorecard_delta": r"""
select t.industry, t.day, t.people, t.contacted, t.connect_pct, t.reply_pct, t.meetings, t.deals,
       w.people as last_week_people, w.contacted as last_week_contacted,
       w.connect_pct as last_week_connect_pct, w.reply_pct as last_week_reply_pct,
       w.meetings as last_week_meetings, w.deals as last_week_deals
from scorecard_snapshot t
left join scorecard_snapshot w on w.industry = t.industry and w.day = t.day - 7
where t.day = (select max(day) from scorecard_snapshot)
""",
    "v_bot_queue_health": r"""
select b.state, count(*) as n, max(b.sent_at) as last_sent_at
from bot_post b
group by b.state
""",
}

GRANT_VIEWS = (
    "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
    + " ".join(f"grant select on {name} to saley_ro;" for name in VIEWS)
    + " end if; end $$"
)

# Control characters other than tab, newline and carriage return are refused (as in 0011).
CTRL = r"'[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]'"

SALEY_SESSION_START = (
    r"""
create or replace function saley_session_start(
  p_user_short text, p_provider text, p_id_hash text, p_hours integer)
returns jsonb
"""
    + HEAD
    + r"""
as $fn$
declare
  v_user users%rowtype;
  v_exp timestamptz;
begin
  if p_provider is null or p_provider not in ('google','mock')
     or p_id_hash is null or p_id_hash !~ '^[0-9a-f]{64}$'
     or p_hours is null or p_hours not between 1 and 720 then
    return jsonb_build_object('ok', false, 'code', 'bad_request');
  end if;
  -- The allowlist (WEB_ALLOWED_EMAILS) is checked by the web code; the user must also exist
  -- here with a known role. No row, no session.
  select * into v_user from users where short = p_user_short and role in ('viewer','approver','admin');
  if not found then
    return jsonb_build_object('ok', false, 'code', 'not_allowed');
  end if;
  v_exp := now() + make_interval(hours => p_hours);
  begin
    insert into web_session (id_hash, user_id, provider, expires_at)
    values (p_id_hash, v_user.id, p_provider, v_exp);
  exception when unique_violation then
    return jsonb_build_object('ok', false, 'code', 'duplicate');
  end;
  return jsonb_build_object('ok', true, 'expires_at', v_exp);
end
$fn$
"""
)

SALEY_SESSION_END = (
    r"""
create or replace function saley_session_end(p_id_hash text)
returns jsonb
"""
    + HEAD
    + r"""
as $fn$
declare
  v_n integer;
begin
  if p_id_hash is null or p_id_hash !~ '^[0-9a-f]{64}$' then
    return jsonb_build_object('ok', false, 'code', 'bad_request');
  end if;
  update web_session set revoked_at = now() where id_hash = p_id_hash and revoked_at is null;
  get diagnostics v_n = row_count;
  return jsonb_build_object('ok', v_n = 1);
end
$fn$
"""
)

SALEY_ASK_LOG = (
    r"""
create or replace function saley_ask_log(
  p_user_short text, p_surface text, p_text text, p_intent text, p_refs jsonb, p_answer text,
  p_from timestamptz, p_to timestamptz, p_left_out integer)
returns bigint
"""
    + HEAD
    + r"""
as $fn$
declare
  v_user users%rowtype;
  v_id bigint;
begin
  -- A bigint cannot carry a refusal code, so a bad call raises (22023).
  if p_surface is null or p_surface not in ('discord','dashboard')
     or p_text is null or length(p_text) > 2000 or p_text ~ """
    + CTRL
    + r"""
     or (p_intent is not null and (length(p_intent) > 64 or p_intent ~ """
    + CTRL
    + r"""))
     or (p_answer is not null and (length(p_answer) > 4000 or p_answer ~ """
    + CTRL
    + r"""))
     or (p_refs is not null and (jsonb_typeof(p_refs) not in ('object','array')
                                 or length(p_refs::text) > 8000))
     or (p_left_out is not null and p_left_out not between 0 and 100000) then
    raise exception 'saley_ask_log: bad_request' using errcode = '22023';
  end if;
  select * into v_user from users where short = p_user_short;
  if not found then
    raise exception 'saley_ask_log: not_allowed' using errcode = '22023';
  end if;
  insert into bot_exchange (user_id, surface, text, intent, refs, answer, window_from,
                            window_to, left_out)
  values (v_user.id, p_surface, p_text, p_intent, p_refs, p_answer, p_from, p_to, p_left_out)
  returning id into v_id;
  return v_id;
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

SURFACE_NEW = "surface is null or surface in ('dashboard','chat','card','discord')"
SURFACE_OLD = "surface is null or surface in ('dashboard','chat','card')"


def upgrade() -> None:
    op.execute("alter table decision drop constraint ck_decision_surface")
    op.execute(f"alter table decision add constraint ck_decision_surface check ({SURFACE_NEW})")
    op.execute(TABLES)
    op.execute(REVOKE_TABLES)
    for name, body in VIEWS.items():
        op.execute(f"create or replace view {name} as {body}")
    op.execute(GRANT_VIEWS)

    op.execute(SALEY_SESSION_START)
    op.execute(SALEY_SESSION_END)
    op.execute(SALEY_ASK_LOG)
    for sig in NEW_SIGS:
        op.execute(f"revoke all on function {sig} from public")
    op.execute(MOVE_OWNER)
    op.execute(GRANT_FUNCTIONS)


def downgrade() -> None:
    for sig in NEW_SIGS:
        op.execute(f"drop function if exists {sig}")

    for name in reversed(tuple(VIEWS)):
        op.execute(f"drop view if exists {name}")
    op.execute("drop index if exists uq_users_discord_id")
    for name in reversed(NEW_TABLE_NAMES):
        op.execute(f"drop table if exists {name}")
    # Rows that only 0018 allows are remapped first: the older check would refuse them.
    op.execute("update decision set surface = 'chat' where surface = 'discord'")
    op.execute("alter table decision drop constraint ck_decision_surface")
    op.execute(f"alter table decision add constraint ck_decision_surface check ({SURFACE_OLD})")
