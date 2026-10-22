"""R3 web fixes: health views, row-add duplicates, undo while the worker pushes.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-19

Why (pipeline stage 5, part 2, findings A1, A7, A9):
- A1 The web role reads views only, so the Sources and health page needs views for what part 1
  counts in Python: `v_news_run_summary` (stale sources), `v_news_scorer` (share scored by rules,
  LLM calls today), `v_news_family` (last good edition per family). `v_search_request` also shows
  `reason` and `http_status` (new columns last: `create or replace view` keeps the column order).
- A9 `saley_act(row_add)`: the duplicate check looks at the person in ANY company (the sync key
  `contact.name_key` is global), uses the key of the sheet sync (Python `casefold`; the web sends
  it as `name_key`, SQL falls back to `lower` plus the German sharp s), and ignores earlier
  `row_add` decisions whose outbox row failed or was cancelled: those never reached the sheet.
- A10 `saley_mark_seen`: the first time a person saw an item stays on record, with one exception.
  A company on the New companies list that got new search hits after that time is shown again;
  when the page marks it again, `seen_at` moves forward, so the company is "seen" once more.
- A7 `saley_undo`: while the worker holds the `append` outbox row (it is writing the row), the
  undo answers `busy` at once and the web returns 409. Deleting a row that is half written would
  race the worker.

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

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ------------------------------------------------------------------ views

NEW_VIEWS: dict[str, str] = {
    # One row per active source that was ever polled. The Python rule decides what is stale.
    "v_news_run_summary": r"""
select n.id as source_id, n.name, n.extractor,
       min(r.started_at) as first_run,
       max(r.started_at) filter (where r.items_new > 0) as last_new,
       max(r.started_at) filter (where r.status = 'ok') as last_ok,
       max(r.started_at) filter (where r.status = '304') as last_304
from news_source_runs r join news_sources n on n.id = r.source_id
where n.active
group by n.id, n.name, n.extractor
""",
    # One row. `llm_ok_today` counts what the daily LLM cap counts (not cached, ok).
    "v_news_scorer": r"""
select (select count(*) from news_stories
         where scored_at >= now() - interval '24 hours') as scored_24h,
       (select count(*) from news_stories
         where scored_at >= now() - interval '24 hours' and scorer_backend = 'rules') as rules_24h,
       (select count(*) from news_stories where rescore_due) as rescore_due,
       (select e.scorer_backend from news_editions e
         where e.scorer_backend is not null order by e.id desc limit 1) as last_backend,
       (select count(*) from llm_calls
         where at >= saley_today()::timestamp at time zone 'Asia/Kolkata'
           and not cached and ok) as llm_ok_today
""",
    # One row per family: news editions, arXiv, OpenAlex.
    "v_news_family": r"""
select f.family,
       e.finished_at as last_edition_at, e.status as last_edition_status,
       (select max(g.finished_at) from news_editions g
         where g.family = f.family and g.status in ('ok','partial')) as last_good_at,
       st.last_story_at
from (values ('news'), ('arxiv'), ('openalex')) f(family)
left join lateral (
  select x.finished_at, x.status from news_editions x
   where x.family = f.family and x.finished_at is not null
   order by x.id desc limit 1) e on true
left join lateral (
  select max(s.first_seen_at) as last_story_at
  from news_stories s join news_sources n on n.id = s.first_source_id
  where case when f.family = 'news' then coalesce(n.extractor, '') not in ('arxiv', 'openalex')
             else n.extractor = f.family end) st on true
""",
}

V_SEARCH_REQUEST_NEW = r"""
select r.id, r.account_id, r.state, r.requested_at, r.finished_at, r.hits, r.error,
       u.short as user_short, r.reason, r.http_status
from search_request r left join users u on u.id = r.user_id
"""

GRANT_VIEWS = (
    "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
    + " ".join(f"grant select on {name} to saley_ro;" for name in (*NEW_VIEWS, "v_search_request"))
    + " end if; end $$"
)

# ------------------------------------------------------------------ functions


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"_m_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _swap(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:  # pragma: no cover - guards a changed 0011 text
        raise RuntimeError(f"0011 function text changed; update 0014 ({old[:40]!r})")
    return text.replace(old, new)


NKEY_OLD = (
    "      v_nkey := lower(regexp_replace(btrim(v_new_cells ->> 'name'), '\\s+', ' ', 'g'));\n"
)
NKEY_NEW = r"""      -- A9: the key of the sheet sync is Python casefold. The web sends it as `name_key`;
      -- another caller gets the closest SQL form (lower, and the sharp s as `ss`).
      if p_payload ? 'name_key' and (
           jsonb_typeof(p_payload -> 'name_key') is distinct from 'string'
           or length(p_payload ->> 'name_key') not between 1 and 400
           or (p_payload ->> 'name_key') ~ '[[:cntrl:]]') then
        return jsonb_build_object('ok', false, 'code', 'bad_value');
      end if;
      v_nkey := coalesce(
        p_payload ->> 'name_key',
        replace(lower(regexp_replace(btrim(v_new_cells ->> 'name'), '\s+', ' ', 'g')),
                'ß', 'ss'));
