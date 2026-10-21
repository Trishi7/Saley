"""Per-user seen (R3-U01, D75): seen is per person, a list says how many it left out.

The page posts the ids it showed. The function that records them is the only door the web role
has to `user_seen`; these tests also guard that door with abuse cases.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.core.clock import today_ist
from saley.core.models import Event
from saley.web.app import create_app
from tests.conftest import SignedClient, sql_call
from tests.test_signals import add_story, link_all

H = {"X-Saley-Act": "1"}
SID = {"X-Saley-As": "sid"}
VAISHNAVI = {"X-Saley-As": "vaishnavi"}


@pytest.fixture
def client(synced: Session) -> Iterator[TestClient]:
    with SignedClient(create_app()) as c:
        yield c


def mark(
    client: TestClient, who: dict[str, str], item_type: str, ids: list[int], **extra: Any
) -> Any:
    body = {"item_type": item_type, "ids": ids, **extra}
    return client.post("/api/seen", json=body, headers={**H, **who})


def listed(client: TestClient, who: dict[str, str], path: str = "/api/signals") -> dict[str, Any]:
    return client.get(path, headers=who).json()


def test_seen_is_per_user_with_left_out_count(client: TestClient, synced: Session) -> None:
    """R3-U01: A sees, B still sees, A gets "left out 1 you saw"."""
    one = add_story(synced, "First market story", importance=3)
    two = add_story(synced, "Second market story", importance=2)

    assert {i["id"] for i in listed(client, SID)["items"]} == {one.id, two.id}
    assert mark(client, SID, "story", [one.id], query_id="signals-all-1").json() == {
        "ok": True,
        "marked": 1,
    }

    a = listed(client, SID)
    assert [i["id"] for i in a["items"]] == [two.id]
    assert a["left_out_seen"] == 1 and a["total"] == 1
    assert a["left_out_note"] == "1 you saw is left out. Show it."
    b = listed(client, VAISHNAVI)
    assert {i["id"] for i in b["items"]} == {one.id, two.id}  # B still sees both
    assert b["left_out_seen"] == 0 and b["left_out_note"] is None

    shown = listed(client, SID, "/api/signals?include_seen=true")
    assert {i["id"] for i in shown["items"]} == {one.id, two.id}
    assert {i["id"]: i["seen"] for i in shown["items"]} == {one.id: True, two.id: False}
    assert shown["left_out_seen"] == 0
    rows = synced.execute(
        text("select user_short, item_type, item_id, query_id from v_user_seen")
    ).all()
    assert [tuple(r) for r in rows] == [("sid", "story", one.id, "signals-all-1")]


def test_marking_twice_is_harmless_and_the_first_time_stays(
    client: TestClient, synced: Session
) -> None:
    story = add_story(synced, "A story")
    first = mark(client, SID, "story", [story.id, story.id], query_id="first")
    again = mark(client, SID, "story", [story.id], query_id="second")
    assert first.json()["marked"] == 1 and again.json()["marked"] == 0
    assert synced.scalar(text("select query_id from user_seen")) == "first"


def test_linked_events_and_suggestions_have_their_own_seen(
    client: TestClient, synced: Session
) -> None:
    story = add_story(synced, "Sarvam AI raises a round", importance=4)
    link_all(synced)
    signal_id = synced.scalar(text("select id from signal"))
    assert mark(client, SID, "signal", [signal_id]).status_code == 200
    linked = listed(client, SID, "/api/signals?tab=linked")
    assert linked["items"] == [] and linked["left_out_seen"] == 1
    # The same number as a story id is a different item: the All tab is not touched.
    assert [i["id"] for i in listed(client, SID)["items"]] == [story.id]

    synced.execute(text("delete from event"))
    synced.add(Event(sheet_row_key="e:1", name="Soon", start=today_ist() + timedelta(days=2)))
    synced.commit()
    event_id = synced.scalar(text("select id from event"))
    assert mark(client, SID, "event", [event_id]).status_code == 200
    events = listed(client, SID, "/api/events")
    assert events["items"] == [] and events["left_out_seen"] == 1
    assert len(listed(client, VAISHNAVI, "/api/events")["items"]) == 1

    account = synced.scalar(text("select id from account where name = 'AI4Bharat'"))
    assert mark(client, SID, "suggest_newco", [account]).status_code == 200
    newco = listed(client, SID, "/api/suggest/newco")
    assert newco["items"] == [] and newco["left_out_seen"] == 1
    assert listed(client, SID, "/api/suggest/newco?include_seen=true")["items"][0]["seen"] is True
    assert len(listed(client, VAISHNAVI, "/api/suggest/newco")["items"]) == 1


def test_the_function_checks_everything_itself(synced: Session) -> None:
    def call(user: str, kind: str, ids: str, query: str | None = None) -> dict[str, Any]:
        return sql_call(
            synced,
            f"select saley_mark_seen(:u, :k, {ids}::bigint[], :q)",
            u=user,
            k=kind,
            q=query,
        )

    assert call("sid", "story", "array[1, 2]")["marked"] == 2
    assert call("ghost", "story", "array[1]")["code"] == "not_allowed"
    assert call("sid", "story", "array[0]")["code"] == "bad_request"
    assert call("sid", "story", "array[-4]")["code"] == "bad_request"
    assert call("sid", "story", "array[null]::bigint[]")["code"] == "bad_request"
    assert call("sid", "other", "array[1]")["code"] == "bad_request"
    assert call("sid", "story", "array[]::bigint[]")["code"] == "bad_request"
    assert call("sid", "story", "(select array_agg(i) from generate_series(1, 201) i)")["code"] == (
        "bad_request"
    )
    assert call("sid", "story", "array[3]", "x" * 65)["code"] == "bad_request"
    # a viewer may be marked too: seen is for anyone who is a user
    synced.execute(text("update users set role = 'viewer' where short = 'kushal'"))
    synced.commit()
    try:
        assert call("kushal", "story", "array[1]")["ok"] is True
    finally:  # the users table is not cleaned between tests
        synced.execute(text("update users set role = 'approver' where short = 'kushal'"))
        synced.commit()


# ------------------------------------------------------------------ abuse cases for POST /api/seen


def test_seen_post_needs_the_header_and_a_same_origin_page(client: TestClient) -> None:
    body = {"item_type": "story", "ids": [1]}
    assert client.post("/api/seen", json=body).status_code == 403
    assert client.post("/api/seen", json=body, headers={"X-Saley-Act": "0"}).status_code == 403
    for extra in (
        {"Origin": "https://evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "https://[x"},
    ):
        assert client.post("/api/seen", json=body, headers={**H, **extra}).status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {"item_type": "story", "ids": []},
        {"item_type": "story", "ids": list(range(1, 202))},
        {"item_type": "story", "ids": [0]},
        {"item_type": "story", "ids": [-1]},
        {"item_type": "story", "ids": ["1 or 1=1"]},
        {"item_type": "story", "ids": [2**63]},
        {"item_type": "story", "ids": [1.5]},
        {"item_type": "users", "ids": [1]},
        {"item_type": "story'; drop table user_seen; --", "ids": [1]},
        {"item_type": "story", "ids": [1], "query_id": "x" * 65},
        {"item_type": "story", "ids": [1], "query_id": "a b; drop"},
        {"item_type": "story", "ids": [1], "user": "kushal"},
        {"item_type": "story"},
        {"ids": [1]},
        [],
    ],
)
def test_seen_post_refuses_bad_bodies(client: TestClient, body: Any) -> None:
    assert client.post("/api/seen", json=body, headers=H).status_code == 422


def test_seen_post_refuses_odd_requests(client: TestClient, synced: Session) -> None:
    assert client.post("/api/seen", content=b"not json", headers=H).status_code == 422
    big = {"item_type": "story", "ids": [1], "query_id": "a" * 20_000}
    assert client.post("/api/seen", json=big, headers=H).status_code == 413
    for method in ("get", "put", "patch", "delete"):
        assert getattr(client, method)("/api/seen", headers=H).status_code in (404, 405)
    # X-Saley-As works for a known name only, in dev, from this machine.
    assert mark(client, {"X-Saley-As": "Sid; drop table users"}, "story", [1]).status_code == 403
    assert mark(client, {"X-Saley-As": "ghost"}, "story", [1]).status_code == 403
    away = TestClient(client.app, client=("203.0.113.9", 5000))
    assert (
        away.post("/api/seen", json={"item_type": "story", "ids": [1]}, headers=H).status_code
        == 401
    )
    assert synced.scalar(text("select count(*) from user_seen")) == 0


def test_the_seen_post_cannot_write_for_someone_else(client: TestClient, synced: Session) -> None:
    mark(client, SID, "story", [5])
    owner = synced.scalar(text("select u.short from user_seen s join users u on u.id = s.user_id"))
    assert owner == "sid"
