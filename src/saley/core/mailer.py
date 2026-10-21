"""Outbound mail with a sink (D124, R4-C03).

WHY: CLAUDE.md "Email sending": `EMAIL_SEND_ENABLED=false` stays false until a decision. In that
state `send` writes one `.eml` file and opens no connection. Humans send the drafts. The SMTP
path exists and is tested against a mock only.
"""

from __future__ import annotations

import smtplib
import ssl
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

import structlog

from saley.core.egress import EgressBlocked, get_policy
from saley.core.settings import Settings, get_settings

log = structlog.get_logger(__name__)

SMTP_TIMEOUT_SECONDS = 20


class MailerError(Exception):
    """The draft or the set-up is not fit to send."""


@dataclass(frozen=True)
class Draft:
    to: str
    subject: str
    body: str
    cc: str = ""


def _clean_header(name: str, value: str) -> str:
    """Refuse CR, LF and NUL: one of them in a header value would add a header."""
    if not isinstance(value, str) or any(c in value for c in "\r\n\0"):
        raise MailerError(f"header {name} has a line break or is not text")
    return value


def build_message(draft: Draft, sender: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = _clean_header("From", sender or "saley@localhost")
    msg["To"] = _clean_header("To", draft.to)
    if draft.cc:
        msg["Cc"] = _clean_header("Cc", draft.cc)
    msg["Subject"] = _clean_header("Subject", draft.subject)
    msg.set_content(draft.body)
    return msg


def write_to_sink(msg: EmailMessage, sink_dir: str | Path) -> Path:
    """Write one .eml. The name is time plus a random id: no user text, so no traversal."""
    directory = Path(sink_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{stamp}-{uuid.uuid4().hex}.eml"
    path.write_bytes(bytes(msg))
    return path


class SmtpSender:
    """Real sending. Built only when EMAIL_SEND_ENABLED is true."""

    def __init__(self, settings: Settings | None = None) -> None:
        s = settings or get_settings()
        if not s.email_send_enabled:
            raise MailerError("SmtpSender needs EMAIL_SEND_ENABLED=true")
        if not s.email_from.strip():
            raise MailerError("EMAIL_FROM is required when EMAIL_SEND_ENABLED=true")
        _clean_header("EMAIL_FROM", s.email_from)
        try:
            get_policy().check(f"https://{s.smtp_host}")
        except EgressBlocked as exc:
            raise MailerError(f"SMTP_HOST refused by the egress allowlist: {exc}") from exc
        self.host = s.smtp_host
        self.port = s.smtp_port
        self.sender = s.email_from.strip()
        self._password = s.smtp_app_password

    def send(self, draft: Draft) -> None:
        msg = build_message(draft, self.sender)
        context = ssl.create_default_context()  # verifies the chain and the host name
        with smtplib.SMTP(self.host, self.port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.starttls(context=context)
            if self._password:
                smtp.login(self.sender, self._password)
            smtp.send_message(msg)
        log.info("mail_sent", host=self.host)


def send(draft: Draft, settings: Settings | None = None) -> Path | None:
    """Flag off: write the sink file and return its path. Flag on: send, return None."""
    s = settings or get_settings()
    if not s.email_send_enabled:
        path = write_to_sink(build_message(draft, s.email_from), s.email_sink_path)
        log.info("mail_to_sink", file=path.name)
        return path
    SmtpSender(s).send(draft)
    return None
