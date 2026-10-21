"""The validator: a text may only hold values that are in the input facts (CLAUDE.md, R3-G01).

Why: the LLM writes words. It must never invent a URL, an email, a name, a number or a date.
This check is plain code. It extracts those five kinds of value from the text and refuses the
text when one of them is not in the facts the model was given. Used by the scorer `why`, the
`action_hint` and the `row_add` payload (docs/plans/R3.md §3).

Scope: a name is a run of two or more capitalised words ("Dinesh Tewari"). A single capitalised
word is not checked, so common prose does not fail on "Indian" or "Monday".
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'\])]+", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}
_MONTH_WORDS = "|".join(sorted(MONTHS, key=len, reverse=True))
ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DAY_MONTH_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_WORDS})\b\.?(?:,?\s+(\d{{4}}))?", re.IGNORECASE
)
MONTH_DAY_RE = re.compile(
    rf"\b({_MONTH_WORDS})\b\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.IGNORECASE
)
NUMBER_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
CAP_WORD_RE = re.compile(r"[A-Z][a-zA-Z'’\-]+")
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*|[^\sA-Za-z]+")
# Capitalised only because they start a sentence. They never begin a name run.
SENTENCE_STARTERS = frozenset(
    [
        "the",
        "a",
        "an",
        "this",
        "that",
        "these",
        "those",
        "new",
        "it",
        "its",
        "our",
        "their",
        "his",
        "her",
        "we",
        "they",
        "you",
        "i",
        "in",
        "on",
        "at",
        "for",
        "to",
        "of",
        "and",
        "or",
        "but",
        "with",
        "from",
        "by",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "after",
        "before",
        "while",
        "when",
        "how",
        "why",
        "what",
        "who",
        "which",
        "if",
        "so",
        "no",
        "not",
    ]
)
TRAILING = ".,;:!?)]}'\""

type DateKey = tuple[int | None, int, int]


@dataclass(frozen=True)
class Violation:
    """One value the text holds that the facts do not."""

    kind: str  # url | email | number | date | name
    value: str


class GuardViolation(ValueError):
    """The text holds values that are not in the facts."""

    def __init__(self, violations: list[Violation]) -> None:
        self.violations = violations
        shown = ", ".join(f"{v.kind}:{v.value}" for v in violations[:5])
        super().__init__(f"values not in the facts: {shown}")


def _norm_url(raw: str) -> str:
    url = raw.strip().rstrip(TRAILING).lower()
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^www\.", "", url)
    return url.rstrip("/")


def _date_key(year: str | None, month: int, day: int) -> DateKey:
    return (int(year) if year else None, month, day)


def _extract_dates(text: str) -> tuple[set[DateKey], str]:
    """Return the dates in the text and the text with their spans blanked (so digits in a date
    are not read again as numbers)."""
    found: set[DateKey] = set()
    rest = text

    def take(match: re.Match[str], key: DateKey) -> str:
        found.add(key)
        return " " * len(match.group(0))

    rest = ISO_DATE_RE.sub(lambda m: take(m, _date_key(m[1], int(m[2]), int(m[3]))), rest)
    rest = DAY_MONTH_RE.sub(
        lambda m: take(m, _date_key(m[3], MONTHS[m[2].lower()], int(m[1]))), rest
    )
    rest = MONTH_DAY_RE.sub(
        lambda m: take(m, _date_key(m[3], MONTHS[m[1].lower()], int(m[2]))), rest
    )
    return found, rest


def _date_known(key: DateKey, known: set[DateKey]) -> bool:
    year, month, day = key
    if key in known:
        return True
    if year is None:  # "12 March" matches a fact date in any year
        return any(k[1:] == (month, day) for k in known)
    return (None, month, day) in known


def _norm_number(raw: str) -> str:
    cleaned = raw.replace(",", "").rstrip(".")
    if "." in cleaned:
        cleaned = cleaned.rstrip("0").rstrip(".")
    return cleaned.lstrip("0") or "0"


def _name_runs(text: str) -> list[tuple[str, bool]]:
    """Runs of two or more capitalised words, and whether each starts a sentence.

    A leading sentence starter ("The", "This") is cut off first.
    """
    runs: list[tuple[list[str], bool]] = []
    current: list[str] = []
    initial = True
    previous = ""
    start_initial = True
    for token in TOKEN_RE.findall(text):
        if CAP_WORD_RE.fullmatch(token):
            if not current:
                start_initial = initial
            current.append(token)
            initial = False
            continue
        if current:
            runs.append((current, start_initial))
            current = []
        previous = token
        initial = previous[-1] in ".!?:"
    if current:
        runs.append((current, start_initial))
    names = []
    for run, at_start in runs:
        while run and run[0].lower() in SENTENCE_STARTERS:
            run = run[1:]
            at_start = False
        if len(run) >= 2:
            names.append((" ".join(run), at_start))
    return names


def _squash(text: str) -> str:
    return " ".join(text.lower().replace("’", "'").split())


def _join(facts: Iterable[str | None]) -> str:
    return "\n".join(f for f in facts if f)


def find_violations(text: str, facts: Iterable[str | None]) -> list[Violation]:
    """Every URL, email, date, number or name run in `text` that `facts` does not hold."""
    fact_text = _join(facts)
    out: list[Violation] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, value: str) -> None:
        if (kind, value) not in seen:
            seen.add((kind, value))
            out.append(Violation(kind, value))

    fact_urls = {_norm_url(u) for u in URL_RE.findall(fact_text)}
    fact_emails = {e.lower() for e in EMAIL_RE.findall(fact_text)}
    for url in URL_RE.findall(text):
        if _norm_url(url) not in fact_urls:
            add("url", url.rstrip(TRAILING))
    for email in EMAIL_RE.findall(text):
        if email.lower() not in fact_emails:
            add("email", email)

    # URLs and emails carry digits and capitals of their own; read them once, as URLs and emails.
    body = EMAIL_RE.sub(" ", URL_RE.sub(" ", text))
    facts_body = EMAIL_RE.sub(" ", URL_RE.sub(" ", fact_text))

    known_dates, facts_rest = _extract_dates(facts_body)
    text_dates, text_rest = _extract_dates(body)
    for key in sorted(text_dates, key=lambda k: (k[0] or 0, k[1], k[2])):
        if not _date_known(key, known_dates):
            year, month, day = key
            add("date", f"{year or '----'}-{month:02d}-{day:02d}")

    fact_numbers = {_norm_number(n) for n in NUMBER_RE.findall(facts_rest)}
    for number in NUMBER_RE.findall(text_rest):
        if _norm_number(number) not in fact_numbers:
            add("number", number.rstrip("."))

    squashed = _squash(facts_body)
    for name, at_start in _name_runs(body):
        if _squash(name) in squashed:
            continue
        # "Ask Dinesh Tewari": the first word is a verb that only looks like part of a name.
        trimmed = name.split(" ", 1)[1] if at_start and name.count(" ") >= 2 else ""
        if not trimmed or _squash(trimmed) not in squashed:
            add("name", name)
    return out


def passes(text: str, facts: Iterable[str | None]) -> bool:
    return not find_violations(text, facts)


def require_clean(text: str, facts: Iterable[str | None]) -> str:
    """Return the text when it is clean. Raise GuardViolation otherwise."""
    violations = find_violations(text, facts)
    if violations:
        raise GuardViolation(violations)
    return text


def clean_or_empty(text: str, facts: Iterable[str | None]) -> str:
    """The text when it is clean, else an empty string (the scorer drops a bad `why`)."""
    return text if passes(text, facts) else ""
