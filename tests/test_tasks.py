"""My tasks per person and the "working on" facts (R2 phase 4: R2-T01, R2-P01).

Why: Today shows each person the tasks the rules give them (D31). The rules are pure functions
(tests below, no database); the API tests check the real views and the narrow web role path.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.core.clock import now_ist, today_ist
from saley.intel import tasks as rules
from saley.intel.facts import EXPIRY, refresh_working_on
from saley.web.app import create_app
from tests.conftest import SignedClient, contact_id, sql_act

TODAY = date(2026, 10, 19)  # a Monday


def row(**kw) -> rules.ContactRow:
    kw.setdefault("contact_id", 1)
    kw.setdefault("name", "Pat")
    return rules.ContactRow(**kw)


def kinds(found: list[rules.Task]) -> list[str]:
    return [t.kind for t in found]


@pytest.fixture
def client(synced: Session) -> Iterator[TestClient]:
    with SignedClient(create_app()) as c:
        yield c


def mine(client: TestClient, person: str) -> list[dict]:
    response = client.get(f"/api/today?user={person}", headers={"X-Saley-As": person})
    assert response.status_code == 200
    block = response.json()["my_tasks"]
    assert block["person"] == person
    return block["items"]


# ------------------------------------------------------------------ R2-T01


def test_my_tasks_per_person(client: TestClient, synced: Session) -> None:
    """R2-T01: Sid gets LinkedIn work, Vaishnavi research and emails, Kushal approvals (D31)."""
    today = today_ist()
    ro_one = contact_id(synced, "Ro One")
    synced.execute(
        text(
            "update contact set connected = true, contacted = true, li_status = 'Connected', "
            "li_connected_date = :d, owner = 'vaishnavi', owner_source = 'default' "
            "where name = 'Ro One'"
        ),
        {"d": today - timedelta(days=5)},
    )
    synced.execute(
        text(
            "update contact set li_connected_date = :d, first_contact_date = :d "
            "where name = 'Dinesh Tewari'"
        ),
        {"d": today - timedelta(days=5)},
    )
    synced.execute(
        text(
            "insert into decision(kind, choice, reason, state, user_id) values "
            "('strategy_decision', 'D99', 'Test decision', 'proposed', "
            "(select id from users where short = 'trishi'))"
        )
    )
    synced.execute(text("insert into proposal(type, card_key) values ('meeting_status', 'k')"))
    synced.commit()
    alex = contact_id(synced, "Alex Search")
    sql_act(synced, "sid", [{"role": "li_connected_date", "value": today.isoformat()}], [alex])

    sid_all = mine(client, "sid")
    # R5 T3: Alex's write is Sid's own tick inside its undo window: the row stays, as an Undo.
    ticked = [i for i in sid_all if i["done"]]
    assert [(i["kind"], i["name"]) for i in ticked] == [("li_confirm", "Alex Search")]
    assert ticked[0]["action"] is None and ticked[0]["undo_id"] and ticked[0]["undo_until"]
    sid = [i for i in sid_all if not i["done"]]
    by_kind: dict[str, list[dict]] = {}
    for item in sid:
        by_kind.setdefault(item["kind"], []).append(item)
    names = {k: sorted(i["name"] for i in v) for k, v in by_kind.items()}
    # LI connections to confirm: Alex already has a write waiting, so he is not asked again.
    assert names["li_confirm"] == ["David Gamez", "Sshubam Verma"]
    assert names["li_first"] == ["Ro Two"]  # Ro One is connected now
    assert names["reconnect"] == ["Alex Search", "Pat Park"]
    first = by_kind["li_first"][0]
    assert first["action"]["endpoint"] == "/api/act"
    # D153: the body names the task and carries no value; the server picks the values.
    body = first["action"]["body"]
    assert (body["action"], body["target_ids"]) == ("write", [first["contact_id"]])
    assert body["payload"] == {
        "cells": None,
        "task": None,
        "cycle": None,
        "proposal_id": None,
        "done": "li_first",
    }
    assert by_kind["reconnect"][0]["action"]["body"]["action"] == "task_done"
    assert by_kind["li_confirm"][0]["title"].startswith("Confirm LinkedIn connection: ")

    vaishnavi = mine(client, "vaishnavi")
    found = {(i["kind"], i["name"]) for i in vaishnavi}
    assert ("add_email", "Ro One") in found
    assert ("email1", "Dinesh Tewari") in found
    assert not any(k in ("li_confirm", "li_first", "decision") for k, _ in found)
    add_email = next(i for i in vaishnavi if i["kind"] == "add_email")
    assert add_email["title"] == "Add email for Ro One"
    assert add_email["action"] is None  # D154: no Done; it ends when an email is saved
    assert add_email["input_role"] == "email"
    assert add_email["soon"] == "email_write"  # EMAIL_WRITE_ALLOWED is false

    kushal = mine(client, "kushal")
    assert sorted(i["kind"] for i in kushal) == ["decision", "inbox_cards", "review_write"]
    decision = next(i for i in kushal if i["kind"] == "decision")
    assert decision["title"] == "Confirm decision D99"
    assert decision["action"]["endpoint"].endswith("/confirm")
    review = next(i for i in kushal if i["kind"] == "review_write")
    assert review["undo_id"] and review["name"] == "Alex Search"
    assert review["title"] == "Sid saved LI Connected Date for Alex Search"
    assert ro_one  # the person exists in the fixture


def test_everyone_sees_commitments_due_today(client: TestClient, synced: Session) -> None:
    today = today_ist()
    synced.execute(
        text(
            "insert into commitment(owner, text, due, source_type) select id, :t, :d, 'mail' "
            "from users where short = 'vaishnavi'"
        ),
        {"t": "I will send the NDA by Monday.", "d": today},
    )
    synced.execute(
        text(
            "insert into commitment(owner, text, due) select id, 'Later', :d "
            "from users where short = 'vaishnavi'"
        ),
        {"d": today + timedelta(days=1)},
    )
    synced.commit()
    items = mine(client, "vaishnavi")
    due = [i for i in items if i["kind"] == "commitment"]
    assert [i["title"] for i in due] == ["Due today: I will send the NDA by Monday."]
    assert due[0]["why"] == "From Mail"
    assert due[0]["action"]["body"] is None
    assert due[0]["action"]["endpoint"].endswith("/done")
    assert not [i for i in mine(client, "sid") if i["kind"] == "commitment"]


def test_dev_header_picks_the_person_and_only_on_this_machine(
    client: TestClient, synced: Session
) -> None:
    """D50: X-Saley-As sets the person for Today, from this machine only."""
    default = client.get("/api/today").json()["my_tasks"]
    assert default["person"] == "trishi" and default["items"] == []
    assert default["empty"] == "No tasks for you today."
    as_sid = client.get("/api/today", headers={"X-Saley-As": "sid"}).json()["my_tasks"]
    assert as_sid["person"] == "sid" and as_sid["total"] > 0
    assert client.get("/api/today", headers={"X-Saley-As": "Sid; drop"}).status_code == 403
    away = TestClient(client.app, client=("203.0.113.9", 5000))
    assert away.get("/api/today").status_code == 401


def test_unknown_person_gets_no_tasks(client: TestClient) -> None:
    block = client.get("/api/today?user=nobody").json()["my_tasks"]
    assert block == {
        "person": "nobody",
        "items": [],
        "total": 0,
        "offset": 0,
        "more": 0,
        "more_label": None,
        "empty": "No tasks for you today.",
    }


def test_email_write_flag_offers_an_email_input(
    client: TestClient, synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from saley.core.settings import get_settings

    synced.execute(
        text(
            "update contact set connected = true, li_connected_date = :d, owner = 'vaishnavi' "
            "where name = 'Ro One'"
        ),
        {"d": today_ist() - timedelta(days=9)},
    )
    synced.commit()
    monkeypatch.setenv("EMAIL_WRITE_ALLOWED", "true")
    get_settings.cache_clear()
    try:
        research = [i for i in mine(client, "vaishnavi") if i["kind"] == "add_email"]
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert research and research[0]["input_role"] == "email" and research[0]["soon"] is None


def test_done_on_a_task_removes_it(client: TestClient, synced: Session) -> None:
    """The click-path: Done calls the task's own action, and the task is gone on reload."""
    task = next(i for i in mine(client, "sid") if i["kind"] == "reconnect")
    response = client.post(
        task["action"]["endpoint"], json=task["action"]["body"], headers={"X-Saley-Act": "1"}
    )
    assert response.status_code == 200
    left = [i["key"] for i in mine(client, "sid") if i["kind"] == "reconnect"]
    assert task["key"] not in left


