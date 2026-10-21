"""Browser checks of releases R1 to R4 on the dashboard app at `/` (ported at the cut-over, R5
T16b, D224): views, tiles, search, drawers, signals, pack, Saley card and confirm/dismiss.

Each check verifies the behaviour its matrix row names (R1.1-B01, R2-B02, R3-B01, R4-B02,
R4-B03), read through the hooks of docs/08 Appendix B. Where a later decision changed what a row
describes, the check verifies the decided behaviour and its docstring names the decision; the
list is in docs/QUESTIONS.md Q-R5-12. R2-B01 (`done_undo`) and the step 9 click path
(`click_path`) are the R5 checks of the same names (checks_r5, checks_r5_team): Undo in place,
D204 and D220.
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from qa_browser.checks_r5_team import CONTROL_FACTS
from qa_browser.core import (
    ALLOWED_POST,
    ASK_PATH,
    DRAWERS,
    MIN_RAIL_VIEWS,
    NARROW,
    QA_USER,
    SETTLE_MS,
    WIDE,
    Run,
    api_items,
    click_act,
    forget_seen,
    fresh_page,
    hide_other_news,
    no_error_box,
    no_scroll,
    order_of,
    rail_names,
    settle,
    snap,
)
from qa_browser.r5_rules import control_problems

VIEW = "section.view.on"
# D202: the tiles of Today; `live` is the funnel's Talking now bar, `late` the blockers line.
TILE_KEYS = ("deals", "backlog", "p1", "week")
LIST_ROWS = f"{VIEW} tbody tr, {VIEW} [data-goal], {VIEW} [data-pager-list] [data-row]"
SEEN_COUNT = VIEW + ' [data-seen-tab="{tab}"]'


def views(page: Page, base: str, run: Run, shots: Path, width: int) -> None:
    """Every rail view (+ money) at one width: shown, no error box, no sideways scroll, a
    screenshot (R1.1-B01, gate step 8)."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{base}/#today")
    settle(page)
    names = rail_names(page)
    run.expect(len(names) >= MIN_RAIL_VIEWS, f"{width}: only {len(names)} views in the rail")
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


TILES_WAIT_MS = 10_000
# A tile number: "12", or the deals headline "0 of 3" (closed of target; the target may be "—").
TILE_NUMBER = re.compile(r"(\d+)(?: of (?:\d+|—))?")
# Tiles that open a page of cards, not a list: the number counts something else (deals closed);
# the click must show the cards (as the R1.1 check did with `.card.goal`).
CARD_TILES = {"deals": "[data-goal]"}


def _number(link: Locator) -> int | None:
    match = TILE_NUMBER.fullmatch(link.locator(".v").inner_text().strip())
    return int(match.group(1)) if match else None


