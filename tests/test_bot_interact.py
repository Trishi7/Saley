"""bot/interact.py: one `c7:` interaction is routed to the press, a modal, A2, Ask or A1.

Fake discord.py interactions (AsyncMock); the database and the press door are real.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import cards, deliver, interact, press, queue
from saley.bot import words as bw
from saley.bot.cards.samples import SAMPLES
from saley.core import clock
from tests.conftest import TEAM_IDS, make_card

SID, KUSHAL = int(TEAM_IDS["sid"]), int(TEAM_IDS["kushal"])


def _interaction(custom_id: str, user: int, values: list[str] | None = None) -> Any:
    data: dict[str, Any] = {"custom_id": custom_id}
    if values is not None:
        data["values"] = values
    return SimpleNamespace(
        data=data,
        user=SimpleNamespace(id=user),
        response=SimpleNamespace(
            defer=AsyncMock(), send_modal=AsyncMock(), send_message=AsyncMock()
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def _card(db: Session, card_type: str, data: dict[str, Any], card_id: str) -> None:
    queue.insert_card(db, cards.render(card_type, data, card_id), due_at=clock.now_ist())
    db.execute(text("update bot_post set state = 'sent' where card_id = :c"), {"c": card_id})
    db.commit()


@pytest.fixture
def edits(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(deliver, "edit_card", lambda card_id: seen.append(card_id) or "edited")
    return seen


def _route(interaction: Any) -> AsyncMock:
    more = AsyncMock()
    asyncio.run(interact.on_card(interaction, more))
    return more


def test_a_button_press_defers_writes_and_edits(crew: Session, edits: list[str]) -> None:
    pid = make_card(crew)
    data = {**SAMPLES["M10"], "proposal_id": pid, "contact_id": None, "account_id": 1}
    _card(crew, "M10", data, "m10-i1")
    hit = _interaction("c7:m10-i1:do", SID)
    _route(hit)
    hit.response.defer.assert_awaited_once()
    hit.followup.send.assert_not_awaited()  # ok: the card itself changes
    assert edits == ["m10-i1"]
    assert (
        crew.execute(text("select state from proposal where id = :p"), {"p": pid}).scalar()
        == "acted"
    )


def test_a_non_writer_gets_a2_in_private(crew: Session, edits: list[str]) -> None:
    pid = make_card(crew)
    _card(
        crew,
        "M10",
        {**SAMPLES["M10"], "proposal_id": pid, "contact_id": None, "account_id": 1},
        "m10-i2",
    )
    hit = _interaction("c7:m10-i2:do", KUSHAL)
    _route(hit)
    content = hit.followup.send.await_args.args[0]
    kwargs = hit.followup.send.await_args.kwargs
    assert content.startswith(bw.REFUSE_WRITE.format(names="Sid and Vaishnavi"))
    assert kwargs["ephemeral"] is True
    assert [c.label for c in kwargs["view"].children] == ["Ask Sid", "Ask Vaishnavi"]
    assert edits == []


def test_a_select_carries_its_value(
    crew: Session, edits: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple] = []

    def fake(
        session: Session, settings: Any, user: int, card_id: str, action: str, values: dict
    ) -> press.Outcome:
        calls.append((card_id, action, values))
        return press.Outcome("ok", edit=True)

    monkeypatch.setattr(press, "press", fake)
    _route(_interaction("c7:m3-i3:do", SID, ["201"]))
    assert calls == [("m3-i3", "do", {"value": "201"})]


def test_a_modal_opens_for_a_writer_and_a2_for_anyone_else(crew: Session, edits: list[str]) -> None:
    _card(crew, "M8", {**SAMPLES["M8"], "commitment_id": 1}, "m8-i4")
    writer = _interaction("c7:m8-i4:move", SID)
    _route(writer)
    modal = writer.response.send_modal.await_args.args[0]
    assert isinstance(modal, interact.CardModal) and modal.action == "move"
    other = _interaction("c7:m8-i4:move", KUSHAL)
    _route(other)
    other.response.send_modal.assert_not_awaited()
    assert other.response.send_message.await_args.kwargs["ephemeral"] is True


def test_the_modal_submit_runs_the_press(
    crew: Session, edits: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        press,
        "press",
        lambda s, st, u, c, a, v: (
            calls.append(v) or press.Outcome("error", "bad_date", {"content": bw.BAD_DATE})
        ),
    )

    async def submit() -> Any:
        modal = interact.CardModal(
            "m8-i5", "move", cards.render("M8", SAMPLES["M8"], "m8-i5")["acts"]["move"]["modal"]
        )
        modal.fields["date"]._value = "next week"
        hit = _interaction("", SID)
        await modal.on_submit(hit)
        return hit

    hit = asyncio.run(submit())
    assert calls == [{"date": "next week"}]
    assert hit.followup.send.await_args.args[0] == bw.BAD_DATE
    assert edits == []  # an error edits nothing


def test_ask_and_a1_and_foreign_ids(
    crew: Session, edits: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[tuple] = []
    monkeypatch.setattr(
        press,
        "ask_writer",
        lambda s, st, u, c, w: (
            asked.append((u, c, w)) or press.Outcome("ok", None, {"content": "asked"})
        ),
    )
    hit = _interaction("c7:m10-x:ask_sid", KUSHAL)
    _route(hit)
    assert asked == [(KUSHAL, "m10-x", "sid")]
    assert hit.followup.send.await_args.args[0] == "asked"
    more = _route(_interaction("c7:a1:more", SID))
    more.assert_awaited_once()
    nothing = _interaction("card:12:do", SID)  # an R4 button: its own DynamicItem answers it
    assert not _route(nothing).await_count
    nothing.response.defer.assert_not_awaited()


def test_a_reply_that_fails_is_logged_not_raised() -> None:
    hit = _interaction("c7:m1-x:x", SID)
    hit.followup.send.side_effect = discord.HTTPException(
        SimpleNamespace(status=500, reason="x"), "down"
    )
    asyncio.run(interact._reply(hit, {"content": "x"}))  # no exception
