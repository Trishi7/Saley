"""Sign-in: Google or mock, the allowlist, server-side sessions (R4, D114-D116, D50).

Why: since R4 every /api route needs a session in every mode. The loopback dev bypass is gone.
The cookie `saley_session` holds a random token; the database keeps only its sha256 (the web
role has no table grant, so `saley_session_start` and `saley_session_end` do the writes).

Providers (D116). `AUTH_PROVIDER=auto` means google when the client id, the secret and
`WEB_ALLOWED_EMAILS` are all set, else mock. Mock signs in as DEV_USER_EMAIL and works only
with SALEY_ENV=dev from this machine; anywhere else it is 404. `X-Saley-As` (D50) works only
with mock.

Allowlist (D115). `WEB_ALLOWED_EMAILS` is `short=email,...`. The entry names the user, so Saley
never guesses a short from an address (D35). A bad shape stops the app at start; at run time
it fails closed (nobody is allowed). Google sessions are checked against the list on EVERY
request, so removing an address ends its session at the next click.

Google flow. Code flow with `state` and PKCE (S256). The state and the PKCE verifier wait in a
short cookie (`saley_oauth`, HttpOnly, SameSite=Lax, 10 minutes, Path=/auth); the callback
compares the state in the URL with the cookie and clears the cookie, so a callback runs once.
The server talks to Google only at the token endpoint, through `saley.core.egress`.

Why the id_token signature is not checked: OIDC Core 3.1.3.7 lets a client skip it when the
token comes straight from the token endpoint over TLS, as here. We still check iss, aud, exp and
email_verified. No new dependency: stdlib base64, json, hashlib, secrets.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from saley.core import egress
from saley.core.db import ro_engine
from saley.core.logging import get_logger
from saley.core.settings import Settings, get_settings, is_missing
from saley.web import words as w
from saley.web.guards import _cross_site

log = get_logger("saley.web.auth")

SESSION_COOKIE = "saley_session"
OAUTH_COOKIE = "saley_oauth"
OAUTH_COOKIE_SECONDS = 600
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - a URL, not a secret
ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})
TOKEN_MAX_BYTES = 64 * 1024
FORM_MAX_BYTES = 2048
MAX_TOKEN_CHARS = 200  # a session token is 43 characters; anything longer is not ours
AUTH_RATE_LIMIT = 10  # per client IP per minute, on /auth/*
AUTH_RATE_WINDOW = 60.0
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})
SHORT_RE = re.compile(r"[a-z]{1,20}")  # fullmatch only: "$" would accept a trailing newline
ALLOW_EMAIL_RE = re.compile(r"[^@\s<>\"'=,;]{1,100}@[^@\s<>\"'=,;]{1,100}\.[^@\s<>\"'=,;]{1,50}")
PAGE_CSS = "/static/app.css"


@dataclass(frozen=True)
class SignedIn:
    email: str
    role: str
    short: str
    provider: str


class BadAllowlist(ValueError):
    """WEB_ALLOWED_EMAILS is not `short=email,...`. The message never carries a value."""


class ClaimsError(ValueError):
    """The id_token from Google failed a check."""


class UpstreamError(RuntimeError):
    """Google did not answer the token request."""


class TokenRefused(ClaimsError):
    """The token endpoint answered with an OAuth error (Q-N5). Carries Google's `error` and
    `error_description` (RFC 6749 5.2): words from Google, never the code or the secret."""

    def __init__(self, error: str, description: str) -> None:
        super().__init__("token endpoint refused the code")
        self.error = error
        self.description = description


GOOGLE_ERROR_MAX = 60
GOOGLE_DESCRIPTION_MAX = 200


# ------------------------------------------------------------------ settings helpers


def parse_allowed_emails(raw: str) -> dict[str, str]:
    """`short=email,...` becomes {email (lower case): short}. Strict: no guessing (D35, D115).

    One short has one email and one email has one short. Anything else raises BadAllowlist.
    """
    if not raw.strip():
        return {}
    by_email: dict[str, str] = {}
    for number, item in enumerate(raw.split(","), start=1):
        short, sep, email = item.partition("=")
        short, email = short.strip(), email.strip().lower()
        if not sep or not SHORT_RE.fullmatch(short) or not ALLOW_EMAIL_RE.fullmatch(email):
            raise BadAllowlist(f"WEB_ALLOWED_EMAILS entry {number} is not short=email")
        if email in by_email or short in by_email.values():
            raise BadAllowlist(f"WEB_ALLOWED_EMAILS entry {number} repeats a short or an email")
        by_email[email] = short
    return by_email


def allowed_emails(settings: Settings) -> dict[str, str]:
    """The allowlist at run time. A bad value fails closed: nobody is allowed."""
    try:
        return parse_allowed_emails(settings.web_allowed_emails)
    except BadAllowlist as exc:
        log.error("allowlist_bad", error=str(exc))
        return {}


def google_configured(settings: Settings) -> bool:
    return not any(
        is_missing(v)
        for v in (
            settings.google_oauth_client_id,
            settings.google_oauth_client_secret,
            settings.web_allowed_emails,
        )
    )


def provider(settings: Settings) -> str:
    """'google' or 'mock' (D116)."""
    if settings.auth_provider != "auto":
        return settings.auth_provider
    return "google" if google_configured(settings) else "mock"


def client_host(request: Request) -> str:
    return request.client.host if request.client else ""


def mock_allowed(request: Request, settings: Settings) -> bool:
    """Mock sign-in: provider mock AND SALEY_ENV=dev AND a client on this machine (D116)."""
    return provider(settings) == "mock" and settings.is_dev and client_host(request) in LOOPBACK


# ------------------------------------------------------------------ rate limit


class RateLimiter:
    """At most `limit` hits per `window` seconds for one key. In process, simple (R4)."""

    PRUNE_AT = 5000

    def __init__(
        self,
        limit: int = AUTH_RATE_LIMIT,
        window: float = AUTH_RATE_WINDOW,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit, self.window, self.now = limit, window, now
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        moment = self.now()
        with self._lock:
            if len(self._hits) > self.PRUNE_AT:
                self._hits = {
                    k: v for k, v in self._hits.items() if v and moment - v[-1] < self.window
                }
            hits = self._hits.setdefault(key, deque())
            while hits and moment - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(moment)
            return True


def rate_limit(request: Request) -> None:
    # Behind caddy every request shares the proxy address, so the limit is shared too. That is
    # enough for a team of four; an X-Forwarded-For header would be spoofable.
    limiter: RateLimiter = request.app.state.auth_limiter
    if not limiter.allow(client_host(request)):
        log.warning("auth_rate_limited")
        raise HTTPException(status_code=429, detail=w.SIGNIN_LIMIT)


# ------------------------------------------------------------------ sessions


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def email_for(settings: Settings, short: str, provider_name: str) -> str | None:
    """The allowlisted address of `short`. The DEV_USER_EMAIL fallback is for mock only:
    under google it would keep a removed dev user signed in (D115)."""
    for email, owner in allowed_emails(settings).items():
        if owner == short:
            return email
    if provider_name == "mock" and short == settings.dev_user_short:
        return settings.dev_user_email
    return None


def session_user(request: Request) -> SignedIn | None:
    """The signed-in user of this request, or None. Never raises for a bad cookie.

    A session counts only when its row is not revoked and not expired, its provider is the
    provider now in force, and (google) the user's address is still on the allowlist.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > MAX_TOKEN_CHARS:
        return None
    settings = get_settings()
    current = provider(settings)
    with ro_engine().connect() as conn:
        row = (
            conn.execute(
                text(
                    "select s.user_short, s.provider, t.role from v_web_session s "
                    "join v_team t on t.short = s.user_short "
                    "where s.id_hash = :h and s.revoked_at is null and s.expires_at > now()"
                ),
                {"h": hash_token(token)},
            )
            .mappings()
            .first()
        )
    if row is None:
        return None
    if row["provider"] != current:
        log.info("session_ended", reason="provider_changed")
        return None
    if current == "mock" and not mock_allowed(request, settings):
        return None  # a mock cookie works only where mock sign-in works: dev + loopback (D116)
    email = email_for(settings, row["user_short"], current)
    if email is None and current == "google":
        # removed from WEB_ALLOWED_EMAILS: the session ends here (D115)
        log.info("session_ended", reason="not_on_allowlist")
        return None
    return SignedIn(email or "", row["role"], row["user_short"], current)