def tiles(page: Page, base: str, run: Run) -> None:
    """R1.1-B01 tile clicks. D202: Today shows the funnel (one `a[data-tile]` per stage) and the
    tiles deals, backlog, p1 and week; the R1.1 tile `live` is the Talking now bar and `late`
    the blockers line (today_funnel checks it). Each number link names its source (tile title,
    bar accessible name) and shows a real number (never a dash; silent-failure review). Its
    click opens a list: a number above 0 opens rows, a 0 opens the list's empty-state line
    (T16b review M1: never skipped)."""
    page.set_viewport_size({"width": WIDE, "height": 900})
    page.goto(f"{base}/#today")
    settle(page)
    # The tiles come from /api/today; on a slow first load `settle` can return before they
    # render (a 1024 run read "tiles []" once). Wait for them; the count is still checked below.
    with contextlib.suppress(PlaywrightTimeout):  # no tile at all: the expect below says so
        page.wait_for_selector("#tiles a.tile[data-tile]", timeout=TILES_WAIT_MS)
    keys = page.locator("#tiles a.tile[data-tile]").evaluate_all(
        "ts => ts.map(t => t.dataset.tile)"
    )
    run.expect(tuple(keys) == TILE_KEYS, f"today: tiles {keys}, want {list(TILE_KEYS)} (D202)")
    bars = page.locator(f"{VIEW} [data-funnel] a[data-tile]").evaluate_all(
        "ts => ts.map(t => t.dataset.tile)"
    )
    run.expect(len(bars) >= 1, "today: no funnel bar")
    run.expect("talking" in bars, "today: no Talking now bar (the R1.1 `live` tile, D202)")
    links = [(f'#tiles a.tile[data-tile="{k}"]', "title", k) for k in keys]
    links += [(f'{VIEW} [data-funnel] a[data-tile="{k}"]', "aria-label", k) for k in bars]
    for selector, source, key in links:
        page.goto(f"{base}/#today")
        settle(page)
        link = page.locator(selector)
        run.expect(bool(link.get_attribute(source)), f"tile {key}: no source ({source})")
        value = _number(link)
        # A page of dashes is not a pass: every number must be real (silent-failure review).
        if not run.expect(value is not None, f"tile {key}: no number"):
            continue
        link.click()
        settle(page)
        view = page.evaluate("location.hash").lstrip("#").split("?")[0]
        run.expect(view not in ("", "today"), f"tile {key}: click did not open a list")
        no_error_box(page, run, f"tile {key}")
        rows = page.locator(LIST_ROWS).count()
        if key in CARD_TILES:
            cards = page.locator(f"{VIEW} {CARD_TILES[key]}").count()
            run.expect(cards > 0, f"tile {key}: the page it opens shows no cards")
        elif value > 0:
            run.expect(rows > 0, f"tile {key}: {value}, but the list it opens is empty")
        else:
            empty = page.locator(f"{VIEW} .empty")
            said = empty.first.inner_text().strip() if empty.count() else ""
            run.expect(rows == 0 and bool(said), f"tile {key}: 0, but no empty-state line")


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
    """R1.1-B01: 3 People drawers, each with the person's name and only https links that open
    with noopener. Hooks (Appendix B): `[data-row][data-person]`, `[data-open-person]`,
    `.drawer.on`, the Sheet title."""
    page.set_viewport_size({"width": WIDE, "height": 900})
    page.goto(f"{base}/#people?filter=all")
    settle(page)
    rows = page.locator(f"{VIEW} [data-row][data-person]")
    count = rows.count()
    run.expect(count >= DRAWERS, f"drawers: {count} people rows, want {DRAWERS}")
    for i in range(min(DRAWERS, count)):
        rows.nth(i).locator("[data-open-person]").click()
        settle(page)
        drawer = page.locator(".drawer.on")
        if not run.expect(drawer.count() == 1, f"drawer {i}: did not open"):
            continue
        name = drawer.locator('[data-slot="sheet-title"]').inner_text()
        run.expect(bool(name.strip()) and name.strip() != "—", f"drawer {i}: no name")
        no_error_box(page, run, f"drawer {i}")
        for a in drawer.locator("a[target=_blank]").all():
            href = a.get_attribute("href") or ""
            rel = a.get_attribute("rel") or ""
            run.expect(href.startswith("https://"), f"drawer {i}: link not https: {href}")
            run.expect("noopener" in rel, f"drawer {i}: link without noopener")
        path = shots / f"drawer-{i}.png"
        page.screenshot(path=str(path))
        run.screens.append(path.name)
        page.keyboard.press("Escape")
        page.wait_for_timeout(SETTLE_MS)


def audit_controls(page: Page, run: Run, where: str) -> None:
    """Every [data-act] control of the shown page: a plain button, aimed at an allowed path;
    no form and no button that can submit (gate step 9)."""

    def allowed(path: str) -> bool:
        return path == ASK_PATH or bool(ALLOWED_POST.match(path))

    facts = page.evaluate(CONTROL_FACTS)
    problems = control_problems(where, facts["controls"], allowed)
    run.expect(not problems, f"click-path: {problems[:5]}")
    run.expect(facts["forms"] == 0, f"click-path: {where}: {facts['forms']} forms on the page")
    run.expect(facts["submits"] == 0, f"click-path: {where}: {facts['submits']} submit buttons")


def confirm_decision(page: Page, base: str, run: Run) -> None:
    """Decisions view: filter by person, confirm the seeded proposal, see it active with a name."""
    page.goto(f"{base}/#decisions")
    settle(page)
    page.select_option("#dec-person", "kushal")
    settle(page)
    row = page.locator("#dec-table tbody tr", has_text="qa:D1")
    if not run.expect(row.count() == 1, f"confirm_dismiss: {row.count()} rows for qa:D1, want 1"):
        return
    if click_act(page, run, row.locator('[data-act="confirm"]'), "confirm") is None:
        return
    after = page.locator("#dec-table tbody tr", has_text="qa:D1")
    run.expect(after.locator('[data-act="confirm"]').count() == 0, "confirm_dismiss: Confirm stays")
    active = api_items(page, base, "/api/decisions?state=active&person=kushal&limit=50")
    ok = [d for d in active if d["label"] == "qa:D1" and d["confirmed_by_name"]]
    run.expect(len(ok) == 1, "confirm_dismiss: the decision is not active with a name")


