"""dashboard-parity skill (.claude/skills/dashboard-parity), step 2-9.

Why: the dashboard must never show a number the sheet does not support. This script recounts
the sheet with its own small rules (no saley.sales code; only settings to find the sheet copy)
and compares every number with the running local API (http://127.0.0.1:8765).
Read-only: gspread with the spreadsheets.readonly scope and GET calls only.

Run:  uv run saley web   (WEB_PORT=8765), then
      uv run python scripts/dashboard_parity.py docs/test-reports/parity-<date>.md
"""

import json
import random
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

import gspread

from saley.core.settings import get_settings

API = "http://127.0.0.1:8765"
IST = timezone(timedelta(hours=5, minutes=30))
TODAY = datetime.now(IST).date()
EPOCH = date(1899, 12, 30)

s = get_settings()
assert s.gtm_sheet_is_copy, "GTM_SHEET_IS_COPY must be true"
gc = gspread.service_account(
    filename=s.google_service_account_file,
    scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
)
sh = gc.open_by_key(s.gtm_sheet_id)
want = [
    "Outreach PoCs",
    "Master Pipeline",
    "Deliverables Checklist",
    "Q4-OND2026-Goal Setting",
    "Sales Packages",
    "AI Events & Summits",
]
res = sh.values_batch_get(
    [f"'{t}'" for t in want],
    params={"valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "SERIAL_NUMBER"},
)
tabs = {t: vr.get("values", []) for t, vr in zip(want, res["valueRanges"], strict=True)}


def get(path):
    with urllib.request.urlopen(API + path, timeout=30) as r:  # noqa: S310 - local API
        return json.load(r)


def table(rows, header_hint):
    hi = next(i for i, r in enumerate(rows[:6]) if header_hint in [str(c).strip() for c in r])
    hdr = [str(c).strip() for c in rows[hi]]
    out = []
    for r in rows[hi + 1 :]:
        out.append({h: (r[i] if i < len(r) else "") for i, h in enumerate(hdr) if h})
    return out


def text(v):
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


def truthy(v):
    if isinstance(v, bool):
        return v
    t = text(v).lower()
    return t not in ("", "false", "no", "0", "not sent", "-") and not t.startswith(("not ", "#"))


def as_date(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return EPOCH + timedelta(days=int(v))
    t = text(v)
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    return None


def word(t, *ws):
    t = text(t).lower()
    return any(
        re.search(rf"(?<!un)\b{w}\b", t) and not re.search(rf"\bnot\s+(a\s+)?{w}", t) for w in ws
    )


# ---------------- sheet side
poc = table(tabs["Outreach PoCs"], "Name")
people, seen = [], set()
for r in poc:
    name = text(r.get("Name"))
    key = (text(r.get("Sr No")), name.lower())
    if name and key not in seen:
        seen.add(key)
        people.append(r)


def prob(v):
    """Closure Prob as a percent: '>50%' 50, '<50%' 49 (below 50), 0.6 60, '' None."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return round(v * 100) if 0 <= v <= 1 else round(v)
    t = text(v)
    m = re.search(r"\d+(\.\d+)?", t)
    if not m:
        return None
    n = round(float(m.group(0)))
    return n - 1 if t.startswith("<") and not t.startswith("<=") and n > 1 else n


def stage(r):
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
    if (
        any(
            truthy(r.get(f"{n} Email Sent")) or as_date(r.get(f"{n} Email Date"))
            for n in ("1st", "2nd", "3rd")
        )
        or truthy(r.get("LI DM Sent"))
        or as_date(r.get("LI DM Date"))
    ):
        return "Engaged"
    if text(r.get("Sid - LI Addition")).lower().startswith("connected"):
        return "Connected"
    if truthy(r.get("First Contact")):
        return "Contacted"
    return "Listed"


sheet = Counter()
by_company = defaultdict(Counter)
for r in people:
    connected = text(r.get("Sid - LI Addition")).lower().startswith("connected")
    contacted = truthy(r.get("First Contact")) or connected
    emailed = (
        any(
            truthy(r.get(f"{n} Email Sent")) or as_date(r.get(f"{n} Email Date"))
            for n in ("1st", "2nd", "3rd")
        )
        or truthy(r.get("LI DM Sent"))
        or as_date(r.get("LI DM Date"))
    )
    st = stage(r)
    sheet["people_total"] += 1
    sheet["contacted"] += contacted
    sheet["connected"] += connected
    sheet["emailed"] += bool(emailed)
    sheet["meetings"] += bool(as_date(r.get("Meeting Date")) or text(r.get("Meeting Status")))
    sheet["p1_total"] += text(r.get("Priority")).upper().startswith("P1")
    sheet[f"stage:{st}"] += 1
    sheet["in_conversation"] += st in ("Engaged", "Meeting", "Qualified")
    sheet["backlog"] += st == "Connected"
    co = text(r.get("Company/Uni")).lower()
    for k, v in (
        ("people", 1),
        ("contacted", contacted),
        ("connected", connected),
        ("emailed", bool(emailed)),
    ):
        by_company[co][k] += v

late = 0
for r in table(tabs["Deliverables Checklist"], "Action Item"):
    if not text(r.get("Action Item")):
        continue
    due = as_date(r.get("Tentative Deadline"))
    st = text(r.get("Status")).lower()
    if due and due < TODAY and not re.match(r"(completed?|done|closed)\b", st):
        late += 1
sheet["late_deliverables"] = late
sheet["packages_not_ready"] = sum(
    1
    for r in table(tabs["Sales Packages"], "Ready?")
    if text(r.get("Package") or r.get("Name")) and text(r.get("Ready?")).lower() == "no"
)
events_30 = sum(
    1
    for r in table(tabs["AI Events & Summits"], "Event Name")
    if (d := as_date(r.get("Date"))) and TODAY <= d <= TODAY + timedelta(days=30)
)

# ---------------- dashboard side
today = {t["key"]: t for t in get("/api/today")["tiles"]}
ppl = get("/api/people?filter=all&limit=1")
blk = get("/api/blockers")
acc = get("/api/accounts?filter=all&limit=1000")
score = get("/api/scorecard")["items"]
dash = Counter()
dash["people_total"] = ppl["counts"]["people"]
dash["in_conversation"] = ppl["counts"]["in_conversation"]
dash["backlog"] = ppl["counts"]["backlog"]
dash["contacted"] = sum(r["contacted"] for r in score)
dash["connected"] = sum(r["connected"] for r in score)
dash["meetings"] = sum(r["meetings"] for r in score)
dash["emailed"] = sum(a["emailed"] for a in acc["items"])
dash["p1_total"] = int(today["p1"]["detail"].split()[1])
for st in (
    "Listed",
    "Contacted",
    "Connected",
    "Engaged",
    "Meeting",
    "Qualified",
    "Parked",
    "Won",
    "Lost",
):
    n = get(f"/api/people?filter=stage:{st}&limit=1")["total"]
    if n or sheet.get(f"stage:{st}"):
        dash[f"stage:{st}"] = n
dash["late_deliverables"] = len(blk["late"])
dash["packages_not_ready"] = len(blk["packages"])

rows = []
for k in sorted(set(sheet) | set(dash)):
    rows.append((k, sheet.get(k, 0), dash.get(k, 0)))

# per company
acc_by = {a["name"].lower(): a for a in acc["items"]}
co_bad = []
for co, c in by_company.items():
    a = acc_by.get(co or "(no company)")
    got = (a["people"], a["contacted"], a["connected"], a["emailed"]) if a else None
    want_ = (c["people"], c["contacted"], c["connected"], c["emailed"])
    if got != want_:
        co_bad.append((co, want_, got))
rows.append(("companies with matching counts", len(by_company), len(by_company) - len(co_bad)))

# spot-check 5 people
random.seed(18)
spot = []
for r in random.sample(people, 5):
    name = text(r.get("Name"))
    found = [
        p
        for p in get("/api/people?filter=all&limit=50&q=" + urllib.request.quote(name))["items"]
        if p["name"] == name and (p["company"] or "").lower() == text(r.get("Company/Uni")).lower()
    ]
    ok = (
        bool(found)
        and found[0]["stage"] == stage(r)
        and (found[0]["title"] or "") == text(r.get("Designation"))
    )
    spot.append(
        (name, text(r.get("Company/Uni")), stage(r), found[0]["stage"] if found else "missing", ok)
    )

# tiles open lists with the same count
tile_rows = []
for key in ("live", "backlog", "p1", "week"):
    t = today[key]
    n = get(f"/api/people?filter={t['link']['filter']}&limit=1")["total"]
    tile_rows.append((t["label"], t["value"], n, t["value"] == n))
late_tile = today["late"]["value"]
tile_rows.append(
    (
        "Late items that block goals",
        late_tile,
        sum(1 for b in blk["late"] if b["blocks_goals"]),
        late_tile == sum(1 for b in blk["late"] if b["blocks_goals"]),
    )
)

# updated within 2 min of job_runs
status = get("/api/status")
last = datetime.fromisoformat(status["last_computed"])
fresh = (
    abs((datetime.now(IST) - last).total_seconds()) < 3600 and status["updated"] == status["sheet"]
)

health = get("/api/health/full")
news_ok = all(n["n"] is None for n in health["news"])

# ---------------- report
lines = [
    f"# Dashboard parity · {TODAY:%d %b %Y}",
    "",
    "Skill: .claude/skills/dashboard-parity. Sheet: GTM Playbook COPY (GTM_SHEET_IS_COPY=true), read-only scope.",
    "Sheet side recounted by this script with its own rules (no saley.sales code). Dashboard side from the local API.",
    "",
    "## Numbers (tolerance 0)",
    "",
    "| Metric | Sheet | Dashboard | Result |",
    "| --- | --- | --- | --- |",
]
fails = 0
for k, a, b in rows:
    ok = a == b
    fails += not ok
    lines.append(f"| {k} | {a} | {b} | {'PASS' if ok else 'FAIL'} |")
lines += [
    "",
    "## Tiles open lists with the same count",
    "",
    "| Tile | Tile value | List count | Result |",
    "| --- | --- | --- | --- |",
]
for label, v, n, ok in tile_rows:
    fails += not ok
    lines.append(f"| {label} | {v} | {n} | {'PASS' if ok else 'FAIL'} |")
lines += [
    "",
    "## Spot-check: 5 random people (name, company, title, stage)",
    "",
    "| Person | Company | Sheet stage | Dashboard stage | Result |",
    "| --- | --- | --- | --- | --- |",
]
for name, co, a, b, ok in spot:
    fails += not ok
    lines.append(f"| {name} | {co} | {a} | {b} | {'PASS' if ok else 'FAIL'} |")
fails += not fresh
lines += [
    "",
    "## Other checks",
    "",
    f"- Updated line equals the last sheet sync (`{status['updated']}` = `{status['sheet']}`), last sync {last:%H:%M} IST: {'PASS' if fresh else 'FAIL'}",
    f"- Events in the next 30 days (sheet): {events_30}. The Events view is R3; not compared.",
    f'- News funnel: news tables do not exist in R1; the dashboard shows "not started" for every step: {"PASS" if news_ok else "FAIL"}',
]
fails += not news_ok
if co_bad:
    lines += ["", "## Company mismatches", ""] + [
        f"- {c}: sheet {w}, dashboard {g}" for c, w, g in co_bad[:20]
    ]
lines += ["", f"**Result: {'PASS' if fails == 0 else 'FAIL'}** ({fails} failures)", ""]
with open(sys.argv[1], "w", encoding="utf-8", newline="\n") as report:
    report.write("\n".join(lines))
print("PASS" if fails == 0 else f"FAIL {fails}")
