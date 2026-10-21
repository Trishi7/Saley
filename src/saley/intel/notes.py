"""Meeting notes: ONE parser for notes from mail, Drive and the local folder (R2, D52, D81).

Why: a note can arrive as a mail body, as a .md / .txt / .pdf attachment, as a file in a Drive
folder (also .docx and Google Docs), or as a file in MEETING_NOTES_DIR. All of them become a
`Note` and go through `extract_commitments`, so the
same text gives the same commitments wherever it came from. No LLM (D52): plain patterns find
"I will / I'll / will send ... by <day>". The owner is the speaker on the line. A due date
comes only from the note text and the note date; a missing or unclear date stays empty and is
never guessed (CLAUDE.md). Note text is DATA. It is matched with patterns and never run,
never followed as an instruction.

D92 (proposed), Q-R3-8: besides first-person promises, a line under a "Next steps" style heading
(Gemini notes) is a commitment of the person it names: `[Name] action`, `- [Name] action`, or
`Name: action`. The name is the owner; `commitments.py` decides if it is a team member. The same
caps, the `by <day>` rule and the quoted-line skip apply. One parser for Drive, mail and files.
"""

from __future__ import annotations

import hashlib
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from saley.core.logging import get_logger

log = get_logger("saley.intel.notes")

NOTE_SUFFIXES = (".md", ".txt", ".pdf")
MAX_NOTE_CHARS = 200_000
MAX_FILE_BYTES = 5_000_000
MAX_PDF_PAGES = 50
MAX_PDF_CHARS = 200_000  # extracted text of one PDF; more is a bomb or not a note (M6)
MAX_DOCX_XML_BYTES = 5_000_000  # the unpacked text part of one .docx (zip bomb guard)
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_LINE_CHARS = 1_000
MAX_COMMITMENT_CHARS = 200
MAX_PER_NOTE = 200
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

MONTHS = {
    m: i
    for i, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for m in names
}
WEEKDAYS = {
    name: i
    for i, names in enumerate(
        (
            ("mon", "monday"),
            ("tue", "tues", "tuesday"),
            ("wed", "wednesday"),
            ("thu", "thur", "thurs", "thursday"),
            ("fri", "friday"),
            ("sat", "saturday"),
            ("sun", "sunday"),
        )
    )
    for name in names
}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_DAY = "|".join(sorted(WEEKDAYS, key=len, reverse=True))

SPEAKER = re.compile(
    r"^\s*(?:\[[^\]]{1,20}\]\s*|\(\d{1,2}:\d{2}\)\s*)?(?:[-*•]\s*)?\*{0,2}"
    r"(?P<who>[A-Z][\w'’.-]{0,24}(?: [A-Z][\w'’.-]{0,24})?)\*{0,2}\s*:\s*\*{0,2}\s*"
    r"(?P<body>\S.*)$"
)
COMMIT = re.compile(
    r"(?:\bI(?:'ll|’ll| will| shall)\s+|^\s*(?=will\s+send\b))(?P<act>(?!not\b).+)$",
    re.IGNORECASE,
)
# D92: the headings that open a list of next steps, and the lines inside it.
STEPS_HEADING = re.compile(
    r"(?:suggested\s+)?next[ -]steps?|action[ -]items?|to[- ]?dos?|follow[- ]?ups?", re.IGNORECASE
)
BULLET = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+")
BOLD_LINE = re.compile(r"(\*\*|__)(?P<t>[^*_]{1,80}?)(\*\*|__)\s*:?")
BARE_HEADING = re.compile(r"[A-Za-z][A-Za-z '’-]{0,40}")
STEP_BRACKET = re.compile(
    r"^\s*(?:(?:[-*•]|\d{1,2}[.)])\s+)?\[(?P<who>[A-Za-z][^\]]{0,39})\]\s*[:\-–]?\s*(?P<body>\S.*)$"
)
MAX_BARE_HEADING_WORDS = 3
MIN_PROSE_WORDS = 4  # a plain line this long, in no step shape, is prose: it ends the section
CHECKBOX = re.compile(r"^(\s*(?:(?:[-*•]|\d{1,2}[.)])\s+)?)\[[ xX]?\]\s*")
BY = re.compile(r"\bby\s+(?P<when>[^,;.]{1,40})", re.IGNORECASE)
ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\b")
DAY_FIRST = re.compile(rf"^(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH})\b\.?(?:,?\s+(\d{{4}}))?", re.I)
MONTH_FIRST = re.compile(
    rf"^({_MONTH})\b\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.I
)
WEEKDAY = re.compile(rf"^({_DAY})\b", re.IGNORECASE)
NOTE_DATE = re.compile(r"^\s*(?:meeting\s+)?date\s*[:\-]\s*(?P<d>.+)$", re.IGNORECASE)
FILE_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