def confirm_dismiss(page: Page, base: str, run: Run) -> None:
    """R2-B02: Confirm a seeded decision; dismiss (the "Skip" Button, D220) a seeded Inbox card,
    reload, the card is gone and the other card stays."""
    confirm_decision(page, base, run)
    page.goto(f"{base}/#asks")
    settle(page)
    card = page.locator("#inbox-list .inbox-card", has_text="QA Seed Meeting B")
    if not run.expect(card.count() == 1, f"confirm_dismiss: {card.count()} cards, want 1"):
        return
    if click_act(page, run, card.locator('[data-act="dismiss"]'), "dismiss") is None:
        return
    page.reload()
    settle(page)
    left = page.locator("#inbox-list .inbox-card", has_text="QA Seed Meeting B").count()
    run.expect(left == 0, "confirm_dismiss: the dismissed card came back")
    other = page.locator("#inbox-list .inbox-card", has_text="QA Seed Meeting A").count()
    run.expect(other == 1, "confirm_dismiss: another card went away")


# ---------------------------------------------------------------- R3: signals, events, suggestions

COMPANY = "QA Seed Co"
SIG_ROWS = f"{VIEW} #sig-list > li"


def window_line(page: Page) -> str:
    stamp = page.locator(f"{VIEW} [data-stamp]")
    return stamp.inner_text() if stamp.count() == 1 else ""


def seen_count(page: Page, tab: str) -> int:
    raw = page.locator(SEEN_COUNT.format(tab=tab)).get_attribute("data-count") or ""
    return int(raw) if raw.isdigit() else -1


def pick_tab(page: Page, tab: str) -> None:
    page.locator(SEEN_COUNT.format(tab=tab)).click()
    settle(page)


def sig_first_look(page: Page, base: str, run: Run, shots: Path) -> bool:
    """Signals, New tab, first visit: both seeded stories are new, with every field, and the
    page tells Saley it showed them (one seen mark). Real stories are made seen for the QA
    user first (D226), so ranking cannot push a seeded story off the first page of 10."""
    posts = len(run.seen_posts)
    fresh_page(page, base, "#signals")
    no_error_box(page, run, "signals")
    week = window_line(page)
    run.expect("Last 7 days" in week, f"signals_pages: window line reads {week!r}")
    rows = page.locator(SIG_ROWS, has_text="QA Seed:")
    if not run.expect(rows.count() == 2, f"signals_pages: {rows.count()} seeded stories, want 2"):
        return False
    row = page.locator(SIG_ROWS, has_text="funding round")
    link = row.locator("a[target=_blank]")
    run.expect((link.get_attribute("href") or "").startswith("https://"), "signals_pages: link")
    run.expect("noopener" in (link.get_attribute("rel") or ""), "signals_pages: no noopener")
    meta = row.inner_text()
    # The news tag (Opportunity) is not on the row any more (R5 T15); sig_filters checks it
    # through the tag filter.
    for word in ("Importance 5", "Impact 2", "Ours", "QA Seed Wire"):
        run.expect(word in meta, f"signals_pages: {word!r} missing in the story ({meta!r})")
    page.wait_for_timeout(SETTLE_MS)
    run.expect(len(run.seen_posts) > posts, "signals_pages: the list sent no seen mark")
    snap(page, run, shots, "signals-all.png")
    return True


def sig_range_and_seen(page: Page, run: Run) -> bool:
    """The range switch changes the window. D157 replaced the "N you saw are left out" line and
    its "Show them" link: the seen stories leave New and the Seen earlier tab counts and shows
    them (the tab is the "Seen" mark)."""
    before = window_line(page)
    page.locator(f'{VIEW} [data-range-chips] [data-filter="day"]').click()
    settle(page)
    day = window_line(page)
    run.expect("Last 24 hours" in day and day != before, f"signals_pages: range kept {day!r}")
    new = page.locator(SIG_ROWS, has_text="QA Seed:").count()
    run.expect(new == 0, f"signals_pages: {new} seen stories still on New (D157)")
    earlier = seen_count(page, "seen")
    if not run.expect(earlier >= 2, f"signals_pages: Seen earlier counts {earlier}, want >= 2"):
        return False
    pick_tab(page, "seen")
    rows = page.locator(SIG_ROWS, has_text="QA Seed:")
    run.expect(rows.count() == 2, "signals_pages: Seen earlier does not show the seen stories")
    return True


