"""Browser checks of R5 T15: Signals, Suggestions and Events (matrix R5.yaml).

seen_tabs (R5-N03), signals_categories (R5-N05), events_page (R5-E02). A file of its own:
`checks_r5.py` is near the 800-line ceiling. `checks_r5` imports these names, so the CLI finds
them with the other R5 checks (`--app next`). The rules live in `r5_rules.py` (pure, tested);
this file only reads the page. No check here presses a control that writes: tabs and chips
are GETs. The pages' own seen marks (POST /api/seen, no button) are counted apart by `watch`.
Each check compares the page with the answer the PAGE got (captured from its own request),
because the page marks what it shows as seen: a second API call would see other counts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from urllib.parse import urlparse

from playwright.sync_api import Page, Request, Response, Route

from qa_browser.checks_r5_pages import open_view
from qa_browser.core import QA_USER, SEEN_PATH, SETTLE_MS, WIDE, Run, forget_seen, settle
from qa_browser.r5_rules import (
    category_chip_problems,
    category_pick_problems,
    events_problems,
    seeded_event_problems,
    seen_post_problems,
    seen_tab_problems,
    view_url,
)

SEEN_FACTS = """() => {
  const view = document.querySelector('section.view.on');
  if (!view) return {tabs: {}, rows: 0, text: '', nothing_new: false, pressed: null};
  const tabs = {};
  let pressed = null;
  view.querySelectorAll('[data-seen-tabs] [data-seen-tab]').forEach((t) => {
    tabs[t.dataset.seenTab] = t.dataset.count;
    if (t.getAttribute('aria-pressed') === 'true') pressed = t.dataset.seenTab;
  });
  return {tabs, pressed, rows: view.querySelectorAll('[data-pager-list] [data-row]').length,
    text: view.innerText, nothing_new: !!view.querySelector('[data-nothing-new]')};
}"""
CHIP_TEXT = """() => [...document.querySelectorAll(
  'section.view.on [data-cat-chips] [data-filter]')].map((b) => b.innerText)"""
ROW_CATS = """() => [...document.querySelectorAll(
  'section.view.on [data-pager-list] [data-row][data-story]')].map((r) => r.dataset.cat)"""
EVENT_FACTS = """() => {
  const view = document.querySelector('section.view.on');
  const visible = (n) => n.getClientRects().length > 0;
  return {
    headers: [...view.querySelectorAll('#ev-table th')].map((h) => h.innerText),
    rows: [...view.querySelectorAll('#ev-table tr[data-row][data-event]')].map((r) => ({
      name: (r.querySelector('td .font-medium') || {}).innerText || '',
      registered: r.dataset.registered === '1',
      badge: !!r.querySelector('[data-tag-ref="registered"]'),
      city: (r.querySelector('td[data-city]') || {}).innerText || '',
      venue: (r.querySelector('td[data-venue] [data-one-line]') || {}).innerText || null,
    })),
    soon: [...document.querySelectorAll('[data-soon="speaker_match"]')].filter(visible).length,
  };
}"""
LISTS = (
    # (view, the list path in the page's GET, the seen item type of its New tab)
    ("signals", "/api/signals", "story"),
    ("suggest", "/api/suggest/warm", "suggest_warm"),
)


def _is_list(path: str, tab: str) -> Callable[[Response], bool]:
    def match(response: Response) -> bool:
        url = urlparse(response.url)
        return response.request.method == "GET" and url.path == path and f"view={tab}" in url.query

    return match


def _seen_bodies(page: Page) -> tuple[list[dict], Callable[[Request], None]]:
    """Collect the bodies of the page's POST /api/seen while a step runs."""
    bodies: list[dict] = []

    def on_request(request: Request) -> None:
        if request.method == "POST" and urlparse(request.url).path == SEEN_PATH:
            bodies.append(json.loads(request.post_data or "{}"))

    page.on("request", on_request)
    return bodies, on_request


def _open_list(page: Page, base: str, entry: str, view: str, path: str) -> dict:
    """Open `view` once from Today (the page mounts fresh: one GET, one seen mark) and return
    the answer the page got for its New tab. No reload after it: a reload would ask again and
    show other counts, because the first load marked its items seen."""
    page.set_viewport_size({"width": WIDE, "height": 900})
    page.goto(view_url(base, entry, "today"))
    settle(page)
    with page.expect_response(_is_list(path, "new")) as answer:
        page.goto(view_url(base, entry, view))
    page.wait_for_timeout(SETTLE_MS)
    return answer.value.json()


