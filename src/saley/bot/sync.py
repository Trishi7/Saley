"""Card sync: LISTEN saley_card and edit the card within 5 s (D259, docs/17 §2 and §6).

Why: when the dashboard acts first (or anyone undoes), the trigger of migration 0027 marks the
card and sends `NOTIFY saley_card, <card_id>`. This loop hears it and edits the Discord message
to its new state, so nobody acts twice. It runs in its own thread with its own psycopg
connection (the bot's `saley` login), because LISTEN needs a connection that stays open.

After a lost connection it reconnects and first edits every card changed in the last 10
minutes: a notify sent while it was away is not lost. An edit is idempotent, so a card edited
twice (by a press and by its notify) is harmless.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import psycopg
from sqlalchemy import text
from sqlalchemy.engine import make_url

from saley.bot import deliver
from saley.core import clock
from saley.core.db import session_scope
from saley.core.logging import get_logger
from saley.core.settings import get_settings

log = get_logger("saley.bot.sync")

CHANNEL = "saley_card"
WAIT_SECONDS = 1.0  # how long one notifies() call waits; also how fast `stop` is seen
RECONNECT_SECONDS = 5.0
CATCH_UP_MINUTES = 10
Edit = Callable[[str], str]


@dataclass
class SyncState:
    """What `saley bot status` and the health card show about the loop."""

    connected: bool = False
    last_event: datetime | None = None
    edits: int = 0
    errors: int = 0
    stop: threading.Event = field(default_factory=threading.Event)


STATE = SyncState()


def dsn() -> str:
    """The bot's own login (DATABASE_URL, the `saley` role) as a plain libpq URL."""
    url = make_url(get_settings().database_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def catch_up(edit: Edit, minutes: int = CATCH_UP_MINUTES) -> int:
    """Edit every sent card that changed in the last `minutes` (a notify may be lost)."""
    with session_scope() as session:
        ids = (
            session.execute(
                text(
                    "select card_id from bot_post where state = 'sent' and card_id is not null "
                    "and edited_at > now() - make_interval(mins => :m) order by edited_at"
                ),
                {"m": minutes},
            )
            .scalars()
            .all()
        )
    for card_id in ids:
        edit(card_id)
    return len(ids)


def _handle(card_id: str, edit: Edit, state: SyncState) -> None:
    try:
        result = edit(card_id)
    except Exception:  # one bad card must not stop the loop
        state.errors += 1
        log.exception("card_sync_edit_failed", card=card_id)
        return
    state.edits += 1
    state.last_event = clock.now_ist()
    log.info("card_synced", card=card_id, result=result)


def listen(
    state: SyncState = STATE,
    edit: Edit = deliver.edit_card,
    connect: Callable[..., psycopg.Connection] = psycopg.connect,
) -> None:
    """The loop. Returns only when `state.stop` is set. Never raises."""
    while not state.stop.is_set():
        try:
            with connect(dsn(), autocommit=True) as conn:
                conn.execute(f"listen {CHANNEL}")
                state.connected = True
                log.info("card_sync_listening", caught_up=catch_up(edit))
                while not state.stop.is_set():
                    for note in conn.notifies(timeout=WAIT_SECONDS):
                        _handle(note.payload, edit, state)
        except Exception:
            state.errors += 1
            log.warning("card_sync_reconnect", exc_info=True)
        finally:
            state.connected = False
        state.stop.wait(RECONNECT_SECONDS)


def start(state: SyncState = STATE, edit: Edit = deliver.edit_card) -> threading.Thread:
    thread = threading.Thread(target=listen, args=(state, edit), name="card-sync", daemon=True)
    thread.start()
    return thread
