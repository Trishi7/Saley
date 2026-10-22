"""R3 review, round 3: undo of a half-written row add, two indexes, a bounded run view.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-19

Why (pipeline stage 5, round 3, findings M1, MED2):
- M1 `saley_undo(row_add)`: the worker saves the plan in `decision.before` before it writes the
  row. When the append worked but the read-back or the network failed, the outbox row is still
  `pending` and the row is in the sheet. The undo used to cancel the push and leave an orphan
  row. Now, when `before` is set, the undo cancels the open push AND queues a `restore`
  (the removal), like for `done` and `failed`. With `before` empty nothing reached the sheet and
  the undo only cancels.
- MED2 `ix_news_stories_scored_at` serves the 24 hour scorer counts; `ix_news_editions_family`
  serves "last edition per family" (`order by id desc limit 1`). `v_news_run_summary` reads only
  the last RUN_DAYS (90) days of runs: the cleanup deletes older runs anyway, so the view no
  longer scans rows the job would remove.

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

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"_m_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _swap(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:  # pragma: no cover - guards a changed 0014 text
        raise RuntimeError(f"0014 function text changed; update 0015 ({old[:40]!r})")
    return text.replace(old, new)


UNDO_OLD = """    if v_o.state in ('pending','held') then
      update sync_outbox set state = 'cancelled', updated_at = now() where id = v_o.id;
      v_cancelled := true;
    elsif v_o.state = 'done'
       or (v_o.state = 'failed' and v_d.kind = 'row_add' and v_d.before is not null) then
      -- A8: a row add that failed on its signature note is in the sheet: undo removes it.
"""
UNDO_NEW = """    if v_o.state in ('pending','held') then
      update sync_outbox set state = 'cancelled', updated_at = now() where id = v_o.id;
      -- M1: a row add with a saved plan may be in the sheet already: only "cancelled" if not.
      v_cancelled := not (v_d.kind = 'row_add' and v_d.before is not null);
    end if;
    if v_o.state = 'done'
       or (v_d.kind = 'row_add' and v_d.before is not null
           and v_o.state in ('pending','held','failed')) then
      -- A8, M1: a row add that failed on its signature note, or whose push is still open after
      -- the append worked, is in the sheet: undo removes it (a restore), the push is cancelled.
"""

NEW_RUN_SUMMARY = r"""
select n.id as source_id, n.name, n.extractor,
       min(r.started_at) as first_run,
       max(r.started_at) filter (where r.items_new > 0) as last_new,
       max(r.started_at) filter (where r.status = 'ok') as last_ok,
       max(r.started_at) filter (where r.status = '304') as last_304
from news_source_runs r join news_sources n on n.id = r.source_id
where n.active and r.started_at >= now() - interval '90 days'
group by n.id, n.name, n.extractor
"""

INDEXES = {
    "news_stories": (
        "create index ix_news_stories_scored_at on news_stories (scored_at)",
        "ix_news_stories_scored_at",
    ),
    "news_editions": (
        "create index ix_news_editions_family on news_editions (family, id desc)",
        "ix_news_editions_family",
    ),
}


def _new_undo() -> str:
    return _swap(_load("0014_r3_web_fixes.py")._new_undo(), UNDO_OLD, UNDO_NEW)


def upgrade() -> None:
    for create, _name in INDEXES.values():
        op.execute(create)
    op.execute(f"create or replace view v_news_run_summary as {NEW_RUN_SUMMARY}")

    op.execute(_new_undo())


def downgrade() -> None:
    older = _load("0014_r3_web_fixes.py")
    op.execute(older._new_undo())

    op.execute(
        f"create or replace view v_news_run_summary as {older.NEW_VIEWS['v_news_run_summary']}"
    )
    for _create, name in INDEXES.values():
        op.execute(f"drop index if exists {name}")