def _call(sql: str, params: dict[str, Any]) -> dict[str, Any]:
    from saley.web.act import call_function  # late import: act needs app, app needs this module

    with ro_engine().connect() as conn:
        return call_function(conn, sql, params)


def start_session(short: str, provider_name: str, hours: int) -> str | None:
    """Make a session row (hash only). Returns the cookie token, or None when refused."""
    token = secrets.token_urlsafe(32)
    answer = _call(
        "select saley_session_start(:u, :p, :h, :n)",
        {"u": short, "p": provider_name, "h": hash_token(token), "n": hours},
    )
    if not answer.get("ok"):
        log.warning("session_refused", short=short, code=answer.get("code"))
        return None
    return token


def end_session(token: str) -> None:
    _call("select saley_session_end(:h)", {"h": hash_token(token)})


# ------------------------------------------------------------------ pages and cookies


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    """A small server-rendered page. Every value in `body` is escaped by the caller.

    The meta tag lets this page send its real Origin on the sign-in form. With the header policy
    `no-referrer` alone, Chromium posts `Origin: null` and the same-origin guard refuses the click.
    A request to another site still gets no referrer.
    """
    document = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="referrer" content="same-origin">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<link rel="icon" href="data:,">'  # no favicon request, no 404 in the console
        f'<title>{html.escape(title)}</title><link rel="stylesheet" href="{PAGE_CSS}">'
        f'</head><body class="signin"><main class="card signin-card"><h1>{html.escape(title)}'
        f"</h1>{body}</main></body></html>"
    )
    return HTMLResponse(document, status_code=status, headers={"Cache-Control": "no-store"})


