"""Read the 8 GTM tabs from the sheet COPY in one batch call (docs/01 §08).

Why: one `values:batchGet` per sync keeps us far under Google's quota. UNFORMATTED values with
SERIAL_NUMBER dates let the parser tell real dates from text dates. Tabs and headers are found
by alias, so a renamed tab or a moved column does not break the sync.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import gspread

from saley.core.egress import GuardedHTTPClient
from saley.core.logging import get_logger
from saley.core.settings import get_settings
from saley.sales.columns import POC_ROLES, Block, Resolution, Role, norm_header, resolve

log = get_logger("saley.sales.sheet")

HEADER_SCAN_ROWS = 6
REQUIRED_TABS = ("poc", "master", "goals", "deliverables")
READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"


def _roles(*specs: tuple[str, tuple[str, ...]]) -> tuple[Role, ...]:
    return tuple(Role(name, Block.READ_ONLY, aliases) for name, aliases in specs)


@dataclass(frozen=True)
class TabSpec:
    kind: str
    title_aliases: tuple[str, ...]
    roles: tuple[Role, ...]
    key_role: str
    # Headers that exist in the sheet but feed no role (docs/10). They are not "new columns".
    known_unused: tuple[str, ...] = ()


TABS: tuple[TabSpec, ...] = (
    TabSpec("poc", ("outreach pocs", "outreach poc"), POC_ROLES, "name"),
    TabSpec(
        "master",
        ("master pipeline",),
        _roles(
            ("sr_no", ("sr no", "sr. no.", "sr no.", "s no")),
            ("company", ("company", "company name", "company/uni")),
            ("industry", ("industry",)),
            ("geo", ("geography", "geo", "country", "location")),
            ("funding", ("approx. funding", "approx funding", "funding")),
            ("outreach_line", ("outreach line - researchers", "outreach line")),
        ),
        "company",
        ("dates",),
    ),
    TabSpec(
        "strategy",
        ("sept2026_sales strategy", "sales strategy"),
        _roles(
            ("sr_no", ("sr no.", "sr no", "sr. no.", "s no")),
            ("name", ("strategy name", "strategy")),
            ("description", ("description",)),
            ("icp", ("icp type", "icp")),
            (
                "icp_per_month",
                ("target icps/month", "target icps / month", "target icps per month"),
            ),
            (
                "orgs_per_month",
                ("target orgs/month", "target orgs / month", "target orgs per month"),
            ),
            ("collaterals", ("collaterals",)),
            ("ready", ("are all ready?", "are all ready", "ready?")),
            ("pending", ("what's pending?", "what's pending", "whats pending?", "what’s pending?")),
            ("timeline", ("timeline",)),
            ("metric", ("success metrics", "success metric")),
        ),
        "name",
    ),
    TabSpec(
        "goals",
        ("q4-ond2026-goal setting", "goal setting"),
        _roles(
            ("sr_no", ("row no.", "row no", "sr no", "sr. no.", "s no")),
            ("text", ("goal setting", "goal", "goals")),
            ("value", ("estd. value", "estd value", "estimated value", "value")),
            ("metric", ("success metric", "success metrics")),
            ("target", ("q4 target", "target")),
            ("owner", ("accountable", "owner")),
            ("dependencies", ("dependencies", "dependency")),
            ("total_leads", ("total leads",)),
            ("status", ("status",)),
            ("due", ("due date", "due")),
        ),
        "text",
    ),
    TabSpec(
        "deliverables",
        ("deliverables checklist", "deliverables"),
        _roles(
            ("sr_no", ("sr no", "sr. no.", "sr no.", "s no")),
            ("item", ("action item", "item", "deliverable")),
            ("dependency", ("functional dependency", "dependency", "owner")),
            ("priority", ("priority",)),
            ("deadline", ("tentative deadline", "deadline", "due date")),
            ("timelines", ("timelines", "timeline")),
            ("link", ("link",)),
            ("status", ("status",)),
            ("remarks", ("remarks",)),
            ("reminder", ("reminder freq", "reminder frequency")),
            ("done", ("done date", "done")),
        ),
        "item",
    ),
    TabSpec(
        "packages",
        ("sales packages", "packages"),
        _roles(
            ("package", ("package",)),
            ("name", ("name", "package name")),
            ("purpose", ("purpose",)),
            ("use_case", ("use case",)),
            ("size", ("size",)),
            ("pct", ("% completion", "% complete", "completion %", "completion")),
            ("ready", ("ready?", "ready")),
            ("status", ("status",)),
        ),
        "package",
        (
            "audio files",
            "image files",
            "jsonl output",
            "pulse_product doc",
            "audio",
            "image",
            "jsonl",
            "pulse doc",
        ),
    ),
    TabSpec(
        "events",
        ("ai events & summits", "ai events", "events"),
        _roles(
            ("sr_no", ("sr no", "sr. no.", "sr no.", "s no")),
            ("location", ("location", "city")),
            ("name", ("event name", "event")),
            ("link", ("link",)),
            ("date", ("date", "dates")),
            ("timings", ("timings", "timing")),
            ("key_people", ("key people",)),
            ("reg_deadline", ("last day for registration", "registration deadline")),
            ("registered", ("registered?", "registered")),
            ("attended", ("attended?", "attended")),
        ),
        "name",
    ),
    TabSpec("funnel", ("sales funnel - march-june 2026", "sales funnel"), (), ""),
)
TAB_BY_KIND = {spec.kind: spec for spec in TABS}


@dataclass
class TabData:
    """One tab: its title, header resolution and data rows (1-based sheet row numbers)."""

    kind: str
    title: str
    resolution: Resolution
    header_row: int
    rows: list[tuple[int, list[Any]]] = field(default_factory=list)
    raw_hash: str = ""  # hash of every cell as read: same hash = same snapshot (D40)

    @property
    def new_headers(self) -> list[str]:
        """Headers no role uses and docs/10 does not list: shown as "new columns" (D41)."""
        known = set(TAB_BY_KIND[self.kind].known_unused) if self.kind in TAB_BY_KIND else set()
        return [h for h in self.resolution.unknown_headers if norm_header(h) not in known]

    def cell(self, row: Sequence[Any], role: str) -> Any:
        index = self.resolution.columns.get(role)
        if index is None or index >= len(row):
            return None
        return row[index]


SheetData = dict[str, TabData]


class SheetNotCopy(RuntimeError):
    """GTM_SHEET_IS_COPY is false. R1 reads only the copy (D18, docs/11)."""


class TabMissing(RuntimeError):
    """A required tab was not found by any alias."""


def match_tab(title: str) -> TabSpec | None:
    """First spec whose alias equals the normalised title, then prefix matches."""
    norm = norm_header(title)
    for spec in TABS:
        if norm in spec.title_aliases:
            return spec
    for spec in TABS:
        if any(norm.startswith(alias) for alias in spec.title_aliases):
            return spec
    return None


def find_header(rows: Sequence[Sequence[Any]], roles: Sequence[Role]) -> tuple[int, Resolution]:
    """The header is the row (of the first few) where the most roles resolve."""
    best_index, best = 0, resolve(rows[0] if rows else [], roles)
    for i, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        res = resolve(row, roles)
        if len(res.columns) > len(best.columns):
            best_index, best = i, res
    return best_index, best


def build_tabs(raw: dict[str, list[list[Any]]]) -> SheetData:
    """Turn {tab title: rows} into TabData per kind. Pure, so tests feed fixture rows here."""
    data: SheetData = {}

    # Exact title matches first, then prefix matches: a tab named "Outreach PoCs copy"
    # placed before the real one must not become the source (code review).
    def exact_first(title: str) -> int:
        spec = match_tab(title)
        return 0 if spec and norm_header(title) in spec.title_aliases else 1

    for title in sorted(raw, key=exact_first):
        rows = raw[title]
        spec = match_tab(title)
        if spec is None:
            continue
        if spec.kind in data:
            log.warning("tab_ignored", title=title, kind=spec.kind, used=data[spec.kind].title)
            continue
        header_index, resolution = find_header(rows, spec.roles)
        body = [
            (header_index + 2 + offset, list(row))
            for offset, row in enumerate(rows[header_index + 1 :])
            if any(c not in (None, "", False) and str(c).strip() for c in row)
        ]
        digest = hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()
        data[spec.kind] = TabData(spec.kind, title, resolution, header_index + 1, body, digest)
    return data


def _client() -> gspread.Client:
    settings = get_settings()
    return gspread.service_account(
        filename=settings.google_service_account_file,
        scopes=[READONLY_SCOPE],  # R1 never writes, so the token cannot write either (D23)
        http_client=GuardedHTTPClient,
    )


def fetch_raw(client_factory: Callable[[], gspread.Client] = _client) -> dict[str, list[list[Any]]]:
    """Read every tab of the copy. Refuses unless GTM_SHEET_IS_COPY=true."""
    settings = get_settings()
    if not settings.gtm_sheet_is_copy:
        raise SheetNotCopy("GTM_SHEET_IS_COPY is false. R1 reads only the sheet copy.")
    settings.require("sync")
    spreadsheet = client_factory().open_by_key(settings.gtm_sheet_id)
    titles = [ws.title for ws in spreadsheet.worksheets()]
    wanted = [t for t in titles if match_tab(t) is not None]
    quoted = ["'" + t.replace("'", "''") + "'" for t in wanted]
    response = spreadsheet.values_batch_get(
        quoted,
        params={"valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "SERIAL_NUMBER"},
    )
    ranges = response.get("valueRanges", [])
    return {title: vr.get("values", []) for title, vr in zip(wanted, ranges, strict=True)}


def read_sheet(client_factory: Callable[[], gspread.Client] = _client) -> SheetData:
    """Every tab found by alias. A missing tab is not an error here: the sync marks it stale."""
    data = build_tabs(fetch_raw(client_factory))
    missing = [kind for kind in REQUIRED_TABS if kind not in data]
    if missing:
        log.warning("tabs_missing", kinds=missing)
    return data
