"""Browser checks of releases R1 to R4: views, tiles, search, drawers, signals, pack, Saley card,
done/undo, confirm/dismiss and the click-path audit (gate steps 8 and 9)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Locator, Page, Response
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from qa_browser.core import (
    ALLOWED_POST,
    ASK_PATH,
    CONTROL_KINDS,
    DRAWERS,
    LEFT_OUT,
    MIN_RAIL_VIEWS,
    NARROW,
    QA_MARKS,
    QA_USER,
    ROW_OF,
    SAVED_TOAST,
    SETTLE_MS,
    TIMEOUT_MS,
    WIDE,
    Run,
    api_items,
    fingerprint,
    forget_seen,
    fresh_page,
    no_error_box,
    no_scroll,
    order_of,
    rail_names,
    settle,
    snap,
    today_list_of,
)


def views(page: Page, base: str, run: Run, shots: Path, width: int, entry: str = "/") -> None:
    """Every rail view (+ money) at one width: shown, no error box, no sideways scroll, a
    screenshot. `entry` is "/next" for the R5 app during the strangler (D147); the R5 shots get
    a "next-" prefix so they never replace the legacy ones."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{base}{entry}#today")
    settle(page)
    names = rail_names(page)
    run.expect(len(names) >= MIN_RAIL_VIEWS, f"{width}: only {len(names)} views in the rail")
    prefix = "" if entry == "/" else f"{entry.strip('/')}-"
    for name in [*names, "money"]:
        page.goto(f"{base}{entry}#{name}")
        settle(page)
        shown = page.locator(f"#v-{name}.on").count()
        run.expect(shown == 1, f"{width} {name}: view not shown")
        no_error_box(page, run, f"{width} {name}")
        scroll = page.evaluate("document.documentElement.scrollWidth")
        run.expect(scroll <= width, f"{width} {name}: horizontal scroll ({scroll}px)")
        path = shots / f"{prefix}{width}-{name}.png"
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


def click_act(
    page: Page, run: Run, control: Locator, kind: str, *, scoped: bool = True
) -> Response | None:
    """Press one control: exactly one allowed POST that works, and the page changes.

    A control outside the seeded rows is never pressed (it could write for a real person).
    """
    if scoped:
        row = control.locator(ROW_OF).inner_text().lower()
        if not any(mark in row for mark in QA_MARKS):
            run.findings.append(
                f"click-path: refused to press a {kind} control outside the QA rows"
            )
            return None
    posts, seen = len(run.non_get), fingerprint(page)
    try:
        with page.expect_response(lambda r: r.request.method == "POST", timeout=TIMEOUT_MS) as hit:
            control.click()
    except PlaywrightTimeout:
        run.findings.append(f"click-path: a {kind} control sent no POST")
        return None
    response = hit.value
    settle(page)
    run.act_clicks += 1
    run.act_kinds.add(kind)
    path = urlparse(response.url).path
    sent = len(run.non_get) - posts
    run.expect(sent == 1, f"click-path: a {kind} press sent {sent} POSTs, want 1")
    run.expect(bool(ALLOWED_POST.match(path)), f"click-path: {kind} posted to {path}")
    run.expect(response.status == 200, f"click-path: {kind} answered {response.status}")
    run.expect(
        response.request.headers.get("x-saley-act") == "1", f"click-path: {kind} had no X-Saley-Act"
    )
    run.expect(fingerprint(page) != seen, f"click-path: a {kind} press changed nothing on the page")
    return response


