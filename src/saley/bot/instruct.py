"""Chat instructions: a fixed parser for the docs/09 grammar (R4 ticket 7, D24, D129, D135).

Why: an instruction from an approver IS the approval (D24). The grammar is closed and plain code:
no LLM reads the text, picks a person or sets a stage. Five forms, all in the one @Saley message:

- `remind me <when> to <text>` and `cancel reminder <n>`: a reminder for the author only.
- `note: <fact> about <person>`: a fact with its author. The person comes from an exact name
  match in `contact`; two matches or none is a question back, never a guess.
- `approve <id>` and `reject <id> because <reason>`: the id is the number on the action card.
  Both call `saley_proposal_resolve` (migration 0022) with surface 'discord': one write at once
  with a signature and an undo window. `instruction_key` (D48) is built in SQL from the cell, so a
  click on the dashboard and this message are ONE write; the second one answers "already done".

Who may do what comes from the allowlists (D135): `DISCORD_INSTRUCT_IDS` for reminders and notes,
`DISCORD_APPROVER_IDS` for approve and reject. Anyone else gets a polite refusal that names the
people by short name, and nothing is written. An author with no `users` row is not handled here:
the question path answers them with one line and no data.

The message text is DATA. It is stored as text and echoed back only inside fixed templates; the
reply never allows a mention (the gateway sets `AllowedMentions.none()`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import card, identity
from saley.bot import words as bw
from saley.core import clock
from saley.core.logging import get_logger
from saley.core.settings import Settings
from saley.core.text import norm_name

log = get_logger("saley.bot.instruct")

REMIND_HOUR = 10  # a reminder for a day, with no hour, fires at 10:00 IST
TODAY_HOUR = 17  # "today" means the end of the working day
MAX_REMINDER = 200
MAX_FACT = 300
MAX_REASON = 300
MAX_DAYS = 365
MAX_HOURS = 72
MAX_PENDING = 20  # reminders waiting per person: the politeness budget stays small
NOTE_CONFIDENCE = 3  # a teammate said it
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
FLAGS = re.IGNORECASE

REMIND = re.compile(r"remind me (?P<when>.+?) to (?P<text>.+)", FLAGS)
CANCEL = re.compile(r"cancel reminder (?P<n>[0-9]{1,15})", FLAGS)
NOTE = re.compile(r"note:\s*(?P<fact>.+)\s+about\s+(?P<person>.+)", FLAGS)
APPROVE = re.compile(r"approve (?P<n>[0-9]{1,15})", FLAGS)
REJECT = re.compile(r"reject (?P<n>[0-9]{1,15})(?:\s+because\s+(?P<reason>.+))?", FLAGS)
IN_N = re.compile(r"in (?P<n>[0-9]{1,3}) (?P<unit>day|days|hour|hours)", FLAGS)
ISO_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
WEEKDAY = re.compile(rf"(?P<way>on |next )?(?P<day>{'|'.join(WEEKDAYS)})", FLAGS)
NEAR_MISS = re.compile(r"\s*(?P<word>approve|reject|remind|cancel|note:)", FLAGS)
HINTS = {
    "approve": bw.HINT_APPROVE,
    "reject": bw.HINT_REJECT,
    "remind": bw.HINT_REMIND,
    "cancel": bw.HINT_CANCEL,
    "note:": bw.HINT_NOTE,
}
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MENTION = re.compile(r"<[@#&!:]|@everyone|@here", FLAGS)


@dataclass(frozen=True)
class Instruction:
    kind: str  # remind, cancel, note, approve or reject
    n: int = 0  # a reminder id or a card id
    when: str = ""
    body: str = ""  # reminder text, note fact, or reject reason
    person: str = ""

    @property
    def needs_approver(self) -> bool:
        return self.kind in ("approve", "reject")


def parse(raw: str) -> Instruction | None:
    """The instruction in one message, or None when it is not one of the five forms."""
    line = raw.strip()
    if not line or "\n" in line:
        return None
    if found := CANCEL.fullmatch(line):
        return Instruction("cancel", n=int(found["n"]))
    if found := APPROVE.fullmatch(line):
        return Instruction("approve", n=int(found["n"]))
    if found := REJECT.fullmatch(line):
        return Instruction("reject", n=int(found["n"]), body=(found["reason"] or "").strip())
    if found := REMIND.fullmatch(line):
        return Instruction("remind", when=found["when"].strip(), body=found["text"].strip())
    if found := NOTE.fullmatch(line):
        return Instruction(
            "note", body=found["fact"].strip(), person=found["person"].strip().rstrip(".")
        )
    return None


def _at(day: datetime, hour: int) -> datetime:
    return day.replace(hour=hour, minute=0, second=0, microsecond=0)


def parse_when(raw: str, now: datetime) -> datetime | None:
    """Fixed forms only: today, tomorrow, a weekday, in N days or hours, or an ISO date.

    `friday` is the next Friday after today; `next friday` is the Friday of the week after.

    A day with no hour fires at 10:00 IST. Anything else is None: the time is never guessed.
    """
    word = " ".join(raw.casefold().split())
    if word == "today":
        return _at(now, TODAY_HOUR)
    if word == "tomorrow":
        return _at(now + timedelta(days=1), REMIND_HOUR)
    if found := WEEKDAY.fullmatch(word):
        index = WEEKDAYS.index(found["day"])
        if (found["way"] or "").strip() == "next":  # "next friday" is that day in the week after
            return _at(now + timedelta(days=7 - now.weekday() + index), REMIND_HOUR)
        return _at(now + timedelta(days=(index - now.weekday()) % 7 or 7), REMIND_HOUR)
    if found := IN_N.fullmatch(word):
        count = int(found["n"])
        if found["unit"].startswith("day"):
            return _at(now + timedelta(days=count), REMIND_HOUR) if 0 < count <= MAX_DAYS else None
        return now + timedelta(hours=count) if 0 < count <= MAX_HOURS else None
    if ISO_DAY.fullmatch(word):
        try:
            day = datetime.fromisoformat(word).replace(tzinfo=now.tzinfo)
        except ValueError:
            return None
        if (day.date() - now.date()).days > MAX_DAYS:
            return None
        return _at(day, REMIND_HOUR)
    return None


def near_miss(raw: str) -> str | None:
    """The key of the hint when a message starts like an instruction but is not one."""
    found = NEAR_MISS.match(raw)
    return found["word"].casefold() if found else None


def _safe(value: str, limit: int) -> str | None:
    """None when the text may be kept; else the reply words that say why not."""
    if CONTROL.search(value) or MENTION.search(value):
        return bw.TEXT_MENTION
    return bw.TEXT_LONG if len(value) > limit else None


# ------------------------------------------------------------------ reminders and notes


def _remind(session: Session, short: str, item: Instruction, now: datetime) -> str:
    """A reminder for the author. The reply names the day and the earliest time, never an exact
    send time: the bot caps (D132) may move it, so a promise of "10:00" could be wrong."""
    refusal = _safe(item.body, MAX_REMINDER)
    if refusal:
        return refusal
    fire = parse_when(item.when, now)
    if fire is None:
        return bw.REMIND_BAD_WHEN
    if fire <= now:
        return bw.REMIND_PAST
    waiting = session.execute(
        text(
            "select count(*) from reminder where state = 'pending' "
            "and for_user = (select id from users where short = :s)"
        ),
        {"s": short},
    ).scalar_one()
    if waiting >= MAX_PENDING:
        return bw.REMIND_TOO_MANY.format(n=waiting)
    new_id = session.execute(
        text(
            "insert into reminder (for_user, text, fire_at, kind, state) "
            "select u.id, :t, :f, 'due_day', 'pending' from users u where u.short = :s "
            "and not exists (select 1 from reminder r where r.for_user = u.id "
            "and r.text = :t and r.fire_at = :f and r.state = 'pending') returning id"
        ),
        {"t": item.body, "f": fire, "s": short},
    ).scalar_one_or_none()
    session.commit()
    if new_id is None:
        return bw.REMIND_DUPLICATE
    log.info("reminder_set", person=short, reminder=new_id)
    return bw.REMIND_SET.format(n=new_id, day=clock.day_label(fire), time=f"{fire:%H:%M}")


def _cancel(session: Session, short: str, item: Instruction) -> str:
    """Only the author's own pending reminder; another person's id is "not found"."""
    done = session.execute(
        text(
            "update reminder set state = 'skipped' where id = :i and state = 'pending' "
            "and for_user = (select id from users where short = :s) returning id"
        ),
        {"i": item.n, "s": short},
    ).first()
    session.commit()
    if done is None:
        return bw.REMIND_NONE.format(n=item.n)
    log.info("reminder_cancelled", person=short, reminder=item.n)
    return bw.REMIND_CANCELLED.format(n=item.n)


def _note(session: Session, short: str, item: Instruction, now: datetime) -> str:
    """A fact with its author. The person is an exact name match; two or none is not a guess.

    The reply names the contact's company, so a wrong match shows at once. The same note for the
    same person on the same day is a duplicate, not a second fact.
    """
    refusal = _safe(item.body, MAX_FACT) or _safe(item.person, 100)
    if refusal:
        return refusal
    rows = session.execute(
        text(
            "select c.id, c.name, a.name as company from contact c "
            "left join account a on a.id = c.account_id "
            "where c.name_key = :k and c.removed_at is null order by c.id"
        ),
        {"k": norm_name(item.person)},
    ).all()
    if not rows:
        return bw.NOTE_NO_PERSON.format(who=item.person)
    if len(rows) > 1:
        return bw.NOTE_MANY.format(who=item.person)
    person = rows[0]
    saved = session.execute(
        text(
            "insert into fact (entity_type, entity_id, kind, value, source_type, source_ref, "
            "author, observed_at, confidence) "
            "select 'contact', :c, 'note', cast(:v as jsonb), 'teammate', 'discord', :a, :t, :q "
            "where not exists (select 1 from fact where entity_type = 'contact' "
            "and entity_id = :c and kind = 'note' and value ->> 'text' = :b "
            "and (observed_at at time zone 'Asia/Kolkata')::date = :day) returning id"
        ),
        {
            "c": person.id,
            "v": json.dumps({"text": item.body}),
            "b": item.body,
            "a": short,
            "t": now,
            "q": NOTE_CONFIDENCE,
            "day": now.astimezone(clock.tz()).date(),
        },
    ).first()
    session.commit()
    if saved is None:
        return bw.NOTE_DUPLICATE.format(who=person.name)
    log.info("note_saved", person=short, contact=person.id)
    return bw.NOTE_SAVED.format(who=person.name, company=person.company or "?")


# ------------------------------------------------------------------ the entry point


def instruct_reply(
    session: Session,
    author_id: int,
    raw: str,
    settings: Settings,
    now: datetime | None = None,
) -> str | None:
    """The reply to an instruction, or None when the message is not one (or the author is unknown).

    None sends the message on to the question engine, which gives an unknown author one line and
    no data (D135). A known author who may not do the thing gets a refusal and nothing is written.
    A message that starts like an instruction but breaks the grammar gets a one-line hint of the
    right form and does not fall through to the question engine.
    """
    item = parse(raw)
    word = near_miss(raw) if item is None else None
    if item is None and word is None:
        return None
    who = identity.identify(session, author_id, settings)
    if who.short is None:
        return None
    needs_approver = item.needs_approver if item else word in ("approve", "reject")
    if needs_approver and not who.can_approve:
        return bw.NOT_APPROVER.format(names=card.approver_names(session, settings))
    if not needs_approver and not who.can_instruct:
        return bw.NOT_INSTRUCTOR.format(names=card.instructor_names(session, settings))
    if item is None:
        return HINTS[str(word)]
    when = now or clock.now_ist()
    if item.kind == "remind":
        return _remind(session, who.short, item, when)
    if item.kind == "cancel":
        return _cancel(session, who.short, item)
    if item.kind == "note":
        return _note(session, who.short, item, when)
    return _resolve(session, settings, author_id, item)


def _resolve(session: Session, settings: Settings, author_id: int, item: Instruction) -> str:
    """approve and reject: the card door does the rest (allowlist, lock, signature)."""
    if item.kind == "reject":
        if not item.body:
            return bw.NEED_REASON.format(n=item.n)
        refusal = _safe(item.body, MAX_REASON)
        if refusal:
            return refusal
    choice = "do" if item.kind == "approve" else "skip"
    return card.resolve_card(
        session, settings, author_id, item.n, choice, reason=item.body or None
    ).text
