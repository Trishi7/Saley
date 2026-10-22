"""`saley_mark_seen` accepts the item types of @Saley answers (R4 ticket 6).

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-20

Why (D75, D131): an answer marks the items it showed as seen for that person only. News uses the
type `story`, as the dashboard does, so a story seen in Discord is also seen on the dashboard.
The people and task lists get one type for each question, so a person seen in "who at ..." is
not hidden from "which P1 have no email". The function stays the only writer for the web role.

Function owner: same pattern as 0014. `create or replace` needs the owner, so the step switches
to the owner role when the migrating role is a member of it.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ANSWER_TYPES = (
    "answer_who",
    "answer_working",
    "answer_p1",
    "answer_due_meeting",
    "answer_due_task",
)
LIST_OLD = "'suggest_newco','suggest_res')"
LIST_NEW = (
    "'suggest_newco','suggest_res',\n                            "
    + ",".join(f"'{t}'" for t in ANSWER_TYPES)
    + ")"
)


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"_m_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _new_seen() -> str:
    body = _load("0014_r3_web_fixes.py")._new_seen()
    if body.count(LIST_OLD) != 1:  # pragma: no cover - guards a changed 0011 text
        raise RuntimeError("saley_mark_seen text changed; update 0020")
    return body.replace(LIST_OLD, LIST_NEW)


def upgrade() -> None:
    op.execute(_new_seen())


def downgrade() -> None:
    op.execute(_load("0014_r3_web_fixes.py")._new_seen())
