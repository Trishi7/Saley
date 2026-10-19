"""Stage per person, derived from sheet columns (docs/06 §6.1, S29, decision D2).

Why: stages are exclusive. One person is in exactly one stage. Order (D29, confirmed):
Won / Lost > Qualified > Meeting > Parked > Engaged > Connected > Contacted > Listed. A Reconnect
Date parks a person only below Meeting. Words for Won, Lost and Qualified follow D31. The reason
is plain words for the stage history in the person drawer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

STAGES = (
    "Listed",
    "Contacted",
    "Connected",
    "Engaged",
    "Meeting",
    "Qualified",
    "Parked",
    "Won",
    "Lost",
)
# Warmth order for "max stage across people" (docs/06 §7). Parked: paused. Lost: cold.
WARMTH_RANK = {
    "Listed": 0,
    "Parked": 1,
    "Contacted": 2,
    "Connected": 3,
    "Engaged": 4,
    "Meeting": 5,
    "Qualified": 6,
    "Lost": 0,
    "Won": 7,
}


_NEGATED = re.compile(r"\b(not|no|un|non)[\s-]*$")


# 'Sid - LI Addition' values that mean no request went out. Same list as saley_li_requested().
NOT_REQUESTED = frozenset(
    {
        "",
        "no",
        "not sent",
        "false",
        "-",
        "n/a",
        "na",
        "couldn't find",
        "could not find",
        "not found",
    }
)


@dataclass(frozen=True)
class StageInputs:
    """The cells the rules read. Built by the sync from one Outreach PoCs row."""

    first_contact: bool = False
    li_status: str | None = None
    email_sent: bool = False
    li_dm_sent: bool = False
    meeting_date: date | None = None
    meeting_status: str | None = None
    prospect_status: str | None = None
    deal_status: str | None = None
    reconnect_date: date | None = None
    closure_prob: int | None = None  # percent; "<50%" arrives as 49 (see parse.parse_prob)


def _low(text: str | None) -> str:
    return (text or "").strip().casefold()


def is_li_connected(li_status: str | None) -> bool:
    """'Sid - LI Addition' = Connected (or Accepted). 'Not connected' is not connected."""
    status = _low(li_status)
    return status.startswith("connected") or status.startswith("accepted")


def li_requested(li_status: str | None) -> bool:
    """An LI request exists: any value except empty, 'no', 'not sent'."""
    return _low(li_status) not in NOT_REQUESTED


def has_word(text: str | None, *words: str) -> bool:
    """A whole word or phrase from `words`, not negated.

    'Not won', 'Not closed won', 'Unsigned', 'not a lead' and "won't" do not count.
    """
    low = _low(text)
    for word in words:
        for match in re.finditer(rf"\b{re.escape(word)}\b(?!'t)", low):
            before = low[: match.start()]
            if _NEGATED.search(before) or re.search(r"\bnot\s+(a|an|closed)\s+$", before):
                continue
            return True
    return False


WON_WORDS = ("won", "signed", "closed won")
LOST_WORDS = ("lost", "dead", "closed lost")
QUALIFIED_WORDS = ("lead", "demo", "quote", "deal")
QUALIFIED_PROB = 50


def derive_stage(row: StageInputs) -> tuple[str, str]:
    """Return (stage, reason). First matching rule wins (D29, D31)."""
    if has_word(row.deal_status, *WON_WORDS):
        return "Won", f"Deal Status is {row.deal_status}"
    if has_word(row.deal_status, *LOST_WORDS):
        return "Lost", f"Deal Status is {row.deal_status}"
    if row.closure_prob == 0:
        return "Lost", "Closure Prob is 0%"
    if has_word(row.prospect_status, *QUALIFIED_WORDS):
        return "Qualified", f"Status is {row.prospect_status}"
    if row.closure_prob is not None and row.closure_prob >= QUALIFIED_PROB:
        return "Qualified", f"Closure Prob is {row.closure_prob}% or more"
    if row.meeting_date is not None or _low(row.meeting_status):
        return "Meeting", "Meeting Date or Meeting Status is set"
    if row.reconnect_date is not None:
        return "Parked", f"Reconnect Date set to {row.reconnect_date:%d %b %Y}"
    if row.email_sent or row.li_dm_sent:
        return "Engaged", "an email or a LinkedIn DM was sent"
    if is_li_connected(row.li_status):
        return "Connected", "LinkedIn request accepted"
    if row.first_contact:
        return "Contacted", "First Contact is set"
    return "Listed", "not contacted yet"
