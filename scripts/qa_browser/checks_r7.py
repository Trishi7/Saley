"""R7 browser check `deep_link_card` (R7-B01, docs/09 §7, D261).

A Discord card links to PUBLIC_URL/<page>/<id>?card=<card_id>. With no session the link must go
to sign-in, keep itself as `next`, and after the press land on the page with the item open and
outlined. A `next` for another site must be refused. The check uses its own browser context
(no cookie), so the gate's signed-in page is never signed out; that second sign-in is set-up,
not a control press, so it is not counted with the presses (D162).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from playwright.sync_api import Page

from qa_browser.core import TIMEOUT_MS, Run, guard

CARD = "qa-deep-link"
OUTLINE_WAIT_MS = 5000


def _first_person(page: Page, base: str) -> int | None:
    """A contact id from the gate's own (seeded) database, read with the signed-in page."""
    found = page.request.get(f"{base}/api/people?limit=1")
    items = found.json().get("items") if found.ok else None
    return int(items[0]["id"]) if items else None


def deep_link_card(page: Page, base: str, run: Run) -> None:
    person = _first_person(page, base)
    if not run.expect(person is not None, "deep_link_card: no person to link to"):
        return
    context = page.context.browser.new_context()  # type: ignore[union-attr]
    try:
        fresh = context.new_page()
        fresh.set_default_timeout(TIMEOUT_MS)
        fresh.route("**/*", guard(run))
        fresh.on(
            "console", lambda m: m.type == "error" and run.findings.append(f"console: {m.text}")
        )
        fresh.on("pageerror", lambda e: run.findings.append(f"page error: {e}"))
        link = f"/people/{person}?card={CARD}"
        fresh.goto(base + link)
        url = urlparse(fresh.url)
        run.expect(url.path == "/signin", f"deep_link_card: no sign-in first: {fresh.url}")
        run.expect("next=" in url.query, f"deep_link_card: sign-in lost the link: {fresh.url}")
        fresh.get_by_role("button", name=re.compile(r"^Sign in as ")).click()
        fresh.wait_for_url(lambda u: urlparse(u).fragment.startswith("people"))
        landed = urlparse(fresh.url).fragment
        run.expect(
            landed == f"people?open={person}&card={CARD}",
            f"deep_link_card: landed on #{landed}",
        )
        fresh.wait_for_selector('[data-drawer="person"].on')
        outlined = fresh.wait_for_selector("[data-card-focus]", timeout=OUTLINE_WAIT_MS)
        run.expect(outlined is not None, "deep_link_card: the item was not outlined")
        # A foreign `next` is dropped: the page does not carry it and the press lands at home.
        context.clear_cookies()
        fresh.goto(f"{base}/signin?next=https://evil.example/")
        run.expect("evil" not in fresh.content(), "deep_link_card: a foreign next was kept")
        fresh.get_by_role("button", name=re.compile(r"^Sign in as ")).click()
        fresh.wait_for_url(lambda u: urlparse(u).hostname == urlparse(base).hostname)
        run.expect(
            urlparse(fresh.url).hostname == urlparse(base).hostname,
            f"deep_link_card: a foreign next was followed: {fresh.url}",
        )
    finally:
        context.close()
