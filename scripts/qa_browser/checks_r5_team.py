"""Browser checks of R5 T16a, the team pages on the new app (re-exported by `checks_r5.py`):

- commitment_owner_toast (R5-C03, D155): as the QA user, press "Mark done" on the seeded `qa:`
  promise of another person (Kushal). The server answers 403 with the owner line; the page
  shows it as a toast; nothing is written and the row stays open.
- run_news_now (R5-N09, D150, D178): on Sources and health (the gate signs in as the admin),
  the Button exists; one press is one POST /api/admin/news/run with X-Saley-Act; the page shows
  the queued state with the Button off, or the server's calm line (busy, cooldown). The press
  is signed by the throwaway admin `qaadmin` of the seed, never by the real admin. Its request
  rows are removed before and after the check. The gate's worker slot (`worker:gate`) never
  registers the job that runs a request (D151), so the gate fetches no news. The gate's server,
  this run and its seed and teardown all use the gate's test database (`saley_test`, D113:
  `saley.qa.gate.plan` passes DATABASE_URL to both and refuses a name without `_test`), so the
  human's dev worker on the dev database never sees a `qaadmin` request; on any other database
  (a manual run) the check deletes the request right after the POST. If a request is already
  open, the Button is off by design: the check checks that state and waives the press with a
  note that step 9 shows.
- click_path (R5-K01, and R2-B03 gate step 9 since the cut-over): Done and Undo on Today
  (unless `done_undo` already pressed them), Primary on the two seeded Inbox cards (R2), Add
  email (the drawer opens, nothing is saved), the owner's Commitment Done turning into Undo in
  place and back (D220: the R2 "a done promise leaves the open list at once" is now Undo in
  place), Run news now; then every `[data-act]` control on the team pages is a button aimed at
  an allowed POST, there is no form, every non-GET request was one press, and every R2 and R5
  control kind was pressed (confirm, dismiss and add by the R2/R3 checks before it).

Each check takes `entry`: "/" since the cut-over (T16b, D224).
The rules live in `r5_team_rules.py` (pure, tested); this file only reads the page.
"""

from __future__ import annotations

import contextlib
import os
import re
from urllib.parse import urlparse

from playwright.sync_api import Locator, Page, Request, Response, Route
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from qa_browser.core import (
    ALLOWED_POST,
    ASK_PATH,
    CONTROL_KINDS,
    QA_USER,
    SETTLE_MS,
    TIMEOUT_MS,
    WIDE,
    Run,
    click_act,
    settle,
    today_list_of,
)
from qa_browser.r5_rules import control_problems, view_url
from qa_browser.r5_team_rules import (
    COMMIT_DONE_PATH,
    NEWS_RUN_PATH,
    OPEN_REQUEST_NOTE,
    commit_undo_problems,
    drop_refusal_noise,
    missing_kinds,
    news_run_problems,
    open_request_problems,
    owner_line,
    owner_toast_problems,
    press_problems,
)

COMMIT_ROWS = "#commit-table tbody tr"
NEWS_RUN_API = re.compile(r"/api/admin/news/run(?:\?|$)")
TEAM_VIEWS = ("today", "asks", "commit", "decisions")
ROW_FACTS = """(row) => ({
  done_buttons: row.querySelectorAll('[data-act="commitment-done"]').length,
  undo_buttons: row.querySelectorAll('[data-act="undo"]').length,
  row_state: row.dataset.state || '',
  row_lines: row.querySelectorAll('[data-row-line]').length,
})"""
NEWS_FACTS = """() => {
  const card = document.querySelector('section.view.on #news-run');
  if (!card) return {state: null, disabled: false, line: '', line_bad: false};
  const button = card.querySelector('[data-act="run-news"]');
  const line = card.querySelector('[data-news-line]');
  return {
    state: (card.querySelector('[data-news-state]') || {dataset: {}}).dataset.newsState || null,
    disabled: !!button && button.disabled,
    line: line ? line.innerText : '',
    line_bad: !!(line && line.querySelector('[data-row-error]')),
  };
}"""
# The same facts as checks_r5.CONTROL_FACTS: every control on the shown page.
CONTROL_FACTS = """() => ({
  controls: [...document.querySelectorAll('section.view.on [data-act]')].map((n) => ({
    act: n.dataset.act, type: n.getAttribute('type'), endpoint: n.dataset.endpoint || ''})),
  forms: document.querySelectorAll('form').length,
  submits: document.querySelectorAll('section.view.on button:not([type=button])').length,
})"""


