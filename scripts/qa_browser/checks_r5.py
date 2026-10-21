"""Browser checks of release R5 (docs/plans/R5.md, matrix tests/matrix/R5.yaml).

T12 (shell and design system): csp_clean (R5-S06), legend_every_page (R5-Q01),
no_underlined_links (R5-Q02), coming_soon_badges (R5-Q03), themes (R5-Q04), pagers (R5-Q07).
T13 (Today): today_funnel (R5-T02), no_page_search (R5-T03), done_undo (R5-T09, and R2-B01
since the cut-over: D204 Undo in place), needs_look_cards (R5-T13), add_email (R5-T16).
T14 (Q4 goals, Accounts, People): goals_cards (R5-G02), accounts_page (R5-A03), people_drawer
(R5-P03) live in `checks_r5_pages.py` (this file is near the line ceiling) and are imported here.
T15 (Signals, Suggestions, Events): seen_tabs (R5-N03), signals_categories (R5-N05),
events_page (R5-E02) live in `checks_r5_watch.py`, imported here the same way.
T16a (team pages): commitment_owner_toast (R5-C03), run_news_now (R5-N09) and the new app's
click_path (R5-K01) live in `checks_r5_team.py`, imported here the same way.
D207: no_view_as (R5-V02), load_error_retry (R5-V01).
Each check takes `entry`: "/" since the cut-over (T16b, D224; it was "/next" during the
strangler, D147).
The rules live in `r5_rules.py` (pure, tested); this file only reads the page.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from playwright.sync_api import ConsoleMessage, Locator, Page, Request, Route

from qa_browser.checks_r5_pages import (  # noqa: F401 (T14 checks, found by the CLI here)
    accounts_page,
    goals_cards,
    people_drawer,
)
from qa_browser.checks_r5_team import (  # noqa: F401 (T16a checks, found by the CLI here)
    click_path,
    commitment_owner_toast,
    run_news_now,
)
from qa_browser.checks_r5_watch import (  # noqa: F401 (T15 checks, found by the CLI here)
    events_page,
    seen_tabs,
    signals_categories,
)
from qa_browser.core import (
    ALLOWED_POST,
    ASK_PATH,
    NARROW,
    QA_USER,
    SETTLE_MS,
    WIDE,
    Run,
    api_items,
    click_act,
    rail_names,
    settle,
    today_list_of,
)
from qa_browser.r5_rules import (
    blockers_problems,
    control_problems,
    demanded,
    done_back_problems,
    drop_abort_noise,
    funnel_problems,
    hover_picks,
    hover_problems,
    legend_problems,
    link_problems,
    load_error_problems,
    look_card_problems,
    page_search_problems,
    pager_problems,
    pagers_verdict,
    recovered_problems,
    shown_count,
    soon_problems,
    theme_problems,
    undo_row_problems,
    view_as_problems,
    view_url,
)

THEME_KEY = "saley.theme"  # web/src/theme.ts and web/public/theme-init.js
EXTRA_VIEWS = ("money", "search")  # views with no rail link (Blockers tile, the search box)
_CSP_CONTEXTS: set[int] = set()  # browser contexts that already hold the CSP listener

# Runs before any page script (CDP), so the page CSP does not apply to it. It records every
# CSP report of the page; the check reads and empties the list per view.
CSP_LISTENER = """
window.__saleyCsp = [];
window.__saleyCspReady = true;
document.addEventListener('securitypolicyviolation', (e) => {
  window.__saleyCsp.push(e.violatedDirective + ' ' + (e.blockedURI || 'inline'));
});
"""
TAKE_CSP = """() => {
  if (!window.__saleyCspReady) return null;
  const list = window.__saleyCsp; window.__saleyCsp = []; return list;
}"""
LEGEND_FACTS = """() => {
  const view = document.querySelector('section.view.on');
  if (!view) return {legends: 0};
  const legends = view.querySelectorAll('[data-legend]');
  const legend = legends[0];
  const tags = legend ? [...legend.querySelectorAll('[data-tag]')] : [];
  const refs = [...view.querySelectorAll('[data-tag-ref]')]
    .filter((n) => !legend || !legend.contains(n) )
    .filter((n) => n.getClientRects().length)
    .map((n) => n.dataset.tagRef);
  const bare = tags
    .filter((t) => !t.querySelector('[data-swatch]') || !t.innerText.trim())
    .map((t) => t.dataset.tag);
  const content = view.querySelector(
    '[data-content], [data-row], [data-tile], table, [data-pager], [data-click-card]');
  const above = !!legend && !!content &&
    legend.getBoundingClientRect().top <= content.getBoundingClientRect().top;
  return {legends: legends.length, tags: tags.map((t) => t.dataset.tag), refs, bare,
    content: !!content, above};
}"""
# Each visible link with its kind (by hook), its text and its underline. The links are numbered
# in `data-qa-link`, so the hover step can find the one it picked.
LINK_FACTS = """() => [...document.querySelectorAll('a')]
  .filter((a) => a.getClientRects().length && getComputedStyle(a).visibility !== 'hidden')
  .map((a, i) => {
    a.setAttribute('data-qa-link', String(i));
    const kind = a.dataset.v !== undefined ? 'rail'
      : a.dataset.tile !== undefined ? 'tile'
      : a.hasAttribute('data-click-card') ? 'card'
      : a.hasAttribute('data-legacy-link') ? 'legacy'
      : (a.dataset.slot || (a.className.split(' ')[0]) || 'a');
    return {kind, text: a.innerText, href: a.getAttribute('href') || '',
      decoration: getComputedStyle(a).textDecorationLine};
  })"""
# The underline of a link and of every element inside it, read while the mouse is on it.
HOVER_DECORATION = """(a) => [a, ...a.querySelectorAll('*')]
  .map((n) => getComputedStyle(n).textDecorationLine).join(' ')"""
SOON_FACTS = """() => {
  const visible = (n) => n.getClientRects().length > 0;
  const view = document.querySelector('section.view.on') || document.body;
  const soon = [...document.querySelectorAll('[data-soon]')].filter(visible)
    .map((n) => n.dataset.soon);
  const places = [...view.querySelectorAll('[data-feature]')];
  return {
    hosts: (view.dataset.features || '').split(' ').filter(Boolean),
    soon,
    gates: places.map((n) => n.dataset.feature),
    gated: places.filter((n) => n.querySelector(`[data-soon="${n.dataset.feature}"]`))
      .map((n) => n.dataset.feature),
  };
}"""
PAGER_FACTS = """() => [...document.querySelectorAll('section.view.on [data-pager]')].map((p) => ({
  total: p.dataset.total, offset: p.dataset.offset, shown: p.dataset.shown,
  rows: p.querySelectorAll('[data-pager-list] [data-row]').length,
  more: (p.querySelector('[data-pager-more]') || {}).innerText || '',
}))"""
THEME_FACTS = """() => ({
  dark: document.documentElement.classList.contains('dark'),
  bg: getComputedStyle(document.body).backgroundColor,
})"""


def all_views(page: Page, base: str, entry: str) -> list[str]:
    page.set_viewport_size({"width": WIDE, "height": 900})
    page.goto(view_url(base, entry, "today"))
    settle(page)
    return [*rail_names(page), *EXTRA_VIEWS]


def each_view(
    page: Page, base: str, entry: str, views: list[str], fn: Callable[[str], None]
) -> None:
    for view in views:
        page.goto(view_url(base, entry, view))
        settle(page)
        fn(view)


def set_theme(page: Page, base: str, entry: str, theme: str | None) -> None:
    """Pick a theme the way a person's saved choice does (D163), then load the page again."""
    page.goto(view_url(base, entry, "today"))
    if theme is None:
        page.evaluate(f"() => localStorage.removeItem('{THEME_KEY}')")
    else:
        page.evaluate(f"() => localStorage.setItem('{THEME_KEY}', '{theme}')")
    page.reload()
    settle(page)


