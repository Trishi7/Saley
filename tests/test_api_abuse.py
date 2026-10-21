"""API abuse suite (R1.1 ticket C, gate step 5).

Each test's first docstring line is the row text in docs/test-reports/<release>-api-abuse.md,
which scripts/verify.py writes from the junit result. Every row must pass or be explained.
"""

from __future__ import annotations

import re
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
from tests.conftest import SignedClient

LIST_ENDPOINTS = (
    "/api/people",
    "/api/accounts",
    "/api/goals",
    "/api/blockers",
    "/api/money-blocked",
    "/api/scorecard",
    "/api/inbox",
    "/api/commitments",
    "/api/decisions",
    "/api/signals",
    "/api/events",
    "/api/suggest/warm",
    "/api/suggest/best",
    "/api/suggest/newco",
    "/api/suggest/res",
)
ALL_GETS = (
    "/api/me",
    "/api/status",
    "/api/today",
    *LIST_ENDPOINTS,
    "/api/health/full",
    "/api/search?q=a",
    "/api/news/funnel",
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
    with SignedClient(create_app()) as c:
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
        "/static/app/..%2f..%2f..%2fpyproject.toml",  # the built app mount (D145)
        "/static/app/%2e%2e/%2e%2e/app.py",
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
    with SignedClient(create_app()) as c:
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
        with SignedClient(create_app(), raise_server_exceptions=False) as c:
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
    with SignedClient(create_app(), raise_server_exceptions=False) as c:
        r = c.get("/api/today")
    assert r.status_code == 500
    assert "secret" not in r.text and "select" not in r.text and "Traceback" not in r.text


def test_methods_other_than_get_are_refused(client: TestClient) -> None:
    """POST, PUT, PATCH and DELETE are refused on every endpoint except the act routes (R2)."""
    for path in ALL_GETS:
        bare = path.split("?")[0]
        for method in ("post", "put", "patch", "delete"):
            assert getattr(client, method)(bare).status_code in (404, 405), (method, bare)


def test_security_headers_on_every_response(client: TestClient) -> None:
    """Security headers on API, page and error responses; no CORS header anywhere."""
    for path in ("/", "/api/today", "/api/nope", "/static/signin.css", "/static/app.js"):
        r = client.get(path)
        assert "default-src 'self'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["cross-origin-resource-policy"] == "same-origin"
        assert "access-control-allow-origin" not in r.headers


ACT_BODY = {"action": "task_done", "target_ids": [1], "payload": {"task": "research"}}
ACT_PATHS = (
    "/api/act",
    "/api/undo/1",
    "/api/decision/1/confirm",
    "/api/proposal/1/dismiss",
    "/api/proposal/1/edit",  # R4 ticket 7
    "/api/commitment/1/done",
    "/api/seen",
    "/api/ask",  # R4 ticket 6
)


def test_act_posts_need_the_header(client: TestClient) -> None:
    """Every act POST without the X-Saley-Act header is refused with 403."""
    for path in ACT_PATHS:
        assert client.post(path, json=ACT_BODY).status_code == 403, path
        assert client.post(path, json=ACT_BODY, headers={"X-Saley-Act": "0"}).status_code == 403


def test_act_posts_refuse_cross_site_calls(client: TestClient) -> None:
    """A cross-site or same-site page cannot call an act route, even with the header."""
    headers = {"X-Saley-Act": "1"}
    for path in ACT_PATHS:
        evil = {**headers, "Origin": "https://evil.example"}
        assert client.post(path, json=ACT_BODY, headers=evil).status_code == 403, path
        cross = {**headers, "Sec-Fetch-Site": "cross-site"}
        assert client.post(path, json=ACT_BODY, headers=cross).status_code == 403, path
        broken = {**headers, "Origin": "https://[x"}
        assert client.post(path, json=ACT_BODY, headers=broken).status_code == 403, path


def test_act_unknown_action_and_huge_lists_are_422(client: TestClient) -> None:
    """An unknown action, a huge target list, odd fields or a bad id are rejected with 422."""
    headers = {"X-Saley-Act": "1"}
    bad_bodies = [
        {**ACT_BODY, "action": "delete_everything"},
        {**ACT_BODY, "action": "row_add"},
        {**ACT_BODY, "target_ids": list(range(1, 52))},
        {**ACT_BODY, "target_ids": []},
        {**ACT_BODY, "target_ids": [0]},
        {**ACT_BODY, "target_ids": ["1 or 1=1"]},
        {**ACT_BODY, "target_ids": [2**40]},
        {**ACT_BODY, "extra": "x"},
        {**ACT_BODY, "payload": {"task": "research", "sql": "drop table contact"}},
        {**ACT_BODY, "payload": {"task": "nope"}},
        {"action": "write", "target_ids": [1], "payload": {"role": "Bad Role!", "value": "x"}},
        {
            "action": "write",
            "target_ids": [1],
            "payload": {"cells": [{"role": "a", "value": "b"}] * 6},
        },
    ]
    for body in bad_bodies:
        assert client.post("/api/act", json=body, headers=headers).status_code == 422, body
    assert client.post("/api/act", content=b"not json", headers=headers).status_code == 422
    for path in (
        "/api/undo/0",
        "/api/undo/abc",
        "/api/decision/-1/confirm",
        "/api/proposal/x/dismiss",
        "/api/proposal/x/edit",
        "/api/commitment/0/done",
    ):
        assert client.post(path, headers=headers).status_code == 422, path


def test_act_routes_accept_only_post(client: TestClient) -> None:
    """GET, PUT, PATCH and DELETE on the act routes are refused; other POST paths are 404."""
    for path in ACT_PATHS:
        for method in ("get", "put", "patch", "delete"):
            assert getattr(client, method)(path).status_code in (404, 405), (method, path)
    assert client.post("/api/act/extra", headers={"X-Saley-Act": "1"}).status_code == 404
    assert client.post("/api/people", headers={"X-Saley-Act": "1"}).status_code in (404, 405)


def test_every_api_route_refuses_a_call_without_a_session(synced: Session) -> None:
    """Every /api route, every method, answers 401 to a call with no session (R4, D114)."""
    app = create_app()
    seen: list[str] = []
    with TestClient(app) as bare:  # not signed in: the loopback bypass is gone
        for path, methods in app.openapi()["paths"].items():  # the app hides /openapi.json
            if not path.startswith("/api"):
                continue
            url = re.sub(r"\{[^}]+\}", "1", path)
            for method in methods:
                r = bare.request(method.upper(), url, headers={"X-Saley-Act": "1"})
                assert r.status_code == 401, (method, path, r.status_code)
                assert r.json() == {"detail": "Sign in to use Saley."}, (method, path)
                seen.append(f"{method} {path}")
    assert len(seen) >= len(ALL_GETS), seen  # no route skipped quietly


def test_act_post_needs_a_signed_in_local_user(client: TestClient) -> None:
    """The X-Saley-As header works only with the mock sign-in on this machine (D50, D116)."""
    headers = {"X-Saley-Act": "1", "X-Saley-As": "Sid; drop table users"}
    assert client.post("/api/act", json=ACT_BODY, headers=headers).status_code == 403
    away = TestClient(client.app, client=("203.0.113.9", 5000))
    assert away.post("/api/act", json=ACT_BODY, headers={"X-Saley-Act": "1"}).status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/inbox?type=write",
        "/api/inbox?type=",
        "/api/commitments?state=overdue",
        "/api/commitments?owner=SID",
        "/api/commitments?owner=sid;drop",
        "/api/decisions?surface=email",
        "/api/decisions?undo=maybe",
        "/api/decisions?state=deleted",
        "/api/decisions?person=1%3Bdrop",
        "/api/decisions?person=" + "a" * 21,
    ],
)
def test_team_view_filters_are_a_closed_list(client: TestClient, path: str) -> None:
    """Inbox, Commitments and Decisions refuse a filter value outside their closed list."""
    assert client.get(path).status_code == 422, path


