"""Impeccable audit scores per page: check them and print the test-report table (R5-I01).

Why: the R5 brief asks for the Impeccable audit score in the test report. Impeccable runs as
text guidance only (D196): each page is scored by hand with the rubric in
.claude/skills/impeccable/reference/audit.md (5 dimensions, 0-4 each, total /20), in both
themes. The scores live in docs/test-reports/<release>-impeccable.json. This script refuses a
file that misses a page, a theme or a dimension, so the gate cannot pass on a partial audit.
The brief sets no minimum score: the score is recorded, not judged. Open critical findings
fail the check (plan T17: "fix every critical finding").

Run:
  uv run python scripts/impeccable_report.py --check --release R5   exit 1 on a gap
  uv run python scripts/impeccable_report.py --table --release R5   markdown for the report
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "docs" / "test-reports"

# docs/08 "Views", in rail order. Sources and health is admin only, but it is a page too.
PAGES = (
    "today",
    "goals",
    "accounts",
    "people",
    "suggestions",
    "signals",
    "blockers",
    "events",
    "inbox",
    "commitments",
    "decisions",
    "pack",
    "scorecard",
    "health",
)
THEMES = ("light", "dark")
# reference/audit.md "Audit Health Score", in its order.
DIMENSIONS = ("accessibility", "performance", "theming", "responsive", "integrity")
MAX_SCORE = 4
# reference/audit.md rating bands: lowest total of each band.
BANDS = ((18, "Excellent"), (14, "Good"), (10, "Acceptable"), (6, "Poor"), (0, "Critical"))


def report_path(release: str) -> Path:
    return REPORTS / f"{release}-impeccable.json"


def band(total: int) -> str:
    return next(name for low, name in BANDS if total >= low)


def problems(data: object) -> list[str]:
    """Every gap in the scores file, as one plain line each. Empty means the file is complete."""
    if not isinstance(data, dict) or not isinstance(data.get("pages"), dict):
        return ["the file has no 'pages' object"]
    found: list[str] = []
    pages = data["pages"]
    for page in PAGES:
        themes = pages.get(page)
        if not isinstance(themes, dict):
            found.append(f"{page}: no scores")
            continue
        for theme in THEMES:
            found += _theme_problems(f"{page} {theme}", themes.get(theme))
    found += [f"{name}: not a page" for name in pages if name not in PAGES]
    return found


def _theme_problems(where: str, entry: object) -> list[str]:
    if not isinstance(entry, dict):
        return [f"{where}: no scores"]
    scores = entry.get("scores")
    if not isinstance(scores, dict):
        return [f"{where}: no 'scores' object"]
    found = []
    for dim in DIMENSIONS:
        value = scores.get(dim)
        # bool is an int in Python: True must not count as a score of 1.
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_SCORE:
            found.append(f"{where}: {dim} must be a whole number 0-{MAX_SCORE}")
    critical = entry.get("critical_open", 0)
    if not isinstance(critical, int) or isinstance(critical, bool) or critical < 0:
        found.append(f"{where}: critical_open must be a whole number")
    elif critical:
        found.append(f"{where}: {critical} critical finding(s) still open")
    return found


def total(scores: dict[str, int]) -> int:
    return sum(scores[dim] for dim in DIMENSIONS)


def table(data: dict) -> str:
    """The markdown table for the test report: one row per page and theme."""
    head = "| Page | Theme | " + " | ".join(d.capitalize() for d in DIMENSIONS)
    lines = [head + " | Total | Band |", "|" + " --- |" * (len(DIMENSIONS) + 4)]
    for page in PAGES:
        for theme in THEMES:
            scores = data["pages"][page][theme]["scores"]
            cells = " | ".join(str(scores[d]) for d in DIMENSIONS)
            score = total(scores)
            lines.append(f"| {page} | {theme} | {cells} | {score}/20 | {band(score)} |")
    lines.append("")
    lines.append(
        "Scored by hand with the Impeccable 4.6.0 audit rubric (D196); the bundled detector "
        "step was not run (D195)."
    )
    return "\n".join(lines)


def load(release: str) -> object:
    path = report_path(release)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--release", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--table", action="store_true")
    args = parser.parse_args(argv)
    data = load(args.release)
    if data is None:
        print(f"FAIL: {report_path(args.release).name} is missing or not JSON")
        return 1
    found = problems(data)
    if found:
        print("FAIL: " + "; ".join(found[:20]))
        return 1
    if args.table:
        print(table(data))  # type: ignore[arg-type]
    else:
        count = len(PAGES) * len(THEMES)
        print(f"PASS: {count} page scores recorded ({len(PAGES)} pages, both themes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
