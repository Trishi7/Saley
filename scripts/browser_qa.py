"""Browser QA for the release gate: steps 8 (skill browser-qa) and 9 (skill click-path-audit).

Why: the API tests prove the numbers; this proves a person can see them. It drives the
installed Edge through Playwright on the local dashboard only:
- every view in the left rail, at widths 1440, 1024 and 390, with a screenshot each;
- every Today tile: the click opens its list and the list loads;
- the search terms in tests/fixtures/search-terms.txt: grouped results or a "0 results" card;
- 3 People drawers;
- zero console errors, zero failed requests, no horizontal scroll;
- R2 named checks `done_undo` and `confirm_dismiss`, and step 9 (click-path audit): the only
  requests that are not GET are the five allowed POSTs, each made by one press of a
  `[data-act]` control, and every press changes the page. Any other non-GET, form or submit
  button fails. The one known exception (R3, D75) is `POST /api/seen`: a list tells Saley which
  items it showed. It has no button, so it is recorded apart (`seen_posts`), never in `non_get`.
  The second (R4, D131) is `POST /api/ask`: the Ask card sends a typed question. It is recorded
  apart too (`ask_posts`); it changes only `user_seen` and `bot_exchange`.
- R3 named check `signals_pages`: Signals (range, sort, filters, Linked, the funnel links),
  Events, the four Suggestions tabs, Today "needs a look" and "Saley suggests", "left out N"
  after a reload, and Add to pipeline -> one POST -> toast -> Undo, on the seeded "QA Seed Co".
- D207 named checks on both apps: `no_view_as` (no View as, no person tabs, Today without
  `user=`) and `load_error_retry` (/api/status aborted once: a named error and Retry, never
  "Failed to fetch"; Retry brings the Updated line back). A check that needs another person's
  task list adds `?user=` to /api/today in a route (`today_list_of`); there is no UI for it.
Every request to a host other than localhost is aborted: never Google, Gmail or LinkedIn.
Every /api call carries `X-Saley-As: qa` (the throwaway user of `saley.qa.seed`): the pages mark
what they show as "seen" for `qa`, never for a real person (A11). The run counts the seen marks
real people got while it ran and fails if there is one.

Safety: the controls are pressed only on rows that `saley.qa.seed` made (names "QA Seed ...",
keys `qa:`). They are not in the sheet, so a seeded write cannot reach it. The seed runs first
and the teardown runs last, in `finally`. A control outside those rows is checked, never pressed.
"Find people" is checked, never pressed: it would start a real web search. The seeded Add stays
held (run `saley dev` with SHEET_WRITE_ENABLED=false, as scripts/verify.py does) and the check
undoes it; the worker also refuses any append for a "QA Seed" company.

Run:  uv run python scripts/browser_qa.py --base http://localhost:8090 --release R1.1
      (scripts/verify.py passes the WEB_PORT base itself)
R5 app during the strangler (D147), one width (D142):
      uv run python scripts/browser_qa.py --base http://localhost:8090 --release R5           --app next --widths 1440
Prints one JSON line with the verdict (SHIP or BLOCK) and the findings.
"""

# ruff: noqa: F403, F405
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/ for the qa_browser package

from playwright.sync_api import Page, sync_playwright
from qa_browser import checks_r5
from qa_browser.checks_r1_r4 import *
from qa_browser.core import *
from qa_browser.r5_rules import entry_path


def check_all(
    page: Page,
    base: str,
    run: Run,
    shots: Path,
    terms: list[str],
    widths: tuple[int, ...] = WIDTHS,
    views_only: bool = False,
) -> None:
    """All groups. Low memory (D112): the gate runs one width per process; the extra widths
    run the views group only, the rest runs once at the first width."""
    page.set_default_timeout(TIMEOUT_MS)
    page.route("**/*", guard(run))
    watch(page, run)
    run.group("sign_in_page", lambda: sign_in_page(page, base, run))
    run.group("views", lambda: [views(page, base, run, shots, w) for w in widths])
    if views_only:
        return
    run.group("tiles", lambda: tiles(page, base, run))
    run.group("search", lambda: searches(page, base, run, shots, terms))
    run.group("drawers", lambda: drawers(page, base, run, shots))
    run.group("signals_pages", lambda: signals_pages(page, base, run, shots))
    run.group("weekly_pack_page", lambda: weekly_pack_page(page, base, run, shots))
    run.group("saley_card", lambda: saley_card(page, base, run, shots))
    run.group("done_undo", lambda: done_undo(page, base, run))
    run.group("confirm_dismiss", lambda: confirm_dismiss(page, base, run))
    run.group("click_path", lambda: click_path(page, base, run))
    # D207 (R5-V01, R5-V02): the same named checks run on both apps; the gate ANDs them.
    run.group("no_view_as", lambda: checks_r5.no_view_as(page, base, run, "/"))
    run.group("load_error_retry", lambda: checks_r5.load_error_retry(page, base, run, "/"))


