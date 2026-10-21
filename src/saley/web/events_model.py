"""Events read model: City, Venue and the registered-first order (D160, R5 T6).

Why: the sheet has one Location cell. City is one of three fixed names found in it. Venue is
the whole cell. A Location that names none of them is "Other": Saley never guesses a city.
"""

from __future__ import annotations

import re

from saley.web import words as w

CITY_ALIASES = (
    (w.CITY_BANGALORE, re.compile(r"\b(bangalore|bengaluru|blr)\b", re.IGNORECASE)),
    (w.CITY_LONDON, re.compile(r"\blondon\b", re.IGNORECASE)),
)
SPEAKER_FEATURE = "speaker_match"  # the badge key of /api/me.features

# Registered events first, then the start date (D160). The SQL order, so paging stays correct.
ORDER_UPCOMING = "(t.registered is true) desc, t.start_date asc nulls last, t.event_id"
ORDER_PAST = "(t.registered is true) desc, t.start_date desc nulls last, t.event_id desc"


def city_of(location: str | None) -> str:
    """Bangalore, London or Other. Two cities in one cell are not a guess: they are Other."""
    text = location or ""
    found = [name for name, pattern in CITY_ALIASES if pattern.search(text)]
    return found[0] if len(found) == 1 else w.CITY_OTHER


def venue_of(location: str | None) -> str | None:
    """The full Location text, or None when the cell is blank."""
    text = (location or "").strip()
    return text or None