# ------------------------------------------------------------------ the rules, no database


def test_first_picks_are_p1_first_then_bucket_and_capped_at_five() -> None:
    people = [
        row(contact_id=i, name=f"N{i}", is_p1=i in (7, 8), application_bucket=b)
        for i, b in enumerate("FEDCBAxA", start=1)
    ]
    found = rules.first_pick_tasks(people)
    assert len(found) == rules.PICK_COUNT == 5
    # P1 people first (8 has bucket A, 7 has no bucket), then the rest by bucket A-F.
    assert [t.contact_id for t in found] == [8, 7, 6, 5, 4]


def test_first_picks_skip_contacted_requested_and_waiting_writes() -> None:
    people = [
        row(contact_id=1, contacted=True),
        row(contact_id=2, li_requested=True),
        row(contact_id=3, open_roles=frozenset({"first_contact_date"})),
        row(contact_id=4, stage="Connected"),
        row(contact_id=5),
    ]
    assert [t.contact_id for t in rules.first_pick_tasks(people)] == [5]
    assert rules.first_pick_tasks(people, count=0) == []


def test_li_confirm_needs_a_request_and_no_date() -> None:
    people = [
        row(contact_id=1, li_requested=True),
        row(contact_id=2, li_requested=True, li_connected_date=TODAY),
        row(contact_id=3, li_requested=False),
        row(contact_id=4, li_requested=True, stage="Lost"),
        row(contact_id=5, li_requested=True, open_roles=frozenset({"li_connected_date"})),
    ]
    assert [t.contact_id for t in rules.li_confirm_tasks(people)] == [1]


