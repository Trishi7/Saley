"""Decisions: import from docs/DECISIONS.md, and list them for the Decisions view (R2, S45).

Why: the decision table is the one place the dashboard shows what the team decided, who
confirmed it and from which surface. The docs table stays the human record. The import is
idempotent: run it as often as you like. A row the docs mark "proposed" is `proposed` and
waits for a Confirm; every other row is `active`. A decision already confirmed on the
dashboard is never moved back to proposed by a later import.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from saley.core.clock import tz
from saley.core.logging import get_logger

log = get_logger("saley.intel.decisions")

DECISIONS_FILE = Path(__file__).resolve().parents[3] / "docs" / "DECISIONS.md"
ID_CELL = re.compile(r"^D\d+(–D\d+)?$")
MAX_TEXT = 4000


@dataclass(frozen=True)
class DocDecision:
    ident: str
    day: date | None
    by: str
    text: str
    state: str  # proposed or active


@dataclass
class ImportStats:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0

    def note(self) -> str:
        return (
            f"{self.added} added · {self.updated} updated · {self.unchanged} unchanged"
            f" · {self.skipped} skipped"
        )


def parse_table(body: str) -> tuple[list[DocDecision], int]:
    """(rows, number skipped). Superseded proposals are left out."""
    out: list[DocDecision] = []
    skipped = 0
    for line in body.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or not ID_CELL.match(cells[0]):
            continue
        by = cells[2]
        if "superseded" in by.casefold():
            skipped += 1  # a Confirm button on a replaced proposal would mislead
            continue
        try:
            day: date | None = date.fromisoformat(cells[1])
        except ValueError:
            day = None
        state = "proposed" if by.casefold().startswith("proposed") else "active"
        out.append(DocDecision(cells[0], day, by, cells[3][:MAX_TEXT], state))
    return out, skipped


def _user_id(session: Session, by: str) -> int | None:
    """The team member named first in the 'By' cell ('Trishi (confirmed in chat)')."""
    first = re.split(r"[\s(]", by.strip(), maxsplit=1)[0].casefold()
    if not first:
        return None
    return session.execute(
        text("select id from users where lower(short) = :n or lower(name) = :n"), {"n": first}
    ).scalar()


def import_decisions(session: Session, path: Path = DECISIONS_FILE) -> ImportStats:
    """Insert or update one decision row per docs row. Safe to run again."""
    stats = ImportStats()
    if not path.exists():
        log.warning("decisions_file_missing")
        return stats
    docs, stats.skipped = parse_table(path.read_text(encoding="utf-8"))
    for doc in docs:
        existing = session.execute(
            text(
                "select id, state, reason from decision "
                "where kind = 'strategy_decision' and choice = :c order by id limit 1"
            ),
            {"c": doc.ident},
        ).one_or_none()
        if existing is None:
            at = datetime.combine(doc.day, datetime.min.time(), tz()) if doc.day else None
            session.execute(
                text(
                    "insert into decision (kind, choice, reason, user_id, at, state, "
                    "instruction_key) values ('strategy_decision', :c, :r, :u, "
                    "coalesce(:at, now()), :s, :k)"
                ),
                {
                    "c": doc.ident,
                    "r": doc.text,
                    "u": _user_id(session, doc.by) if doc.state == "active" else None,
                    "at": at,
                    "s": doc.state,
                    "k": f"doc:{doc.ident}",
                },
            )
            stats.added += 1
            continue
        new_state = "active" if existing.state == "proposed" and doc.state == "active" else None
        if new_state is None and existing.reason == doc.text:
            stats.unchanged += 1
            continue
        session.execute(
            text("update decision set reason = :r, state = coalesce(:s, state) where id = :i"),
            {"r": doc.text, "s": new_state, "i": existing.id},
        )
        stats.updated += 1
    return stats


# ------------------------------------------------------------------ the Decisions view

DECISION_COLUMNS = (
    "id, kind, choice, reason, at, surface, role, after, state, undo_until, undo_state, "
    "user_short, user_name, undone_by_name, confirmed_by_name, confirmed_at, "
    "confirmed_surface, contact_id, person, sync_state, sync_error, sync_op"
)


def list_decisions(
    conn: Connection,
    *,
    person: str | None,
    surface: str | None,
    undo: str | None,
    state: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """Decision rows newest first. Every filter is bound, never joined into the SQL."""
    where = (
        "(cast(:person as text) is null or user_short = :person) "
        "and (cast(:surface as text) is null or surface = :surface) "
        "and (cast(:undo as text) is null or undo_state = :undo) "
        "and (cast(:state as text) is null or state = :state)"
    )
    params = {"person": person, "surface": surface, "undo": undo, "state": state}
    total = conn.execute(
        text(f"select count(*) from v_decision where {where}"), params
    ).scalar_one()
    rows = conn.execute(
        text(
            f"select {DECISION_COLUMNS} from v_decision where {where} "
            "order by at desc, id desc limit :limit offset :offset"
        ),
        {**params, "limit": limit, "offset": offset},
    ).mappings()
    return [dict(r) for r in rows], int(total)