def done_undo(page: Page, base: str, run: Run) -> None:
    """R2-B01: tick Done on the seeded reconnect task (Sid's list, as the QA user) -> Undo -> old state.

    The seeded research row is an `add_email` task (D154): it has no Done button, asserted here.
    D207: the page has no "View as" and no person tabs. `today_list_of` shows Vaishnavi's and
    then Sid's list with no UI (the server's mock-only `?user=`); every press is still signed
    by the QA user (`guard`), which the open-decision row of QA_USER below proves.
    """
    page.set_viewport_size({"width": 1440, "height": 900})
    with today_list_of(page, "vaishnavi"):
        page.goto(f"{base}/#today")
        page.reload()  # a hash-only move keeps the old page state; start clean
        settle(page)
        research = page.locator("#tasks li.task", has_text="QA Seed Research")
        run.expect(research.count() == 1, f"done_undo: {research.count()} research tasks, want 1")
        run.expect(
            research.locator('[data-act="done"]').count() == 0,
            "done_undo: the add_email task has a Done button",
        )
    with today_list_of(page, "sid"):
        page.reload()
        settle(page)
        tick_and_undo(page, base, run)


def tick_and_undo(page: Page, base: str, run: Run) -> None:
    """The Done -> toast -> Undo part of done_undo, on Sid's list (shown by the caller)."""
    task = page.locator("#tasks li.task", has_text="QA Seed Reconnect")
    if not run.expect(task.count() == 1, f"done_undo: {task.count()} seeded tasks, want 1"):
        return
    if click_act(page, run, task.locator('[data-act="done"]'), "done") is None:
        return
    toast = page.locator("#toast")
    shown = toast.inner_text() if toast.is_visible() else ""
    run.expect(bool(SAVED_TOAST.search(shown)), f"done_undo: toast reads {shown!r}")
    page.wait_for_timeout(1500)
    run.expect(toast.inner_text() != shown, "done_undo: the countdown does not move")
    run.expect(
        page.locator("#tasks li.task.is-done", has_text="QA Seed Reconnect").count() == 1,
        "done_undo: the task is not marked done",
    )
    live = api_items(page, base, f"/api/decisions?person={QA_USER}&undo=open&limit=50")
    ours = [d for d in live if d["kind"] == "task_done" and d["person"] == "QA Seed Reconnect"]
    run.expect(len(ours) == 1, f"done_undo: {len(ours)} open Done rows for the task, want 1")
    if click_act(page, run, page.locator("#toast-undo"), "undo", scoped=False) is None:
        return
    run.expect(toast.is_hidden(), "done_undo: the toast stays after Undo")
    task = page.locator("#tasks li.task", has_text="QA Seed Reconnect")
    back = task.locator('[data-act="done"]:not([disabled])')
    run.expect(task.count() == 1 and back.count() == 1, "done_undo: the task did not come back")
    run.expect(task.locator(".is-done").count() == 0, "done_undo: the old state is not back")
    undone = api_items(page, base, f"/api/decisions?person={QA_USER}&undo=undone&limit=50")
    gone = [d for d in undone if d["person"] == "QA Seed Reconnect"]
    run.expect(len(gone) == 1, "done_undo: the decision row is not marked undone")


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
    """R2-B02: Confirm a seeded decision; dismiss a seeded Inbox card, reload, the card is gone."""
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


def audit_controls(page: Page, run: Run, where: str) -> None:
    """Every [data-act] control on the page: a plain button, aimed at an allowed path."""
    for control in page.locator("[data-act]").all():
        kind = control.get_attribute("data-act") or "?"
        run.expect(
            control.get_attribute("type") == "button",
            f"click-path: {where}: {kind} is not a button",
        )
        endpoint = control.get_attribute("data-endpoint")
        if endpoint is None:
            run.expect(kind == "undo", f"click-path: {where}: {kind} control has no endpoint")
        else:
            run.expect(
                bool(ALLOWED_POST.match(endpoint)), f"click-path: {where}: {kind} -> {endpoint}"
            )
    forms = page.locator("form").count()
    submit = page.locator("button:not([type=button])").count()
    run.expect(forms == 0, f"click-path: {where}: {forms} forms on the page")
    run.expect(submit == 0, f"click-path: {where}: {submit} buttons that can submit")


