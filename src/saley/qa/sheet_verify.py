"""`saley sheet verify`: gate step 6, skill dashboard-parity.

Why: the dashboard must never show a number the sheet does not support. This module recounts
the raw sheet values with its own small rules, written again on purpose: it imports nothing
from `saley.sales` or the SQL views, so a bug there cannot hide here. It then reads the same
numbers from the API in process (the app the browser uses) and compares with tolerance 0.
Read-only: the caller passes raw values; the CLI reads them with the read-only scope.

R3 adds the news funnel. `NewsTables` counts it straight from the news tables with its own SQL
(no view, no `saley.web` code), then the API numbers for the same day must equal those counts.
It also checks that the funnel only goes down (fetched, stored, scored, linked).
"""

from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol

EPOCH = date(1899, 12, 30)  # Google Sheets serial day 0
STAGES = (
    "Listed",
    "Contacted",
    "Connected",
    "Engaged",
    "Meeting",
    "Qualified",
    "Parked",
    "Won",
    "Lost",
)
EMAIL_STEPS = ("1st", "2nd", "3rd")
DONE_RE = re.compile(r"(completed?|done|closed)\b")
SPOT_SEED = 18
SPOT_COUNT = 5
HEADER_SCAN = 6
PAGE = 1000
# Tabs and the header that proves the tab was read. A missing one fails the gate: an empty
# sheet side must never match an empty API side as "0 = 0, OK".
REQUIRED = (
    ("Outreach PoCs", "Name"),
    ("Deliverables Checklist", "Action Item"),
    ("Sales Packages", "Ready?"),
)
SERIAL_MAX = 2_958_465  # 9999-12-31 as a sheet serial day

Get = Callable[[str], dict[str, Any]]


