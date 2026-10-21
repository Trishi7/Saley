"""Sync the Drive notes folder into the database (D81). Plain code, no LLM.

Why: a note file must be read once per change, not once per poll. `notes_file` holds the file
id and the modifiedTime Saley parsed. A new id is `new`. A known id with another modifiedTime is
`changed`. A file that left the listing (deleted, in the bin, or moved out) is `gone`: its
commitments stay, and the Commitments view says "source removed". An unreadable file is a
data-quality line and is not tried again until its modifiedTime changes.

Safety: all work for one sync runs inside a savepoint. If Drive fails half way, the file rows
and the commitments roll back together, so no file is marked parsed without its promises.
A listing cut by a cap or an error never marks files as gone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.core.clock import now_ist
from saley.core.logging import get_logger
from saley.intel import drive, notes
from saley.intel.commitments import record_unreadable, scan_notes
from saley.intel.notes import Note

log = get_logger("saley.intel.notes_sync")

STATE_KEY = "drive"
STATE_PROBLEM_NOTES = {
    "folder_not_shared": "folder not shared",
    "api_disabled": "Drive API is off",
    "error": "last sync failed",
}
PARTIAL_NOTE = "listing cut (partial)"


@dataclass
class DriveStats:
    listed: int = 0
    new: int = 0
    changed: int = 0
    gone: int = 0
    parsed: int = 0
    skipped: int = 0
    unreadable: int = 0
    commitments: int = 0
    unknown_owner: int = 0  # a team-looking word that does not resolve (ambiguous key), or mail
    outside_facts: int = 0  # D92: an outsider said they will do it: kept as a fact on them
    outside_no_contact: int = 0  # the outsider matches no contact, or several: nothing stored
    state: str = "ok"  # ok | partial | folder_not_shared | api_disabled | error
    enable_url: str | None = None
    detail: str | None = None  # why: the enable link, the cut, or the error reason

    def note(self) -> str:
        if self.state not in ("ok", "partial"):
            return STATE_PROBLEM_NOTES[self.state]
        text_ = (
            f"{self.listed} files · {self.new} new · {self.changed} changed · "
            f"{self.gone} gone · {self.parsed} parsed · {self.commitments} commitments"
        )
        return f"{text_} · {PARTIAL_NOTE}" if self.state == "partial" else text_


def note_for(file: drive.DriveFile, data: bytes) -> Note:
    """One Drive file as a Note for the one parser. The day comes only from the file name."""
    kind = drive.note_kind(file)
    if kind == "pdf":
        body = notes.pdf_text(data)
    elif kind == "docx":
        body = notes.docx_text(data)
    elif kind in ("gdoc", "text"):
        body = notes.decode_text(data)
    else:
        raise notes.NoteUnreadable("type")
    return Note("drive", f"drive:{file.id}", notes.day_from_name(file.name), body)


def _read_note(client: drive.DriveClient, file: drive.DriveFile) -> tuple[Note | None, str | None]:
    """(note, None) or (None, why). Transient Drive errors are raised: try again next poll."""
    if file.size is not None and file.size > notes.MAX_FILE_BYTES:
        return None, "too big"
    try:
        return note_for(file, client.read(file)), None
    except notes.NoteUnreadable as exc:
        return None, str(exc)[:80] or "unreadable"
    except drive.DriveError as exc:
        if exc.transient:
            raise
        return None, f"drive {exc.status}"


def _save_file(session: Session, file: drive.DriveFile, now: datetime, problem: str | None) -> None:
    session.execute(
        text(
            "insert into notes_file (file_id, name, path, mime, modified_time, parsed_modified, "
            "parsed_at, state, problem, first_seen, last_seen, gone_at) "
            "values (:id, :name, :path, :mime, :mod, :mod, :now, 'active', :problem, :now, :now, "
            "null) on conflict (file_id) do update set name = :name, path = :path, mime = :mime, "
            "modified_time = :mod, parsed_modified = :mod, parsed_at = :now, state = 'active', "
            "problem = :problem, last_seen = :now, gone_at = null"
        ),
        {
            "id": file.id,
            "name": file.name[:300],
            "path": file.path[:500],
            "mime": file.mime,
            "mod": file.modified,
            "now": now,
            "problem": problem,
        },
    )


def _apply(
    session: Session, client: drive.DriveClient, folder_id: str, now: datetime, stats: DriveStats
) -> None:
    found = drive.walk(client, folder_id)
    known = {
        r.file_id: r
        for r in session.execute(text("select file_id, state, parsed_modified from notes_file"))
    }
    stats.listed, stats.skipped = len(found.files), found.skipped
    stats.unreadable = len(found.problems)
    batch: list[Note] = []
    for file in found.files:
        row = known.get(file.id)
        if row is None:
            stats.new += 1
        elif row.state == "gone" or row.parsed_modified != file.modified:
            stats.changed += 1
        else:
            continue
        note, problem = _read_note(client, file)
        _save_file(session, file, now, problem)
        if note is None:
            stats.unreadable += 1
        else:
            stats.parsed += 1
            batch.append(note)
    if found.complete:
        stats.gone = _mark_gone(session, known, {f.id for f in found.files}, now)
    else:  # a cut listing proves nothing about the files it did not show (B10)
        stats.state = "partial"
        stats.detail = _partial_detail(found)
    scanned = scan_notes(session, batch)
    stats.commitments, stats.unknown_owner = scanned.added, scanned.unknown_owner
    stats.outside_facts, stats.outside_no_contact = (
        scanned.outside_facts,
        scanned.outside_no_contact,
    )
    open_refs = list(
        session.execute(
            text("select path from notes_file where state = 'active' and problem is not null")
        ).scalars()
    )
    record_unreadable(session, [*open_refs, *found.problems], "drive", now)


def _partial_detail(found: drive.Walk) -> str:
    if found.problems:
        shown = ", ".join(found.problems[:3])
        return f"{len(found.problems)} folders not listed: {shown}"[:200]
    return (found.cut or "listing cut")[:200]


def _mark_gone(session: Session, known: dict, current: set[str], now: datetime) -> int:
    ids = [fid for fid, row in known.items() if row.state == "active" and fid not in current]
    if ids:
        session.execute(
            text(
                "update notes_file set state = 'gone', gone_at = :now "
                "where file_id = any(:ids) and state = 'active'"
            ),
            {"now": now, "ids": ids},
        )
    return len(ids)


def _reason_text(error: drive.DriveError) -> str:
    """A short fixed reason for the health line. Never a token, never a Google message."""
    return f"{error.status} {error.reason}"[:100]


def _state_for(error: drive.DriveError) -> str:
    if error.api_disabled:
        return "api_disabled"
    if error.transient:  # a rate limit or a network error: try again next cycle
        return "error"
    if error.not_shared:  # a 403 for another reason (quota, policy) is `error`
        return "folder_not_shared"
    return "error"


def _save_state(
    session: Session, folder_id: str, state: str, detail: str | None, now: datetime
) -> None:
    """One row with the last result. Counts come from notes_file when the sync was ok.

    A partial sync updates the counts too (it did list files) but not `last_ok_at`.
    """
    ok = state == "ok"
    counted = state in ("ok", "partial")
    session.execute(
        text(
            "insert into notes_sync_state (key, folder_id, state, detail, files, last_change, "
            "checked_at, last_ok_at) select :key, :folder, :state, :detail, "
            "count(*) filter (where state = 'active'), "
            "max(modified_time) filter (where state = 'active'), :now, "
            "case when :ok then cast(:now as timestamptz) end from notes_file "
            "on conflict (key) do update set folder_id = :folder, state = :state, "
            "detail = :detail, checked_at = :now, "
            "files = case when :counted then excluded.files else notes_sync_state.files end, "
            "last_change = case when :counted then excluded.last_change "
            "else notes_sync_state.last_change end, "
            "last_ok_at = case when :ok then :now else notes_sync_state.last_ok_at end"
        ),
        {
            "key": STATE_KEY,
            "folder": folder_id,
            "state": state,
            "detail": detail,
            "now": now,
            "ok": ok,
            "counted": counted,
        },
    )


def sync_drive(
    session: Session,
    client: drive.DriveClient,
    folder_id: str,
    now: datetime | None = None,
) -> DriveStats:
    """One sync of the folder. A Drive failure becomes a state, never a crash."""
    now = now or now_ist()
    stats = DriveStats()
    try:
        with session.begin_nested():
            _apply(session, client, folder_id, now, stats)
    except drive.DriveError as exc:
        log.warning("notes_drive_failed", status=exc.status, reason=exc.reason)
        stats = DriveStats(state=_state_for(exc), enable_url=exc.activation_url)
        stats.detail = stats.enable_url or _reason_text(exc)
        if stats.state == "folder_not_shared":
            # The web role cannot read the key file: keep the address to share with (A15).
            stats.detail = getattr(client, "account", None) or stats.detail
        _save_state(session, folder_id, stats.state, stats.detail, now)
        return stats
    _save_state(session, folder_id, stats.state, stats.detail, now)
    return stats