def message_page(message: str, status: int, detail: str = "") -> HTMLResponse:
    link = f'<p><a href="/signin">{html.escape(w.SIGNIN_BACK)}</a></p>'
    extra = f"<p>{html.escape(detail)}</p>" if detail else ""
    return page(message, extra + link, status)


def refused(reason: str) -> HTMLResponse:
    log.warning("signin_refused", reason=reason, kind="allowlist")
    return message_page(w.SIGNIN_REFUSED, 403, w.SIGNIN_REFUSED_HELP)


def _secure(request: Request) -> bool:
    # Outside dev always Secure, even when a proxy reaches us over loopback.
    return not get_settings().is_dev or client_host(request) not in LOOPBACK


def set_session_cookie(response: Response, request: Request, token: str, hours: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=hours * 3600,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_secure(request),
    )


def clear_cookie(response: Response, request: Request, name: str, path: str) -> None:
    response.delete_cookie(name, path=path, httponly=True, samesite="lax", secure=_secure(request))


def refuse_cross_site(request: Request) -> None:

    if _cross_site(request):
        raise HTTPException(status_code=403, detail=w.ERR_CROSS_SITE)


def finish(request: Request, short: str, provider_name: str) -> Response:
    """Start the session and send the browser to the dashboard."""
    settings = get_settings()
    token = start_session(short, provider_name, settings.session_hours)
    if token is None:
        # On the list, but no team row (v_team): not an allowlist problem (T10 review).
        log.warning("signin_failed", reason="no_such_user", kind="other")
        return message_page(w.SIGNIN_NO_USER, 403)
    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, request, token, settings.session_hours)
    log.info("signed_in", short=short, provider=provider_name)
    return response


# ------------------------------------------------------------------ google
def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def token_client() -> httpx.Client:
    """The HTTP client for the token call. Tests swap it for a MockTransport client."""
    return egress.make_client()


