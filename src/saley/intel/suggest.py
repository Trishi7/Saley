"""Suggestions: score parts for four lists, and the add form for a found person (S07-S10, R11).

Why: rules decide who to suggest, in plain code, with the parts of the score visible. The views
(`v_suggest_*`) give raw facts from the sheet; this module gives each fact points. The LLM takes no
part. Nothing here invents a value: the add form holds only sheet values, the search hit and
what the human types, and the validator (`intel.guard`) checks it before it leaves.
This module knows no words. It returns part codes and points; `web/words.py` gives the text.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from saley.intel.guard import passes

KINDS = ("warm", "best", "newco", "res")

# Points per fact. One place, so a change is one edit and a test.
CONNECTION_POINTS = 3
CONNECTION_CAP = 3
EMAIL_POINTS = 2
DECISION_ROLE_POINTS = 3
P1_POINTS = 2
PAPER_POINTS = 1
ROLE_CLASS_POINTS = {"budget": 4, "research": 3, "data": 3, "user": 2, "other": 0}
GOAL_POINTS = 2
SIGNAL_POINTS = 2
SIGNAL_CAP = 3
TOP_STORY_POINTS = 2
TOP_STORY_MIN = 4
FUNDING_STEPS = ((100_000_000, 4), (10_000_000, 3), (1_000_000, 2), (1, 1))
TOPIC_POINTS = 3
RECENCY_STEPS = ((30, 3), (90, 2), (365, 1))
CITY_EVENT_POINTS = 2
RES_PAPER_POINTS = 2
RES_EMAIL_POINTS = 1

_FUNDING = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([kmb])?", re.IGNORECASE)
_SCALE = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
ADD_ROLES = ("name", "designation", "li_url", "industry")
_TITLE_TAIL = re.compile(r"\s*[|\-–—]\s*linkedin\s*$", re.IGNORECASE)
_TITLE_SPLIT = re.compile(r"\s+[-–—|]\s+")
_AT_COMPANY = re.compile(r"\s+(?:at|@)\s+.+$", re.IGNORECASE)
_PERSON_TOKEN = re.compile(r"^[^\W\d_][\w'’.-]*$")


@dataclass(frozen=True)
class Part:
    """One fact that earned points. `n` is a number to show (people connected, days old)."""

    code: str
    points: int
    n: int | None = None


def funding_usd(text: str | None) -> int | None:
    """'$41M' → 41,000,000. The first number with its K, M or B. None when there is no number."""
    if not text:
        return None
    found = _FUNDING.search(text)
    if not found:
        return None
    number = float(found.group(1).replace(",", ""))
    return int(number * _SCALE[(found.group(2) or "").lower()])


def _step(value: float | int | None, steps: Sequence[tuple[int, int]]) -> int:
    if value is None:
        return 0
    return next((points for limit, points in steps if value >= limit), 0)


def _recency(days: int | None) -> int:
    if days is None or days < 0:
        return 0
    return next((points for limit, points in RECENCY_STEPS if days <= limit), 0)


def parts_warm(p: Mapping[str, Any]) -> list[Part]:
    n = min(int(p.get("connections") or 0), CONNECTION_CAP)
    return [
        Part("connections", n * CONNECTION_POINTS, int(p.get("connections") or 0)),
        Part("email", EMAIL_POINTS if p.get("has_email") else 0),
        Part("decision_role", DECISION_ROLE_POINTS if p.get("decision_role") else 0),
        Part("p1", P1_POINTS if p.get("p1") else 0),
        Part("paper", PAPER_POINTS if p.get("has_paper") else 0),
    ]


def parts_best(p: Mapping[str, Any]) -> list[Part]:
    role = str(p.get("role_class") or "other")
    return [
        Part(f"role_{role}", ROLE_CLASS_POINTS.get(role, 0)),
        Part("email", EMAIL_POINTS if p.get("has_email") else 0),
        Part("p1", P1_POINTS if p.get("p1") else 0),
        Part("paper", PAPER_POINTS if p.get("has_paper") else 0),
    ]


def parts_newco(p: Mapping[str, Any]) -> list[Part]:
    goals = int(p.get("goal_fit") or 0)
    stories = int(p.get("signals_30d") or 0)
    top = p.get("top_importance")
    return [
        Part("goal_fit", goals * GOAL_POINTS, goals),
        Part("funding", _step(funding_usd(p.get("funding")), FUNDING_STEPS)),
        Part("news", min(stories, SIGNAL_CAP) * SIGNAL_POINTS, stories),
        Part("top_story", TOP_STORY_POINTS if top is not None and top >= TOP_STORY_MIN else 0),
    ]


def parts_res(p: Mapping[str, Any]) -> list[Part]:
    days = p.get("fact_days")
    return [
        Part("res_paper", RES_PAPER_POINTS if p.get("has_paper") else 0),
        Part("topic", TOPIC_POINTS if p.get("topic_hit") else 0),
        Part("recent", _recency(days), days),
        Part("city_event", CITY_EVENT_POINTS if p.get("city_event") else 0),
        Part("email", RES_EMAIL_POINTS if p.get("has_email") else 0),
    ]


SCORERS: dict[str, Callable[[Mapping[str, Any]], list[Part]]] = {
    "warm": parts_warm,
    "best": parts_best,
    "newco": parts_newco,
    "res": parts_res,
}


def score(kind: str, parts: Mapping[str, Any]) -> tuple[int, list[Part]]:
    """Total points and the parts that earned any (zero parts are not shown)."""
    earned = [part for part in SCORERS[kind](parts) if part.points > 0]
    return sum(part.points for part in earned), earned


@dataclass(frozen=True)
class Suggestion:
    kind: str
    ref_id: int
    account_id: int | None
    contact_id: int | None
    name: str
    company: str | None
    title: str | None
    score: int
    parts: tuple[Part, ...]
    evidence: Mapping[str, Any]
    raw: Mapping[str, Any]


def build(kind: str, row: Mapping[str, Any]) -> Suggestion:
    total, parts = score(kind, row.get("parts") or {})
    return Suggestion(
        kind=kind,
        ref_id=int(row["ref_id"]),
        account_id=row.get("account_id"),
        contact_id=row.get("contact_id"),
        name=row["name"],
        company=row.get("company"),
        title=row.get("title"),
        score=total,
        parts=tuple(parts),
        evidence=row.get("evidence") or {},
        raw=row.get("parts") or {},
    )


def rank(kind: str, rows: Iterable[Mapping[str, Any]]) -> list[Suggestion]:
    """Score every row, best first. For `best` only the top person of each company stays."""
    items = sorted((build(kind, r) for r in rows), key=lambda s: (-s.score, s.name, s.ref_id))
    if kind != "best":
        return items
    seen: set[int | None] = set()
    top = []
    for item in items:
        if item.account_id in seen:
            continue
        seen.add(item.account_id)
        top.append(item)
    return top


# ------------------------------------------------------------------ the add form (R11, D76)


def parse_hit_title(title: str) -> tuple[str | None, str | None]:
    """'Priya Sharma - Head of Data - Acme | LinkedIn' → ('Priya Sharma', 'Head of Data').

    Both pieces are cut from the title as it is. A piece that does not look like a name is None,
    so the human types it. Nothing is guessed.
    """
    text = _TITLE_TAIL.sub("", title.strip())
    pieces = [p.strip() for p in _TITLE_SPLIT.split(text) if p.strip()]
    if not pieces:
        return None, None
    tokens = pieces[0].split()
    is_name = 2 <= len(tokens) <= 4 and all(_PERSON_TOKEN.match(t) for t in tokens)
    name = pieces[0] if is_name else None
    designation = _AT_COMPANY.sub("", pieces[1]).strip() if len(pieces) > 1 and name else None
    return name, designation or None


def add_form(
    account_name: str, industry: str | None, hit_url: str, hit_title: str
) -> dict[str, Any]:
    """Values for the Add form: sheet company and industry, the hit link, and what the title says.

    The validator confirms that every value is in these facts. `needs` lists what the human must
    still type. The human edits name and designation, then adds (D76).
    """
    name, designation = parse_hit_title(hit_title)
    values = {"name": name, "designation": designation, "li_url": hit_url, "industry": industry}
    facts = [account_name, industry, hit_url, hit_title]
    fields = []
    for role in ADD_ROLES:
        value = values[role]
        if value and passes(value, facts):
            fields.append({"role": role, "value": value})
    have = {f["role"] for f in fields}
    return {
        "company": account_name,
        "fields": fields,
        "needs": [r for r in ("name", "designation") if r not in have],
    }
