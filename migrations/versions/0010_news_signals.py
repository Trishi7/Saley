"""R3 news and signals: sources, editions, runs, seen, dropped, stories, signals, search.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-19

Why (docs/05 §6, docs/03 §3.3, docs/plans/R3.md §3):
- `news_sources` is the registry (D71: Google News, arXiv, OpenAlex only in R3).
- `news_source_runs` counts seen / new / dropped per fetch, so the funnel never guesses.
- `news_seen` is the 14-day memory: URL hash and title fingerprint (docs/04 §2).
- `news_stories` holds one row per unique story with its outlets and its score (D72).
- `signal` links a scored story to an account or a person (D74).
- `search_hit` and `search_request` serve the profile-link search (phase 6).
- The web role reads views only: `v_signal_all`, `v_signal_linked`, `v_news_funnel_day`,
  `v_news_source_health`. No table is granted to `saley_ro`.
The funnel is counted per story day (cohort), so fetched >= deduped >= scored >= linked holds
by construction (D73).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = r"""
create table news_sources (
  id serial primary key,
  name text not null unique,
  kind text not null,
  extractor text,
  lane text,
  url text not null,
  tier smallint not null default 3,
  area text,
  categories text[] not null default '{}',
  trusted boolean not null default false,
  ai_only boolean not null default false,
  active boolean not null default true,
  class text,
  poll_active_min integer not null default 120,
  poll_idle_min integer not null default 240,
  active_from smallint,
  active_to smallint,
  weekend_min integer,
  depth integer,
  rate_7d numeric,
  never_miss_min integer,
  next_due_at timestamptz,
  last_ok_at timestamptz,
  last_status text,
  last_error text,
  fail_streak integer not null default 0,
  backoff_until timestamptz,
  route text not null default 'direct',
  etag text,
  last_modified text,
  notes text,
  constraint ck_news_sources_kind
    check (kind in ('rss','atom','page','google','alert','api','mcp')),
  constraint ck_news_sources_route check (route in ('direct','google_site','bing'))
);

create table news_editions (
  id serial primary key,
  slot text not null,
  family text not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text not null default 'running',
  scorer_calls integer not null default 0,
  scorer_backend text,
  note text,
  constraint ck_news_editions_status check (status in ('running','ok','failed'))
);

create table news_source_runs (
  id bigserial primary key,
  source_id integer not null references news_sources(id) on delete cascade,
  edition_id integer references news_editions(id) on delete set null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text not null default 'ok',
  http_status integer,
  items_seen integer not null default 0,
  items_new integer not null default 0,
  dropped_seen integer not null default 0,
  dropped_gate integer not null default 0,
  dropped_copy integer not null default 0,
  bytes integer not null default 0,
  error text,
  route_used text,
  constraint ck_news_source_runs_status check (status in ('ok','304','error','skipped'))
);

create table news_stories (
  id bigserial primary key,
  title text not null,
  summary text not null default '',
  url text not null,
  url_hash text not null,
  fingerprint text not null,
  kind text not null default 'news',
  outlets text[] not null default '{}',
  outlet_count integer not null default 1,
  first_source_id integer references news_sources(id) on delete set null,
  edition_id integer references news_editions(id) on delete set null,
  flag boolean not null default false,
  name_kind text,
  names text[] not null default '{}',
  published_at timestamptz,
  first_seen_at timestamptz not null default now(),
  importance smallint,
  impact smallint,
  tag text,
  why text not null default '',
  scored_at timestamptz,
  scorer_backend text,
  constraint ck_news_stories_kind check (kind in ('news','paper')),
  constraint ck_news_stories_importance check (importance between 1 and 5),
  constraint ck_news_stories_impact check (impact between 0 and 3),
  constraint ck_news_stories_tag
    check (tag in ('Opportunity','People','Risk','Market','Tender','Discover','Ours')),
  constraint ck_news_stories_backend check (scorer_backend in ('llm','rules')),
  constraint ck_news_stories_scored check ((scored_at is null) = (importance is null))
);

create table news_seen (
  url_hash text primary key,
  fingerprint text not null,
  story_id bigint references news_stories(id) on delete cascade,
  seen_at timestamptz not null default now()
);

create table news_dropped (
  id bigserial primary key,
  title text not null,
  url text not null,
  source_id integer references news_sources(id) on delete cascade,
  reason text not null,
  seen_at timestamptz not null default now()
);

create table news_sent (
  story_id bigint not null references news_stories(id) on delete cascade,
  channel text not null,
  sent_at timestamptz not null default now(),
  message_link text,
  primary key (story_id, channel)
);

create table signal (
  id bigserial primary key,
  story_id bigint not null references news_stories(id) on delete cascade,
  entity_type text not null,
  entity_id bigint not null,
  match_kind text not null,
  tag text,
  importance smallint not null,
  impact smallint not null default 0,
  confidence smallint not null default 1,
  verify boolean not null default false,
  action_hint text,
  state text not null default 'new',
  created_at timestamptz not null default now(),
  constraint ck_signal_entity check (entity_type in ('account','contact')),
  constraint ck_signal_match check (match_kind in ('name_exact','alias','domain','author')),
  constraint ck_signal_state check (state in ('new','in_action','dismissed')),
  constraint uq_signal_story_entity unique (story_id, entity_type, entity_id)
);

create table search_request (
  id bigserial primary key,
  user_id integer references users(id),
  account_id integer references account(id) on delete cascade,
  state text not null default 'pending',
  requested_at timestamptz not null default now(),
  finished_at timestamptz,
  hits integer not null default 0,
  error text,
  constraint ck_search_request_state check (state in ('pending','running','done','failed'))
);