def decode_claims(id_token: str) -> dict[str, Any]:
    """The claims of an id_token. No signature check: see the module docstring (OIDC 3.1.3.7)."""
    parts = id_token.split(".")
    if len(parts) != 3:
        raise ClaimsError("not a jwt")
    payload = parts[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, RecursionError) as exc:  # bad base64/utf-8/json; deeply nested json
        raise ClaimsError("unreadable claims") from exc
    if not isinstance(claims, dict):
        raise ClaimsError("claims are not an object")
    return claims


def check_claims(claims: dict[str, Any], client_id: str, now: float) -> str:
    """Check iss, aud, exp and email_verified. Returns the address in lower case."""
    exp = claims.get("exp")
    email = claims.get("email")
    iss = claims.get("iss")
    if not isinstance(iss, str) or iss not in ISSUERS:  # a list or dict iss is not hashable
        raise ClaimsError("iss")
    if claims.get("aud") != client_id:  # a list aud fails closed here, as it should
        raise ClaimsError("aud")
    if isinstance(exp, bool) or not isinstance(exp, int | float) or exp <= now:
        raise ClaimsError("exp")
    if claims.get("email_verified") is not True:
        raise ClaimsError("email_verified")
    if not isinstance(email, str) or not email.strip():
        raise ClaimsError("email")
    return email.lower()


def _oauth_error(answer: httpx.Response) -> tuple[str, str]:
    """Google's `error` and `error_description`, cut short. Empty strings when absent."""
    try:
        body = answer.json()
    except ValueError:
        return "", ""
    if not isinstance(body, dict):
        return "", ""
    error, description = body.get("error"), body.get("error_description")
    return (
        error[:GOOGLE_ERROR_MAX] if isinstance(error, str) else "",
        description[:GOOGLE_DESCRIPTION_MAX] if isinstance(description, str) else "",
    )


def exchange_code(settings: Settings, code: str, verifier: str) -> str:
    """Swap the code for tokens at Google (egress only). Returns the id_token text."""
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings.google_oauth_client_id,
        "client_secret": settings.google_oauth_client_secret,
        "redirect_uri": settings.google_oauth_redirect_url,
        "code_verifier": verifier,
    }
    try:
        with token_client() as client:
            answer = egress.request_capped(
                client,
                "POST",
                TOKEN_URL,
                max_bytes=TOKEN_MAX_BYTES,
                idempotent=False,
                data=form,
                headers={"Accept": "application/json"},
            )
    except (httpx.HTTPError, egress.EgressBlocked, egress.BodyTooLarge) as exc:
        log.error("token_call_failed", error=type(exc).__name__)
        raise UpstreamError("token call failed") from exc
    if answer.status_code >= 500:
        log.error("token_call_5xx", status=answer.status_code)
        raise UpstreamError("token endpoint error")
    if answer.status_code != 200:
        raise TokenRefused(*_oauth_error(answer))
    try:
        id_token = answer.json().get("id_token")
    except (ValueError, AttributeError) as exc:
        raise ClaimsError("token answer is not json") from exc
    if not isinstance(id_token, str):
        raise ClaimsError("no id_token")
    return id_token


# ------------------------------------------------------------------ routes
router = APIRouter()
Limited = Annotated[None, Depends(rate_limit)]


def _provider_or_404(request: Request, wanted: str) -> Settings:
    settings = get_settings()
    if provider(settings) != wanted or (wanted == "mock" and not mock_allowed(request, settings)):
        raise HTTPException(status_code=404, detail="Not Found")
    return settings


def _not_set_up() -> HTMLResponse:
    return message_page(w.SIGNIN_NOT_SET, 503)


@router.get("/signin", include_in_schema=False)
def signin(request: Request) -> Response:
    settings = get_settings()
    if provider(settings) == "mock" and not mock_allowed(request, settings):
        raise HTTPException(status_code=404, detail="Not Found")
    if session_user(request) is not None:
        return RedirectResponse("/", status_code=303)
    if provider(settings) == "google":
        if not google_configured(settings):
            return _not_set_up()
        button = f'<a class="btn" href="/auth/google">{html.escape(w.SIGNIN_GOOGLE)}</a>'
        return page(w.SIGNIN_TITLE, button)
    label = html.escape(w.SIGNIN_MOCK.replace("{who}", settings.dev_user_short))
    form = (
        '<form method="post" action="/auth/mock">'
        f'<button class="btn" type="submit">{label}</button></form>'
    )
    return page(w.SIGNIN_TITLE, form)