def test_add_email_needs_more_than_three_days_and_a_known_date() -> None:
    def case(days: int | None, **kw) -> list[rules.Task]:
        when = None if days is None else TODAY - timedelta(days=days)
        return rules.add_email_tasks(
            [row(connected=True, li_connected_date=when, **kw)], TODAY, False
        )

    assert case(3) == []  # exactly 3 days is not "more than 3"
    assert len(case(4)) == 1
    assert case(None) == []  # no connected date: the age is unknown, so no task
    assert case(10, has_email=True) == []
    assert case(10, li_dm_sent=True) == []
    assert case(10, email_added=True) == []  # D154: a live email_write hides the task
    assert case(10, open_roles=frozenset({"email"})) == []
    assert (
        rules.add_email_tasks(
            [row(connected=True, li_connected_date=TODAY - timedelta(days=9))], TODAY, True
        )[0].soon
        is None
    )
    off = case(10)[0]  # D154: with email writes off the task stays, flagged "coming soon"
    assert (off.kind, off.action, off.input_role, off.soon) == (
        "add_email",
        None,
        "email",
        "email_write",
    )
    assert off.due == TODAY - timedelta(days=10) + timedelta(days=4)


def test_email_steps_follow_the_timers_and_the_anchor() -> None:
    base = {"connected": True, "has_email": True, "stage": "Connected"}
    anchor = TODAY - timedelta(days=2)
    due_now = rules.email_step_tasks([row(last_step_date=anchor, **base)], TODAY)
    assert kinds(due_now) == ["email1"] and due_now[0].due == TODAY
    # Not due yet: the anchor is yesterday and email 1 waits two days.
    assert (
        rules.email_step_tasks([row(last_step_date=TODAY - timedelta(days=1), **base)], TODAY) == []
    )
    # D51: with no step date the First Contact Date is the anchor; with neither there is no task.
    assert kinds(rules.email_step_tasks([row(first_contact_date=anchor, **base)], TODAY)) == [
        "email1"
    ]
    assert rules.email_step_tasks([row(**base)], TODAY) == []
    # Email 2 waits seven days after email 1 and needs email 1 sent.
    sent1 = {"email_sent": (True, False, False), "email_dates": (anchor, None, None)}
    assert rules.email_step_tasks([row(last_step_date=anchor, **sent1, **base)], TODAY) == []
    week = TODAY - timedelta(days=7)
    later = rules.email_step_tasks([row(last_step_date=week, **sent1, **base)], TODAY)
    assert kinds(later) == ["email2"]
    # All three sent: nothing more.
    done = {"email_sent": (True, True, True)}
    assert rules.email_step_tasks([row(last_step_date=week, **done, **base)], TODAY) == []
    # Skips: no address, not connected, a meeting exists, Done already ticked.
    for extra in (
        {"has_email": False},
        {"connected": False},
        {"meeting_date": TODAY},
        {"stage": "Listed"},
        {"done_tasks": frozenset({"task:email1"})},
    ):
        assert (
            rules.email_step_tasks([row(last_step_date=anchor, **{**base, **extra})], TODAY) == []
        )


