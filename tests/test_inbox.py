"""Inbox cards, proposals and the decisions import (R2 phase 5: R2-I01).

Why: Saley's own ideas show ONCE as a one-click card (D24). `card_key` is UNIQUE, so a card
that was acted on, dismissed or expired never comes back, even when the sheet looks the same.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.cli import app as cli_app
from saley.core.clock import today_ist
from saley.intel.decisions import import_decisions, parse_table
from saley.intel.proposals import refresh_proposals
from saley.web.app import create_app
from tests.conftest import SignedClient, contact_id, sql_act

H = {"X-Saley-Act": "1"}


@pytest.fixture
def client(synced: Session) -> Iterator[TestClient]:
    with SignedClient(create_app()) as c:
        yield c


def book_past_meeting(db: Session, name: str = "David Gamez", days_ago: int = 3) -> int:
    db.execute(
        text("update contact set meeting_date = :d, meeting_status = 'Booked' where name = :n"),
        {"d": today_ist() - timedelta(days=days_ago), "n": name},
    )
    db.commit()
    return contact_id(db, name)


def cards(client: TestClient) -> list[dict]:
    return client.get("/api/inbox?type=card").json()["items"]


def proposal_states(db: Session) -> list[tuple[str, str]]:
    db.rollback()
    return [tuple(r) for r in db.execute(text("select type, state from proposal order by id"))]


# ------------------------------------------------------------------ R2-I01


def test_card_once_and_dismissed_never_returns(client: TestClient, synced: Session) -> None:
    """R2-I01: an Inbox card shows once; a dismissed card never returns."""
    david = book_past_meeting(synced)
    first = refresh_proposals(synced)
    again = refresh_proposals(synced)
    synced.commit()
    assert (first.added, again.added) == (1, 0)  # the same idea is not inserted twice

    shown = cards(client)
    assert len(shown) == 1
    card = shown[0]
    assert card["title"] == "Mark meeting as completed? David Gamez"
    assert card["button"] == "Mark completed"
    assert card["why"].endswith("Status is Booked")
    assert card["evidence"]["row"].startswith("Outreach PoCs:")
    assert card["action"]["endpoint"] == "/api/act"
    assert card["action"]["body"]["target_ids"] == [david]
    assert card["action"]["body"]["payload"]["cells"] == [
        {"role": "meeting_status", "value": "completed"}
    ]
    assert card["action"]["body"]["payload"]["proposal_id"] == card["id"]

    dismissed = client.post(card["dismiss"], headers=H)
    assert dismissed.json()["already"] is False
    assert cards(client) == []

    # The sheet still shows a booked meeting in the past. The card must not come back.
    refresh_proposals(synced)
    synced.commit()
    assert cards(client) == []
    assert proposal_states(synced) == [("meeting_status", "dismissed")]


def test_the_card_button_is_one_write_and_the_card_is_acted(
    client: TestClient, synced: Session
) -> None:
    book_past_meeting(synced)
    refresh_proposals(synced)
    synced.commit()
    card = cards(client)[0]
    done = client.post(card["action"]["endpoint"], json=card["action"]["body"], headers=H)
    assert done.status_code == 200
    assert cards(client) == []
    refresh_proposals(synced)
    synced.commit()
    assert proposal_states(synced) == [("meeting_status", "acted")]  # never a second card
    assert synced.execute(text("select count(*) from sync_outbox")).scalar_one() == 1


def test_no_card_for_a_write_the_human_already_asked_for(synced: Session) -> None:
    david = book_past_meeting(synced)
    sql_act(synced, "sid", [{"role": "meeting_status", "value": "completed"}], [david])
    assert refresh_proposals(synced).added == 0  # a waiting outbox row hides the idea
    synced.execute(text("update sync_outbox set state = 'done'"))
    synced.commit()
    assert refresh_proposals(synced).added == 0  # a live decision hides it while the sync lags
    synced.execute(text("update decision set undone_at = now()"))
    synced.commit()
    assert refresh_proposals(synced).added == 1  # an undone write is no answer


def test_a_card_expires_when_the_idea_no_longer_holds(client: TestClient, synced: Session) -> None:
    book_past_meeting(synced)
    refresh_proposals(synced)
    synced.commit()
    synced.execute(text("update contact set meeting_status = 'completed'"))
    synced.commit()
    stats = refresh_proposals(synced)
    synced.commit()
    assert stats.expired == 1 and cards(client) == []
    assert proposal_states(synced) == [("meeting_status", "expired")]


def test_other_meeting_states_make_no_card(synced: Session) -> None:
    today = today_ist()
    for status, offset in (("completed", -3), ("booked", 0), ("booked", 5), ("no-show", -3)):
        synced.execute(
            text("update contact set meeting_date = :d, meeting_status = :s where name = 'Ro One'"),
            {"d": today + timedelta(days=offset), "s": status},
        )
        synced.commit()
        assert refresh_proposals(synced).added == 0, (status, offset)


def test_li_connected_card_needs_a_date_and_an_open_request(
    client: TestClient, synced: Session
) -> None:
    alex = contact_id(synced, "Alex Search")
    assert refresh_proposals(synced).added == 0  # Alex has no connected date
    synced.execute(
        text("update contact set li_connected_date = :d where id = :i"),
        {"d": today_ist() - timedelta(days=1), "i": alex},
    )
    synced.commit()
    assert refresh_proposals(synced).added == 1
    synced.commit()
    card = cards(client)[0]
    assert card["title"] == "Mark LinkedIn as connected? Alex Search"
    assert card["action"]["body"]["payload"]["cells"] == [
        {"role": "sid_li_added", "value": "Connected"}
    ]
    # Dinesh and Sshubam are Connected already: no card for them.
    assert len(cards(client)) == 1


def test_two_meeting_dates_are_two_ideas(synced: Session) -> None:
    book_past_meeting(synced, days_ago=3)
    refresh_proposals(synced)
    book_past_meeting(synced, days_ago=2)  # the meeting moved: a new date is a new idea
    refresh_proposals(synced)
    synced.commit()
    assert proposal_states(synced) == [("meeting_status", "expired"), ("meeting_status", "open")]


# ------------------------------------------------------------------ the rest of the Inbox


def test_inbox_lists_asks_reminders_and_decisions_to_confirm(
    client: TestClient, synced: Session
) -> None:
    today = today_ist()
    synced.execute(
        text(
            "insert into ask(to_user, question) "
            "select id, 'Who owns Acme?' from users where short = 'sid'"
        )
    )
    synced.execute(
        text(
            "insert into decision(kind, choice, reason, state) "
            "values ('strategy_decision', 'D77', 'Use the plan.', 'proposed')"
        )
    )
    synced.execute(
        text(
            "insert into commitment(id, owner, text, due) select 1, id, 'I will send it.', :d "
            "from users where short = 'sid'"
        ),
        {"d": today},
    )
    synced.execute(
        text(
            "insert into reminder(for_user, text, fire_at, commitment_id, kind) "
            "select id, 'Due today: I will send it.', now(), 1, 'due_day' from users "
            "where short = 'sid'"
        )
    )
    synced.commit()
    body = client.get("/api/inbox").json()
    by_kind = {i["kind"]: i for i in body["items"]}
    assert set(by_kind) == {"ask", "confirm", "reminder"}
    assert by_kind["ask"]["title"] == "Who owns Acme?" and by_kind["ask"]["for_user"] == "sid"
    assert by_kind["confirm"]["title"] == "Confirm decision D77"
    assert by_kind["confirm"]["why"] == "Use the plan."
    assert by_kind["confirm"]["action"]["endpoint"].endswith("/confirm")
    assert by_kind["reminder"]["title"] == "Due today: I will send it."
    assert by_kind["reminder"]["action"] is None
    assert body["total"] == 3 and client.get("/api/inbox?type=ask").json()["total"] == 1

    # A reminder leaves the Inbox when its commitment is done or the due day has passed.
    synced.execute(text("update commitment set state = 'done'"))
    synced.commit()
    assert client.get("/api/inbox?type=reminder").json()["items"] == []
    synced.execute(
        text("update commitment set state = 'open', due = :d"), {"d": today - timedelta(days=1)}
    )
    synced.commit()
    assert client.get("/api/inbox?type=reminder").json()["items"] == []


def test_empty_inbox_says_so_and_pages(client: TestClient) -> None:
    body = client.get("/api/inbox?limit=1&offset=5").json()
    assert body["items"] == [] and body["empty"] == "No items in the Inbox."


def test_confirm_removes_the_decision_from_the_inbox(client: TestClient, synced: Session) -> None:
    synced.execute(
        text(
            "insert into decision(kind, choice, state) "
            "values ('strategy_decision', 'D1', 'proposed')"
        )
    )
    synced.commit()
    item = client.get("/api/inbox?type=confirm").json()["items"][0]
    assert client.post(item["action"]["endpoint"], headers=H).json()["already"] is False
    assert client.get("/api/inbox?type=confirm").json()["items"] == []


# ------------------------------------------------------------------ decisions import

TABLE = """# Decisions

