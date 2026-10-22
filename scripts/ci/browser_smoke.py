"""Browser smoke through caddy (R6-G02, D247): the compose stack as a person sees it.

    uv run python scripts/ci/browser_smoke.py https://localhost:8443

One Chromium run (Playwright) against caddy with its local certificate (https://localhost only):
- /api/me without a session is 401; / leads to /signin; the mock button signs in (SALEY_ENV=ci);
- caddy adds HSTS and hides its Server header; the app's own CSP (script-src 'self', a style
  nonce) reaches the browser unchanged;
- every rail view opens, shows no error box, and the run has zero console errors, zero page
  errors and no failed request.
The full browser QA (666 checks, three widths) stays at gate step 8 on the laptop: same code.
Exit 0 = pass. Findings are printed one per line.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qa_browser.core import MIN_RAIL_VIEWS, rail_names, settle

LOCAL = ("localhost", "127.0.0.1")
WIDTH = 1440
# Before sign-in /api/me is 401 on purpose; nothing else may fail.
EXPECTED_401 = "/api/me"


def headers_problems(headers: dict[str, str]) -> list[str]:
    problems = []
    if "max-age=" not in headers.get("strict-transport-security", ""):
        problems.append("caddy: no Strict-Transport-Security header")
    if "server" in headers:
        problems.append(f"caddy: Server header shown ({headers['server']})")
    csp = headers.get("content-security-policy", "")
    if "script-src 'self'" not in csp or "'nonce-" not in csp:
        problems.append(f"app CSP changed on the way: {csp[:120]!r}")
    if "unsafe-inline" in csp:
        problems.append("app CSP has unsafe-inline")
    return problems


def run(base: str) -> list[str]:
    found: list[str] = []
    signed_in = {"yes": False}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            ignore_https_errors=True, viewport={"width": WIDTH, "height": 900}
        )
        page = context.new_page()
        page.on("console", lambda m: m.type == "error" and found.append(f"console: {m.text[:200]}"))
        page.on("pageerror", lambda e: found.append(f"page error: {str(e)[:200]}"))

        def on_response(response) -> None:
            path = urlparse(response.url).path
            if response.status < 400:
                return
            if response.status == 401 and path == EXPECTED_401 and not signed_in["yes"]:
                return
            found.append(f"request: {response.status} {path}")

        page.on("response", on_response)
        page.on("requestfailed", lambda r: found.append(f"request failed: {urlparse(r.url).path}"))

        anon = page.request.get(f"{base}/api/me")
        if anon.status != 401:
            found.append(f"/api/me without a session gave {anon.status}, want 401")
        first = page.goto(f"{base}/")
        if urlparse(page.url).path != "/signin":
            found.append(f"/ did not lead to /signin: {page.url}")
        button = page.get_by_role("button", name="Sign in as", exact=False)
        if button.count() != 1:
            found.append("the sign-in page shows no mock button (SALEY_ENV=ci, AUTH_PROVIDER=mock)")
            browser.close()
            return found
        button.click()
        page.wait_for_url(lambda url: urlparse(url).path == "/")
        signed_in["yes"] = True
        home = page.goto(f"{base}/#today")
        found += headers_problems(dict(home.headers) if home else {})
        if first is None:
            found.append("no answer for /")
        settle(page)
        names = rail_names(page)
        if len(names) < MIN_RAIL_VIEWS:
            found.append(f"only {len(names)} views in the rail")
        for name in names:
            page.goto(f"{base}/#{name}")
            settle(page)
            if page.locator(f"#v-{name}.on").count() != 1:
                found.append(f"{name}: view not shown")
            errors = page.locator(".view.on .err").all_inner_texts()
            if errors:
                found.append(f"{name}: error box {errors[0][:120]!r}")
        print(f"views opened: {len(names)}")
        browser.close()
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: browser_smoke.py https://localhost:8443", file=sys.stderr)
        return 2
    base = argv[0].rstrip("/")
    if urlparse(base).hostname not in LOCAL:
        print("Refused: the browser smoke runs against https://localhost only", file=sys.stderr)
        return 2
    found = run(base)
    for line in found:
        print(f"FINDING {line}")
    print("browser smoke PASS" if not found else f"browser smoke FAIL ({len(found)} findings)")
    return 0 if not found else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
