"""The dashboard: the built app, served at `/` by this API (R5 T10, cut-over T16b).

Why a module of its own: app.py is near the 800-line ceiling, and the build has its own rules.
The name stays from the strangler (D147); `/next` is gone since the cut-over (D224).
- D145: Vite builds `web/` into `src/saley/web/dist/` (gitignored). Its files are served under
  `/static/app/`. No new runtime service.
- D224 (T16b): `/` serves the built page and needs a session (signed out -> /signin). The legacy
  static app and `/next` are removed; `/static/signin.css` is the one file left for the sign-in
  and message pages (auth.py), served by one route, so no other file under `static/` is served.
- D146: `/` puts a fresh nonce into the page's `<meta name="csp-nonce">` and into the CSP
  header (`style-src 'self' 'nonce-…'`). Radix style tags carry it; nothing else may add a
  style tag. script-src stays 'self'.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from saley.core.logging import get_logger
from saley.web import words as w
from saley.web.auth import session_user
from saley.web.guards import csp_with_style_nonce

DIST = Path(__file__).parent / "dist"
APP_PATH = "/"
SIGNIN_CSS = Path(__file__).parent / "static" / "signin.css"
SIGNIN_CSS_URL = "/static/signin.css"
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
        # The gate reads the stamp from disk; index.html only through / (nonce, session).
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
        log.warning("app_page_unreadable")
        return None
    if NONCE_PLACEHOLDER not in html:
        # The page still works; only library style tags lose their nonce. Say it in the log.
        log.warning("app_page_no_nonce_placeholder")
    return html.replace(NONCE_PLACEHOLDER, nonce)


def app_page(request: Request) -> Response:
    if session_user(request) is None:
        return RedirectResponse("/signin", status_code=303)
    nonce = secrets.token_urlsafe(NONCE_BYTES)
    html = page(nonce)
    if html is None:
        return PlainTextResponse(
            w.APP_NOT_BUILT, status_code=503, headers={"Cache-Control": "no-store"}
        )
    headers = {"Cache-Control": "no-store", "Content-Security-Policy": csp_with_style_nonce(nonce)}
    return HTMLResponse(html, headers=headers)


def signin_css() -> FileResponse:
    """The sign-in pages' stylesheet. One file by name: nothing else under `static/` is served."""
    return FileResponse(SIGNIN_CSS, media_type="text/css", headers={"Cache-Control": "no-cache"})


def install(app: FastAPI) -> None:
    """Add `/` (the app page), `/static/signin.css` and the `/static/app` mount (D145, D224)."""
    app.add_api_route(APP_PATH, app_page, methods=["GET"], include_in_schema=False)
    app.add_api_route(SIGNIN_CSS_URL, signin_css, methods=["GET"], include_in_schema=False)
    app.mount(ASSET_URL, DistFiles(directory=DIST, check_dir=False), name="static-app")
