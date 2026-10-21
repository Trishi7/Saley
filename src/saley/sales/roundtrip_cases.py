"""Live sheet cases in SALEY_TEST (R5 ticket T9: R5-W01..W04, D153, D154, D155, D165, D168).

Why: the unit tests prove the Done, Undo, Add email and commitment rules on a fake sheet. These
cases prove them on the real Google sheet COPY, through the same code the dashboard uses: the
`/api/act`, `/api/undo` and `/api/commitment/.../done` routes (a TestClient on the real app and
the real database), then the worker's own push (`outbox.push_selected`) into the scratch tab.
Nothing is written to Outreach PoCs.

Safety rules, in the order they act:
- `GuardedSheet` wraps the sheet. It refuses a read or a write on any tab but the scratch tab,
  before the call reaches Google. It also refuses to start when the scratch name is the name of
  a real tab. It paces every call (the Google quota) and waits and retries on a 429. It never
  loops fast.
- The outbox rows carry the tab key `poc`; the push maps `poc` to the scratch tab only for our
  rows. After each act the rows are held back one hour (`next_at`), so a running worker does not
  take them. The people do not exist in the real sheet, so it could write nothing there anyway.
- Test people, their account, test commitments, decisions, outbox and audit rows all use the key
  prefix `saley-test|` (commitments: the text prefix `SALEY_TEST`). They are removed at the
  start (a leftover of a crash) and in `finally` at the end, also on failure.
- D165: `add-email` sets EMAIL_WRITE_ALLOWED true in this process only, and puts it back.

A reason only a human can fix is "NOT CONFIGURED" and exit 3 (docs/16 §3).
"""

from __future__ import annotations

import random
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text

from saley.core.clock import now_ist
from saley.core.db import session_scope
from saley.core.logging import scrub_text
from saley.core.settings import get_settings
from saley.sales import vocab
from saley.sales.columns import POC_ROLES, Resolution
from saley.sales.outbox import push_selected
from saley.sales.parse import norm_name
from saley.sales.roundtrip import (
    HEADER_ROW,
    MARKER,
    MARKER_ROW,
    PERSON_ROW,
    TAB_ROWS,
    NotConfigured,
    Report,
    TabNotOurs,
    _tear_down,
    claim_tab,
    header_labels,
    permission_refused,
)
from saley.sales.sheet import HEADER_SCAN_ROWS, TABS, find_header
from saley.sales.writer import SheetPort, norm_value

CASES = ("done-undo", "add-email", "commitment-nonowner", "parity")
KEY_PREFIX = "saley-test|"
COMMITMENT_PREFIX = "SALEY_TEST"
COMMITMENT_TEXT = f"{COMMITMENT_PREFIX} promise"  # exact match only: a real "SALEY_TEST plan" stays
ACT_HEADERS = {"X-Saley-Act": "1"}
ACTOR = "sid"  # an approver (users.role); the actor of every Done and Undo
OTHER = "vaishnavi"  # an approver who does not own the test commitment
PACE_SECONDS = 0.2  # the egress client already waits ~1.5 s a call per host (rate limit)
WAIT_429_SECONDS = 65.0
RETRIES_429 = 3
GOOD_EMAIL = "saley.test@example.com"  # a fixture for SALEY_TEST only, never a found address
BAD_EMAILS = ("not-an-email", "a@b.c;d@e.f")
DEFAULT_N = 20
DEFAULT_SEED = 20261021
WINDOW_MARGIN_SECONDS = 45  # leave a Done alone when its undo window closes this soon
REAL_TAB_ALIASES = frozenset(a for spec in TABS for a in spec.title_aliases)


class TabRefused(RuntimeError):
    """A call named a tab that is not the scratch tab. Nothing reached Google."""


class StepFailed(RuntimeError):
    """A step cannot go on (the report holds the DIFF line). Ends the case, runs the cleanup."""


# ------------------------------------------------------------------ the guarded sheet