def sig_filters(page: Page, run: Run) -> None:
    """On Seen earlier: the server rank (D216 dropped the sort select: ours and impact first),
    the tag filter, the source text and a category chip (D156: names, never letters)."""
    a, b = order_of(page, SIG_ROWS, "funding round", "quiet note")
    run.expect(0 <= a < b, f"signals_pages: rank did not put the funding round first ({a}, {b})")
    page.select_option("#sig-tag", "Opportunity")
    settle(page)
    run.expect(page.locator(SIG_ROWS, has_text="quiet note").count() == 0, "signals_pages: tag")
    run.expect(
        page.locator(SIG_ROWS, has_text="funding round").count() == 1, "signals_pages: tag lost"
    )
    page.select_option("#sig-tag", "")
    page.fill("#sig-src", "QA Seed Wire")
    settle(page)
    run.expect(page.locator(SIG_ROWS, has_text="QA Seed:").count() == 2, "signals_pages: source")
    page.fill("#sig-src", "no such outlet qa")
    settle(page)
    run.expect(
        page.locator(SIG_ROWS, has_text="QA Seed:").count() == 0, "signals_pages: source miss"
    )
    page.fill("#sig-src", "")
    page.locator(f'{VIEW} [data-cat-chips] [data-filter="Funding"]').click()
    settle(page)
    no_error_box(page, run, "signals category filter")
    page.locator(f'{VIEW} [data-cat-chips] [data-filter="all"]').click()
    settle(page)


def sig_linked(page: Page, run: Run) -> None:
    """Linked to us (New tab): the seeded signal with its company, next step and the
    one-match flag, and the company filter."""
    pick_tab(page, "new")
    page.locator(f'{VIEW} [data-sig-tabs] [data-filter="linked"]').click()
    settle(page)
    row = page.locator(SIG_ROWS, has_text=COMPANY)
    if not run.expect(
        row.count() >= 1, "signals_pages: the seeded signal is not on the Linked tab"
    ):
        return
    text = row.first.inner_text()
    run.expect("About: QA Seed Co" in text, f"signals_pages: linked entity missing ({text!r})")
    run.expect("Next step: Ask QA Seed Co" in text, "signals_pages: action hint missing")
    run.expect("Check the match" in text, "signals_pages: the one-match flag is missing")
    run.expect(page.locator("#sig-entity").is_visible(), "signals_pages: no entity filter")
    page.locator(f'{VIEW} [data-sig-tabs] [data-filter="all"]').click()
    settle(page)


def funnel_links(page: Page, base: str, run: Run) -> None:
    """Sources: each funnel number opens its list, and the list holds the same number. D157:
    the list is New plus Seen earlier, so the two tab counts add up to the number. "Stored as
    stories" opens the per-source counts with its line (D225)."""
    for key in ("scored", "scored_4", "linked"):
        page.goto(f"{base}/#sources")
        settle(page)
        link = page.locator(f'#news-funnel a[data-tile="{key}"]')
        if not run.expect(link.count() == 1, f"signals_pages: no funnel link for {key}"):
            continue
        wanted = _number(link)
        link.click()
        settle(page)
        view = page.evaluate("location.hash")
        run.expect(view.startswith("#signals"), f"signals_pages: {key} link opened {view!r}")
        got = seen_count(page, "new") + seen_count(page, "seen")
        run.expect(got == wanted, f"signals_pages: {key} is {wanted}, the list holds {got}")
    page.goto(f"{base}/#sources")
    settle(page)
    page.locator('#news-funnel a[data-tile="deduped"]').click()
    settle(page)
    pick = page.locator("#news-pick")
    shown = pick.inner_text() if pick.count() == 1 else ""
    run.expect("Stored as stories" in shown, f"signals_pages: deduped line {shown!r}")
    run.expect(page.locator("#src-table").count() == 1, "signals_pages: no per-source table")
    conn = page.locator("#conn-list").inner_text()
    run.expect("Apollo: off" in conn, f"signals_pages: Apollo is not 'off' ({conn!r})")
    run.expect("SearXNG" in conn, "signals_pages: no search line")
    run.expect("notes" in conn.lower(), "signals_pages: no meeting notes line")
    no_error_box(page, run, "sources news block")