create table search_hit (
  id bigserial primary key,
  request_id bigint references search_request(id) on delete cascade,
  query text not null,
  engine text not null,
  url text not null,
  title text not null default '',
  account_id integer references account(id) on delete cascade,
  found_at timestamptz not null default now()
);

create index ix_news_source_runs_source on news_source_runs (source_id, started_at desc);
create index ix_news_source_runs_started on news_source_runs (started_at);
create index ix_news_seen_fingerprint on news_seen (fingerprint, seen_at);
create index ix_news_seen_seen_at on news_seen (seen_at);
create index ix_news_stories_fingerprint on news_stories (fingerprint, first_seen_at);
create index ix_news_stories_first_seen on news_stories (first_seen_at);
create index ix_news_stories_unscored on news_stories (id) where scored_at is null;
create index ix_news_dropped_seen_at on news_dropped (seen_at);
create index ix_signal_story on signal (story_id);
create index ix_signal_entity on signal (entity_type, entity_id);
create index ix_search_hit_account on search_hit (account_id);
create index ix_search_request_state on search_request (state);
"""

VIEWS: dict[str, str] = {
    "v_signal_all": r"""
select s.id as story_id, s.title, s.url, s.summary, s.kind, s.outlets, s.outlet_count,
       s.importance, s.impact, s.tag, s.why, s.flag, s.name_kind, s.names,
       s.published_at, s.first_seen_at, s.scored_at, s.scorer_backend,
       (select count(*) from signal g where g.story_id = s.id) as linked_count
from news_stories s
where s.scored_at is not null
""",
    "v_signal_linked": r"""
select g.id as signal_id, g.story_id, g.entity_type, g.entity_id,
       coalesce(a.name, c.name) as entity_name,
       coalesce(a.name, ca.name) as account_name,
       g.match_kind, g.confidence, g.verify, g.action_hint, g.state,
       g.tag, g.importance, g.impact, g.created_at,
       s.title, s.url, s.outlets, s.outlet_count, s.why, s.kind,
       s.published_at, s.first_seen_at
from signal g
join news_stories s on s.id = g.story_id
left join account a on g.entity_type = 'account' and a.id = g.entity_id
left join contact c on g.entity_type = 'contact' and c.id = g.entity_id
left join account ca on ca.id = c.account_id
""",
    "v_news_funnel_day": r"""
select d.day,
       coalesce(r.fetched, 0) as fetched,
       coalesce(r.dropped_seen, 0) as dropped_seen,
       coalesce(r.dropped_gate, 0) as dropped_gate,
       coalesce(r.dropped_copy, 0) as dropped_copy,
       coalesce(r.tried, 0) as tried,
       coalesce(r.answered, 0) as answered,
       coalesce(t.deduped, 0) as deduped,
       coalesce(t.scored, 0) as scored,
       coalesce(t.scored_4, 0) as scored_4,
       coalesce(t.linked, 0) as linked,
       coalesce(t.sent, 0) as sent
from (select day from (
        select (started_at at time zone 'Asia/Kolkata')::date as day from news_source_runs
        union
        select (first_seen_at at time zone 'Asia/Kolkata')::date from news_stories) x) d
left join (
  select (started_at at time zone 'Asia/Kolkata')::date as day,
         sum(items_seen) as fetched, sum(dropped_seen) as dropped_seen,
         sum(dropped_gate) as dropped_gate, sum(dropped_copy) as dropped_copy,
         count(*) as tried, count(*) filter (where status in ('ok','304')) as answered
  from news_source_runs group by 1) r on r.day = d.day
left join (
  select (s.first_seen_at at time zone 'Asia/Kolkata')::date as day,
         count(*) as deduped,
         count(*) filter (where s.scored_at is not null) as scored,
         count(*) filter (where s.importance >= 4) as scored_4,
         count(*) filter (where s.scored_at is not null
                            and exists (select 1 from signal g where g.story_id = s.id)) as linked,
         count(*) filter (where exists (select 1 from news_sent n where n.story_id = s.id)) as sent
  from news_stories s group by 1) t on t.day = d.day
""",
    "v_news_source_health": r"""
select n.id as source_id, n.name, n.kind, n.lane, n.active, n.last_ok_at, n.last_status,
       n.last_error, n.fail_streak, n.next_due_at, n.backoff_until, n.route,
       coalesce(x.runs_24h, 0) as runs_24h, coalesce(x.seen_24h, 0) as seen_24h,
       coalesce(x.new_24h, 0) as new_24h, coalesce(x.errors_24h, 0) as errors_24h
from news_sources n
left join (
  select source_id, count(*) as runs_24h, sum(items_seen) as seen_24h,
         sum(items_new) as new_24h, count(*) filter (where status = 'error') as errors_24h
  from news_source_runs where started_at > now() - interval '24 hours' group by source_id) x
  on x.source_id = n.id
""",
}

GRANT_VIEWS = (
    "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
    + " ".join(f"grant select on {name} to saley_ro;" for name in VIEWS)
    + " end if; end $$"
)

TABLE_NAMES = (
    "search_hit",
    "search_request",
    "signal",
    "news_sent",
    "news_dropped",
    "news_seen",
    "news_stories",
    "news_source_runs",
    "news_editions",
    "news_sources",
)


def upgrade() -> None:
    op.execute(TABLES)
    for name, body in VIEWS.items():
        op.execute(f"create or replace view {name} as {body}")
    op.execute(GRANT_VIEWS)


def downgrade() -> None:
    for name in reversed(list(VIEWS)):
        op.execute(f"drop view if exists {name}")
    for name in TABLE_NAMES:
        op.execute(f"drop table if exists {name}")
