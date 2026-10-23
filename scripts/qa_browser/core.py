"""Browser QA core: the run record, the request guard and watcher, and shared page helpers.

Why a package: one file held 1100 lines. Core has no check (only the one-press helper
`click_act` that every group uses). The checks of R1-R4 and R5 live in `checks_r1_r4.py` and
`checks_r5*.py`; `scripts/browser_qa.py` is the CLI. Since the cut-over (T16b, D224) every check
runs on the one app at `/`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, Request, Response, Route
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from qa_browser.r5_rules import with_query

ROOT = Path(__file__).resolve().parents[2]
WIDTHS = (1440, 1024, 390)
LOCAL_HOSTS = {"localhost", "127.0.0.1"}
SETTLE_MS = 600
DRAWERS = 3
TIMEOUT_MS = 15000
# The POST paths a control may call (six, since the card edit of D129) (gate step 9; scripts/verify.py keeps the same list).
ALLOWED_POST = re.compile(
    r"^/api/(act|undo/\d+|decision/\d+/confirm|proposal/\d+/(dismiss|edit)|commitment/\d+/done|admin/news/run)$"
)
SEEN_PATH = "/api/seen"
SIGN_IN_PATH = "/auth/mock"  # R4, D116: the gate signs in first; it is set-up, not a control
ASK_PATH = "/api/ask"  # R4, D131: the Ask card sends a typed question; no [data-act] control
QA_USER = "qa"  # saley.qa.seed.QA_USER: every API call of this run is made as this person
QA_MARKS = ("qa seed", "qa:")  # a control is pressed only inside a row that shows one of these
CONTROL_KINDS = {"done", "undo", "confirm", "primary", "dismiss", "commitment-done", "add"}
NARROW = 390
WIDE = 1440
MIN_RAIL_VIEWS = 10  # docs/08: 13 views for a team member, 14 for the admin
ROW_OF = "xpath=ancestor::*[self::li or self::tr or contains(@class,'inbox-card')][1]"
TODAY_API = re.compile(r"/api/today(?:\?|$)")


class Run:
    def __init__(self) -> None:
        self.findings: list[str] = []
        self.non_get: list[str] = []  # POSTs of controls only (step 9)
        self.sign_ins: list[str] = []  # the mock sign-in POSTs, kept apart from controls
        self.seen_posts: list[str] = []  # POST /api/seen after a list render (D75)
        self.ask_posts: list[str] = []  # POST /api/ask from the Ask card (R4 ticket 6)
        self.blocked: list[str] = []
        self.checks = 0
        self.screens: list[str] = []
        self.named: dict[str, bool] = {}  # check name -> passed (matrix rows, tool browser)
        self.act_clicks = 0  # presses of a [data-act] control; each must send exactly one POST
        self.act_kinds: set[str] = set()
        self.notes: dict[str, list[str]] = {}  # check name -> what it verified (R5 T12)
        # R5 T16a review: a press a check could not make by design (kind -> why). Step 9 names
        # it in its note and does not demand that press; it never passes silently.
        self.waived: dict[str, str] = {}

    def group(self, name: str, fn: Callable[[], None]) -> None:
        """Run one named check: it passes when it adds no finding AND at least one check ran.

        A group that ran no check proves nothing (a skipped body looks like a pass), so it fails.
        """
        before, ran = len(self.findings), self.checks
        try:
            fn()
        except PlaywrightError as exc:
            # R6 gate: a page script that threw ended the whole run with no JSON and only a
            # stack tail, so nobody knew which check. Now it is a finding of THIS check (the
            # verdict is BLOCK) and the other checks still run.
            first = (str(exc).strip().splitlines() or ["?"])[0][:200]
            self.checks += 1
            self.findings.append(f"{name}: raised {type(exc).__name__}: {first}")
        if self.checks == ran:
            self.findings.append(f"{name}: no check ran")
        self.named[name] = len(self.findings) == before

    def expect(self, ok: object, finding: str) -> bool:
        self.checks += 1
        if not ok:
            self.findings.append(finding)
        return bool(ok)


def guard(run: Run) -> Callable[[Route, Request], None]:
    """Route handler: localhost only. Anything else is aborted and recorded."""

    def handle(route: Route, request: Request) -> None:
        url = urlparse(request.url)
        if url.hostname in LOCAL_HOSTS and url.path.startswith("/api/"):
            # View as the QA user whatever the page state is (it resets on every reload).
            route.continue_(headers={**request.headers, "x-saley-as": QA_USER})
            return
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
        if request.method == "GET":
            return
        entry = f"{request.method} {request.url}"
        if request.method == "POST" and urlparse(request.url).path == SEEN_PATH:
            run.seen_posts.append(entry)
            if request.headers.get("x-saley-act") != "1":
                run.findings.append("seen: a mark had no X-Saley-Act")
            return
        if request.method == "POST" and urlparse(request.url).path == SIGN_IN_PATH:
            run.sign_ins.append(entry)
            if run.act_clicks > 0:  # R5 D162: the gate signs in once, before the first press
                run.findings.append("sign-in after a control press")
            return
        if request.method == "POST" and urlparse(request.url).path == ASK_PATH:
            run.ask_posts.append(entry)
            if request.headers.get("x-saley-act") != "1":
                run.findings.append("ask: a question had no X-Saley-Act")
            return
        run.non_get.append(entry)

    page.on("request", on_request)


def sign_in_page(page: Page, base: str, run: Run) -> None:
    """R4-B01: the page shows one button, /api without the cookie is 401, the button signs in.

    Runs first in every browser process: each process is a new browser with no cookie, and every
    later check needs the session this click makes (D114). The gate's web runs the mock provider.
    """
    anon = page.request.get(f"{base}/api/me")
    run.expect(anon.status == 401, f"sign_in_page: /api/me without a session gave {anon.status}")
    page.goto(f"{base}/")
    run.expect(
        urlparse(page.url).path == "/signin", f"sign_in_page: / did not lead to /signin: {page.url}"
    )
    button = page.get_by_role("button", name=re.compile(r"^Sign in as "))
    if not run.expect(button.count() == 1, "sign_in_page: the page shows no sign-in button"):
        return
    button.click()
    page.wait_for_url(lambda url: urlparse(url).path == "/")
    page.wait_for_selector("#me:not(:empty)")  # not wait_for_function: the CSP blocks its eval
    me = page.request.get(f"{base}/api/me")
    run.expect(me.status == 200, f"sign_in_page: /api/me after sign-in gave {me.status}")


def rail_names(page: Page) -> list[str]:
    """The views of the left rail. At phone width the R5 app folds the rail into a menu
    (D198): then the menu Button opens it, the names are read, and Escape closes it again.
    The legacy page shows its rail at every width, so it never opens a menu."""
    script = "as => as.map(a => a.dataset.v)"
    names = page.locator(".rail a[data-v]:visible").evaluate_all(script)
    menu = page.locator("[data-menu]")
    if len(names) < MIN_RAIL_VIEWS and menu.count() and menu.first.is_visible():
        menu.first.click()
        page.wait_for_timeout(SETTLE_MS)
        names = page.locator(".rail a[data-v]:visible").evaluate_all(script)
        page.keyboard.press("Escape")
        page.wait_for_timeout(SETTLE_MS)
    return list(dict.fromkeys(names))


def settle(page: Page) -> None:
    page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
    page.wait_for_timeout(SETTLE_MS)


def no_error_box(page: Page, run: Run, where: str) -> None:
    errors = page.locator(".view.on .err, .drawer.on .err").all_inner_texts()
    run.expect(not errors, f"{where}: error shown: {errors}")


@contextmanager
def today_list_of(page: Page, person: str) -> Iterator[None]:
    """Show `person`'s task list on Today while the block runs, with no UI (D207).

    The page has no person picker: it asks /api/today for the signed-in person. This route adds
    `?user=<person>` to that call. The server keeps `?user=` only with the mock provider (D206,
    saley.web.today), which is the gate's mode. The signer stays the QA user (`guard`):
    an X-Saley-As of a real person would sign the press and mark items seen for that person
    (A11), so the check would no longer run as `qa`.
    """

    def handle(route: Route, request: Request) -> None:
        url = with_query(request.url, "user", person)
        route.continue_(url=url, headers={**request.headers, "x-saley-as": QA_USER})

    page.route(TODAY_API, handle)
    try:
        yield
    finally:
        page.unroute(TODAY_API, handle)


# ---------------------------------------------------------------- R2: controls that change state


def fingerprint(page: Page) -> str:
    """What a person sees: the main text, the toast flag and the done rows."""
    return page.evaluate(
        "document.querySelector('main').innerText + '|' + document.getElementById('toast').hidden"
        " + '|' + document.querySelectorAll('.is-done').length"
    )


def api_items(page: Page, base: str, path: str) -> list[dict]:
    """A GET through the browser's own request context (same localhost, no route needed)."""
    response = page.request.get(f"{base}{path}")
    return response.json().get("items", []) if response.ok else []


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