"""

OLD_SHORTCUT = (
    "       where d.kind = 'row_add' and d.instruction_key = v_ikey2 and d.undone_at is null;\n"
)
NEW_SHORTCUT = r"""       where d.kind = 'row_add' and d.instruction_key = v_ikey2 and d.undone_at is null
         and not exists (select 1 from sync_outbox o
                          where o.decision_id = d.id and o.state in ('failed','cancelled'));
"""

DUP_OLD = r"""      if exists (select 1 from contact c
                  where c.account_id = v_acc.id and c.name_key = v_nkey and c.removed_at is null)
         or exists (select 1 from decision d
                     where d.kind = 'row_add' and d.reason = v_scope and d.undone_at is null) then
"""
DUP_NEW = r"""      -- A9: the person in ANY company (contact.name_key is the sheet's global key). A row add
      -- whose outbox row failed or was cancelled never reached the sheet: it does not count.
      if exists (select 1 from contact c where c.name_key = v_nkey and c.removed_at is null)
         or exists (select 1 from decision d
                     where d.kind = 'row_add' and d.undone_at is null
                       and substring(d.reason from position(':' in d.reason) + 1) = v_nkey
                       and not exists (select 1 from sync_outbox o
                                        where o.decision_id = d.id
                                          and o.state in ('failed','cancelled'))) then
"""

LOCK_OLD = (
    "  select * into v_o from sync_outbox where decision_id = v_d.id and op in ('write','append') "
    "for update;\n"
)
LOCK_NEW = r"""  if v_d.kind = 'row_add' then
    -- A7: the worker holds this row while it writes the sheet row. Do not race it.
    select * into v_o from sync_outbox
     where decision_id = v_d.id and op = 'append' for update skip locked;
    if not found and exists (select 1 from sync_outbox
                              where decision_id = v_d.id and op = 'append') then
      return jsonb_build_object('ok', false, 'code', 'busy');
    end if;
  else
    select * into v_o from sync_outbox
     where decision_id = v_d.id and op in ('write','append') for update;
  end if;
"""


DONE_OLD = "    elsif v_o.state = 'done' then\n"
DONE_NEW = (
    "    elsif v_o.state = 'done'\n"
    "       or (v_o.state = 'failed' and v_d.kind = 'row_add' and v_d.before is not null) then\n"
    "      -- A8: a row add that failed on its signature note is in the sheet: undo removes it.\n"
)


SEEN_OLD = "  on conflict do nothing;\n"
SEEN_NEW = r"""  -- A10: a company with search hits newer than the mark counts as seen again only now.
  on conflict (user_id, item_type, item_id) do update
     set seen_at = now(), query_id = excluded.query_id
   where user_seen.item_type = 'suggest_newco'
     and exists (select 1 from search_hit h
                  where h.account_id = excluded.item_id and h.found_at > user_seen.seen_at);
"""


def _new_seen() -> str:
    return _swap(_load("0011_row_add_seen.py").SALEY_MARK_SEEN, SEEN_OLD, SEEN_NEW)


def _new_act() -> str:
    body = _load("0011_row_add_seen.py")._new_act()
    body = _swap(body, NKEY_OLD, NKEY_NEW)
    body = _swap(body, OLD_SHORTCUT, NEW_SHORTCUT)
    return _swap(body, DUP_OLD, DUP_NEW)


def _new_undo() -> str:
    body = _swap(_load("0011_row_add_seen.py")._new_undo(), LOCK_OLD, LOCK_NEW)
    return _swap(body, DONE_OLD, DONE_NEW)


def upgrade() -> None:
    op.execute(f"create or replace view v_search_request as {V_SEARCH_REQUEST_NEW}")
    for name, body in NEW_VIEWS.items():
        op.execute(f"create or replace view {name} as {body}")
    op.execute(GRANT_VIEWS)

    op.execute(_new_act())
    op.execute(_new_undo())
    op.execute(_new_seen())


def downgrade() -> None:
    older = _load("0011_row_add_seen.py")
    for name in reversed(list(NEW_VIEWS)):
        op.execute(f"drop view if exists {name}")
    op.execute("drop view if exists v_search_request")
    op.execute(f"create view v_search_request as {older.VIEWS['v_search_request']}")
    op.execute(
        "do $$ begin if exists (select 1 from pg_roles where rolname = 'saley_ro') then "
        "grant select on v_search_request to saley_ro; end if; end $$"
    )

    op.execute(older._new_act())
    op.execute(older._new_undo())
    op.execute(older.SALEY_MARK_SEEN)