class GuardedSheet:
    """A SheetPort that touches only the scratch tab, paced, with a patient retry on 429."""

    def __init__(
        self,
        inner: SheetPort,
        tab: str,
        *,
        pace: float = PACE_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        wait_429: float = WAIT_429_SECONDS,
    ) -> None:
        if " ".join(tab.split()).casefold() in REAL_TAB_ALIASES:
            raise TabRefused(f"{tab!r} is the name of a real tab")
        self._inner, self.tab, self._pace = inner, tab, pace
        self._sleep, self._wait_429 = sleep, wait_429
        self._last = 0.0

    def _call(self, name: str, tab: str, *args: Any) -> Any:
        if tab != self.tab:  # asserted before EVERY call, reads too
            raise TabRefused(f"only the {self.tab} tab may be used; got {tab!r}")
        for attempt in range(RETRIES_429 + 1):
            gap = self._pace - (time.monotonic() - self._last)
            if gap > 0:
                self._sleep(gap)
            self._last = time.monotonic()
            try:
                return getattr(self._inner, name)(tab, *args)
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status != 429 or attempt == RETRIES_429:
                    raise
                self._sleep(self._wait_429)
        raise AssertionError("unreachable")

    def read_header(self, tab: str, rows: int = HEADER_SCAN_ROWS) -> list[list[Any]]:
        return self._call("read_header", tab, rows)

    def read_cell(self, tab: str, row: int, col: int) -> Any:
        return self._call("read_cell", tab, row, col)

    def write_cell(self, tab: str, row: int, col: int, value: str) -> None:
        self._call("write_cell", tab, row, col, value)

    def read_note(self, tab: str, row: int, col: int) -> str:
        return self._call("read_note", tab, row, col)

    def write_note(self, tab: str, row: int, col: int, text: str) -> None:
        self._call("write_note", tab, row, col, text)

    def find_row(self, tab: str, col: int, value: str, after: int = 0) -> int | None:
        return self._call("find_row", tab, col, value, after)

    def write_row(self, tab: str, row: int, values: list[str]) -> None:
        self._call("write_row", tab, row, values)

    def read_column(self, tab: str, col: int) -> list[Any]:
        return self._call("read_column", tab, col)

    def read_row(self, tab: str, row: int) -> list[Any]:
        return self._call("read_row", tab, row)

    def append_row(self, tab: str, key_col: int, values: list[str]) -> int:
        return self._call("append_row", tab, key_col, values)

    def delete_row(self, tab: str, row: int) -> None:
        self._call("delete_row", tab, row)

    def tab_exists(self, title: str) -> bool:
        return self._call("tab_exists", title)

    def add_tab(self, title: str, rows: int = 50, cols: int = 10) -> None:
        self._call("add_tab", title, rows, cols)

    def delete_tab(self, title: str) -> None:
        self._call("delete_tab", title)


# ------------------------------------------------------------------ the dashboard client


