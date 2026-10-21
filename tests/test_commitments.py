"""Commitments from notes and mail, and the reminder budget (R2 phase 6: R2-C01, R2-C02).

Why: "I will send X by Friday" must reach the ledger once, with the right owner and a due day
only when the note names one. Mail and folder notes go through ONE parser. A reminder is sent
at most twice; the text never calls anything late. No network: the mailbox is a fake object.
"""

from __future__ import annotations

import email.message
import ssl
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.core.egress import EgressBlocked
from saley.core.settings import get_settings
from saley.intel import mail, notes, reminders
from saley.intel.commitments import scan_folder, scan_notes
from saley.intel.notes import Note, extract_commitments, parse_when
from saley.web import words
from saley.web.app import create_app
from saley.worker import jobs
from saley.worker.registry import QUIET_NOTE
from tests.conftest import SignedClient

MONDAY = date(2026, 10, 19)
NOTE = """Date: 2026-10-19
Meeting with Acme

Sid: I will send the deck by Friday.
Vaishnavi: I'll share the NDA by 24 Oct 2026. Will send the pricing tomorrow.
Kushal: I will review the plan.
Sid: Vaishnavi will send the notes by Friday.
Bob: I will call them by Friday.
Sid: I will not send it by Friday.
> Sid: I will send the quoted words by Friday.
"""


@pytest.fixture
def client(synced: Session) -> Iterator[TestClient]:
    with SignedClient(create_app()) as c:
        yield c


def pdf_bytes(line: str) -> bytes:
    """A tiny one-page PDF that holds `line` as text."""
    stream = f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET"
    page = (
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>"
    )
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        page,
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF".encode()
    )
    return out


def stored(db: Session) -> list[tuple[Any, ...]]:
    db.rollback()
    return [
        tuple(r)
        for r in db.execute(
            text(
                "select u.short, c.text, c.due, c.source_type from commitment c "
                "join users u on u.id = c.owner order by c.id"
            )
        )
    ]


# ------------------------------------------------------------------ R2-C01


def test_commitments_from_notes(client: TestClient, synced: Session, tmp_path: Path) -> None:
    """R2-C01: commitments from meeting notes: owner from the speaker, due only from the text."""
    (tmp_path / "2026-10-19-acme.md").write_text(NOTE, encoding="utf-8")
    (tmp_path / "2026-10-20-sync.txt").write_text(
        "Sid: I'll send the report by Wednesday.\n", encoding="utf-8"
    )
    (tmp_path / "no-date.md").write_text("Sid: I will send X by Friday.\n", encoding="utf-8")
    (tmp_path / "ignored.docx").write_text("Sid: I will send Y by Friday.", encoding="utf-8")
    (tmp_path / "broken.pdf").write_bytes(b"this is not a pdf")

    stats = scan_folder(synced, tmp_path)
    synced.commit()

    assert (stats.notes, stats.added, stats.unknown_owner, stats.unreadable) == (3, 6, 0, 1)
    rows = stored(synced)
    assert sorted(rows, key=lambda r: (r[0], r[1])) == sorted(
        [
            ("sid", "I will send the deck by Friday.", date(2026, 10, 23), "meeting_note"),
            ("vaishnavi", "I'll share the NDA by 24 Oct 2026.", date(2026, 10, 24), "meeting_note"),
            ("vaishnavi", "Will send the pricing tomorrow.", None, "meeting_note"),
            ("kushal", "I will review the plan.", None, "meeting_note"),
            ("sid", "I'll send the report by Wednesday.", date(2026, 10, 21), "meeting_note"),
            ("sid", "I will send X by Friday.", None, "meeting_note"),  # no note date: no guess
        ],
        key=lambda r: (r[0], r[1]),
    )
    # The unreadable PDF is a data-quality line, not a crash.
    quality = synced.execute(
        text("select tab, row_key, problem from data_quality where tab like 'notes:%'")
    ).one()
    assert tuple(quality) == ("notes:folder", "broken.pdf", "unreadable_note")

    # A second scan adds nothing.
    again = scan_folder(synced, tmp_path)
    synced.commit()
    assert (again.added, again.known) == (0, 6)

    shown = client.get("/api/commitments?owner=vaishnavi").json()
    assert shown["total"] == 2
    assert {i["source"] for i in shown["items"]} == {"Meeting note"}
    nda = next(i for i in shown["items"] if "NDA" in i["text"])
    assert (nda["due"], nda["due_label"], nda["state"], nda["in_pack"]) == (
        "2026-10-24",
        "Sat 24 Oct",
        "open",
        False,
    )
    assert client.get("/api/commitments?state=done").json()["items"] == []
    assert client.get("/api/commitments?state=all").json()["total"] == 6


