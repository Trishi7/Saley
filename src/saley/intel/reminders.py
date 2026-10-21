"""Reminders for commitments: the politeness budget (C05, S42, plan 3.7).

Why: the team asked for a polite voice. A commitment gets AT MOST two reminders: one the day
before the due day and one on the due day. After that it goes into the weekly pack
(`in_pack`) and gets no more. The text never says a task is late. In R2 a reminder shows in
the Inbox only; R4 adds Discord delivery with the bot caps and weekend silence.

`plan` is a pure function so the budget is easy to test. A commitment with no due day gets
no reminder: the day is never guessed. All reminder text comes from `web/words.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.core.clock import now_ist, today_ist
from saley.core.logging import get_logger
from saley.web import words as w

log = get_logger("saley.intel.reminders")

MAX_REMINDERS = 2  # one before the due day, one on the due day


@dataclass(frozen=True)
class Plan:
    kind: str | None  # 'before', 'due_day' or None
    in_pack: bool


def plan(
    due: date | None,
    reminders_sent: int,
    last_reminded_on: date | None,
    in_pack: bool,
    today: date,
) -> Plan:
    """What to do with one open commitment today. Never more than MAX_REMINDERS."""
    if due is None or in_pack:
        return Plan(None, in_pack)
    if last_reminded_on == today:
        return Plan(None, False)  # one reminder a day at most
    if reminders_sent >= MAX_REMINDERS or today > due:
        return Plan(None, True)  # the weekly pack carries it from here
    if today == due:
        return Plan("due_day", True)  # the last reminder: the pack takes over after it
    if today == due - timedelta(days=1) and reminders_sent == 0:
        return Plan("before", False)
    return Plan(None, False)


@dataclass
class ReminderStats:
    before: int = 0
    due_day: int = 0
    packed: int = 0

    @property
    def sent(self) -> int:
        return self.before + self.due_day

    def note(self) -> str:
        return f"{self.before} before · {self.due_day} on the day · {self.packed} to the pack"


def message(kind: str, commitment_text: str) -> str:
    template = w.REMIND_BEFORE if kind == "before" else w.REMIND_DUE_DAY
    return w.fill(template, text=commitment_text)


def run_reminders(
    session: Session, today: date | None = None, now: datetime | None = None
) -> ReminderStats:
    """Create the reminders that are due today and move finished budgets to the pack."""
    today = today or today_ist()
    now = now or now_ist()
    stats = ReminderStats()
    rows = session.execute(
        text(
            "select id, owner, text, due, reminders_sent, last_reminded_on, in_pack "
            "from commitment where state = 'open' and due is not null and not in_pack "
            "order by id for update"
        )
    ).all()
    for c in rows:
        step = plan(c.due, c.reminders_sent, c.last_reminded_on, c.in_pack, today)
        if step.kind is None and step.in_pack == c.in_pack:
            continue
        if step.kind is not None:
            session.execute(
                text(
                    "insert into reminder (for_user, text, fire_at, commitment_id, kind) "
                    "values (:u, :t, :at, :c, :k)"
                ),
                {
                    "u": c.owner,
                    "t": message(step.kind, c.text),
                    "at": now,
                    "c": c.id,
                    "k": step.kind,
                },
            )
            session.execute(
                text(
                    "update commitment set reminders_sent = reminders_sent + 1, "
                    "last_reminded_on = :d, in_pack = :p where id = :i"
                ),
                {"d": today, "p": step.in_pack, "i": c.id},
            )
            stats.before += step.kind == "before"
            stats.due_day += step.kind == "due_day"
        else:
            session.execute(
                text("update commitment set in_pack = :p where id = :i"),
                {"p": step.in_pack, "i": c.id},
            )
        stats.packed += step.in_pack
    return stats
