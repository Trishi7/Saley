"""Mailer sink (R4-C03, D124). No test opens a network connection."""

from __future__ import annotations

import smtplib
import ssl
from collections.abc import Iterator
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from saley.core import mailer
from saley.core.egress import EgressPolicy, reset_policy
from saley.core.settings import Settings

DRAFT = mailer.Draft(to="bob@example.com", subject="Hello", body="Body text")


def make_settings(tmp_path: Path, **kw: object) -> Settings:
    base: dict[str, object] = {"email_sink_path": str(tmp_path / "outbox"), "email_from": "a@x.io"}
    return Settings(_env_file=None, **{**base, **kw})  # type: ignore[arg-type]


@pytest.fixture
def smtp_mocks(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    plain, ssl_cls = MagicMock(name="SMTP"), MagicMock(name="SMTP_SSL")
    monkeypatch.setattr(smtplib, "SMTP", plain)
    monkeypatch.setattr(smtplib, "SMTP_SSL", ssl_cls)
    return plain, ssl_cls


@pytest.fixture(autouse=True)
def _reset_policy() -> Iterator[None]:
    yield
    reset_policy(None)


def test_send_disabled_goes_to_sink_never_smtp(tmp_path: Path, smtp_mocks) -> None:
    result = mailer.send(DRAFT, make_settings(tmp_path))

    assert smtp_mocks[0].call_count == 0
    assert smtp_mocks[1].call_count == 0
    files = list((tmp_path / "outbox").glob("*.eml"))
    assert files == [result]
    msg = BytesParser(policy=policy.default).parsebytes(files[0].read_bytes())
    assert (msg["To"], msg["Subject"], msg["From"]) == ("bob@example.com", "Hello", "a@x.io")
    assert msg.get_content().strip() == "Body text"


def test_sink_file_name_has_no_user_text(tmp_path: Path) -> None:
    d = mailer.Draft(to="b@example.com", subject="../../evil", body="x")
    path = mailer.send(d, make_settings(tmp_path))
    assert path is not None
    assert path.parent == tmp_path / "outbox"
    assert "evil" not in path.name and path.suffix == ".eml"


@pytest.mark.parametrize("field", ["to", "subject", "cc"])
@pytest.mark.parametrize("bad", ["x\r\nBcc: spy@evil.io", "x\nBcc: spy@evil.io", "x\ry"])
def test_header_injection_is_refused(tmp_path: Path, field: str, bad: str) -> None:
    kw = {"to": "b@example.com", "subject": "s", "body": "b", field: bad}
    with pytest.raises(mailer.MailerError):
        mailer.send(mailer.Draft(**kw), make_settings(tmp_path))
    assert not list((tmp_path / "outbox").glob("*.eml"))


def test_host_off_allowlist_is_refused_when_flag_on(tmp_path: Path, smtp_mocks) -> None:
    reset_policy(EgressPolicy(["other.example.com"], [], 30, max_wait_seconds=1))
    s = make_settings(tmp_path, email_send_enabled=True, smtp_host="smtp.evil.io")
    with pytest.raises(mailer.MailerError, match="allowlist"):
        mailer.send(DRAFT, s)
    assert smtp_mocks[0].call_count == 0


def test_flag_on_without_email_from_is_refused(tmp_path: Path, smtp_mocks) -> None:
    reset_policy(EgressPolicy(["smtp.gmail.com"], [], 30, max_wait_seconds=1))
    s = make_settings(tmp_path, email_send_enabled=True, email_from="")
    with pytest.raises(mailer.MailerError, match="EMAIL_FROM"):
        mailer.send(DRAFT, s)
    assert smtp_mocks[0].call_count == 0


def test_flag_on_uses_starttls_with_verified_context(tmp_path: Path, smtp_mocks) -> None:
    reset_policy(EgressPolicy(["smtp.gmail.com"], [], 30, max_wait_seconds=1))
    s = make_settings(tmp_path, email_send_enabled=True, smtp_app_password="pw")
    assert mailer.send(DRAFT, s) is None
    plain = smtp_mocks[0]
    assert plain.call_args.args[:2] == ("smtp.gmail.com", 587)
    server = plain.return_value.__enter__.return_value
    ctx = server.starttls.call_args.kwargs["context"]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    server.login.assert_called_once_with("a@x.io", "pw")
    assert server.send_message.call_count == 1