def test_step_tasks_go_to_vaishnavi_unless_first_contact_names_sid() -> None:
    base = {"connected": True, "has_email": True, "stage": "Connected", "last_step_date": TODAY}
    far = TODAY + timedelta(days=5)
    sid_owned = row(owner="sid", owner_source="first_contact", **base)
    li_owned = row(owner="sid", owner_source="li_addition", **base)
    steps = rules.email_step_tasks([sid_owned, li_owned], far)
    assert [(t.person, t.contact_id) for t in steps] == [("sid", 1), ("vaishnavi", 1)]


def test_meeting_tasks_today_and_booked_after_the_date() -> None:
    people = [
        row(contact_id=1, meeting_date=TODAY, meeting_status="Booked"),
        row(contact_id=2, meeting_date=TODAY, meeting_status="completed"),
        row(contact_id=3, meeting_date=TODAY - timedelta(days=1), meeting_status="booked"),
        row(contact_id=4, meeting_date=TODAY - timedelta(days=1), meeting_status=""),
        row(
            contact_id=5,
            meeting_date=TODAY,
            meeting_status="",
            open_roles=frozenset({"meeting_status"}),
        ),
        row(contact_id=6, meeting_date=TODAY + timedelta(days=1), meeting_status="booked"),
    ]
    found = rules.meeting_tasks(people, TODAY)
    assert [(t.kind, t.contact_id) for t in found] == [("meeting", 1), ("status_check", 3)]
    assert found[0].action and found[0].action.body["payload"] == {"done": "meeting"}
    assert found[1].action and found[1].action.body["payload"] == {"done": "status_check"}


def test_reconnect_is_a_task_done_and_hidden_once_ticked() -> None:
    due = TODAY - timedelta(days=1)
    people = [
        row(contact_id=1, reconnect_due=True, reconnect_due_on=due),
        row(contact_id=2, reconnect_due=True, done_tasks=frozenset({"task:reconnect"})),
        row(contact_id=3, reconnect_due=False),
    ]
    found = rules.reconnect_tasks(people)
    assert [t.contact_id for t in found] == [1]
    assert found[0].action and found[0].action.body["action"] == "task_done"