PACK_KEYS = {
    "sync_id",
    "last_computed",
    "last_computed_label",
    "stale",
    "week_start",
    "built_at",
    "body",
}
PACK_BODY_KEYS = {
    "week_start",
    "last_week_day",
    "team",
    "scorecard",
    "late_total",
    "late",
    "packed_total",
    "packed_reminders",
    "tasks",
}
SCORE_KEYS = {
    "industry",
    "people",
    "contacted",
    "connect_pct",
    "reply_pct",
    "reply_note",
    "meetings",
    "deals",
    "last_week",
    "last_week_note",
}


def test_r4_pack_and_scorecard_contract(client: TestClient, synced: Session) -> None:
    """/api/pack and /api/scorecard return allowed fields only; bad week 422; no session 401."""
    from datetime import date

    from saley.intel import pack

    monday = date(2026, 10, 19)
    pack.build_weekly_pack(synced, now=now_ist().replace(year=2026, month=10, day=19, hour=14))
    synced.commit()
    app = client.app
    assert "/api/pack" in app.openapi()["paths"]  # the no-session sweep above reaches it
    # Allowed fields only: no database ids, no post ids, nothing the schema does not list.
    body = client.get("/api/pack").json()
    assert set(body) == PACK_KEYS and body["week_start"] == str(monday)
    assert set(body["body"]) == PACK_BODY_KEYS
    assert all(set(r) == SCORE_KEYS for r in body["body"]["scorecard"])
    assert client.get(f"/api/pack?week={monday}").json() == body
    items = client.get("/api/scorecard").json()["items"]
    assert items and all(set(r) == SCORE_KEYS for r in items)
    # A bad week is 422 (fixed text, the input is not echoed); a week with no pack is 404.
    for bad in ("abc", "2026-13-40", "2026-10-20", "2026-10-19%3Bdrop", "1e9", "", "x" * 300):
        r = client.get(f"/api/pack?week={bad}")
        assert r.status_code == 422, bad
        assert bad not in r.text or bad == ""
    missing = client.get("/api/pack?week=2020-01-06")
    assert missing.status_code == 404 and missing.json() == {"detail": "No pack for that week."}
    assert client.get("/api/pack?week=2026-10-19&x=1").status_code == 422
    assert client.get("/api/pack?limit=1").status_code == 422
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/api/pack").status_code in (404, 405)
    with TestClient(app) as bare:
        for path in ("/api/pack", "/api/scorecard"):
            r = bare.get(path)
            assert r.status_code == 401 and r.json() == {"detail": "Sign in to use Saley."}