def hide_other_news(run: Run, where: str) -> bool:
    """Only the seeded stories and signal are new for the QA user (seed.hide_other_news, D226).

    The seed refuses any database that is not a `_test` one (RuntimeError). That is a finding
    (the run BLOCKs with its JSON line), never a crash (T16b review L3). True when it ran.
    """
    from saley.core.db import session_scope
    from saley.qa import seed

    try:
        with session_scope() as session:
            seed.hide_other_news(session)
    except RuntimeError as err:
        run.findings.append(f"{where}: the seen set-up refused: {err}")
        return False
    return True


def forget_seen() -> None:
    """Start from a list where every seeded item is new (the views check has opened the pages)."""
    from saley.core.db import session_scope
    from saley.qa import seed

    with session_scope() as session:
        seed.reset_seen(session)


def fresh_page(page: Page, base: str, hash_: str) -> None:
    """A new page state (the JS state is gone: no View as person, no filters), then the hash."""
    page.goto(f"{base}/#today")
    page.reload()
    settle(page)
    page.goto(f"{base}/{hash_}")
    settle(page)


def no_scroll(page: Page, run: Run, where: str) -> None:
    """At 390 px the page must not scroll sideways; then back to the wide size."""
    page.set_viewport_size({"width": NARROW, "height": 900})
    page.wait_for_timeout(SETTLE_MS)
    scroll = page.evaluate("document.documentElement.scrollWidth")
    run.expect(
        scroll <= NARROW, f"signals_pages: {where}: horizontal scroll at {NARROW}px ({scroll})"
    )
    page.set_viewport_size({"width": WIDE, "height": 900})