def open_view(page: Page, base: str, entry: str, view: str, query: str = "") -> None:
    """A fresh page on `view` (a hash-only move keeps the old page state)."""
    page.set_viewport_size({"width": WIDE, "height": 900})
    page.goto(view_url(base, entry, view) + (f"?{query}" if query else ""))
    page.reload()
    settle(page)


def api_json(page: Page, base: str, path: str, as_user: str = QA_USER) -> dict:
    response = page.request.get(f"{base}{path}", headers={"x-saley-as": as_user})
    return response.json() if response.ok else {}


def press(
    page: Page, run: Run, control: Locator, kind: str
) -> tuple[Response | None, dict[str, object]]:
    """Press one control and wait for its POST. The press counts for step 9 (one press, one
    POST). Returns the answer and the facts for `press_problems`."""
    posts = len(run.non_get)
    try:
        with page.expect_response(lambda r: r.request.method == "POST", timeout=TIMEOUT_MS) as hit:
            control.click()
    except PlaywrightTimeout:
        run.findings.append(f"click-path: a {kind} control sent no POST")
        return None, {}
    response = hit.value
    settle(page)
    run.act_clicks += 1
    run.act_kinds.add(kind)
    return response, {
        "sent": len(run.non_get) - posts,
        "path": urlparse(response.url).path,
        "header": response.request.headers.get("x-saley-act"),
        "status": response.status,
    }


def _commit_decisions(page: Page, base: str) -> int:
    """Commitment Done rows the QA user signed (the person filter of /api/decisions)."""
    items = api_json(page, base, f"/api/decisions?person={QA_USER}&limit=1000").get("items", [])
    return sum(1 for d in items if d.get("kind") == "commitment_done")


