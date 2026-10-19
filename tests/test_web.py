"""saley.web: every R1 endpoint on the synced fixture sheet, read-only, dev sign-in."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.core.settings import get_settings
from saley.web import schemas
from saley.web.app import SignedIn, create_app, current_user


@pytest.fixture
def client(synced: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
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
    assert body["news"][0] == {"step": "Sources tried", "n": None}
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
    page = client.get("/")
    assert page.status_code == 200 and "Search everything" in page.text
    assert client.get("/static/app.js").status_code == 200
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert client.get("/api/status").headers["cache-control"] == "no-store"


def test_no_write_endpoints(client: TestClient) -> None:
    for path in ("/api/people", "/api/act", "/api/accounts"):
        assert client.post(path, json={}).status_code in (404, 405)


def test_outside_dev_every_api_call_is_refused(
    synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SALEY_ENV", "prod")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as c:
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
    app.dependency_overrides[current_user] = lambda: SignedIn("v@x", "viewer")
    with TestClient(app) as c:
        assert c.get("/api/health/full").status_code == 403
        assert c.get("/api/today").status_code == 200


def test_headers_limits_and_no_openapi(synced: Session) -> None:
    with TestClient(create_app()) as c:
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
    with TestClient(create_app()) as c:
        tile = next(t for t in c.get("/api/today").json()["tiles"] if t["key"] == "deals")
    assert tile["value"] == "0 of —"


# ---------------------------------------- R1.1 API contract (C) and fixes (A)


EXPECTED_KEYS = {
    "/api/today": {
        "greeting",
        "date_label",
        "tiles",
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
        "sheet_mode",
        "late_total",
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
    assert r.status_code == 422 and r.json()["detail"] == "Unknown parameter: debug"
    assert client.get("/api/today?user=sid").status_code == 200  # declared alias is fine


def test_cross_site_request_is_refused(client: TestClient) -> None:
    assert client.get("/api/today", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/today", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    same = client.get("/api/today", headers={"Origin": "http://testserver"})
    assert same.status_code == 200
    assert "access-control-allow-origin" not in same.headers


def test_status_bar_states(synced: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from saley.web import app as web_app

    with TestClient(create_app()) as c:
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
    with TestClient(create_app()) as c:
        tiles = c.get("/api/today").json()["tiles"]
    assert all(
        t["value"] is None and t["detail"] == "No data yet. The sheet has not synced."
        for t in tiles
    )


def test_scorecard_reply_has_no_source(client: TestClient) -> None:
    row = client.get("/api/scorecard").json()["items"][0]
    assert row["reply_pct"] is None and row["reply_note"] == "No source until R2"


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
    with TestClient(create_app()) as c:
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
    with TestClient(create_app()) as c:
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
    with TestClient(create_app()) as c:
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


def _has_ro_role(db: Session) -> bool:
    return bool(db.execute(text("select 1 from pg_roles where rolname = 'saley_ro'")).first())


def test_web_role_reads_only_views(synced: Session) -> None:
    """saley_ro has SELECT on v_* views and the two snapshot tables, and on no other table."""
    assert _has_ro_role(synced), "run deploy/sql/roles.sql: the gate needs the saley_ro role"
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


def test_every_endpoint_works_as_the_real_web_role(
    synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API runs as saley_ro in production. Every endpoint must work with views only."""
    from saley.core.db import dispose_engines
    from saley.core.settings import get_settings
    from tests.conftest import RO_TEST_URL

    assert _has_ro_role(synced)
    person = synced.execute(text("select min(id) from contact")).scalar_one()
    account = synced.execute(text("select min(id) from account")).scalar_one()
    monkeypatch.setenv("DATABASE_URL_RO", RO_TEST_URL["url"])
    get_settings.cache_clear()
    dispose_engines()
    try:
        from saley.core.db import ro_engine

        with ro_engine().connect() as conn:
            assert conn.execute(text("select current_user")).scalar_one() == "saley_ro"
        with TestClient(create_app()) as c:
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
            ):
                r = c.get(path)
                assert r.status_code == 200, (path, r.text)
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
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        r = c.get("/api/today")
    assert r.status_code == 500
    assert r.json() == {"detail": "Server error. The error is in the log."}
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
