"""R7-I05: the press path on a fake Discord (docs/09 §6, docs/17 §5, D257).

A writer's press calls the door once and the card footer says "Done by Sid in Discord"; a
press on a card the dashboard already did answers "Already done by ..."; a non-writer gets A2
with Ask buttons and nothing is written; a deleted message makes the row `gone`.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import cards, components, deliver, press, queue
from saley.bot import words as bw
from saley.core import clock
from saley.core.settings import get_settings
from tests.conftest import TEAM_IDS, make_card, sql_call
from tests.test_bot_deliver_r7 import FakeDiscord

SID, KUSHAL, VAISHNAVI = (int(TEAM_IDS[s]) for s in ("sid", "kushal", "vaishnavi"))
RESOLVE = "select saley_proposal_resolve(:u, :id, 'do', null, null, 'dashboard', 0, 5, false)"


def _sent_card(db: Session, fake: FakeDiscord, card_id: str = "m10-press") -> int:
    pid = make_card(db)
    data = {
        "who": "Sid",
        "proposal_id": pid,
        "type": "meeting_status",
        "name": "Alex Search",
        "company": "Google",
        "contact_id": None,
        "writes": "Meeting Status = completed",
    }
    payload = cards.render("M10", {**data, "contact_id": 1}, card_id)
    post = queue.insert_card(db, payload, due_at=clock.now_ist())
    db.execute(text("update bot_post set state = 'ready' where id = :i"), {"i": post})
    db.commit()
    deliver.deliver_ready(client=fake.client())
    return pid


def _press(db: Session, who: int, card_id: str, action: str, **values: str) -> press.Outcome:
    return press.press(db, get_settings(), who, card_id, action, values)


def test_a_writer_press_writes_once_and_the_footer_names_discord(crew: Session) -> None:
    fake = FakeDiscord()
    pid = _sent_card(crew, fake)
    outcome = _press(crew, SID, "m10-press", "do")
    assert outcome == press.Outcome("ok", None, None, edit=True)
    assert deliver.edit_card("m10-press", client=fake.client()) == "edited"
    footer = fake.calls[-1][2]["embeds"][-1]["footer"]["text"]
    assert footer.startswith("Done by Sid in Discord at ") and "Undo 5 min left" in footer
    writes = crew.execute(
        text("select count(*) from decision where proposal_id = :p and surface = 'discord'"),
        {"p": pid},
    ).scalar()
    assert writes == 1


def test_a_press_on_a_card_done_on_the_dashboard_answers_already(crew: Session) -> None:
    fake = FakeDiscord()
    pid = _sent_card(crew, fake)
    assert sql_call(crew, RESOLVE, u="vaishnavi", id=pid)["ok"] is True
    outcome = _press(crew, SID, "m10-press", "do")
    assert outcome.result == "already" and outcome.edit
    assert outcome.reply is not None
    assert outcome.reply["content"].startswith("Already done by Vaishnavi at ")


def test_a_non_writer_gets_a2_and_nothing_is_written(crew: Session) -> None:
    fake = FakeDiscord()
    pid = _sent_card(crew, fake)
    outcome = _press(crew, KUSHAL, "m10-press", "do")
    assert outcome.result == "refused" and outcome.code == "not_writer" and not outcome.edit
    reply: dict[str, Any] = outcome.reply or {}
    assert (
        reply["content"]
        == f"{bw.REFUSE_WRITE.format(names='Sid and Vaishnavi')}\n{bw.REFUSE_OFFER}"
    )
    asks = [b["custom_id"] for b in components.buttons_of(reply)]
    assert asks == ["c7:m10-press:ask_sid", "c7:m10-press:ask_vaishnavi"]
    assert (
        crew.execute(
            text("select count(*) from decision where proposal_id = :p"), {"p": pid}
        ).scalar()
        == 0
    )
    logged = crew.execute(text("select result, code from bot_interaction")).one()
    assert tuple(logged) == ("refused", "not_writer")


def test_ask_posts_the_card_into_a_thread_for_the_writer(crew: Session) -> None:
    fake = FakeDiscord()
    _sent_card(crew, fake)
    outcome = press.ask_writer(
        crew, get_settings(), KUSHAL, "m10-press", "sid", client=fake.client()
    )
    assert outcome.reply == {"content": bw.ASKED_IN_THREAD.format(name="Sid")}
    posted = fake.posts()[-1]
    assert posted["content"] == "[TEST] Kushal asks you to look at this card, Sid."
    assert posted["allowed_mentions"] == {"parse": []}  # test mode pings nobody
    assert crew.execute(text("select thread_id from bot_post")).scalar()


def test_an_unknown_author_gets_one_line(crew: Session) -> None:
    outcome = _press(crew, 777, "m10-press", "do")
    assert outcome.reply == {"content": bw.UNKNOWN_AUTHOR} and outcome.result == "refused"


def test_a_database_fault_is_a_kind_line(crew: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _sent_card(crew, FakeDiscord())
    monkeypatch.setattr(press, "press_sql", "select saley_card_press_missing(:c, :u, :a, :p)")
    outcome = _press(crew, SID, "m10-press", "do")
    assert outcome.result == "error" and outcome.reply == {"content": bw.PRESS_FAILED}


def test_the_writer_rule_is_the_sql_rule() -> None:
    assert not press.needs_writer({"door": "mark_seen"})
    assert not press.needs_writer({"door": "close"})
    assert not press.needs_writer({"door": "fact", "args": {"kind": "answer"}})
    assert press.needs_writer({"door": "fact", "args": {"kind": "note"}})
    assert press.needs_writer({"door": "act"}) and press.needs_writer(None)


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("28 Oct", "2026-10-28"),
        ("28 october 2027", "2027-10-28"),
        ("2026-11-02", "2026-11-02"),
        ("2 Jan", "2027-01-02"),
        ("30 Feb", None),
        ("next week", None),
        ("2026-02-30", None),
    ],
)
def test_parse_day(raw: str, want: str | None) -> None:
    assert press.parse_day(raw, date(2026, 10, 23)) == want
