"""Commitments ledger: notes become rows with an owner and (maybe) a day (S42, D52).

Why: "I will send X by Friday" must not get lost. The parser (`intel/notes.py`) finds the
promise; this module stores it once. The owner is the speaker, matched to a team member by
short code or first name. An unknown speaker is counted and skipped: Saley never assigns a
promise to someone who did not make it. The due day stays empty unless the note names it.
D92 (confirmed), Q-R3-8: a "Next steps" line names its owner; the FIRST WORD of the owner
must equal a short code, a first name or an alias in TEAM_NAME_ALIASES. Exact token, never a
prefix and never a guess. The same promise is never inserted twice (fingerprint, not line).
A promise by someone outside the team is a fact on that person, not a commitment.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.core.clock import now_ist
from saley.core.logging import get_logger
from saley.core.models import Fact
from saley.core.settings import get_settings
from saley.intel.notes import Found, Note, extract_commitments, note_day, read_folder
from saley.sales.parse import norm_name

log = get_logger("saley.intel.commitments")

NOTES_TAB = "notes"  # data_quality.tab prefix for unreadable notes (the sheet sync skips it)
SOURCE_TYPES = {"folder": "meeting_note", "drive": "meeting_note", "mail": "mail"}


@dataclass
class ScanStats:
    notes: int = 0
    added: int = 0
    known: int = 0
    unknown_owner: int = 0  # a team-looking word that does not resolve (ambiguous), or in mail
    unreadable: int = 0
    outside_facts: int = 0  # D92: what an outsider said they will do, kept as a fact on them
    outside_no_contact: int = 0  # the outsider matches no contact, or several: nothing stored

    def note(self) -> str:
        return (
            f"{self.notes} notes · {self.added} new · {self.known} known · "
            f"{self.unknown_owner} unknown owner · {self.unreadable} unreadable"
        )


OWNER_EDGE = ",;:.()[]"
# An entry starts at "<short code><sep>"; its names run to the next entry (any human spelling).
ALIAS_ENTRY = re.compile(r"([A-Za-z][\w.]*)\s*(?:->|→|=|:)\s*")
ALIAS_MARK = re.compile(r"->|→|=|:")
NAME_SPLIT = re.compile(r"[|/;,]+")
OUTSIDE_KIND = "said_will"  # fact kind: an outsider said they will do something
OUTSIDE_CONFIDENCE = 2  # said in a meeting, written by someone else
MAX_FACT_TEXT = 260


def _key(value: str) -> str:
    """One form for a name: case folded, inner spaces collapsed, edge punctuation cut."""
    return " ".join(value.casefold().split()).strip(OWNER_EDGE + " ")


def parse_aliases(raw: str) -> dict[str, list[str]]:
    """`short:alias|alias2,short2:alias` → {short: [alias, ...]}. A bad part is skipped.

    D92/D108 (confirmed): tolerant of the human's own spelling. Between the short code and the
    names `:` `=` `→` `->`; between names and between people any of `|` `/` `;` `,` (spaces
    around them do not matter), so `sid:A,B;vaishnavi:C` and `sid:A|B,vaishnavi:C` read the
    same. Spaces inside a name are kept, so "Sid K" never makes a one-letter alias "k".
    """
    found: dict[str, list[str]] = {}
    entries = list(ALIAS_ENTRY.finditer(raw))
    for i, entry in enumerate(entries):
        end = entries[i + 1].start() if i + 1 < len(entries) else len(raw)
        short = _key(entry.group(1))
        names = NAME_SPLIT.split(raw[entry.end() : end])
        # A piece with a separator but no short code (":x") is junk, never a name.
        aliases = [_key(n) for n in names if _key(n) and not ALIAS_MARK.search(n)]
        if short and aliases:
            found.setdefault(short, []).extend(aliases)
    return found


class TeamIndex(dict[str, int]):
    """Key → user id. `ambiguous` holds the keys two people share (they map to nobody)."""

    ambiguous: frozenset[str] = frozenset()


def team_index(session: Session) -> TeamIndex:
    """Short code, first name and alias of every team member → user id.

    D106: a key two people share maps to nobody, with a warning; never database order. An alias
    equal to the same person's own short code or first name is not a clash. A clash with another
    person drops the alias. All keys are exact words, never prefixes.
    """
    owners: dict[str, set[int]] = {}
    shorts: dict[str, int] = {}

    def claim(key: str, user_id: int) -> None:
        if key:
            owners.setdefault(key, set()).add(user_id)

    users = 0
    for row in session.execute(text("select id, short, name from users order by id")):
        users += 1
        shorts[_key(row.short)] = row.id
        claim(_key(row.short), row.id)
        claim(_key(row.name.split()[0]) if row.name.split() else "", row.id)
    # D102: collect every alias claim first, so an alias two people list goes to nobody whatever
    # the order of the setting.
    alias_owners: dict[str, set[int | None]] = {}
    for short, aliases in parse_aliases(get_settings().team_name_aliases).items():
        for alias in aliases:
            alias_owners.setdefault(alias, set()).add(shorts.get(short))
    loaded = 0
    for alias, claimants in alias_owners.items():
        owner = next(iter(claimants)) if len(claimants) == 1 else None
        if owner is None or (alias in owners and owners[alias] != {owner}):
            log.warning("team_alias_ignored", alias=alias)
            continue
        claim(alias, owner)
        loaded += 1
    index = TeamIndex()
    clash: set[str] = set()
    for key, ids in owners.items():
        if len(ids) == 1:
            index[key] = next(iter(ids))
        else:
            clash.add(key)
            log.warning("team_name_ambiguous", name=key)
    index.ambiguous = frozenset(clash)
    log.info("team_aliases_loaded", users=users, aliases=loaded)  # counts only, no names
    return index


def _first_word(speaker: str) -> str:
    words = _key(speaker).split()
    return words[0].strip(OWNER_EDGE) if words else ""


def _owner_id(index: dict[str, int], speaker: str) -> int | None:
    """D92 (confirmed by the human): the FIRST WORD of the owner, case-insensitive, must equal a
    short code, a first name or an alias. Exact token only: no prefix, no fuzzy match, ever.
    "Sid Kapoor" and "Siddharth Rao" are sid (alias); "Siddhu" and "Priya Sharma" are nobody."""
    return index.get(_first_word(speaker))


def _is_ambiguous(index: dict[str, int], speaker: str) -> bool:
    return _first_word(speaker) in getattr(index, "ambiguous", frozenset())


def _ref(note: Note, item: Found) -> str:
    """Where it was said: `drive:<id>#L<n>`, `file:<path>#L<n>`. Mail keeps note#fingerprint."""
    if note.source_type == "mail":
        return f"{note.source_ref}#{item.fingerprint}"
    prefix = "file:" if note.source_type == "folder" else ""
    return f"{prefix}{note.source_ref}#L{item.line}"


