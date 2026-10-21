"""Mail: read-only IMAP of the Membrane cc mailbox; each message becomes Notes (Q-R2-3, Q-R2-4).

Why: meeting notes arrive by mail, as body text or as .md / .txt / .pdf attachments. This
module only READS. It opens the mailbox with `readonly=True` and fetches with BODY.PEEK, so
no message is marked as read, moved or deleted. It never sends. The host must pass the egress
allowlist (`imap.gmail.com`) like every outbound call. The app password is never logged and
never put in an error text. Mail content is DATA: it goes to the one note parser
(`intel/notes.py`) and nowhere else.

With `MAIL_USER` empty the job is skipped and Sources and health says "mail: not configured".
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import imaplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from saley.core.clock import today_ist, tz
from saley.core.egress import get_policy
from saley.core.logging import get_logger
from saley.core.settings import Settings
from saley.intel.notes import (
    MAX_FILE_BYTES,
    NOTE_SUFFIXES,
    Note,
    PdfUnreadable,
    decode_text,
    pdf_text,
)

log = get_logger("saley.intel.mail")

LOOKBACK_DAYS = 14
MAX_MESSAGES = 200
MAILBOX = "INBOX"
MAX_MESSAGE_ATTACH_BYTES = 10_000_000  # all note attachments of one message (M6)
MAX_RUN_ATTACH_BYTES = 50_000_000  # all note attachments of one poll (M6)
# IMAP wants English month names whatever the machine locale is (strftime %b follows it).
IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

ImapFactory = Callable[..., Any]


@dataclass
class ByteBudget:
    """Attachment bytes still allowed in this poll. One object is shared by all messages."""

    left: int = MAX_RUN_ATTACH_BYTES


def imap_date(day: date) -> str:
    """'05-Jan-2026' for the IMAP SINCE search, independent of the machine locale (M5)."""
    return f"{day.day:02d}-{IMAP_MONTHS[day.month - 1]}-{day.year}"


class MailError(RuntimeError):
    """The mailbox could not be read. The text never holds the password."""


def configured(settings: Settings) -> bool:
    return bool(settings.mail_user.strip())


def fetch_raw_messages(
    settings: Settings,
    since: date | None = None,
    factory: ImapFactory = imaplib.IMAP4_SSL,
) -> list[bytes]:
    """Raw messages of the last LOOKBACK_DAYS days. Read only. Raises MailError."""
    since = since or today_ist() - timedelta(days=LOOKBACK_DAYS)
    host = settings.mail_imap_host
    get_policy().guard(f"https://{host}")  # allowlist and per-host rate limit (EgressBlocked)
    try:
        # H2: verify the server certificate and the host name. Never an unverified context.
        client = factory(
            host, timeout=settings.http_timeout_seconds, ssl_context=ssl.create_default_context()
        )
    except (OSError, imaplib.IMAP4.error) as exc:
        raise MailError(f"cannot connect to {host}: {type(exc).__name__}") from None
    raw: list[bytes] = []
    try:
        try:
            client.login(settings.mail_user, settings.mail_app_password)
        except imaplib.IMAP4.error:
            raise MailError("login refused") from None  # not `from exc`: keep server text out
        status, _ = client.select(MAILBOX, readonly=True)
        if status != "OK":
            raise MailError("cannot open the mailbox")
        status, data = client.search(None, "SINCE", imap_date(since))
        if status != "OK":
            raise MailError("search failed")
        ids = (data[0] or b"").split()[-MAX_MESSAGES:]
        for number in ids:
            status, parts = client.fetch(number, "(BODY.PEEK[])")
            if status == "OK" and parts and isinstance(parts[0], tuple):
                raw.append(bytes(parts[0][1]))
    except (OSError, imaplib.IMAP4.error) as exc:
        raise MailError(f"mailbox read failed: {type(exc).__name__}") from None
    finally:
        try:
            client.logout()
        except Exception:  # the session may be gone already
            log.debug("mail_logout_failed")
    return raw


def _sender(msg: EmailMessage) -> str | None:
    name, address = parseaddr(str(msg.get("From", "")))
    label = name.strip() or address.split("@", 1)[0]
    return label or None


def _received(msg: EmailMessage) -> date | None:
    """The day the mail arrived, in IST like every other day in Saley (M5)."""
    try:
        sent = parsedate_to_datetime(str(msg.get("Date", "")))
    except (TypeError, ValueError):
        return None
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=UTC)  # a date header without a zone is read as UTC
    return sent.astimezone(tz()).date()


def _message_ref(msg: EmailMessage, raw: bytes) -> str:
    ident = str(msg.get("Message-ID", "")).strip()
    digest = hashlib.sha1((ident or raw[:2000].hex()).encode(), usedforsecurity=False)
    return "mail:" + digest.hexdigest()[:16]


def message_to_notes(raw: bytes, budget: ByteBudget | None = None) -> tuple[list[Note], list[str]]:
    """(notes, problems): the body and each .md / .txt / .pdf attachment of one message.

    Attachment bytes are capped per message and per poll (`budget`, M6). An attachment over a
    cap is a problem line, never a crash and never read.
    """
    budget = budget if budget is not None else ByteBudget()
    message_left = MAX_MESSAGE_ATTACH_BYTES
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    if not isinstance(msg, EmailMessage):  # pragma: no cover - the default policy gives it
        return [], []
    ref, author, received = _message_ref(msg, raw), _sender(msg), _received(msg)
    notes: list[Note] = []
    problems: list[str] = []
    body = msg.get_body(preferencelist=("plain",))
    if body is not None:
        text = body.get_content()
        if isinstance(text, str) and text.strip():
            notes.append(Note("mail", ref, received, text, author))
    for part in msg.iter_attachments():
        name = part.get_filename() or ""
        suffix = ("." + name.rsplit(".", 1)[-1]).lower() if "." in name else ""
        if suffix not in NOTE_SUFFIXES:
            continue
        label = f"{ref}/{name[:80]}"
        data = part.get_payload(decode=True)
        size = len(data) if isinstance(data, bytes) else 0
        if not isinstance(data, bytes) or size > min(MAX_FILE_BYTES, message_left, budget.left):
            problems.append(label)
            continue
        message_left -= size
        budget.left -= size
        try:
            text = pdf_text(data) if suffix == ".pdf" else decode_text(data)
        except PdfUnreadable:
            problems.append(label)
            continue
        notes.append(Note("mail", label, received, text, author))
    return notes, problems


def read_notes(
    settings: Settings, factory: ImapFactory = imaplib.IMAP4_SSL, since: date | None = None
) -> tuple[list[Note], list[str]]:
    """All notes in the mailbox window. Raises MailError or EgressBlocked."""
    notes: list[Note] = []
    problems: list[str] = []
    budget = ByteBudget()
    for raw in fetch_raw_messages(settings, since, factory):
        found, bad = message_to_notes(raw, budget)
        notes += found
        problems += bad
    return notes, problems


def state(settings: Settings, last_ok: datetime | None, last_failed: bool) -> str:
    """not_configured, failing, waiting (no poll yet) or ok: the health row of the mail source."""
    if not configured(settings):
        return "not_configured"
    if last_failed:
        return "failing"
    return "ok" if last_ok is not None else "waiting"
