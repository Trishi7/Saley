"""Request guards for every route: security headers, the same-origin rule, the dev Host-port
check and the body limit.

Why a module of its own: app.py, act.py and auth.py all need them, and app.py includes the
other two routers. Here they import without a cycle.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

from saley.core.logging import get_logger
from saley.core.settings import get_settings
from saley.web import words as w

log = get_logger("saley.web")

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'none'"
)
# The sign-in pages post a form to this origin, so they need form-action 'self'.
CSP_FORMS = CSP.replace("form-action 'none'", "form-action 'self'")
# Pages whose handler sets its own CSP with a per-request style nonce (D146; `/` since D224).
NONCE_PAGES = frozenset({"/"})
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def csp_with_style_nonce(nonce: str) -> str:
    """The R4 CSP plus one style nonce (D146).

    Why: Radix (react-remove-scroll) inserts a <style> tag. A strict style-src 'self' blocks
    it and logs a console error. The nonce allows only tags that carry this request's value.
    script-src stays 'self': no inline script, no unsafe-inline, no unsafe-eval.
    """
    if not NONCE_RE.match(nonce):
        raise ValueError("bad nonce")  # only secrets.token_urlsafe output reaches a header
    return CSP.replace("style-src 'self'", f"style-src 'self' 'nonce-{nonce}'")


def csp_for(path: str, current: str | None) -> str:
    """The CSP the header middleware sets on a non-API answer.

    A nonce page keeps the CSP its handler set (it holds the nonce of this request). Every
    other page gets the fixed policy, whatever a handler set.
    """
    if path in NONCE_PAGES and current and "'nonce-" in current:
        return current
    pages = path == "/signin" or path.startswith("/auth/")
    return CSP_FORMS if pages else CSP


def _security_headers(response: Response, csp: str = CSP) -> Response:
    response.headers["Content-Security-Policy"] = csp
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return response


def _cross_site(request: Request) -> bool:
    """Only same-origin pages and direct visits may call the API. A broken Origin is refused."""
    if request.headers.get("sec-fetch-site") in ("cross-site", "same-site"):
        return True
    origin = request.headers.get("origin")
    if not origin:
        return False
    try:
        return urlsplit(origin).netloc != request.headers.get("host")
    except ValueError:
        return True


MAX_BODY_BYTES = 16 * 1024  # every POST body of this API is a few hundred bytes (M2)
DEFAULT_HTTP_PORT = 80


def _bad_host(request: Request) -> bool:
    """Dev only (M7): the Host header must name the port this server listens on.

    TrustedHostMiddleware checks the name; it ignores the port. A page on another local port
    that rebinds a name to 127.0.0.1 would pass it, so the port is checked here too.
    """
    server = request.scope.get("server")
    if not get_settings().is_dev or not server:
        return False
    _, _, port = request.headers.get("host", "").rpartition(":")
    asked = int(port) if port.isdigit() else DEFAULT_HTTP_PORT
    return asked != server[1]


class BodyLimit:
    """Refuse a request body over MAX_BODY_BYTES on /api/ before it is parsed (M2).

    A Content-Length over the limit is refused at once. A body without one (chunked) is counted
    while it is read; the HTTPException raised from `receive` reaches the normal error handler.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in ("POST", "PUT", "PATCH", "DELETE")
            or not scope["path"].startswith("/api/")
        ):
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
            refusal = JSONResponse(status_code=413, content={"detail": w.ERR_TOO_BIG})
            await refusal(scope, receive, send)
            return
        seen = 0

        async def counted() -> Any:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > MAX_BODY_BYTES:
                    raise HTTPException(status_code=413, detail=w.ERR_TOO_BIG)
            return message

        await self.app(scope, counted, send)


def _query_names(dependant: Any) -> set[str]:
    """Every query parameter name a route accepts, its sub-dependencies included."""
    names = {p.alias for p in dependant.query_params}
    for sub in dependant.dependencies:
        names |= _query_names(sub)
    return names


def reject_unknown_params(request: Request) -> None:
    """Only the query parameters a route declares are allowed (R1.1 C)."""
    route = request.scope.get("route")
    if route is None or not hasattr(route, "dependant"):
        return
    if request.url.path == "/auth/callback":
        return  # Google adds scope, authuser, prompt and more to the callback URL
    allowed = _query_names(route.dependant)
    for key in request.query_params:
        if key not in allowed:
            # Fixed text (A4): the caller typed the key, so it goes to the log, not the answer.
            log.warning("unknown_param", path=request.url.path, param=key[:40])
            raise HTTPException(status_code=422, detail=w.ERR_PARAM)