def events_window(page: Page, base: str, run: Run, shots: Path) -> None:
    """Events: Upcoming ("From today on") and Past ("Before today") (R5-E02 checks the rows)."""
    page.goto(f"{base}/#events")
    settle(page)
    no_error_box(page, run, "events")
    run.expect("From today on" in window_line(page), "signals_pages: events window")
    when = page.locator(f"{VIEW} [data-when-tabs] [data-filter]")
    run.expect(when.count() == 2, "signals_pages: events tabs")
    page.locator(f'{VIEW} [data-when-tabs] [data-filter="past"]').click()
    settle(page)
    run.expect("Before today" in window_line(page), "signals_pages: past window")
    no_error_box(page, run, "events past")
    page.locator(f'{VIEW} [data-when-tabs] [data-filter="upcoming"]').click()
    settle(page)
    snap(page, run, shots, "events.png")
    no_scroll(page, run, "events")


def qa_row_adds(page: Page, base: str, undo: str) -> list[dict]:
    """row_add decisions signed by the QA user that name the seeded person ('' = any state)."""
    query = f"/api/decisions?person={QA_USER}&limit=200" + (f"&undo={undo}" if undo else "")
    return [
        d
        for d in api_items(page, base, query)
        if d.get("kind") == "row_add" and "QA Seed Person" in json.dumps(d)
    ]


def add_to_pipeline(page: Page, base: str, run: Run, row: Locator) -> None:
    """The seeded hit: open the form, edit the name, Add -> exactly one POST -> Undo -> back.
    The R3 "Saved · Undo" toast is now Undo in place on the same Button (D204, ported to Add
    in R5 T15): no timer, no toast; the API shows the open and then the undone row_add."""
    hit = row.locator("li[data-hit]", has_text="QA Seed Person")
    if not run.expect(hit.count() == 1, f"signals_pages: {hit.count()} seeded search hits, want 1"):
        return
    hit.locator('[data-open="add"]').click()
    form = hit.locator('[data-form="add"]')
    run.expect(form.is_visible(), "signals_pages: the Add form did not open")
    name, role = form.locator("input").nth(0), form.locator("input").nth(1)
    run.expect(
        name.input_value() == "QA Seed Person", f"signals_pages: name {name.input_value()!r}"
    )
    run.expect(role.input_value() == "Head of Data", f"signals_pages: role {role.input_value()!r}")
    run.expect("QA Seed Co" in form.inner_text(), "signals_pages: the form lacks the company")
    name.fill("QA Seed Person")  # the person edits the name; it stays the made-up one
    before = {d.get("id") for d in qa_row_adds(page, base, "")}
    if click_act(page, run, form.locator('[data-act="add"]'), "add") is None:
        return
    undo = form.locator('[data-act="undo"]')
    run.expect(undo.count() == 1, "signals_pages: Add did not turn into Undo in place")
    run.expect(page.locator("#toast-undo").count() == 0, "signals_pages: an Undo toast showed")
    # T16b review L4: exactly one NEW open row_add of the seeded person, signed by the QA user.
    opened = [d for d in qa_row_adds(page, base, "open") if d.get("id") not in before]
    run.expect(len(opened) == 1, f"signals_pages: {len(opened)} new open row adds, want 1")
    if undo.count() != 1 or click_act(page, run, undo, "undo") is None:
        return
    add = form.locator('[data-act="add"]')
    run.expect(
        add.count() == 1 and add.inner_text().strip() == "Add" and add.is_enabled(),
        "signals_pages: Add did not come back",
    )
    ids = {d.get("id") for d in opened}
    gone = [d for d in qa_row_adds(page, base, "undone") if d.get("id") in ids]
    run.expect(len(opened) == 1 and len(gone) == 1, "signals_pages: row add not marked undone")


def newco_checks(page: Page, base: str, run: Run) -> None:
    """The New companies kind, on its first load (the seeded company is new again). D164:
    Find people is off, so its Button is inert with the "coming soon" badge; when the feature
    is on, the Button must be enabled."""
    row = page.locator(f'{VIEW} #sg-list > li[data-kind="newco"]', has_text=COMPANY)
    if not run.expect(row.count() == 1, f"signals_pages: {row.count()} seeded companies, want 1"):
        return
    run.expect("Score" in row.inner_text(), "signals_pages: no score on the company")
    run.expect(row.locator("ul[aria-label] > li").count() >= 2, "signals_pages: no score parts")
    find = row.locator('[data-act="find"]')
    me = page.request.get(f"{base}/api/me", headers={"x-saley-as": QA_USER})
    on = bool(me.ok and (me.json().get("features") or {}).get("find_people"))
    if on:
        run.expect(find.count() == 1 and find.is_enabled(), "signals_pages: no enabled Find people")
    else:
        soon = row.locator('[data-feature="find_people"] [data-soon="find_people"]').count()
        run.expect(find.count() == 1 and soon == 1, "signals_pages: Find people is not 'soon'")
    state = row.locator('[data-field="search"]')
    shown = state.inner_text() if state.count() == 1 else ""
    run.expect(shown == "Search is done", f"signals_pages: search state {shown!r}")
    add_to_pipeline(page, base, run, row)