def click_path(page: Page, base: str, run: Run) -> None:
    """Step 9 (R2-B03): the only non-GET calls are the allowed POSTs, one per control press."""
    page.goto(f"{base}/#asks")
    settle(page)
    cards = page.locator("#inbox-list .inbox-card")
    for text in ("qa:D2", "QA Seed Meeting A"):
        card = cards.filter(has_text=text)
        if run.expect(card.count() == 1, f"click-path: {card.count()} Inbox cards for {text!r}"):
            click_act(page, run, card.locator('[data-act="primary"]'), "primary")
    page.goto(f"{base}/#commit")
    settle(page)
    promise = page.locator("#commit-table tbody tr", has_text="qa: send the QA note")
    if run.expect(promise.count() == 1, f"click-path: {promise.count()} seeded promises, want 1"):
        click_act(page, run, promise.locator('[data-act="commitment-done"]'), "commitment-done")
        left = page.locator("#commit-table tbody tr", has_text="qa: send the QA note").count()
        run.expect(left == 0, "click-path: the finished promise stays in the open list")
    for view in ("today", "asks", "commit", "decisions"):
        page.goto(f"{base}/#{view}")
        settle(page)
        audit_controls(page, run, view)
    sent = len(run.non_get)
    run.expect(
        sent == run.act_clicks,
        f"click-path: {sent} non-GET requests but {run.act_clicks} control presses: "
        f"{run.non_get[:5]}",
    )
    stray = [x for x in run.non_get if not ALLOWED_POST.match(urlparse(x.split(" ", 1)[1]).path)]
    run.expect(not stray, f"click-path: non-GET requests to other paths {stray[:5]}")
    run.expect(
        not stray and all(x.startswith("POST ") for x in run.non_get), "click-path: not a POST"
    )
    missing = sorted(CONTROL_KINDS - run.act_kinds)
    run.expect(not missing, f"click-path: controls never pressed: {missing}")


# ---------------------------------------------------------------- R3: signals, events, suggestions

COMPANY = "QA Seed Co"


def sig_first_look(page: Page, base: str, run: Run, shots: Path) -> bool:
    """Signals · All on the first visit: both seeded stories are new, with every field."""
    fresh_page(page, base, "#signals")
    no_error_box(page, run, "signals")
    week = page.locator("#sig-window").inner_text()
    run.expect("Last 7 days" in week, f"signals_pages: window line reads {week!r}")
    rows = page.locator("#sig-list li", has_text="QA Seed:")
    if not run.expect(rows.count() == 2, f"signals_pages: {rows.count()} seeded stories, want 2"):
        return False
    link = page.locator("#sig-list li", has_text="funding round").locator(".it-title a")
    run.expect((link.get_attribute("href") or "").startswith("https://"), "signals_pages: link")
    run.expect("noopener" in (link.get_attribute("rel") or ""), "signals_pages: no noopener")
    meta = page.locator("#sig-list li", has_text="funding round").locator(".it-meta").inner_text()
    for word in ("Opportunity", "Importance 5", "Impact 2", "Ours", "QA Seed Wire"):
        run.expect(word in meta, f"signals_pages: {word!r} missing in the story line ({meta!r})")
    run.expect(len(run.seen_posts) >= 1, "signals_pages: the list sent no seen mark")
    snap(page, run, shots, "signals-all.png")
    return True


def sig_range_and_left_out(page: Page, run: Run) -> bool:
    """Range switch changes the window; the seen stories are left out; Show them brings them back."""
    before = page.locator("#sig-window").inner_text()
    page.locator("#sig-range button", has_text="24 hours").click()
    settle(page)
    day = page.locator("#sig-window").inner_text()
    run.expect("Last 24 hours" in day and day != before, f"signals_pages: range kept {day!r}")
    left = page.locator('[data-left="show"]')
    if not run.expect(left.count() == 1, "signals_pages: no 'left out' line for the seen stories"):
        return False
    match = LEFT_OUT.match(left.inner_text())
    run.expect(
        bool(match) and int(match.group(1)) >= 2, f"signals_pages: left out {left.inner_text()!r}"
    )
    left.click()
    settle(page)
    rows = page.locator("#sig-list li", has_text="QA Seed:")
    run.expect(rows.count() == 2, "signals_pages: Show them did not bring the seen stories back")
    run.expect(rows.locator(".pill", has_text="Seen").count() == 2, "signals_pages: no Seen pill")
    return True