@dataclass(frozen=True)
class Note:
    """One note, whatever its way in. `author` is the sender of a mail, else None."""

    source_type: Literal["mail", "folder", "drive"]
    source_ref: str
    received_at: date | datetime | None
    text: str
    author: str | None = None


@dataclass(frozen=True)
class Found:
    speaker: str
    text: str
    due: date | None
    fingerprint: str  # stable per speaker and sentence, NOT per line: "do not insert twice"
    line: int = 0  # 1-based line of the note text (round 6: the source is file + line)
    action: str = ""  # what they will do, as written (a step line, or the part after "I'll")


class NoteUnreadable(Exception):
    """A note file that is damaged, locked, too big or empty. A data-quality line, never a crash."""


class PdfUnreadable(NoteUnreadable):
    """A PDF that is damaged, locked or has no text."""


# ------------------------------------------------------------------ dates


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_when(text: str, ref: date | None) -> date | None:
    """A day named in note text. Relative days need `ref` (the note date); else None."""
    value = text.strip()
    if m := ISO.match(value):
        return _safe_date(int(m[1]), int(m[2]), int(m[3]))
    if m := DAY_FIRST.match(value):
        return _month_day(int(m[1]), MONTHS[m[2].lower()], m[3], ref)
    if m := MONTH_FIRST.match(value):
        return _month_day(int(m[2]), MONTHS[m[1].lower()], m[3], ref)
    if ref is None:
        return None
    low = value.lower()
    if re.match(r"tomorrow\b", low):
        return ref + timedelta(days=1)
    if re.match(r"today\b", low):
        return ref
    if m := WEEKDAY.match(value):
        # The first such weekday AFTER the note date: "by Friday" said on Friday is next Friday.
        ahead = (WEEKDAYS[m[1].lower()] - ref.weekday()) % 7 or 7
        return ref + timedelta(days=ahead)
    return None


def _month_day(day: int, month: int, year: str | None, ref: date | None) -> date | None:
    if year:
        return _safe_date(int(year), month, day)
    if ref is None:
        return None  # no year in the text and no note date: do not guess one
    found = _safe_date(ref.year, month, day)
    if found is not None and found < ref:
        found = _safe_date(ref.year + 1, month, day)
    return found


def note_day(note: Note) -> date | None:
    """The note's own day: a 'Date:' line in the text, else the day it was received."""
    for line in note.text.splitlines()[:30]:
        if m := NOTE_DATE.match(line):
            found = parse_when(m["d"], None)
            if found:
                return found
    received = note.received_at
    if isinstance(received, datetime):
        return received.date()
    return received


# ------------------------------------------------------------------ the one parser


def _sentences(body: str) -> Iterator[str]:
    for part in re.split(r"(?<=[.!?;])\s+", body):
        if part.strip():
            yield part.strip()


def _fingerprint(speaker: str, sentence: str) -> str:
    key = f"{speaker.casefold()}|{' '.join(sentence.casefold().split())}"
    return hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:12]


def _heading(line: str, in_steps: bool) -> str | None:
    """The words of a heading line, or None. A bare line is a heading only when it opens a
    next-steps section, or (inside one) when it is a short title: then it ends the section."""
    plain = line.strip()
    if not plain or BULLET.match(line):
        return None
    if plain.startswith("#"):
        return plain.lstrip("#").strip().rstrip(":").strip()
    if bold := BOLD_LINE.fullmatch(plain):
        return bold["t"].strip().rstrip(":").strip()
    bare = plain.rstrip(":").strip()
    if plain.endswith(":") or STEPS_HEADING.fullmatch(bare):
        return bare
    if in_steps and BARE_HEADING.fullmatch(bare) and len(bare.split()) <= MAX_BARE_HEADING_WORDS:
        return bare
    return None