def suggest_pages(page: Page, base: str, run: Run, shots: Path) -> None:
    """The four kinds: each shows its window line and a list. A list is not blank: rows on New,
    or (D157) the "Nothing new" line with items in Seen earlier."""
    page.goto(f"{base}/#suggest")
    settle(page)
    for key in ("warm", "best", "newco", "res"):
        page.locator(f'{VIEW} [data-kind-tabs] [data-filter="{key}"]').click()
        settle(page)
        no_error_box(page, run, f"suggest {key}")
        window = window_line(page)
        run.expect(window.startswith("Window: Sheet"), f"signals_pages: {key} window {window!r}")
        rows = page.locator(f"{VIEW} #sg-list > li").count()
        nothing = page.locator(f"{VIEW} [data-nothing-new]").count() == 1
        run.expect(
            rows >= 1 or (nothing and seen_count(page, "seen") >= 1),
            f"signals_pages: {key} list is blank",
        )
        snap(page, run, shots, f"suggest-{key}.png")
        if key == "newco":
            newco_checks(page, base, run)
    audit_controls(page, run, "suggest")
    no_scroll(page, run, "suggest")


def today_sections(page: Page, base: str, run: Run, shots: Path) -> None:
    """Today "Needs a look" and "Saley suggests". D158: needs a look is one card per item (a
    link with its type), the seeded linked story among them; D164: mail replies are a "coming
    soon" badge while mail is off (the R3 "Replies:" line). D202: the blockers line sits in the
    tile area. Order: tiles, tasks, needs a look, Saley suggests; each suggestion is one link."""
    page.goto(f"{base}/#today")
    settle(page)
    no_error_box(page, run, "today")
    look = page.locator(f"{VIEW} #look a[data-kind]", has_text="QA Seed:")
    run.expect(look.count() >= 1, "signals_pages: Today 'needs a look' lacks the seeded story")
    today = page.request.get(f"{base}/api/today", headers={"x-saley-as": QA_USER})
    soon = (today.json().get("needs_look") or {}).get("soon") if today.ok else "?"
    badge = page.locator(f'{VIEW} #look [data-soon="mail_replies"]').count()
    run.expect(badge == (1 if soon == "mail_replies" else 0), f"signals_pages: replies {soon!r}")
    cards = page.locator(f"{VIEW} #suggests li")
    empty = page.locator(f"{VIEW} #suggests .empty")
    blank = cards.count() == 0 and not (empty.count() == 1 and empty.inner_text().strip())
    run.expect(not blank, "signals_pages: Saley suggests is blank")
    for i in range(cards.count()):
        links = cards.nth(i).locator("a, button").count()
        run.expect(links == 1, f"signals_pages: suggestion {i} has {links} controls, want 1")
    follows = page.evaluate(
        "['tiles','tasks','look'].map(id => !!(document.getElementById(id)"
        ".compareDocumentPosition(document.getElementById('suggests')) & 4))"
    )
    run.expect(all(follows), f"signals_pages: Today sections are out of order {follows}")
    inside = page.locator("#tiles [data-blockers-line]").count()
    run.expect(inside == 1, "signals_pages: the blockers line is not in the tile area (D202)")
    audit_controls(page, run, "today sections")
    snap(page, run, shots, "today-r3.png")
    no_scroll(page, run, "today")


def seen_after_reload(page: Page, base: str, run: Run) -> None:
    """The Linked signal was shown on the first visit. After a reload it is not on New; D157:
    Seen earlier counts and shows it (the R3 "1 you saw is left out" line and "Show them")."""
    fresh_page(page, base, "#signals?tab=linked")
    page.fill("#sig-entity", COMPANY)
    settle(page)
    new = page.locator(SIG_ROWS, has_text=COMPANY).count()
    run.expect(new == 0, "signals_pages: the seen signal is still on New")
    earlier = seen_count(page, "seen")
    if not run.expect(earlier == 1, f"signals_pages: Seen earlier counts {earlier}, want 1"):
        return
    pick_tab(page, "seen")
    shown = page.locator(SIG_ROWS, has_text=COMPANY).count()
    run.expect(shown == 1, "signals_pages: Seen earlier does not show the signal")


