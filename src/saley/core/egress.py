"""The only door to the internet: allowlist, blocklist, per-host rate limit, timeout, retries.

Why: CLAUDE.md says every outbound HTTP call goes through here. linkedin.com is blocked even
if someone adds it to the allowlist (S39). A token bucket per host keeps us off blacklists.
Every call is logged with structlog and counted in `egress_log` for the health view.

The guard lives in an httpx transport, so redirects are checked hop by hop and third-party
SDKs (Anthropic) can use the same door. gspread gets a small HTTPClient subclass (D32).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from gspread.http_client import HTTPClient
from tenacity import (
    Retrying,
    retry_if_exception_type,
    retry_if_result,
    stop_after_attempt,
    wait_exponential,
)

from saley.core.logging import get_logger
from saley.core.settings import get_settings

log = get_logger("saley.egress")

ALLOWED_SCHEMES = frozenset({"http", "https"})
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_ATTEMPTS = 3
IDEMPOTENT = frozenset({"GET", "HEAD", "OPTIONS"})
# Always blocked, whatever EGRESS_BLOCKLIST says (S39, CLAUDE.md: never fetch linkedin.com).
HARD_BLOCKLIST = frozenset({"linkedin.com", "licdn.com", "lnkd.in"})
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - a URL, not a secret


class EgressBlocked(Exception):
    """The URL is not allowed out: blocklisted, not allowlisted, or a bad scheme."""


class BodyTooLarge(Exception):
    """The answer is bigger than the caller allows. The rest of the body was never read."""


class EgressRateLimited(EgressBlocked):
    """The host bucket is empty for longer than the caller may wait."""


def host_matches(host: str, pattern: str) -> bool:
    """`linkedin.com` matches `linkedin.com` and any subdomain, never `notlinkedin.com`."""
    host, pattern = host.lower().rstrip("."), pattern.lower().lstrip(".")
    return host == pattern or host.endswith("." + pattern)


@dataclass
class TokenBucket:
    """Classic token bucket. `take()` returns how long the caller must wait for one token."""

    capacity: float
    refill_per_second: float
    clock: Callable[[], float] = time.monotonic
    tokens: float = field(init=False)
    updated: float = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = self.capacity
        self.updated = self.clock()

    def _refill(self) -> None:
        now = self.clock()
        elapsed = max(0.0, now - self.updated)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.updated = now

    def wait_time(self) -> float:
        self._refill()
        if self.tokens >= 1:
            return 0.0
        return (1 - self.tokens) / self.refill_per_second

    def take(self) -> None:
        self._refill()
        self.tokens -= 1


Recorder = Callable[[str, bool], None]


def db_recorder(host: str, ok: bool) -> None:
    """Count the call in egress_log. A failed count is logged, never raised: the call worked."""
    from sqlalchemy import text  # local import keeps egress usable before the DB exists

    from saley.core.clock import today_ist
    from saley.core.db import rw_engine

    try:
        with rw_engine().begin() as conn:
            conn.execute(
                text(
                    "insert into egress_log(day, host, calls, errors) values (:d, :h, 1, :e) "
                    "on conflict (day, host) do update set calls = egress_log.calls + 1, "
                    "errors = egress_log.errors + excluded.errors"
                ),
                {"d": today_ist(), "h": host, "e": 0 if ok else 1},
            )
    except Exception as exc:  # the health view will show the gap; the caller's call stands
        log.warning("egress_log_write_failed", host=host, error=type(exc).__name__)


class EgressPolicy:
    """Decides if a URL may go out, and paces calls per host."""

    def __init__(
        self,
        allowlist: list[str],
        blocklist: list[str],
        rate_per_minute: int,
        *,
        max_wait_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        recorder: Recorder | None = None,
    ) -> None:
        self.allowlist = [h.lower() for h in allowlist]
        self.blocklist = sorted({h.lower() for h in blocklist} | HARD_BLOCKLIST)
        self.rate_per_minute = rate_per_minute
        self.max_wait_seconds = max_wait_seconds
        self._clock = clock
        self._sleep = sleep
        self._recorder = recorder
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def check(self, url: str) -> str:
        """Return the host if the URL may go out. Raise EgressBlocked otherwise."""
        parts = urlsplit(str(url))
        host = (parts.hostname or "").lower()
        if parts.scheme.lower() not in ALLOWED_SCHEMES or not host:
            raise EgressBlocked(f"scheme or host not allowed: {parts.scheme}://{host}")
        if any(host_matches(host, blocked) for blocked in self.blocklist):
            log.warning("egress_blocked", host=host, reason="blocklist")
            raise EgressBlocked(f"{host} is on the blocklist")
        if not any(host_matches(host, allowed) for allowed in self.allowlist):
            log.warning("egress_blocked", host=host, reason="not_allowlisted")
            raise EgressBlocked(f"{host} is not on the allowlist")
        return host

    def acquire(self, host: str) -> None:
        """Take one token for the host. Wait when the wait is short, refuse when it is long."""
        with self._lock:
            bucket = self._buckets.get(host)
            if bucket is None:
                bucket = TokenBucket(
                    capacity=float(self.rate_per_minute),
                    refill_per_second=self.rate_per_minute / 60.0,
                    clock=self._clock,
                )
                self._buckets[host] = bucket
            wait = bucket.wait_time()
            if wait > self.max_wait_seconds:
                raise EgressRateLimited(f"{host}: rate limit, next slot in {wait:.0f} s")
            # Reserve the token now; sleep outside the lock so other hosts are not held up.
            bucket.take()
        if wait > 0:
            log.info("egress_rate_wait", host=host, seconds=round(wait, 2))
            self._sleep(wait)

    def guard(self, url: str) -> str:
        host = self.check(url)
        self.acquire(host)
        return host

    def record(self, host: str, *, ok: bool) -> None:
        if self._recorder is not None:
            self._recorder(host, ok)


class EgressTransport(httpx.BaseTransport):
    """httpx transport that applies the policy to every request, redirects included."""

    def __init__(self, policy: EgressPolicy, inner: httpx.BaseTransport | None = None) -> None:
        self.policy = policy
        self.inner = inner or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = self.policy.guard(str(request.url))
        started = time.monotonic()
        try:
            response = self.inner.handle_request(request)
        except Exception as exc:
            log.warning("egress_call", host=host, method=request.method, error=type(exc).__name__)
            self.policy.record(host, ok=False)
            raise
        ms = int((time.monotonic() - started) * 1000)
        ok = response.status_code < 400
        log.info(
            "egress_call", host=host, method=request.method, status=response.status_code, ms=ms
        )
        self.policy.record(host, ok=ok)
        return response

    def close(self) -> None:
        self.inner.close()


def build_policy(*, recorder: Recorder | None = db_recorder) -> EgressPolicy:
    settings = get_settings()
    return EgressPolicy(
        settings.allowlist,
        settings.blocklist,
        settings.egress_rate_per_host_per_min,
        max_wait_seconds=settings.http_timeout_seconds,
        recorder=recorder,
    )


_POLICY: EgressPolicy | None = None
_POLICY_LOCK = threading.Lock()


def get_policy() -> EgressPolicy:
    """One policy (and so one set of buckets) per process."""
    global _POLICY
    with _POLICY_LOCK:
        if _POLICY is None:
            _POLICY = build_policy()
        return _POLICY


def reset_policy(policy: EgressPolicy | None = None) -> None:
    """Tests swap the process policy here."""
    global _POLICY
    with _POLICY_LOCK:
        _POLICY = policy


def make_client(
    policy: EgressPolicy | None = None, inner: httpx.BaseTransport | None = None, **kwargs: Any
) -> httpx.Client:
    """An httpx client whose every hop goes through the egress policy."""
    settings = get_settings()
    return httpx.Client(
        transport=EgressTransport(policy or get_policy(), inner),
        timeout=settings.http_timeout_seconds,
        follow_redirects=True,
        **kwargs,
    )


def _last_result(retry_state: Any) -> Any:
    """After the last retry, hand the caller the last response instead of a RetryError."""
    return retry_state.outcome.result()


def _retrying(safe: bool) -> Retrying:
    return Retrying(
        stop=stop_after_attempt(RETRY_ATTEMPTS if safe else 1),
        wait=wait_exponential(multiplier=0.5, max=8),
        retry=(
            retry_if_exception_type(httpx.TransportError)
            | retry_if_result(lambda r: r.status_code in RETRY_STATUSES)
        ),
        retry_error_callback=_last_result,
        reraise=True,
    )


def request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    idempotent: bool | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """One call with retries on network errors and 429/5xx. EgressBlocked is never retried."""
    # A POST is retried only when the caller says it is safe: a timeout after the server
    # accepted it would otherwise send it twice.
    safe = method.upper() in IDEMPOTENT if idempotent is None else idempotent
    return _retrying(safe)(client.request, method, url, **kwargs)


ERROR_BODY_BYTES = 65_536  # an error page is read up to here, for its message only


def request_capped(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_bytes: int,
    idempotent: bool | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Like `request`, but the body is streamed and the read stops at `max_bytes` (B1).

    Why: `client.request` loads the whole body before the caller can look at its size, so a
    hostile or broken host could fill memory. Here the read stops one byte past the cap and
    raises BodyTooLarge. Every redirect hop still passes the egress transport. The returned
    response holds the (decoded) body, so `.content`, `.text` and `.json()` work as usual.
    An error status is read only up to ERROR_BODY_BYTES and never raises BodyTooLarge.
    """
    safe = method.upper() in IDEMPOTENT if idempotent is None else idempotent

    def once() -> httpx.Response:
        with client.stream(method, url, **kwargs) as streamed:
            failed = streamed.status_code >= 400
            limit = min(max_bytes, ERROR_BODY_BYTES) if failed else max_bytes
            declared = streamed.headers.get("content-length", "")
            if not failed and declared.isdigit() and int(declared) > limit:
                raise BodyTooLarge(f"{declared} bytes declared, cap {limit}")
            chunks: list[bytes] = []
            size = 0
            for chunk in streamed.iter_bytes():
                size += len(chunk)
                if size > limit:
                    if failed:
                        break
                    raise BodyTooLarge(f"more than {limit} bytes")
                chunks.append(chunk)
            headers = [
                (k, v)
                for k, v in streamed.headers.multi_items()
                if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
            ]
            return httpx.Response(
                streamed.status_code,
                headers=headers,
                content=b"".join(chunks),
                request=streamed.request,
            )

    return _retrying(safe)(once)


class GuardedHTTPClient(HTTPClient):
    """gspread HTTP client that asks the egress policy before every Sheets call.

    The Sheets API never redirects, so redirects are refused: a hop could not be checked.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.session.max_redirects = 0

    def login(self) -> None:
        get_policy().guard(GOOGLE_TOKEN_URL)
        super().login()

    def request(self, method: str, endpoint: str, *args: Any, **kwargs: Any) -> Any:
        policy = get_policy()
        host = policy.guard(endpoint)
        try:
            response = super().request(method, endpoint, *args, **kwargs)
        except Exception as exc:
            log.warning("egress_call", host=host, method=method.upper(), error=type(exc).__name__)
            policy.record(host, ok=False)
            raise
        log.info("egress_call", host=host, method=method.upper(), status=response.status_code)
        policy.record(host, ok=response.ok)
        return response
