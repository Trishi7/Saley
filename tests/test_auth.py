"""Sign-in: mock and Google providers, the allowlist, sessions (R4 ticket 2, D114-D116).

Google is never called. The token endpoint is an httpx MockTransport behind the egress
policy, so the allowlist and the host check still run (CLAUDE.md: every call via egress).
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.core import egress
from saley.web import auth
from saley.web.app import create_app
from tests.conftest import sign_in

CLIENT_ID = "cid-123.apps.googleusercontent.com"
SID = "sid@membrane.test"
VR = "vr@membrane.test"
GOOGLE_ENV = {
    "GOOGLE_OAUTH_CLIENT_ID": CLIENT_ID,
    "GOOGLE_OAUTH_CLIENT_SECRET": "test-secret-not-real",
    "WEB_ALLOWED_EMAILS": f"sid={SID},vaishnavi={VR}",
}
REFUSED = "Your email is not on the list."


def b64(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def make_id_token(**changes: Any) -> str:
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": int(time.time()) + 3600,
        "email": SID,
        "email_verified": True,
    }
    claims.update(changes)
    claims = {k: v for k, v in claims.items() if v is not None}
    return f"{b64({'alg': 'RS256'})}.{b64(claims)}.not-checked"


class FakeGoogle:
    """The token endpoint. Records every request it gets."""

    def __init__(self, id_token: str | None = None, status: int = 200) -> None:
        self.id_token = id_token if id_token is not None else make_id_token()
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = {"id_token": self.id_token, "access_token": "x", "token_type": "Bearer"}
        return httpx.Response(self.status, json=body if self.status == 200 else {"error": "x"})


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch, set_env: Callable[..., None]) -> Iterator[FakeGoogle]:
    set_env(**GOOGLE_ENV)
    fake = FakeGoogle()
    monkeypatch.setattr(
        auth, "token_client", lambda: egress.make_client(inner=httpx.MockTransport(fake))
    )
    yield fake


@pytest.fixture
def client(synced: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def begin(client: TestClient) -> tuple[str, str]:
    """Start the Google flow. Returns (state, code_challenge) from the redirect."""
    r = client.get("/auth/google", follow_redirects=False)
    assert r.status_code == 303
    url = urlsplit(r.headers["location"])
    assert (url.scheme, url.netloc, url.path) == (
        "https",
        "accounts.google.com",
        "/o/oauth2/v2/auth",
    )
    query = parse_qs(url.query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["client_id"] == [CLIENT_ID] and query["response_type"] == ["code"]
    assert query["redirect_uri"] == ["http://localhost:8090/auth/callback"]
    return query["state"][0], query["code_challenge"][0]


def callback(client: TestClient, state: str, code: str = "the-code") -> httpx.Response:
    return client.get(
        "/auth/callback", params={"state": state, "code": code}, follow_redirects=False
    )


def cookie_header(response: httpx.Response, name: str) -> str:
    for value in response.headers.get_list("set-cookie"):
        if value.lower().startswith(name + "="):
            return value
    raise AssertionError(f"no {name} cookie in {response.headers.get_list('set-cookie')}")


def sessions(db: Session) -> list[Any]:
    return list(
        db.execute(
            text("select provider, expires_at, revoked_at, id_hash from web_session order by id")
        ).all()
    )


# ------------------------------------------------------------------ R4-A01


def test_allowlisted_in_others_refused(
    synced: Session, set_env: Callable[..., None], client: TestClient
) -> None:
    """Only an allowlisted address signs in; any other address gets the refusal page (403)."""
    set_env(AUTH_PROVIDER="mock", WEB_ALLOWED_EMAILS=f"sid={SID},vaishnavi={VR}")
    refused = client.post("/auth/mock", data={"email": "evil@membrane.test"})
    assert refused.status_code == 403 and REFUSED in refused.text
    assert "saley_session" not in refused.headers.get("set-cookie", "")
    assert client.get("/api/me").status_code == 401
    # a look-alike of an allowed address is not that address
    assert client.post("/auth/mock", data={"email": "sid@membrane.test.evil.io"}).status_code == 403
    assert client.post("/auth/mock", data={"email": " " + SID + "\n"}).status_code == 403

    ok = client.post("/auth/mock", data={"email": SID.upper()}, follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/"
    me = client.get("/api/me").json()
    assert me["email"] == SID
    assert [s[0] for s in sessions(synced)] == ["mock"]


def test_google_allowlisted_in_others_refused(google: FakeGoogle, client: TestClient) -> None:
    """The Google callback maps the id_token email to a short; others get 403."""
    google.id_token = make_id_token(email="stranger@membrane.test")
    state, _ = begin(client)
    r = callback(client, state)
    assert r.status_code == 403 and REFUSED in r.text
    assert client.get("/api/me").status_code == 401

    google.id_token = make_id_token(email=VR.upper())  # case does not matter
    state, _ = begin(client)
    assert callback(client, state).status_code == 303
    assert client.get("/api/me").json()["email"] == VR


def test_mock_default_signs_in_as_the_dev_user(client: TestClient) -> None:
    sign_in(client)
    me = client.get("/api/me").json()
    assert me["email"] == "dev@membrane.local" and me["role"] == "admin" and me["dev"] is True


def test_signin_page_has_one_button(client: TestClient, google: FakeGoogle) -> None:
    page = client.get("/signin")
    assert page.status_code == 200 and "Sign in with Google" in page.text
    assert "/auth/mock" not in page.text
    assert "form-action 'self'" in page.headers["content-security-policy"]


def test_signin_page_in_mock_mode(client: TestClient) -> None:
    page = client.get("/signin")
    assert page.status_code == 200
    assert "Sign in as trishi" in page.text and 'action="/auth/mock"' in page.text
    assert "Sign in with Google" not in page.text
    # Chromium posts `Origin: null` under the header policy no-referrer; the meta tag fixes it.
    assert '<meta name="referrer" content="same-origin">' in page.text
    sign_in(client)
    assert client.get("/signin", follow_redirects=False).status_code == 303  # already in


# ------------------------------------------------------------------ R4-A01 (no session)


def test_no_api_without_session(client: TestClient) -> None:
    """Every mode needs a session: no cookie, a forged cookie and a signed-out cookie get 401."""
    for path in ("/api/me", "/api/today", "/api/people", "/api/search?q=a", "/api/health/full"):
        r = client.get(path)
        assert r.status_code == 401, path
        assert r.json()["detail"] == "Sign in to use Saley."
    post = client.post("/api/act", json={}, headers={"X-Saley-Act": "1"})
    assert post.status_code == 401
    # the old dev bypass: an As header does not make a session either
    assert client.get("/api/me", headers={"X-Saley-As": "sid"}).status_code == 401

    home = client.get("/", follow_redirects=False)
    assert home.status_code == 303 and home.headers["location"] == "/signin"
    assert client.get("/static/signin.css").status_code == 200  # the sign-in style stays public
    assert client.get("/healthz").status_code == 200

    client.cookies.set("saley_session", "x" * 43)
    assert client.get("/api/me").status_code == 401
    client.cookies.clear()

    sign_in(client)
    token = client.cookies["saley_session"]
    assert client.get("/api/me").status_code == 200
    assert client.get("/", follow_redirects=False).status_code == 200
    out = client.post("/auth/signout", follow_redirects=False)
    assert out.status_code == 303 and out.headers["location"] == "/signin"
    assert 'saley_session=""' in cookie_header(out, "saley_session") or "Max-Age=0" in (
        cookie_header(out, "saley_session")
    )
    client.cookies.set("saley_session", token)  # replay of a signed-out token
    assert client.get("/api/me").status_code == 401


def test_session_cookie_is_httponly_lax_and_stores_only_a_hash(
    client: TestClient, synced: Session
) -> None:
    r = client.post("/auth/mock", follow_redirects=False)
    header = cookie_header(r, "saley_session").lower()
    assert "httponly" in header and "samesite=lax" in header and "path=/" in header
    assert "secure" not in header  # loopback
    token = client.cookies["saley_session"]
    assert len(token) >= 43
    (row,) = sessions(synced)
    assert row[3] == hashlib.sha256(token.encode()).hexdigest() and token not in row[3]


def test_cookie_is_secure_off_loopback(synced: Session, google: FakeGoogle) -> None:
    # https, so the client sends back the Secure cookies it is given
    with TestClient(
        create_app(), base_url="https://testserver", client=("203.0.113.9", 5000)
    ) as away:
        start = away.get("/auth/google", follow_redirects=False)
        assert "secure" in cookie_header(start, "saley_oauth").lower()
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        r = callback(away, state)
        assert r.status_code == 303
        assert "secure" in cookie_header(r, "saley_session").lower()


def test_session_lasts_session_hours(
    client: TestClient, synced: Session, set_env: Callable[..., None]
) -> None:
    set_env(SESSION_HOURS="3")
    sign_in(client)
    hours = synced.execute(
        text("select extract(epoch from expires_at - now()) / 3600 from web_session")
    ).scalar_one()
    assert 2.9 < float(hours) <= 3.0


# ------------------------------------------------------------------ R4-A03


def test_dev_mode_off_when_google_configured(
    google: FakeGoogle, client: TestClient, synced: Session
) -> None:
    """With Google set up: mock sign-in is 404, X-Saley-As is 403, and no loopback bypass."""
    assert client.post("/auth/mock").status_code == 404
    assert client.post("/auth/mock", data={"email": SID}).status_code == 404
    assert client.get("/api/me").status_code == 401  # loopback, dev env, still no bypass
    assert client.get("/api/me", headers={"X-Saley-As": "kushal"}).status_code == 401

    state, _ = begin(client)
    assert callback(client, state).status_code == 303
    assert client.get("/api/me").status_code == 200
    for who in ("kushal", "sid", "Sid; drop table users"):
        r = client.get("/api/today", headers={"X-Saley-As": who})
        assert r.status_code == 403, who
    post = client.post("/api/act", json={}, headers={"X-Saley-Act": "1", "X-Saley-As": "kushal"})
    assert post.status_code == 403

    # a leftover mock session is dead the moment Google is configured
    synced.execute(text("update web_session set provider = 'mock'"))
    synced.commit()
    assert client.get("/api/me").status_code == 401


def test_mock_works_only_in_dev_from_this_machine(
    synced: Session, set_env: Callable[..., None]
) -> None:
    with TestClient(create_app(), client=("10.1.2.3", 5000)) as away:
        assert away.post("/auth/mock").status_code == 404
        assert away.get("/signin").status_code == 404
    set_env(SALEY_ENV="prod")
    with TestClient(create_app()) as here:
        assert here.post("/auth/mock").status_code == 404
        assert here.get("/signin").status_code == 404
        assert here.get("/auth/google").status_code == 404  # mock provider: no Google flow
    assert synced.execute(text("select count(*) from web_session")).scalar_one() == 0


def test_x_saley_as_works_with_mock_only(client: TestClient) -> None:
    sign_in(client)
    assert client.get("/api/today", headers={"X-Saley-As": "sid"}).status_code == 200
    assert client.get("/api/today", headers={"X-Saley-As": "nobody"}).status_code == 403


# ------------------------------------------------------------------ R4-A04


def test_callback_checks_state_token_and_cookie(
    google: FakeGoogle, client: TestClient, synced: Session
) -> None:
    """Bad state, aud, iss, exp or email_verified is refused; the cookies are locked down."""
    client.app.state.auth_limiter = auth.RateLimiter(limit=1000)  # this test makes many calls
    start = client.get("/auth/google", follow_redirects=False)
    flags = cookie_header(start, "saley_oauth").lower()
    assert "httponly" in flags and "samesite=lax" in flags and "path=/auth" in flags
    assert "max-age=600" in flags

    # state does not match / missing / no code / no cookie
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    assert callback(client, "wrong-state").status_code == 400
    assert not google.requests  # a bad state never reaches Google
    client.cookies.clear()  # no state cookie: the callback has nothing to compare
    assert callback(client, state).status_code == 400
    assert client.get("/auth/callback", follow_redirects=False).status_code == 400
    assert client.get("/auth/callback", params={"error": "access_denied"}).status_code == 400
    assert not google.requests

    bad_tokens = {
        "aud": make_id_token(aud="someone-else"),
        "iss": make_id_token(iss="https://evil.example"),
        "exp": make_id_token(exp=int(time.time()) - 5),
        "no_exp": make_id_token(exp=None),
        "unverified": make_id_token(email_verified=False),
        "unverified_text": make_id_token(email_verified="true"),
        "no_email": make_id_token(email=None),
        "garbage": "not.a.jwt",
        "one_part": "abc",
    }
    for label, token in bad_tokens.items():
        google.id_token = token
        state, _ = begin(client)
        r = callback(client, state)
        assert r.status_code == 400, label
        assert "saley_session" not in r.headers.get("set-cookie", ""), label
        assert client.get("/api/me").status_code == 401, label
    google.id_token = make_id_token(iss="accounts.google.com")  # the second allowed form
    state, _ = begin(client)
    assert callback(client, state).status_code == 303

    assert synced.execute(text("select count(*) from web_session")).scalar_one() == 1


def test_callback_exchange_uses_pkce_and_egress(google: FakeGoogle, client: TestClient) -> None:
    state, challenge = begin(client)
    r = callback(client, state, code="abc 123")
    assert r.status_code == 303 and r.headers["location"] == "/"
    (req,) = google.requests
    assert (req.method, str(req.url)) == ("POST", "https://oauth2.googleapis.com/token")
    form = parse_qs(req.content.decode())
    assert form["grant_type"] == ["authorization_code"] and form["code"] == ["abc 123"]
    assert form["client_id"] == [CLIENT_ID] and form["client_secret"] == ["test-secret-not-real"]
    assert form["redirect_uri"] == ["http://localhost:8090/auth/callback"]
    verifier = form["code_verifier"][0]
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert challenge == expected.rstrip(b"=").decode()
    assert 43 <= len(verifier) <= 128
    # the one-time cookie is gone, so the same callback cannot run twice
    assert callback(client, state).status_code == 400


def test_callback_survives_a_token_endpoint_failure(
    google: FakeGoogle, client: TestClient, synced: Session
) -> None:
    google.status = 500
    state, _ = begin(client)
    r = callback(client, state)
    assert r.status_code == 502 and "Traceback" not in r.text
    assert synced.execute(text("select count(*) from web_session")).scalar_one() == 0


def test_google_explicit_without_setup_is_503(
    client: TestClient, set_env: Callable[..., None]
) -> None:
    set_env(AUTH_PROVIDER="google")
    for path in ("/auth/google", "/auth/callback", "/signin"):
        r = client.get(path)
        assert r.status_code == 503 and "Sign-in is not set up." in r.text, path


# ------------------------------------------------------------------ R5-A01..A05 (Q-N5)
# One test per failure reason: the page says the reason in plain words, never only "Try again".


def test_signin_reason_allowlist(google: FakeGoogle, client: TestClient) -> None:
    google.id_token = make_id_token(email="stranger@membrane.test")
    state, _ = begin(client)
    r = callback(client, state)
    assert r.status_code == 403
    assert "Your email is not on the list." in r.text and "Try again" not in r.text


def test_signin_reason_state(google: FakeGoogle, client: TestClient) -> None:
    """A wrong state, or no state cookie (another address), says the link expired and names
    the address to use. Google is never called."""
    state, _ = begin(client)
    wrong = callback(client, "wrong-state")
    assert wrong.status_code == 400 and "Sign-in link expired, start again." in wrong.text
    state, _ = begin(client)
    client.cookies.clear()
    lost = callback(client, state)
    assert lost.status_code == 400 and "Sign-in link expired, start again." in lost.text
    assert "http://localhost:8090" in lost.text
    assert not google.requests


def test_signin_reason_token_exchange(google: FakeGoogle, client: TestClient) -> None:
    google.status = 400  # Google refuses the code (wrong secret, used code, wrong redirect)
    state, _ = begin(client)
    refused = callback(client, state)
    assert refused.status_code == 400
    assert "Google did not accept the sign-in code. Start again." in refused.text
    google.status = 503
    state, _ = begin(client)
    down = callback(client, state)
    assert down.status_code == 502 and "Google did not answer." in down.text


def test_signin_reason_google_stopped(google: FakeGoogle, client: TestClient) -> None:
    r = client.get("/auth/callback", params={"error": "access_denied"})
    assert r.status_code == 400 and "Google stopped the sign-in. Start again." in r.text


def test_signin_reason_claims(google: FakeGoogle, client: TestClient) -> None:
    google.id_token = make_id_token(email_verified=False)
    state, _ = begin(client)
    r = callback(client, state)
    assert r.status_code == 400 and "Your Google email is not verified." in r.text
    google.id_token = make_id_token(aud="someone-else")
    state, _ = begin(client)
    r = callback(client, state)
    assert r.status_code == 400 and "The answer from Google failed a check." in r.text


def test_session_ends_on_expiry_or_allowlist_removal(
    google: FakeGoogle, client: TestClient, synced: Session, set_env: Callable[..., None]
) -> None:
    """A session ends when it expires, when the user leaves the allowlist, or on sign-out."""
    state, _ = begin(client)
    callback(client, state)
    assert client.get("/api/me").status_code == 200

    set_env(**{**GOOGLE_ENV, "WEB_ALLOWED_EMAILS": f"vaishnavi={VR},kushal=k@membrane.test"})
    assert client.get("/api/me").status_code == 401  # sid was removed
    set_env(**GOOGLE_ENV)
    assert client.get("/api/me").status_code == 200  # back on the list: the same session lives

    synced.execute(text("update web_session set expires_at = now() - interval '1 second'"))
    synced.commit()
    assert client.get("/api/me").status_code == 401
    assert client.get("/", follow_redirects=False).headers["location"] == "/signin"

    # a new sign-in works; sign-out revokes the row
    state, _ = begin(client)
    callback(client, state)
    assert client.get("/api/me").status_code == 200
    assert client.post("/auth/signout", follow_redirects=False).status_code == 303
    assert client.get("/api/me").status_code == 401
    assert [row[2] is not None for row in sessions(synced)] == [False, True]


def test_google_session_of_the_dev_user_ends_on_allowlist_removal(
    google: FakeGoogle, client: TestClient, set_env: Callable[..., None]
) -> None:
    """The DEV_USER_EMAIL fallback is mock only; under google it must not keep sid in (D115)."""
    set_env(**{**GOOGLE_ENV, "DEV_USER_SHORT": "sid", "DEV_USER_EMAIL": "dev@membrane.test"})
    state, _ = begin(client)
    callback(client, state)
    assert client.get("/api/me").status_code == 200
    set_env(
        **{
            **GOOGLE_ENV,
            "DEV_USER_SHORT": "sid",
            "DEV_USER_EMAIL": "dev@membrane.test",
            "WEB_ALLOWED_EMAILS": f"vaishnavi={VR},kushal=k@membrane.test",
        }
    )
    assert client.get("/api/me").status_code == 401


def test_mock_session_expires(client: TestClient, synced: Session) -> None:
    sign_in(client)
    synced.execute(text("update web_session set revoked_at = now()"))
    synced.commit()
    assert client.get("/api/me").status_code == 401


# ------------------------------------------------------------------ guards


def test_auth_posts_refuse_cross_site_calls(client: TestClient) -> None:
    for path in ("/auth/mock", "/auth/signout"):
        r = client.post(path, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403, path
        r = client.post(path, headers={"Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403, path


def test_auth_is_rate_limited_per_client(client: TestClient) -> None:
    codes = [client.post("/auth/signout", follow_redirects=False).status_code for _ in range(12)]
    assert codes[:10] == [303] * 10 and codes[10:] == [429, 429]
    with TestClient(client.app, client=("10.9.9.9", 1)) as other:
        assert other.post("/auth/signout", follow_redirects=False).status_code == 303
    assert client.get("/signin").status_code == 200  # the page itself is not limited


def test_rate_limit_window_moves(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 1000.0}
    limiter = auth.RateLimiter(limit=2, window=60, now=lambda: clock["t"])
    assert [limiter.allow("a"), limiter.allow("a"), limiter.allow("a")] == [True, True, False]
    clock["t"] += 61
    assert limiter.allow("a") is True


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", {}),
        ("  ", {}),
        ("sid=a@b.co", {"a@b.co": "sid"}),
        (" sid = A@B.co , vaishnavi=v@b.co ", {"a@b.co": "sid", "v@b.co": "vaishnavi"}),
    ],
)
def test_allowlist_parses_strictly(raw: str, expected: dict[str, str]) -> None:
    assert auth.parse_allowed_emails(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "a@b.co",  # no short: Saley never guesses it (D35)
        "sid=",
        "=a@b.co",
        "sid=not-an-email",
        "Sid Smith=a@b.co",
        "sid=a@b.co,sid=c@d.co",  # one short, two emails
        "sid=a@b.co,vr=a@b.co",  # one email, two people
        "sid=a@b.co,,vr=c@d.co",
        "sid=a@b.co;vr=c@d.co",
    ],
)
def test_allowlist_rejects_bad_shapes(raw: str) -> None:
    with pytest.raises(auth.BadAllowlist):
        auth.parse_allowed_emails(raw)


def test_bad_allowlist_stops_the_app_at_start(set_env: Callable[..., None]) -> None:
    set_env(WEB_ALLOWED_EMAILS="sid-without-email")
    with pytest.raises(auth.BadAllowlist):
        create_app()


@pytest.mark.parametrize(
    "env, expected",
    [
        ({}, "mock"),
        ({"AUTH_PROVIDER": "mock", **GOOGLE_ENV}, "mock"),
        ({"AUTH_PROVIDER": "google"}, "google"),
        (GOOGLE_ENV, "google"),
        ({**GOOGLE_ENV, "GOOGLE_OAUTH_CLIENT_SECRET": ""}, "mock"),
        ({**GOOGLE_ENV, "WEB_ALLOWED_EMAILS": ""}, "mock"),
    ],
)
def test_provider_choice(env: dict[str, str], expected: str, set_env: Callable[..., None]) -> None:
    from saley.core.settings import get_settings

    set_env(**env)
    assert auth.provider(get_settings()) == expected


def test_session_hours_and_provider_bounds(set_env: Callable[..., None]) -> None:
    from pydantic import ValidationError

    from saley.core.settings import Settings

    for bad in ("0", "721"):
        with pytest.raises(ValidationError):
            Settings(session_hours=bad)
    with pytest.raises(ValidationError):
        Settings(auth_provider="github")
    assert Settings().session_hours == 12 and Settings().auth_provider == "auto"


# ------------------------------------------------------------------ review fixes (ticket 2)


@pytest.mark.parametrize("iss", [["accounts.google.com"], {"a": 1}, None, 7])
def test_a_non_string_iss_is_a_claims_error_not_a_crash(iss: Any) -> None:
    claims = {"iss": iss, "aud": "x", "exp": time.time() + 60, "email_verified": True}
    with pytest.raises(auth.ClaimsError, match="iss"):
        auth.check_claims(claims, "x", time.time())


def test_deeply_nested_claims_are_a_claims_error() -> None:
    deep = "[" * 100_000 + "]" * 100_000
    body = base64.urlsafe_b64encode(deep.encode()).rstrip(b"=").decode()
    with pytest.raises(auth.ClaimsError):
        auth.decode_claims(f"e30.{body}.sig")


def test_a_mock_cookie_does_not_work_off_loopback(client: TestClient) -> None:
    """D116: a mock session counts only where mock sign-in works (dev + this machine)."""
    sign_in(client)
    token = client.cookies.get("saley_session")
    assert token
    with TestClient(client.app, client=("10.1.2.3", 5000)) as away:
        away.cookies.set("saley_session", token)
        assert away.get("/api/me").status_code == 401


def test_signin_reason_state_with_a_bad_redirect_url(
    google: FakeGoogle, client: TestClient, set_env: Callable[..., None]
) -> None:
    """A bad redirect URL in settings never breaks the error page; the help line is left out."""
    set_env(GOOGLE_OAUTH_REDIRECT_URL="<REQUIRED: the redirect URL>")
    r = callback(client, "some-state")
    assert r.status_code == 400 and "Sign-in link expired, start again." in r.text
    assert "://" not in r.text.split("<h1>", 1)[1]


def test_signin_on_the_list_without_a_team_row(
    google: FakeGoogle, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the list but no team member: its own message, not "not on the list"."""
    monkeypatch.setattr(auth, "start_session", lambda *_a: None)
    state, _ = begin(client)
    r = callback(client, state)
    assert r.status_code == 403 and "no team member matches it" in r.text and REFUSED not in r.text


