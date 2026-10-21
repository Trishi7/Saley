"""Egress: blocklist, allowlist, rate limit, retry, redirects, gspread guard, egress_log."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from sqlalchemy import Engine, text

from saley.core import egress
from saley.core.egress import (
    EgressBlocked,
    EgressPolicy,
    EgressRateLimited,
    GuardedHTTPClient,
    host_matches,
    make_client,
    request,
)


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def policy(
    rate: int = 30,
    max_wait: float = 20.0,
    clock: FakeClock | None = None,
    recorder: Callable[[str, bool], None] | None = None,
) -> EgressPolicy:
    clock = clock or FakeClock()
    return EgressPolicy(
        ["example.com", "sheets.googleapis.com", "linkedin.com"],
        ["linkedin.com"],
        rate,
        max_wait_seconds=max_wait,
        clock=clock,
        sleep=clock.sleep,
        recorder=recorder,
    )


def ok_transport(calls: list[str]) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(200, json={"ok": True})

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    "url",
    [
        "https://linkedin.com/in/someone",
        "https://www.linkedin.com/in/someone",
        "http://uk.LinkedIn.com/feed",
        "https://linkedin.com.:443/x",
    ],
)
def test_linkedin_is_blocked_even_when_allowlisted(url: str) -> None:
    with pytest.raises(EgressBlocked):
        policy().check(url)


def test_lookalike_host_is_not_blocked_but_not_allowed_either() -> None:
    assert not host_matches("notlinkedin.com", "linkedin.com")
    with pytest.raises(EgressBlocked, match="not on the allowlist"):
        policy().check("https://notlinkedin.com/")


@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd", "https:///nohost"])
def test_bad_schemes_and_hosts_are_blocked(url: str) -> None:
    with pytest.raises(EgressBlocked):
        policy().check(url)


def test_allowlisted_host_and_subdomain_pass() -> None:
    p = policy()
    assert p.check("https://example.com/a") == "example.com"
    assert p.check("https://api.example.com/a") == "api.example.com"


def test_client_call_goes_through_and_is_logged() -> None:
    calls: list[str] = []
    seen: list[tuple[str, bool]] = []
    p = policy(recorder=lambda host, ok: seen.append((host, ok)))
    with make_client(p, inner=ok_transport(calls)) as client:
        response = client.get("https://example.com/x")
    assert response.status_code == 200
    assert calls == ["https://example.com/x"]
    assert seen == [("example.com", True)]


def test_blocked_call_never_reaches_the_network() -> None:
    calls: list[str] = []
    with make_client(policy(), inner=ok_transport(calls)) as client, pytest.raises(EgressBlocked):
        client.get("https://www.linkedin.com/in/x")
    assert calls == []


def test_redirect_to_linkedin_is_blocked_hop_by_hop() -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(302, headers={"Location": "https://www.linkedin.com/in/x"})

    client = make_client(policy(), inner=httpx.MockTransport(handler))
    with client, pytest.raises(EgressBlocked):
        client.get("https://example.com/start")
    assert calls == ["https://example.com/start"]


def test_rate_limit_holds_per_host() -> None:
    clock = FakeClock()
    p = policy(rate=3, max_wait=60, clock=clock)
    for _ in range(3):
        p.acquire("example.com")
    assert clock.slept == []
    p.acquire("example.com")  # 4th call waits for a token: 60 s / 3 = 20 s
    assert clock.slept == [pytest.approx(20.0)]
    p.acquire("other.example.com")  # other hosts have their own bucket
    assert len(clock.slept) == 1


def test_rate_limit_refuses_when_wait_is_too_long() -> None:
    p = policy(rate=1, max_wait=5)
    p.acquire("example.com")
    with pytest.raises(EgressRateLimited):
        p.acquire("example.com")


def test_retry_on_503_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _s: None)
    answers = iter([503, 503, 200])

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(next(answers))

    with make_client(policy(), inner=httpx.MockTransport(handler)) as client:
        assert request(client, "GET", "https://example.com/x").status_code == 200


def test_retry_gives_up_and_returns_last_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _s: None)
    count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        count["n"] += 1
        return httpx.Response(503)

    with make_client(policy(), inner=httpx.MockTransport(handler)) as client:
        assert request(client, "GET", "https://example.com/x").status_code == 503
    assert count["n"] == egress.RETRY_ATTEMPTS


def test_retry_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _s: None)
    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectError("down", request=req)
        return httpx.Response(200)

    with make_client(policy(), inner=httpx.MockTransport(handler)) as client:
        assert request(client, "GET", "https://example.com/x").status_code == 200


def test_blocked_is_not_retried() -> None:
    with make_client(policy(), inner=ok_transport([])) as client, pytest.raises(EgressBlocked):
        request(client, "GET", "https://linkedin.com/")


def test_gspread_client_is_guarded() -> None:
    egress.reset_policy(policy())
    client = GuardedHTTPClient.__new__(GuardedHTTPClient)
    with pytest.raises(EgressBlocked):
        client.request("get", "https://evil.example.org/v4/spreadsheets/x")


def test_db_recorder_counts_calls_and_errors(engine: Engine) -> None:
    egress.db_recorder("example.com", True)
    egress.db_recorder("example.com", False)
    with engine.begin() as conn:
        row = conn.execute(
            text("select calls, errors from egress_log where host = 'example.com'")
        ).one()
        conn.execute(text("delete from egress_log"))
    assert tuple(row) == (2, 1)


# ---------------------------------------- review fixes: egress


def test_linkedin_blocked_even_with_empty_blocklist() -> None:
    policy = egress.EgressPolicy(["linkedin.com", "example.com"], [], 30, max_wait_seconds=1)
    with pytest.raises(egress.EgressBlocked):
        policy.check("https://www.linkedin.com/in/x")


def test_post_is_not_retried_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _s: None)
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    policy = egress.EgressPolicy(["example.com"], [], 30, max_wait_seconds=1)
    with egress.make_client(policy, inner=httpx.MockTransport(handler)) as client:
        egress.request(client, "POST", "https://example.com/x")
    assert calls["n"] == 1


# ---------------------------------------- R3 guardrails: matrix rows R3-G02 and R3-G03

LINKEDIN_SHAPES = [
    "https://www.linkedin.com/in/x",
    "https://LinkedIn.com./in/x",  # upper case and a trailing dot
    "https://uk.linkedin.com/in/x",  # a country subdomain
    "https://example.com@www.linkedin.com/in/x",  # user-info trick
    "https://media.licdn.com/dms/image/x.png",  # LinkedIn CDN
    "https://lnkd.in/abc",  # LinkedIn short links
]


def test_linkedin_blocked_three_shapes() -> None:
    """R3-G02: no linkedin.com request leaves the process, even if the allowlist names it."""
    calls: list[str] = []
    allowlisted = EgressPolicy(
        ["example.com", "linkedin.com", "licdn.com", "lnkd.in"],
        [],
        30,
        max_wait_seconds=1,
    )
    with make_client(allowlisted, inner=ok_transport(calls)) as client:
        for url in LINKEDIN_SHAPES:
            with pytest.raises(EgressBlocked):
                client.get(url)

        def redirect(req: httpx.Request) -> httpx.Response:
            calls.append(str(req.url))
            return httpx.Response(301, headers={"Location": "https://uk.linkedin.com/in/x"})

        hop = make_client(allowlisted, inner=httpx.MockTransport(redirect))
        with hop, pytest.raises(EgressBlocked):
            hop.get("https://example.com/go")
    assert calls == ["https://example.com/go"]  # only the first hop was sent


def test_host_outside_allowlist_blocked() -> None:
    """R3-G03: a host that is not on EGRESS_ALLOWLIST never gets a request."""
    calls: list[str] = []
    with make_client(policy(), inner=ok_transport(calls)) as client:
        for url in [
            "https://evil.example.org/x",
            "https://example.com.evil.org/x",  # allowed name as a prefix only
            "https://notexample.com/x",  # allowed name as a suffix only
            "http://127.0.0.1:8080/admin",
            "https://[::1]/x",
        ]:
            with pytest.raises(EgressBlocked, match="not on the allowlist"):
                client.get(url)

        def redirect(req: httpx.Request) -> httpx.Response:
            calls.append(str(req.url))
            return httpx.Response(302, headers={"Location": "https://evil.example.org/x"})

        hop = make_client(policy(), inner=httpx.MockTransport(redirect))
        with hop, pytest.raises(EgressBlocked):
            hop.get("https://example.com/go")
    assert calls == ["https://example.com/go"]


def test_default_allowlist_has_the_news_hosts_and_no_linkedin() -> None:
    from saley.core.settings import Settings

    allow = Settings(_env_file=None).allowlist
    for host in ("news.google.com", "export.arxiv.org", "api.openalex.org", "html.duckduckgo.com"):
        assert host in allow
    assert not any("linkedin" in host for host in allow)