def csp_clean(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-S06: zero CSP reports and zero console errors on every view, light and dark, and in
    the phone menu (Radix inserts a <style> there; it must carry the nonce, D146)."""
    errors: list[str] = []

    def on_console(message: ConsoleMessage) -> None:
        if message.type == "error":
            errors.append(message.text)

    if id(page.context) not in _CSP_CONTEXTS:  # once per context: init scripts add up
        page.context.add_init_script(CSP_LISTENER)
        _CSP_CONTEXTS.add(id(page.context))
    page.on("console", on_console)
    try:
        views = all_views(page, base, entry)
        for theme in ("light", "dark"):
            set_theme(page, base, entry, theme)

            def check(view: str, theme: str = theme) -> None:
                found = page.evaluate(TAKE_CSP)
                if run.expect(
                    found is not None, f"csp_clean: {view}: the CSP listener did not load"
                ):
                    run.expect(not found, f"csp_clean: {theme} {view}: CSP reports {found[:3]}")

            each_view(page, base, entry, views, check)
        page.set_viewport_size({"width": NARROW, "height": 900})
        page.goto(view_url(base, entry, "today"))
        settle(page)
        menu = page.locator("[data-menu]")
        if run.expect(menu.is_visible(), "csp_clean: no menu Button at 390 px"):
            menu.click()
            page.wait_for_timeout(SETTLE_MS)
            found = page.evaluate(TAKE_CSP) or []
            run.expect(not found, f"csp_clean: phone menu: CSP reports {found[:3]}")
            page.keyboard.press("Escape")
        run.expect(not errors, f"csp_clean: {len(errors)} console errors: {errors[:3]}")
    finally:
        page.remove_listener("console", on_console)
        page.set_viewport_size({"width": WIDE, "height": 900})
        set_theme(page, base, entry, None)


def legend_every_page(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-Q01: every page has one legend at the top that explains every tag on it."""
    views = all_views(page, base, entry)

    def check(view: str) -> None:
        problems = legend_problems(view, page.evaluate(LEGEND_FACTS))
        run.expect(not problems, f"legend_every_page: {problems}")

    each_view(page, base, entry, views, check)
    run.notes["legend_every_page"] = views


def hover_each_kind(page: Page, view: str, links: list[dict], run: Run) -> None:
    """Hover the first link of each kind and read the underline of it and its children."""
    hovered = []
    for i in hover_picks(links):
        link = page.locator(f'a[data-qa-link="{i}"]')
        if not link.count() or not link.first.is_visible():
            continue
        link.first.hover()
        page.wait_for_timeout(SETTLE_MS // 4)
        hovered.append(
            {"kind": links[i]["kind"], "decoration": link.first.evaluate(HOVER_DECORATION)}
        )
    page.mouse.move(0, 0)
    problems = hover_problems(view, hovered)
    run.expect(not problems, f"no_underlined_links: {problems[:5]}")


def no_underlined_links(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-Q02: no visible link is underlined (also on hover, one link of each kind), and none
    shows a raw URL as its text."""
    views = all_views(page, base, entry)
    count = 0

    def check(view: str) -> None:
        nonlocal count
        links = page.evaluate(LINK_FACTS)
        count += len(links)
        problems = link_problems(view, links)
        run.expect(not problems, f"no_underlined_links: {problems[:5]}")
        hover_each_kind(page, view, links, run)

    each_view(page, base, entry, views, check)
    run.expect(count > 0, "no_underlined_links: no link found on any page")
    run.notes["no_underlined_links"] = [f"{count} links on {len(views)} pages"]


def coming_soon_badges(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-Q03: every feature that /api/me says is off shows its badge where it appears."""
    me = page.request.get(f"{base}/api/me")
    if not run.expect(me.ok, f"coming_soon_badges: /api/me gave {me.status}"):
        return
    features = me.json().get("features") or {}
    run.expect(bool(features), "coming_soon_badges: /api/me has no features")
    shown: dict[str, dict[str, list[str]]] = {}
    views = all_views(page, base, entry)
    each_view(
        page, base, entry, views, lambda view: shown.__setitem__(view, page.evaluate(SOON_FACTS))
    )
    problems = soon_problems(features, shown)
    run.expect(not problems, f"coming_soon_badges: {problems[:5]}")
    off = sorted(name for name, on in features.items() if not on)
    checked = demanded(features, shown)
    run.notes["coming_soon_badges"] = [
        f"off: {', '.join(off) or 'none'}",
        f"checked on visited pages: {', '.join(checked) or 'none'}",
    ]


def themes(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-Q04 (D163): the top-bar toggle switches, saves, and survives a reload."""
    set_theme(page, base, entry, None)
    before = page.evaluate(THEME_FACTS)
    toggle = page.locator("[data-theme-toggle]")
    if not run.expect(toggle.count() == 1, f"themes: {toggle.count()} theme toggles, want 1"):
        return
    toggle.click()
    page.wait_for_timeout(SETTLE_MS)
    after = page.evaluate(THEME_FACTS)
    saved = page.evaluate(f"() => localStorage.getItem('{THEME_KEY}')")
    page.reload()
    settle(page)
    reloaded = page.evaluate(THEME_FACTS)
    problems = theme_problems(
        {
            "before_dark": before["dark"],
            "after_dark": after["dark"],
            "saved": saved,
            "reload_dark": reloaded["dark"],
            "before_bg": before["bg"],
            "after_bg": after["bg"],
        }
    )
    run.expect(not problems, f"themes: {problems}")
    set_theme(page, base, entry, None)


def pagers(page: Page, base: str, run: Run, entry: str, allow_no_pager: bool = False) -> None:
    """R5-Q07: every pager shows at most 10 rows and a right "N more" count. No pager on any
    page is a FAIL, unless `--allow-no-pager` (T12 QA only; the gate never passes it)."""
    verified: list[str] = []
    views = all_views(page, base, entry)

    def check(view: str) -> None:
        for pager in page.evaluate(PAGER_FACTS):
            verified.append(view)
            problems = pager_problems(view, pager)
            run.expect(not problems, f"pagers: {problems}")

    each_view(page, base, entry, views, check)
    problems = pagers_verdict(verified, allow_no_pager)
    run.expect(not problems, f"pagers: {problems}")
    run.notes["pagers"] = sorted(set(verified)) or [f"no pager yet on {len(views)} pages"]


# ---------------------------------------------------------------- T13: Today on the new app

PAGE_TURNS = 20  # at most 200 tasks are paged through to find one seeded row
FUNNEL_FACTS = """() => [...document.querySelectorAll('section.view.on [data-funnel] a[data-tile]')]
  .map((a) => ({key: a.dataset.tile, href: a.getAttribute('href') || '',
    value: (a.querySelector('.v') || {}).innerText || ''}))"""
BLOCKERS_FACTS = """() => {
  const lines = document.querySelectorAll('section.view.on [data-blockers-line]');
  const line = lines[0];
  const button = line ? line.querySelector('[data-blockers-open]') : null;
  return {lines: lines.length, inside_tiles: !!(line && line.closest('#tiles')),
    count: line ? line.dataset.count : null, text: line ? line.innerText : '',
    button: button ? button.innerText : '', button_slot: button ? button.dataset.slot : null,
    href: button ? button.getAttribute('href') : ''};
}"""
INPUT_FACTS = """() => [...document.querySelectorAll('section.view.on input')]
  .filter((n) => n.getClientRects().length)
  .map((n) => ({id: n.id, type: n.type, label: n.getAttribute('aria-label') || '',
    placeholder: n.placeholder || ''}))"""
CONTROL_FACTS = """() => ({
  controls: [...document.querySelectorAll('section.view.on [data-act]')].map((n) => ({
    act: n.dataset.act, type: n.getAttribute('type'), endpoint: n.dataset.endpoint || ''})),
  forms: document.querySelectorAll('form').length,
  submits: document.querySelectorAll('section.view.on button:not([type=button])').length,
})"""
LOOK_FACTS = """() => [...document.querySelectorAll('section.view.on #look [data-click-card]')]
  .map((c) => {
    const badge = c.querySelector('[data-tag-ref]');
    return {tag: c.tagName, kind: c.dataset.kind, href: c.getAttribute('href') || '',
      badge: badge ? badge.dataset.tagRef : null, badge_text: badge ? badge.innerText : '',
      inner: c.querySelectorAll('a, button').length};
  })"""
UNDO_FACTS = """(row) => ({
  same_button: !!row.querySelector('[data-qa-pressed][data-act="undo"]'),
  undo: row.querySelectorAll('[data-act="undo"]').length,
  text: row.innerText,
  bars: document.querySelectorAll('#toast-undo').length,
  toasts: document.querySelectorAll('[data-toast-line]').length,
  focused: !!(document.activeElement && document.activeElement.hasAttribute('data-qa-pressed')),
})"""
DONE_BACK_FACTS = """(row) => ({
  same_button: !!row.querySelector('[data-qa-pressed][data-act="done"]'),
  undo: row.querySelectorAll('[data-act="undo"]').length,
  focused: !!(document.activeElement && document.activeElement.hasAttribute('data-qa-pressed')),
})"""


def today_api(page: Page, base: str, query: str = "") -> dict:
    """GET /api/today as the QA user (the page's own view of it)."""
    response = page.request.get(f"{base}/api/today{query}", headers={"x-saley-as": QA_USER})
    return response.json() if response.ok else {}


def open_today(page: Page, base: str, entry: str) -> None:
    """Today as the page shows it: the signed-in person's list (D207). A check that needs
    another person's list runs inside `today_list_of` (no UI for it)."""
    page.set_viewport_size({"width": WIDE, "height": 900})
    page.goto(view_url(base, entry, "today"))
    page.reload()  # a hash-only move keeps the old page state; start clean
    settle(page)


def find_task(page: Page, text: str) -> Locator | None:
    """The `#tasks li.task` row with `text`, paging forward with Next when it is not shown."""
    for _ in range(PAGE_TURNS):
        row = page.locator("#tasks li.task", has_text=text)
        if row.count() == 1:
            return row
        nxt = page.locator("section.view.on [data-pager] nav button").last
        if not nxt.count() or nxt.is_disabled():
            return None
        nxt.click()
        settle(page)
    return None


def hash_view(page: Page) -> str:
    return page.evaluate("location.hash").lstrip("#").split("?")[0]


def today_funnel(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-T02: the funnel bars (`a[data-tile]`) show the /api/today counts and open their
    lists; the blockers line has the count and an "Open blockers" Button, in the tile area."""
    open_today(page, base, entry)
    api = today_api(page, base)
    problems = funnel_problems(api.get("funnel") or [], page.evaluate(FUNNEL_FACTS))
    run.expect(not problems, f"today_funnel: {problems[:5]}")
    tiles = page.locator("#tiles a.tile[data-tile] .v").count()
    run.expect(tiles >= 1, "today_funnel: no tile in #tiles")
    problems = blockers_problems(api.get("blockers_line"), page.evaluate(BLOCKERS_FACTS))
    run.expect(not problems, f"today_funnel: {problems}")
    bar = page.locator("section.view.on [data-funnel] a[data-tile]").first
    if run.expect(bar.count() == 1, "today_funnel: no funnel bar to click"):
        bar.click()
        settle(page)
        view = hash_view(page)
        run.expect(view == "people", f"today_funnel: a bar opened #{view}, want #people")
    open_today(page, base, entry)
    opener = page.locator("section.view.on [data-blockers-open]")
    if run.expect(opener.count() == 1, "today_funnel: no Open blockers Button"):
        opener.click()
        settle(page)
        view = hash_view(page)
        run.expect(view == "blockers", f"today_funnel: Open blockers opened #{view}")
    run.notes["today_funnel"] = [
        f"{len(api.get('funnel') or [])} bars equal /api/today",
        f"blockers {shown_count((api.get('blockers_line') or {}).get('count'))}",
    ]


def no_page_search(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-T03: no page search box on Today and Q4 goals; the global `#gsearch` stays."""
    for view in ("today", "goals"):
        page.set_viewport_size({"width": WIDE, "height": 900})
        page.goto(view_url(base, entry, view))
        settle(page)
        shown = page.locator(f"#v-{view}.on").count()
        run.expect(shown == 1, f"no_page_search: {view} not shown")
        problems = page_search_problems(view, page.evaluate(INPUT_FACTS))
        run.expect(not problems, f"no_page_search: {problems}")
        run.expect(page.locator("#gsearch").is_visible(), f"no_page_search: {view}: no #gsearch")


def audit_today_controls(page: Page, run: Run) -> None:
    """Gate step 9 on the new Today: plain buttons aimed at an allowed POST; no form."""
    facts = page.evaluate(CONTROL_FACTS)

    def allowed(path: str) -> bool:
        return path == ASK_PATH or bool(ALLOWED_POST.match(path))

    problems = control_problems("today", facts["controls"], allowed)
    run.expect(bool(facts["controls"]), "click-path: today: no [data-act] control")
    run.expect(not problems, f"click-path: {problems[:5]}")
    run.expect(facts["forms"] == 0, f"click-path: today: {facts['forms']} forms")
    run.expect(facts["submits"] == 0, f"click-path: today: {facts['submits']} submit buttons")


def done_undo(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-T09: Done on the seeded reconnect task turns the SAME button into Undo inside the
    row: no timer text, no bottom bar, no toast. Undo brings Done back. The gate's server
    holds the writes (SHEET_WRITE_ENABLED=false); the QA rows never reach the sheet.
    D207: Sid's list shows through `today_list_of` (no picker); the QA user still signs."""
    with today_list_of(page, "sid"):
        done_undo_on_sid(page, base, run, entry)


def _reconnect_rows(page: Page, base: str, undo: str) -> list[dict]:
    """task_done rows of the seeded reconnect task signed by the QA user ('' = any undo state)."""
    query = f"/api/decisions?person={QA_USER}&limit=200" + (f"&undo={undo}" if undo else "")
    return [
        d
        for d in api_items(page, base, query)
        if d.get("kind") == "task_done" and d.get("person") == "QA Seed Reconnect"
    ]


def done_undo_on_sid(page: Page, base: str, run: Run, entry: str) -> None:
    open_today(page, base, entry)
    audit_today_controls(page, run)
    row = find_task(page, "QA Seed Reconnect")
    if not run.expect(row is not None, "done_undo: no 'QA Seed Reconnect' task on Sid's list"):
        return
    done = row.locator('[data-act="done"]')
    if not run.expect(done.count() == 1, "done_undo: the seeded task has no Done"):
        return
    done.evaluate("(b) => b.setAttribute('data-qa-pressed', '1')")
    before = {d.get("id") for d in _reconnect_rows(page, base, "")}
    if click_act(page, run, done, "done") is None:
        return
    # R2-B01 (T16b review M2): exactly one NEW open task_done row for this task, signed by the
    # QA user (the person filter); older rows of earlier runs cannot satisfy it.
    opened = [d for d in _reconnect_rows(page, base, "open") if d.get("id") not in before]
    run.expect(len(opened) == 1, f"done_undo: {len(opened)} new open Done rows, want 1")
    row = page.locator("#tasks li.task", has_text="QA Seed Reconnect")
    problems = undo_row_problems(row.evaluate(UNDO_FACTS))
    run.expect(not problems, f"done_undo: {problems}")
    undo = row.locator('[data-act="undo"]')
    undo.evaluate("(b) => b.setAttribute('data-qa-pressed', '1')")  # the same node, marked again
    if click_act(page, run, undo, "undo") is None:
        return
    row = page.locator("#tasks li.task", has_text="QA Seed Reconnect")
    problems = done_back_problems(row.evaluate(DONE_BACK_FACTS))
    run.expect(not problems, f"done_undo: {problems}")
    back = row.locator('[data-act="done"]:not([aria-disabled="true"])').count()
    run.expect(back == 1, "done_undo: Done did not come back after Undo")
    # R2-B01: Undo marked exactly that row undone, and no Done row of this task stays open.
    ids = {d.get("id") for d in opened}
    gone = [d for d in _reconnect_rows(page, base, "undone") if d.get("id") in ids]
    run.expect(len(opened) == 1 and len(gone) == 1, "done_undo: the Done row is not undone")
    still = [d for d in _reconnect_rows(page, base, "open") if d.get("id") in ids]
    run.expect(not still, "done_undo: the Done row is still open after Undo")
    run.notes["done_undo"] = ["Done -> Undo in place -> Done on 'QA Seed Reconnect'"]


def needs_look_cards(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-T13: every "Needs a look" card is one link with a type badge, no inner "Open"."""
    open_today(page, base, entry)
    api_cards = (today_api(page, base).get("needs_look") or {}).get("cards") or []
    cards = page.evaluate(LOOK_FACTS)
    problems = look_card_problems(api_cards, cards)
    run.expect(not problems, f"needs_look_cards: {problems[:5]}")
    if not cards:
        empty = page.locator("section.view.on #look .empty").count()
        run.expect(empty == 1, "needs_look_cards: no cards and no empty line")
    else:
        page.locator("section.view.on #look [data-click-card]").first.click()
        settle(page)
        view = hash_view(page)
        run.expect(view not in ("", "today"), "needs_look_cards: a card click opened nothing")
    run.notes["needs_look_cards"] = [f"{len(cards)} cards"]


def add_email(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-T16: the seeded add_email task has no Done; its click opens the drawer with the
    Email field focused (or disabled with "coming soon" when email writes are off). Read
    only: nothing is saved."""
    me = page.request.get(f"{base}/api/me")
    email_on = bool(((me.json() if me.ok else {}).get("features") or {}).get("email_write"))
    with today_list_of(page, "vaishnavi"):  # D207: the seeded research task is Vaishnavi's
        add_email_on_vaishnavi(page, run, base, entry, email_on)


def add_email_on_vaishnavi(page: Page, run: Run, base: str, entry: str, email_on: bool) -> None:
    open_today(page, base, entry)
    row = find_task(page, "QA Seed Research")
    if not run.expect(row is not None, "add_email: no 'QA Seed Research' task"):
        return
    run.expect(row.locator('[data-act="done"]').count() == 0, "add_email: the task has Done")
    button = row.locator("[data-add-email]")
    if not run.expect(button.count() == 1, "add_email: no Add email Button"):
        return
    button.click()
    page.wait_for_timeout(SETTLE_MS)
    drawer = page.locator(".drawer.on")
    if not run.expect(drawer.count() == 1, "add_email: the drawer did not open"):
        return
    field = drawer.locator("#drawer-email")
    run.expect(field.count() == 1, "add_email: no Email field in the drawer")
    save = drawer.locator('[data-act="email-save"]')
    aimed = save.get_attribute("type") == "button" and save.get_attribute("data-endpoint") == (
        "/api/act"
    )
    run.expect(aimed, "add_email: Save is not a button aimed at /api/act")
    focused = page.evaluate("document.activeElement && document.activeElement.id")
    if email_on:
        run.expect(focused == "drawer-email", f"add_email: focus is on {focused!r}")
    else:
        run.expect(field.is_disabled(), "add_email: the field works while email writes are off")
        soon = drawer.locator('[data-soon="email_write"]').count()
        run.expect(soon == 1, "add_email: no 'coming soon' while email writes are off")
    page.keyboard.press("Escape")
    page.wait_for_timeout(SETTLE_MS)
    run.expect(page.locator(".drawer.on").count() == 0, "add_email: Escape did not close it")
    run.notes["add_email"] = [f"email_write {'on' if email_on else 'off'}; nothing saved"]


# ---------------------------------------------------------------- D207: top bar

STATUS_API = re.compile(r"/api/status(?:\?|$)")
TOP_RETRY = "header [data-retry]:visible"
# The removed picker was pressable person Buttons in a header (#who, aria-pressed). People and
# goal rows also carry `data-person` as a row hook (T14); they open a drawer and are not a picker.
VIEW_AS_HOOKS = (
    "#act-as",
    "#act-as-wrap",
    "#viewing-as",
    "#who",
    "header [data-person], [aria-pressed][data-person]",
)
STATUS_FACTS = """() => {
  const line = document.querySelector('#updated');
  const bar = line ? line.closest('header, .topbar') || document.body : document.body;
  const retry = [...bar.querySelectorAll('[data-retry]')].find((n) => n.getClientRects().length);
  return {text: line ? line.innerText : '', retry_tag: retry ? retry.tagName : '',
    retry_visible: !!retry};
}"""
TOP_WORDS = """() => {
  const line = document.querySelector('#updated');
  const bar = line ? line.closest('header, .topbar') : null;
  return bar ? bar.innerText : '';
}"""


def no_view_as(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-V02 (D207): no "View as" / "Viewing as" in the top bar, no person tabs, no
    pressable person Buttons, and Today asks /api/today for the signed-in person (no `user=`)."""
    asked: list[str] = []

    def on_request(request: Request) -> None:
        if "/api/today" in request.url:
            asked.append(request.url)

    page.on("request", on_request)
    try:
        open_today(page, base, entry)
    finally:
        page.remove_listener("request", on_request)
    for view in ("today", "people"):
        page.goto(view_url(base, entry, view))
        settle(page)
        hooks = {sel: page.locator(sel).count() for sel in VIEW_AS_HOOKS}
        facts = {"hooks": hooks, "words": page.evaluate(TOP_WORDS), "today_urls": asked}
        problems = view_as_problems(facts)
        run.expect(not problems, f"no_view_as: {view}: {problems}")
    run.notes["no_view_as"] = [f"{len(asked)} /api/today calls, none with user="]


def load_error_retry(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-V01: /api/status aborted once -> the top bar names the data and the reason
    ("The server did not answer."), with a Retry button and no "Failed to fetch"; the route
    goes, Retry is pressed -> the Updated line is back."""
    aborted: list[str] = []

    def abort_once(route: Route, request: Request) -> None:
        if aborted:
            route.fallback()  # the guard sends it on as the QA user
            return
        aborted.append(request.url)
        route.abort()

    before = len(run.findings)
    page.set_viewport_size({"width": WIDE, "height": 900})
    page.route(STATUS_API, abort_once)
    try:
        page.goto(view_url(base, entry, "today"))
        page.reload()  # a fresh page: its first /api/status is the aborted one
        settle(page)
    finally:
        page.unroute(STATUS_API, abort_once)
    run.findings[before:] = drop_abort_noise(run.findings[before:], len(aborted))
    if not run.expect(aborted, "load_error_retry: the page asked for no /api/status"):
        return
    problems = load_error_problems(page.evaluate(STATUS_FACTS))
    if not run.expect(not problems, f"load_error_retry: {problems}"):
        return
    page.locator(TOP_RETRY).first.click()
    settle(page)
    problems = recovered_problems(page.evaluate(STATUS_FACTS))
    run.expect(not problems, f"load_error_retry: {problems}")
    run.notes["load_error_retry"] = ["abort /api/status once -> named error + Retry -> Updated"]
