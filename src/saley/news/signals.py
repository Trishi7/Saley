"""Link scored stories to our companies and people (D74, docs/03 §3.3).

Why: a story is a signal only when it names something in the sheet. The match is plain code, the
LLM never picks a person. Each match has kinds of evidence:
- a company: its name (or an alias a human approved), its mail domain, a person of that company;
- a person: the full name, the company name, the mail domain; for a paper, the author line
  "Name (Institute)".
Two kinds of evidence give confidence 3. One kind gives confidence 1 and the `verify` flag, so a
human checks it. A short or common account name (under 5 characters, or a word like "Meta")
matches only in the exact case written in the sheet, or through an alias. Only scored stories
are linked, so the funnel always goes down (D73). Running it twice changes nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from saley.core.logging import get_logger
from saley.core.models import Account, Alias, Contact, NewsStory, Signal

log = get_logger("saley.news.signals")

CONFIDENCE_TWO = 3
CONFIDENCE_ONE = 1
SHORT_NAME = 5  # D74: an account name under 5 characters needs an alias or an exact case
LOOKBACK = timedelta(days=7)
MAX_STORIES = 500
FREE_MAIL = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "icloud.com",
        "proton.me",
        "protonmail.com",
        "aol.com",
    }
)
# Words that are also a company name. They match in the exact case of the sheet, or by alias.
COMMON_WORDS = frozenset(
    {
        "meta",
        "scale",
        "apple",
        "amazon",
        "orange",
        "square",
        "stripe",
        "slack",
        "notion",
        "spark",
        "mark",
        "base",
        "open",
        "next",
        "point",
        "signal",
        "lens",
        "rise",
        "bolt",
        "grid",
    }
)
AUTHORS_LINE = re.compile(r"^Authors:\s*(.+)$", re.MULTILINE)
AUTHOR_ITEM = re.compile(r"\s*([^;(]+?)\s*(?:\(([^)]*)\))?\s*(?:;|$)")


@dataclass(frozen=True)
class AccountRef:
    id: int
    name: str
    aliases: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContactRef:
    id: int
    name: str
    account_id: int
    company: str
    domain: str | None = None


@dataclass
class EntityIndex:
    """The names a story can match, read once per run."""

    accounts: list[AccountRef] = field(default_factory=list)
    contacts: list[ContactRef] = field(default_factory=list)

    @classmethod
    def load(cls, session: Session) -> EntityIndex:
        aliases: dict[int, list[str]] = {}
        for alias in session.scalars(select(Alias)):
            aliases.setdefault(alias.account_id, []).append(alias.alias)
        domains: dict[int, set[str]] = {}
        contacts = []
        accounts_by_id = {a.id: a for a in session.scalars(select(Account))}
        for row in session.scalars(select(Contact).where(Contact.removed_at.is_(None))):
            account = accounts_by_id.get(row.account_id)
            if account is None or account.removed_at is not None:
                continue
            domain = email_domain(row.email)
            if domain:
                domains.setdefault(account.id, set()).add(domain)
            contacts.append(ContactRef(row.id, row.name, account.id, account.name, domain))
        accounts = [
            AccountRef(
                a.id,
                a.name,
                tuple(aliases.get(a.id, ())),
                tuple(sorted(domains.get(a.id, ()))),
            )
            for a in accounts_by_id.values()
            if a.removed_at is None
        ]
        return cls(accounts, contacts)


def email_domain(email: str | None) -> str | None:
    """The mail domain of a company address. A free-mail domain says nothing about a company."""
    if not email or "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip().lower()
    return None if not domain or domain in FREE_MAIL else domain


def _word_pattern(name: str, *, exact_case: bool) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", 0 if exact_case else re.IGNORECASE)


def is_strict_name(name: str) -> bool:
    """D74: short or common names need the exact case (or an alias)."""
    clean = name.strip()
    return len(clean) < SHORT_NAME or (" " not in clean and clean.lower() in COMMON_WORDS)


def find_account_name(text: str, account: AccountRef) -> tuple[str | None, str]:
    """('name_exact' | 'alias' | None, the text with the matched words blanked).

    A strict name matches only in its exact case. Blanking the words lets a longer name hide a
    shorter one: "Google DeepMind" in a story does not also name the account "Google".
    """
    name = account.name.strip()
    if len(name) >= 2:
        pattern = _word_pattern(name, exact_case=is_strict_name(name))
        if pattern.search(text):
            return "name_exact", pattern.sub(lambda m: " " * len(m.group(0)), text)
    for alias in account.aliases:
        if alias.strip():
            pattern = _word_pattern(alias.strip(), exact_case=False)
            if pattern.search(text):
                return "alias", pattern.sub(lambda m: " " * len(m.group(0)), text)
    return None, text


def matches_account_name(text: str, account: AccountRef) -> str | None:
    return find_account_name(text, account)[0]


def has_domain(text: str, domain: str) -> bool:
    return bool(re.search(rf"(?<![\w.-]){re.escape(domain)}(?![\w-])", text, re.IGNORECASE))


@dataclass(frozen=True)
class Match:
    entity_type: str
    entity_id: int
    match_kind: str
    evidence: int  # kinds of evidence found

    @property
    def confidence(self) -> int:
        return CONFIDENCE_TWO if self.evidence >= 2 else CONFIDENCE_ONE

    @property
    def verify(self) -> bool:
        return self.evidence < 2


def parse_authors(summary: str) -> list[tuple[str, str]]:
    """(author, affiliation) pairs from the 'Authors:' line the OpenAlex fetch writes."""
    found = AUTHORS_LINE.search(summary or "")
    if not found:
        return []
    pairs = []
    for name, inst in AUTHOR_ITEM.findall(found.group(1) + ";"):
        if name.strip():
            pairs.append((name.strip(), (inst or "").strip()))
    return pairs


def _person_match(
    text: str, url: str, story: NewsStory, person: ContactRef, account: AccountRef | None
) -> Match | None:
    name = person.name.strip()
    if len(name.split()) < 2:  # a single word is not a person
        return None
    if story.kind == "paper":
        for author, inst in parse_authors(story.summary):
            if author.casefold() != name.casefold():
                continue
            hit = bool(inst) and account is not None and _affiliation(inst, account)
            return Match("contact", person.id, "author", 2 if hit else 1)
        return None
    if not _word_pattern(name, exact_case=False).search(text):
        return None
    company = account is not None and matches_account_name(text, account) is not None
    domain = bool(person.domain) and (
        has_domain(text, person.domain or "") or has_domain(url, person.domain or "")
    )
    evidence = 1 + int(company or domain)
    return Match("contact", person.id, "name_exact", evidence)


def _affiliation(inst: str, account: AccountRef) -> bool:
    low = inst.casefold()
    names = [account.name, *account.aliases]
    return any(n.strip() and n.strip().casefold() in low for n in names)


def _account_match(
    text: str, url: str, account: AccountRef, person_named: bool, kind: str | None
) -> Match | None:
    """A company needs its name (or alias) or its domain. A named person is the second proof."""
    domain = any(has_domain(text, d) or has_domain(url, d) for d in account.domains)
    if kind is None and not domain:
        return None
    evidence = int(kind is not None) + int(domain) + int(person_named)
    return Match("account", account.id, kind or "domain", evidence)


def match_story(story: NewsStory, index: EntityIndex) -> list[Match]:
    """Every entity this story names, with the evidence found. Pure: no database."""
    text = " . ".join((story.title, story.summary))
    accounts = {a.id: a for a in index.accounts}
    matches: list[Match] = []
    named_accounts: set[int] = set()
    for person in index.contacts:
        found = _person_match(text, story.url, story, person, accounts.get(person.account_id))
        if found is not None:
            matches.append(found)
            named_accounts.add(person.account_id)
    masked = text
    for account in sorted(index.accounts, key=lambda a: (-len(a.name), a.id)):
        kind, masked = find_account_name(masked, account)
        found = _account_match(text, story.url, account, account.id in named_accounts, kind)
        if found is not None:
            matches.append(found)
    return matches


@dataclass
class LinkStats:
    stories: int = 0
    linked: int = 0
    signals: int = 0
    verify: int = 0

    def note(self) -> str:
        return (
            f"stories {self.stories} · linked {self.linked} · "
            f"signals {self.signals} · to verify {self.verify}"
        )


def link_scored(
    session: Session,
    *,
    now: datetime,
    index: EntityIndex | None = None,
    stories: Iterable[NewsStory] | None = None,
) -> LinkStats:
    """Link the scored stories of the last week. Safe to run again (D74, unique per entity)."""
    index = index or EntityIndex.load(session)
    if stories is None:
        stories = session.scalars(
            select(NewsStory)
            .where(NewsStory.scored_at.is_not(None), NewsStory.first_seen_at >= now - LOOKBACK)
            # Newest first: with more than MAX_STORIES in the week the oldest are the ones left
            # out, never the story that was just scored (A7).
            .order_by(NewsStory.first_seen_at.desc(), NewsStory.id.desc())
            .limit(MAX_STORIES)
        )
    stats = LinkStats()
    for story in stories:
        if story.scored_at is None:  # D73: linking only on scored stories
            continue
        stats.stories += 1
        matches = match_story(story, index)
        added = 0
        for match in matches:
            created = session.execute(
                insert(Signal.__table__)
                .values(
                    story_id=story.id,
                    entity_type=match.entity_type,
                    entity_id=match.entity_id,
                    match_kind=match.match_kind,
                    tag=story.tag,
                    importance=story.importance,
                    impact=story.impact or 0,
                    confidence=match.confidence,
                    verify=match.verify,
                    created_at=now,
                )
                .on_conflict_do_nothing(constraint="uq_signal_story_entity")
                .returning(Signal.__table__.c.id)
            ).first()
            if created is not None:
                added += 1
                stats.verify += int(match.verify)
        stats.signals += added
        stats.linked += int(added > 0)
    session.flush()
    return stats
