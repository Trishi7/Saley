"""The dashboard v2 build, served by this API (R5 T10).

Why a module of its own: app.py is near the 800-line ceiling, and the build has its own rules.
- D145: Vite builds `web/` into `src/saley/web/dist/` (gitignored). Its files are served under
  `/static/app/`. No new runtime service.
- D147 (strangler): `/` keeps the legacy static app until the cut-over ticket (T16). The new
  app is at `/next`. It needs a session, exactly like `/`.
- D146: `/next` puts a fresh nonce into the page's `<meta name="csp-nonce">` and into the CSP
  header (`style-src 'self' 'nonce-…'`). Radix style tags carry it; nothing else may add a
  style tag. script-src stays 'self'.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from saley.core.logging import get_logger
from saley.web import words as w
from saley.web.auth import session_user
from saley.web.guards import csp_with_style_nonce

DIST = Path(__file__).parent / "dist"
NEXT_PATH = "/next"
ASSET_URL = "/static/app"
NONCE_PLACEHOLDER = "__CSP_NONCE__"
STAMP_FILE = "build-stamp.json"
NOT_SERVED = frozenset({STAMP_FILE, "index.html"})
NONCE_BYTES = 16
# Vite puts a content hash in every file name under assets/, so a file never changes.
IMMUTABLE = "public, max-age=31536000, immutable"
log = get_logger("saley.web")


class DistFiles(StaticFiles):
    """StaticFiles that answers 404, not 500, while dist is missing (a laptop before the first
    build). Hashed assets get a long cache; the stamp and other files do not."""

    async def check_config(self) -> None:
        if self.directory is not None and Path(self.directory).is_dir():
            await super().check_config()

    async def get_response(self, path: str, scope: Any) -> Response:
        if self.directory is None or not Path(self.directory).is_dir():
            raise StarletteHTTPException(status_code=404)
        # The gate reads the stamp from disk; index.html only through /next (nonce, session).
        if path.replace("\\", "/").strip("/").lower() in NOT_SERVED:
            raise StarletteHTTPException(status_code=404)
        response = await super().get_response(path, scope)
        if response.status_code == 200 and path.replace("\\", "/").startswith("assets/"):
            response.headers["Cache-Control"] = IMMUTABLE
        return response


def page(nonce: str, dist: Path | None = None) -> str | None:
    """The built index.html with this request's nonce, or None when there is no build."""
    index = (dist or DIST) / "index.html"
    if not index.is_file():
        return None
    try:
        html = index.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Vite empties dist while it builds: answer the "not built" page, never a 500.
        log.warning("next_page_unreadable")
        return None
    if NONCE_PLACEHOLDER not in html:
        # The page still works; only library style tags lose their nonce. Say it in the log.
        log.warning("next_page_no_nonce_placeholder")
    return html.replace(NONCE_PLACEHOLDER, nonce)


def next_page(request: Request) -> Response:
    if session_user(request) is None:
        return RedirectResponse("/signin", status_code=303)
    nonce = secrets.token_urlsafe(NONCE_BYTES)
    html = page(nonce)
    if html is None:
        return PlainTextResponse(
            w.NEXT_NOT_BUILT, status_code=503, headers={"Cache-Control": "no-store"}
        )
    headers = {"Cache-Control": "no-store", "Content-Security-Policy": csp_with_style_nonce(nonce)}
    return HTMLResponse(html, headers=headers)


def install(app: FastAPI) -> None:
    """Add `/next` and the `/static/app` mount. Call before the legacy `/static` mount."""
    app.add_api_route(NEXT_PATH, next_page, methods=["GET"], include_in_schema=False)
    app.mount(ASSET_URL, DistFiles(directory=DIST, check_dir=False), name="static-app")