def test_r4_pack_with_no_pack_is_404(client: TestClient) -> None:
    """With no pack built yet, /api/pack is a plain 404 with fixed text."""
    r = client.get("/api/pack")
    assert r.status_code == 404 and r.json() == {"detail": "No pack for that week."}


@pytest.mark.parametrize(
    "value", ["abc", "1.5", "", "1e9", "0x10", "-1", "1001", "99999999999999999999"]
)
def test_tasks_offset_bad_values_are_422(client: TestClient, value: str) -> None:
    """R5 T3: tasks_offset is a whole number from 0 to 1000. Anything else is refused."""
    assert client.get(f"/api/today?tasks_offset={value}").status_code == 422


@pytest.mark.parametrize("value", ["0", "10", "1000"])
def test_tasks_offset_edges_are_accepted(client: TestClient, value: str) -> None:
    assert client.get(f"/api/today?tasks_offset={value}").status_code == 200


def test_done_task_name_only_and_never_a_client_value(client: TestClient) -> None:
    """R5 T3 (D153): a column Done names a task; a value beside it, or a bad name, is 422."""
    head = {"X-Saley-Act": "1"}

    def post(payload: dict) -> int:
        body = {"action": "write", "target_ids": [1], "payload": payload}
        return client.post("/api/act", json=body, headers=head).status_code

    assert post({"done": "nope"}) == 422
    assert post({"done": "meeting", "value": "Done", "role": "meeting_status"}) == 422
    assert post({"done": "meeting", "cells": [{"role": "meeting_status", "value": "x"}]}) == 422
    assert post({"done": "meeting", "fields": [{"role": "name", "value": "x"}]}) == 422


BAD_EMAILS = [
    "a@b.com,c@d.com",
    "a@b.com;c@d.com",
    "a​@b.com",  # zero-width space
    "a@b.com‮",  # bidi override
    "a@.com",
    "a@b",
    "a" * 195 + "@b.com",  # 201 characters
    "a@b.com\r\nBcc: x@y.com",
    "é@b.com",
]


