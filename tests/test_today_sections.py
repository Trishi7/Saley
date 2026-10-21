"""R3 phase 7: Today "needs a look" and "Saley suggests" in /api/today, and the seen/QA helpers.

No network. Stories and events are inserted as rows. Order of Today (docs/08 view 1):
numbers, my tasks, needs a look, blocked, Saley suggests.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from saley.core.models import Signal
from saley.web.app import create_app
from tests.conftest import SignedClient
from tests.test_signals import add_people, add_story, event, link_all, reset_events


@pytest.fixture
def client(synced: Session) -> Iterator[TestClient]:
    with SignedClient(create_app()) as c:
        yield c


def today(client: TestClient) -> dict[str, Any]:
    r = client.get("/api/today?user=sid")
    assert r.status_code == 200, r.text
    return r.json()


def test_today_sections_come_in_the_documented_order(client: TestClient) -> None:
    body = today(client)
    keys = list(body)
    assert keys.index("tiles") < keys.index("my_tasks") < keys.index("needs_look")
    assert keys.index("needs_look") < keys.index("saley_suggests")


def test_needs_a_look_lists_strong_linked_stories_and_close_events(
    client: TestClient, synced: Session
) -> None:
    add_story(synced, "Sarvam AI raises a round", importance=5)
    add_story(synced, "Sarvam AI hires a new CFO", importance=2)
    link_all(synced)
    reset_events(synced, event("e:9", "Tomorrow Summit", "London", 1), event("e:8", "Far", "X", 30))
    block = today(client)["needs_look"]
    assert block["total"] == 2
    kinds = {(i["kind"], i["title"]) for i in block["items"]}
    assert kinds == {("signal", "Sarvam AI raises a round"), ("event", "Tomorrow Summit")}
    signal = next(i for i in block["items"] if i["kind"] == "signal")
    assert signal["detail"] == "Sarvam AI · rated 5 of 5"
    assert signal["verify_note"] == "One match only. Check it."  # D74: one match
    assert signal["link"] == {"view": "signals", "filter": "linked"}
    assert signal["href"].startswith("https://")
    ev = next(i for i in block["items"] if i["kind"] == "event")
    assert ev["detail"] == "London · In 1 day" and ev["link"]["view"] == "events"
    assert ev["verify_note"] is None


def test_needs_a_look_is_empty_with_a_plain_line_and_names_the_mail_gap(
    client: TestClient, synced: Session
) -> None:
    reset_events(synced)
    block = today(client)["needs_look"]
    assert block["items"] == [] and block["total"] == 0
    assert block["empty"].startswith("No items. Last computed")
    assert block["replies_note"] == "Replies: mail not configured"  # Q-R2-3


def test_needs_a_look_cuts_the_list_but_keeps_the_total(
    client: TestClient, synced: Session
) -> None:
    for n in range(12):
        add_story(synced, f"Sarvam AI raises round number {n}", importance=5)
    link_all(synced)
    reset_events(synced)
    block = today(client)["needs_look"]
    assert block["total"] == 12 and len(block["items"]) == 8


def test_a_dismissed_or_old_signal_does_not_need_a_look(
    client: TestClient, synced: Session
) -> None:
    add_story(synced, "Sarvam AI raises a round", importance=5)
    old = timedelta(days=20)
    add_story(synced, "Sarvam AI old news from last month", importance=5, seen_ago=old)
    link_all(synced)
    reset_events(synced)
    assert today(client)["needs_look"]["total"] == 1  # the 20-day-old story is out
    synced.execute(text("update signal set state = 'dismissed'"))
    synced.commit()
    assert today(client)["needs_look"]["total"] == 0


def test_saley_suggests_gives_the_top_three_with_one_action_each(
    client: TestClient, synced: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    add_people(synced, raw_sheet)
    block = today(client)["saley_suggests"]
    items = block["items"]
    assert 1 <= len(items) <= 3
    assert {i["kind"] for i in items} <= {"newco", "warm"}
    scores = [i["score"] for i in items]
    assert scores == sorted(scores, reverse=True)
    newco = next(i for i in items if i["kind"] == "newco")
    assert newco["can_find_people"] is True and newco["account_id"] == newco["id"]
    assert all(i["parts"] and i["reason"] for i in items)
    assert block["empty"].startswith("No items. Last computed")


def test_saley_suggests_is_empty_when_nothing_qualifies(
    client: TestClient, synced: Session
) -> None:
    synced.execute(text("update account set removed_at = now()"))
    synced.commit()
    block = today(client)["saley_suggests"]
    assert block["items"] == [] and block["empty"]


def test_today_sections_do_not_hide_what_the_person_saw(
    client: TestClient, synced: Session
) -> None:
    """Today lists what needs action now. Seen filters the Signals and Events pages only."""
    story = add_story(synced, "Sarvam AI raises a round", importance=5)
    link_all(synced)
    reset_events(synced)
    sid = synced.scalar(select(Signal.id).where(Signal.story_id == story.id))
    r = client.post(
        "/api/seen",
        json={"item_type": "signal", "ids": [sid]},
        headers={"X-Saley-Act": "1", "X-Saley-As": "sid"},
    )
    assert r.status_code == 200
    assert today(client)["needs_look"]["total"] == 1
    assert client.get("/api/signals?tab=linked", headers={"X-Saley-As": "sid"}).json()["total"] == 0