def commitment_owner_toast(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-C03: a non-owner Done -> 403 -> the owner toast; no write; the row stays open."""
    from saley.qa import seed

    open_view(page, base, entry, "commit", f"owner={seed.OTHER_OWNER}")
    listed = api_json(page, base, f"/api/commitments?owner={seed.OTHER_OWNER}&limit=1000")
    item = next(
        (c for c in listed.get("items", []) if c.get("text") == seed.OTHER_COMMITMENT_TEXT), None
    )
    if not run.expect(item is not None, "commitment_owner_toast: no seeded promise of another"):
        return
    row = page.locator(COMMIT_ROWS, has_text=seed.OTHER_COMMITMENT_TEXT)
    if not run.expect(row.count() == 1, f"commitment_owner_toast: {row.count()} rows, want 1"):
        return
    button = row.locator('[data-act="commitment-done"]')
    if not run.expect(button.count() == 1, "commitment_owner_toast: no Mark done Button"):
        return
    decisions_before = _commit_decisions(page, base)
    before = len(run.findings)
    response, facts = press(page, run, button, "commitment-refused")
    if response is None:
        return
    run.findings[before:] = drop_refusal_noise(run.findings[before:], 403, response.url)
    problems = press_problems(
        "commitment_owner_toast", {**facts, "want_path": COMMIT_DONE_PATH}, (403,)
    )
    run.expect(not problems, f"click-path: {problems}")
    expected = owner_line(str(item["owner_name"]))
    with contextlib.suppress(PlaywrightTimeout):  # owner_toast_problems names a missing toast
        page.locator("[data-toast-line]", has_text=expected).first.wait_for(timeout=TIMEOUT_MS)
    after = api_json(page, base, f"/api/commitments?owner={seed.OTHER_OWNER}&limit=1000")
    state = next((c["state"] for c in after.get("items", []) if c["id"] == item["id"]), None)
    row = page.locator(COMMIT_ROWS, has_text=seed.OTHER_COMMITMENT_TEXT)
    seen = {
        **(row.evaluate(ROW_FACTS) if row.count() == 1 else {}),
        "toasts": page.locator("[data-toast-line]").all_inner_texts(),
        "api_state": state,
        "decisions_before": decisions_before,
        "decisions_after": _commit_decisions(page, base),
    }
    problems = owner_toast_problems(seen, expected)
    run.expect(not problems, f"commitment_owner_toast: {problems}")
    run.notes["commitment_owner_toast"] = [f"403 -> toast {expected!r}; row open; no write"]


def on_test_db() -> bool:
    """True when this process uses the gate's test database (D113: its name ends in `_test`;
    `saley.qa.gate.plan` refuses any other and passes it to the server and to this run)."""
    from sqlalchemy.engine import make_url

    from saley.core.settings import get_settings

    return (make_url(get_settings().database_url).database or "").endswith("_test")


def clear_qa_news_requests() -> int:
    """Remove the requests the throwaway admin signed (so no cooldown, and nothing to run)."""
    from sqlalchemy import text

    from saley.core.db import session_scope
    from saley.qa import seed

    with session_scope() as session:
        result = session.execute(
            text(
                "delete from news_run_request where requested_by in "
                "(select id from users where short = :a)"
            ),
            {"a": seed.QA_ADMIN},
        )
        return result.rowcount or 0


def news_press(page: Page, base: str, run: Run, entry: str, where: str) -> str:
    """Open Sources and health as the admin, press Run news now once, check the page. The POST
    and the state GET go as the throwaway admin. Returns a note for the report."""
    from saley.qa import seed

    def as_qa_admin(route: Route, request: Request) -> None:
        route.continue_(headers={**request.headers, "x-saley-as": seed.QA_ADMIN})

    me = page.request.get(f"{base}/api/me")
    role = (me.json() if me.ok else {}).get("role")
    if not run.expect(role == "admin", f"{where}: the signed-in gate user is {role!r}, not admin"):
        return "not admin"
    cleared = clear_qa_news_requests()
    page.route(NEWS_RUN_API, as_qa_admin)
    try:
        open_view(page, base, entry, "sources")
        button = page.locator('section.view.on [data-act="run-news"]')
        if not run.expect(button.count() == 1, f"{where}: {button.count()} Run news Buttons"):
            return "no Button"
        aimed = button.get_attribute("type") == "button"
        aimed = aimed and button.get_attribute("data-endpoint") == NEWS_RUN_PATH
        run.expect(aimed, f"{where}: Run news now is not a button aimed at {NEWS_RUN_PATH}")
        if not button.is_enabled():
            # A request (anyone's) is already open: the Button is off by design. Check that
            # state; the press is waived with a note that reaches the step 9 note (never silent).
            problems = open_request_problems(page.evaluate(NEWS_FACTS))
            run.expect(not problems, f"{where}: {problems}")
            run.waived["run-news"] = OPEN_REQUEST_NOTE
            return OPEN_REQUEST_NOTE
        before = len(run.findings)
        response, facts = press(page, run, button, "run-news")
        if response is None:
            return "no POST"
        if not on_test_db():
            # Not the gate (a manual run on the dev database, where the main dev worker takes
            # requests every 30 s): close the QA request at once, so no news is fetched.
            settle(page)
            clear_qa_news_requests()
        status = response.status
        detail = "" if status == 200 else str((response.json() or {}).get("detail") or "")
        if status != 200:
            run.findings[before:] = drop_refusal_noise(run.findings[before:], status, response.url)
        problems = press_problems(where, {**facts, "want_path": NEWS_RUN_PATH}, (200, 409, 429))
        run.expect(not problems, f"click-path: {problems}")
        page.wait_for_timeout(SETTLE_MS)
        facts_now = page.evaluate(NEWS_FACTS)
        api_open = (
            api_json(page, base, NEWS_RUN_PATH, seed.QA_ADMIN).get("open")
            if on_test_db()
            else facts_now.get("state") in ("queued", "running")  # the request is closed already
        )
        seen = {**facts_now, "status": status, "detail": detail, "api_open": api_open}
        problems = news_run_problems(seen)
        run.expect(not problems, f"{where}: {problems}")
        return f"answer {status}, state {seen.get('state')}; {cleared} old QA requests removed"
    finally:
        page.unroute(NEWS_RUN_API, as_qa_admin)
        clear_qa_news_requests()


def run_news_now(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-N09: the admin's Run news now Button: one POST, then the queued state or the line."""
    note = news_press(page, base, run, entry, "run_news_now")
    slot = os.environ.get("WORKER_SLOT", "?")
    run.notes["run_news_now"] = [note, f"server worker slot {slot}: no news job runs there (D151)"]


def commitment_done_undo(page: Page, base: str, run: Run, entry: str) -> None:
    """The owner (the QA user) presses Mark done: the same place turns into Undo; Undo brings
    Mark done back. Both presses are signed by the QA user and reach their endpoint once."""
    from saley.qa import seed

    open_view(page, base, entry, "commit", f"owner={QA_USER}")
    row = page.locator(COMMIT_ROWS, has_text=seed.COMMITMENT_TEXT)
    if not run.expect(row.count() == 1, f"click-path: {row.count()} seeded QA promises, want 1"):
        return
    if click_act(page, run, row.locator('[data-act="commitment-done"]'), "commitment-done") is None:
        return
    row = page.locator(COMMIT_ROWS, has_text=seed.COMMITMENT_TEXT)
    problems = commit_undo_problems(row.evaluate(ROW_FACTS), "done")
    run.expect(not problems, f"click-path: commitment: {problems}")
    if click_act(page, run, row.locator('[data-act="undo"]'), "undo") is None:
        return
    row = page.locator(COMMIT_ROWS, has_text=seed.COMMITMENT_TEXT)
    problems = commit_undo_problems(row.evaluate(ROW_FACTS), "undo")
    run.expect(not problems, f"click-path: commitment: {problems}")


