"""R7 T1: chat memory, the answer intent queue, event links, and the bot views for the web.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-23

Why (docs/17 §4, D262):
- `chat_message`: every message in the bot's channels (text only, no attachments). The nightly
  job deletes rows older than CHAT_RETENTION_DAYS. `saley chat forget <message_id>` deletes one;
  its facts go with it (`on delete cascade`).
- `chat_summary` (one per channel and day) and `chat_fact` (who said what about which company)
  are written by the nightly summary job from the LLM output, parsed to a fixed schema.
- `answer_intent`: the bot has no LLM key (docs/17 §3). It writes the question here; the worker
  writes the intent (a fixed schema) back. The bot waits at most BOT_INTENT_TIMEOUT_S.
- `discord_event_link`: an M4 event and the Discord scheduled event made for it, once.
- Views for saley_ro (the web reads views only): `v_bot_card`, `v_bot_press`, `v_chat_recent`,
  and `v_bot_health` (the Discord block of Sources & health, M15, `saley bot status`).
- `saley_mark_seen` takes the item type `answer_chat`: the answer's chat lookup is seen per
  person like every other answer list (D75).
Owner model (D256): saley_admin owns all of it; saley has row rights by the default grants.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VIEWS = ("v_bot_card", "v_bot_press", "v_chat_recent", "v_bot_health")

TABLES = r"""
create table chat_message (
  id bigserial primary key,
  channel_id bigint not null,
  message_id bigint not null unique,
  thread_id bigint,
  author_short text,
  author_discord_id bigint,
  text text not null check (length(text) <= 4000),
  reply_to bigint,
  at timestamptz not null
);
create index ix_chat_message_channel_at on chat_message (channel_id, at);
create index ix_chat_message_at on chat_message (at);

create table chat_summary (
  channel_id bigint not null,
  day date not null,
  summary text not null,
  facts integer not null default 0,
  model text,
  at timestamptz not null default now(),
  primary key (channel_id, day)
);

create table chat_fact (
  id bigserial primary key,
  channel_id bigint not null,
  message_id bigint not null references chat_message (message_id) on delete cascade,
  company_id integer references account (id) on delete set null,
  contact_id integer references contact (id) on delete set null,
  text text not null check (length(text) <= 500),
  author_short text,
  at timestamptz not null
);
create index ix_chat_fact_company on chat_fact (company_id, at desc);

create table discord_event_link (
  event_id integer primary key references event (id) on delete cascade,
  discord_event_id bigint not null unique,
  at timestamptz not null default now()
);

create table answer_intent (
  id bigserial primary key,
  user_short text not null,
  question text not null check (length(question) <= 300),
  state text not null default 'waiting' check (state in ('waiting','done','none','failed')),
  intent jsonb,
  created_at timestamptz not null default now(),
  done_at timestamptz
);
create index ix_answer_intent_waiting on answer_intent (created_at) where state = 'waiting';

create view v_bot_card as
select card_id, card_type, shape, channel, state, priority, held_reason, due_at, sent_at,
       created_at, done_by, done_at, done_surface, pinned, edited_at,
       thread_id is not null as has_thread
  from bot_post where card_id is not null;

create view v_bot_press as
select card_id, action, result, code, latency_ms, at from bot_interaction;

create view v_chat_recent as
select channel_id, author_short, at, left(text, 500) as text
  from chat_message where at > now() - interval '90 days';

-- docs/17 §10: the Discord numbers of today (IST) in one row. The M15 card, the Sources &
-- health block and `saley bot status` read this view, so the three never differ.
create view v_bot_health as
with day as (
  select (date_trunc('day', now() at time zone 'Asia/Kolkata') at time zone 'Asia/Kolkata') as t
)
select
  (select count(*) from bot_post, day where card_id is not null and state = 'sent'
     and sent_at >= day.t) as sent_today,
  (select count(*) from bot_post where card_id is not null and state = 'deferred') as held,
  (select count(*) from bot_post, day where card_id is not null and created_at >= day.t
     and (state = 'gone' or (state = 'dropped'
          and held_reason is distinct from 'done before send'))) as failed_today,
  (select count(*) from bot_interaction, day where at >= day.t and result = 'ok') as presses_ok,
  (select count(*) from bot_interaction, day where at >= day.t
     and result = 'already') as presses_already,
  (select count(*) from bot_interaction, day where at >= day.t
     and result = 'refused') as presses_refused,
  (select percentile_cont(0.5) within group (order by latency_ms)
     from bot_interaction, day where at >= day.t) as press_p50_ms,
  (select max(sent_at) from bot_post where state = 'sent') as last_sent,
  (select max(edited_at) from bot_post where card_id is not null) as last_sync,
  (select count(*) from chat_message) as chat_lines,
  (select max(at) from chat_summary) as last_summary;
"""

SEEN_SIG = "saley_mark_seen(text, text, bigint[], text)"
SEEN_OLD = "'answer_due_meeting','answer_due_task')"
SEEN_NEW = "'answer_due_meeting','answer_due_task','answer_chat')"


def _patch_seen(old: str, new: str) -> str:
    """The `chat` answer lookup marks its lines seen per person (D75): one more item type in
    the live text of saley_mark_seen (as 0020 did). A missing pattern stops the migration."""
    quoted_old, quoted_new = old.replace("'", "''"), new.replace("'", "''")
    return (
        "do $$ declare def text; begin "
        f"def := pg_get_functiondef('{SEEN_SIG}'::regprocedure); "
        f"if position('{quoted_old}' in def) = 0 then "
        "raise exception '0028: item types not found in saley_mark_seen'; end if; "
        f"execute replace(def, '{quoted_old}', '{quoted_new}'); end $$"
    )


GRANTS = (
    "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
    + " ".join(f"grant select on {v} to saley_ro;" for v in VIEWS)
    + " end if; end $$"
)


def upgrade() -> None:
    op.execute(TABLES)
    op.execute(GRANTS)
    op.execute(_patch_seen(SEEN_OLD, SEEN_NEW))


def downgrade() -> None:
    op.execute("delete from user_seen where item_type = 'answer_chat'")
    op.execute(_patch_seen(SEEN_NEW, SEEN_OLD))
    for view in reversed(VIEWS):
        op.execute(f"drop view if exists {view}")
    for table in ("answer_intent", "discord_event_link", "chat_fact", "chat_summary"):
        op.execute(f"drop table if exists {table}")
    op.execute("drop table if exists chat_message")
