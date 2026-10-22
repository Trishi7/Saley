"""The story topic: seven names and Other, set by plain code (D156, D239).

Why: the A-L letters in the source registry are source groups, not what a story is about. A person
filters Signals by Hiring, Funding, Product launch, Research, Partnership, Opportunity or Event.
These rules live in ONE place. New stories get a topic when they are stored; old rows get one from
the job `news_topic_backfill`. The LLM never picks a topic.

Order of the rules (first match wins): a paper (arXiv or OpenAlex) is Research. Then the title is
checked against the keyword sets in the order Funding, Hiring, Partnership, Product launch,
Opportunity, Event; only if the title has no match is the summary checked the same way. "Raises
$5M to launch" is Funding because money news is the stronger signal for sales. Opportunity (a call
for proposals, a tender, a grant) comes just before Event (the human, D239): a call for proposals
at a summit is an opening to answer, not only a date.
"""

from __future__ import annotations

import re
from typing import Final

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from saley.core.models import NewsStory

TOPIC_HIRING: Final = "Hiring"
TOPIC_FUNDING: Final = "Funding"
TOPIC_PRODUCT: Final = "Product launch"
TOPIC_RESEARCH: Final = "Research"
TOPIC_PARTNERSHIP: Final = "Partnership"
TOPIC_EVENT: Final = "Event"
TOPIC_OPPORTUNITY: Final = "Opportunity"
TOPIC_OTHER: Final = "Other"

#: The seven names a person can filter by. `Other` is the eighth value, never a filter chip.
TOPIC_NAMES: Final = (
    TOPIC_HIRING,
    TOPIC_FUNDING,
    TOPIC_PRODUCT,
    TOPIC_RESEARCH,
    TOPIC_PARTNERSHIP,
    TOPIC_OPPORTUNITY,
    TOPIC_EVENT,
)
TOPICS: Final = (*TOPIC_NAMES, TOPIC_OTHER)

PAPER_KIND: Final = "paper"  # arXiv and OpenAlex items (news/fetch.py)
SUMMARY_CHARS: Final = 600
BACKFILL_BATCH: Final = 500

#: (topic, pattern) in the order of the rules. Whole words, case-insensitive.
_RULES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        (
            TOPIC_FUNDING,
            # "raises" only with money ("raises $20M", "raised Rs 50 crore"): a bare "raises
            # concerns" is not funding. A bare "investors" is too broad (T7 review).
            r"\b(?:(?:raises?|raised|secures?|secured|bags?|bagged)\s+(?:\$|usd\s*|inr\s*|rs\.?\s*|₹|€|£)?\d|"
            r"(?:raises?|raised) (?:funds|capital|money)|"
            r"funding|fundraise|series [a-e]\b|seed round|pre-seed|valuation|unicorn|"
            r"backed by|ipo\b|acquires?\b|acquired\b|acquisition|"
            # D239 (the human's review of the D177 sets, 22 Oct): "acquired by" is in "acquired".
            r"investments?\b|invests? in\b|close[sd]? (?:a|an|its) (?:\S+ ){0,2}round\b|"
            r"mergers?\b)",
        ),
        (
            TOPIC_HIRING,
            r"\b(hires?|hired|hiring|appoints?|appointed|names? new|new (ceo|cto|coo|cfo|chief)|"
            r"joins as|steps down|job openings?|recruit(s|ing|ment)?|"
            r"head of|vp of|layoffs?)\b",  # the last line: D239
        ),
        (
            TOPIC_PARTNERSHIP,
            r"\b(partners? with|partnership|partnered|teams? up|tie-?ups?|ties up|alliance|"
            r"collaborat\w*|joins forces|mou|signs (a |an )?(deal|agreement|pact))\b",
        ),
        (
            TOPIC_PRODUCT,
            r"\b(launch(es|ed)?|unveils?|unveiled|introduces?|introduced|rolls? out|rolled out|"
            # A bare "releases" matches "press releases": only "releases a/an/its/new".
            r"debuts?|(?:releases?|released) (?:a|an|its|new)|now available|"
            r"open-sources?|new model|"
            r"announces (a |an )?new|"
            # D239. "GA" counts only in capitals: a lower-case "ga" is a name or a word part.
            r"beta|general availability|(?-i:GA)|open[- ]source)\b",
        ),
        (
            TOPIC_OPPORTUNITY,
            # D239: an opening a company can answer. Checked before Event.
            r"\b(calls? for proposals|cfps?|rfps?|tenders?|grants?|pilot programmes?|"
            r"expressions? of interest|applications open)\b",
        ),
        (
            TOPIC_EVENT,
            r"\b(conference|summit|webinar|hackathon|expo|keynote|workshop|meet-?up|"
            r"roadshow|symposium)\b",
        ),
    )
)


def topic_for(title: str, summary: str = "", kind: str = "news") -> str:
    """The topic of one story. Plain code, same answer every time for the same input."""
    if kind == PAPER_KIND:
        return TOPIC_RESEARCH
    for text in (title or "", (summary or "")[:SUMMARY_CHARS]):
        for name, pattern in _RULES:
            if pattern.search(text):
                return name
    return TOPIC_OTHER


def category_name(value: str | None) -> str | None:
    """Map a `cat=` value to its canonical name, case-insensitive. None when it is not a topic.

    A letter (A-L), a number or any other text gives None: the caller answers 422 (D156).
    """
    if value is None:
        return None
    wanted = value.strip().casefold()
    for name in TOPICS:
        if name.casefold() == wanted:
            return name
    return None


def backfill_topics(session: Session, batch: int = BACKFILL_BATCH) -> int:
    """Set the topic of every story that has none. Returns how many rows it set.

    Only rows with a null topic are read, so a second run finds nothing and does nothing. Rows
    are taken in id order, `batch` at a time, so one run never holds a huge result.
    """
    done = 0
    while True:
        rows = session.execute(
            select(NewsStory.id, NewsStory.title, NewsStory.summary, NewsStory.kind)
            .where(NewsStory.topic.is_(None))
            .order_by(NewsStory.id)
            .limit(batch)
        ).all()
        if not rows:
            return done
        for row in rows:
            session.execute(
                update(NewsStory)
                .where(NewsStory.id == row.id, NewsStory.topic.is_(None))
                .values(topic=topic_for(row.title, row.summary, row.kind))
            )
        session.flush()
        done += len(rows)