| Id | Date | By | Decision | Affects docs | Docs updated |
| --- | --- | --- | --- | --- | --- |
| D1–D16 | 2026-10-18 | Trishi | See Part I | 02 | yes |
| D24 | 2026-10-18 | Trishi (confirmed in chat) | One click, no second yes. | 08 | yes |
| D25 | 2026-10-18 | proposed → superseded by D31 | Old owner rule. | 08 | awaiting |
| D45 | 2026-10-19 | proposed (Claude Code, R2 plan) | Web commits at once. | 01 | no |
| D50 | 2026-10-19 | human (R1 ticket) | A human row. | 08 | yes |
| not a row | x | y | z |
"""


def test_parse_table_maps_states_and_skips_superseded() -> None:
    docs, skipped = parse_table(TABLE)
    assert [(d.ident, d.state) for d in docs] == [
        ("D1–D16", "active"),
        ("D24", "active"),
        ("D45", "proposed"),
        ("D50", "active"),
    ]
    assert skipped == 1 and docs[1].day and docs[1].day.isoformat() == "2026-10-18"


def test_import_is_idempotent_and_keeps_a_dashboard_confirm(
    synced: Session, tmp_path: Path
) -> None:
    path = tmp_path / "DECISIONS.md"
    path.write_text(TABLE, encoding="utf-8")
    first = import_decisions(synced, path)
    second = import_decisions(synced, path)
    synced.commit()
    assert (first.added, first.skipped, second.added, second.unchanged) == (4, 1, 0, 4)
    rows = {
        r.choice: r
        for r in synced.execute(
            text(
                "select d.choice, d.state, d.reason, u.short from decision d "
                "left join users u on u.id = d.user_id"
            )
        )
    }
    assert rows["D24"].state == "active" and rows["D24"].short == "trishi"
    assert rows["D45"].state == "proposed" and rows["D45"].short is None
    assert rows["D50"].short is None

    # A confirm on the dashboard stays when the docs still say "proposed".
    synced.execute(text("update decision set state = 'active' where choice = 'D45'"))
    synced.commit()
    import_decisions(synced, path)
    assert (
        synced.execute(text("select state from decision where choice = 'D45'")).scalar_one()
        == "active"
    )

    # The human confirms in the docs: a proposed row becomes active; a changed text is updated.
    synced.execute(text("update decision set state = 'proposed' where choice = 'D45'"))
    synced.commit()
    path.write_text(
        TABLE.replace("proposed (Claude Code, R2 plan)", "Trishi").replace(
            "at once.", "at once, yes."
        ),
        encoding="utf-8",
    )
    third = import_decisions(synced, path)
    synced.commit()
    assert third.updated == 1
    row = synced.execute(text("select state, reason from decision where choice = 'D45'")).one()
    assert tuple(row) == ("active", "Web commits at once, yes.")
    assert synced.execute(text("select count(*) from decision")).scalar_one() == 4


def test_import_with_no_file_does_nothing(synced: Session, tmp_path: Path) -> None:
    assert import_decisions(synced, tmp_path / "missing.md").added == 0


def test_the_real_decisions_file_imports(synced: Session) -> None:
    stats = import_decisions(synced)
    synced.commit()
    assert stats.added > 20
    proposed = synced.execute(
        text("select count(*) from decision where state = 'proposed'")
    ).scalar_one()
    assert proposed >= 10
    # Superseded D25 is not a Confirm button.
    assert (
        synced.execute(text("select count(*) from decision where choice = 'D25'")).scalar_one() == 0
    )


def test_cli_decisions_import(synced: Session, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    path = tmp_path / "DECISIONS.md"
    path.write_text(TABLE, encoding="utf-8")
    result = CliRunner().invoke(cli_app, ["decisions", "import", "--file", str(path)])
    assert result.exit_code == 0, result.output
    assert "4 added" in result.output
    again = CliRunner().invoke(cli_app, ["decisions", "import", "--file", str(path)])
    assert "0 added" in again.output
    missing = CliRunner().invoke(
        cli_app, ["decisions", "import", "--file", str(tmp_path / "no.md")]
    )
    assert missing.exit_code == 1
