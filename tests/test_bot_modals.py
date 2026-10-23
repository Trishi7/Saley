"""R7-I03: modals (docs/09 §5 M5, M6, M8, M14; docs/17 §6 "Modal field invalid").

M6: a bad date is refused in private and nothing is written; a good date writes the next step
(a commitment) and the meeting status (the sheet, through saley_act) with surface=discord.
"""

from __future__ import annotations

import asyncio

import discord
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import cards, interact, press, queue
from saley.bot import words as bw
from saley.bot.cards.samples import SAMPLES
from saley.core import clock
from saley.core.settings import get_settings
from tests.conftest import TEAM_IDS, contact_id

VAISHNAVI, KUSHAL = int(TEAM_IDS["vaishnavi"]), int(TEAM_IDS["kushal"])


def _m6(db: Session) -> tuple[str, int]:
    person = contact_id(db, "Alex Search")
    row_key = db.execute(
        text("select sheet_row_key from contact where id = :c"), {"c": person}
    ).scalar()
    data = {
        "who": "Vaishnavi",
        "name": "Alex Search",
        "company": "Google",
        "contact_id": person,
        "row_key": row_key,
        "statuses": ["completed", "no show"],
        "no_show": "no show",
    }
    payload = cards.render("M6", data, "m6-modal")
    queue.insert_card(db, payload, due_at=clock.now_ist())
    db.execute(text("update bot_post set state = 'sent'"))
    db.commit()
    return "m6-modal", person


def _press(db: Session, who: int, **values: str) -> press.Outcome:
    return press.press(db, get_settings(), who, "m6-modal", "next", values)


def test_a_bad_date_is_refused_in_private_and_nothing_is_written(crew: Session) -> None:
    _m6(crew)
    outcome = _press(crew, VAISHNAVI, text="Send pricing", date="next week", status="completed")
    assert outcome.result == "error" and outcome.reply == {"content": bw.BAD_DATE}
    for table in ("commitment", "decision", "bot_interaction"):
        assert crew.execute(text(f"select count(*) from {table}")).scalar() == 0


def test_a_good_date_writes_the_step_and_the_status_signed_discord(crew: Session) -> None:
    card_id, _person = _m6(crew)
    outcome = _press(crew, VAISHNAVI, text="Send pricing", date="28 Oct", status="completed")
    assert outcome.result == "ok"
    step = crew.execute(
        text(
            "select c.text, c.due, c.source_type, u.short from commitment c "
            "join users u on u.id = c.owner"
        )
    ).one()
    assert step[0] == "Send pricing" and step[1].month == 10 and step[1].day == 28
    assert (step[2], step[3]) == ("discord", "vaishnavi")
    write = crew.execute(
        text("select role, after, surface from decision where kind = 'column_write'")
    ).one()
    assert tuple(write) == ("meeting_status", "completed", "discord")
    done = crew.execute(
        text("select done_by, done_surface from bot_post where card_id = :c"), {"c": card_id}
    ).one()
    assert tuple(done) == ("Vaishnavi", "discord")


def test_a_status_not_on_the_card_is_refused(crew: Session) -> None:
    _m6(crew)
    outcome = _press(crew, VAISHNAVI, text="x", date="28 Oct", status="won the lottery")
    assert outcome.result == "error" and outcome.reply == {"content": bw.BAD_PICK}
    assert crew.execute(text("select count(*) from commitment")).scalar() == 0


def test_the_modal_is_built_from_the_card_spec() -> None:
    spec = cards.render("M6", SAMPLES["M6"], "m6-x")["acts"]["next"]["modal"]

    async def build() -> interact.CardModal:
        return interact.CardModal("m6-x", "next", spec)

    modal = asyncio.run(build())
    assert modal.title == "Next step · Test Person One"
    kinds = [type(i).__name__ for i in modal.children]
    assert kinds == ["TextInput", "TextInput", "Label"]
    status = modal.fields["status"]
    assert isinstance(status, discord.ui.Select)
    assert [o.value for o in status.options] == ["completed", "no show", "rescheduled"]
    modal.fields["text"]._value = "Send pricing"  # what Discord fills in on submit
    modal.fields["date"]._value = "28 Oct"
    assert modal.values() == {"text": "Send pricing", "date": "28 Oct", "status": ""}


def test_a_non_writer_sees_a2_before_any_modal(crew: Session) -> None:
    _m6(crew)
    act, can_write = interact._act_and_writer("m6-modal", "next", KUSHAL)
    assert act is not None and press.needs_writer(act) and not can_write