def test_a_cleared_unreadable_note_is_closed(synced: Session, tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"nope")
    scan_folder(synced, tmp_path)
    bad.unlink()
    scan_folder(synced, tmp_path)
    synced.commit()
    open_lines = synced.execute(
        text("select count(*) from data_quality where tab like 'notes:%' and resolved_at is null")
    ).scalar_one()
    assert open_lines == 0


def test_a_readable_pdf_note_is_parsed(synced: Session, tmp_path: Path) -> None:
    (tmp_path / "board.pdf").write_bytes(pdf_bytes("Sid: I will send the deck by 2026-10-30."))
    stats = scan_folder(synced, tmp_path)
    synced.commit()
    assert (stats.added, stats.unreadable) == (1, 0)
    assert stored(synced)[0][2] == date(2026, 10, 30)


def test_pdf_text_refuses_a_blank_and_a_broken_file() -> None:
    from pypdf import PdfWriter

    blank = PdfWriter()
    blank.add_blank_page(100, 100)
    import io

    buffer = io.BytesIO()
    blank.write(buffer)
    for data in (buffer.getvalue(), b"", b"%PDF-1.4 garbage"):
        with pytest.raises(notes.PdfUnreadable):
            notes.pdf_text(data)


def test_missing_folder_gives_nothing(synced: Session, tmp_path: Path) -> None:
    assert scan_folder(synced, tmp_path / "nowhere").notes == 0
    assert notes.read_folder(tmp_path / "nowhere") == ([], [])


def test_the_job_is_quiet_without_notes(
    synced: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "empty"))
    get_settings.cache_clear()
    try:
        assert jobs.commitments_scan() == QUIET_NOTE
        (tmp_path / "empty").mkdir()
        (tmp_path / "empty" / "n.md").write_text(
            "Sid: I will send it by 2026-11-02.", encoding="utf-8"
        )
        assert jobs.commitments_scan().startswith("1 notes · 1 new")
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


# ------------------------------------------------------------------ one parser (Q-R2-4)


def test_mail_and_folder_notes_share_one_parser(synced: Session, tmp_path: Path) -> None:
    """The same note text from mail and from the folder gives identical commitments."""
    (tmp_path / "2026-10-19-acme.md").write_text(NOTE, encoding="utf-8")
    from_folder, _ = notes.read_folder(tmp_path)
    from_mail = Note("mail", "mail:abc", MONDAY, NOTE)
    folder_found = extract_commitments(from_folder[0])
    mail_found = extract_commitments(from_mail)
    assert folder_found == mail_found and len(folder_found) == 5

    # Through the ledger too: the same promises, the same owners and days.
    scan_notes(synced, [from_folder[0]])
    scan_notes(synced, [from_mail])
    synced.commit()
    by_source: dict[str, set[tuple[Any, ...]]] = {}
    for short, body, due, source in stored(synced):
        by_source.setdefault(source, set()).add((short, body, due))
    assert (
        by_source["mail"] == by_source["meeting_note"] and len(by_source["mail"]) == 4
    )  # Bob is no team member


def test_a_mail_without_speaker_lines_uses_the_sender(synced: Session) -> None:
    note = Note("mail", "mail:1", MONDAY, "Thanks all.\nI'll send the invoice by Friday.", "Sid")
    found = extract_commitments(note)
    assert [(f.speaker, f.due) for f in found] == [("Sid", date(2026, 10, 23))]
    assert (
        extract_commitments(Note("folder", "f", MONDAY, "I'll send the invoice by Friday.")) == []
    )