def sig_sort_and_filters(page: Page, run: Run) -> None:
    rows = "#sig-list li"
    for sort, first in (
        ("newest", "quiet note"),
        ("rank", "funding round"),
        ("importance", "funding round"),
    ):
        page.select_option("#sig-sort", sort)
        a, b = order_of(page, rows, "funding round", "quiet note")
        ok = a >= 0 and b >= 0 and ((a < b) if first == "funding round" else (b < a))
        run.expect(ok, f"signals_pages: sort {sort} did not put {first!r} first")
    page.select_option("#sig-tag", "Opportunity")
    settle(page)
    run.expect(page.locator(rows, has_text="quiet note").count() == 0, "signals_pages: tag filter")
    run.expect(page.locator(rows, has_text="funding round").count() == 1, "signals_pages: tag lost")
    page.select_option("#sig-tag", "")
    page.fill("#sig-src", "QA Seed Wire")
    settle(page)
    run.expect(page.locator(rows, has_text="QA Seed:").count() == 2, "signals_pages: source filter")
    page.fill("#sig-src", "no such outlet qa")
    settle(page)
    run.expect(page.locator(rows, has_text="QA Seed:").count() == 0, "signals_pages: source miss")
    page.fill("#sig-src", "")
    page.select_option("#sig-cat", "Funding")  # D156: names, never letters
    settle(page)
    no_error_box(page, run, "signals category filter")
    page.select_option("#sig-cat", "")
    settle(page)


def sig_linked(page: Page, run: Run) -> None:
    page.locator("#sig-tabs button", has_text="Linked to us").click()
    settle(page)
    row = page.locator("#sig-list li", has_text=COMPANY)
    if not run.expect(
        row.count() >= 1, "signals_pages: the seeded signal is not on the Linked tab"
    ):
        return
    text = row.first.inner_text()
    run.expect("About: QA Seed Co" in text, f"signals_pages: linked entity missing ({text!r})")
    run.expect("Next step: Ask QA Seed Co" in text, "signals_pages: action hint missing")
    run.expect("Check the match" in text, "signals_pages: the one-match flag is missing")
    run.expect(page.locator("#sig-entity").is_visible(), "signals_pages: no entity filter")
    page.locator("#sig-tabs button", has_text="All news").click()
    settle(page)


def funnel_links(page: Page, base: str, run: Run) -> None:
    """Sources: each funnel number opens its list, and the list shows the same total."""
    for key in ("scored", "scored_4", "linked"):
        page.goto(f"{base}/#sources")
        settle(page)
        link = page.locator(f'#news-funnel a[data-news="{key}"]')
        if not run.expect(link.count() == 1, f"signals_pages: no funnel link for {key}"):
            continue
        wanted = int(link.inner_text())
        link.click()
        settle(page)
        view = page.evaluate("location.hash")
        window = page.locator("#sig-window").inner_text()
        run.expect(view.startswith("#signals"), f"signals_pages: {key} link opened {view!r}")
        run.expect(
            window.endswith(f"{wanted} stories" if wanted != 1 else "1 story"),
            f"signals_pages: {key} is {wanted}, list says {window!r}",
        )
    page.goto(f"{base}/#sources")
    settle(page)
    page.locator('#news-funnel a[data-news="deduped"]').click()
    settle(page)
    run.expect(
        "Stored as stories" in page.locator("#news-pick").inner_text(), "signals_pages: deduped"
    )
    conn = page.locator("#conn-list").inner_text()
    run.expect("Apollo: off" in conn, f"signals_pages: Apollo is not 'off' ({conn!r})")
    run.expect("SearXNG" in conn, "signals_pages: no search line")
    run.expect("notes" in conn.lower(), "signals_pages: no meeting notes line")
    no_error_box(page, run, "sources news block")