class Dash:
    """The dashboard routes through a TestClient: the same code a click runs."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def _headers(self, who: str) -> dict[str, str]:
        return {**ACT_HEADERS, "X-Saley-As": who}

    def act(self, who: str, body: dict[str, Any]) -> Any:
        return self.client.post("/api/act", json=body, headers=self._headers(who))

    def undo(self, who: str, write_id: int) -> Any:
        return self.client.post(f"/api/undo/{write_id}", headers=self._headers(who))

    def commitment_done(self, who: str, commitment_id: int) -> Any:
        return self.client.post(f"/api/commitment/{commitment_id}/done", headers=self._headers(who))

    def decisions(self, who: str) -> dict[int, dict[str, Any]]:
        response = self.client.get(f"/api/decisions?person={who}&limit=1000")
        response.raise_for_status()
        return {int(item["id"]): item for item in response.json()["items"]}


@contextmanager
def dashboard() -> Iterator[Dash]:
    """The real app on the real database. Sign-in is the dev mock (as `sheet verify` does)."""
    from fastapi.testclient import TestClient

    from saley.web.app import create_app

    with TestClient(create_app()) as client:
        if client.post("/auth/mock", follow_redirects=False).status_code != 303:
            raise NotConfigured("sign-in needs AUTH_PROVIDER=mock with SALEY_ENV=dev")
        yield Dash(client)


# ------------------------------------------------------------------ the database rows


def sql(statement: str, **params: Any) -> list[Any]:
    """Run one statement as the database owner (these rows are ours) and return its rows."""
    with session_scope() as session:
        result = session.execute(text(statement), params)
        return list(result.all()) if result.returns_rows else []


def clean_database() -> int:
    """Remove every row the cases made. Safe to run twice. Returns the contacts left (0)."""
    like = KEY_PREFIX + "%"
    cids = [r[0] for r in sql("select id from commitment where text = :t", t=COMMITMENT_TEXT)]
    ckeys = [f"commitment:{i}" for i in cids]
    sql(
        "delete from sync_audit where outbox_id in "
        "(select id from sync_outbox where row_key like :p)",
        p=like,
    )
    sql("delete from sync_outbox where row_key like :p", p=like)
    sql(
        "update decision set undo_of = null where undo_of in "
        "(select id from decision where row_key like :p or row_key = any(cast(:k as text[])))",
        p=like,
        k=ckeys,
    )
    sql(
        "delete from decision where row_key like :p or row_key = any(cast(:k as text[]))",
        p=like,
        k=ckeys,
    )
    sql("delete from reminder where commitment_id = any(cast(:c as bigint[]))", c=cids)
    sql("delete from commitment where id = any(cast(:c as bigint[]))", c=cids)
    sql("delete from contact where sheet_row_key like :p", p=like)
    sql("delete from account where sheet_row_key like :p", p=like)
    left = sql("select count(*) from contact where sheet_row_key like :p", p=like)
    return int(left[0][0])


# ------------------------------------------------------------------ seeded people


@dataclass(frozen=True)
class Seed:
    """A test person: its sheet cells (role -> text) and where it sits."""

    name: str
    cells: dict[str, str]
    connected: bool = False


def seeds(today: date) -> list[Seed]:
    """Real vocabulary values only: "Completed", TRUE, LI and dates, as the real columns hold."""
    old = (today - timedelta(days=10)).isoformat()
    return [
        Seed("Test Meeting A", {"meeting_status": "Scheduled"}),
        Seed("Test Meeting B", {"meeting_status": "Scheduled"}),
        Seed("Test Meeting C", {"meeting_status": "Scheduled"}),
        Seed(
            "Test Decoy",
            {
                "meeting_status": "Completed",
                "first_contact": "TRUE",
                "first_contact_type": "LI",
                "first_contact_date": old,
                "li_connected_date": old,
                "meeting_date": old,
            },
        ),
        Seed("Test First A", {}),
        Seed("Test Email A", {"li_connected_date": old}, connected=True),
    ]


@dataclass
class Person:
    seed: Seed
    row: int  # the 1-based sheet row
    contact_id: int = 0

    @property
    def name(self) -> str:
        return self.seed.name


def sheet_row(person: Person) -> list[str]:
    cells = {
        "sr_no": str(person.row - PERSON_ROW + 1),
        "company": "Saley Test Co",
        "name": person.name,
        **person.seed.cells,
    }
    return [cells.get(role.name, "") for role in POC_ROLES]


def seed_database(people: list[Person], token: str) -> None:
    """The account and the contacts, with the same cells the sheet rows hold."""
    from saley.core.models import Account, Contact

    with session_scope() as session:
        account = Account(
            name="Saley Test Co",
            name_key=f"saley test co {token}",
            sheet_row_key=f"{KEY_PREFIX}{token}|account",
        )
        session.add(account)
        session.flush()
        for number, person in enumerate(people):
            cells = person.seed.cells
            contact = Contact(
                account_id=account.id,
                sheet_row_key=f"{KEY_PREFIX}{token}|{number}",
                sr_no=str(person.row - PERSON_ROW + 1),
                row_number=person.row,
                name=person.name,
                name_key=norm_name(person.name),
                meeting_status=cells.get("meeting_status"),
                first_contact=cells.get("first_contact"),
                first_contact_type=cells.get("first_contact_type"),
                first_contact_date=_day(cells.get("first_contact_date")),
                li_connected_date=_day(cells.get("li_connected_date")),
                meeting_date=_day(cells.get("meeting_date")),
                connected=person.seed.connected,
                li_status="Connected" if person.seed.connected else None,
                contacted=bool(cells.get("first_contact")),
                cells=dict(cells),
            )
            session.add(contact)
            session.flush()
            person.contact_id = contact.id


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


# ------------------------------------------------------------------ the case context


@dataclass
class Ctx:
    sheet: SheetPort
    tab: str
    report: Report
    dash: Dash
    today: date
    moment: datetime
    people: dict[str, Person]
    columns: dict[str, int]
    resolution: Resolution
    names: dict[str, str]
    email_ok: bool = False
    rng: random.Random = field(default_factory=random.Random)

    def need(self, metric: str, expected: object, got: object) -> None:
        """Check, and stop the case on a DIFF: later steps would rest on a false base."""
        if not self.report.check(metric, expected, got):
            raise StepFailed(metric)

    def cell(self, person: Person, role: str) -> str:
        return norm_value(role, self.sheet.read_cell(self.tab, person.row, self.columns[role]))

    def note(self, person: Person) -> str:
        return self.sheet.read_note(self.tab, person.row, self.columns["name"])

    def hold_rows(self) -> None:
        """Keep a running worker away from our rows; our own push ignores `next_at`."""
        sql(
            "update sync_outbox set next_at = now() + interval '1 hour' "
            "where row_key like :p and state = 'pending'",
            p=KEY_PREFIX + "%",
        )

    def open_ids(self) -> list[int]:
        rows = sql(
            "select id from sync_outbox where row_key like :p and state in ('pending','held')",
            p=KEY_PREFIX + "%",
        )
        return [int(r[0]) for r in rows]

    def push(self, label: str) -> None:
        """The worker's push of our rows into the scratch tab. Every row must end `done`."""
        ids = self.open_ids()
        stats = push_selected(
            self.sheet,
            ids,
            titles={"poc": self.tab},
            email_write_allowed=self.email_ok,
        )
        states = sql(
            "select state, coalesce(error, '') from sync_outbox "
            "where id = any(cast(:i as bigint[]))",
            i=ids,
        )
        bad = [f"{s}: {e}" for s, e in states if s != "done"]
        self.need(f"{label}: an outbox row is waiting", True, bool(ids))
        got = f"{stats.done} done" + (f" {bad}" if bad else "")
        self.need(f"{label}: push done", f"{len(ids)} done", got)

    def act(self, label: str, body: dict[str, Any], who: str = ACTOR) -> dict[str, Any]:
        response = self.dash.act(who, body)
        self.need(f"{label}: answer", 200, _answer(response))
        self.hold_rows()
        return response.json()

    def undo(self, label: str, write_id: int, who: str = ACTOR) -> dict[str, Any]:
        response = self.dash.undo(who, write_id)
        self.need(f"{label}: answer", 200, _answer(response))
        self.hold_rows()
        return response.json()

    def signed(self, label: str, person: Person, decision_id: int, *, undone: bool) -> None:
        """The D47 line is on the Name note: who, where, and the decision id."""
        note = self.note(person)
        who = self.names[ACTOR]
        mark = f"undone d{decision_id} · {who} · dashboard" if undone else f"· d{decision_id}"
        self.need(f"{label}: signature in the note", True, mark in note and who in note)