def test_kushal_decisions_are_capped_and_own_writes_are_not_reviewed() -> None:
    proposed = [rules.ProposedDecision(i, f"D{i}") for i in range(1, 30)]
    writes = [
        rules.RecentWrite(1, "sid", "Sid", "meeting_status", 5, "Pat"),
        rules.RecentWrite(2, "kushal", "Kushal", "meeting_status", 5, "Pat"),
    ]
    inputs = rules.Inputs(proposed=proposed, open_cards=3, recent_writes=writes)
    found = rules.tasks_for("kushal", inputs, TODAY)
    assert kinds(found).count("decision") == rules.DECISION_CAP
    assert [t.undo_id for t in found if t.kind == "review_write"] == [1]
    assert [t.params["n"] for t in found if t.kind == "inbox_cards"] == [3]
    assert rules.card_task(0) == []
    # Sid and Vaishnavi get none of Kushal's tasks.
    assert rules.tasks_for("sid", inputs, TODAY) == []


def test_commitments_only_for_the_owner_and_only_today() -> None:
    items = [
        rules.CommitmentRow(1, "sid", "A", TODAY),
        rules.CommitmentRow(2, "sid", "B", TODAY + timedelta(days=1)),
        rules.CommitmentRow(3, "sid", "C", None),
        rules.CommitmentRow(4, "sid", "D", TODAY, state="done"),
        rules.CommitmentRow(5, "vaishnavi", "E", TODAY),
    ]
    assert [t.key for t in rules.commitment_tasks("sid", items, TODAY)] == ["commitment:1"]


def test_tasks_are_ordered_by_kind() -> None:
    inputs = rules.Inputs(
        contacts=[
            row(contact_id=1, li_requested=True),
            row(contact_id=2, meeting_date=TODAY, meeting_status="booked"),
            row(contact_id=3),
        ],
    )
    order = kinds(rules.tasks_for("sid", inputs, TODAY))
    assert order == ["meeting", "li_confirm", "li_first", "li_first"]


# ------------------------------------------------------------------ R2-P01 and facts


def set_cells(db: Session, name: str, about: str | None, paper: str | None) -> int:
    db.execute(
        text("update contact set about = :a, paper_url = :p where name = :n"),
        {"a": about, "p": paper, "n": name},
    )
    db.commit()
    return contact_id(db, name)


def test_drawer_working_on_from_facts(client: TestClient, synced: Session) -> None:
    """R2-P01: the drawer shows what a person works on with its source and date."""
    dinesh = set_cells(
        synced, "Dinesh Tewari", "Evaluates speech models", "https://arxiv.org/abs/2401.00001"
    )
    david = set_cells(synced, "David Gamez", None, "https://example.ac.uk/paper.pdf")
    ro_one = contact_id(synced, "Ro One")
    stats = refresh_working_on(synced)
    synced.commit()
    assert (stats.added, stats.extended, stats.ended) == (2, 0, 0)

    shown = client.get(f"/api/people/{dinesh}").json()["item"]["working_on"]
    assert shown == {
        "found": True,
        "text": "Evaluates speech models",
        "source": "Outreach PoCs, About PoC",
        "date": today_ist().isoformat(),
        "href": "https://arxiv.org/abs/2401.00001",
    }
    paper_only = client.get(f"/api/people/{david}").json()["item"]["working_on"]
    assert paper_only["text"] == "Research paper link"
    assert paper_only["source"] == "Outreach PoCs, Research Paper Link"
    none = client.get(f"/api/people/{ro_one}").json()["item"]["working_on"]
    assert none == {
        "found": False,
        "text": "None on file",
        "source": None,
        "date": None,
        "href": None,
    }
    # The People list carries the same line (docs/08 view 4).
    rows = {r["id"]: r for r in client.get("/api/people?filter=all").json()["items"]}
    assert rows[dinesh]["working_on"]["text"] == "Evaluates speech models"
    assert rows[ro_one]["working_on"]["found"] is False

    fact = synced.execute(
        text(
            "select source_type, source_ref, expires_at - observed_at "
            "from fact where entity_id = :i"
        ),
        {"i": dinesh},
    ).one()
    assert fact[0] == "about_poc" and fact[1].startswith("Outreach PoCs:")
    assert fact[2] == EXPIRY