def add_email_no_save(page: Page, base: str, run: Run, entry: str) -> None:
    """Add email opens the drawer on the seeded research task and saves nothing (no POST)."""
    from qa_browser import checks_r5  # late: checks_r5 imports this module

    with today_list_of(page, "vaishnavi"):
        checks_r5.open_today(page, base, entry)
        row = checks_r5.find_task(page, "QA Seed Research")
        if not run.expect(row is not None, "click-path: no 'QA Seed Research' task"):
            return
        posts = len(run.non_get)
        row.locator("[data-add-email]").click()
        page.wait_for_timeout(SETTLE_MS)
        run.expect(page.locator(".drawer.on").count() == 1, "click-path: Add email opened nothing")
        page.keyboard.press("Escape")
        page.wait_for_timeout(SETTLE_MS)
        run.expect(len(run.non_get) == posts, "click-path: Add email sent a POST without Save")


def audit_views(page: Page, base: str, run: Run, entry: str, views: tuple[str, ...]) -> None:
    """Gate step 9 on each page: plain buttons aimed at an allowed POST; no form, no submit."""

    def allowed(path: str) -> bool:
        return path == ASK_PATH or bool(ALLOWED_POST.match(path))

    for view in views:
        open_view(page, base, entry, view)
        facts = page.evaluate(CONTROL_FACTS)
        problems = control_problems(view, facts["controls"], allowed)
        run.expect(not problems, f"click-path: {problems[:5]}")
        run.expect(facts["forms"] == 0, f"click-path: {view}: {facts['forms']} forms")
        run.expect(facts["submits"] == 0, f"click-path: {view}: {facts['submits']} submit buttons")


def inbox_primary(page: Page, base: str, run: Run, entry: str) -> None:
    """R2 click path: Primary (Do it / Confirm) on the two seeded Inbox cards, one POST each."""
    open_view(page, base, entry, "asks")
    cards = page.locator("#inbox-list .inbox-card")
    for text in ("qa:D2", "QA Seed Meeting A"):
        card = cards.filter(has_text=text)
        if run.expect(card.count() == 1, f"click-path: {card.count()} Inbox cards for {text!r}"):
            click_act(page, run, card.locator('[data-act="primary"]'), "primary")


def click_path(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-K01: Done, Undo, Add email, Commitment Done and Run news now on the new app, each one
    POST with X-Saley-Act to its endpoint (Add email: none), and the right end state."""
    from qa_browser import checks_r5  # late: checks_r5 imports this module

    if not {"done", "undo"} <= run.act_kinds:  # done_undo did not run or did not press
        with today_list_of(page, "sid"):
            checks_r5.done_undo_on_sid(page, base, run, entry)
    inbox_primary(page, base, run, entry)
    add_email_no_save(page, base, run, entry)
    commitment_done_undo(page, base, run, entry)
    news_press(page, base, run, entry, "click-path: run news")
    admin = (api_json(page, base, "/api/me") or {}).get("role") == "admin"
    audit_views(page, base, run, entry, (*TEAM_VIEWS, "sources") if admin else TEAM_VIEWS)
    sent = len(run.non_get)
    run.expect(
        sent == run.act_clicks,
        f"click-path: {sent} non-GET requests but {run.act_clicks} control presses: "
        f"{run.non_get[:5]}",
    )
    stray = [x for x in run.non_get if not ALLOWED_POST.match(urlparse(x.split(" ", 1)[1]).path)]
    run.expect(not stray, f"click-path: non-GET requests to other paths {stray[:5]}")
    missing = missing_kinds(run.act_kinds, run.waived)
    missing += sorted(CONTROL_KINDS - run.act_kinds - set(run.waived) - set(missing))
    run.expect(not missing, f"click-path: controls never pressed: {missing}")
    run.notes["click_path"] = [
        f"{run.act_clicks} presses: {', '.join(sorted(run.act_kinds))}",
        "Add email: drawer opened, no POST",
        *(f"{kind} not pressed: {why}" for kind, why in sorted(run.waived.items())),
    ]