def seen_tabs(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-N03: on Signals and Suggestions, the New and Seen earlier tabs carry the counts of the
    page's own answer, New shows at most 10 rows and marks exactly them seen (one POST, the
    legacy body), Seen earlier pages by offset, no "left out" line anywhere, and an empty New
    tab (the answer emptied in a route) says "Nothing new since your last visit"."""
    notes = []
    for view, path, item_type in LISTS:
        open_view(page, base, entry, "today")  # a clean page; its marks are not counted below
        bodies, listener = _seen_bodies(page)
        try:
            api = _open_list(page, base, entry, view, path)
            facts = page.evaluate(SEEN_FACTS)
            problems = seen_tab_problems(f"{view} new", api, facts)
            problems += seen_post_problems(view, item_type, api.get("items") or [], bodies)
        finally:
            page.remove_listener("request", listener)
        run.expect(not problems, f"seen_tabs: {problems[:5]}")
        with page.expect_response(_is_list(path, "seen")) as answer:
            page.locator('section.view.on [data-seen-tab="seen"]').click()
        page.wait_for_timeout(SETTLE_MS)
        problems = seen_tab_problems(f"{view} seen", answer.value.json(), page.evaluate(SEEN_FACTS))
        run.expect(not problems, f"seen_tabs: {problems[:5]}")
        notes.append(f"{view}: new {api.get('new_total')}, seen {api.get('seen_total')}")
    notes.append(empty_new(page, base, run, entry))
    run.notes["seen_tabs"] = notes


def empty_new(page: Page, base: str, run: Run, entry: str) -> str:
    """The New answer of Suggestions with no item (a route empties the real answer): the tab
    says 0 and the list says "Nothing new since your last visit"."""
    view, path, _ = LISTS[1]

    def emptied(route: Route, request: Request) -> None:
        if "view=new" not in urlparse(request.url).query:
            route.fallback()  # the guard sends it on as the QA user
            return
        response = route.fetch(headers={**request.headers, "x-saley-as": QA_USER})
        body = {**response.json(), "items": [], "total": 0, "new_total": 0}
        route.fulfill(response=response, json=body)

    pattern = re.compile(re.escape(path) + r"\?")
    page.route(pattern, emptied)
    try:
        api = _open_list(page, base, entry, view, path)
        problems = seen_tab_problems(f"{view} empty new", api, page.evaluate(SEEN_FACTS))
    finally:
        page.unroute(pattern, emptied)
    run.expect(not problems, f"seen_tabs: {problems[:5]}")
    return "an empty New answer shows the 'Nothing new' line"


def signals_categories(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-N05: the category chips are All + the six names, never a letter; picking Funding asks
    cat=Funding, and the rows on New or Seen earlier are all Funding (the seed has one)."""
    open_view(page, base, entry, "signals")
    problems = category_chip_problems(page.evaluate(CHIP_TEXT))
    run.expect(not problems, f"signals_categories: {problems}")
    urls: list[str] = []
    answers: list[dict] = []
    rows: list[str] = []
    for tab in ("new", "seen"):
        with page.expect_response(_is_list("/api/signals", tab)) as answer:
            if tab == "new":
                page.locator('section.view.on [data-cat-chips] [data-filter="Funding"]').click()
            else:
                page.locator('section.view.on [data-seen-tab="seen"]').click()
        page.wait_for_timeout(SETTLE_MS)
        urls.append(answer.value.url)
        answers.append(answer.value.json())
        rows += page.evaluate(ROW_CATS)
    problems = category_pick_problems("Funding", urls, answers, rows)
    run.expect(not problems, f"signals_categories: {problems}")
    run.notes["signals_categories"] = [f"cat=Funding: {len(rows)} rows on New and Seen earlier"]


def events_page(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-E02: Events shows City and Venue columns, registered events first with the tag, City
    only Bangalore, London or Other, and the speaker-match "coming soon" badge; the QA events
    (made new again first) show with the City and Venue the rule gives."""
    from saley.qa.seed import EVENTS

    forget_seen()  # the views check marked the first page seen; the QA events are new again
    _open_list(page, base, entry, "events", "/api/events")
    facts = page.evaluate(EVENT_FACTS)
    problems = events_problems(facts) + seeded_event_problems(facts.get("rows") or [], list(EVENTS))
    run.expect(not problems, f"events_page: {problems[:5]}")
    run.notes["events_page"] = [f"{len(facts.get('rows') or [])} rows, registered first"]
