"""saley.web: every R1 endpoint on the synced fixture sheet, read-only, dev sign-in."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.core.clock import today_ist
from saley.core.settings import get_settings
from saley.web import schemas
from saley.web.app import SignedIn, create_app, current_user
from tests.conftest import SignedClient, require_ro_role


@pytest.fixture
def client(synced: Session) -> Iterator[TestClient]:
    with SignedClient(create_app()) as c:
        yield c


def tiles(client: TestClient) -> dict[str, dict]:
    return {t["key"]: t for t in client.get("/api/today?user=vaishnavi").json()["tiles"]}


def test_today_tiles(client: TestClient) -> None:
    body = client.get("/api/today?user=vaishnavi").json()
    assert body["greeting"].endswith(", Vaishnavi")
    t = {x["key"]: x for x in body["tiles"]}
    assert t["deals"]["value"] == "0 of 3"
    assert t["deals"]["detail"].endswith("days left")
    assert t["live"]["value"] == 2
    assert (t["backlog"]["value"], t["backlog"]["detail"]) == (1, "1 of them have an email")
    assert (t["p1"]["value"], t["p1"]["detail"]) == (1, "Of 2 marked P1")
    assert all(tile["source"] for tile in t.values())
    assert (t["week"]["value"], t["week"]["detail"]) == (1, "Last week 1")
    assert t["late"]["value"] == 2
    assert "NDA" in t["late"]["detail"]
    assert body["last_computed"] is not None


@pytest.mark.parametrize("key", ["live", "backlog", "p1", "week"])
def test_tile_number_equals_its_list(client: TestClient, key: str) -> None:
    tile = tiles(client)[key]
    listed = client.get(f"/api/people?filter={tile['link']['filter']}").json()
    assert listed["total"] == tile["value"] == len(listed["items"])


def test_late_tile_matches_blockers(client: TestClient) -> None:
    late = client.get("/api/blockers").json()["late"]
    assert tiles(client)["late"]["value"] == len([b for b in late if b["blocks_goals"]])


def test_people_rows_carry_evidence_and_touches(client: TestClient) -> None:
    body = client.get("/api/people?filter=backlog").json()
    row = body["items"][0]
    assert row["name"] == "Dinesh Tewari"
    assert row["evidence"] == {"row": "Outreach PoCs:1:dinesh tewari"}
    assert {t["type"] for t in row["last_touches"]} <= {"first_contact", "li_req", "li_accept"}
    assert body["counts"]["people"] == 7


def test_people_filters(client: TestClient) -> None:
    def names(f: str, q: str = "") -> set[str]:
        return {p["name"] for p in client.get(f"/api/people?filter={f}&q={q}").json()["items"]}

    assert names("in_conversation") == {"Sshubam Verma", "David Gamez"}
    assert names("p1_waiting") == {"Alex Search"}
    assert "Pat Park" in names("reconnect_due")
    assert names("stage:Listed") == {"Ro One", "Ro Two"}
    assert names("all", "sarvam") == {"Sshubam Verma"}
    assert names("all", "%") == set()  # wildcards are escaped


def test_bad_filter_is_422(client: TestClient) -> None:
    assert client.get("/api/people?filter=stage:Nope").status_code == 422
    assert client.get("/api/people?filter=1;drop table contact").status_code == 422
    assert client.get("/api/accounts?filter=goal:G1 or 1=1").status_code == 422


def test_injection_text_is_just_text(client: TestClient) -> None:
    r = client.get("/api/people", params={"filter": "all", "q": "'; drop table contact; --"})
    assert r.status_code == 200 and r.json()["total"] == 0
    assert client.get("/api/people?filter=all").json()["total"] == 7


def test_accounts_and_filters(client: TestClient) -> None:
    body = client.get("/api/accounts").json()
    names = {a["name"] for a in body["items"]}
    assert {"Google", "Google DeepMind", "AI4Bharat", "Rosebud AI"} <= names
    deepmind = next(a for a in body["items"] if a["name"] == "Google DeepMind")
    assert (deepmind["people"], deepmind["connected"], deepmind["p1_count"]) == (1, 1, 1)
    assert deepmind["goal_codes"] == ["G1", "G2", "G4"]
    assert deepmind["evidence"]["row"].startswith("Master Pipeline:")
    p1 = {a["name"] for a in client.get("/api/accounts?filter=p1").json()["items"]}
    assert p1 == {"Google", "Google DeepMind"}
    g5 = {a["name"] for a in client.get("/api/accounts?filter=goal:G5").json()["items"]}
    assert g5 == {"Middlesex University London"}
    voice = client.get("/api/accounts?filter=industry:voice").json()["items"]
    assert [a["name"] for a in voice] == ["Sarvam AI"]
    assert body["industries"][-1]["industry"] == "No industry"


def test_drawers(client: TestClient) -> None:
    people = client.get("/api/people?filter=all&q=david").json()["items"]
    person = client.get(f"/api/people/{people[0]['id']}").json()["item"]
    assert person["stage_history"][0]["stage"] == "Qualified"
    assert any(t["type"] == "meeting" for t in person["touches"])
    assert person["opportunity"]["prob"] == 60
    account = client.get(f"/api/accounts/{people[0]['account_id']}").json()["item"]
    assert account["people_list"][0]["name"] == "David Gamez"
    assert client.get("/api/people/999999").status_code == 404
    assert client.get("/api/accounts/999999").status_code == 404


def test_goals(client: TestClient) -> None:
    goals = {g["code"]: g for g in client.get("/api/goals").json()["items"]}
    assert goals["G1"]["value_text"] == "USD 30–50k"
    assert [b["item"] for b in goals["G1"]["blockers"]] == [
        "NDA",
        "Pulse Product Overview Document",
    ]
    assert goals["G5"]["active_count"] == 1 and goals["G5"]["rag"] == "on track"
    assert goals["G5"]["active"][0]["row"] == "Outreach PoCs:4:david gamez"
    assert (goals["G3"]["active_count"], goals["G3"]["rag"]) == (1, "at risk")
    assert goals["G1"]["rag"] == "blocked"
    assert goals["G5"]["weighted_usd"] == 18000.0


def test_blockers_and_packages(client: TestClient) -> None:
    body = client.get("/api/blockers").json()
    assert {b["item"] for b in body["late"]} == {"NDA", "Pulse Product Overview Document"}
    nda = next(b for b in body["late"] if b["item"] == "NDA")
    assert nda["late_by_days"] == 20 and nda["blocks_goals"] == ["G1", "G2", "G3"]
    assert nda["short"] == "NDA" and nda["due_label"]
    assert body["late_total"] == 2 and body["packages_total"] == 1
    assert [p["name"] for p in body["packages"]] == ["Hinglish STT training set"]
    assert client.get("/api/blockers?q=pulse").json()["late"][0]["owner"] == "Design"


def test_scorecard(client: TestClient) -> None:
    rows = {r["industry"]: r for r in client.get("/api/scorecard").json()["items"]}
    assert rows["AI Labs"]["people"] == 2
    assert rows["AI Labs"]["connect_pct"] == 50
    assert rows["No industry"]["people"] == 3
    assert rows["AI Labs"]["reply_pct"] is None


def test_health_full(client: TestClient) -> None:
    body = client.get("/api/health/full").json()
    assert any(j["job"] == "sheet_sync" for j in body["jobs"])
    first = body["news"]["steps"][0]  # R3: the news funnel, counted from the news tables
    assert (first["key"], first["step"], first["n"]) == ("tried", "Sources tried", 0)
    assert body["apollo"]["state"] == "off" and body["search"]["searxng"] == "not configured"
    assert body["missing_industry"] == 3
    assert any(q["problem"] == "bad_date" for q in body["quality"])
    assert body["llm"]["calls"] == 0


def test_search_groups(client: TestClient) -> None:
    body = client.get("/api/search?q=deepmind").json()
    groups = body["groups"]
    assert set(groups) == {"people", "companies", "goals", "deliverables", "decisions"}
    assert [p["name"] for p in groups["people"]["items"]] == ["Dinesh Tewari"]
    company = groups["companies"]["items"][0]
    assert company["name"] == "Google DeepMind" and company["match"] == [7, 8]
    assert groups["people"]["items"][0]["match"] is None  # matched in the company, not the name
    assert groups["people"]["items"][0]["pill"] == "Connected"
    nda = client.get("/api/search?q=nda").json()["groups"]["deliverables"]["items"][0]
    assert (nda["name"], nda["owner"]) == ("NDA", "Legal")
    decisions = client.get("/api/search?q=PostgreSQL").json()["groups"]["decisions"]["items"]
    assert decisions and decisions[0]["id"] == "D17"
    assert client.get("/api/search?q=zzzz-nothing").json()["total"] == 0
    assert client.get("/api/search?q=").status_code == 422


def test_status_me_and_static(client: TestClient) -> None:
    status = client.get("/api/status").json()
    assert status["news"] == "—" and status["sheet_mode"] == "copy"
    me = client.get("/api/me").json()
    assert [p["name"] for p in me["people"]] == ["Vaishnavi", "Sid", "Kushal"]
    page = client.get("/")  # the built app (test_web_assets), or the not-built line here
    assert page.status_code in (200, 503)
    assert client.get("/static/signin.css").status_code == 200  # the sign-in pages' style
    assert client.get("/static/app.js").status_code == 404  # the legacy app is gone (D224)
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert client.get("/api/status").headers["cache-control"] == "no-store"


def test_no_write_endpoints(client: TestClient) -> None:
    """Only the four act routes accept a POST (R2), and they refuse a call without the header."""
    for path in ("/api/people", "/api/accounts", "/api/today", "/api/health/full"):
        assert client.post(path, json={}).status_code in (404, 405)
    assert client.post("/api/act", json={}).status_code == 403


def test_outside_dev_every_api_call_is_refused(
    synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SALEY_ENV", "prod")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as c:
            assert c.post("/auth/mock").status_code == 404  # no mock sign-in outside dev
            assert c.get("/api/today").status_code == 401
            assert c.get("/api/health/full").status_code == 401
            assert c.get("/healthz").status_code == 200
    finally:
        monkeypatch.setenv("SALEY_ENV", "dev")
        get_settings.cache_clear()


# ---------------------------------------- review fixes: web


def test_dev_sign_in_only_from_this_machine(synced: Session) -> None:
    with TestClient(create_app(), client=("10.1.2.3", 5000)) as c:
        assert c.get("/api/today").status_code == 401
    with TestClient(create_app(), base_url="http://evil.example") as c:
        assert c.get("/api/today").status_code == 400  # TrustedHost blocks DNS rebinding


def test_viewer_cannot_open_health(synced: Session) -> None:
    app = create_app()
    app.dependency_overrides[current_user] = lambda: SignedIn("v@x", "viewer", "kushal", "mock")
    with TestClient(app) as c:
        assert c.get("/api/health/full").status_code == 403
        assert c.get("/api/today").status_code == 200


def test_headers_limits_and_no_openapi(synced: Session) -> None:
    with SignedClient(create_app()) as c:
        r = c.get("/api/status")
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert c.get("/api/people?limit=0").status_code == 422
        assert c.get("/api/people?limit=5000").status_code == 422
        assert c.get("/openapi.json").status_code == 404
        person = c.get("/api/people?filter=all").json()["items"][0]
        detail = c.get(f"/api/people/{person['id']}").json()["item"]
        assert "cells" not in detail and "x_handle" not in detail and "li_url" not in detail


def test_deals_target_is_empty_without_close_goals(synced: Session) -> None:
    synced.execute(text("update goal set text = 'Win ' || text"))
    synced.commit()
    with SignedClient(create_app()) as c:
        tile = next(t for t in c.get("/api/today").json()["tiles"] if t["key"] == "deals")
    assert tile["value"] == "0 of —"


# ---------------------------------------- R1.1 API contract (C) and fixes (A)


EXPECTED_KEYS = {
    "/api/today": {
        "my_tasks",
        "needs_look",
        "saley_suggests",
        "greeting",
        "date_label",
        "tiles",
        "funnel",
        "blockers_line",
        "sync_id",
        "last_computed",
        "last_computed_label",
        "stale",
    },
    "/api/status": {
        "state",
        "message",
        "updated",
        "sheet",
        "news",
        "news_old",
        "sheet_mode",
        "late_total",
        "pending_writes",
        "pending",
        "held",
        "failed",
        "write_note",
        "write_bad",
        "sync_id",
        "last_computed",
        "last_computed_label",
        "stale",
    },
    "/api/money-blocked": {
        "items",
        "total",
        "total_usd",
        "no_value",
        "sync_id",
        "last_computed",
        "last_computed_label",
        "stale",
    },
}


@pytest.mark.parametrize("path", sorted(EXPECTED_KEYS))
def test_responses_carry_only_declared_fields(client: TestClient, path: str) -> None:
    assert set(client.get(path).json()) == EXPECTED_KEYS[path]


def test_person_drawer_fields(client: TestClient) -> None:
    pid = client.get("/api/people?filter=all&q=dinesh").json()["items"][0]["id"]
    item = client.get(f"/api/people/{pid}").json()["item"]
    assert set(item) == set(schemas.PersonDetail.model_fields)
    assert (item["email"], item["email_href"]) == (
        "dinesh@example.com",
        "mailto:dinesh@example.com",
    )
    assert item["li_href"] is None and item["site_href"] is None  # nothing on file in the fixture


def test_every_response_has_the_same_sync_id(client: TestClient) -> None:
    ids = {client.get(p).json()["sync_id"] for p in ("/api/today", "/api/goals", "/api/people")}
    assert len(ids) == 1 and None not in ids


def test_old_sync_id_gets_409(client: TestClient) -> None:
    current = client.get("/api/today").json()["sync_id"]
    assert client.get(f"/api/today?sync_id={current}").status_code == 200
    stale = client.get(f"/api/today?sync_id={current + 999}")
    assert stale.status_code == 409
    assert stale.json() == {"detail": "Data changed. Reload the page.", "sync_id": current}


def test_unknown_parameter_is_rejected(client: TestClient) -> None:
    r = client.get("/api/people?filter=all&debug=1")
    assert r.status_code == 422 and r.json()["detail"] == "Unknown parameter."
    assert "debug" not in r.text  # A4: the key is logged, never echoed
    assert client.get("/api/today?user=sid").status_code == 200  # declared alias is fine


def test_cross_site_request_is_refused(client: TestClient) -> None:
    assert client.get("/api/today", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/today", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    same = client.get("/api/today", headers={"Origin": "http://testserver"})
    assert same.status_code == 200
    assert "access-control-allow-origin" not in same.headers


def test_status_bar_states(synced: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from saley.web import app as web_app

    with SignedClient(create_app()) as c:
        body = c.get("/api/status").json()
        assert body["state"] == "ok" and body["message"].startswith("Updated ")
        synced.execute(
            text(
                "update job_runs set started_at = now() - interval '1 hour', "
                "finished_at = now() - interval '1 hour'"
            )
        )
        synced.commit()
        down = c.get("/api/status").json()
        assert down["state"] == "worker_down"
        assert down["message"].startswith("Worker not running since ")
        assert down["stale"] is True
        synced.execute(
            text(
                "insert into job_runs(job, status, started_at) "
                "values ('sheet_sync', 'failed', now())"
            )
        )
        synced.commit()
        failing = c.get("/api/status").json()
        assert failing["state"] == "sync_failing"
        assert failing["message"].startswith("Sheet sync failing since ")
    assert web_app.STALE_FACTOR == 3


def test_no_sync_yet_shows_no_data_not_zero(db: Session) -> None:
    with SignedClient(create_app()) as c:
        tiles = c.get("/api/today").json()["tiles"]
    assert all(
        t["value"] is None and t["detail"] == "No data yet. The sheet has not synced."
        for t in tiles
    )


def test_scorecard_reply_has_no_source(client: TestClient) -> None:
    row = client.get("/api/scorecard").json()["items"][0]
    assert row["reply_pct"] is None and row["reply_note"] == "No source yet"


def test_money_blocked_total_equals_the_list(db: Session, raw_sheet: dict) -> None:
    from saley.core.clock import now_ist
    from saley.sales.sheet import build_tabs
    from saley.sales.sync import run_sync
    from tests.conftest import POC_HEADERS

    rows = raw_sheet["Outreach PoCs"]
    rows[3][POC_HEADERS.index("Estd. Deal Size (USD)")] = "USD 10–20k"  # Sshubam, voice → G3
    rows[3][POC_HEADERS.index("Status")] = "Demo"
    rows[1][POC_HEADERS.index("Status")] = "Lead"  # Dinesh, labs → G1/G2/G4, no value
    run_sync(db, build_tabs(raw_sheet), now_ist())
    db.execute(
        text(
            "insert into job_runs(job, status, started_at, finished_at) "
            "values ('sheet_sync', 'ok', now(), now())"
        )
    )
    db.commit()
    with SignedClient(create_app()) as c:
        money = c.get("/api/money-blocked").json()
        tile = c.get("/api/blockers").json()["money_tile"]
    known = [m["size_usd"] for m in money["items"] if m["size_usd"] is not None]
    assert money["total_usd"] == sum(known) == 15000  # D42: USD 10–20k counts 15k
    assert money["total"] == len(money["items"]) == 2
    assert money["no_value"] == 1
    no_value = next(m for m in money["items"] if m["size_usd"] is None)
    assert no_value["value_label"] == "No value on file"
    sshubam = next(m for m in money["items"] if m["name"] == "Sshubam Verma")
    assert sshubam["value_label"] == "≈ USD 15k (USD 10–20k)"
    assert sshubam["deliverable"] == "NDA" and sshubam["owner"] == "Legal"
    assert (tile["value"], tile["detail"]) == ("USD 15,000", "Deals with no value on file: 1")


def test_goal_card_fields(client: TestClient) -> None:
    g1 = next(g for g in client.get("/api/goals").json()["items"] if g["code"] == "G1")
    assert g1["why"] == "2 deliverables late"
    assert [b["short"] for b in g1["blockers"]] == ["NDA", "Pulse Product Overview Document"]
    assert g1["blockers"][0]["owner"] == "Legal"
    assert g1["weighted_usd"] is None
    assert g1["weighted_note"] == "≈ USD 0 · deal size fields are empty"


@pytest.mark.parametrize(
    ("item", "short"),
    [
        ("NDA - to be prepared and vetted by legal", "NDA"),
        (
            "Client on-boarding T&Cs, Privacy Policy, Acceptable Use & Research Safety Policy"
            " - to be incorporated on Pulse external dashboard",
            "Client on-boarding T&Cs + 2 policies",
        ),
        ("External Dashboard - Demo for PoCs", "External Dashboard"),
    ],
)
def test_short_label(item: str, short: str) -> None:
    from saley.web.app import short_label

    assert short_label(item) == short


@pytest.mark.parametrize(
    ("value", "href"),
    [
        ("https://www.linkedin.com/in/x", "https://www.linkedin.com/in/x"),
        ("javascript:alert(1)", None),
        ("http://plain.example", None),
        ("https://x.example/a b", None),
        ("@handle", None),
        (None, None),
    ],
)
def test_only_https_links(value: str | None, href: str | None) -> None:
    from saley.web.app import https_link

    assert https_link(value) == href


def test_bad_email_is_not_a_link() -> None:
    from saley.web.app import contact_links

    assert contact_links({"email": "not an email"})["email_href"] is None
    assert contact_links({"email": "a@b.co"})["email_href"] == "mailto:a@b.co"


def test_health_lists_tabs_and_new_columns(db: Session, raw_sheet: dict) -> None:
    from saley.core.clock import now_ist
    from saley.sales.sheet import build_tabs
    from saley.sales.sync import run_sync

    raw_sheet["Master Pipeline"][0] = [*raw_sheet["Master Pipeline"][0], "Brand new"]
    run_sync(db, build_tabs(raw_sheet), now_ist())
    db.commit()
    with SignedClient(create_app()) as c:
        tabs = {t["tab"]: t for t in c.get("/api/health/full").json()["tabs"]}
    assert tabs["master"]["new_headers"] == ["Brand new"]
    assert tabs["master"]["status"] == "ok"


def test_health_job_notes_never_show_passwords(synced: Session) -> None:
    synced.execute(
        text(
            "insert into job_runs(job, status, started_at, finished_at, note) values "
            "('x', 'failed', now(), now(), 'OperationalError: postgresql://u:hunter2@h/db')"
        )
    )
    synced.commit()
    with SignedClient(create_app()) as c:
        notes = [j["note"] for j in c.get("/api/health/full").json()["jobs"] if j["job"] == "x"]
    assert notes == ["OperationalError: postgresql://u:***@h/db"]


def test_value_label_band_midpoint_and_exact() -> None:
    """D42: a band shows its midpoint with the band text; an exact value shows as given."""
    from saley.web.app import usd_short, value_label

    assert value_label(40000, "USD 30–50k") == "≈ USD 40k (USD 30–50k)"
    assert value_label(40000, "40000") == "USD 40,000"
    assert value_label(None, "TBD") == "No value on file"
    assert (usd_short(1_750_000), usd_short(12_500), usd_short(900)) == ("1.75M", "12.5k", "900")
    assert usd_short(999_999) == "1M"


def test_money_value_is_never_zero_for_unknown() -> None:
    from saley.web.app import money_tile, money_value

    assert money_value({"total_usd": 0, "deals": 1, "no_value": 1}) is None
    assert money_value({"total_usd": 0, "deals": 0, "no_value": 0}) == "USD 0"
    assert money_value({"total_usd": 5000, "deals": 2, "no_value": 1}) == "USD 5,000"
    tile = money_tile({"total_usd": 0, "deals": 1, "no_value": 1})
    assert (tile["value"], tile["detail"]) == (None, "Deals with no value on file: 1")


# ------------------------------------------------------------------ R1.1 review fixes


def test_web_role_reads_only_views(synced: Session) -> None:
    """saley_ro has SELECT on v_* views and the two snapshot tables, and on no other table."""
    require_ro_role()
    # has_table_privilege sees grants by any owner; information_schema shows only some.
    readable = synced.execute(
        text(
            "select c.relname, c.relkind from pg_class c "
            "join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relkind in ('r', 'v', 'm', 'p') "
            "and has_table_privilege('saley_ro', c.oid, 'SELECT')"
        )
    ).all()
    tables = {name for name, kind in readable if kind != "v"}
    views = {name for name, kind in readable if kind == "v"}
    assert tables == {"sheet_snapshot", "sheet_tab_status"}
    assert {"v_contact_stage", "v_touch", "v_opportunity", "v_job_runs"} <= views
    # R2: the one extra right is EXECUTE on the saley_* act functions (tests/test_act.py, R2-G02).
    writable = synced.execute(
        text(
            "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relkind in ('r', 'v', 'm', 'p') "
            "and has_table_privilege('saley_ro', c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE')"
        )
    ).all()
    assert writable == []
    executable = synced.execute(
        text(
            "select p.proname from pg_proc p where p.pronamespace = 'public'::regnamespace "
            "and p.prosecdef and has_function_privilege('saley_ro', p.oid, 'EXECUTE')"
        )
    ).all()
    assert {name for (name,) in executable} == {
        "saley_act",
        "saley_undo",
        "saley_confirm",
        "saley_dismiss",
        "saley_commitment_done",
        "saley_mark_seen",
        "saley_request_search",
        "saley_session_start",
        "saley_session_end",
        "saley_ask_log",
        "saley_proposal_resolve",
        "saley_undo_group",
        "saley_request_news_run",
    }


def test_every_endpoint_works_as_the_real_web_role(
    synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API runs as saley_ro in production. Every endpoint must work with views only."""
    from saley.core.db import dispose_engines
    from saley.core.settings import get_settings
    from tests.conftest import RO_TEST_URL

    require_ro_role()
    person = synced.execute(text("select min(id) from contact")).scalar_one()
    account = synced.execute(text("select min(id) from account")).scalar_one()
    monkeypatch.setenv("DATABASE_URL_RO", RO_TEST_URL["url"])
    monkeypatch.setenv("FIND_PEOPLE_ENABLED", "true")  # D164
    get_settings.cache_clear()
    dispose_engines()
    try:
        from saley.core.db import ro_engine

        with ro_engine().connect() as conn:
            assert conn.execute(text("select current_user")).scalar_one() == "saley_ro"
        with SignedClient(create_app()) as c:
            for path in (
                "/api/me",
                "/api/status",
                "/api/today",
                "/api/goals",
                "/api/accounts",
                f"/api/accounts/{account}",
                "/api/people?filter=all",
                f"/api/people/{person}",
                "/api/blockers",
                "/api/money-blocked",
                "/api/scorecard",
                "/api/health/full",
                "/api/search?q=a",
                "/api/inbox",
                "/api/commitments",
                "/api/decisions",
                "/api/today?user=kushal",
                "/api/today?user=sid",
                # R3: every list below reads views only, as the real web role
                "/api/signals",
                "/api/signals?tab=linked&range=year&entity=a&src=b&cat=Funding&tag=Risk",
                "/api/signals?view=seen&min_importance=3&linked_only=true",
                "/api/events",
                "/api/events?when=past",
                "/api/suggest/warm",
                "/api/suggest/best",
                "/api/suggest/newco",
                "/api/suggest/res?view=seen",
                "/api/news/funnel",
            ):
                r = c.get(path)
                assert r.status_code == 200, (path, r.text)
            # R3: the two new POSTs reach the database only through their SQL functions
            headers = {"X-Saley-Act": "1", "X-Saley-As": "sid"}
            seen = c.post("/api/seen", json={"item_type": "story", "ids": [1]}, headers=headers)
            assert seen.status_code == 200 and seen.json()["marked"] == 1
            find = c.post(
                "/api/act", json={"action": "find_people", "target_ids": [account]}, headers=headers
            )
            assert find.status_code == 200 and find.json()["already"] is False
            added = c.post(
                "/api/act",
                json={
                    "action": "row_add",
                    "target_ids": [account],
                    "payload": {"fields": [{"role": "name", "value": "Role Test"}]},
                },
                headers=headers,
            )
            assert added.status_code == 200, added.text
    finally:
        get_settings.cache_clear()
        dispose_engines()