def test_a_javascript_link_in_the_paper_cell_is_not_a_link(
    client: TestClient, synced: Session
) -> None:
    person = set_cells(synced, "Dinesh Tewari", "Voice data", "javascript:alert(1)")
    refresh_working_on(synced)
    synced.commit()
    shown = client.get(f"/api/people/{person}").json()["item"]["working_on"]
    assert shown["text"] == "Voice data" and shown["href"] is None


def test_refresh_is_idempotent_and_a_change_supersedes_the_old_fact(synced: Session) -> None:
    person = set_cells(synced, "Dinesh Tewari", "First line", None)
    first = refresh_working_on(synced)
    again = refresh_working_on(synced)
    assert (first.added, again.added, again.extended) == (1, 0, 1)
    synced.execute(text("update contact set about = 'Second line' where id = :i"), {"i": person})
    changed = refresh_working_on(synced)
    synced.commit()
    assert changed.added == 1
    facts = synced.execute(
        text("select value ->> 'text', superseded_by is not null from fact order by id")
    ).all()
    assert facts == [("First line", True), ("Second line", False)]
    live = synced.execute(text("select text from v_fact_working_on")).scalar_one()
    assert live == "Second line"


def test_a_cleared_cell_ends_the_fact(synced: Session) -> None:
    person = set_cells(synced, "Dinesh Tewari", "Some work", None)
    refresh_working_on(synced)
    synced.execute(text("update contact set about = null where id = :i"), {"i": person})
    ended = refresh_working_on(synced)
    synced.commit()
    assert ended.ended == 1
    assert synced.execute(text("select count(*) from v_fact_working_on")).scalar_one() == 0


def test_expired_facts_leave_the_view_and_teammate_facts_stay(synced: Session) -> None:
    person = set_cells(synced, "Dinesh Tewari", "Old news", None)
    refresh_working_on(synced, now_ist() - EXPIRY - timedelta(days=1))
    synced.commit()
    assert synced.execute(text("select count(*) from v_fact_working_on")).scalar_one() == 0
    synced.execute(
        text(
            "insert into fact(entity_type, entity_id, kind, value, source_type, author) "
            "values ('contact', :i, 'working_on', cast(:v as jsonb), 'teammate', 'sid')"
        ),
        {"i": person, "v": '{"text": "Said on a call"}'},
    )
    refresh_working_on(synced)
    synced.commit()
    teammate = synced.execute(
        text(
            "select expires_at is null, superseded_by is null from fact "
            "where source_type = 'teammate'"
        )
    ).one()
    assert tuple(teammate) == (True, True)  # never touched by the sheet refresh


def test_long_and_odd_text_is_trimmed_not_rejected(synced: Session) -> None:
    person = set_cells(synced, "Dinesh Tewari", "  " + "ä" * 900 + "\n\t😀  x ", None)
    refresh_working_on(synced)
    synced.commit()
    value = synced.execute(
        text("select text from v_fact_working_on where entity_id = :i"), {"i": person}
    ).scalar_one()
    assert len(value) == 500 and "\n" not in value


# ------------------------------------------------------------------ Fix A: a tick is per cycle


