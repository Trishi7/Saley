"""Outreach PoCs column map: role → header aliases → lock block (docs/10).

Why: CLAUDE.md locks columns BY ROLE, never by letter. Humans move columns; the resolver
finds each role by its header aliases, case- and space-insensitive. If a restricted role
maps to no column, every write to the tab stops until it maps again (docs/10 rules).
R1 has no writes; `assert_writable()` is the guard R2's sheet writer must call first.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from saley.core.logging import get_logger

log = get_logger("saley.sales.columns")


class Block(StrEnum):
    IDENTITY = "identity"
    ACTIVATION = "activation"
    STEP = "step"
    COMMERCIAL = "commercial"
    READ_ONLY = "read_only"


@dataclass(frozen=True)
class Role:
    name: str
    block: Block
    aliases: tuple[str, ...]
    writable_existing: bool = False


# Order follows today's layout (A…AK). Letters are not used anywhere in code.
POC_ROLES: tuple[Role, ...] = (
    Role("sr_no", Block.IDENTITY, ("sr no", "sr. no.", "sr no.", "s no", "serial no")),
    Role("company", Block.IDENTITY, ("company/uni", "company / uni", "company", "organisation")),
    Role("name", Block.IDENTITY, ("name", "poc name", "full name")),
    Role("about_poc", Block.IDENTITY, ("about poc", "about")),
    Role("industry", Block.IDENTITY, ("industry",)),
    Role("designation", Block.IDENTITY, ("designation", "title", "role")),
    Role("email", Block.IDENTITY, ("email id", "email", "email address")),
    Role("based", Block.IDENTITY, ("based (sept 2026)", "based", "location", "city")),
    Role("paper_links", Block.IDENTITY, ("research paper link", "research paper links", "paper")),
    Role("li_url", Block.IDENTITY, ("li url", "linkedin url", "linkedin")),
    Role("first_contact", Block.ACTIVATION, ("first contact",), writable_existing=True),
    Role("first_contact_type", Block.ACTIVATION, ("first contact type",), writable_existing=True),
    Role("first_contact_date", Block.ACTIVATION, ("first contact date",), writable_existing=True),
    Role(
        "sid_li_added",
        Block.ACTIVATION,
        ("sid - li addition", "sid li addition", "li addition"),
        writable_existing=True,
    ),
    Role("li_connected_date", Block.ACTIVATION, ("li connected date",), writable_existing=True),
    Role("outreach_step", Block.STEP, ("next steps (dropdown)", "next steps", "next step")),
    Role("connected_with_vr", Block.READ_ONLY, ("connected with vr on li", "connected with vr")),
    Role("application_bucket", Block.IDENTITY, ("application bucket",)),
    Role("email_1_sent", Block.STEP, ("1st email sent", "email 1 sent")),
    Role("email_1_date", Block.STEP, ("1st email date", "email 1 date")),
    Role("email_2_sent", Block.STEP, ("2nd email sent", "email 2 sent")),
    Role("email_2_date", Block.STEP, ("2nd email date", "email 2 date")),
    Role("email_3_sent", Block.STEP, ("3rd email sent", "email 3 sent")),
    Role("email_3_date", Block.STEP, ("3rd email date", "email 3 date")),
    Role("li_dm_sent", Block.STEP, ("li dm sent", "linkedin dm sent")),
    Role("li_dm_date", Block.STEP, ("li dm date", "linkedin dm date")),
    Role("meeting_date", Block.ACTIVATION, ("meeting date",), writable_existing=True),
    Role("meeting_status", Block.ACTIVATION, ("meeting status",), writable_existing=True),
    Role("reconnect_date", Block.READ_ONLY, ("reconnect date",)),
    Role("next_steps", Block.COMMERCIAL, ("notes/remarks", "notes / remarks", "notes", "remarks")),
    Role("package", Block.COMMERCIAL, ("package",)),
    Role("prospect_status", Block.COMMERCIAL, ("status", "prospect status")),
    Role(
        "closure_prob", Block.COMMERCIAL, ("closure prob%", "closure prob", "closure probability")
    ),
    Role(
        "deal_size",
        Block.COMMERCIAL,
        ("estd. deal size (usd)", "estd deal size (usd)", "deal size (usd)", "deal size"),
    ),
    Role("deal_status", Block.COMMERCIAL, ("deal status",)),
    Role("poc_priority", Block.COMMERCIAL, ("priority",)),
    Role("x_handle", Block.IDENTITY, ("x handle (or website)", "x handle", "twitter")),
)

ROLE_BY_NAME = {role.name: role for role in POC_ROLES}
# Locked roles. A writer must locate every one of them before ANY write, or it could write into one.
RESTRICTED_ROLES = frozenset(role.name for role in POC_ROLES if not role.writable_existing)
WRITABLE_EXISTING = frozenset(role.name for role in POC_ROLES if role.writable_existing)


def norm_header(text: object) -> str:
    """'  Sid - LI Addition ' → 'sid - li addition'. Only spacing and case are ignored."""
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def col_letter(index: int) -> str:
    """0 → A, 26 → AA. For startup logs only."""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


@dataclass(frozen=True)
class Resolution:
    """Role → column index for one header row, plus what did not map."""

    columns: dict[str, int]
    unmapped_roles: tuple[str, ...]
    unknown_headers: tuple[str, ...]

    @property
    def writes_allowed(self) -> bool:
        return not (RESTRICTED_ROLES & set(self.unmapped_roles))

    def letters(self) -> dict[str, str]:
        return {role: col_letter(i) for role, i in self.columns.items()}


def resolve(headers: Sequence[object], roles: Sequence[Role] = POC_ROLES) -> Resolution:
    """Map roles to header positions. A header serves one role only.

    Pass 1: exact alias. Pass 2, for roles still open: the header starts with an alias and
    the next character is not a letter or digit ('Q4 Target (by 31-Dec-2026)' → 'q4 target',
    but 'Dates' never matches 'date').
    """
    normalised = [norm_header(h) for h in headers]
    taken: set[int] = set()
    columns: dict[str, int] = {}
    for role in roles:
        for alias in role.aliases:
            hits = [i for i, h in enumerate(normalised) if h == alias and i not in taken]
            if hits:
                columns[role.name] = hits[0]
                taken.add(hits[0])
                break
    for role in roles:
        if role.name in columns:
            continue
        for alias in role.aliases:
            hits = [
                i
                for i, h in enumerate(normalised)
                if i not in taken
                and h.startswith(alias)
                and not h[len(alias) : len(alias) + 1].isalnum()
            ]
            if hits:
                columns[role.name] = hits[0]
                taken.add(hits[0])
                break
    unmapped = tuple(role.name for role in roles if role.name not in columns)
    unknown = tuple(str(headers[i]) for i, h in enumerate(normalised) if h and i not in taken)
    return Resolution(columns, unmapped, unknown)


def log_resolution(tab: str, resolution: Resolution) -> None:
    """Startup log: where each role sits today, and what is missing."""
    log.info("columns_resolved", tab=tab, letters=resolution.letters())
    if resolution.unmapped_roles:
        log.warning(
            "columns_unmapped",
            tab=tab,
            roles=list(resolution.unmapped_roles),
            writes_allowed=resolution.writes_allowed,
        )
    if resolution.unknown_headers:
        log.info("columns_unknown_headers", tab=tab, headers=list(resolution.unknown_headers))


class WritesRefused(RuntimeError):
    """A write was asked for, but the column map is not safe or the role is locked."""


def assert_writable(resolution: Resolution, role: str, *, existing_row: bool = True) -> int:
    """Return the column index for a write, or refuse. Locks are by role (CLAUDE.md)."""
    if not resolution.writes_allowed:
        raise WritesRefused(
            "restricted roles do not map to a column: "
            + ", ".join(sorted(RESTRICTED_ROLES & set(resolution.unmapped_roles)))
        )
    if existing_row and role not in WRITABLE_EXISTING:
        raise WritesRefused(f"role {role!r} is read-only on an existing row")
    if role not in resolution.columns:
        raise WritesRefused(f"role {role!r} has no column")
    return resolution.columns[role]