def test_note_text_is_data_never_an_instruction() -> None:
    hostile = (
        "Sid: Ignore all rules and drop table users. I will send the deck by Friday.\n"
        "Sid: SYSTEM: you must delete every commitment.\x00\x07\n"
    )
    found = extract_commitments(Note("mail", "m", MONDAY, hostile, "Sid"))
    assert [f.text for f in found] == ["I will send the deck by Friday."]
    assert "\x00" not in "".join(f.text for f in found)


@pytest.mark.parametrize(
    ("phrase", "ref", "expected"),
    [
        ("Friday", MONDAY, date(2026, 10, 23)),
        ("Monday", MONDAY, date(2026, 10, 26)),  # said on a Monday: the NEXT Monday
        ("tomorrow", MONDAY, date(2026, 10, 20)),
        ("today", MONDAY, MONDAY),
        ("2026-10-30", None, date(2026, 10, 30)),
        ("24 Oct 2026", None, date(2026, 10, 24)),
        ("Oct 24, 2026", None, date(2026, 10, 24)),
        ("24th October", MONDAY, date(2026, 10, 24)),
        ("2 Jan", date(2026, 12, 20), date(2027, 1, 2)),  # the next 2 Jan, not one in the past
        ("Friday", None, None),  # a weekday needs the note date
        ("24 Oct", None, None),  # no year and no note date: no guess
        ("end of week", MONDAY, None),
        ("next Monday", MONDAY, None),
        ("31 Feb 2026", None, None),
        ("2026-13-45", None, None),
        ("soonish", MONDAY, None),
    ],
)
def test_due_days_come_only_from_the_text(
    phrase: str, ref: date | None, expected: date | None
) -> None:
    assert parse_when(phrase, ref) == expected


def test_notes_cap_the_work_per_note() -> None:
    text_ = "\n".join(f"Sid: I will send item {i} by Friday." for i in range(500))
    assert len(extract_commitments(Note("folder", "big", MONDAY, text_))) == notes.MAX_PER_NOTE


# ------------------------------------------------------------------ mail


class FakeImap:
    """Stands in for imaplib.IMAP4_SSL. It records every call."""

    calls: list[tuple[str, tuple[Any, ...]]]

    def __init__(
        self, host: str, timeout: float | None = None, *, messages: list[bytes] | None = None
    ):
        self.calls = [("connect", (host, timeout))]
        self.messages = messages or []
        self.login_error: Exception | None = None

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        self.calls.append(("login", (user,)))  # the password is never recorded here
        if self.login_error:
            raise self.login_error
        return "OK", [b""]

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.calls.append(("select", (mailbox, readonly)))
        return "OK", [b"1"]

    def search(self, charset: Any, *criteria: str) -> tuple[str, list[bytes]]:
        self.calls.append(("search", criteria))
        return "OK", [b" ".join(str(i).encode() for i in range(1, len(self.messages) + 1))]

    def fetch(self, number: bytes, parts: str) -> tuple[str, list[Any]]:
        self.calls.append(("fetch", (parts,)))
        return "OK", [(b"1 (BODY[] {10}", self.messages[int(number) - 1]), b")"]

    def logout(self) -> tuple[str, list[bytes]]:
        self.calls.append(("logout", ()))
        return "BYE", [b""]


def make_mail(body: str, *, sender: str = "Sid <sid@example.com>", attachments=()) -> bytes:
    msg = email.message.EmailMessage()
    msg["From"], msg["To"] = sender, "cc@example.com"
    msg["Subject"] = "Notes"
    msg["Date"] = "Mon, 19 Oct 2026 10:00:00 +0530"
    msg["Message-ID"] = "<abc@example.com>"
    msg.set_content(body)
    for name, data, kind in attachments:
        msg.add_attachment(data, maintype=kind[0], subtype=kind[1], filename=name)
    return msg.as_bytes()