def _answer(response: Any) -> str:
    """The status, and the refusal text when there is one (it is the toast the user sees)."""
    if response.status_code == 200:
        return "200"
    return f"{response.status_code} {str(response.json().get('detail'))[:80]}"


def done_body(kind: str, contact_id: int) -> dict[str, Any]:
    return {"action": "write", "target_ids": [contact_id], "payload": {"done": kind}}


def expected_done(ctx: Ctx, kind: str) -> dict[str, str]:
    """role -> value the server must pick (D153), and a check that the column already uses it."""
    with session_scope() as session:
        conn = session.connection()
        picked = vocab.done_cells(conn, kind, ctx.today)
        known = {c["role"]: vocab.column_vocab(conn, c["role"]) for c in picked}
    out = {c["role"]: c["value"] for c in picked}
    for role, value in out.items():
        used = value in known[role] or value == ctx.today.isoformat()
        ctx.need(f"{kind}: {role} value is already used in the column", True, used)
    return out


# ------------------------------------------------------------------ case: done-undo (R5-W01)


def _single_done_undo(ctx: Ctx) -> None:
    person = ctx.people["Test Meeting A"]
    want = expected_done(ctx, "meeting")["meeting_status"]
    ctx.need("meeting: cell before Done", "Scheduled", ctx.cell(person, "meeting_status"))
    body = ctx.act("meeting Done", done_body("meeting", person.contact_id))
    item = body["items"][0]
    ctx.need("meeting: server value", want, item["value"])
    ctx.push("meeting Done")
    ctx.need("meeting: cell after Done", want, ctx.cell(person, "meeting_status"))
    ctx.signed("meeting Done", person, item["write_id"], undone=False)
    ctx.undo("meeting Undo", item["write_id"])
    ctx.push("meeting Undo")
    ctx.need("meeting: cell after Undo", "Scheduled", ctx.cell(person, "meeting_status"))
    ctx.signed("meeting Undo", person, item["write_id"], undone=True)