def _like_prefix(note: Note) -> str:
    """The note part of a ref as a LIKE pattern start (the file name may hold % or _)."""
    base = f"file:{note.source_ref}" if note.source_type == "folder" else note.source_ref
    return base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _store(session: Session, note: Note, item: Found, owner: int) -> bool:
    """Insert one promise unless it is stored already. True when a row was added.

    The dedupe is by note, owner and the promise itself (the fingerprint of speaker + sentence),
    never by line: an edit that shifts lines does not store it twice, and the first row keeps its
    first ref. Rows from before round 6 (`note#fingerprint`) count too.
    """
    source_type = SOURCE_TYPES[note.source_type]
    ref = _ref(note, item)
    exists = session.execute(
        text(
            "select 1 from commitment where source_type = :t and owner = :o and ("
            "source_ref = :legacy or (source_ref like :like "
            "and lower(text) = lower(:x)))"
        ),
        {
            "t": source_type,
            "o": owner,
            "legacy": f"{note.source_ref}#{item.fingerprint}",
            "like": f"{_like_prefix(note)}#L%",
            "x": item.text,
        },
    ).first()
    if exists:
        return False
    session.execute(
        text(
            "insert into commitment (owner, text, due, source_type, source_ref) "
            "values (:o, :x, :d, :t, :r)"
        ),
        {"o": owner, "x": item.text, "d": item.due, "t": source_type, "r": ref},
    )
    return True