def signals_pages(page: Page, base: str, run: Run, shots: Path) -> None:
    """R3-B01: Signals, Events, Suggestions, Today sections, the seen signal after a reload,
    Add + Undo. Decisions that changed the R3 page: D157 (New / Seen earlier, no "left out"
    line), D216 (no sort select), D164 (Find people off), D158 and D202 (Today), D204 (Undo in
    place), D225 (per-source line)."""
    forget_seen()
    if not hide_other_news(run, "signals_pages"):
        return
    page.set_viewport_size({"width": WIDE, "height": 900})
    if sig_first_look(page, base, run, shots) and sig_range_and_seen(page, run):
        sig_filters(page, run)
        no_scroll(page, run, "signals")
    sig_linked(page, run)
    funnel_links(page, base, run)
    events_window(page, base, run, shots)
    suggest_pages(page, base, run, shots)
    today_sections(page, base, run, shots)
    seen_after_reload(page, base, run)
    # T16b review M3: leave the seeded rows new again for the checks that follow.
    forget_seen()
    hide_other_news(run, "signals_pages")


PACK_PATH = "**/api/pack*"
ASK_ROUTE = "**/api/ask"
# A fixed pack body (R4-B02): one null number must show a dash, never 0. The real pack of the
# gate database may not exist yet, so the page is also drawn from this body.
PACK_FIXTURE = {
    "sync_id": None,
    "last_computed": "2026-10-19T14:00:00",
    "last_computed_label": "Mon 19 Oct 14:00",
    "stale": False,
    "week_start": "2026-10-19",
    "built_at": "2026-10-19T08:30:00Z",
    "body": {
        "week_start": "2026-10-19",
        "last_week_day": "2026-10-12",
        "team": [
            {
                "key": "contacted",
                "label": "People contacted this week",
                "value": 12,
                "last_week": 9,
                "last_week_note": None,
            },
            {
                "key": "connect",
                "label": "LinkedIn connect rate %",
                "value": None,
                "last_week": None,
                "last_week_note": "no snapshot last week",
            },
        ],
        "scorecard": [
            {
                "industry": "QA Seed Industry",
                "people": 3,
                "contacted": 2,
                "connect_pct": None,
                "reply_pct": None,
                "reply_note": "no source",
                "meetings": 0,
                "deals": 0,
                "last_week": None,
                "last_week_note": "no snapshot last week",
            },
        ],
        "late_total": 1,
        "late": [
            {
                "item": "QA Seed deck",
                "owner": "sid",
                "blocks": ["Goal 1"],
                "due": "2026-10-12",
                "late_by_days": 7,
            },
        ],
        "packed_total": 1,
        "packed_reminders": [{"owner": "sid", "text": "QA Seed promise", "due": "2026-10-21"}],
        "tasks": [
            {
                "person": "sid",
                "total": 1,
                "items": [{"title": "QA Seed task", "due_label": "Tue", "kind": "task"}],
            }
        ],
    },
}
EVIL_ANSWER = "<img src=x onerror=alert(1)>\nline two"
DASH = "—"


def pack_fixture_checks(page: Page, base: str, run: Run, shots: Path) -> None:
    """The page drawn from a fixed body: dash for null, late item, reminders, tasks, scorecard."""
    page.route(
        PACK_PATH,
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(PACK_FIXTURE)
        ),
    )
    try:
        page.goto(f"{base}/#pack")
        page.reload()
        settle(page)
        tiles = page.locator("#pack-tiles .tile")
        run.expect(tiles.count() == 2, f"weekly_pack_page: {tiles.count()} number tiles, want 2")
        first = tiles.nth(0).inner_text()
        run.expect("12" in first and "last week 9" in first, f"weekly_pack_page: tile {first!r}")
        value = tiles.nth(1).locator(".v").inner_text()
        note = tiles.nth(1).locator(".d").inner_text()
        run.expect(
            value == DASH and note == "no snapshot last week",
            f"weekly_pack_page: a null number reads {value!r} / {note!r}, want a dash",
        )
        for box, needle in (
            ("#pack-late", "QA Seed deck"),
            ("#pack-reminders", "QA Seed promise"),
            ("#pack-tasks", "QA Seed task"),
            ("#pack-score", "QA Seed Industry"),
        ):
            found = page.locator(box, has_text=needle).count()
            run.expect(found == 1, f"weekly_pack_page: {box} lacks {needle!r}")
        score = page.locator("#pack-score tbody tr").first.inner_text()
        run.expect(
            DASH in score and "no snapshot" in score, f"weekly_pack_page: score row {score!r}"
        )
        week = page.locator("#pack-week").inner_text()
        run.expect(week.startswith("Week of Mon 19 Oct"), f"weekly_pack_page: week {week!r}")
        run.expect(
            page.locator("#v-pack", has_text="Coming in R4").count() == 0,
            "weekly_pack_page: the R4 placeholder is still there",
        )
        snap(page, run, shots, "pack-r4.png")
        page.set_viewport_size({"width": NARROW, "height": 900})
        page.wait_for_timeout(SETTLE_MS)
        scroll = page.evaluate("document.documentElement.scrollWidth")
        run.expect(scroll <= NARROW, f"weekly_pack_page: scroll at {NARROW}px is {scroll}")
        page.set_viewport_size({"width": WIDE, "height": 900})
    finally:
        page.unroute(PACK_PATH)