def _grouped_done_undo(ctx: Ctx) -> None:
    """D168: three cells in one Done; ONE Undo on one id puts all three back."""
    person = ctx.people["Test First A"]
    want = expected_done(ctx, "li_first")
    roles = list(want)
    for role in roles:
        ctx.need(f"li_first: {role} before Done", "", ctx.cell(person, role))
    body = ctx.act("li_first Done", done_body("li_first", person.contact_id))
    ctx.need("li_first: cells written", len(roles), len(body["items"]))
    ctx.push("li_first Done")
    for role in roles:
        ctx.need(f"li_first: {role} after Done", want[role], ctx.cell(person, role))
    ids = [i["write_id"] for i in body["items"]]
    ctx.signed("li_first Done", person, ids[0], undone=False)
    ctx.undo("li_first grouped Undo", ids[0])
    live = sql(
        "select count(*) from decision where id = any(cast(:i as bigint[])) and undone_at is null",
        i=ids,
    )
    ctx.need("li_first: all members undone by one click", 0, live[0][0])
    ctx.push("li_first Undo")
    for role in roles:
        ctx.need(f"li_first: {role} after Undo", "", ctx.cell(person, role))


def case_done_undo(ctx: Ctx) -> None:
    _single_done_undo(ctx)
    _grouped_done_undo(ctx)


# ------------------------------------------------------------------ case: add-email (R5-W02)


def _counts(prefix: str = KEY_PREFIX) -> tuple[int, int]:
    like = prefix + "%"
    d = sql("select count(*) from decision where row_key like :p", p=like)[0][0]
    o = sql("select count(*) from sync_outbox where row_key like :p", p=like)[0][0]
    return int(d), int(o)


def email_task_shown(ctx: Ctx, person: Person) -> bool:
    """The Add email task of this person, built by the same rules the dashboard uses."""
    from saley.intel.tasks import ContactRow, add_email_tasks
    from saley.web.queries import task_inputs

    with session_scope() as session:
        found = [
            r for r in task_inputs(session.connection()) if r["contact_id"] == person.contact_id
        ]
    rows = [ContactRow.from_row(r) for r in found]
    return bool(add_email_tasks(rows, ctx.today, True))


def case_add_email(ctx: Ctx) -> None:
    person = ctx.people["Test Email A"]
    body = {"action": "email_write", "target_ids": [person.contact_id]}
    ctx.need("task shows before", True, email_task_shown(ctx, person))
    ctx.need("email cell is blank before", "", ctx.cell(person, "email"))
    base = _counts()
    for bad in BAD_EMAILS:
        response = ctx.dash.act(ACTOR, {**body, "payload": {"role": "email", "value": bad}})
        ctx.need(f"invalid {bad!r}: refused", 422, response.status_code)
    ctx.need("invalid values: no decision, no outbox row", base, _counts())
    ctx.need("invalid values: email cell still blank", "", ctx.cell(person, "email"))
    ctx.need("invalid values: task still shows", True, email_task_shown(ctx, person))

    answer = ctx.act("valid email", {**body, "payload": {"role": "email", "value": GOOD_EMAIL}})
    item = answer["items"][0]
    ctx.need("valid email: task hidden at once", False, email_task_shown(ctx, person))
    ctx.push("valid email")
    ctx.need("valid email: cell written", GOOD_EMAIL, ctx.cell(person, "email"))
    ctx.signed("valid email", person, item["write_id"], undone=False)
    stored = sql("select email from contact where id = :i", i=person.contact_id)[0][0]
    ctx.need("valid email: database holds it", GOOD_EMAIL, stored)
    ctx.need("valid email: task gone after the push", False, email_task_shown(ctx, person))
    again = ctx.dash.act(
        ACTOR, {**body, "payload": {"role": "email", "value": "other@example.com"}}
    )
    ctx.need("a second email is refused (D154)", 409, again.status_code)


# ------------------------------------------------------------------ case: commitment-nonowner


