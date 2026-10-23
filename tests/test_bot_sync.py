"""R7-I01: a dashboard act edits the Discord card within 5 s; undo brings the buttons back (D259).

The listener is the real one (LISTEN saley_card on the test database); Discord is a fake.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import components, deliver, sync
from saley.bot import words as bw
from tests.conftest import sql_call
from tests.test_bot_deliver_r7 import FakeDiscord
from tests.test_bot_press import RESOLVE, _sent_card

LIMIT_SECONDS = 5.0


@pytest.fixture
def listening(crew: Session) -> Iterator[tuple[Session, FakeDiscord, sync.SyncState]]:
    fake = FakeDiscord()
    state = sync.SyncState()

    def edit(card_id: str) -> str:
        return deliver.edit_card(card_id, client=fake.client())

    thread = sync.start(state, edit)
    _wait(lambda: state.connected)
    yield crew, fake, state
    state.stop.set()
    thread.join(timeout=10)


def _wait(done: Callable[[], bool], limit: float = LIMIT_SECONDS) -> float:
    start = time.monotonic()
    while not done():
        assert time.monotonic() - start < limit, "not within 5 s"
        time.sleep(0.05)
    return time.monotonic() - start


def _patches(fake: FakeDiscord) -> list[dict]:
    return [b for m, _, b in fake.calls if m == "PATCH"]


def test_a_dashboard_act_edits_the_card_and_undo_brings_the_buttons_back(listening) -> None:
    db, fake, state = listening
    pid = _sent_card(db, fake)
    before = components.buttons_of(fake.posts()[0])
    result = sql_call(db, RESOLVE, u="vaishnavi", id=pid)
    took = _wait(lambda: len(_patches(fake)) >= 1)
    assert took < LIMIT_SECONDS
    done = _patches(fake)[-1]
    assert done["embeds"][-1]["footer"]["text"].startswith("Done by Vaishnavi on the dashboard at ")
    assert components.buttons_of(done)[0]["label"] == bw.BTN_UNDO
    write_id = result["items"][0]["write_id"]
    assert sql_call(db, "select saley_undo_group('vaishnavi', :w, 'dashboard')", w=write_id)["ok"]
    _wait(lambda: len(_patches(fake)) >= 2)
    back = _patches(fake)[-1]
    assert "footer" not in back["embeds"][-1]
    assert components.buttons_of(back) == before
    assert state.edits >= 2 and state.last_event is not None


def test_a_reconnect_catches_up_on_recent_changes(crew: Session) -> None:
    fake = FakeDiscord()
    _sent_card(crew, fake)
    crew.execute(text("update bot_post set done_by = 'Sid', done_at = now(), edited_at = now()"))
    crew.commit()
    edited: list[str] = []
    assert sync.catch_up(edited.append) == 1 and edited == ["m10-press"]


def test_one_bad_edit_does_not_stop_the_loop() -> None:
    state = sync.SyncState()

    def boom(_: str) -> str:
        raise RuntimeError("discord down")

    sync._handle("m1-x", boom, state)
    assert state.errors == 1 and state.edits == 0