def order_of(page: Page, selector: str, *needles: str) -> list[int]:
    """The position of the first row that holds each needle (-1 when it is missing)."""
    texts = page.locator(selector).all_inner_texts()
    return [next((i for i, t in enumerate(texts) if n in t), -1) for n in needles]


def snap(page: Page, run: Run, shots: Path, name: str) -> None:
    page.screenshot(path=str(shots / name), full_page=True)
    run.screens.append(name)


@contextmanager
def seeded(run: Run) -> Iterator[bool]:
    """Seed the QA rows, and remove them again in `finally` (also when a check fails)."""
    from saley.core.db import session_scope
    from saley.qa import seed

    try:
        with session_scope() as session:
            seed.seed(session)
    except Exception as exc:  # a missing seed is a BLOCK with the reason, not a crash
        run.findings.append(f"seed failed: {type(exc).__name__}: {exc}")
        yield False
        return
    try:
        yield True
    finally:
        try:
            with session_scope() as session:
                seed.teardown(session)
        except Exception as exc:
            run.findings.append(f"teardown failed, run scripts/qa_seed.py teardown: {exc}")


def real_seen_marks(since: str) -> int:
    """Seen marks that people other than the QA user got since `since` (must stay 0, A11)."""
    from sqlalchemy import text

    from saley.core.db import session_scope

    with session_scope() as session:
        return int(
            session.execute(
                text(
                    "select count(*) from user_seen s join users u on u.id = s.user_id "
                    "where u.short <> :qa and s.seen_at >= cast(:since as timestamptz)"
                ),
                {"qa": QA_USER, "since": since},
            ).scalar_one()
        )


def db_now() -> str:
    from sqlalchemy import text

    from saley.core.db import session_scope

    with session_scope() as session:
        return str(session.execute(text("select now()")).scalar_one())


def parse_widths(raw: str) -> tuple[int, ...]:
    """'1440,390' -> (1440, 390). Only the widths of docs/08; anything else gives ()."""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts or not all(p.isdigit() and int(p) in WIDTHS for p in parts):
        return ()
    return tuple(int(p) for p in parts)