def case_commitment_nonowner(ctx: Ctx) -> None:
    from saley.web import words as w

    owner = sql("select id, name from users where short = :s", s=ACTOR)[0]
    made = sql(
        "insert into commitment (owner, text, due, state) values (:o, :t, :d, 'open') returning id",
        o=owner[0],
        t=COMMITMENT_TEXT,
        d=ctx.today + timedelta(days=2),
    )
    cid = int(made[0][0])
    key = f"commitment:{cid}"
    before_sheet = ctx.sheet.read_header(ctx.tab, TAB_ROWS)
    decisions = sql("select count(*) from decision where row_key = :k", k=key)[0][0]
    outbox = _counts()
    response = ctx.dash.commitment_done(OTHER, cid)
    ctx.need("non-owner Done: status", 403, response.status_code)
    ctx.need(
        "non-owner Done: toast text",
        w.fill(w.ERR_NOT_OWNER, name=str(owner[1])[:30]),
        response.json().get("detail"),
    )
    ctx.need(
        "non-owner Done: no decision",
        decisions,
        sql("select count(*) from decision where row_key = :k", k=key)[0][0],
    )
    ctx.need("non-owner Done: no outbox row", outbox, _counts())
    ctx.need(
        "non-owner Done: commitment still open",
        "open",
        sql("select state from commitment where id = :i", i=cid)[0][0],
    )
    same = before_sheet == ctx.sheet.read_header(ctx.tab, TAB_ROWS)
    ctx.need("non-owner Done: sheet unchanged", "unchanged", "unchanged" if same else "changed")


# ------------------------------------------------------------------ case: parity (R5-W04)


def case_parity(ctx: Ctx, n: int, seed: int) -> None:
    ctx.rng = random.Random(seed)  # noqa: S311 - a test shuffle, not a secret
    names = ["Test Meeting A", "Test Meeting B", "Test Meeting C"]
    want = expected_done(ctx, "meeting")["meeting_status"]
    live: dict[str, int] = {}  # name -> the write id of its live Done
    until: dict[str, datetime] = {}
    steps = dones = undos = 0
    for _ in range(n):
        name = ctx.rng.choice(names)
        if name in live and (until[name] - now_ist()).total_seconds() < WINDOW_MARGIN_SECONDS:
            free = [x for x in names if x not in live]
            if not free:
                continue  # every Done is near the end of its window; leave them
            name = free[0]
        person = ctx.people[name]
        if name in live:
            ctx.undo(f"step {steps} Undo {name}", live.pop(name))
            undos += 1
        else:
            body = ctx.act(f"step {steps} Done {name}", done_body("meeting", person.contact_id))
            live[name] = body["items"][0]["write_id"]
            dones += 1
            until[name] = datetime.fromisoformat(str(body["items"][0]["undo_until"]))
        ctx.push(f"step {steps}")
        steps += 1
    ctx.report.check("steps run", n, steps)
    ctx.report.check("at least one Done and one Undo ran", "yes", "yes" if dones and undos else
        f"no: {dones} Done, {undos} Undo")  # fmt: skip
    _check_parity(ctx, names, live, want)


def _check_parity(ctx: Ctx, names: list[str], live: dict[str, int], want: str) -> None:
    api = ctx.dash.decisions(ACTOR)
    mine = sql(
        "select id, undone_at is null from decision "
        "where row_key like :p and kind = 'column_write'",
        p=KEY_PREFIX + "%",
    )
    ctx.report.check("cells compared", "at least 1", "at least 1" if names else "0")
    for name in names:
        person = ctx.people[name]
        expect = want if name in live else "Scheduled"
        sheet_value = ctx.cell(person, "meeting_status")
        db_value = sql(
            "select cells->>'meeting_status' from contact where id = :i", i=person.contact_id
        )
        ctx.report.check(f"{name}: sheet = model", expect, sheet_value)
        ctx.report.check(f"{name}: database = sheet", sheet_value, db_value[0][0])
    db_live = {int(i) for i, is_live in mine if is_live}
    api_live = {
        i for i in (int(r[0]) for r in mine) if api.get(i, {}).get("undo_state") != "undone"
    }
    ctx.report.check("live Done count: database", len(live), len(db_live))
    ctx.report.check("live Done ids: database = model", sorted(live.values()), sorted(db_live))
    ctx.report.check("live Done ids: dashboard = database", sorted(db_live), sorted(api_live))
    seen = [i for i, _ in mine if int(i) not in api]
    ctx.report.check("every decision shows on the dashboard", [], seen)
    pushed = sql(
        "select count(*) from sync_outbox where row_key like :p and state <> 'done'",
        p=KEY_PREFIX + "%",
    )
    ctx.report.check("every outbox row is done", 0, pushed[0][0])