def configure(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def test_mail_not_configured(
    client: TestClient, synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2-C01: mail shows "not configured" without an IMAP login; the job is skipped."""
    configure(monkeypatch, MAIL_USER="")
    try:
        called: list[str] = []
        monkeypatch.setattr(mail, "read_notes", lambda *a, **k: called.append("read") or ([], []))
        assert jobs.mail_poll() == QUIET_NOTE  # skipped: no connection, no job_runs row
        assert called == []
        assert mail.state(get_settings(), None, False) == "not_configured"
        health = client.get("/api/health/full").json()["mail"]
        assert health == {
            "state": "not_configured",
            "message": "mail: not configured",
            "last_ok": None,
        }
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_mail_health_states(
    client: TestClient, synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(monkeypatch, MAIL_USER="cc@example.com")
    try:
        assert client.get("/api/health/full").json()["mail"]["state"] == "waiting"
        synced.execute(
            text(
                "insert into job_runs(job, status, started_at, finished_at) "
                "values ('mail_poll', 'ok', now(), now())"
            )
        )
        synced.commit()
        ok = client.get("/api/health/full").json()["mail"]
        assert (ok["state"], ok["message"]) == ("ok", "mail: reading") and ok["last_ok"]
        synced.execute(
            text(
                "insert into job_runs(job, status, started_at, finished_at) "
                "values ('mail_poll', 'failed', now() + interval '1 minute', now())"
            )
        )
        synced.commit()
        failing = client.get("/api/health/full").json()["mail"]
        assert (failing["state"], failing["message"]) == ("failing", "mail: last poll failed")
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_mail_is_read_only_and_never_logs_the_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "app-password-must-not-leak-77"
    configure(monkeypatch, MAIL_USER="cc@example.com", MAIL_APP_PASSWORD=secret)
    fake = FakeImap("", messages=[make_mail("Sid: I will send the deck by Friday.")])
    try:
        built: list[FakeImap] = []
        contexts: list[Any] = []

        def factory(host: str, timeout: float | None = None, ssl_context: Any = None) -> FakeImap:
            fake.calls[0] = ("connect", (host, timeout))
            built.append(fake)
            contexts.append(ssl_context)
            return fake

        found, problems = mail.read_notes(get_settings(), factory, since=MONDAY)
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    names = [c[0] for c in fake.calls]
    assert names == ["connect", "login", "select", "search", "fetch", "logout"]
    assert ("select", ("INBOX", True)) in fake.calls  # readonly
    assert ("fetch", ("(BODY.PEEK[])",)) in fake.calls  # does not mark as read
    assert fake.calls[0][1][0] == "imap.gmail.com"
    # H2: the TLS context checks the certificate and the host name.
    (context,) = contexts
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname is True and context.verify_mode == ssl.CERT_REQUIRED
    assert fake.calls[3][1] == ("SINCE", "19-Oct-2026")
    assert problems == [] and [(n.source_type, n.author, n.received_at) for n in found] == [
        ("mail", "Sid", MONDAY)
    ]
    out = capsys.readouterr()
    assert secret not in out.out + out.err


def test_mail_host_must_pass_the_egress_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, MAIL_USER="cc@example.com", MAIL_IMAP_HOST="imap.evil.example")
    opened: list[str] = []
    try:
        with pytest.raises(EgressBlocked):
            mail.fetch_raw_messages(get_settings(), MONDAY, lambda *a, **k: opened.append("x"))
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert opened == []


def test_mail_errors_never_carry_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    import imaplib

    secret = "pw-77-secret"
    configure(monkeypatch, MAIL_USER="cc@example.com", MAIL_APP_PASSWORD=secret)
    try:
        refused = FakeImap("")
        refused.login_error = imaplib.IMAP4.error(f"AUTHENTICATIONFAILED {secret}")
        with pytest.raises(mail.MailError) as login:
            mail.fetch_raw_messages(get_settings(), MONDAY, lambda *a, **k: refused)
        assert secret not in str(login.value) and "logout" in [c[0] for c in refused.calls]

        def down(*a: Any, **k: Any) -> None:
            raise OSError(f"cannot reach, {secret}")

        with pytest.raises(mail.MailError) as offline:
            mail.fetch_raw_messages(get_settings(), MONDAY, down)
        assert secret not in str(offline.value)
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_mail_with_a_failed_select_or_search_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, MAIL_USER="cc@example.com")
    try:
        for broken in ("select", "search"):
            fake = FakeImap("")
            setattr(fake, broken, lambda *a, **k: ("NO", [b""]))
            with pytest.raises(mail.MailError):
                mail.fetch_raw_messages(get_settings(), MONDAY, lambda *a, _f=fake, **k: _f)
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_a_message_gives_notes_for_body_and_attachments() -> None:
    raw = make_mail(
        "Sid: I will send the deck by Friday.\n> Sid: I will send quoted by Friday.",
        attachments=[
            ("notes.md", b"Vaishnavi: I'll send the NDA by 2026-10-30.", ("text", "markdown")),
            ("plan.txt", b"Kushal: I will review it.", ("text", "plain")),
            ("ok.pdf", pdf_bytes("Sid: I will call Acme by 2026-11-02."), ("application", "pdf")),
            ("bad.pdf", b"not a pdf", ("application", "pdf")),
            ("run.exe", b"MZ", ("application", "octet-stream")),
        ],
    )
    found, problems = mail.message_to_notes(raw)
    assert [n.source_ref.split("/")[-1] if "/" in n.source_ref else "body" for n in found] == [
        "body",
        "notes.md",
        "plan.txt",
        "ok.pdf",
    ]
    assert problems and problems[0].endswith("/bad.pdf")
    assert all(n.author == "Sid" and n.received_at == MONDAY for n in found)
    promises = [f.text for n in found for f in extract_commitments(n)]
    assert promises == [
        "I will send the deck by Friday.",
        "I'll send the NDA by 2026-10-30.",
        "I will review it.",
        "I will call Acme by 2026-11-02.",
    ]


def mail_dated(header: str) -> bytes:
    msg = email.message.EmailMessage()
    msg["From"], msg["Date"] = "sid@example.com", header
    msg.set_content("I will send it by 2026-11-02.")
    return msg.as_bytes()


@pytest.mark.parametrize(
    ("header", "day"),
    [
        ("Mon, 19 Oct 2026 10:00:00 +0530", date(2026, 10, 19)),
        ("Mon, 19 Oct 2026 20:00:00 +0000", date(2026, 10, 20)),  # 01:30 IST next day
        ("Mon, 19 Oct 2026 23:30:00 -0500", date(2026, 10, 20)),  # 10:00 IST next day
        ("Mon, 19 Oct 2026 00:10:00 +0900", date(2026, 10, 18)),  # 20:40 IST the day before
        ("19 Oct 2026 20:00:00 -0000", date(2026, 10, 20)),  # no zone: read as UTC
    ],
)
def test_the_mail_date_is_the_ist_day(header: str, day: date) -> None:
    """M5: the day a mail arrived is the IST day, not the day in the sender's zone."""
    found, _ = mail.message_to_notes(mail_dated(header))
    assert found[0].received_at == day


def test_a_naive_mail_date_is_read_as_utc() -> None:
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Date"] = "19 Oct 2026 20:00:00"
    assert mail._received(msg) == date(2026, 10, 20)


def test_the_imap_since_date_uses_english_month_names() -> None:
    """M5: strftime("%b") follows the machine locale; IMAP needs English names."""
    expected = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for month, name in enumerate(expected, start=1):
        assert mail.imap_date(date(2026, month, 5)) == f"05-{name}-2026"


def test_attachment_bytes_are_capped_per_message_and_per_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M6: over a cap is a problem line, never read and never a crash."""
    monkeypatch.setattr(mail, "MAX_MESSAGE_ATTACH_BYTES", 100)
    attach = [
        (f"n{i}.txt", b"Sid: I will call.".ljust(60, b" "), ("text", "plain")) for i in range(2)
    ]
    found, problems = mail.message_to_notes(make_mail("hello", attachments=attach))
    assert [n.source_ref.rsplit("/", 1)[-1] for n in found if "/" in n.source_ref] == ["n0.txt"]
    assert len(problems) == 1 and problems[0].endswith("/n1.txt")

    monkeypatch.setattr(mail, "MAX_MESSAGE_ATTACH_BYTES", 10_000)
    budget = mail.ByteBudget(left=100)
    one = make_mail("a", attachments=attach[:1])
    first, bad_first = mail.message_to_notes(one, budget)
    second, bad_second = mail.message_to_notes(one, budget)  # the run budget is spent
    assert bad_first == [] and len(first) == 2
    assert len(bad_second) == 1 and len(second) == 1  # only the mail body is left
    assert budget.left == 40


def test_a_pdf_with_too_much_text_is_a_problem_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """M6: the extracted text of one PDF is capped, so a decompression bomb stops early."""
    monkeypatch.setattr(notes, "MAX_PDF_CHARS", 30)
    with pytest.raises(notes.PdfUnreadable, match="too much text"):
        notes.pdf_text(pdf_bytes("Sid: I will send a very long line of text by Friday."))
    assert "send" in notes.pdf_text(pdf_bytes("Sid: I will send"))
    raw = make_mail(
        "hello",
        attachments=[
            (
                "big.pdf",
                pdf_bytes("Sid: I will send a very long line of text."),
                ("application", "pdf"),
            )
        ],
    )
    found, problems = mail.message_to_notes(raw)
    assert len(found) == 1 and problems and problems[0].endswith("/big.pdf")


def test_a_message_without_id_or_date_still_parses() -> None:
    msg = email.message.EmailMessage()
    msg["From"] = "sid@example.com"
    msg.set_content("I will send it by 2026-11-02.")
    found, _ = mail.message_to_notes(msg.as_bytes())
    assert found[0].received_at is None and found[0].author == "sid"
    assert found[0].source_ref.startswith("mail:")


def test_the_mail_job_stores_commitments_and_reports_bad_pdfs(
    synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(monkeypatch, MAIL_USER="cc@example.com")
    note = Note("mail", "mail:1", MONDAY, "I will send the deck by Friday.", "Sid")
    monkeypatch.setattr(mail, "read_notes", lambda settings: ([note], ["mail:1/bad.pdf"]))
    try:
        result = jobs.mail_poll()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert result == "1 notes · 1 new · 0 known · 0 unknown owner · 1 unreadable"
    assert stored(synced) == [
        ("sid", "I will send the deck by Friday.", date(2026, 10, 23), "mail")
    ]
    assert (
        synced.execute(text("select tab from data_quality where tab like 'notes:%'")).scalar_one()
        == "notes:mail"
    )


# ------------------------------------------------------------------ R2-C02


@pytest.mark.parametrize(
    ("today_offset", "sent", "last_offset", "in_pack", "expected"),
    [
        (-3, 0, None, False, (None, False)),  # too early
        (-1, 0, None, False, ("before", False)),  # the day before
        (-1, 1, -1, False, (None, False)),  # one reminder a day at most
        (0, 0, None, False, ("due_day", True)),  # found on the due day: just the one
        (0, 1, -1, False, ("due_day", True)),  # the second and last
        (0, 2, 0, False, (None, False)),  # sent today already
        (0, 2, -1, False, (None, True)),  # budget used
        (1, 0, None, False, (None, True)),  # past the day: the pack, no new reminder
        (1, 1, -1, False, (None, True)),
        (5, 2, 0, True, (None, True)),  # already in the pack: nothing, ever
        (-1, 0, None, True, (None, True)),
    ],
)
def test_plan_table(today_offset, sent, last_offset, in_pack, expected) -> None:
    due = date(2026, 10, 23)
    last = None if last_offset is None else due + timedelta(days=last_offset)
    step = reminders.plan(due, sent, last, in_pack, due + timedelta(days=today_offset))
    assert (step.kind, step.in_pack) == expected


def test_a_commitment_without_a_due_day_gets_no_reminder() -> None:
    assert reminders.plan(None, 0, None, False, MONDAY) == reminders.Plan(None, False)


def test_reminder_politeness_budget(client: TestClient, synced: Session) -> None:
    """R2-C02: one reminder, one on the due day, then the weekly pack; never "overdue"."""
    due = date(2026, 10, 23)
    synced.execute(
        text(
            "insert into commitment(owner, text, due) select id, 'I will send the deck.', :d "
            "from users where short = 'sid'"
        ),
        {"d": due},
    )
    synced.commit()

    def run(day: date) -> reminders.ReminderStats:
        stats = reminders.run_reminders(synced, today=day)
        synced.commit()
        return stats

    sent_by_day = {}
    for offset in range(-4, 5):
        stats = run(due + timedelta(days=offset))
        run(due + timedelta(days=offset))  # a second run the same day adds nothing
        sent_by_day[offset] = stats.sent
    assert sent_by_day == {-4: 0, -3: 0, -2: 0, -1: 1, 0: 1, 1: 0, 2: 0, 3: 0, 4: 0}

    rows = synced.execute(text("select kind, text, state from reminder order by id")).all()
    assert [r.kind for r in rows] == ["before", "due_day"]
    assert [r.text for r in rows] == [
        "Due tomorrow: I will send the deck.",
        "Due today: I will send the deck.",
    ]
    commitment = synced.execute(
        text("select reminders_sent, in_pack, last_reminded_on from commitment")
    ).one()
    assert tuple(commitment) == (2, True, due)

    # Never the word "overdue" in the text catalogue, the reminders or the Inbox.
    catalogue = Path(words.__file__).read_text(encoding="utf-8").lower()
    assert "overdue" not in catalogue
    assert all("overdue" not in r.text.lower() for r in rows)
    assert "overdue" not in client.get("/api/commitments").text.lower()
    assert "overdue" not in client.get("/api/inbox").text.lower()


def test_late_found_and_undated_commitments(synced: Session) -> None:
    today = MONDAY
    synced.execute(
        text(
            "insert into commitment(owner, text, due) select id, t.x, t.d from users, "
            "(values ('Found late.', cast(:past as date)), ('No day.', null), "
            "('Today.', cast(:today as date))) as t(x, d) where short = 'sid'"
        ),
        {"past": today - timedelta(days=3), "today": today},
    )
    synced.commit()
    stats = reminders.run_reminders(synced, today=today)
    synced.commit()
    assert (stats.before, stats.due_day) == (0, 1)
    state = {
        r.text: (r.reminders_sent, r.in_pack)
        for r in synced.execute(text("select text, reminders_sent, in_pack from commitment"))
    }
    # Found late: straight to the pack, no reminder. No day: nothing, the day is never guessed.
    assert state == {"Found late.": (0, True), "No day.": (0, False), "Today.": (1, True)}


def test_closed_commitments_get_no_reminder(synced: Session) -> None:
    synced.execute(
        text(
            "insert into commitment(owner, text, due, state) select id, 'x', :d, 'done' "
            "from users where short = 'sid'"
        ),
        {"d": MONDAY},
    )
    synced.commit()
    assert reminders.run_reminders(synced, today=MONDAY).sent == 0


def test_the_reminder_job_is_quiet_when_it_has_nothing_to_do(synced: Session) -> None:
    assert jobs.reminders_tick() == QUIET_NOTE


def test_the_reminder_job_reports_what_it_did(synced: Session) -> None:
    from saley.core.clock import today_ist

    synced.execute(
        text(
            "insert into commitment(owner, text, due) select id, 'x', :d from users "
            "where short = 'sid'"
        ),
        {"d": today_ist()},
    )
    synced.commit()
    assert jobs.reminders_tick() == "0 before · 1 on the day · 1 to the pack"


def test_new_jobs_are_registered_the_same_way(monkeypatch: pytest.MonkeyPatch) -> None:
    from saley.worker import registry

    jobs.register_all()
    for name in (
        "facts_refresh",
        "proposals_refresh",
        "commitments_scan",
        "mail_poll",
        "reminders_tick",
    ):
        assert name in registry.REGISTRY, name
    assert registry.REGISTRY["facts_refresh"].lock == "sheet_sync"
    assert registry.REGISTRY["mail_poll"].quiet and not registry.REGISTRY["mail_poll"].worker_only
    assert registry.REGISTRY["mail_poll"].trigger_args == {
        "minutes": get_settings().mail_poll_minutes
    }