def _form_email(raw: bytes) -> str:
    values = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
    return values.get("email", [""])[0]


def _mock_short(settings: Settings, email: str) -> str | None:
    """The short for a mock sign-in: the dev user, or an address on the allowlist."""
    if not email or email.lower() == settings.dev_user_email.lower():
        return settings.dev_user_short
    return allowed_emails(settings).get(email.lower())


@router.post("/auth/mock", include_in_schema=False)
async def auth_mock(request: Request, _limit: Limited) -> Response:
    settings = _provider_or_404(request, "mock")
    refuse_cross_site(request)
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > FORM_MAX_BYTES:
            raise HTTPException(status_code=413, detail=w.ERR_TOO_BIG)
    short = _mock_short(settings, _form_email(raw))
    if short is None:
        return refused("not_on_allowlist")
    return await run_in_threadpool(finish, request, short, "mock")


@router.get("/auth/google", include_in_schema=False)
def auth_google(request: Request, _limit: Limited) -> Response:
    settings = _provider_or_404(request, "google")
    if not google_configured(settings):
        return _not_set_up()
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.google_oauth_client_id,
            "redirect_uri": settings.google_oauth_redirect_url,
            "scope": "openid email",
            "state": state,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
    )
    response = RedirectResponse(f"{AUTH_URL}?{query}", status_code=303)
    # Q-N5: the start half of the round trip, so a log shows both ends and their process.
    log.info(
        "signin_started",
        secure=_secure(request),
        host=request.headers.get("host", "")[:100],
        pid=os.getpid(),
    )
    response.set_cookie(
        OAUTH_COOKIE,
        f"{state}.{verifier}",
        max_age=OAUTH_COOKIE_SECONDS,
        path="/auth",
        httponly=True,
        samesite="lax",
        secure=_secure(request),
    )
    return response


#: ClaimsError text -> (log kind, page text). Q-N5: the page says the reason in plain words.
TOKEN_REASONS = {"token endpoint refused the code", "token answer is not json", "no id_token"}


# Q-N5: Google's token errors in plain words. Anything else keeps the general line.
GOOGLE_ERROR_PAGES = {
    "invalid_client": w.SIGNIN_BAD_CLIENT,
    "unauthorized_client": w.SIGNIN_BAD_CLIENT,
    "redirect_uri_mismatch": w.SIGNIN_REDIRECT_MISMATCH,
    "invalid_grant": w.SIGNIN_CODE_OLD,
}


def _claims_failure(reason: str, google_error: str = "") -> tuple[str, str]:
    if google_error:
        return "token_exchange", GOOGLE_ERROR_PAGES.get(google_error, w.SIGNIN_CODE_REFUSED)
    if reason in TOKEN_REASONS:
        return "token_exchange", w.SIGNIN_CODE_REFUSED
    if reason == "email_verified":
        return "claims", w.SIGNIN_UNVERIFIED
    return "claims", w.SIGNIN_BAD_ANSWER