@dataclass
class Report:
    rows: list[tuple[str, Any, Any, bool]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def check(self, metric: str, sheet: Any, api: Any) -> None:
        self.rows.append((metric, sheet, api, sheet == api))

    @property
    def ok(self) -> bool:
        # Zero checks is not a pass: an empty comparison must never turn the gate green.
        return bool(self.rows) and all(r[3] for r in self.rows)

    def markdown(self, title: str) -> str:
        lines = [f"# {title}", "", "| Metric | Sheet | API | Result |", "| --- | --- | --- | --- |"]
        lines += [f"| {m} | {s} | {a} | {'OK' if ok else 'DIFF'} |" for m, s, a, ok in self.rows]
        lines += [
            "",
            *[f"- {n}" for n in self.notes],
            "",
            f"**{'PASS' if self.ok else 'FAIL'}**",
            "",
        ]
        return "\n".join(lines)


# ------------------------------------------------------------------ cell helpers


def text(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


def truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    t = text(v).lower()
    return t not in ("", "false", "no", "0", "not sent", "-") and not t.startswith(("not ", "#"))


def as_date(v: Any) -> date | None:
    if isinstance(v, int | float) and not isinstance(v, bool):
        # A phone number or an id in a date cell is not a date; NaN and inf are not either.
        if not 0 <= v <= SERIAL_MAX:
            return None
        return EPOCH + timedelta(days=int(v))
    t = text(v)
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def word(value: Any, *words: str) -> bool:
    t = text(value).lower()
    return any(
        re.search(rf"(?<!un)\b{w}\b", t) and not re.search(rf"\bnot\s+(a\s+)?{w}", t) for w in words
    )


def prob(v: Any) -> int | None:
    """Closure Prob as a percent: '>50%' 50, '<50%' 49, 0.6 60, '' None."""
    if isinstance(v, int | float) and not isinstance(v, bool):
        return round(v * 100) if 0 <= v <= 1 else round(v)
    t = text(v)
    m = re.search(r"\d+(\.\d+)?", t)
    if not m:
        return None
    n = round(float(m.group(0)))
    return n - 1 if t.startswith("<") and not t.startswith("<=") and n > 1 else n


def table(rows: list[list[Any]], header_hint: str) -> list[dict[str, Any]]:
    """Rows as dicts keyed by header text. The header is the first row that has the hint."""
    hi = next(
        (i for i, r in enumerate(rows[:HEADER_SCAN]) if header_hint in [text(c) for c in r]), None
    )
    if hi is None:
        return []
    hdr = [text(c) for c in rows[hi]]
    return [
        {h: (r[i] if i < len(r) else "") for i, h in enumerate(hdr) if h} for r in rows[hi + 1 :]
    ]


def tab(raw: dict[str, list[list[Any]]], *names: str) -> list[list[Any]]:
    """A tab by exact title, or by the start of its title (the sheet adds dates to some)."""
    for name in names:
        for title, rows in raw.items():
            if title == name or title.startswith(name):
                return rows
    return []


# ------------------------------------------------------------------ sheet side


def emailed(r: dict[str, Any]) -> bool:
    return (
        any(
            truthy(r.get(f"{n} Email Sent")) or as_date(r.get(f"{n} Email Date"))
            for n in EMAIL_STEPS
        )
        or truthy(r.get("LI DM Sent"))
        or bool(as_date(r.get("LI DM Date")))
    )


def connected(r: dict[str, Any]) -> bool:
    return text(r.get("Sid - LI Addition")).lower().startswith("connected")


def stage(r: dict[str, Any]) -> str:
    """Stage rules from docs/06 §6.1 (D29, D31), written again here on purpose."""
    deal, status, p = (
        text(r.get("Deal Status")),
        text(r.get("Status")),
        prob(r.get("Closure Prob%")),
    )
    if word(deal, "won", "signed", "closed won"):
        return "Won"
    if word(deal, "lost", "dead", "closed lost") or p == 0:
        return "Lost"
    if word(status, "lead", "demo", "quote", "deal") or (p is not None and p >= 50):
        return "Qualified"
    if as_date(r.get("Meeting Date")) or text(r.get("Meeting Status")):
        return "Meeting"
    if as_date(r.get("Reconnect Date")):
        return "Parked"
    if emailed(r):
        return "Engaged"
    if connected(r):
        return "Connected"
    if truthy(r.get("First Contact")):
        return "Contacted"
    return "Listed"


def people(raw: dict[str, list[list[Any]]]) -> list[dict[str, Any]]:
    """One row per person: duplicates (same Sr No and name) count once."""
    out, seen = [], set()
    for r in table(tab(raw, "Outreach PoCs"), "Name"):
        name = text(r.get("Name"))
        key = (text(r.get("Sr No")), name.lower())
        if name and key not in seen:
            seen.add(key)
            out.append(r)
    return out


def recount(raw: dict[str, list[list[Any]]], today: date) -> tuple[Counter, dict[str, Counter]]:
    sheet: Counter = Counter({f"stage:{st}": 0 for st in STAGES})
    by_company: dict[str, Counter] = defaultdict(Counter)
    for r in people(raw):
        conn, mailed, st = connected(r), emailed(r), stage(r)
        contacted = truthy(r.get("First Contact")) or conn
        sheet["people_total"] += 1
        sheet["contacted"] += contacted
        sheet["connected"] += conn
        sheet["emailed"] += mailed
        sheet["meetings"] += bool(as_date(r.get("Meeting Date")) or text(r.get("Meeting Status")))
        sheet["p1_total"] += text(r.get("Priority")).upper().startswith("P1")
        sheet[f"stage:{st}"] += 1
        sheet["in_conversation"] += st in ("Engaged", "Meeting", "Qualified")
        sheet["backlog"] += st == "Connected"
        company = by_company[text(r.get("Company/Uni")).lower()]
        company.update(people=1, contacted=int(contacted), connected=int(conn), emailed=int(mailed))
    sheet["late_deliverables"] = sum(1 for _ in late_items(raw, today))
    sheet["packages_not_ready"] = sum(
        1
        for r in table(tab(raw, "Sales Packages"), "Ready?")
        if text(r.get("Package") or r.get("Name")) and text(r.get("Ready?")).lower() == "no"
    )
    return sheet, by_company


def late_items(raw: dict[str, list[list[Any]]], today: date) -> Iterable[dict[str, Any]]:
    for r in table(tab(raw, "Deliverables Checklist"), "Action Item"):
        due = as_date(r.get("Tentative Deadline"))
        done = DONE_RE.match(text(r.get("Status")).lower())
        if text(r.get("Action Item")) and due and due < today and not done:
            yield r


# ------------------------------------------------------------------ news funnel (R3)

FUNNEL_KEYS = (
    "tried",
    "answered",
    "fetched",
    "dropped_seen",
    "dropped_copy",
    "dropped_gate",
    "deduped",
    "scored",
    "scored_4",
    "linked",
)
IST_DAY = "(%s at time zone 'Asia/Kolkata')::date"
RUN_DAY = IST_DAY % "started_at"
STORY_DAY = IST_DAY % "first_seen_at"
# The independent side counts a RANGE on the column (it can use the plain index), where the
# API view groups by the IST date. Both must give the same number for every day (B6).
DAY_START = "(cast(:d as date)::timestamp at time zone 'Asia/Kolkata')"
DAY_END = "((cast(:d as date) + 1)::timestamp at time zone 'Asia/Kolkata')"


def _in_day(column: str) -> str:
    return f"{column} >= {DAY_START} and {column} < {DAY_END}"


RUNS_IN_DAY = _in_day("started_at")
STORIES_IN_DAY = _in_day("first_seen_at")
FUNNEL_SQL = {
    "tried": f"select count(*) from news_source_runs where {RUNS_IN_DAY}",
    "answered": (
        f"select count(*) from news_source_runs where {RUNS_IN_DAY} and status in ('ok','304')"
    ),
    "fetched": f"select coalesce(sum(items_seen), 0) from news_source_runs where {RUNS_IN_DAY}",
    "dropped_seen": (
        f"select coalesce(sum(dropped_seen), 0) from news_source_runs where {RUNS_IN_DAY}"
    ),
    "dropped_copy": (
        f"select coalesce(sum(dropped_copy), 0) from news_source_runs where {RUNS_IN_DAY}"
    ),
    "dropped_gate": (
        f"select coalesce(sum(dropped_gate), 0) from news_source_runs where {RUNS_IN_DAY}"
    ),
    "deduped": f"select count(*) from news_stories where {STORIES_IN_DAY}",
    "scored": f"select count(*) from news_stories where {STORIES_IN_DAY} and scored_at is not null",
    "scored_4": f"select count(*) from news_stories where {STORIES_IN_DAY} and importance >= 4",
    "linked": (
        "select count(distinct s.id) from news_stories s join signal g on g.story_id = s.id "
        f"where {_in_day('s.first_seen_at')} and s.scored_at is not null"
    ),
}
LATEST_DAY_SQL = (
    f"select max(d) from (select {RUN_DAY} as d from news_source_runs "
    f"union select {STORY_DAY} from news_stories) x"
)


class NewsCounter(Protocol):
    def counts(self, day: date) -> dict[str, int]: ...

    def latest_day(self) -> date | None: ...


class NewsTables:
    """Counts the funnel from the news tables, with its own SQL (the independent side)."""

    def counts(self, day: date) -> dict[str, int]:
        from sqlalchemy import text as sql  # late: the pure helpers above need no database

        from saley.core.db import rw_engine

        with rw_engine().connect() as conn:
            return {
                key: int(conn.execute(sql(query), {"d": day}).scalar_one() or 0)
                for key, query in FUNNEL_SQL.items()
            }

    def latest_day(self) -> date | None:
        from sqlalchemy import text as sql

        from saley.core.db import rw_engine

        with rw_engine().connect() as conn:
            return conn.execute(sql(LATEST_DAY_SQL)).scalar()


def check_news(report: Report, get: Get, counter: NewsCounter, today: date) -> None:
    """The API funnel equals the table counts for today and the latest day with news."""
    days = [today]
    latest = counter.latest_day()
    if latest is not None and latest != today:
        days.append(latest)
    for day in days:
        from_tables = counter.counts(day)
        api = {s["key"]: s["n"] for s in get(f"/api/news/funnel?day={day.isoformat()}")["steps"]}
        for key in FUNNEL_KEYS:
            report.check(f"news {key} {day:%d %b}", from_tables[key], api.get(key))
        t = from_tables
        down = t["fetched"] >= t["deduped"] >= t["scored"] >= t["linked"] >= 0
        report.check(f"news funnel goes down {day:%d %b}", True, down)
    health = {s["key"]: s["n"] for s in get("/api/health/full")["news"]["steps"]}
    shown = {s["key"]: s["n"] for s in get(f"/api/news/funnel?day={today.isoformat()}")["steps"]}
    report.check("health funnel = funnel endpoint", shown, health)


# ------------------------------------------------------------------ API side


def get_all(get: Get, path: str, key: str = "items", total: str = "total") -> list[dict]:
    """Every row of a list endpoint, page by page: a cut at the page size is not a DIFF."""
    sep = "&" if "?" in path else "?"
    out: list[dict] = []
    while True:
        page = get(f"{path}{sep}limit={PAGE}&offset={len(out)}")
        out += page[key]
        if not page[key] or len(out) >= page[total]:
            return out


def api_numbers(get: Get) -> Counter:
    ppl = get("/api/people?filter=all&limit=1")
    score = get_all(get, "/api/scorecard")
    acc = get_all(get, "/api/accounts?filter=all")
    blk = get("/api/blockers?limit=1")
    dash: Counter = Counter()
    dash["people_total"] = ppl["counts"]["people"]
    dash["in_conversation"] = ppl["counts"]["in_conversation"]
    dash["backlog"] = ppl["counts"]["backlog"]
    dash["contacted"] = sum(r["contacted"] for r in score)
    dash["meetings"] = sum(r["meetings"] for r in score)
    dash["connected"] = sum(a["connected"] for a in acc)
    dash["emailed"] = sum(a["emailed"] for a in acc)
    dash["p1_total"] = ppl["counts"]["p1"]
    for st in STAGES:
        dash[f"stage:{st}"] = get(f"/api/people?filter=stage:{st}&limit=1")["total"]
    dash["late_deliverables"] = blk["late_total"]
    dash["packages_not_ready"] = blk["packages_total"]
    return dash


def check_tiles(report: Report, get: Get) -> None:
    """Every tile number equals the length of the list it opens."""
    tiles = {t["key"]: t for t in get("/api/today")["tiles"]}
    for key in ("live", "backlog", "p1", "week"):
        t = tiles[key]
        n = get(f"/api/people?filter={t['link']['filter']}&limit=1")["total"]
        report.check(f"tile {key} = its list", t["value"], n)
    late = get_all(get, "/api/blockers", "late", "late_total")
    report.check(
        "tile late = its list", tiles["late"]["value"], sum(1 for b in late if b["blocks_goals"])
    )
    won = get("/api/people?filter=stage:Won&limit=1")["total"]
    deals = re.match(r"(\d+)", str(tiles["deals"]["value"] or ""))
    report.check("tile deals closed = Won", won, int(deals.group(1)) if deals else None)


# R5 T5: the Today funnel. A person counts in a bar when at this stage or past it. The sheet
# side recounts it from the stage counts, with the rank written again here on purpose.
FUNNEL_FROM = {
    "contacted": ("Contacted", "Connected", "Engaged", "Meeting", "Qualified", "Won"),
    "connected": ("Connected", "Engaged", "Meeting", "Qualified", "Won"),
    "talking": ("Engaged", "Meeting", "Qualified", "Won"),
    "met": ("Meeting", "Qualified", "Won"),
    "won": ("Won",),
}


def funnel_recount(sheet: Counter) -> dict[str, int]:
    out = {"listed": sheet["people_total"]}
    for key, stages in FUNNEL_FROM.items():
        out[key] = sum(sheet[f"stage:{st}"] for st in stages)
    return out


def check_funnel(report: Report, get: Get, sheet: Counter) -> None:
    """The Today funnel equals the stage recount of the sheet, bar by bar."""
    api = {step["key"]: step["n"] for step in get("/api/today").get("funnel", [])}
    for key, n in funnel_recount(sheet).items():
        report.check(f"funnel {key}", n, api.get(key))


DEAL_SIZE = "Estd. Deal Size (USD)"
_NUM = r"(\d[\d,]*(?:\.\d+)?)\s*([kmb])?(?![a-z])"
_CUR = r"(?:usd|inr|eur|gbp|[$₹€£])?\s*"
_BAND = re.compile(_NUM + r"\s*(?:-|–|—|to)\s*" + _CUR + _NUM, re.I)
_ONE = re.compile(_NUM, re.I)
_SCALE = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def deal_mid(v: Any) -> int | None:
    """Own reading of a deal-size cell (D42): a band counts its midpoint; one number as given.

    A band is "number, dash or 'to', number". Other numbers in the text (a year) are ignored.
    """
    if isinstance(v, int | float) and not isinstance(v, bool):
        return int(v)
    band = _BAND.search(text(v))
    if band:
        low_n, low_s, high_n, high_s = band.groups()
        suffix = (high_s or low_s or "").lower()
        low = float(low_n.replace(",", "")) * _SCALE[(low_s or suffix).lower()]
        high = float(high_n.replace(",", "")) * _SCALE[suffix]
        return int((int(low) + int(high)) // 2) if low <= high else None
    one = _ONE.search(text(v))
    if not one:
        return None
    return int(float(one.group(1).replace(",", "")) * _SCALE[(one.group(2) or "").lower()])


def check_money(report: Report, get: Get, rows: list[dict[str, Any]] | None = None) -> None:
    """The money total equals the sum of the list; each value is the sheet midpoint (D42)."""
    money = get("/api/money-blocked?limit=1")
    items = get_all(get, "/api/money-blocked")
    if rows is not None:
        # A person on two rows may carry two sizes: the API value must match one of them.
        sizes: dict[tuple[str, str], set[int | None]] = {}
        for r in rows:
            key = (text(r.get("Name")).lower(), text(r.get("Company/Uni")).lower())
            sizes.setdefault(key, set()).add(deal_mid(r.get(DEAL_SIZE)))
        bad = [
            m["name"]
            for m in items
            if m["size_usd"]
            not in sizes.get((m["name"].lower(), (m["company"] or "").lower()), {None})
        ]
        report.check("money values = sheet midpoints", len(items), len(items) - len(bad))
        report.notes += [f"money value differs: {n}" for n in bad[:20]]
    known = [m["size_usd"] for m in items if m["size_usd"] is not None]
    report.check("money total = sum of list", sum(known), money["total_usd"])
    report.check("money deals = list length", money["total"], len(items))
    report.check(
        "money no value", sum(1 for m in items if m["size_usd"] is None), money["no_value"]
    )


def check_companies(report: Report, get: Get, by_company: dict[str, Counter]) -> None:
    acc = {a["name"].lower(): a for a in get_all(get, "/api/accounts?filter=all")}
    bad = []
    for name, c in by_company.items():
        a = acc.get(name or "(no company)")
        got = (a["people"], a["contacted"], a["connected"], a["emailed"]) if a else None
        if got != (c["people"], c["contacted"], c["connected"], c["emailed"]):
            bad.append(name)
    report.check("companies with matching counts", len(by_company), len(by_company) - len(bad))
    report.notes += [f"company differs: {n}" for n in bad[:20]]


def check_spot(report: Report, get: Get, rows: list[dict[str, Any]]) -> None:
    rng = random.Random(SPOT_SEED)  # noqa: S311 - a fixed sample, not security
    for r in rng.sample(rows, min(SPOT_COUNT, len(rows))):
        name, company = text(r.get("Name")), text(r.get("Company/Uni"))
        found = [
            p
            for p in get("/api/people?filter=all&limit=50&q=" + _quote(name[:100]))["items"]
            if p["name"] == name and (p["company"] or "").lower() == company.lower()
        ]
        want = (company, text(r.get("Designation")), stage(r))
        got = (
            (found[0]["company"] or "", found[0]["title"] or "", found[0]["stage"])
            if found
            else None
        )
        report.check(f"person {name}", " · ".join(want), " · ".join(got) if got else "missing")


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def check_read(report: Report, raw: dict[str, list[list[Any]]]) -> bool:
    """Every required tab is there with its header, and the sheet has people."""
    ok = True
    for name, header in REQUIRED:
        found = bool(table(tab(raw, name), header))
        report.check(f"tab {name} read", "rows", "rows" if found else "missing")
        ok = ok and found
    has_people = bool(people(raw))
    report.check("people in the sheet", "more than 0", "more than 0" if has_people else "none")
    return ok and has_people


def verify(
    raw: dict[str, list[list[Any]]],
    today: date,
    get: Get,
    news: NewsCounter | None = None,
) -> Report:
    report = Report()
    if not check_read(report, raw):
        report.notes.append("The sheet copy is not readable as expected. Nothing else compared.")
        return report
    sheet, by_company = recount(raw, today)
    dash = api_numbers(get)
    for key in sorted(set(sheet) | set(dash)):
        # A metric missing on one side is a DIFF, never a silent 0.
        report.check(key, sheet.get(key), dash.get(key))
    check_tiles(report, get)
    check_funnel(report, get, sheet)
    check_money(report, get, people(raw))
    check_companies(report, get, by_company)
    check_spot(report, get, people(raw))
    check_news(report, get, news or NewsTables(), today)
    report.notes.append("Sheet side: own rules in saley.qa.sheet_verify. API side: in process.")
    return report