# ------------------------------------------------------------------ run


def seed_sheet(sheet: SheetPort, tab: str, people: list[Person]) -> tuple[Resolution, int]:
    sheet.add_tab(tab, rows=TAB_ROWS, cols=len(POC_ROLES) + 1)
    sheet.write_cell(tab, MARKER_ROW, 0, MARKER)
    sheet.write_row(tab, HEADER_ROW, header_labels())
    for person in people:
        sheet.write_row(tab, person.row, sheet_row(person))
    rows = sheet.read_header(tab, rows=PERSON_ROW)
    index, resolution = find_header(rows, POC_ROLES)
    return resolution, index


def _prepare(sheet: SheetPort, tab: str, report: Report, dash: Dash, moment: datetime) -> Ctx:
    today = moment.date()
    people = [Person(seed, PERSON_ROW + i) for i, seed in enumerate(seeds(today))]
    seed_database(people, uuid.uuid4().hex[:8])
    resolution, index = seed_sheet(sheet, tab, people)
    report.wrote = True
    report.check("header row found", HEADER_ROW, index + 1)
    report.check("locked columns mapped", True, resolution.writes_allowed)
    names = {r[0]: r[1] for r in sql("select short, name from users")}
    return Ctx(
        sheet, tab, report, dash, today, moment,
        {p.name: p for p in people}, dict(resolution.columns), resolution, names,
    )  # fmt: skip


def run_case(
    sheet: SheetPort,
    tab: str,
    case: str,
    *,
    n: int = DEFAULT_N,
    seed: int = DEFAULT_SEED,
    pace: float = PACE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    dash_factory: Callable[[], Any] = dashboard,
    moment: datetime | None = None,
) -> Report:
    """Run one case in the scratch tab. The tab and the database rows go in `finally`."""
    if case not in CASES:
        raise ValueError(f"unknown case {case!r}")
    report = Report()
    guarded = GuardedSheet(sheet, tab, pace=pace, sleep=sleep)
    settings = get_settings()
    old_email, old_auth = settings.email_write_allowed, settings.auth_provider
    created = False
    try:
        clean_database()  # a leftover of a crashed run
        claim_tab(guarded, tab, report)
        created = True
        if not settings.is_dev:
            raise NotConfigured("the cases sign in with the dev mock: SALEY_ENV must be dev")
        if case == "add-email":
            settings.email_write_allowed = True  # D165: this process only, put back below
        settings.auth_provider = "mock"  # D116: this process only; a TestClient on loopback
        with dash_factory() as dash:
            ctx = _prepare(guarded, tab, report, dash, moment or now_ist())
            ctx.email_ok = case == "add-email"
            if case == "done-undo":
                case_done_undo(ctx)
            elif case == "add-email":
                case_add_email(ctx)
            elif case == "commitment-nonowner":
                case_commitment_nonowner(ctx)
            else:
                case_parity(ctx, n, seed)
    except TabNotOurs as exc:
        created = False
        report.check("scratch tab is free", "yes", str(exc))
    except StepFailed:
        pass  # the DIFF line is in the report
    except NotConfigured:
        raise
    except Exception as exc:
        if permission_refused(exc) and not report.wrote:
            raise NotConfigured(
                "Google refused the write (403): the service account needs Editor"
            ) from exc
        report.check("every step runs", "no error", f"{type(exc).__name__}: {scrub_text(str(exc))}")
    finally:
        settings.email_write_allowed, settings.auth_provider = old_email, old_auth
        _finish(guarded, tab, report, created)
    return report


def _finish(sheet: SheetPort, tab: str, report: Report, created: bool) -> None:
    """Always: remove our database rows, then the scratch tab, even on KeyboardInterrupt.

    Why nested try/finally: the tab delete must run when the database cleanup raises anything,
    a BaseException included. The first error is re-raised after both steps ran.
    """
    try:
        try:
            left = clean_database()
            report.check("test rows removed from the database", 0, left)
        except Exception as exc:
            report.check("test rows removed from the database", 0, f"{type(exc).__name__}")
    finally:
        if created:
            _tear_down(sheet, tab, report)