def _step(line: str) -> tuple[str, str] | None:
    """(owner, action) of a next-steps line: `[Name] action`, `- [Name] action`, `Name: action`.

    A checkbox marker (`[ ]`, `[x]`) is not an owner: it is cut off first. The action must hold
    a letter or digit, so a bold heading such as `**Decisions:**` is not a step.
    """
    line = CHECKBOX.sub(lambda m: m[1], line, count=1)
    if m := STEP_BRACKET.match(line):
        who, body = m["who"].strip(), m["body"].strip()
    elif m := SPEAKER.match(BULLET.sub("", line, count=1)):
        who, body = m["who"], m["body"].strip()
    else:
        return None
    return (who, body) if re.search(r"\w", body) else None


def _is_prose(line: str) -> bool:
    """A non-bullet line of 4 or more words (not a step, not a heading): a new paragraph."""
    return bool(line.strip()) and not BULLET.match(line) and len(line.split()) >= MIN_PROSE_WORDS


def _found(
    speaker: str, sentence: str, ref: date | None, line_no: int, action: str | None = None
) -> Found:
    by = BY.search(sentence)
    due = parse_when(by["when"], ref) if by else None
    if action is None:
        promise = COMMIT.search(sentence)
        action = promise["act"] if promise else sentence
    return Found(
        speaker,
        sentence[:MAX_COMMITMENT_CHARS],
        due,
        _fingerprint(speaker, sentence),
        line_no,
        " ".join(action.split())[:MAX_COMMITMENT_CHARS],
    )


def extract_commitments(note: Note) -> list[Found]:
    """Every first-person promise and every next step in the note, with owner and (maybe) day.

    D92 (proposed), Q-R3-8: a line under a "Next steps" style heading names its owner. The
    section ends at the next heading. Outside a section only first-person promises count.
    Round 4 (2): a mail note keeps the R2 first-person rule only; the section rule is for notes
    that come from Drive or the local folder.
    """
    sections = note.source_type != "mail"
    ref = note_day(note)
    found: list[Found] = []
    in_steps = False
    for line_no, raw in enumerate(CONTROL.sub(" ", note.text[:MAX_NOTE_CHARS]).splitlines(), 1):
        line = raw[:MAX_LINE_CHARS]
        if line.lstrip().startswith(">"):
            continue  # a quoted earlier mail is someone else's words
        if sections and in_steps and (step := _step(line)):
            # Round 4 (4): a step is checked before the heading rule, so "Sid: Send these:" stays
            # a step although it ends with a colon.
            found.append(_found(step[0], step[1], ref, line_no, step[1]))
            if len(found) >= MAX_PER_NOTE:
                return found
            continue
        heading = _heading(line, in_steps) if sections else None
        if heading is not None and not COMMIT.search(line):
            in_steps = bool(STEPS_HEADING.fullmatch(heading))
            continue
        if in_steps and _is_prose(line):
            in_steps = False  # round 4 (5): plain prose after the steps ends the section
        speaker_match = SPEAKER.match(line)
        if speaker_match:
            speaker, body = speaker_match["who"], speaker_match["body"]
        elif note.author:
            speaker, body = note.author, line
        else:
            continue
        for sentence in _sentences(body):
            if not COMMIT.search(sentence):
                continue
            found.append(_found(speaker, sentence, ref, line_no))
            if len(found) >= MAX_PER_NOTE:
                return found
    return found


# ------------------------------------------------------------------ readers: folder and PDF


