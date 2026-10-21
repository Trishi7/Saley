"""The value a Done tick writes: one the column already uses (D153).

Why: the client sends only the task key. If the browser sent the value, a page bug or a forged
call could put a new word in a sheet column ("completed" where the team writes "Done"). The
server picks the value from `v_column_vocab` (the values the latest Outreach PoCs read holds,
migration 0023). A column with no matching value gives `NoDoneValue`: the route answers 409 and
nothing is written. Saley never invents a word for a column (CLAUDE.md, D153).

Pure functions take the vocabulary as a dict (value -> number of cells), so the rules are
tested without a database. `done_cells` is the one function that reads the view.
"""

from __future__ import annotations

import re
from datetime import date

from sqlalchemy import Connection, text

from saley.core.logging import get_logger
from saley.sales.parse import parse_date

log = get_logger("saley.sales.vocab")

# Words that mean "the meeting happened", best first (D153).
MEETING_DONE_WORDS = ("Completed", "Done", "Met", "Held")
# saley_act accepts TRUE, FALSE, VR and SID for First Contact (migration 0009), so "Yes" and "Y"
# are not offered: a server pick that the database refuses would be a 422 for a valid click.
FIRST_CONTACT_TRUE_FORMS = ("TRUE",)
LI_TYPE_FORMS = ("LI", "LinkedIn")
# Roles whose Done value is a date. saley_act accepts the ISO form only (migration 0009), and
# Google turns an ISO text into a real date cell (writer.native_value).
DATE_ROLES = frozenset({"first_contact_date", "li_connected_date", "meeting_date"})
_SERIAL = re.compile(r"\d{5}")  # a real sheet date, as Google returns it (SERIAL_NUMBER)

# Done kind -> the roles that Done writes, in order.
DONE_ROLES: dict[str, tuple[str, ...]] = {
    "meeting": ("meeting_status",),
    "status_check": ("meeting_status",),
    "li_confirm": ("li_connected_date",),
    "li_first": ("first_contact", "first_contact_type", "first_contact_date"),
}


class NoDoneValue(Exception):
    """The column holds no value that fits this Done. Carries the role for the log line."""

    def __init__(self, role: str) -> None:
        super().__init__(role)
        self.role = role


def _spelling(vocab: dict[str, int], forms: tuple[str, ...]) -> str | None:
    """The column's own spelling of the first form it holds (most used spelling wins)."""
    for form in forms:
        found = {v: n for v, n in vocab.items() if v.strip().casefold() == form.casefold()}
        if found:
            return max(found, key=lambda v: (found[v], v)).strip()
    return None


def _li_type(vocab: dict[str, int]) -> str | None:
    """LI or LinkedIn, whichever form the column uses more. A tie goes to the first form."""
    totals = {
        form: sum(n for v, n in vocab.items() if v.strip().casefold() == form.casefold())
        for form in LI_TYPE_FORMS
    }
    best = max(LI_TYPE_FORMS, key=lambda f: (totals[f], -LI_TYPE_FORMS.index(f)))
    return _spelling(vocab, (best,)) if totals[best] else None


def _holds_dates(vocab: dict[str, int]) -> bool:
    for value in vocab:
        shown = value.strip()
        parsed, _ = parse_date(int(shown) if _SERIAL.fullmatch(shown) else shown)
        if parsed is not None:
            return True
    return False


def pick_value(role: str, vocab: dict[str, int], today: date) -> str | None:
    """The Done value for one role, or None when the column has no fitting value."""
    if role == "meeting_status":
        return _spelling(vocab, MEETING_DONE_WORDS)
    if role == "first_contact":
        return _spelling(vocab, FIRST_CONTACT_TRUE_FORMS)
    if role == "first_contact_type":
        return _li_type(vocab)
    if role in DATE_ROLES:
        return today.isoformat() if _holds_dates(vocab) else None
    return None


def column_vocab(conn: Connection, role: str) -> dict[str, int]:
    """value -> number of cells, for one role (view v_column_vocab)."""
    found = conn.execute(
        text("select value, n from v_column_vocab where role = :r"), {"r": role}
    ).all()
    return {str(v): int(n) for v, n in found}


def done_cells(conn: Connection, kind: str, today: date) -> list[dict[str, str]]:
    """The cells a Done of this kind writes. Raises NoDoneValue for the first role with no value."""
    cells: list[dict[str, str]] = []
    for role in DONE_ROLES[kind]:
        value = pick_value(role, column_vocab(conn, role), today)
        if value is None:
            log.warning("done_value_missing", task=kind, role=role)
            raise NoDoneValue(role)
        cells.append({"role": role, "value": value})
    return cells