def _store_outside(session: Session, note: Note, item: Found, stats: ScanStats) -> None:
    """D92 (confirmed): an outsider's promise is a fact on them, never a commitment.

    Attached only when the owner's full text matches exactly ONE contact by the sync's casefold
    name key. None or several: nothing is stored and no contact is made (never invent).
    Note text is data: no LLM, nothing in it is followed; the text is capped.
    """
    ids = list(
        session.scalars(
            text("select id from contact where name_key = :k and removed_at is null limit 2"),
            {"k": norm_name(item.speaker)},
        )
    )
    if len(ids) != 1:
        stats.outside_no_contact += 1
        return
    known = session.execute(
        text(
            "select 1 from fact where entity_type = 'contact' and entity_id = :c and kind = :k "
            "and source_type = 'meeting_note' and value ->> 'fp' = :fp "
            "and source_ref like :like"
        ),
        {
            "c": ids[0],
            "k": OUTSIDE_KIND,
            "fp": item.fingerprint,
            "like": f"{_like_prefix(note)}#L%",
        },
    ).first()
    if known:
        return
    day = note_day(note)
    seen_at = datetime(day.year, day.month, day.day, tzinfo=now_ist().tzinfo) if day else now_ist()
    session.add(
        Fact(
            entity_type="contact",
            entity_id=ids[0],
            kind=OUTSIDE_KIND,
            value={
                "text": f"said they will {item.action}"[:MAX_FACT_TEXT],
                "fp": item.fingerprint,
                "day": day.isoformat() if day else None,
            },
            source_type="meeting_note",
            source_ref=_ref(note, item),
            observed_at=seen_at,
            confidence=OUTSIDE_CONFIDENCE,
        )
    )
    stats.outside_facts += 1


def scan_notes(session: Session, notes: Iterable[Note]) -> ScanStats:
    """Run every note through the one parser and store new promises.

    How the owners split: a first word that resolves → a commitment. A key two people share
    (ambiguous) → `unknown_owner`. Any other word, in a Drive or folder note → an outsider:
    a fact on them or `outside_no_contact`. Mail never makes outside facts, and cannot tell an
    outsider from a typo, so its unresolved owners stay `unknown_owner`.
    """
    stats = ScanStats()
    index = team_index(session)
    for note in notes:
        stats.notes += 1
        for item in extract_commitments(note):
            owner = _owner_id(index, item.speaker)
            if owner is None:
                if note.source_type == "mail" or _is_ambiguous(index, item.speaker):
                    stats.unknown_owner += 1
                else:
                    _store_outside(session, note, item, stats)
                continue
            if _store(session, note, item, owner):
                stats.added += 1
            else:
                stats.known += 1
    return stats


def record_unreadable(
    session: Session, refs: Iterable[str], source: str, now: datetime | None = None
) -> int:
    """Data-quality lines for notes that could not be read. Old lines that cleared are closed."""
    now = now or now_ist()
    tab = f"{NOTES_TAB}:{source}"
    count = 0
    for ref in refs:
        count += 1
        session.execute(
            text(
                "insert into data_quality (tab, row_key, column_role, problem, value, "
                "first_seen, last_seen) values (:tab, :ref, 'note', 'unreadable_note', :ref, "
                ":now, :now) on conflict on constraint uq_data_quality_cell do update set "
                "last_seen = :now, resolved_at = null"
            ),
            {"tab": tab, "ref": ref[:300], "now": now},
        )
    session.execute(
        text(
            "update data_quality set resolved_at = :now where tab = :tab "
            "and resolved_at is null and last_seen < :now"
        ),
        {"tab": tab, "now": now},
    )
    return count


def scan_folder(session: Session, folder: Path) -> ScanStats:
    """Read MEETING_NOTES_DIR and store the promises in it."""
    notes, problems = read_folder(folder)
    stats = scan_notes(session, notes)
    stats.unreadable = record_unreadable(session, problems, "folder")
    return stats