def pdf_text(data: bytes) -> str:
    """Text of a PDF. Raises PdfUnreadable for anything that does not give text."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise PdfUnreadable("locked")
        parts: list[str] = []
        total = 0
        for page in reader.pages[:MAX_PDF_PAGES]:
            piece = page.extract_text() or ""
            total += len(piece)
            if total > MAX_PDF_CHARS:  # a decompression bomb stops here, not at the last page
                raise PdfUnreadable("too much text")
            parts.append(piece)
        text = "\n".join(parts)
    except PdfUnreadable:
        raise
    except Exception as exc:  # pypdf raises many types for damaged files
        raise PdfUnreadable(type(exc).__name__) from exc
    if not text.strip():
        raise PdfUnreadable("no text")
    return text


_XML_ENCODING = re.compile(rb"^\s*<\?xml[^>]*?encoding\s*=\s*[\"']([^\"']+)[\"']")
_NOT_UTF8_STARTS = (b"\xff\xfe", b"\xfe\xff", b"\x00", b"<\x00")


def _is_utf8_xml(raw: bytes) -> bool:
    """True only for plain UTF-8 XML. The DOCTYPE check reads bytes, so an XML part in UTF-16
    (where `<!DOCTYPE` has a null between the letters) would pass it and still be parsed (B2)."""
    if raw.startswith(_NOT_UTF8_STARTS):
        return False
    declared = _XML_ENCODING.match(raw.removeprefix(b"\xef\xbb\xbf")[:300])
    return declared is None or declared.group(1).lower().replace(b"_", b"-") in (
        b"utf-8",
        b"utf8",
    )


def docx_text(data: bytes) -> str:
    """Text of a .docx, read with zipfile and xml only (no new dependency). One line per paragraph.

    The XML is refused when it declares a DOCTYPE or entities (entity bombs), and when the
    unpacked text part is bigger than MAX_DOCX_XML_BYTES. Raises NoteUnreadable.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise NoteUnreadable("too big")
            with archive.open(info) as part:
                raw = part.read(MAX_DOCX_XML_BYTES + 1)
    except NoteUnreadable:
        raise
    except (zipfile.BadZipFile, KeyError, OSError, RuntimeError) as exc:
        raise NoteUnreadable(type(exc).__name__) from exc
    if len(raw) > MAX_DOCX_XML_BYTES:
        raise NoteUnreadable("too big")
    if not _is_utf8_xml(raw):
        raise NoteUnreadable("encoding")
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise NoteUnreadable("doctype")
    try:
        root = ET.fromstring(raw)  # noqa: S314 - DOCTYPE and entities are refused above
    except ET.ParseError as exc:
        raise NoteUnreadable("xml") from exc
    lines = [_paragraph_text(p) for p in root.iter(f"{WORD_NS}p")]
    text = "\n".join(lines)
    if not text.strip():
        raise NoteUnreadable("no text")
    return text[:MAX_PDF_CHARS]


def _paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{WORD_NS}t":
            parts.append(node.text or "")
        elif node.tag == f"{WORD_NS}tab":
            parts.append("\t")
        elif node.tag in (f"{WORD_NS}br", f"{WORD_NS}cr"):
            parts.append("\n")
    return "".join(parts)


def decode_text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def day_from_name(name: str) -> date | None:
    m = FILE_DATE.match(name)
    return _safe_date(int(m[1]), int(m[2]), int(m[3])) if m else None


def read_folder(root: Path) -> tuple[list[Note], list[str]]:
    """(notes, problems) for the .md / .txt / .pdf files under `root`.

    Symbolic links and files that resolve outside `root` are ignored. A missing folder gives
    nothing. An unreadable PDF is a problem line (its relative path), not an error.
    """
    notes: list[Note] = []
    problems: list[str] = []
    if not root.is_dir():
        return notes, problems
    base = root.resolve()
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in NOTE_SUFFIXES:
            continue
        if base not in path.resolve().parents:
            continue
        ref = path.resolve().relative_to(base).as_posix()
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                problems.append(ref)
                continue
            data = path.read_bytes()
            body = pdf_text(data) if path.suffix.lower() == ".pdf" else decode_text(data)
        except (PdfUnreadable, OSError):
            problems.append(ref)
            continue
        notes.append(Note("folder", ref, day_from_name(path.name), body))
    return notes, problems
