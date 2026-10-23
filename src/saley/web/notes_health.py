"""Sources and health: the Drive meeting-notes line (D81).

Why: the sync saves its last result in one row (`v_notes_source`). This module turns that row
and the setting into one plain line: not configured, waiting, ok (files and last change), folder
not shared (with the folder id), Drive API off, or last sync failed. A 403 is a line, never a
crash. The web role reads the view only.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, text

from saley.core.clock import hhmm
from saley.core.logging import scrub_text
from saley.core.settings import get_settings
from saley.web import words as w

REASON_SHOWN = 100


def notes_state(conn: Connection) -> dict[str, Any]:
    folder = get_settings().meeting_notes_drive_folder_id.strip()
    if not folder:
        return {"state": "not_configured", "message": w.NOTES_NOT_CONFIGURED, "last_ok": None}
    row = (
        conn.execute(
            text(
                "select folder_id, state, files, last_change, last_ok_at, detail "
                "from v_notes_source where key = 'drive'"
            )
        )
        .mappings()
        .first()
    )
    if row is None or row["folder_id"] != folder:
        return {"state": "waiting", "message": w.NOTES_WAITING, "last_ok": None}
    state = row["state"]
    if state == "ok":
        n, change = row["files"], row["last_change"]
        message = (
            w.fill(w.NOTES_FILES, n=n, t=hhmm(change))
            if change
            else w.fill(w.NOTES_FILES_NO_CHANGE, n=n)
        )
    elif state == "partial":
        # The folder listing was cut (B10): files are shown, but some may be missing. Say why.
        why = scrub_text(row["detail"] or "")[:REASON_SHOWN]
        message = w.fill(w.NOTES_PARTIAL, why=why) if why else w.NOTES_PARTIAL_NO_WHY
    elif state == "folder_not_shared":
        account = (row["detail"] or "").strip()
        message = (
            w.fill(w.NOTES_NOT_SHARED, sa=scrub_text(account), id=folder)
            if "@" in account
            else w.fill(w.NOTES_NOT_SHARED_NO_SA, id=folder)
        )
    elif state == "api_disabled":
        message = w.NOTES_API_OFF
    else:
        message = w.NOTES_FAILING
    return {"state": state, "message": message, "last_ok": row["last_ok_at"]}


# ------------------------------------------------------------------ the Discord block (R7)

DISCORD_SQL = (
    "select sent_today, held, failed_today, presses_ok, presses_already, presses_refused, "
    "press_p50_ms, last_sent, last_sync, chat_lines, last_summary from v_bot_health"
)


def discord_numbers(conn: Connection) -> dict[str, Any]:
    """The row of v_bot_health (docs/17 §10): cards, presses, card sync, chat memory."""
    return dict(conn.execute(text(DISCORD_SQL)).mappings().one())


def _ms(value: float | None) -> str:
    return w.fill(w.DISCORD_MS, n=round(value)) if value is not None else "—"


def discord_state(conn: Connection) -> dict[str, Any]:
    """Sources & health · Discord, and `saley bot status`: the same lines from one view."""
    n = discord_numbers(conn)
    rows = [
        (w.DISCORD_SENT, str(n["sent_today"])),
        (w.DISCORD_HELD, str(n["held"])),
        (w.DISCORD_FAILED, str(n["failed_today"])),
        (
            w.DISCORD_PRESSES,
            w.fill(
                w.DISCORD_PRESSES_VALUE,
                ok=n["presses_ok"],
                already=n["presses_already"],
                refused=n["presses_refused"],
            ),
        ),
        (w.DISCORD_P50, _ms(n["press_p50_ms"])),
        (w.DISCORD_LAST_SENT, hhmm(n["last_sent"])),
        (w.DISCORD_LAST_SYNC, hhmm(n["last_sync"])),
        (w.DISCORD_CHAT, str(n["chat_lines"])),
        (w.DISCORD_SUMMARY, hhmm(n["last_summary"])),
        (w.DISCORD_DMS, w.DISCORD_ON if get_settings().dm_enabled else w.DISCORD_OFF),
    ]
    return {"rows": [{"label": label, "value": value} for label, value in rows]}