def events_page(page: Page, base: str, run: Run, shots: Path) -> None:
    page.goto(f"{base}/#events")
    settle(page)
    no_error_box(page, run, "events")
    run.expect(
        "From today on" in page.locator("#ev-window").inner_text(), "signals_pages: events window"
    )
    run.expect(page.locator("#ev-tabs button").count() == 2, "signals_pages: events tabs")
    page.locator("#ev-tabs button", has_text="Past").click()
    settle(page)
    run.expect(
        "Before today" in page.locator("#ev-window").inner_text(), "signals_pages: past window"
    )
    no_error_box(page, run, "events past")
    page.locator("#ev-tabs button", has_text="Upcoming").click()
    settle(page)
    snap(page, run, shots, "events.png")
    no_scroll(page, run, "events")


def add_to_pipeline(page: Page, base: str, run: Run, row: Locator) -> None:
    """The seeded hit: open the form, edit the name, Add -> exactly one POST -> toast -> Undo."""
    hit = row.locator("ul.hits > li", has_text="QA Seed Person")
    if not run.expect(hit.count() == 1, f"signals_pages: {hit.count()} seeded search hits, want 1"):
        return
    hit.locator('[data-open="add"]').click()
    form = hit.locator(".addform")
    run.expect(form.is_visible(), "signals_pages: the Add form did not open")
    name, role = form.locator("input").nth(0), form.locator("input").nth(1)
    run.expect(
        name.input_value() == "QA Seed Person", f"signals_pages: name {name.input_value()!r}"
    )
    run.expect(role.input_value() == "Head of Data", f"signals_pages: role {role.input_value()!r}")
    run.expect("QA Seed Co" in form.inner_text(), "signals_pages: the form lacks the company")
    name.fill("QA Seed Person")  # the person edits the name; it stays the made-up one
    add = form.locator('[data-act="add"]')
    if click_act(page, run, add, "add") is None:
        return
    toast = page.locator("#toast")
    shown = toast.inner_text() if toast.is_visible() else ""
    run.expect(bool(SAVED_TOAST.search(shown)), f"signals_pages: toast reads {shown!r}")
    run.expect(add.inner_text() == "Added", "signals_pages: the Add button does not say Added")
    live = api_items(page, base, "/api/decisions?undo=open&limit=50")
    run.expect(any(d["kind"] == "row_add" for d in live), "signals_pages: no open row_add decision")
    if click_act(page, run, page.locator("#toast-undo"), "undo", scoped=False) is None:
        return
    run.expect(toast.is_hidden(), "signals_pages: the toast stays after Undo")
    run.expect(
        add.inner_text() == "Add" and add.is_enabled(), "signals_pages: Add did not come back"
    )
    gone = api_items(page, base, "/api/decisions?undo=undone&limit=50")
    run.expect(
        any(d["kind"] == "row_add" for d in gone), "signals_pages: row add not marked undone"
    )


def newco_checks(page: Page, base: str, run: Run) -> None:
    """The New companies tab, on its first load (a second visit would hide the seen company)."""
    row = page.locator("#sg-list > li", has_text=COMPANY)
    if not run.expect(row.count() == 1, f"signals_pages: {row.count()} seeded companies, want 1"):
        return
    run.expect("Score" in row.inner_text(), "signals_pages: no score on the company")
    run.expect(row.locator(".parts .pill").count() >= 2, "signals_pages: no score parts")
    find = row.locator('[data-act="find"]')
    run.expect(find.count() == 1 and find.is_enabled(), "signals_pages: no enabled Find people")
    state = row.locator('[data-field="search"]').inner_text()
    run.expect(state == "Search is done", f"signals_pages: search state {state!r}")
    add_to_pipeline(page, base, run, row)


