"""Sheet → DB sync: upsert entities, touches from step cells, stage_log, data quality.

Why: docs/01 §08 — the sheet wins on identity, step and commercial columns; the DB holds the
derived fields. One run is idempotent: running it twice on the same sheet changes nothing.
Row key = `tab:SrNo:normalised name` (docs/10). A new Sr No with the same name and company is
the same person. Company names match exactly after trim and case fold, or through an approved
alias; Google ≠ DeepMind and Rosebud AI ≠ Rosebud wellness are never merged (C09).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from saley.core.clock import now_ist
from saley.core.logging import get_logger
from saley.core.models import (
    Account,
    Alias,
    Contact,
    DataQuality,
    Deliverable,
    Event,
    Goal,
    Opportunity,
    Package,
    SheetSnapshot,
    SheetTabStatus,
    StageLog,
    Strategy,
    Touch,
)
from saley.core.settings import get_settings
from saley.sales.columns import log_resolution
from saley.sales.goalmap import load_goal_map, short_name
from saley.sales.outbox import keep_open_cells, open_cells
from saley.sales.parse import (
    BAD_DATE,
    TEXT_DATE,
    clean_text,
    deal_value,
    is_truthy,
    norm_name,
    parse_date,
    parse_int,
    parse_money_band,
    parse_pct,
    parse_prob,
    tri_state,
)
from saley.sales.sheet import SheetData, TabData
from saley.sales.stage import StageInputs, derive_stage, is_li_connected, li_requested

log = get_logger("saley.sales.sync")

# Stable tab names for row keys: a renamed tab must not change every key.
TAB_NAMES = {
    "poc": "Outreach PoCs",
    "master": "Master Pipeline",
    "strategy": "Sales Strategy",
    "goals": "Goal Setting",
    "deliverables": "Deliverables Checklist",
    "packages": "Sales Packages",
    "events": "AI Events & Summits",
}
NO_COMPANY = "(no company)"
# Names that must never resolve to each other, whatever an alias row says (C09).
NEVER_MERGE: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset({"google"}), frozenset({"deepmind", "google deepmind"})),
    (frozenset({"rosebud ai"}), frozenset({"rosebud wellness"})),
)
# Columns without which a tab cannot be read safely. A missing one makes THAT tab stale:
# its last good data stays, other tabs go on (D41).
REQUIRED_ROLES = {
    # Stage rules read these; if one is renamed, stages would silently fall back.
    "poc": (
        "name",
        "company",
        "first_contact",
        "sid_li_added",
        "email_1_sent",
        "li_dm_sent",
        "meeting_date",
        "meeting_status",
        "prospect_status",
        "deal_status",
        "closure_prob",
        "reconnect_date",
    ),
    "master": ("company",),
    "goals": ("text",),
    "deliverables": ("item", "deadline", "status"),
    "strategy": ("name",),
    "packages": ("name",),
    "events": ("name",),
}
CORE_TABS = ("poc", "master", "goals", "deliverables")
# A tab that needs another: contacts need their accounts from Master Pipeline.
TAB_NEEDS = {"poc": ("master",)}
# A sync that would remove more than this share of live people is refused.
MAX_REMOVED_SHARE = 0.2
# Rows made by `saley/qa/seed.py` for the browser check carry this prefix. They are not in the
# sheet, so the sync must neither remove them nor merge a sheet row into one (R2 phase 7).
QA_ROW_PREFIX = "qa:"
# R5 T15 (D217): the seeded QA events. The sheet sync keeps them only while a QA seed is active:
# the seed's throwaway user `qa` exists (teardown deletes it) and is younger than QA_SEED_MAX_AGE
# (a gate run is far shorter). A seed left by a crashed run is stale: the next sync removes its
# events (self-heal). No other tab keeps a `qa:` row.
QA_EVENT_PREFIX = f"{QA_ROW_PREFIX}event:"
QA_SEED_USER = "qa"
QA_SEED_MAX_AGE = timedelta(hours=6)
MIN_ROWS_FOR_SHARE_CHECK = 10


class SheetStructureError(RuntimeError):
    """The sheet does not look like the GTM sheet any more. Nothing was written."""


@dataclass
class SyncStats:
    """What one run did. Becomes the job_runs note: '998 rows · 3 changed · 2 bad dates'."""

    rows: int = 0
    inserted: int = 0
    changed: int = 0
    removed: int = 0
    touches: int = 0
    stage_changes: int = 0
    problems: dict[str, int] = field(default_factory=dict)
    sync_id: int | None = None
    stale: list[str] = field(default_factory=list)

    def note(self) -> str:
        bad = self.problems.get(BAD_DATE, 0)
        partial = f"PARTIAL: {', '.join(self.stale)} stale · " if self.stale else ""
        return partial + (
            f"{self.rows} rows · {self.inserted} new · {self.changed} changed · "
            f"{self.removed} removed · {self.touches} new touches · "
            f"{self.stage_changes} stage moves · bad dates {bad}"
        )


@dataclass
class Problems:
    """Data quality findings collected during one run."""

    items: dict[tuple[str, str, str, str], str | None] = field(default_factory=dict)

    def add(self, tab: str, row_key: str, role: str, problem: str, value: object = None) -> None:
        self.items[(tab, row_key, role, problem)] = clean_text(value)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for _, _, _, problem in self.items:
            out[problem] = out.get(problem, 0) + 1
        return out


def leading_int(value: object) -> int | None:
    """The number a target starts with: '1 deal closed' → 1. '>50% closure…' → None."""
    match = re.match(r"\s*(\d{1,6})\b(?!\s*%)", clean_text(value) or "")
    return int(match.group(1)) if match else None


def row_key(tab_kind: str, sr_no: object, name: object) -> str:
    return f"{TAB_NAMES[tab_kind]}:{clean_text(sr_no) or ''}:{norm_name(name)}"


def _date(tab: TabData, row: Sequence[Any], role: str, key: str, problems: Problems) -> date | None:
    raw = tab.cell(row, role)
    parsed, problem = parse_date(raw)
    if problem:
        problems.add(TAB_NAMES.get(tab.kind, tab.kind), key, role, problem, raw)
    return parsed


def _text(tab: TabData, row: Sequence[Any], role: str) -> str | None:
    return clean_text(tab.cell(row, role))


def _apply(obj: Any, values: dict[str, Any]) -> bool:
    """Set attributes that differ. Returns True when something changed."""
    changed = False
    for name, value in values.items():
        if getattr(obj, name) != value:
            setattr(obj, name, value)
            changed = True
    return changed


def _keyed_upsert(
    session: Session,
    model: type[Any],
    rows: Iterable[tuple[str, dict[str, Any]]],
    now: datetime,
    stats: SyncStats,
    problems: Problems | None = None,
    tab_name: str = "",
    keep_prefix: str | None = None,
) -> dict[str, Any]:
    """Upsert by sheet_row_key for the small tabs. Unseen rows get removed_at.

    Duplicates are flagged, never silently merged. Losing more than MAX_REMOVED_SHARE of a
    tab in one run means the tab broke, not that the team deleted rows: refuse.
    `keep_prefix`: unseen rows with this key prefix stay (the active QA seed's events only).
    `qa:` rows never count in the share check: they are not sheet rows.
    """
    existing = {obj.sheet_row_key: obj for obj in session.scalars(select(model))}
    seen: dict[str, Any] = {}
    for key, values in rows:
        if key in seen:
            if problems is not None:
                problems.add(tab_name, key, "key", "duplicate_row")
            continue
        obj = existing.get(key)
        values = {**values, "removed_at": None}
        if obj is None:
            obj = model(sheet_row_key=key, **values)
            session.add(obj)
            stats.inserted += 1
        elif _apply(obj, values):
            obj.updated_at = now
            stats.changed += 1
        seen[key] = obj
    gone = [
        o
        for k, o in existing.items()
        if k not in seen
        and o.removed_at is None
        and not (keep_prefix and k.startswith(keep_prefix))
    ]
    real_gone = sum(1 for o in gone if not o.sheet_row_key.startswith(QA_ROW_PREFIX))
    real_live = sum(
        1 for k, o in existing.items() if o.removed_at is None and not k.startswith(QA_ROW_PREFIX)
    )
    _check_removals(tab_name or model.__tablename__, real_gone, real_live)
    for obj in gone:
        obj.removed_at = now
        stats.removed += 1
    session.flush()
    return seen


def _check_removals(what: str, gone: int, live_before: int) -> None:
    if live_before >= MIN_ROWS_FOR_SHARE_CHECK and gone > MAX_REMOVED_SHARE * live_before:
        raise SheetStructureError(
            f"{what}: {gone} of {live_before} rows would be removed. Nothing was written."
        )


def sync_strategy(
    session: Session, tab: TabData | None, now: datetime, stats: SyncStats
) -> dict[str, Strategy]:
    if tab is None:
        return {}
    rows = []
    for _, row in tab.rows:
        name = _text(tab, row, "name")
        if not name:
            continue
        rows.append(
            (
                row_key("strategy", tab.cell(row, "sr_no"), name),
                {
                    "sr_no": _text(tab, row, "sr_no"),
                    "name": name,
                    "description": _text(tab, row, "description"),
                    "icp": _text(tab, row, "icp"),
                    "icp_per_month": parse_int(tab.cell(row, "icp_per_month")),
                    "orgs_per_month": parse_int(tab.cell(row, "orgs_per_month")),
                    "collaterals": _text(tab, row, "collaterals"),
                    "ready": tri_state(tab.cell(row, "ready")),
                    "pending": _text(tab, row, "pending"),
                    "timeline": _text(tab, row, "timeline"),
                    "metric_text": _text(tab, row, "metric"),
                },
            )
        )
    stats.rows += len(rows)
    return _keyed_upsert(session, Strategy, rows, now, stats, None, TAB_NAMES["strategy"])


def sync_goals(
    session: Session, tab: TabData, now: datetime, stats: SyncStats, problems: Problems
) -> dict[str, Goal]:
    rows = []
    unique_rows, keys = [], set()
    for _, r in tab.rows:
        goal_text = _text(tab, r, "text")
        if not goal_text:
            continue
        k = row_key("goals", tab.cell(r, "sr_no"), goal_text)
        if k in keys:
            problems.add(TAB_NAMES["goals"], k, "text", "duplicate_row")
            continue
        keys.add(k)
        unique_rows.append(r)
    # G1…G5 follow the sheet order after duplicates are dropped (D30).
    for position, row in enumerate(unique_rows, start=1):
        text = _text(tab, row, "text") or ""
        key = row_key("goals", tab.cell(row, "sr_no"), text)
        value_text = _text(tab, row, "value")
        low, high = parse_money_band(value_text)
        rows.append(
            (
                key,
                {
                    "code": f"G{position}",
                    "position": position,
                    "text": text,
                    "value_text": value_text,
                    "value_min": low,
                    "value_max": high,
                    "metric": _text(tab, row, "metric"),
                    "target": _text(tab, row, "target"),
                    "need": leading_int(tab.cell(row, "target")),
                    "owner": _text(tab, row, "owner"),
                    "dependencies": _text(tab, row, "dependencies"),
                    "total_leads": _text(tab, row, "total_leads"),
                    "status": _text(tab, row, "status"),
                    "due": _date(tab, row, "due", key, problems),
                },
            )
        )
    stats.rows += len(rows)
    return _keyed_upsert(session, Goal, rows, now, stats, problems, TAB_NAMES["goals"])


def sync_packages(
    session: Session, tab: TabData | None, now: datetime, stats: SyncStats
) -> dict[str, Package]:
    if tab is None:
        return {}
    rows = []
    for _, row in tab.rows:
        label = _text(tab, row, "package") or _text(tab, row, "name")
        if not label:
            continue
        rows.append(
            (
                row_key("packages", "", label),
                {
                    "name": _text(tab, row, "name") or label,
                    "purpose": _text(tab, row, "purpose"),
                    "use_case": _text(tab, row, "use_case"),
                    "size": _text(tab, row, "size"),
                    "pct": parse_pct(tab.cell(row, "pct")),
                    "ready": tri_state(tab.cell(row, "ready")),
                    "status": _text(tab, row, "status"),
                },
            )
        )
    stats.rows += len(rows)
    return _keyed_upsert(session, Package, rows, now, stats, None, TAB_NAMES["packages"])


_RANGE = re.compile(r"(\d{1,2})\s*[-–]\s*(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s*(\d{4})?")
_MONTH_FIRST = re.compile(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})\s*[-–]\s*(\d{1,2}),?\s*(\d{4})?")
_CROSS_RANGE = re.compile(
    r"(\d{1,2})\s+([A-Za-z]{3,9})\.?\s*[-–]\s*(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s*(\d{4})?"
)


def parse_event_dates(raw: object, today: date) -> tuple[date | None, date | None, str | None]:
    """'20–21 Oct 2026' or '30 Oct - 2 Nov 2026' → two dates. A single date gives start = end."""
    single, problem = parse_date(raw)
    if single is not None:
        return single, single, problem
    text = clean_text(raw) or ""
    cross = _CROSS_RANGE.search(text)
    if cross:
        first, month1, last, month2, year = cross.groups()
    else:
        match = _RANGE.search(text)
        month_first = None if match else _MONTH_FIRST.search(text)
        if match:
            first, last, month2, year = match.groups()
        elif month_first:
            month2, first, last, year = month_first.groups()
        else:
            return None, None, (BAD_DATE if text else None)
        month1 = month2
    year = year or str(today.year)
    start, _ = parse_date(f"{first} {month1[:3]} {year}")
    end, _ = parse_date(f"{last} {month2[:3]} {year}")
    if start is None:
        return None, None, BAD_DATE
    return start, end or start, TEXT_DATE


def sync_events(
    session: Session, tab: TabData | None, now: datetime, stats: SyncStats, problems: Problems
) -> dict[str, Event]:
    if tab is None:
        return {}
    rows = []
    for _, row in tab.rows:
        name = _text(tab, row, "name")
        if not name:
            continue
        key = row_key("events", tab.cell(row, "sr_no"), name)
        start, end, problem = parse_event_dates(tab.cell(row, "date"), now.date())
        if problem:
            problems.add(TAB_NAMES["events"], key, "date", problem, tab.cell(row, "date"))
        rows.append(
            (
                key,
                {
                    "sr_no": _text(tab, row, "sr_no"),
                    "name": name,
                    "city": _text(tab, row, "location"),
                    "start": start,
                    "end": end,
                    "url": _text(tab, row, "link"),
                    "timings": _text(tab, row, "timings"),
                    "key_people": _text(tab, row, "key_people"),
                    "reg_deadline": _date(tab, row, "reg_deadline", key, problems),
                    "registered": tri_state(tab.cell(row, "registered")),
                    "attended": tri_state(tab.cell(row, "attended")),
                },
            )
        )
    stats.rows += len(rows)
    keep = QA_EVENT_PREFIX if qa_seed_active(session, now) else None
    return _keyed_upsert(
        session, Event, rows, now, stats, problems, TAB_NAMES["events"], keep_prefix=keep
    )


def qa_seed_active(session: Session, now: datetime) -> bool:
    """True while a QA seed runs: its user `qa` exists and is younger than QA_SEED_MAX_AGE."""
    found = session.execute(
        text("select 1 from users where short = :qa and created_at > :since"),
        {"qa": QA_SEED_USER, "since": now - QA_SEED_MAX_AGE},
    ).first()
    return found is not None


_STOP_TOKENS = frozenset({"the", "and", "all", "tbd", "none", "n/a", "na", "etc", "for", "with"})


def _tokens(text: str | None) -> list[str]:
    parts = re.split(r"[,;/&\n]|\band\b|\(|\)", norm_name(text))
    tokens = (p.strip(" .-") for p in parts)
    return [t for t in tokens if len(t) >= 3 and t not in _STOP_TOKENS]


def depends_on(item: str, text: str | None, owner: str | None = None) -> bool:
    """True when a goal's Dependencies text (or a motion's What's pending) names this item (D34).

    Either the whole item name appears as words in the text, or a listed part of the text
    starts a word of the item ('Overview doc' → 'Pulse Product Overview Document'), or a
    listed part is '<owner> compliances' ('Legal compliances' → items owned by Legal).
    'api' never matches inside 'rapid'.
    """
    item_key, text_key = norm_name(item), norm_name(text)
    if not item_key or not text_key:
        return False
    if re.search(rf"\b{re.escape(item_key)}\b", text_key):
        return True
    tokens = _tokens(text)
    if any(re.search(rf"\b{re.escape(token)}", item_key) for token in tokens):
        return True
    group = norm_name(owner)
    return bool(group) and any(
        len(t.split()) == 2 and t.split()[0] == group and t.split()[1].startswith("compliance")
        for t in tokens
    )


def sync_deliverables(
    session: Session,
    tab: TabData,
    goals: dict[str, Goal],
    strategies: dict[str, Strategy] | None,
    now: datetime,
    stats: SyncStats,
    problems: Problems,
    goal_map: dict[str, set[str]] | None = None,
) -> dict[str, Deliverable]:
    """Upsert deliverables. Motion links are kept as they were when the strategy tab is absent.

    Goals listed in docs/goal-deliverable-map.yaml use that list; the rest use the text rule (D34).
    """
    goal_map = goal_map or {}
    matched: set[tuple[str, str]] = set()
    rows = []
    for _, row in tab.rows:
        item = _text(tab, row, "item")
        if not item:
            continue
        key = row_key("deliverables", tab.cell(row, "sr_no"), item)
        owner = _text(tab, row, "dependency")
        if tab.cell(row, "deadline") in (None, "") and not is_done(_text(tab, row, "status")):
            problems.add(TAB_NAMES["deliverables"], key, "deadline", "no_deadline")
        rows.append(
            (
                key,
                {
                    "sr_no": _text(tab, row, "sr_no"),
                    "item": item,
                    "dependency": _text(tab, row, "dependency"),
                    "priority": _text(tab, row, "priority"),
                    "due": _date(tab, row, "deadline", key, problems),
                    "timelines": _text(tab, row, "timelines"),
                    "link": _text(tab, row, "link"),
                    "status": _text(tab, row, "status"),
                    "remarks": _text(tab, row, "remarks"),
                    "reminder_freq": _text(tab, row, "reminder"),
                    "done": _date(tab, row, "done", key, problems),
                    "blocks_goal_ids": sorted(
                        g.id for g in goals.values() if _blocks(g, item, owner, goal_map, matched)
                    ),
                },
            )
        )
        if strategies is not None:
            rows[-1][1]["blocks_strategy_ids"] = sorted(
                s.id for s in strategies.values() if depends_on(item, s.pending, owner)
            )
    stats.rows += len(rows)
    codes = {g.code for g in goals.values()}
    for code, names in goal_map.items():
        if code not in codes:
            problems.add("goal-deliverable-map", code, "goal", "map_unknown_goal")
            continue
        for name in sorted(names):
            if (code, name) not in matched:
                problems.add("goal-deliverable-map", code, name, "map_unknown_item")
    return _keyed_upsert(
        session, Deliverable, rows, now, stats, problems, TAB_NAMES["deliverables"]
    )


def _blocks(
    goal: Goal,
    item: str,
    owner: str | None,
    goal_map: dict[str, set[str]],
    matched: set[tuple[str, str]],
) -> bool:
    """Does this deliverable block this goal? The override file wins for the goals it lists.

    A map entry matches the full item name; an entry without " - " also matches the part of
    the item before " - " ("NDA" → "NDA - to be prepared…").
    """
    names = goal_map.get(goal.code)
    if names is None:
        return depends_on(item, goal.dependencies, owner)
    for candidate in (norm_name(item), short_name(item)):
        if candidate in names:
            matched.add((goal.code, candidate))
            return True
    return False


def _never_merge(a: str, b: str) -> bool:
    return any(
        (a in left and b in right) or (a in right and b in left) for left, right in NEVER_MERGE
    )


class AccountBook:
    """Resolves company names to accounts: approved alias first, then exact name key."""

    def __init__(
        self, session: Session, now: datetime, stats: SyncStats, problems: Problems
    ) -> None:
        self.session, self.now, self.stats, self.problems = session, now, stats, problems
        self.by_key = {a.name_key: a for a in session.scalars(select(Account))}
        self.aliases: dict[str, Account] = {}
        for alias in session.scalars(select(Alias)):
            target = session.get(Account, alias.account_id)
            if target is None:
                continue
            if _never_merge(norm_name(alias.alias), target.name_key):
                problems.add("alias", alias.alias, "alias", "alias_never_merge", target.name)
                continue
            self.aliases[norm_name(alias.alias)] = target
        self.seen: set[int] = set()
        self.master_seen: set[int] = set()

    def from_master(self, tab: TabData) -> None:
        for _, row in tab.rows:
            name = _text(tab, row, "company")
            if not name:
                continue
            key = norm_name(name)
            values = {
                "name": name,
                "in_master": True,
                "removed_at": None,
                "sheet_row_key": row_key("master", tab.cell(row, "sr_no"), name),
                "industry": _text(tab, row, "industry"),
                "geo": _text(tab, row, "geo"),
                "funding": _text(tab, row, "funding"),
                "outreach_line": _text(tab, row, "outreach_line"),
            }
            account = self.by_key.get(key)
            if account is not None and account.id in self.seen:
                self.problems.add(
                    TAB_NAMES["master"], values["sheet_row_key"], "company", "duplicate_row", name
                )
                continue
            self.master_seen.add(self._upsert(key, values).id)
            self.stats.rows += 1

    def _upsert(self, key: str, values: dict[str, Any]) -> Account:
        account = self.by_key.get(key)
        if account is None:
            account = Account(name_key=key, **values)
            self.session.add(account)
            self.session.flush()
            self.by_key[key] = account
            self.stats.inserted += 1
        elif _apply(account, values):
            account.updated_at = self.now
            self.stats.changed += 1
        self.seen.add(account.id)
        return account

    def for_company(self, name: str | None, industry: str | None) -> Account:
        """Account for a PoC company cell: alias, then exact key, else a new PoC-only account."""
        key = norm_name(name) or NO_COMPANY
        account = self.aliases.get(key) or self.by_key.get(key)
        if account is None:
            return self._upsert(
                key,
                {
                    "name": name or NO_COMPANY,
                    "in_master": False,
                    "industry": industry,
                    "sheet_row_key": f"{TAB_NAMES['poc']}:company:{key}",
                    "removed_at": None,
                },
            )
        if account.id not in self.seen:
            fill: dict[str, Any] = {"removed_at": None}
            if account.industry is None and industry:
                fill["industry"] = industry
            if _apply(account, fill):
                account.updated_at = self.now
        self.seen.add(account.id)
        return account

    def finish(self) -> None:
        # A QA company (`qa:` key) is not in the sheet; like QA contacts, the sync leaves it alone.
        real = [a for a in self.by_key.values() if not a.sheet_row_key.startswith(QA_ROW_PREFIX)]
        live = [a for a in real if a.removed_at is None]
        gone = [a for a in live if a.id not in self.seen]
        _check_removals("accounts", len(gone), len(live))
        for account in real:
            if account.in_master and account.id not in self.master_seen:
                account.in_master = False  # left Master Pipeline; kept while PoCs point to it
                account.updated_at = self.now
            if account.id not in self.seen and account.removed_at is None:
                account.removed_at = self.now
                self.stats.removed += 1


# ------------------------------------------------------------------ contacts

STEP_TYPES = (
    "first_contact",
    "li_req",
    "li_accept",
    "email1",
    "email2",
    "email3",
    "li_dm",
    "meeting",
)


def owner_for(
    first_contact: str | None, li_status: str | None, first_contact_type: str | None
) -> tuple[str, str]:
    """Owner per contact (D31, confirmed; supersedes the D25 default).

    First Contact text "VR" / "Sid" names the owner. Else any value in Sid - LI Addition →
    the LinkedIn owner (Sid). Else First Contact Type "LI" → Sid. Else the research owner
    (Vaishnavi). The two defaults come from OWNER_DEFAULT_LINKEDIN / OWNER_DEFAULT_RESEARCH.
    """
    value = (first_contact or "").casefold()
    if re.search(r"\bvr\b|vaishnavi", value):
        return "vaishnavi", "first_contact"
    if re.search(r"\bsid\b", value):
        return "sid", "first_contact"
    settings = get_settings()
    if li_requested(li_status):
        return settings.owner_default_linkedin, "li_addition"
    if re.search(r"\b(li|linkedin)\b", (first_contact_type or "").casefold()):
        return settings.owner_default_linkedin, "first_contact_type"
    return settings.owner_default_research, "default"


def contact_values(
    tab: TabData, row: Sequence[Any], key: str, problems: Problems
) -> dict[str, Any]:
    """All mirrored and derived fields of one Outreach PoCs row."""

    def d(role: str) -> date | None:
        return _date(tab, row, role, key, problems)

    def t(role: str) -> str | None:
        return _text(tab, row, role)

    email = t("email")
    if email and "@" not in email:
        problems.add(TAB_NAMES["poc"], key, "email", "bad_email", email)
    first_contact_raw = tab.cell(row, "first_contact")
    li_status = t("sid_li_added")
    email_dates = [d("email_1_date"), d("email_2_date"), d("email_3_date")]
    sent = [
        is_truthy(tab.cell(row, f"email_{i}_sent")) or email_dates[i - 1] is not None
        for i in (1, 2, 3)
    ]
    li_dm_date = d("li_dm_date")
    li_dm_sent = is_truthy(tab.cell(row, "li_dm_sent")) or li_dm_date is not None
    connected = is_li_connected(li_status)
    contacted = is_truthy(first_contact_raw)
    owner, owner_source = owner_for(
        clean_text(first_contact_raw), li_status, t("first_contact_type")
    )
    cells = {role: clean_text(tab.cell(row, role)) for role in tab.resolution.columns}
    return {
        "sr_no": t("sr_no"),
        "name": t("name") or "",
        "name_key": norm_name(t("name")),
        "title": t("designation"),
        "email": email,
        "email_status": "known" if email and "@" in email else None,
        "city": t("based"),
        "li_url": t("li_url"),
        "paper_url": t("paper_links"),
        "x_handle": t("x_handle"),
        "about": t("about_poc"),
        "industry": t("industry"),
        "application_bucket": t("application_bucket"),
        "priority": t("poc_priority"),
        "connected_with_vr": is_truthy(tab.cell(row, "connected_with_vr")),
        "owner": owner,
        "owner_source": owner_source,
        "first_contact": clean_text(first_contact_raw),
        "first_contact_type": t("first_contact_type"),
        "first_contact_date": d("first_contact_date"),
        "li_status": li_status,
        "li_connected_date": d("li_connected_date"),
        "next_step": t("outreach_step"),
        "email1_sent": sent[0],
        "email1_date": email_dates[0],
        "email2_sent": sent[1],
        "email2_date": email_dates[1],
        "email3_sent": sent[2],
        "email3_date": email_dates[2],
        "li_dm_sent": li_dm_sent,
        "li_dm_date": li_dm_date,
        "meeting_date": d("meeting_date"),
        "meeting_status": t("meeting_status"),
        "reconnect_date": d("reconnect_date"),
        "notes": t("next_steps"),
        "contacted": contacted or connected,
        "connected": connected,
        "emailed": any(sent) or li_dm_sent,
        "cells": cells,
    }


def step_touches(v: dict[str, Any]) -> dict[str, tuple[date | None, str | None]]:
    """Step type → (date, outcome) for every step this row shows as done."""
    li_type = (v["first_contact_type"] or "").casefold()
    li_date = v["first_contact_date"] if re.search(r"\b(li|linkedin)\b", li_type) else None
    out: dict[str, tuple[date | None, str | None]] = {}
    if v["contacted"]:
        out["first_contact"] = (v["first_contact_date"], v["first_contact_type"])
    if li_requested(v["li_status"]):
        out["li_req"] = (li_date, v["li_status"])
    if v["connected"]:
        out["li_accept"] = (v["li_connected_date"], None)
    for i in (1, 2, 3):
        if v[f"email{i}_sent"]:
            out[f"email{i}"] = (v[f"email{i}_date"], None)
    if v["li_dm_sent"]:
        out["li_dm"] = (v["li_dm_date"], None)
    if v["meeting_date"] or v["meeting_status"]:
        out["meeting"] = (v["meeting_date"], v["meeting_status"])
    return out


def _stage_inputs(v: dict[str, Any], tab: TabData, row: Sequence[Any]) -> StageInputs:
    return StageInputs(
        first_contact=v["contacted"],
        li_status=v["li_status"],
        email_sent=v["email1_sent"] or v["email2_sent"] or v["email3_sent"],
        li_dm_sent=v["li_dm_sent"],
        meeting_date=v["meeting_date"],
        meeting_status=v["meeting_status"],
        prospect_status=_text(tab, row, "prospect_status"),
        deal_status=_text(tab, row, "deal_status"),
        closure_prob=parse_prob(tab.cell(row, "closure_prob")),
        reconnect_date=v["reconnect_date"],
    )


def sync_contacts(
    session: Session,
    tab: TabData,
    book: AccountBook,
    packages: dict[str, Package],
    now: datetime,
    stats: SyncStats,
    problems: Problems,
) -> None:
    log_resolution(TAB_NAMES["poc"], tab.resolution)
    for role in tab.resolution.unmapped_roles:
        problems.add(TAB_NAMES["poc"], "header", role, "missing_column")
    existing = {
        c.sheet_row_key: c
        for c in session.scalars(select(Contact))
        if not c.sheet_row_key.startswith(QA_ROW_PREFIX)
    }
    by_person = {(c.name_key, c.account_id): c for c in existing.values()}
    touches = {
        (t.contact_id, t.type): t
        for t in session.scalars(select(Touch).where(Touch.source == "sheet"))
    }
    opportunities = {o.contact_id: o for o in session.scalars(select(Opportunity))}
    pending = open_cells(session)
    package_by_name = {norm_name(p.name): p for p in packages.values()} | {
        k.rsplit(":", 1)[-1]: p for k, p in packages.items()
    }
    all_keys = {row_key("poc", tab.cell(r, "sr_no"), _text(tab, r, "name")) for _, r in tab.rows}
    sheet_keys: set[str] = set()
    seen: set[int] = set()

    for row_number, row in tab.rows:
        name = _text(tab, row, "name")
        key = row_key("poc", tab.cell(row, "sr_no"), name)
        if not name:
            problems.add(TAB_NAMES["poc"], f"row {row_number}", "name", "missing_name")
            continue
        if key in sheet_keys:
            problems.add(TAB_NAMES["poc"], key, "name", "duplicate_row", name)
            continue
        sheet_keys.add(key)
        stats.rows += 1
        values = contact_values(tab, row, key, problems)
        account = book.for_company(_text(tab, row, "company"), values["industry"])
        if not _text(tab, row, "company"):
            problems.add(TAB_NAMES["poc"], key, "company", "missing_company")
        contact = existing.get(key)
        if contact is None:
            # Same name and company under a new Sr No is the same person (docs/10).
            candidate = by_person.get((values["name_key"], account.id))
            if (
                candidate is not None
                and candidate.id not in seen
                and candidate.sheet_row_key not in all_keys
            ):
                contact = candidate
        if contact is not None:
            # R2 (D45): a cell with an open outbox row keeps its stored value, so a sync that
            # read the sheet before the push cannot revert the pending write.
            roles = pending.get(key, set()) | pending.get(contact.sheet_row_key, set())
            if roles:
                keep_open_cells(values, contact, roles)
        stage, reason = derive_stage(_stage_inputs(values, tab, row))
        values |= {
            "account_id": account.id,
            "row_number": row_number,
            "stage": stage,
            "removed_at": None,
            "sheet_row_key": key,
        }
        if contact is None:
            contact = Contact(**values)
            session.add(contact)
            session.flush()
            stats.inserted += 1
            moved = True
        else:
            moved = contact.stage != stage
            if _apply(contact, values):
                contact.synced_at = now
                stats.changed += 1
        if moved:
            session.add(StageLog(contact_id=contact.id, stage=stage, from_at=now, reason=reason))
            stats.stage_changes += 1
        seen.add(contact.id)
        _sync_touches(session, contact, values, touches, stats, problems, key)
        _sync_opportunity(session, contact, tab, row, opportunities, package_by_name, now)

    gone = [c for c in existing.values() if c.id not in seen and c.removed_at is None]
    live_before = sum(1 for c in existing.values() if c.removed_at is None)
    if live_before >= MIN_ROWS_FOR_SHARE_CHECK and len(gone) > MAX_REMOVED_SHARE * live_before:
        raise SheetStructureError(
            f"{len(gone)} of {live_before} people would be removed. "
            "Check the Outreach PoCs tab; nothing was written."
        )
    for contact in gone:
        contact.removed_at = now
        stats.removed += 1


def _sync_touches(
    session: Session,
    contact: Contact,
    values: dict[str, Any],
    touches: dict[tuple[int, str], Touch],
    stats: SyncStats,
    problems: Problems,
    key: str,
) -> None:
    """A step cell that becomes filled is a new touch. A changed date updates the touch."""
    current = step_touches(values)
    for kind, (when, outcome) in current.items():
        existing = touches.get((contact.id, kind))
        if existing is None:
            touch = Touch(
                contact_id=contact.id, type=kind, date=when, outcome=outcome, source="sheet"
            )
            session.add(touch)
            touches[(contact.id, kind)] = touch
            stats.touches += 1
        elif existing.date != when or existing.outcome != outcome:
            existing.date, existing.outcome = when, outcome
    for kind in STEP_TYPES:
        if kind not in current and (contact.id, kind) in touches:
            problems.add(TAB_NAMES["poc"], key, kind, "step_cleared")


def _sync_opportunity(
    session: Session,
    contact: Contact,
    tab: TabData,
    row: Sequence[Any],
    opportunities: dict[int, Opportunity],
    package_by_name: dict[str, Package],
    now: datetime,
) -> None:
    package_text = _text(tab, row, "package")
    package = package_by_name.get(norm_name(package_text)) if package_text else None
    values = {
        "package_text": package_text,
        "package_id": package.id if package is not None else None,
        "status": _text(tab, row, "prospect_status"),
        "prob": parse_prob(tab.cell(row, "closure_prob")),
        "size_usd": deal_value(tab.cell(row, "deal_size")),  # D42: band → midpoint
        "size_text": _text(tab, row, "deal_size"),
        "deal_status": _text(tab, row, "deal_status"),
        "notes": _text(tab, row, "next_steps"),
    }
    commercial = any(
        values[k] is not None for k in ("package_text", "status", "prob", "size_usd", "deal_status")
    )
    opportunity = opportunities.get(contact.id)
    if opportunity is None and commercial:
        opportunity = Opportunity(contact_id=contact.id, **values)
        session.add(opportunity)
        opportunities[contact.id] = opportunity
    elif opportunity is not None and _apply(opportunity, values):
        opportunity.updated_at = now


# ------------------------------------------------------------------ data quality


def write_problems(
    session: Session, problems: Problems, now: datetime, not_read: Iterable[str] = ()
) -> None:
    """Upsert this run's findings; findings not seen this run are resolved.

    A stale tab was not read this run, so its open findings stay open: "not seen" there
    means "not looked at", not "fixed" (python review).
    """
    for (tab, key, role, problem), value in problems.items.items():
        stmt = insert(DataQuality).values(
            tab=tab,
            row_key=key,
            column_role=role,
            problem=problem,
            value=value,
            first_seen=now,
            last_seen=now,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_data_quality_cell",
            set_={"value": stmt.excluded.value, "last_seen": now, "resolved_at": None},
        )
        session.execute(stmt)
    session.execute(
        update(DataQuality)
        .where(
            DataQuality.last_seen < now,
            DataQuality.resolved_at.is_(None),
            DataQuality.tab.not_in(list(not_read)),
            # Lines of other jobs (unreadable meeting notes) are theirs to close.
            DataQuality.tab.not_like("notes:%"),
        )
        .values(resolved_at=now)
    )


@dataclass(frozen=True)
class TabCheck:
    """The verdict for one tab before any write."""

    kind: str
    title: str | None
    ok: bool
    missing: tuple[str, ...]
    reason: str | None


def check_structure(data: SheetData) -> dict[str, TabCheck]:
    """Per tab: can it be synced? A missing tab or required header makes it stale (D41).

    Raises SheetStructureError only when every core tab is stale: then the sheet id is wrong,
    not a header.
    """
    checks: dict[str, TabCheck] = {}
    for kind, roles in REQUIRED_ROLES.items():
        tab = data.get(kind)
        if tab is None:
            checks[kind] = TabCheck(kind, None, False, (), "tab not found")
            continue
        missing = tuple(role for role in roles if role not in tab.resolution.columns)
        reason = f"no column for {', '.join(missing)}" if missing else None
        checks[kind] = TabCheck(kind, tab.title, not missing, missing, reason)
    for kind, needs in TAB_NEEDS.items():
        broken = [n for n in needs if not checks[n].ok]
        if checks[kind].ok and broken:
            reason = f"waits for {', '.join(TAB_NAMES[n] for n in broken)}"
            checks[kind] = TabCheck(kind, checks[kind].title, False, (), reason)
    if not any(checks[k].ok for k in CORE_TABS):
        raise SheetStructureError("no core tab can be read: check GTM_SHEET_ID and the tab names")
    return checks


def run_sync(session: Session, data: SheetData, now: datetime | None = None) -> SyncStats:
    """One sync inside the caller's transaction. Stale tabs keep their last good data (D41)."""
    checks = check_structure(data)
    goal_map = load_goal_map()  # a broken override file stops the sync before any write
    now = now or now_ist()
    stats, problems = SyncStats(), Problems()

    def ok(kind: str) -> bool:
        return checks[kind].ok

    strategies = (
        sync_strategy(session, data.get("strategy"), now, stats) if ok("strategy") else None
    )
    goals = (
        sync_goals(session, data["goals"], now, stats, problems)
        if ok("goals")
        else {
            g.sheet_row_key: g
            for g in session.scalars(select(Goal).where(Goal.removed_at.is_(None)))
        }
    )
    packages = (
        sync_packages(session, data.get("packages"), now, stats)
        if ok("packages")
        else {p.sheet_row_key: p for p in session.scalars(select(Package))}
    )
    if ok("events"):
        sync_events(session, data.get("events"), now, stats, problems)
    if ok("master") and ok("poc"):
        book = AccountBook(session, now, stats, problems)
        book.from_master(data["master"])
        sync_contacts(session, data["poc"], book, packages, now, stats, problems)
        book.finish()
    if ok("deliverables"):
        sync_deliverables(
            session, data["deliverables"], goals, strategies, now, stats, problems, goal_map
        )
    stale_kinds = [k for k, c in checks.items() if not c.ok]
    write_problems(session, problems, now, [TAB_NAMES.get(k, k) for k in stale_kinds])
    stats.sync_id = record_snapshot(session, data, checks, now)
    stats.stale = sorted(k for k, c in checks.items() if not c.ok)
    stats.problems = problems.counts()
    log.info(
        "sheet_sync_done",
        **{k: v for k, v in stats.__dict__.items() if k != "problems"},
        problems=stats.problems,
    )
    return stats


def record_snapshot(
    session: Session, data: SheetData, checks: dict[str, TabCheck], now: datetime
) -> int:
    """New sync_id only when the sheet (or a tab's status) changed; tab status upserted (D40)."""
    parts = sorted(
        (kind, data[kind].raw_hash if kind in data else "", check.ok)
        for kind, check in checks.items()
    )
    digest = hashlib.sha256(json.dumps(parts).encode()).hexdigest()
    last = session.scalars(select(SheetSnapshot).order_by(SheetSnapshot.id.desc()).limit(1)).first()
    if last is None or last.raw_hash != digest:
        last = SheetSnapshot(
            raw_hash=digest,
            at=now,
            tabs_ok=sum(c.ok for c in checks.values()),
            tabs_stale=sum(not c.ok for c in checks.values()),
        )
        session.add(last)
        session.flush()
    existing = {t.tab: t for t in session.scalars(select(SheetTabStatus))}
    for kind, check in checks.items():
        row = existing.get(kind) or SheetTabStatus(tab=kind)
        tab = data.get(kind)
        row.title = check.title
        row.status = "ok" if check.ok else "stale"
        row.missing_headers = list(check.missing)
        row.new_headers = tab.new_headers if tab is not None else []
        row.reason = check.reason
        row.checked_at = now
        if check.ok:
            row.last_good_sync_id = last.id
            row.stale_since = None
        elif row.stale_since is None:
            row.stale_since = now
        session.add(row)
        if tab is not None and tab.new_headers:
            log.info("columns_new_headers", tab=TAB_NAMES.get(kind, kind), headers=tab.new_headers)
    session.flush()
    return last.id


_DONE = re.compile(r"^(completed?|done|closed)\b")  # same rule as saley_is_done() in SQL


def is_done(status: str | None) -> bool:
    return bool(_DONE.match((status or "").strip().casefold()))
