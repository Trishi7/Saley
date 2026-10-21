"""Facts: the "working on" line of a person, from the sheet cells (R2, docs/03 §3.2 and §6.3).

Why: the People drawer shows what a person works on WITH its source and date, or "None on
file". Today the sources are the About PoC cell and the Research Paper Link cell of the
Outreach PoCs row. Order of trust: About PoC, then paper (docs/03 §6.3). No LLM, no web
search: the text is the cell text, so nothing is invented (CLAUDE.md).

One fact per statement. A changed cell makes a new fact and the old one points to it
(`superseded_by`); the old row stays as history. A fact that the sheet still shows gets a new
expiry (90 days). A cleared cell ends the fact. Facts from other sources (teammate, news) are
never touched here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from saley.core.clock import now_ist
from saley.core.logging import get_logger
from saley.core.models import Contact, Fact

log = get_logger("saley.intel.facts")

WORKING_ON = "working_on"
EXPIRY = timedelta(days=90)  # docs/03: working_on expires after 90 days
MAX_TEXT = 500
SHEET_SOURCES = ("about_poc", "paper_link")  # source_type values this module owns
CONFIDENCE_SHEET = 3  # a teammate typed it in the sheet


@dataclass
class FactStats:
    added: int = 0
    extended: int = 0
    ended: int = 0

    def note(self) -> str:
        return f"{self.added} new · {self.extended} kept · {self.ended} ended"


def wanted(about: str | None, paper_url: str | None) -> tuple[str, dict[str, Any]] | None:
    """(source_type, value) for a row, or None when both cells are empty."""
    text = " ".join((about or "").split())[:MAX_TEXT]
    url = (paper_url or "").strip() or None
    if text:
        return "about_poc", {"text": text, "url": url}
    if url:
        return "paper_link", {"text": None, "url": url}
    return None


def refresh_working_on(session: Session, now: datetime | None = None) -> FactStats:
    """Bring the sheet-based working_on facts in line with the contact rows."""
    now = now or now_ist()
    stats = FactStats()
    live: dict[int, Fact] = {}
    rows = session.scalars(
        select(Fact)
        .where(
            Fact.entity_type == "contact",
            Fact.kind == WORKING_ON,
            Fact.source_type.in_(SHEET_SOURCES),
            Fact.superseded_by.is_(None),
            (Fact.expires_at.is_(None)) | (Fact.expires_at > now),
        )
        .order_by(Fact.observed_at, Fact.id)
    )
    for fact in rows:
        live[fact.entity_id] = fact  # the newest wins if two live rows ever exist
    for contact in session.scalars(select(Contact).where(Contact.removed_at.is_(None))):
        want = wanted(contact.about, contact.paper_url)
        current = live.get(contact.id)
        if want is None:
            if current is not None:
                current.expires_at = now
                stats.ended += 1
            continue
        source_type, value = want
        if current and current.source_type == source_type and current.value == value:
            current.expires_at = now + EXPIRY
            stats.extended += 1
            continue
        fact = Fact(
            entity_type="contact",
            entity_id=contact.id,
            kind=WORKING_ON,
            value=value,
            source_type=source_type,
            source_ref=contact.sheet_row_key,
            observed_at=now,
            confidence=CONFIDENCE_SHEET,
            expires_at=now + EXPIRY,
        )
        session.add(fact)
        session.flush()
        if current is not None:
            current.superseded_by = fact.id
        stats.added += 1
    log.info("facts_refreshed", added=stats.added, extended=stats.extended, ended=stats.ended)
    return stats