# ------------------------------------------------------------------ R5-L06, R5-L07 (Q-N5)


def test_state_cookie_survives_the_google_round_trip(
    google: FakeGoogle, client: TestClient
) -> None:
    """The state cookie set at /auth/google is a plain first-party cookie that a top-level
    GET from Google brings back: HttpOnly, SameSite=Lax, Path=/auth (covers /auth/callback),
    no Domain, and not Secure on http://localhost in dev. With it, the callback passes the
    state check and reaches the token exchange; the cookie is cleared after one use."""
    start = client.get("/auth/google", follow_redirects=False)
    flags = [f.strip().lower() for f in cookie_header(start, "saley_oauth").split(";")]
    assert "httponly" in flags and "samesite=lax" in flags and "path=/auth" in flags
    assert "secure" not in flags  # dev on loopback over http: a Secure cookie would be dropped
    assert not any(f.startswith("domain=") for f in flags)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    r = client.get(
        "/auth/callback",
        params={"state": state, "code": "c", "scope": "openid email", "authuser": "0"},
        headers={"Referer": "https://accounts.google.com/", "Sec-Fetch-Site": "cross-site"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and len(google.requests) == 1  # state passed: token exchange ran
    assert "saley_oauth=" in cookie_header(r, "saley_oauth")  # cleared: one use only
    assert callback(client, state).status_code == 400  # a replay finds no cookie


def test_state_ttl_is_10_minutes(google: FakeGoogle, client: TestClient) -> None:
    """The state lives 10 minutes (Max-Age=600), long enough for the Google account chooser."""
    assert auth.OAUTH_COOKIE_SECONDS == 600
    start = client.get("/auth/google", follow_redirects=False)
    assert "max-age=600" in cookie_header(start, "saley_oauth").lower()


def test_no_view_as_in_me_and_today_is_the_signed_in_person(
    google: FakeGoogle, client: TestClient
) -> None:
    """D207 (supersedes D206): no "View as" in any mode. /api/me carries no picker flag, and
    "Your tasks" is the signed-in person's own list; with Google `?user=` is ignored. Tests
    that need another person send X-Saley-As with the mock provider (D50, D116); no UI."""
    google.id_token = make_id_token(email=VR)
    state, _ = begin(client)
    assert callback(client, state).status_code == 303
    me = client.get("/api/me").json()
    assert "view_as" not in me
    assert set(me) == {"email", "role", "dev", "features", "people"}
    own = client.get("/api/today").json()["my_tasks"]
    assert own["person"] == "vaishnavi"  # the signed-in person, with no ?user=
    other = client.get("/api/today", params={"user": "sid"}).json()["my_tasks"]
    assert other == own  # ?user=sid is ignored under Google: still Vaishnavi's list


# ------------------------------------------------------------------ R5-L09..L12 (Q-N5)


@pytest.mark.parametrize(
    ("google_error", "page_text"),
    [
        ("invalid_client", "Google refused the client id or secret."),
        ("redirect_uri_mismatch", "The sign-in address and Google setup differ."),
        ("invalid_grant", "The sign-in code expired or was used. Start again."),
        ("temporarily_unavailable", "Google did not accept the sign-in code. Start again."),
    ],
    ids=["invalid_client", "redirect_uri_mismatch", "invalid_grant", "other"],
)
def test_google_token_error_gets_its_own_page_and_log(
    google_error: str,
    page_text: str,
    google: FakeGoogle,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google's `error` from the token endpoint names the cause in plain words; the log line
    has `google_error` and `google_error_description`, never the code or the secret."""
    google.status = 400
    monkeypatch.setattr(
        FakeGoogle,
        "__call__",
        lambda self, request: (
            self.requests.append(request)
            or httpx.Response(400, json={"error": google_error, "error_description": "Bad x"})
        ),
    )
    logged: list[dict[str, Any]] = []
    monkeypatch.setattr(auth.log, "warning", lambda event, **kw: logged.append({"e": event, **kw}))
    state, _ = begin(client)
    r = callback(client, state, code="secret-code-123")
    assert r.status_code == 400 and page_text in r.text
    (line,) = [x for x in logged if x["e"] == "signin_failed"]
    assert line["google_error"] == google_error and line["google_error_description"] == "Bad x"
    assert "secret-code-123" not in str(logged) and "test-secret-not-real" not in str(logged)


def test_start_up_logs_the_redirect_uri_and_client_id_end(
    google: FakeGoogle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once at start: the redirect URI Saley sends and the client id's last 6 characters."""
    logged: list[dict[str, Any]] = []
    monkeypatch.setattr(auth.log, "warning", lambda event, **kw: logged.append({"e": event, **kw}))
    create_app()
    (line,) = [x for x in logged if x["e"] == "auth_google_setup"]
    assert line["redirect_uri"] == "http://localhost:8090/auth/callback"
    assert line["client_id_end"] == CLIENT_ID[-6:]
    assert "test-secret-not-real" not in str(logged)