def weekly_pack_page(page: Page, base: str, run: Run, shots: Path) -> None:
    """R4-B02: the rail opens the pack page; real pack or the empty line; then a fixed body."""
    page.set_viewport_size({"width": WIDE, "height": 900})
    page.goto(f"{base}/#today")
    settle(page)
    link = page.locator('.rail a[data-v="pack"]')
    run.expect(link.count() == 1 and "R4" not in link.inner_text(), "weekly_pack_page: rail link")
    real = page.request.get(f"{base}/api/pack", headers={"x-saley-as": QA_USER})
    if real.status == 200:  # a real pack exists: the page shows its numbers
        link.click()
        settle(page)
        want = len(real.json()["body"]["team"])
        got = page.locator("#pack-tiles .tile").count()
        run.expect(got == want, f"weekly_pack_page: {got} tiles for {want} numbers")
    else:  # no pack yet: the page must not be asked (a 404 is a console error)
        run.expect(real.status == 404, f"weekly_pack_page: /api/pack gave {real.status}")
    pack_fixture_checks(page, base, run, shots)
    run.expect(urlparse(page.url).fragment == "pack", "weekly_pack_page: not on #pack")
    run.expect(page.locator("#v-pack.on").count() == 1, "weekly_pack_page: pack view not shown")


def ask_answer_checks(page: Page, run: Run) -> None:
    """A hostile answer is shown as plain text: no element is made from it."""
    body = {"text": EVIL_ANSWER, "known": True, "shown": 0, "more": 0, "left_out": 2}
    page.route(
        ASK_ROUTE,
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)
        ),
    )
    try:
        page.fill("#ask-input", "QA Seed question")
        page.click("#ask-send")
        page.locator("#ask-state", has_text="2 you saw").wait_for()
        run.expect(page.locator("#ask-answer *").count() == 0, "saley_card: answer holds elements")
        shown = page.locator("#ask-answer").inner_text()
        run.expect(shown == EVIL_ANSWER, f"saley_card: the answer reads {shown!r}")
    finally:
        page.unroute(ASK_ROUTE)


def saley_card(page: Page, base: str, run: Run, shots: Path) -> None:
    """R4-B03: the Ask card sends one question, shows the answer as text, keeps line breaks."""
    page.set_viewport_size({"width": WIDE, "height": 900})
    fresh_page(page, base, "#today")
    card = page.locator("#ask-card")
    if not run.expect(card.count() == 1, "saley_card: no Ask card on Today"):
        return
    before = len(run.ask_posts)
    page.click("#ask-send")  # an empty question sends nothing
    run.expect(len(run.ask_posts) == before, "saley_card: an empty question was sent")
    hint = page.locator("#ask-state").inner_text()
    run.expect(hint == "Type a question first.", f"saley_card: hint reads {hint!r}")
    page.fill("#ask-input", "What is new today?")
    with page.expect_response(lambda r: urlparse(r.url).path == ASK_PATH) as hit:
        page.click("#ask-send")
    run.expect(hit.value.status == 200, f"saley_card: /api/ask gave {hit.value.status}")
    sent = hit.value.request.headers.get("x-saley-act")
    run.expect(sent == "1", "saley_card: the question had no X-Saley-Act")
    settle(page)
    answer = page.locator("#ask-answer")
    run.expect(answer.is_visible() and answer.inner_text().strip() != "", "saley_card: no answer")
    style = page.evaluate("getComputedStyle(document.getElementById('ask-answer')).whiteSpace")
    run.expect(style == "pre-line", f"saley_card: white-space is {style!r}, want pre-line")
    run.expect(len(run.ask_posts) == before + 1, "saley_card: want exactly one question POST")
    snap(page, run, shots, "ask-card-r4.png")
    ask_answer_checks(page, run)
    run.expect(len(run.ask_posts) == before + 2, "saley_card: the second question is not counted")