def test_email_write_value_abuse(
    client: TestClient, synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T4 review (D154, D169): list, invisible, non-ASCII, CRLF, filled and pending cases."""
    from tests.test_web import _email_write, _no_writes, _ready_for_add_email

    monkeypatch.setenv("EMAIL_WRITE_ALLOWED", "true")
    get_settings.cache_clear()
    try:
        cid = _ready_for_add_email(synced)
        for value in BAD_EMAILS:
            assert _email_write(client, cid, value).status_code == 422, repr(value)
        assert _no_writes(synced)

        first = _email_write(client, cid, "ro.one@example.com")  # a pending earlier add
        assert first.status_code == 200, first.text
        again = _email_write(client, cid, "other@example.com")
        assert again.status_code == 409
        assert again.json()["detail"] == "That person already has an email."
        count = "select count(*) from decision where role = 'email'"
        assert synced.execute(text(count)).scalar_one() == 1
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


TABBED = ("/api/signals", "/api/events", "/api/suggest/warm", "/api/suggest/res")


@pytest.mark.parametrize("path", TABBED)
@pytest.mark.parametrize("value", ["all", "", "New", "SEEN", "new;seen", "1", "new%20", "../x"])
def test_view_outside_new_or_seen_is_422(client: TestClient, path: str, value: str) -> None:
    """R5 T7: view takes new or seen only. Anything else is refused."""
    assert client.get(path, params={"view": value}).status_code == 422


@pytest.mark.parametrize("path", TABBED)
@pytest.mark.parametrize("view", ["new", "seen"])
@pytest.mark.parametrize("value", ["abc", "1.5", "", "-1", "1001", "99999999999999999999"])
def test_tab_offset_is_a_whole_number_from_0_to_1000(
    client: TestClient, path: str, view: str, value: str
) -> None:
    """R5 T7: in a tabbed list the offset is 0 to 1000."""
    assert client.get(path, params={"view": view, "offset": value}).status_code == 422


@pytest.mark.parametrize("path", TABBED)
@pytest.mark.parametrize("value", ["0", "10", "1000"])
def test_tab_offset_edges_are_accepted(client: TestClient, path: str, value: str) -> None:
    assert client.get(path, params={"view": "new", "offset": value}).status_code == 200


@pytest.mark.parametrize(
    "value",
    ["F", "A", "L", "Z", "", "Hiring;", "1 or 1=1", "Fund", "Product  launch", "x" * 41, "%00"],
)
def test_cat_letters_and_unknown_text_are_422(client: TestClient, value: str) -> None:
    """R5 T7 (D156): cat takes a topic name. A letter or any other text is refused."""
    assert client.get("/api/signals", params={"cat": value}).status_code == 422


@pytest.mark.parametrize(
    "value",
    [
        "Hiring",
        "Funding",
        "Product launch",
        "Research",
        "Partnership",
        "Event",
        "Other",
        "hiring",
        "PRODUCT LAUNCH",
    ],
)
def test_cat_names_are_accepted_in_any_case(client: TestClient, value: str) -> None:
    assert client.get("/api/signals", params={"cat": value}).status_code == 200


def test_r5_contract(client: TestClient, synced: Session) -> None:
    """R5-X04: the R5 params and routes. Allowed fields only, 422 for bad input, 401 unsigned."""
    # tasks_offset: a whole number from 0 to 1000.
    for bad in ("abc", "1.5", "", "-1", "1001", "99999999999999999999"):
        assert client.get("/api/today", params={"tasks_offset": bad}).status_code == 422, bad
    for good in ("0", "10", "1000"):
        assert client.get("/api/today", params={"tasks_offset": good}).status_code == 200, good
    tasks = client.get("/api/today", params={"tasks_offset": "0"}).json()["my_tasks"]
    assert {"offset", "more", "more_label"} <= set(tasks)
    # view and offset on the tabbed lists; cat takes topic names only.
    for path in TABBED:
        for bad in ("all", "", "New", "new;seen"):
            assert client.get(path, params={"view": bad}).status_code == 422, (path, bad)
        for bad in ("-1", "1001", "x"):
            assert client.get(path, params={"view": "new", "offset": bad}).status_code == 422
        body = client.get(path, params={"view": "seen", "offset": "0"})
        assert body.status_code == 200 and {"new_total", "seen_total"} <= set(body.json()), path
    for bad in ("F", "Z", "", "Hiring;", "x" * 41):
        assert client.get("/api/signals", params={"cat": bad}).status_code == 422, bad
    assert client.get("/api/signals", params={"cat": "product LAUNCH"}).status_code == 200
    # /api/me.features: exactly the six flags, all booleans.
    features = client.get("/api/me").json()["features"]
    assert set(features) == {
        "find_people",
        "email_write",
        "speaker_match",
        "apollo",
        "mail_replies",
        "dms",
    }
    assert all(isinstance(v, bool) for v in features.values())
    # /api/admin/news/run: the header, the methods, the fields, and a session.
    run = "/api/admin/news/run"
    assert client.post(run).status_code == 403
    assert client.post(run, headers={"X-Saley-Act": "0"}).status_code == 403
    assert client.post(run + "/extra", headers={"X-Saley-Act": "1"}).status_code == 404
    for method in ("put", "delete", "patch"):
        assert getattr(client, method)(run, headers={"X-Saley-Act": "1"}).status_code == 405
    queued = client.post(run, headers={"X-Saley-Act": "1"}, json={"state": "done", "id": 9})
    assert queued.status_code == 200 and set(queued.json()) == {"ok", "request_id", "label"}
    latest = client.get(run).json()
    assert set(latest) == {"latest", "open", "label"}
    assert set(latest["latest"]) == {
        "id",
        "requested_by",
        "requested_at",
        "state",
        "started_at",
        "finished_at",
        "note",
    }
    assert latest["latest"]["state"] == "queued"  # a body cannot set the state
    with TestClient(client.app) as bare:
        assert bare.get(run).status_code == 401
        assert bare.post(run, headers={"X-Saley-Act": "1"}).status_code == 401
