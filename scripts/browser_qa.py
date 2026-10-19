"""Browser QA for the release gate: steps 8 (skill browser-qa) and 9 (skill click-path-audit).

Why: the API tests prove the numbers; this proves a person can see them. It drives the
installed Edge through Playwright, read-only, on the local dashboard only:
- every view in the left rail, at widths 1440, 1024 and 390, with a screenshot each;
- every Today tile: the click opens its list and the list loads;
- the search terms in tests/fixtures/search-terms.txt: grouped results or a "0 results" card;
- 3 People drawers;
- zero console errors, zero failed requests, no horizontal scroll;
- step 9: the page sends no request other than GET (R1 has no button that changes state).
Every request to a host other than localhost is aborted: never Google, Gmail or LinkedIn.

Run:  uv run python scripts/browser_qa.py --base http://localhost:8090 --release R1.1
      (scripts/verify.py passes the WEB_PORT base itself)
Prints one JSON line with the verdict (SHIP or BLOCK) and the findings.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page, Request, Route, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WIDTHS = (1440, 1024, 390)
LOCAL_HOSTS = {"localhost", "127.0.0.1"}
SETTLE_MS = 600
DRAWERS = 3
TIMEOUT_MS = 15000


class Run:
    def __init__(self) -> None:
        self.findings: list[str] = []
        self.non_get: list[str] = []
        self.blocked: list[str] = []
        self.checks = 0
        self.screens: list[str] = []

    def expect(self, ok: bool, finding: str) -> None:
        self.checks += 1
        if not ok:
            self.findings.append(finding)


def guard(run: Run) -> Callable[[Route, Request], None]:
    """Route handler: localhost only. Anything else is aborted and recorded."""

    def handle(route: Route, request: Request) -> None:
        url = urlparse(request.url)
        if url.scheme == "data" or url.hostname in LOCAL_HOSTS:
            route.continue_()
            return
        run.blocked.append(request.url)
        route.abort()

    return handle


def watch(page: Page, run: Run) -> None:
    page.on("console", lambda m: m.type == "error" and run.findings.append(f"console: {m.text}"))
    page.on("pageerror", lambda e: run.findings.append(f"page error: {e}"))
    page.on(
        "response",
        lambda r: r.status >= 400 and run.findings.append(f"HTTP {r.status} {r.url}"),
    )

    def on_request(request: Request) -> None:
        if request.method != "GET":
            run.non_get.append(f"{request.method} {request.url}")

    page.on("request", on_request)


def settle(page: Page) -> None:
    page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
    page.wait_for_timeout(SETTLE_MS)


def no_error_box(page: Page, run: Run, where: str) -> None:
    errors = page.locator(".view.on .err, .drawer.on .err").all_inner_texts()
    run.expect(not errors, f"{where}: error shown: {errors}")


def views(page: Page, base: str, run: Run, shots: Path, width: int) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{base}/#today")
    settle(page)
    names = [
        v
        for v in page.locator(".rail a[data-v]:visible").evaluate_all(
            "as => as.map(a => a.dataset.v)"
        )
    ]
    run.expect(len(names) >= 10, f"{width}: only {len(names)} views in the rail")
    for name in [*names, "money"]:
        page.goto(f"{base}/#{name}")
        settle(page)
        shown = page.locator(f"#v-{name}.on").count()
        run.expect(shown == 1, f"{width} {name}: view not shown")
        no_error_box(page, run, f"{width} {name}")
        scroll = page.evaluate("document.documentElement.scrollWidth")
        run.expect(scroll <= width, f"{width} {name}: horizontal scroll ({scroll}px)")
        path = shots / f"{width}-{name}.png"
        page.screenshot(path=str(path), full_page=True)
        run.screens.append(path.name)


def tiles(page: Page, base: str, run: Run) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{base}/#today")
    settle(page)
    keys = page.locator("#tiles a.tile").evaluate_all("ts => ts.map(t => t.dataset.tile)")
    run.expect(len(keys) == 6, f"today: {len(keys)} tiles, want 6")
    # A page of dashes is not a pass: the people numbers must be real (silent-failure review).
    for key in ("live", "backlog", "p1"):
        value = page.locator(f'#tiles a.tile[data-tile="{key}"] .v').inner_text().strip()
        run.expect(value.isdigit(), f"tile {key}: no number ({value!r})")
    for key in keys:
        page.goto(f"{base}/#today")
        settle(page)
        tile = page.locator(f'#tiles a.tile[data-tile="{key}"]')
        run.expect(bool(tile.get_attribute("title")), f"tile {key}: no source tooltip")
        tile.click()
        settle(page)
        view = page.evaluate("location.hash").lstrip("#").split("?")[0]
        run.expect(view not in ("", "today"), f"tile {key}: click did not open a list")
        no_error_box(page, run, f"tile {key}")
        rows = page.locator(".view.on tbody tr, .view.on .card.goal").count()
        run.expect(rows > 0, f"tile {key}: the list it opens is empty")


def searches(page: Page, base: str, run: Run, shots: Path, terms: list[str]) -> None:
    for term in terms:
        page.goto(f"{base}/#today")
        settle(page)
        page.fill("#gsearch", term)
        page.press("#gsearch", "Enter")
        settle(page)
        hits = page.locator("#search-results .hit").count()
        zero = page.locator("#search-results .empty-state").count()
        run.expect(hits > 0 or zero == 1, f"search {term!r}: an empty box")
        if hits:
            marks = page.locator("#search-results mark").count()
            groups = page.locator("#search-results [data-group]").count()
            run.expect(groups >= 1, f"search {term!r}: results not grouped")
            run.expect(marks >= 1, f"search {term!r}: no bold match")
        no_error_box(page, run, f"search {term!r}")
        path = shots / f"search-{re.sub(r'[^a-z0-9]+', '-', term.lower()).strip('-')}.png"
        page.screenshot(path=str(path), full_page=True)
        run.screens.append(path.name)


def drawers(page: Page, base: str, run: Run, shots: Path) -> None:
    page.goto(f"{base}/#people?filter=all")
    settle(page)
    for i in range(DRAWERS):
        page.locator("#ppl-table tbody tr.click").nth(i).click()
        settle(page)
        name = page.locator(".drawer.on h1").inner_text()
        run.expect(bool(name.strip()), f"drawer {i}: no name")
        no_error_box(page, run, f"drawer {i}")
        for a in page.locator(".drawer.on a[target=_blank]").all():
            href = a.get_attribute("href") or ""
            rel = a.get_attribute("rel") or ""
            run.expect(href.startswith("https://"), f"drawer {i}: link not https: {href}")
            run.expect("noopener" in rel, f"drawer {i}: link without noopener")
        path = shots / f"drawer-{i}.png"
        page.screenshot(path=str(path))
        run.screens.append(path.name)
        page.keyboard.press("Escape")
        page.wait_for_timeout(SETTLE_MS)


def click_path(page: Page, run: Run) -> None:
    """Step 9: R1 has no button that changes state. Prove no form posts and no non-GET call."""
    forms = page.locator("form").count()
    submit = page.locator("button:not([type=button])").count()
    run.expect(forms == 0, f"click-path: {forms} forms on the page")
    run.expect(submit == 0, f"click-path: {submit} buttons that can submit")
    run.expect(not run.non_get, f"click-path: non-GET requests {run.non_get[:5]}")


def check_all(page: Page, base: str, run: Run, shots: Path, terms: list[str]) -> None:
    page.set_default_timeout(TIMEOUT_MS)
    page.route("**/*", guard(run))
    watch(page, run)
    for width in WIDTHS:
        views(page, base, run, shots, width)
    tiles(page, base, run)
    searches(page, base, run, shots, terms)
    drawers(page, base, run, shots)
    click_path(page, run)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="http://localhost:<WEB_PORT>")
    parser.add_argument("--release", required=True)
    parser.add_argument("--terms", default=str(ROOT / "tests" / "fixtures" / "search-terms.txt"))
    args = parser.parse_args()
    if urlparse(args.base).hostname not in LOCAL_HOSTS:
        print(json.dumps({"verdict": "BLOCK", "findings": ["base must be localhost"]}))
        return 1
    shots = ROOT / "docs" / "test-reports" / f"{args.release}-screens"
    shots.mkdir(parents=True, exist_ok=True)
    lines = Path(args.terms).read_text(encoding="utf-8").splitlines()
    terms = [t.strip() for t in lines if t.strip() and not t.startswith("#")]
    run = Run()
    run.expect(len(terms) >= 1, "no search terms in the terms file")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge")
        try:
            check_all(browser.new_page(), args.base, run, shots, terms)
        finally:
            browser.close()
    run.expect(not run.blocked, f"requests to other hosts were aborted: {run.blocked[:5]}")
    verdict = "SHIP" if not run.findings else "BLOCK"
    print(
        json.dumps(
            {
                "verdict": verdict,
                "checks": run.checks,
                "findings": run.findings,
                "non_get": run.non_get,
                "screens": len(run.screens),
                "dir": str(shots.relative_to(ROOT)).replace("\\", "/"),
            }
        )
    )
    return 0 if verdict == "SHIP" else 1


if __name__ == "__main__":
    sys.exit(main())