def _origin(url: str) -> str:
    """The scheme and host of the redirect URL, or "" when it is not a usable URL."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def _failed(
    request: Request,
    reason: str,
    kind: str,
    message: str,
    status: int = 400,
    detail: str = "",
    **google: str,
) -> Response:
    log.warning("signin_failed", reason=reason, kind=kind, **google)
    response = message_page(message, status, detail)
    clear_cookie(response, request, OAUTH_COOKIE, "/auth")
    return response


def _expired(request: Request, settings: Settings, reason: str) -> Response:
    """No state cookie, or a state that does not match. The usual cause on a laptop: the
    sign-in started at one address (127.0.0.1) and Google sent it back to another (localhost),
    so the browser did not send the cookie. The page names the address to use."""
    origin = _origin(settings.google_oauth_redirect_url)
    detail = w.SIGNIN_EXPIRED_HELP.format(origin=origin) if origin else ""
    # Q-N5: name the cause without a secret: did any cookie arrive, which host, which process.
    log.warning(
        "signin_state_detail",
        reason=reason,
        oauth_cookie=OAUTH_COOKIE in request.cookies,
        cookie_names=sorted(request.cookies)[:10],
        host=request.headers.get("host", "")[:100],
        pid=os.getpid(),
    )
    return _failed(request, reason, "state", w.SIGNIN_EXPIRED, detail=detail)


Text = Annotated[str | None, Query(max_length=2048)]


@router.get("/auth/callback", include_in_schema=False)
def auth_callback(
    request: Request,
    _limit: Limited,
    code: Text = None,
    state: Text = None,
    error: Text = None,
) -> Response:
    settings = _provider_or_404(request, "google")
    if not google_configured(settings):
        return _not_set_up()
    saved_state, _, verifier = request.cookies.get(OAUTH_COOKIE, "").partition(".")
    if error is not None:
        return _failed(request, "google_error", "google_error", w.SIGNIN_STOPPED)
    if not code or not state:
        return _failed(request, "missing_code_or_state", "other", w.SIGNIN_EXPIRED)
    if not saved_state or not verifier:
        return _expired(request, settings, "no_state_cookie")
    if not hmac.compare_digest(state.encode(), saved_state.encode()):
        return _expired(request, settings, "state_mismatch")
    try:
        claims = decode_claims(exchange_code(settings, code, verifier))
        email = check_claims(claims, settings.google_oauth_client_id, time.time())
    except UpstreamError:
        return _failed(request, "upstream", "token_exchange", w.SIGNIN_NO_ANSWER, 502)
    except TokenRefused as exc:
        kind, message = _claims_failure(str(exc), exc.error or "other")
        return _failed(
            request,
            f"claims:{exc}",
            kind,
            message,
            google_error=exc.error,
            google_error_description=exc.description,
        )
    except ClaimsError as exc:
        kind, message = _claims_failure(str(exc))
        return _failed(request, f"claims:{exc}", kind, message)
    short = allowed_emails(settings).get(email)
    if short is None:
        response = refused("not_on_allowlist")
        clear_cookie(response, request, OAUTH_COOKIE, "/auth")
        return response
    response = finish(request, short, "google")
    clear_cookie(response, request, OAUTH_COOKIE, "/auth")
    return response


@router.post("/auth/signout", include_in_schema=False)
def auth_signout(request: Request, _limit: Limited) -> Response:
    refuse_cross_site(request)
    token = request.cookies.get(SESSION_COOKIE)
    if token and len(token) <= MAX_TOKEN_CHARS:
        end_session(token)
    response = RedirectResponse("/signin", status_code=303)
    clear_cookie(response, request, SESSION_COOKIE, "/")
    return response


def install(app: FastAPI) -> None:
    """Add the sign-in routes. A bad WEB_ALLOWED_EMAILS stops the app here (D115)."""
    settings = get_settings()
    parse_allowed_emails(settings.web_allowed_emails)
    chosen = provider(settings)
    log.info("auth_provider", provider=chosen)
    if chosen == "google" and not google_configured(settings):
        log.error("auth_google_not_set_up")  # every sign-in answers 503 until it is
    elif chosen == "google":
        # Q-N5: once at start, what Saley sends to Google, to compare with the Google console.
        # The client id is public; only its last 6 characters are shown. Never the secret.
        log.warning(
            "auth_google_setup",
            redirect_uri=settings.google_oauth_redirect_url,
            client_id_end=settings.google_oauth_client_id[-6:],
        )
    elif chosen == "mock" and not settings.is_dev:
        log.warning("auth_mock_outside_dev")  # nobody can sign in: mock needs SALEY_ENV=dev
    app.state.auth_limiter = RateLimiter()
    app.include_router(router)