def check_all_next(
    page: Page,
    base: str,
    run: Run,
    shots: Path,
    widths: tuple[int, ...] = WIDTHS,
    views_only: bool = False,
    entry: str = "/next",
    allow_no_pager: bool = False,
) -> None:
    """The R5 app (`--app next`, plan 3.5): sign in, the views at each width, then the R5
    checks once. The R1-R4 groups need the legacy page, so they do not run here (D147)."""
    page.set_default_timeout(TIMEOUT_MS)
    page.route("**/*", guard(run))
    watch(page, run)
    run.group("sign_in_page", lambda: sign_in_page(page, base, run))
    run.group("views", lambda: [views(page, base, run, shots, w, entry) for w in widths])
    if views_only:
        return
    for name in R5_CHECKS:
        check = getattr(checks_r5, name)
        if name == "pagers":
            run.group(name, lambda: checks_r5.pagers(page, base, run, entry, allow_no_pager))
        else:
            run.group(name, lambda check=check: check(page, base, run, entry))


# The named R5 checks of the matrix (tests/matrix/R5.yaml, tool browser), in run order.
R5_CHECKS = (
    "csp_clean",
    "legend_every_page",
    "no_underlined_links",
    "coming_soon_badges",
    "themes",
    "pagers",
    "today_funnel",
    "no_page_search",
    "done_undo",
    "needs_look_cards",
    "add_email",
    "goals_cards",
    "accounts_page",
    "people_drawer",
    "seen_tabs",
    "signals_categories",
    "events_page",
    "no_view_as",
    "load_error_retry",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="http://localhost:<WEB_PORT>")
    parser.add_argument("--release", required=True)
    parser.add_argument("--terms", default=str(ROOT / "tests" / "fixtures" / "search-terms.txt"))
    parser.add_argument("--widths", default=",".join(map(str, WIDTHS)), help="e.g. 1024")
    parser.add_argument("--views-only", action="store_true", help="only the views group (D112)")
    parser.add_argument(
        "--app",
        choices=("legacy", "next"),
        default="legacy",
        help="legacy: the R1-R4 checks on /; next: the R5 checks on /next (D147)",
    )
    parser.add_argument(
        "--allow-no-pager",
        action="store_true",
        help="R5 `pagers` passes with no pager on any page. Only for T12 QA, before T13 adds "
        "the first pager; the gate (scripts/verify.py) never passes it",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    widths = parse_widths(args.widths)
    if not widths:
        print(json.dumps({"verdict": "BLOCK", "findings": [f"bad --widths {args.widths!r}"]}))
        return 1
    if urlparse(args.base).hostname not in LOCAL_HOSTS:
        print(json.dumps({"verdict": "BLOCK", "findings": ["base must be localhost"]}))
        return 1
    shots = ROOT / "docs" / "test-reports" / f"{args.release}-screens"
    shots.mkdir(parents=True, exist_ok=True)
    lines = Path(args.terms).read_text(encoding="utf-8").splitlines()
    terms = [t.strip() for t in lines if t.strip() and not t.startswith("#")]
    run = Run()
    run.expect(len(terms) >= 1, "no search terms in the terms file")
    started = db_now()
    with seeded(run) as ready, sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge")
        try:
            if ready and args.app == "next":
                check_all_next(
                    browser.new_page(),
                    args.base,
                    run,
                    shots,
                    widths,
                    args.views_only,
                    entry_path(args.app),
                    args.allow_no_pager,
                )
            elif ready:
                check_all(browser.new_page(), args.base, run, shots, terms, widths, args.views_only)
        finally:
            browser.close()
        if ready:
            marks = real_seen_marks(started)
            run.expect(marks == 0, f"seen: {marks} marks went to real people during the run")
    run.expect(not run.blocked, f"requests to other hosts were aborted: {run.blocked[:5]}")
    verdict = "SHIP" if not run.findings else "BLOCK"
    print(
        json.dumps(
            {
                "verdict": verdict,
                "checks": run.checks,
                "findings": run.findings,
                "non_get": run.non_get,
                "seen_posts": run.seen_posts,
                "ask_posts": run.ask_posts,
                "sign_ins": len(run.sign_ins),
                "act_posts": run.act_clicks,
                "named": run.named,
                "notes": run.notes,
                "app": args.app,
                "screens": len(run.screens),
                "dir": str(shots.relative_to(ROOT)).replace("\\", "/"),
            }
        )
    )
    return 0 if verdict == "SHIP" else 1


if __name__ == "__main__":
    sys.exit(main())