def suggest_pages(page: Page, base: str, run: Run, shots: Path) -> None:
    page.goto(f"{base}/#suggest")
    settle(page)
    tabs = (
        ("Warm intros", "warm"),
        ("Best contact", "best"),
        ("New companies", "newco"),
        ("Researchers", "res"),
    )
    for label, key in tabs:
        page.locator("#sg-tabs button", has_text=label).click()
        settle(page)
        no_error_box(page, run, f"suggest {key}")
        window = page.locator("#sg-window").inner_text()
        run.expect(window.startswith("Window: Sheet"), f"signals_pages: {key} window {window!r}")
        run.expect(
            page.locator("#sg-list > li").count() >= 1, f"signals_pages: {key} list is blank"
        )
        snap(page, run, shots, f"suggest-{key}.png")
        if key == "newco":
            newco_checks(page, base, run)
    audit_controls(page, run, "suggest")
    no_scroll(page, run, "suggest")


def today_sections(page: Page, base: str, run: Run, shots: Path) -> None:
    page.goto(f"{base}/#today")
    settle(page)
    no_error_box(page, run, "today")
    look = page.locator("#look-list li", has_text="QA Seed:")
    run.expect(look.count() >= 1, "signals_pages: Today 'needs a look' lacks the seeded story")
    replies = page.locator("#look-replies").inner_text()
    run.expect(replies.startswith("Replies:"), f"signals_pages: replies line reads {replies!r}")
    cards = page.locator("#sug-list > li")
    blank = cards.count() == 0 and not page.locator("#sug-empty").inner_text()
    run.expect(not blank, "signals_pages: Saley suggests is blank")
    for i in range(cards.count()):
        run.expect(
            cards.nth(i).locator("button").count() == 1, "signals_pages: want one button each"
        )
    follows = page.evaluate(
        "['tiles','tasks','look-card','blocked-card'].map(id => "
        "!!(document.getElementById(id).compareDocumentPosition(document.getElementById('sug-card')) & 4))"
    )
    run.expect(all(follows), f"signals_pages: Today sections are out of order {follows}")
    audit_controls(page, run, "today sections")
    snap(page, run, shots, "today-r3.png")
    no_scroll(page, run, "today")


def left_out_after_reload(page: Page, base: str, run: Run) -> None:
    """The Linked signal was shown on the first visit. After a reload it is left out: 1."""
    fresh_page(page, base, "#signals?tab=linked")
    page.fill("#sig-entity", COMPANY)
    settle(page)
    left = page.locator('[data-left="show"]')
    if not run.expect(left.count() == 1, "signals_pages: no 'left out' line after the reload"):
        return
    match = LEFT_OUT.match(left.inner_text())
    run.expect(bool(match) and match.group(1) == "1", f"signals_pages: reads {left.inner_text()!r}")
    run.expect(
        page.locator("#sig-list li", has_text=COMPANY).count() == 0, "signals_pages: seen row shown"
    )
    left.click()
    settle(page)
    shown = page.locator("#sig-list li", has_text=COMPANY)
    seen = shown.locator(".pill", has_text="Seen").count()
    run.expect(shown.count() == 1 and seen == 1, "signals_pages: Show them did not show the signal")


def signals_pages(page: Page, base: str, run: Run, shots: Path) -> None:
    """R3-B01: Signals, Events, Suggestions, Today sections, left out after reload, Add + Undo."""
    forget_seen()
    page.set_viewport_size({"width": WIDE, "height": 900})
    if sig_first_look(page, base, run, shots) and sig_range_and_left_out(page, run):
        sig_sort_and_filters(page, run)
        no_scroll(page, run, "signals")
    sig_linked(page, run)
    funnel_links(page, base, run)
    events_page(page, base, run, shots)
    suggest_pages(page, base, run, shots)
    today_sections(page, base, run, shots)
    left_out_after_reload(page, base, run)


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
