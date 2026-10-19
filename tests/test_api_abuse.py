"""API abuse suite (R1.1 ticket C, gate step 5).

Each test's first docstring line is the row text in docs/test-reports/<release>-api-abuse.md,
which scripts/verify.py writes from the junit result. Every row must pass or be explained.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.core.clock import now_ist
from saley.core.settings import get_settings
from saley.sales.sheet import build_tabs
from saley.sales.sync import run_sync
from saley.web import app as web_app
from saley.web.app import create_app

LIST_ENDPOINTS = (
    "/api/people",
    "/api/accounts",
    "/api/goals",
    "/api/blockers",
    "/api/money-blocked",
    "/api/scorecard",
)
ALL_GETS = (
    "/api/me",
    "/api/status",
    "/api/today",
    *LIST_ENDPOINTS,
    "/api/health/full",
    "/api/search?q=a",
)
SECRETS = {
    "GTM_SHEET_ID": "SHEET-ID-must-not-leak-77",
    "DISCORD_BOT_TOKEN": "discord-token-must-not-leak-77",
    "GOOGLE_SERVICE_ACCOUNT_FILE": "C:/keys/service-account-must-not-leak.json",
    "ANTHROPIC_API_KEY": "sk-ant-must-not-leak-77",
    "WEB_SESSION_SECRET": "session-secret-must-not-leak-77",
}


@pytest.fixture
def client(synced: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


@pytest.mark.parametrize("path", LIST_ENDPOINTS)
@pytest.mark.parametrize("value", ["abc", "1.5", "", "1e9", "0x10"])
def test_wrong_types_are_422(client: TestClient, path: str, value: str) -> None:
    """Wrong types in limit and offset are rejected with 422."""
    assert client.get(f"{path}?limit={value}").status_code == 422
    assert client.get(f"{path}?offset={value}").status_code == 422


@pytest.mark.parametrize("path", LIST_ENDPOINTS)
def test_huge_limits_are_422(client: TestClient, path: str) -> None:
    """A limit above 1000 or an offset above 100000 is rejected."""
    assert client.get(f"{path}?limit=1001").status_code == 422
    assert client.get(f"{path}?limit=99999999999999999999").status_code == 422
    assert client.get(f"{path}?offset=100001").status_code == 422


@pytest.mark.parametrize("path", LIST_ENDPOINTS)
def test_negative_offsets_are_422(client: TestClient, path: str) -> None:
    """Negative offset and zero or negative limit are rejected."""
    assert client.get(f"{path}?offset=-1").status_code == 422
    assert client.get(f"{path}?limit=0").status_code == 422
    assert client.get(f"{path}?limit=-5").status_code == 422


@pytest.mark.parametrize(
    "q",
    [
        "'; drop table contact; --",
        "' or 1=1 --",
        "%",
        "_",
        "\\",
        "<script>alert(1)</script>",
        '" onmouseover="alert(1)',
        "../../etc/passwd",
        "ñandú 名前 😀",
    ],
)
def test_sql_and_script_text_is_just_data(client: TestClient, q: str) -> None:
    """SQL, wildcards and script text in search come back as data, never run."""
    for path in ("/api/search", "/api/people?filter=all", "/api/accounts"):
        sep = "&" if "?" in path else "?"
        r = client.get(
            f"{path}{sep}q={q}" if q not in {"%", "_", "\\"} else path,
            params={"q": q} if q in {"%", "_", "\\"} else None,
        )
        assert r.status_code == 200, (path, q, r.status_code)
        assert "<script>" not in r.headers.get("content-type", "")
    assert client.get("/api/people?filter=all").json()["total"] == 7  # nothing dropped


def test_search_too_long_is_422(client: TestClient) -> None:
    """Search text over 100 characters is rejected, not cut silently."""
    assert client.get("/api/search", params={"q": "x" * 101}).status_code == 422
    assert client.get("/api/people", params={"filter": "all", "q": "x" * 101}).status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/static/..%2f..%2fpyproject.toml",
        "/static/%2e%2e/%2e%2e/pyproject.toml",
        "/static/../app.py",
        "/static/..\\..\\pyproject.toml",
        "/api/people/..%2F1",
        "/api/people/1%2F..%2F2",
        "/api/accounts/-1",
        "/api/people/99999999999999999999",
    ],
)
def test_path_traversal_is_refused(client: TestClient, path: str) -> None:
    """Path traversal and odd ids never return a file or a row."""
    r = client.get(path)
    assert r.status_code in (400, 404, 422), (path, r.status_code)
    assert "[project]" not in r.text and "def create_app" not in r.text


def test_unknown_parameters_are_422(client: TestClient) -> None:
    """Every endpoint rejects a parameter it does not declare."""
    for path in ALL_GETS:
        sep = "&" if "?" in path else "?"
        r = client.get(f"{path}{sep}debug=true")
        assert r.status_code == 422, path


def test_unknown_filter_is_422(client: TestClient) -> None:
    """A filter outside the closed list is rejected."""
    for bad in ("stage:Nope", "1;drop table contact", "goal:G1 or 1=1", "account:abc"):
        assert client.get("/api/people", params={"filter": bad}).status_code == 422


def test_fifty_parallel_requests(client: TestClient) -> None:
    """50 parallel requests all succeed: no 5xx, no pool exhaustion."""
    paths = [ALL_GETS[i % len(ALL_GETS)] for i in range(50)]
    with ThreadPoolExecutor(max_workers=50) as pool:
        codes = list(pool.map(lambda p: client.get(p).status_code, paths))
    assert codes == [200] * 50


def test_sheet_change_during_a_request(synced: Session, raw_sheet: dict[str, Any]) -> None:
    """A sync that commits mid-request does not change what that request reads."""
    gen = web_app.db()
    conn = next(gen)
    before_id = conn.info["sync_id"]
    before = conn.execute(text("select count(*) from v_contact_stage")).scalar_one()
    raw_sheet["Outreach PoCs"].pop()  # the sheet changes: one person fewer
    run_sync(synced, build_tabs(raw_sheet), now_ist())
    synced.commit()
    during = conn.execute(text("select count(*) from v_contact_stage")).scalar_one()
    snapshot_during = conn.execute(text("select max(id) from sheet_snapshot")).scalar_one()
    with pytest.raises(StopIteration):
        next(gen)
    assert (during, snapshot_during) == (before, before_id)
    with TestClient(create_app()) as c:
        after = c.get("/api/people?filter=all").json()
    assert after["total"] == before - 1 and after["sync_id"] > before_id


def test_expired_sync_id_is_409(client: TestClient, synced: Session, raw_sheet: dict) -> None:
    """A client that sends an old sync_id gets 409 and the current id."""
    old = client.get("/api/today").json()["sync_id"]
    raw_sheet["Outreach PoCs"].pop()
    run_sync(synced, build_tabs(raw_sheet), now_ist())
    synced.commit()
    r = client.get(f"/api/people?sync_id={old}")
    assert r.status_code == 409
    assert r.json()["sync_id"] > old
    assert client.get("/api/people?sync_id=abc").status_code == 422
    assert client.get("/api/people?sync_id=0").status_code == 422


def test_no_secrets_in_any_response(synced: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """No env value (sheet id, tokens, key path, DB URL) appears in any response or error."""
    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    try:
        db_url = get_settings().database_url
        with TestClient(create_app(), raise_server_exceptions=False) as c:
            bodies = [c.get(p).text for p in ALL_GETS]
            bodies += [
                c.get(p).text
                for p in (
                    "/api/people/999999",
                    "/api/people?filter=x",
                    "/api/people?limit=x",
                    "/api/x",
                    "/static/x",
                )
            ]
            bodies += [c.post("/api/people").text]
            bodies += [c.get("/api/today", headers={"Origin": "https://evil.example"}).text]
    finally:
        get_settings.cache_clear()
    joined = "\n".join(bodies)
    for value in [*SECRETS.values(), db_url, db_url.split("@")[0]]:
        assert value not in joined
    assert "Traceback" not in joined and 'File "' not in joined


def test_server_error_has_no_stack_trace(synced: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """An internal error returns a plain 500 with no trace or SQL."""

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("postgresql://saley:secret@db/saley select * from contact")

    monkeypatch.setattr(web_app.queries, "today_numbers", boom)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        r = c.get("/api/today")
    assert r.status_code == 500
    assert "secret" not in r.text and "select" not in r.text and "Traceback" not in r.text


def test_methods_other_than_get_are_refused(client: TestClient) -> None:
    """POST, PUT, PATCH and DELETE are refused on every endpoint: R1 has no writes."""
    for path in ALL_GETS:
        bare = path.split("?")[0]
        for method in ("post", "put", "patch", "delete"):
            assert getattr(client, method)(bare).status_code in (404, 405), (method, bare)


def test_security_headers_on_every_response(client: TestClient) -> None:
    """Security headers on API, page and error responses; no CORS header anywhere."""
    for path in ("/", "/api/today", "/api/nope", "/static/app.js"):
        r = client.get(path)
        assert "default-src 'self'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["cross-origin-resource-policy"] == "same-origin"
        assert "access-control-allow-origin" not in r.headers