def test_a_tick_hides_only_its_own_cycle() -> None:
    """A Done must not hide a task for ever: the next round (a later due day) shows again."""
    first = TODAY - timedelta(days=9)
    second = TODAY - timedelta(days=1)
    ticked = {"task:reconnect": TODAY - timedelta(days=8)}
    done = frozenset(ticked)

    def shown(due: date) -> list[rules.Task]:
        people = [row(reconnect_due=True, reconnect_due_on=due, done_tasks=done, done_on=ticked)]
        return rules.reconnect_tasks(people)

    assert shown(first) == []  # ticked on day 1 of the same cycle
    assert shown(TODAY - timedelta(days=8)) == []  # the tick day itself closes the cycle
    again = shown(second)  # the sheet moved the anchor: a new cycle
    assert len(again) == 1
    assert again[0].action and again[0].action.body["payload"] == {
        "task": "reconnect",
        "cycle": second.isoformat(),
    }


def test_email_steps_return_in_the_next_cycle() -> None:
    mail = {"connected": True, "has_email": True, "stage": "Connected"}
    anchor = TODAY - timedelta(days=2)
    step = {"task:email1": anchor - timedelta(days=20)}
    again = rules.email_step_tasks(
        [row(last_step_date=anchor, done_tasks=frozenset(step), done_on=step, **mail)], TODAY
    )
    assert kinds(again) == ["email1"]
    assert again[0].action and again[0].action.body["payload"]["cycle"] == TODAY.isoformat()


def test_a_tick_without_a_known_day_still_hides_the_task() -> None:
    """Old rows carry no day: ask nothing twice."""
    people = [
        row(reconnect_due=True, reconnect_due_on=TODAY, done_tasks=frozenset({"task:reconnect"}))
    ]
    assert rules.reconnect_tasks(people) == []


def test_done_in_a_later_cycle_is_a_new_write(client: TestClient, synced: Session) -> None:
    """Regression: the same Done body in the next cycle must not come back as 'already'."""
    head = {"X-Saley-Act": "1"}
    task = next(i for i in mine(client, "sid") if i["kind"] == "reconnect")
    body = task["action"]["body"]
    assert body["payload"]["cycle"] == task["due"]
    first = client.post("/api/act", json=body, headers=head).json()
    assert first["already"] is False
    assert task["key"] not in [i["key"] for i in mine(client, "sid")]
    # The same call again in the same cycle is the first write (D48).
    assert client.post("/api/act", json=body, headers=head).json()["write_id"] == first["write_id"]

    # Days pass. The sheet gets a later Reconnect Date that is due now.
    synced.execute(
        text("update decision set at = at - interval '5 days' where id = :i"),
        {"i": first["write_id"]},
    )
    synced.execute(
        text("update contact set reconnect_date = :d where id = :i"),
        {"d": today_ist() - timedelta(days=1), "i": task["contact_id"]},
    )
    synced.commit()
    again = next(
        i
        for i in mine(client, "sid")
        if i["contact_id"] == task["contact_id"] and i["kind"] == "reconnect"
    )
    assert again["action"]["body"]["payload"]["cycle"] != body["payload"]["cycle"]
    second = client.post("/api/act", json=again["action"]["body"], headers=head).json()
    assert second["already"] is False and second["write_id"] != first["write_id"]
    assert again["key"] not in [i["key"] for i in mine(client, "sid") if i["kind"] == "reconnect"]


def test_ticked_rows_show_only_to_the_person_who_ticked() -> None:
    """T3 review: the Undo of a tick belongs to its signer, not to whoever views the list."""
    until = TODAY  # any value; the rule only copies it
    write = rules.RecentWrite(7, "sid", "Sid", "li_connected_date", 1, "Pat", until)
    inputs = rules.Inputs(contacts=[row(contact_id=1)], recent_writes=[write])

    def ticked(**kw: str) -> list[int | None]:
        return [t.undo_id for t in rules.tasks_for("sid", inputs, TODAY, **kw) if t.ticked]

    assert ticked(viewer="sid") == [7]
    assert ticked(viewer="kushal") == []
    assert ticked() == [7]