def test_stale_tab_turns_the_bar_red_for_everyone(client: TestClient, synced: Session) -> None:
    synced.execute(text("update sheet_tab_status set status = 'stale' where tab = 'deliverables'"))
    synced.commit()
    status = client.get("/api/status").json()
    assert status["state"] == "partial"
    assert status["message"].startswith("Old data: tab ")
    synced.execute(text("update sheet_tab_status set status = 'stale' where tab = 'goals'"))
    synced.commit()
    assert client.get("/api/status").json()["message"] == "Old data: 2 tabs not read"


def test_tiles_are_unknown_when_their_tab_was_never_read(
    client: TestClient, synced: Session
) -> None:
    synced.execute(text("update sheet_tab_status set last_good_sync_id = null where tab = 'poc'"))
    synced.commit()
    tiles = {t["key"]: t for t in client.get("/api/today").json()["tiles"]}
    for key in ("deals", "live", "backlog", "p1", "week"):
        assert tiles[key]["value"] is None, key
        assert tiles[key]["detail"] == "No data yet. Its sheet tab was never read."
    assert tiles["late"]["value"] is not None
    assert client.get("/api/blockers").json()["money_tile"]["value"] is None


def test_broken_urls_never_cause_a_500(client: TestClient) -> None:
    from saley.web.app import https_link

    assert https_link("https://[abc") is None
    assert https_link("https://google.com@evil.example/x") is None
    assert https_link("https://ok.example/a\x00b") is None
    assert https_link("https://ok.example/path") == "https://ok.example/path"
    r = client.get("/api/today", headers={"Origin": "https://[x"})
    assert r.status_code == 403
    assert client.get("/api/today", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert client.get("/api/today", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_server_error_keeps_security_headers(
    synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from saley.web import app as web_app

    def boom(*_args: object) -> None:
        raise RuntimeError("secret detail")

    monkeypatch.setattr(web_app.queries, "today_numbers", boom)
    with SignedClient(create_app(), raise_server_exceptions=False) as c:
        r = c.get("/api/today")
    assert r.status_code == 500
    assert r.json() == {"detail": "Server error. The error is in the log."}
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


# ---------------------------------------------------- R4 ticket 10: dashboard UI wiring
# Since the cut-over (R5 T16b, D224) the page is the app in web/ (built to dist/, served at /).
# These read its sources: the same guards the R4 tests kept on the legacy static files.

WEB_SRC = Path(schemas.__file__).parents[4] / "web" / "src"


def web_text(name: str) -> str:
    return (WEB_SRC / name).read_text(encoding="utf-8")


def web_sources() -> list[Path]:
    return sorted(p for p in WEB_SRC.rglob("*") if p.suffix in {".ts", ".tsx"})


def test_new_words_exist_and_fit_the_limit() -> None:
    words = web_text("words.ts")
    for key in ("packNone", "askSend", "askDown", "editSave", "undoneLine"):
        assert f"  {key}:" in words, key
    assert "overdue" not in words.lower() and "comes in R4" not in words


def test_new_views_write_server_text_with_textcontent_only() -> None:
    """No page writes server text as markup: React text only (the chart's style element is a
    comment in chart.tsx, never code)."""
    for path in web_sources():
        source = path.read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("//"))
        for bad in ("dangerouslySetInnerHTML", "innerHTML", "insertAdjacentHTML", "document.write"):
            assert bad not in code, (path.name, bad)
        assert "eval(" not in code, path.name
    ask = web_text("views/today/AskCard.tsx")
    assert "whitespace-pre-line" in ask  # the answer keeps its line breaks, as plain text


def test_ask_card_posts_to_the_one_fixed_path_with_the_act_header() -> None:
    ask = web_text("views/today/AskCard.tsx")
    assert ask.count('"/api/ask"') == 1 and "askDown" in ask
    api = web_text("api.ts")
    assert '"X-Saley-Act": "1"' in api and 'credentials: "same-origin"' in api


def test_card_editor_uses_the_edit_path_the_act_script_allows() -> None:
    api = web_text("api.ts")
    allowed = re.search(r"const CONTROL_PATH =\s*(/.*/);", api)
    assert allowed and "(dismiss|edit)" in allowed.group(1)
    card = web_text("views/inbox/InboxCard.tsx")
    assert "/api/proposal/${draft.id}/edit" in card


def test_card_editor_is_defined_once_with_one_edit_button() -> None:
    """A pasted-twice block gave two Edit buttons, one bound to the wrong editor signature."""
    card = web_text("views/inbox/InboxCard.tsx")
    assert card.count('act="edit-save"') <= 1 and card.count("/api/proposal/${draft.id}/edit") == 1


def test_pack_page_shows_a_dash_for_a_missing_number() -> None:
    parts = web_text("views/pack/PackParts.tsx")
    assert "num(n.value)" in parts  # num() turns null into a dash, never 0
    fmt = web_text("lib/format.ts")
    assert "export function num(" in fmt and 'const DASH = "—"' in fmt


def test_pack_route_serves_the_body_the_page_reads(client: TestClient, synced: Session) -> None:
    """The page needs these keys; the schema is the contract (PackOut)."""
    body_fields = set(schemas.PackBody.model_fields)
    needed = {"week_start", "last_week_day", "team", "scorecard", "late_total", "late"}
    assert needed <= body_fields
    assert {"packed_total", "packed_reminders", "tasks"} <= body_fields
    assert client.get("/api/pack?week=2026-10-20").status_code == 422  # a Tuesday
    assert client.get("/api/pack?week=1999-01-04").status_code == 404


# ------------------------------------------------------------------ R5 T3: paging, Done value


def _add_commitments(db: Session, n: int) -> None:
    for i in range(n):
        db.execute(
            text(
                "insert into commitment(owner, text, due) select id, :t, :d "
                "from users where short = 'vaishnavi'"
            ),
            {"t": f"Promise {i:02d}", "d": today_ist()},
        )
    db.commit()


def test_tasks_page_of_10_with_more(client: TestClient, synced: Session) -> None:
    _add_commitments(synced, 13)
    first = client.get("/api/today?user=vaishnavi").json()["my_tasks"]
    total = first["total"]
    assert total >= 13
    assert (len(first["items"]), first["offset"]) == (10, 0)
    assert first["more"] == total - 10
    assert first["more_label"] == f"{total - 10} more"
    second = client.get("/api/today?user=vaishnavi&tasks_offset=10").json()["my_tasks"]
    assert len(second["items"]) == min(10, total - 10)
    assert second["more"] == max(total - 20, 0)
    assert not {i["key"] for i in first["items"]} & {i["key"] for i in second["items"]}
    last = client.get(f"/api/today?user=vaishnavi&tasks_offset={total}").json()["my_tasks"]
    assert (last["items"], last["more"], last["more_label"]) == ([], 0, None)
    # A short list has no "more".
    short = client.get("/api/today?user=kushal").json()["my_tasks"]
    assert short["more"] == 0 and short["more_label"] is None
    for bad in ("-1", "1001", "x", "1.5", ""):
        assert client.get(f"/api/today?user=vaishnavi&tasks_offset={bad}").status_code == 422


def _book_meeting(db: Session, status: str = "booked") -> int:
    """David Gamez: a meeting two days ago that is still booked (a Sid status_check)."""
    day = today_ist() - timedelta(days=2)
    cid = db.execute(
        text(
            "update contact set meeting_date = :d, meeting_status = :s "
            "where name = 'David Gamez' returning id"
        ),
        {"d": day, "s": status},
    ).scalar_one()
    db.commit()
    return int(cid)


def _set_status_cells(db: Session, value: str | None) -> None:
    """Make the meeting_status column hold exactly this one value (or nothing)."""
    if value is None:
        db.execute(text("update contact set cells = cells - 'meeting_status'"))
    else:
        db.execute(
            text("update contact set cells = cells || cast(:p as jsonb)"),
            {"p": json.dumps({"meeting_status": value})},
        )
    db.commit()


def _done(client: TestClient, cid: int, **payload: object):
    body = {"action": "write", "target_ids": [cid], "payload": payload}
    return client.post("/api/act", json=body, headers={"X-Saley-Act": "1", "X-Saley-As": "sid"})


def _outbox(db: Session, role: str) -> list[str]:
    return [
        r[0] for r in db.execute(text("select value from sync_outbox where role = :r"), {"r": role})
    ]


def test_done_value_is_one_the_column_uses(client: TestClient, synced: Session) -> None:
    """D153: the server picks the Done value from the column; none there gives 409, no write."""
    cid = _book_meeting(synced)
    # The column holds "Held": that is the word written, in its own case.
    _set_status_cells(synced, "Held")
    sent = _done(client, cid, done="status_check")
    assert sent.status_code == 200, sent.text
    assert [i["value"] for i in sent.json()["items"]] == ["Held"]
    assert sent.json()["undo_until"] and sent.json()["write_id"]
    assert _outbox(synced, "meeting_status") == ["Held"]

    # Completed beats Held when both are in the column; the spelling is the sheet's.
    cid = _book_meeting(synced)
    synced.execute(text("delete from sync_outbox"))
    synced.execute(text("delete from decision"))
    synced.commit()
    _set_status_cells(synced, "COMPLETED")
    assert _done(client, cid, done="status_check").json()["items"][0]["value"] == "COMPLETED"

    # No fitting word in the column: 409, and nothing is queued.
    synced.execute(text("delete from sync_outbox"))
    synced.execute(text("delete from decision"))
    synced.commit()
    _set_status_cells(synced, "Booked")
    refused = _done(client, cid, done="status_check")
    assert refused.status_code == 409
    assert refused.json()["detail"] == "This column has no done value yet."
    assert _outbox(synced, "meeting_status") == []
    assert synced.execute(text("select count(*) from decision")).scalar_one() == 0

    # A client value beside the task key is refused (422), never trusted.
    for extra in ({"role": "meeting_status", "value": "Done"}, {"task": "reconnect"}):
        assert _done(client, cid, done="status_check", **extra).status_code == 422
    assert _done(client, cid, done="not_a_task").status_code == 422


def test_done_li_first_uses_the_column_forms(client: TestClient, synced: Session) -> None:
    """first_contact is TRUE, the type is the LI form in use, the date is today as ISO."""
    cid = synced.execute(text("select id from contact where name = 'Ro One'")).scalar_one()
    sent = _done(client, cid, done="li_first")
    assert sent.status_code == 200, sent.text
    got = {i["role"]: i["value"] for i in sent.json()["items"]}
    assert got == {
        "first_contact": "TRUE",
        "first_contact_type": "LinkedIn",
        "first_contact_date": today_ist().isoformat(),
    }
    assert len(sent.json()["items"]) == 3


def test_ticked_task_keeps_its_undo_on_reload(client: TestClient, synced: Session) -> None:
    """R5 T3: a Done inside the undo window stays on Today with undo_id and undo_until."""
    cid = _book_meeting(synced)
    _set_status_cells(synced, "Done")
    answer = _done(client, cid, done="status_check").json()
    tasks = client.get("/api/today?user=sid", headers={"X-Saley-As": "sid"}).json()["my_tasks"]
    mine = [t for t in tasks["items"] if t["key"] == f"status_check:{cid}"]
    assert len(mine) == 1
    assert mine[0]["done"] is True and mine[0]["action"] is None
    assert mine[0]["undo_id"] == answer["write_id"] and mine[0]["undo_until"]
    undone = client.post(
        f"/api/undo/{answer['write_id']}", headers={"X-Saley-Act": "1", "X-Saley-As": "sid"}
    )
    assert undone.status_code == 200
    again = client.get("/api/today?user=sid", headers={"X-Saley-As": "sid"}).json()["my_tasks"]
    assert [t["done"] for t in again["items"] if t["key"] == f"status_check:{cid}"] == [False]


def _undo(client: TestClient, write_id: int, who: str = "sid"):
    return client.post(f"/api/undo/{write_id}", headers={"X-Saley-Act": "1", "X-Saley-As": who})


def _live_li_first(client: TestClient, synced: Session) -> tuple[int, list[int]]:
    cid = synced.execute(text("select id from contact where name = 'Ro One'")).scalar_one()
    sent = _done(client, cid, done="li_first").json()
    return sent["write_id"], [i["write_id"] for i in sent["items"]]


def test_undo_of_a_three_cell_done_reverts_every_cell(client: TestClient, synced: Session) -> None:
    """R5 T3: ONE POST /api/undo/{id} undoes all cells of the Done; nothing is left to push."""
    first, ids = _live_li_first(client, synced)
    assert len(ids) == 3
    undone = _undo(client, first)
    assert undone.status_code == 200, undone.text
    assert undone.json() == {"ok": True, "write_id": first, "cancelled": True}
    states = synced.execute(
        text(
            "select d.undone_at is not null, o.state from decision d "
            "join sync_outbox o on o.decision_id = d.id where d.id = any(:i)"
        ),
        {"i": ids},
    ).all()
    assert states == [(True, "cancelled")] * 3
    # A second click is the old "already undone" refusal, not a half undo.
    assert _undo(client, first).status_code == 409


def test_undo_of_a_single_cell_done_undoes_one(client: TestClient, synced: Session) -> None:
    cid = _book_meeting(synced)
    _set_status_cells(synced, "Held")
    one = _done(client, cid, done="status_check").json()
    assert len(one["items"]) == 1
    other = synced.execute(text("select id from contact where name = 'Ro One'")).scalar_one()
    _done(client, other, done="li_first")
    assert _undo(client, one["write_id"]).status_code == 200
    live = synced.execute(
        text("select count(*) from decision where undone_at is null")
    ).scalar_one()
    assert live == 3  # the three cells of the other Done are untouched


def test_group_undo_is_for_the_signer_only(client: TestClient, synced: Session) -> None:
    first, ids = _live_li_first(client, synced)
    refused = _undo(client, first, who="kushal")
    assert refused.status_code == 403
    assert _undo(client, first, who="nobody").status_code == 403
    left = synced.execute(
        text("select count(*) from decision where id = any(:i) and undone_at is null"), {"i": ids}
    ).scalar_one()
    assert left == 3


def test_group_undo_after_the_window_refuses_the_whole_group(
    client: TestClient, synced: Session
) -> None:
    first, ids = _live_li_first(client, synced)
    synced.execute(
        text("update decision set undo_until = now() - interval '1 minute' where id = :i"),
        {"i": ids[-1]},
    )
    synced.commit()
    refused = _undo(client, first)
    assert refused.status_code == 409
    rows = synced.execute(
        text("select count(*) from decision where id = any(:i) and undone_at is null"), {"i": ids}
    ).scalar_one()
    assert rows == 3  # all or nothing: the members before the closed one are rolled back
    states = (
        synced.execute(
            text("select distinct state from sync_outbox where decision_id = any(:i)"), {"i": ids}
        )
        .scalars()
        .all()
    )
    assert states == ["pending"]


def test_group_never_joins_a_row_of_another_moment(client: TestClient, synced: Session) -> None:
    """T3 review: a reused txid (after a restore) with another `at` is not part of the group."""
    first, ids = _live_li_first(client, synced)
    synced.execute(
        text("update decision set at = at - interval '1 day' where id = :i"), {"i": ids[-1]}
    )
    synced.commit()
    assert _undo(client, first).status_code == 200
    state = dict(
        synced.execute(
            text("select id, undone_at is not null from decision where id = any(:i)"), {"i": ids}
        ).all()
    )
    assert state == {ids[0]: True, ids[1]: True, ids[2]: False}


def test_group_undo_skips_a_member_already_undone(client: TestClient, synced: Session) -> None:
    """T3 review: a member undone earlier by an inline undo does not block the rest."""
    first, ids = _live_li_first(client, synced)
    done = synced.execute(text("select saley_undo('sid', :i, 'dashboard')"), {"i": ids[1]})
    assert done.scalar_one()["ok"] is True
    synced.commit()
    assert _undo(client, first).status_code == 200
    left = synced.execute(
        text("select count(*) from decision where id = any(:i) and undone_at is null"), {"i": ids}
    ).scalar_one()
    assert left == 0
    # The requested member itself already undone keeps the old refusal.
    assert _undo(client, ids[1]).status_code == 409


def test_ticked_row_is_hidden_from_another_viewer(client: TestClient, synced: Session) -> None:
    """T3 review: kushal looking at ?user=sid sees no ticked rows and no Undo."""
    cid = _book_meeting(synced)
    _set_status_cells(synced, "Done")
    answer = _done(client, cid, done="status_check").json()
    seen = client.get("/api/today?user=sid", headers={"X-Saley-As": "kushal"}).json()["my_tasks"]
    assert [t for t in seen["items"] if t["done"] or t["undo_id"] == answer["write_id"]] == []
    own = client.get("/api/today?user=sid", headers={"X-Saley-As": "sid"}).json()["my_tasks"]
    assert [t["done"] for t in own["items"] if t["key"] == f"status_check:{cid}"] == [True]


def test_own_tick_survives_many_newer_writes_by_others(client: TestClient, synced: Session) -> None:
    """T3 review: the window list is per signer, so 50 newer writes of others do not push it out."""
    cid = _book_meeting(synced)
    _set_status_cells(synced, "Done")
    answer = _done(client, cid, done="status_check").json()
    synced.execute(
        text(
            "insert into decision(kind, role, state, user_id, undo_until, at) "
            "select 'column_write', 'meeting_status', 'active', "
            "(select id from users where short = 'kushal'), now() + interval '5 minutes', "
            "now() + (g || ' seconds')::interval from generate_series(1, 60) g"
        )
    )
    synced.commit()
    own = client.get("/api/today?user=sid", headers={"X-Saley-As": "sid"}).json()["my_tasks"]
    assert [t["undo_id"] for t in own["items"] if t["done"]] == [answer["write_id"]]


# ------------------------------------------------------------------ R5 T4: Add email, commitments

ACT_HEADERS = {"X-Saley-Act": "1", "X-Saley-As": "vaishnavi"}
GOOD_EMAIL = "ro.one@example.com"


def _ready_for_add_email(db: Session) -> int:
    """Ro One: connected nine days ago, no email, Vaishnavi's list (the add_email rule)."""
    cid = db.execute(
        text(
            "update contact set connected = true, li_connected_date = :d, owner = 'vaishnavi' "
            "where name = 'Ro One' returning id"
        ),
        {"d": today_ist() - timedelta(days=9)},
    ).scalar_one()
    db.commit()
    return int(cid)


def _add_email_tasks(client: TestClient) -> list[dict]:
    got = client.get("/api/today?user=vaishnavi", headers={"X-Saley-As": "vaishnavi"})
    return [i for i in got.json()["my_tasks"]["items"] if i["kind"] == "add_email"]


def _email_write(client: TestClient, cid: int, value: str, targets: list[int] | None = None):
    body = {
        "action": "email_write",
        "target_ids": targets or [cid],
        "payload": {"role": "email", "value": value},
    }
    return client.post("/api/act", json=body, headers=ACT_HEADERS)


def _no_writes(db: Session) -> bool:
    return not any(
        db.execute(text(f"select count(*) from {t}")).scalar_one()
        for t in ("decision", "sync_outbox")
    )


def test_add_email_completes_on_valid_write(
    client: TestClient, synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5-T14 (D154): no Done; a valid email through email_write ends the task; Undo undoes it."""
    monkeypatch.setenv("EMAIL_WRITE_ALLOWED", "true")
    get_settings.cache_clear()
    try:
        cid = _ready_for_add_email(synced)
        (task,) = [t for t in _add_email_tasks(client) if t["contact_id"] == cid]
        assert task["title"] == "Add email for Ro One"
        assert (task["action"], task["input_role"], task["soon"]) == (None, "email", None)

        saved = _email_write(client, cid, GOOD_EMAIL)
        assert saved.status_code == 200, saved.text
        assert [i["role"] for i in saved.json()["items"]] == ["email"]
        queued = synced.execute(text("select role, value, state from sync_outbox")).all()
        assert [tuple(r) for r in queued] == [("email", GOOD_EMAIL, "pending")]
        assert not [t for t in _add_email_tasks(client) if t["contact_id"] == cid]

        undone = client.post(f"/api/undo/{saved.json()['write_id']}", headers=ACT_HEADERS)
        assert undone.status_code == 200, undone.text
        assert [t for t in _add_email_tasks(client) if t["contact_id"] == cid]
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_add_email_refused_bad_value_or_flag_off(
    client: TestClient, synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5-T15 (D154): flag off is 403, the task shows "soon"; bad values write nothing."""
    cid = _ready_for_add_email(synced)
    off = _email_write(client, cid, GOOD_EMAIL)
    assert off.status_code == 403
    assert off.json()["detail"] == "Email writes are off (EMAIL_WRITE_ALLOWED)."
    (task,) = [t for t in _add_email_tasks(client) if t["contact_id"] == cid]
    assert (task["input_role"], task["soon"], task["action"]) == ("email", "email_write", None)
    assert _no_writes(synced)

    monkeypatch.setenv("EMAIL_WRITE_ALLOWED", "true")
    get_settings.cache_clear()
    try:
        bad = [
            "no-at.example.com",
            "a@nodot",
            "a@.com",
            "a@b..com",
            "a b@example.com",
            "=cmd@example.com",
            "",
            "a" * 250 + "@b.co",  # 255 characters
        ]
        for value in bad:
            assert _email_write(client, cid, value).status_code == 422, value
        other = synced.execute(
            text("select id from contact where name = 'Alex Search'")
        ).scalar_one()
        assert _email_write(client, cid, GOOD_EMAIL, [cid, other]).status_code == 422
        # The cell must be blank (docs/10): a filled cell gives 409.
        synced.execute(
            text("update contact set email = 'old@example.com' where id = :i"), {"i": cid}
        )
        synced.commit()
        filled = _email_write(client, cid, GOOD_EMAIL)
        assert filled.status_code == 409
        assert filled.json()["detail"] == "That person already has an email."
        assert _no_writes(synced)
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def _promise(db: Session, owner: str) -> int:
    cid = db.execute(
        text(
            "insert into commitment(owner, text, due) "
            "select id, 'I will send the deck.', :d from users where short = :o returning id"
        ),
        {"d": today_ist(), "o": owner},
    ).scalar_one()
    db.execute(
        text(
            "insert into reminder(for_user, text, fire_at, commitment_id, kind, state) "
            "select id, 'Due today: x', now(), :c, 'due_day', 'pending' from users "
            "where short = :o"
        ),
        {"c": cid, "o": owner},
    )
    db.commit()
    return int(cid)


def test_commitment_done_owner_only_no_write(client: TestClient, synced: Session) -> None:
    """R5-C01 (D155): a non-owner, an admin too, gets 403 with the owner's name and no write."""
    cid = _promise(synced, "vaishnavi")
    name = synced.execute(text("select name from users where short = 'vaishnavi'")).scalar_one()
    line = f"This is {name}'s commitment. Only they can mark it done."
    touches = synced.execute(text("select count(*) from touch")).scalar_one()
    for actor in ("sid", "trishi"):  # trishi is the admin: no exception
        refused = client.post(
            f"/api/commitment/{cid}/done", headers={"X-Saley-Act": "1", "X-Saley-As": actor}
        )
        assert refused.status_code == 403, actor
        assert refused.json()["detail"] == line
    assert _no_writes(synced)
    assert synced.execute(text("select state from commitment")).scalar_one() == "open"
    assert synced.execute(text("select state from reminder")).scalar_one() == "pending"
    assert synced.execute(text("select count(*) from touch")).scalar_one() == touches

    done = client.post(f"/api/commitment/{cid}/done", headers=ACT_HEADERS)
    assert done.status_code == 200 and done.json()["already"] is False, done.text
    assert synced.execute(text("select state from commitment")).scalar_one() == "done"
    # Undo is for the signer only (D155).
    by_sid = {"X-Saley-Act": "1", "X-Saley-As": "sid"}
    late = client.post(f"/api/undo/{done.json()['write_id']}", headers=by_sid)
    assert late.status_code == 403
    assert late.json()["detail"] == "Only the person who marked it done can undo it."
    assert synced.execute(text("select state from commitment")).scalar_one() == "done"
    back = client.post(f"/api/undo/{done.json()['write_id']}", headers=ACT_HEADERS)
    assert back.status_code == 200
    assert synced.execute(text("select state from commitment")).scalar_one() == "open"


# ------------------------------------------------------------------ R5 T5: Today read model

FUNNEL_KEYS = ["listed", "contacted", "connected", "talking", "met", "won"]


def _stage_counts(db: Session) -> dict[str, int]:
    found = db.execute(text("select stage, count(*) from v_contact_stage group by stage")).all()
    return {stage: n for stage, n in found}


def test_today_funnel_matches_stage_view(client: TestClient, synced: Session) -> None:
    funnel = client.get("/api/today?user=vaishnavi").json()["funnel"]
    assert [s["key"] for s in funnel] == FUNNEL_KEYS
    assert [s["label"] for s in funnel] == [
        "In the list",
        "Contacted",
        "Connected",
        "Talking now",
        "Met",
        "Won",
    ]
    counts = _stage_counts(synced)

    def at_or_past(*stages: str) -> int:
        return sum(counts.get(s, 0) for s in stages)

    want = [
        sum(counts.values()),
        at_or_past("Contacted", "Connected", "Engaged", "Meeting", "Qualified", "Won"),
        at_or_past("Connected", "Engaged", "Meeting", "Qualified", "Won"),
        at_or_past("Engaged", "Meeting", "Qualified", "Won"),
        at_or_past("Meeting", "Qualified", "Won"),
        at_or_past("Won"),
    ]
    assert [s["n"] for s in funnel] == want
    assert want == sorted(want, reverse=True)
    for step in funnel:  # each bar opens a list with the same count
        listed = client.get(f"/api/people?filter={step['link']['filter']}&limit=1").json()
        assert listed["total"] == step["n"], step["key"]
        assert step["link"]["view"] == "people"


def test_funnel_stage_without_people_is_a_real_zero(client: TestClient, synced: Session) -> None:
    synced.execute(text("update contact set removed_at = now()"))
    synced.commit()
    funnel = client.get("/api/today").json()["funnel"]
    assert [s["n"] for s in funnel] == [0] * 6


def test_tiles_have_the_short_labels_with_the_same_keys(client: TestClient) -> None:
    labels = {k: t["label"] for k, t in tiles(client).items()}
    assert labels["backlog"] == "Connected, no email yet"
    assert labels["p1"] == "P1 waiting on LinkedIn"
    assert labels["week"] == "New this week"
    assert set(labels) == {"deals", "live", "backlog", "p1", "week", "late"}


def test_blockers_line_counts_late_items_that_block_goals(client: TestClient) -> None:
    line = client.get("/api/today").json()["blockers_line"]
    assert line == {
        "count": 2,
        "label": "2 late items block goals",
        "link": {"view": "blockers", "filter": None},
    }
    assert len(line["label"]) <= 60 and "overdue" not in line["label"].lower()


def test_blockers_line_words_for_none_and_one() -> None:
    from saley.web import today_model

    assert today_model.blockers_line(0)["label"] == "No late item blocks goals"
    assert today_model.blockers_line(1)["label"] == "1 late item blocks goals"
    unknown = today_model.blockers_line(None)
    assert unknown["count"] is None and unknown["label"]


def viewer_id(db: Session) -> int:
    short = get_settings().dev_user_short
    return db.execute(text("select id from users where short = :s"), {"s": short}).scalar_one()


def test_needs_look_kinds_no_redirect_only(client: TestClient, synced: Session) -> None:
    from tests.test_signals import add_story, event, link_all, reset_events

    add_story(synced, "Sarvam AI raises a round", importance=5)
    link_all(synced)
    reset_events(synced, event("e:9", "Tomorrow Summit", "London", 1))
    user_id = viewer_id(synced)
    other_id = synced.execute(
        text("select id from users where id <> :u order by id limit 1"), {"u": user_id}
    ).scalar_one()
    synced.execute(
        text(
            "insert into ask (to_user, question, asked_at) values "
            "(:u, 'Who owns the NDA?', now() - interval '30 hours'), "
            "(:u, 'A fresh question', now() - interval '2 hours'), "
            "(:o, 'Not for the viewer', now() - interval '40 hours')"
        ),
        {"u": user_id, "o": other_id},
    )
    synced.commit()
    block = client.get("/api/today").json()["needs_look"]
    cards = block["cards"]
    kinds = [c["kind"] for c in cards]
    assert set(kinds) == {"news", "event", "blocker", "waiting"}
    assert kinds.count("blocker") == 2 and kinds.count("waiting") == 1  # the 2 h ask is too new
    waiting = next(c for c in cards if c["kind"] == "waiting")
    assert waiting["title"] == "Who owns the NDA?" and "30 h" in waiting["why"]
    assert waiting["why"] == "Waiting for your answer · 30 h"  # D172: viewer-centred, no name
    for card in cards:  # every card holds an item of its own, never a bare pointer
        assert card["title"].strip() and card["why"].strip()
        assert card["title"] != "Blocked or waiting"
        assert len(card["title"]) <= 60 and len(card["why"]) <= 60
        assert card["link"]["view"] in {"signals", "events", "blockers", "asks"}
    assert block["soon"] == "mail_replies"
    assert block["items"]  # the legacy page still reads items


def test_needs_look_has_no_news_event_or_waiting_card_when_none_exist(
    client: TestClient, synced: Session
) -> None:
    from tests.test_signals import reset_events

    reset_events(synced)
    cards = client.get("/api/today").json()["needs_look"]["cards"]
    assert {c["kind"] for c in cards} <= {"blocker"}


def test_suggest_line_uses_input_facts_only(
    client: TestClient, synced: Session, raw_sheet: dict
) -> None:
    from saley.intel import guard
    from saley.web import today_model
    from tests.test_signals import add_people

    add_people(synced, raw_sheet)
    items = client.get("/api/today?user=sid").json()["saley_suggests"]["items"]
    assert items
    for item in items:
        line = item["line"]
        assert line.startswith("Add " + item["subject"]) and len(line) <= 60  # D173
        assert " to pipeline — " in line or line.endswith("…")
        assert guard.passes(line.removeprefix("Add "), today_model.suggest_facts(item)), line
        if " to pipeline — " in line:
            assert len(line.split(" to pipeline — ", 1)[1]) <= today_model.REASON_MAX
        if item["company"] and item["company"] != item["subject"] and "…" not in line:
            assert f"({item['company']})" in line


def test_suggest_line_with_an_invented_value_is_dropped_and_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from saley.web import today_model

    logged: list[dict] = []
    monkeypatch.setattr(
        today_model.log, "warning", lambda event, **kw: logged.append({"event": event, **kw})
    )
    good = {
        "id": 1,
        "kind": "warm",
        "subject": "Wendy Warm",
        "company": "Acme Labs",
        "title": None,
        "reason": "Fits 2 goals",
        "parts": [{"label": "Fits 2 goals"}],
        "evidence": {},
    }
    bad = {**good, "id": 2, "reason": "Met on 2026-03-04 with 9 people", "parts": []}
    kept = today_model.with_lines([good, bad])
    assert [i["id"] for i in kept] == [1]
    assert kept[0]["line"] == "Add Wendy Warm (Acme Labs) to pipeline — Fits 2 goals"
    assert "line" not in good  # the input item is not changed
    assert logged and logged[0]["event"] == "suggest_line_dropped" and logged[0]["id"] == 2


def test_suggest_line_keeps_a_short_company_name_that_starts_with_caps() -> None:
    from saley.web import today_model

    item = {
        "id": 3,
        "kind": "newco",
        "subject": "AI Labs",
        "company": None,
        "title": None,
        "reason": "Fits 2 goals",
        "parts": [{"label": "Fits 2 goals"}],
        "evidence": {},
    }
    assert today_model.with_lines([item])[0]["line"] == "Add AI Labs to pipeline — Fits 2 goals"


def test_suggest_reason_is_cut_at_40_characters() -> None:
    from saley.web import today_model

    long = "Fits 2 goals · works with three of our people · Head of Data"
    cut = today_model.cut_reason(long)
    assert len(cut) <= 40 and cut.endswith("…") and long.startswith(cut[:-1])
    assert today_model.cut_reason("Fits 2 goals") == "Fits 2 goals"


def test_goal_ring_won_of_need_or_dash(client: TestClient, synced: Session) -> None:
    goals = {g["code"]: g for g in client.get("/api/goals").json()["items"]}
    for g in goals.values():
        prog = g["progress"]
        assert prog["won"] == g["won"] and prog["need"] == g["need"]
        if g["need"]:
            assert prog["pct"] == round(100 * g["won"] / g["need"])
    synced.execute(text("update goal set need = null where code = 'G5'"))
    synced.execute(text("update goal set need = 0 where code = 'G3'"))
    synced.commit()
    goals = {g["code"]: g for g in client.get("/api/goals").json()["items"]}
    assert goals["G5"]["progress"] == {"won": goals["G5"]["won"], "need": None, "pct": None}
    assert goals["G3"]["progress"]["pct"] is None and goals["G3"]["progress"]["need"] is None


def test_goal_active_note_is_one_short_line(client: TestClient, synced: Session) -> None:
    from saley.web import goal_model

    people = [p for g in client.get("/api/goals").json()["items"] for p in g["active"]]
    assert people and all(p["note"] and len(p["note"]) <= goal_model.NOTE_MAX for p in people)
    synced.execute(text("update contact set next_step = repeat('word ', 30)"))
    synced.commit()
    people = [p for g in client.get("/api/goals").json()["items"] for p in g["active"]]
    assert all(p["note"].endswith("…") and len(p["note"]) == goal_model.NOTE_MAX for p in people)
    assert "\n" not in goal_model.cut("a\nb  c")


def test_waiting_cards_show_only_the_viewers_asks_over_24_h(synced: Session) -> None:
    from saley.core.clock import now_ist
    from saley.web import today_model

    me = viewer_id(synced)
    other = synced.execute(
        text("select id from users where id <> :u order by id limit 1"), {"u": me}
    ).scalar_one()
    synced.execute(
        text(
            "insert into ask (to_user, question, asked_at) values "
            "(:o, 'Other old', now() - interval '50 hours'), "
            "(:u, 'Mine old', now() - interval '25 hours'), "
            "(:u, 'Mine new', now() - interval '23 hours')"
        ),
        {"u": me, "o": other},
    )
    synced.commit()
    conn = synced.connection()
    short = get_settings().dev_user_short
    titles = [c["title"] for c in today_model._waiting_cards(conn, now_ist(), short)]
    assert titles == ["Mine old"]
    assert today_model._waiting_cards(conn, now_ist(), None) == []  # no viewer, no fallback


def test_needs_look_kinds_do_not_starve_each_other(synced: Session) -> None:
    from saley.web import signals as web_signals
    from saley.web import today_model
    from tests.test_signals import add_story, event, link_all, reset_events

    for n in range(2):
        add_story(synced, f"Sarvam AI old story {n}", importance=5, seen_ago=timedelta(days=5))
    link_all(synced)
    reset_events(synced, *[event(f"e:{n}", f"Summit {n}", "Pune", 1) for n in range(16)])
    conn = synced.connection()
    cards = today_model.look_cards(conn, lambda s: s, "nobody")
    kinds = [c["kind"] for c in cards]
    assert kinds.count("event") == 8 and kinds.count("news") == 2
    assert len(web_signals.look_rows(conn)) == 10


def test_today_reads_needs_look_view_once(
    client: TestClient, synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from saley.web import signals as web_signals

    calls: list[str | None] = []
    real = web_signals.needs_look

    def spy(conn, limit=20, kind=None):
        calls.append(kind)
        return real(conn, limit, kind)

    monkeypatch.setattr(web_signals, "needs_look", spy)
    assert client.get("/api/today").status_code == 200
    assert sorted(k or "" for k in calls) == ["event", "signal"]  # one read per kind, no repeat


def test_progress_rounds_half_up() -> None:
    from saley.web import goal_model

    assert goal_model.progress(1, 8)["pct"] == 13  # 12.5 -> 13, not banker's 12
    assert goal_model.progress(1, 3)["pct"] == 33
    assert goal_model.progress(2, 3)["pct"] == 67
    assert goal_model.progress(0, 5)["pct"] == 0
    assert goal_model.progress(5, 5)["pct"] == 100


def test_today_cut_adds_ellipsis_and_blocker_links_are_copies() -> None:
    from saley.web import today_model

    long = "x" * 80
    assert today_model.cut(long) == "x" * 59 + "…" and len(today_model.cut(long)) == 60
    assert today_model.cut("short") == "short"
    item = {"subject": "N" * 50, "company": "C" * 50, "reason": "good fit"}
    line = today_model.suggest_line(item)
    assert len(line) == 60 and line.endswith("…")
    a = today_model.blockers_line(1)["link"]
    a["view"] = "changed"
    assert today_model.blockers_line(1)["link"]["view"] == "blockers"


# ------------------------------------------------------------------ R5 T6: Accounts, People, Events


def _person_id(client: TestClient, name: str = "dinesh") -> int:
    return client.get(f"/api/people?filter=all&q={name}").json()["items"][0]["id"]


def _account_of(client: TestClient, pid: int) -> int:
    return client.get(f"/api/people/{pid}").json()["item"]["account_id"]


def _add_touches(db: Session, pid: int, *days: object, kind: str = "email_1") -> None:
    from saley.core.models import Touch

    db.add_all(Touch(contact_id=pid, type=kind, date=d, source="test") for d in days)
    db.commit()


def test_account_people_p1_stage_counts(client: TestClient) -> None:
    from saley.web import accounts_model

    assert accounts_model.people_label(13, 9) == "13 people · 9 P1"
    assert accounts_model.people_label(1, 0) == "1 person · 0 P1"
    body = client.get("/api/accounts").json()
    assert body["items"]
    for item in body["items"]:
        assert sum(item["stage_counts"].values()) == item["people"]
        assert item["people_label"] == accounts_model.people_label(item["people"], item["p1_count"])
        assert set(item["tags"]) <= {"Qualified", "Engaged", "Single-threaded"}
        assert ("Single-threaded" in item["tags"]) == (item["threads"] == 1)
        assert ("Engaged" in item["tags"]) == (item["threads"] >= 2)
    assert {t["tag"] for t in body["tags_legend"]} == {"Qualified", "Engaged", "Single-threaded"}
    assert all(t["meaning"] and t["source"] for t in body["tags_legend"])
    detail = client.get(f"/api/accounts/{body['items'][0]['id']}").json()["item"]
    assert detail["stage_counts"] == body["items"][0]["stage_counts"]
    assert detail["evidence_hidden"] is True and "row" in detail["evidence"]  # old page needs it


def test_account_tags_are_rules_on_stored_counts() -> None:
    from saley.web import accounts_model

    assert accounts_model.tags({"Qualified": 1}, 0) == ["Qualified"]
    assert accounts_model.tags({"Engaged": 2}, 2) == ["Engaged"]
    assert accounts_model.tags({"Qualified": 1}, 1) == ["Qualified", "Single-threaded"]
    assert accounts_model.tags({}, 0) == []


def test_touch_timeline_by_month(client: TestClient, synced: Session) -> None:
    from saley.web import timeline

    today = today_ist()
    pid = _person_id(client)
    start = timeline.window_start(today)
    synced.execute(text("delete from touch where contact_id = :i"), {"i": pid})
    _add_touches(synced, pid, today, start, start - timedelta(days=1), kind="email_1")
    _add_touches(synced, pid, today, kind="li_dm")
    for url in (f"/api/accounts/{_account_of(client, pid)}", f"/api/people/{pid}"):
        strip = client.get(url).json()["item"]["timeline"]
        assert len(strip) == 12 and strip[0]["month"] == f"{today:%Y-%m}"
        assert strip[0]["label"] == f"{today:%b %Y}" and strip[-1]["month"] == f"{start:%Y-%m}"
        assert sorted(t["type"] for t in strip[0]["touches"]) == ["email_1", "li_dm"]
        assert strip[0]["touches"][0]["person"] == "Dinesh Tewari"
        assert [t["day"] for t in strip[-1]["touches"]] == [start.isoformat()]
        assert sum(len(m["touches"]) for m in strip) == 3  # the day before the window is out


def test_timeline_month_boundaries_follow_ist(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import date

    from saley.web import timeline

    monkeypatch.setattr(timeline, "today_ist", lambda: date(2026, 11, 1))  # IST is past midnight
    touches = [
        {"day": date(2026, 10, 31), "type": "email_1", "person": "A"},
        {"day": date(2026, 11, 1), "type": "li_dm", "person": "A"},
        {"day": date(2025, 12, 1), "type": "email_2", "person": "A"},
        {"day": date(2025, 11, 30), "type": "email_3", "person": "A"},
        {"day": date(2026, 11, 2), "type": "email_1", "person": "A"},  # in the future: not shown
        {"day": None, "type": "email_1", "person": "A"},
    ]
    strip = timeline.build(touches)
    months = [m["month"] for m in strip]
    assert months[:2] == ["2026-11", "2026-10"] and months[-1] == "2025-12"
    assert [t["type"] for t in strip[0]["touches"]] == ["li_dm"]
    assert [t["type"] for t in strip[1]["touches"]] == ["email_1"]
    assert [t["type"] for t in strip[-1]["touches"]] == ["email_2"]
    assert sum(len(m["touches"]) for m in strip) == 3
    assert timeline.first_of_month(date(2026, 1, 15), 1) == date(2025, 12, 1)


def test_timeline_keeps_at_most_200_newest_touches() -> None:
    from datetime import date

    from saley.web import timeline

    today = date(2026, 10, 21)
    many = [
        {"day": today - timedelta(days=i % 300), "type": "email_1", "person": "A"}
        for i in range(450)
    ]
    strip = timeline.build(many, today)
    shown = [t for m in strip for t in m["touches"]]
    assert len(shown) == 200 and len(strip) == 12
    newest = sorted((t["day"] for t in many), reverse=True)[199]
    assert min(t["day"] for t in shown) >= newest


def test_person_follow_up_by_papers_source(client: TestClient, synced: Session) -> None:
    from sqlalchemy import update

    from saley.core.models import Contact, Fact

    pid = _person_id(client)
    synced.execute(
        update(Contact)
        .where(Contact.id == pid)
        .values(paper_url="https://arxiv.org/abs/2401.00001 http://example.org/a/deep-paper.pdf")
    )
    synced.add(
        Fact(
            entity_type="contact",
            entity_id=pid,
            kind="working_on",
            value={"text": "Distillation study"},
            source_type="meeting_note",
        )
    )
    synced.commit()
    item = client.get(f"/api/people/{pid}").json()["item"]
    assert item["follow_up_by"]["rule"] == "the follow-up rule"
    assert item["follow_up_by"]["name"].lower().startswith(item["owner"])
    assert item["follow_up_by"]["source"] in {
        "First Contact",
        "Sid - LI Addition",
        "First Contact Type",
        "default rule",
    }
    assert item["papers_label"] == "2 papers" and len(item["papers"]) == 2
    assert item["papers"][0] == {
        "text": "arxiv.org/2401.00001",
        "href": "https://arxiv.org/abs/2401.00001",
    }
    assert item["working_on"]["text"] == "Distillation study"
    assert re.fullmatch(r"note \w{3} \d{1,2} \w{3}", item["working_on"]["source"])


def test_working_on_source_is_the_sheet_column(client: TestClient) -> None:
    from datetime import UTC, datetime

    from saley.web import people_model

    seen = datetime(2026, 10, 14, 20, 0, tzinfo=UTC)  # 15 Oct in IST
    f = people_model.working_on_source
    about = {"text": "x", "url": None, "source_type": "about_poc", "observed_at": seen}
    paper = {"text": None, "url": "https://a.org/p", "source_type": "paper_link"}
    note = {"text": "x", "source_type": "meeting_note", "observed_at": seen}
    assert f(about) == "Outreach PoCs, About PoC"
    assert f(paper) == "Outreach PoCs, Research Paper Link"
    assert f(note) == "note Thu 15 Oct"
    assert f(None) is None and f({"text": "", "url": ""}) is None
    pid = _person_id(client)
    none_on_file = client.get(f"/api/people/{pid}").json()["item"]["working_on"]
    assert none_on_file["found"] is False and none_on_file["source"] is None


def test_paper_link_text_never_invented(client: TestClient, synced: Session) -> None:
    from sqlalchemy import update

    from saley.core.models import Contact, SearchHit
    from saley.web import people_model

    known = "https://papers.example.org/p/42"
    long_url = "https://www.example.com/research/" + "x" * 80
    bad = "javascript:alert(1)"
    pid = _person_id(client)
    synced.add(SearchHit(query="q", engine="duckduckgo", url=known, title="A Stored Title"))
    cell = f"{known} {long_url} {bad} https://user:pw@evil.example/x ftp://h/f"
    synced.execute(update(Contact).where(Contact.id == pid).values(paper_url=cell))
    synced.commit()
    papers = client.get(f"/api/people/{pid}").json()["item"]["papers"]
    assert [p["href"] for p in papers] == [known, long_url]  # javascript:, user info, ftp refused
    assert papers[0]["text"] == "A Stored Title"  # a stored title wins
    assert papers[1]["text"].startswith("example.com/xxx") and len(papers[1]["text"]) == 40
    assert papers[1]["text"].endswith("…")
    assert people_model.safe_href("https://www.linkedin.com/in/x")  # shown, never fetched
    for refused in (
        bad,
        "data:text/html,x",
        "//a.org/x",
        "https://a.org/ x",
        "",
        None,
        "https://[x",
    ):
        assert people_model.safe_href(refused) is None
    assert people_model.derived_text("https://arxiv.org/") == "arxiv.org"
    assert people_model.cut("y" * 41) == "y" * 39 + "…" and people_model.cut("y" * 40) == "y" * 40


def test_person_without_owner_or_paper_shows_no_guess(client: TestClient, synced: Session) -> None:
    from sqlalchemy import update

    from saley.core.models import Contact

    pid = _person_id(client)
    synced.execute(
        update(Contact)
        .where(Contact.id == pid)
        .values(owner=None, owner_source=None, paper_url=None)
    )
    synced.commit()
    item = client.get(f"/api/people/{pid}").json()["item"]
    assert item["follow_up_by"] == {"name": None, "source": None, "rule": "the follow-up rule"}
    assert item["papers"] == [] and item["papers_label"] == "0 papers"


def test_events_city_venue_registered_first(client: TestClient, synced: Session) -> None:
    from saley.web import events_model
    from tests.test_signals import event, reset_events

    cases = {
        "Bengaluru": "Bangalore",
        "BLR": "Bangalore",
        "Bangalore": "Bangalore",
        "Taj Hotel, Bengaluru, India": "Bangalore",
        "London": "London",
        "ExCeL London, Royal Victoria Dock": "London",
        "Paris": "Other",
        "London and Bangalore": "Other",
        "": "Other",
        None: "Other",
    }
    for location, city in cases.items():
        assert events_model.city_of(location) == city, location
    assert events_model.venue_of("  ExCeL London  ") == "ExCeL London"
    assert events_model.venue_of(" ") is None

    late = event("e:1", "Late Registered", "ExCeL London, Royal Victoria Dock", 30)
    late.registered = True
    early = event("e:2", "Early Open", "Bengaluru", 2)
    mid = event("e:3", "Mid Open", "Paris", 10)
    undated = event("e:4", "No Date Registered", "BLR", None)
    undated.registered = True
    reset_events(synced, early, mid, late, undated)
    up = client.get("/api/events").json()["items"]
    names = [i["name"] for i in up]
    assert names == ["Late Registered", "No Date Registered", "Early Open", "Mid Open"]
    assert [i["registered"] for i in up] == [True, True, False, False]
    assert [i["city"] for i in up] == ["London", "Bangalore", "Bangalore", "Other"]
    assert up[0]["venue"] == "ExCeL London, Royal Victoria Dock" and up[3]["venue"] == "Paris"
    assert all(i["speaker_soon"] == "speaker_match" for i in up)
    paged = client.get("/api/events?limit=1&offset=1").json()["items"]
    assert [i["name"] for i in paged] == ["No Date Registered"]  # the order holds across pages


def test_me_features_match_settings(client: TestClient, set_env) -> None:
    names = {"find_people", "email_write", "speaker_match", "apollo", "mail_replies", "dms"}
    off = client.get("/api/me").json()["features"]
    assert set(off) == names and not any(off.values())
    set_env(
        FIND_PEOPLE_ENABLED="true",
        EMAIL_WRITE_ALLOWED="true",
        DM_ENABLED="true",
        MAIL_USER="cc@example.org",
        APOLLO_ENABLED="true",
        APOLLO_MONTHLY_CREDIT_CAP="100",
    )
    on = client.get("/api/me").json()["features"]
    assert on == {
        "find_people": True,
        "email_write": True,
        "speaker_match": False,  # S12 is not built
        "apollo": False,  # the Apollo pipeline is not built (D78)
        "mail_replies": True,
        "dms": True,
    }


def test_find_people_act_is_refused_while_the_feature_is_off(client: TestClient, set_env) -> None:
    headers = {"X-Saley-Act": "1", "X-Saley-As": "sid"}
    body = {"action": "find_people", "target_ids": [1]}
    off = client.post("/api/act", json=body, headers=headers)
    assert off.status_code == 403
    assert off.json()["detail"] == "Find people is off for now."
    set_env(FIND_PEOPLE_ENABLED="true")
    assert client.post("/api/act", json=body, headers=headers).status_code != 403


def test_new_read_models_refuse_unknown_query_parameters(client: TestClient) -> None:
    pid = _person_id(client)
    for path in (
        "/api/accounts?timeline=1",
        f"/api/people/{pid}?papers=x",
        "/api/events?city=London",
    ):
        r = client.get(path)
        assert r.status_code == 422 and r.json()["detail"] == "Unknown parameter."


def test_paper_links_refuse_backslash_and_look_alike_hosts() -> None:
    """T6 review: a backslash is a slash to a browser; an IDN host can look like another."""
    from saley.web import people_model

    assert people_model.safe_href(r"https://evil.com\.good.com/p") is None
    assert people_model.safe_href("https://xn--80ak6aa92e.com/p") is not None  # punycode is ASCII
    assert people_model.safe_href("https://аpple.com/p") is None  # Cyrillic a
    assert people_model.safe_href("https://arxiv.org/abs/2401.1") == "https://arxiv.org/abs/2401.1"


def test_derived_paper_text_drops_hidden_marks() -> None:
    from saley.web import people_model

    text = people_model.derived_text("https://arxiv.org/abs/ab%E2%80%AEcd%00e")
    assert "‮" not in text and "\x00" not in text and text == "arxiv.org/abcde"


def test_no_left_out_line_nothing_new_text(client: TestClient, synced: Session) -> None:
    """R5-N02: a tabbed list has no "left out" line; an empty New tab says so in words (D157)."""
    from tests.test_signals import add_story

    one = add_story(synced, "First market story", importance=3)
    two = add_story(synced, "Second market story", importance=2)
    sid = {"X-Saley-As": "sid"}
    act = {"X-Saley-Act": "1", **sid}
    seen = client.post(
        "/api/seen", json={"item_type": "story", "ids": [one.id, two.id]}, headers=act
    )
    assert seen.status_code == 200

    new = client.get("/api/signals?view=new", headers=sid).json()
    assert new["items"] == [] and (new["new_total"], new["seen_total"]) == (0, 2)
    assert new["new_empty"] == "Nothing new since your last visit"
    assert "left_out_note" not in new and new["left_out_seen"] == 0  # the field went (D224)

    earlier = client.get("/api/signals?view=seen", headers=sid).json()
    assert {i["id"] for i in earlier["items"]} == {one.id, two.id}
    assert earlier["new_empty"] is None and "left_out_note" not in earlier
    assert "left out" not in json.dumps(earlier).lower()

    # Another person has not seen them: no empty line for her.
    other = client.get("/api/signals?view=new", headers={"X-Saley-As": "vaishnavi"}).json()
    assert other["new_total"] == 2 and other["new_empty"] is None

    # Events and Suggestions: no "left out" line in a tabbed answer, whatever the seen state.
    for path in ("/api/events?view=new", "/api/events?view=seen", "/api/suggest/warm?view=new"):
        body = client.get(path, headers=sid).json()
        assert "left_out_note" not in body and body["left_out_seen"] == 0, path
    listed = client.get("/api/events?view=new", headers=sid).json()["items"]
    # The fixture events are dated: after a day turns, New can already be empty, and an empty
    # `ids` list is a 422 by design. Mark only when there is something to mark (no date flake).
    if listed:
        body = {"item_type": "event", "ids": [e["id"] for e in listed]}
        assert client.post("/api/seen", json=body, headers=act).status_code == 200
    events = client.get("/api/events?view=new", headers=sid).json()
    assert events["new_total"] == 0 and events["new_empty"] == "Nothing new since your last visit"

    # No `view` (D224, the cut-over): new items only, the seen ones counted, never a line.
    plain = client.get("/api/signals", headers=sid).json()
    assert plain["items"] == [] and plain["left_out_seen"] == 2
    assert "left out" not in json.dumps(plain).lower()
    assert client.get("/api/signals?include_seen=true", headers=sid).status_code == 422


# ------------------------------------------------------------------ Run news now (R5 T8)

NEWS_RUN = "/api/admin/news/run"
ACT = {"X-Saley-Act": "1"}


def _age_news_requests(db: Session, state: str = "done") -> None:
    db.execute(
        text(
            "update news_run_request set state = :s, finished_at = now(), "
            "requested_at = now() - interval '11 minutes'"
        ),
        {"s": state},
    )
    db.commit()


def test_run_news_now_admin_only_one_open(client: TestClient, synced: Session) -> None:
    """R5-N07 (D150): admin only, header and same-origin like every act, one open at a time."""
    assert client.post(NEWS_RUN).status_code == 403  # no X-Saley-Act header
    first = client.post(NEWS_RUN, headers=ACT)
    assert first.status_code == 200
    assert first.json() == {"ok": True, "request_id": 1, "label": "News run queued."}
    busy = client.post(NEWS_RUN, headers=ACT)
    assert (busy.status_code, busy.json()["detail"]) == (
        409,
        "A news run is already waiting or running.",
    )
    assert synced.execute(text("select count(*) from news_run_request")).scalar_one() == 1
    state = client.get(NEWS_RUN).json()
    assert state["open"] is True and state["latest"]["state"] == "queued"
    assert state["latest"]["requested_by"] == "trishi"
    # A running request is still one open request.
    synced.execute(text("update news_run_request set state = 'running', started_at = now()"))
    synced.commit()
    assert client.post(NEWS_RUN, headers=ACT).status_code == 409
    assert client.get(NEWS_RUN).json()["open"] is True


def test_run_news_now_cooldown_is_429_with_a_wait(client: TestClient, synced: Session) -> None:
    assert client.post(NEWS_RUN, headers=ACT).status_code == 200
    synced.execute(text("update news_run_request set state = 'done', finished_at = now()"))
    synced.commit()
    cooling = client.post(NEWS_RUN, headers=ACT)
    assert cooling.status_code == 429
    assert cooling.json()["detail"] == "Wait 10 min before the next news run."
    assert 0 < int(cooling.headers["retry-after"]) <= 600
    _age_news_requests(synced)
    assert client.post(NEWS_RUN, headers=ACT).status_code == 200  # after 10 min a new one runs
    state = client.get(NEWS_RUN).json()
    assert state["latest"]["id"] == 2 and state["open"] is True


def test_run_news_now_refuses_people_who_are_not_admin(synced: Session) -> None:
    app = create_app()
    # A viewer session, and an "admin" session whose person is not an admin in the table.
    for role, short in (("viewer", "kushal"), ("approver", "sid"), ("admin", "sid")):
        app.dependency_overrides[current_user] = lambda role=role, short=short: SignedIn(
            "x@y.z", role, short, "mock"
        )
        with TestClient(app) as c:
            post = c.post(NEWS_RUN, headers=ACT)
            assert post.status_code == 403, (role, short)
            assert post.json()["detail"] == "Only an admin can run news now."
            if role != "admin":
                assert c.get(NEWS_RUN).status_code == 403
    assert synced.execute(text("select count(*) from news_run_request")).scalar_one() == 0


def test_run_news_now_refuses_cross_site_and_a_missing_session(client: TestClient) -> None:
    evil = {**ACT, "Origin": "https://evil.example"}
    assert client.post(NEWS_RUN, headers=evil).status_code == 403
    cross = {**ACT, "Sec-Fetch-Site": "cross-site"}
    assert client.post(NEWS_RUN, headers=cross).status_code == 403
    assert client.get(NEWS_RUN).json() == {
        "latest": None,
        "open": False,
        "label": "No news run was asked for yet.",
    }
    with TestClient(client.app) as bare:  # no session
        assert bare.post(NEWS_RUN, headers=ACT).status_code == 401
        assert bare.get(NEWS_RUN).status_code == 401


def test_run_news_now_works_as_the_real_web_role(
    synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The web role has no table grant: the function and the view are its only way in."""
    from saley.core.db import dispose_engines
    from tests.conftest import RO_TEST_URL

    require_ro_role()
    monkeypatch.setenv("DATABASE_URL_RO", RO_TEST_URL["url"])
    get_settings.cache_clear()
    dispose_engines()
    try:
        with SignedClient(create_app()) as c:
            assert c.get(NEWS_RUN).status_code == 200
            assert c.post(NEWS_RUN, headers=ACT).status_code == 200
            assert c.get(NEWS_RUN).json()["latest"]["state"] == "queued"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        dispose_engines()


def test_commitments_keep_the_signers_open_undo_after_a_reload(
    client: TestClient, synced: Session
) -> None:
    """R5 T16a review: /api/commitments gives `undo_id` and `undo_until` only for the row whose
    newest Done the asking person signed, not undone and inside its window (as Today)."""
    cid = _promise(synced, "vaishnavi")
    as_v = {"X-Saley-Act": "1", "X-Saley-As": "vaishnavi"}
    done = client.post(f"/api/commitment/{cid}/done", headers=as_v)
    assert done.status_code == 200, done.text

    def row(who: str) -> dict:
        items = client.get("/api/commitments?state=all", headers={"X-Saley-As": who}).json()
        return next(i for i in items["items"] if i["id"] == cid)

    mine = row("vaishnavi")
    assert mine["undo_id"] == done.json()["write_id"] and mine["undo_until"]
    other = row("sid")  # another person never gets the signer's Undo (D155)
    assert other["undo_id"] is None and other["undo_until"] is None
    synced.execute(
        text("update decision set undo_until = now() - interval '1 minute' where id = :i"),
        {"i": done.json()["write_id"]},
    )
    synced.commit()
    expired = row("vaishnavi")
    assert expired["undo_id"] is None and expired["undo_until"] is None
