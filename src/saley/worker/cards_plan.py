"""`cards_plan`: one Discord card for each dashboard notification (docs/09 §5, docs/17 §5, D259).

Why the worker: it owns every decision (when a card is due, what it says, whom it mentions) and
holds the rules the dashboard uses (tasks, vocab, suggestions). The bot only delivers. Each
producer reads the same rows as a dashboard section and queues a card per item with a stable
`card_id`, so a second run queues nothing twice (`bot_post.card_id` is unique). The caps
(`bot_plan`) then decide when each card goes. Card text comes from `bot/cards/`, values from
rows only.

Producers and their dashboard source:
- M5 meeting brief and M6 after the meeting: `v_contact_stage.meeting_date`.
- M7 reconnect: the Today reconnect task (`intel.tasks.reconnect_tasks`); Done is the same
  write as the dashboard's Done (task_done with the due day as cycle, D48 key).
- M8 and M9: `reminder` rows (with a commitment: M8; without: M9). A reminder is marked sent.
- M10: open proposals (the Inbox one-click cards, D24).
- M2: linked signals rated LOOK_MIN or more (the Today rule), or on a P1 person, per owner
  and day.
- M3 (Tue, Fri), M4 and M11 (Wed): Suggestions (warm), Events, accounts new this week.
- M14: open `ask` rows.
The R4 shapes queue their cards where they are built: M1 (`bot/brief.py`), M12/M13
(`intel/pack.py`), M15 (`bot/health.py`).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import cards, links, queue
from saley.bot import words as bw
from saley.core import clock
from saley.core.logging import get_logger
from saley.core.settings import get_settings
from saley.intel import suggest, tasks
from saley.web import signals as web_signals
from saley.web import words as w
from saley.web.act import instruction_key
from saley.web.today_model import checked_line

log = get_logger("saley.worker.cards_plan")

LIST_TAKE = 25  # rows read for a list card; the card shows BOT_LIST_MAX, the thread the rest
# docs/09 M2 says "linked signal >= 7"; importance runs 1-5, so the card uses the dashboard rule
# (Today needs a look: rated 4 or 5, web/signals.py) or a P1 person (Q-R7-1).
LOOK_MIN = 4
LOOK_HOURS = 24
MEETING_BACK_DAYS = 3  # M6 asks about meetings of the last 3 days only
EVENTS_DAYS = 21
STATUS_VALUES = 8  # M6 offers at most this many Meeting Status values from the sheet
NO_SHOW = re.compile(r"no.?show", re.IGNORECASE)
TUESDAY, WEDNESDAY, FRIDAY = 1, 2, 4
SUGGEST_DAYS = (TUESDAY, FRIDAY)
WEEKLY_DAY = WEDNESDAY
TOUCH_LABELS = {
    "email1": "Email 1",
    "email2": "Email 2",
    "email3": "Email 3",
    "li_dm": "LinkedIn note",
    "reconnect": "Reconnect",
    "status_check": "Status check",
}


@dataclass
class PlanStats:
    queued: dict[str, int] = field(default_factory=dict)

    def note(self) -> str:
        if not self.queued:
            return "no new cards"
        return " · ".join(f"{t} {n}" for t, n in sorted(self.queued.items()))


@dataclass(frozen=True)
class Person:
    id: int
    short: str
    first: str


def team(session: Session) -> dict[str, Person]:
    rows = session.execute(text("select id, short, name from users")).all()
    return {r.short: Person(r.id, r.short, (r.name or r.short).split()[0]) for r in rows}


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=clock.tz())


def _short_day(day: date) -> str:
    return f"{day.day} {day:%b}"


def _owner(people: dict[str, Person], short: str | None, default: str) -> Person | None:
    return people.get(short or "") or people.get(default)


def _queue(
    session: Session,
    stats: PlanStats,
    card_type: str,
    data: dict[str, Any],
    card_id: str,
    *,
    due_at: datetime,
    to: Sequence[Person] = (),
    **kw: Any,
) -> None:
    payload = cards.render(card_type, data, card_id)
    if queue.insert_card(session, payload, due_at=due_at, mentions=[p.id for p in to], **kw):
        stats.queued[card_type] = stats.queued.get(card_type, 0) + 1


def _exists(session: Session, card_id: str) -> bool:
    found = session.execute(text("select 1 from bot_post where card_id = :c"), {"c": card_id})
    return found.first() is not None


# ------------------------------------------------------------------ M5 and M6 (meetings)


def _touches(session: Session, contact_id: int) -> list[dict[str, str]]:
    rows = session.execute(
        text(
            "select type, date from touch where contact_id = :c and date is not null "
            "order by date desc, id desc limit 3"
        ),
        {"c": contact_id},
    ).all()
    return [
        {
            "label": TOUCH_LABELS.get(r.type, r.type.replace("_", " ").capitalize()),
            "day": _short_day(r.date),
        }
        for r in reversed(rows)
    ]


def _risk(row: Any) -> str | None:
    """One rule-based risk, from the row (never a guess)."""
    cells = row.cells or {}
    if not row.has_email:
        return bw.RISK_NO_EMAIL
    if not str(cells.get("package") or "").strip():
        return bw.RISK_NO_PACKAGE
    if not str(cells.get("deal_size") or "").strip():
        return bw.RISK_NO_DEAL
    return None


MEETING_SQL = (
    "select s.id, s.sheet_row_key, s.name, s.title, s.company, s.account_id, s.stage, s.owner, "
    "s.has_email, s.meeting_date, s.meeting_status, s.next_step, c.cells, "
    "(select w.text from v_fact_working_on w where w.entity_type = 'contact' "
    " and w.entity_id = s.id order by w.observed_at desc limit 1) as working_on "
    "from v_contact_stage s join contact c on c.id = s.id "
    "where s.meeting_date between :d0 and :d1 "
    "and coalesce(lower(s.meeting_status), '') not like 'cancel%' order by s.meeting_date, s.id"
)


def meeting_briefs(
    session: Session, now: datetime, people: dict[str, Person], stats: PlanStats
) -> None:
    """M5: 24 h before Meeting Date (a date: the brief is due the day before, at 00:00)."""
    today = now.date()
    for r in session.execute(text(MEETING_SQL), {"d0": today, "d1": today + timedelta(days=1)}):
        if (r.meeting_status or "").strip().casefold().startswith("complete"):
            continue
        owner = _owner(people, r.owner, "vaishnavi")
        package = str((r.cells or {}).get("package") or "").strip() or None
        data = {
            "who": owner.first if owner else "team",
            "name": r.name,
            "company": r.company or r.name,
            "title": r.title,
            "stage": r.stage,
            "day": clock.day_label(r.meeting_date),
            "contact_id": r.id,
            "thumbnail": None,
            "working_on": r.working_on,
            "touches": _touches(session, r.id),
            "package": package,
            "questions": [],
            "risk": _risk(r),
        }
        card_id = f"m5-c{r.id}-{r.meeting_date:%Y%m%d}"
        _queue(
            session,
            stats,
            "M5",
            data,
            card_id,
            due_at=_midnight(r.meeting_date) - timedelta(days=1),
            to=[owner] if owner else [],
            is_meeting=True,
            item=("contact", r.id),
        )


def _statuses(session: Session) -> list[str]:
    rows = session.execute(
        text(
            "select value from v_column_vocab where role = 'meeting_status' "
            "order by n desc, value limit :n"
        ),
        {"n": STATUS_VALUES},
    ).scalars()
    return [str(v).strip() for v in rows if str(v).strip()]


def after_meetings(
    session: Session, now: datetime, people: dict[str, Person], stats: PlanStats
) -> None:
    """M6: "How did it go?" the day after the meeting, + AFTER_MEETING_HOURS (Q-R7-1).

    Asked only while the sheet has no next step for the person."""
    settings = get_settings()
    today = now.date()
    statuses = _statuses(session)
    if not statuses:
        return  # the column holds no value we could offer: never invent one (D153)
    no_show = next((s for s in statuses if NO_SHOW.search(s)), None)
    window = {"d0": today - timedelta(days=MEETING_BACK_DAYS), "d1": today}
    for r in session.execute(text(MEETING_SQL), window):
        if (r.next_step or "").strip():
            continue
        due = _midnight(r.meeting_date + timedelta(days=1)) + timedelta(
            hours=settings.after_meeting_hours
        )
        owner = _owner(people, r.owner, "vaishnavi")
        data = {
            "who": owner.first if owner else "team",
            "name": r.name,
            "company": r.company or r.name,
            "contact_id": r.id,
            "row_key": r.sheet_row_key,
            "statuses": statuses,
            "no_show": no_show,
        }
        _queue(
            session,
            stats,
            "M6",
            data,
            f"m6-c{r.id}-{r.meeting_date:%Y%m%d}",
            due_at=due,
            to=[owner] if owner else [],
            item=("contact", r.id),
        )


# ------------------------------------------------------------------ M7 reconnect


def reconnects(
    session: Session, now: datetime, people: dict[str, Person], stats: PlanStats
) -> None:
    """M7: the Today reconnect tasks. Vaishnavi takes reconnects (docs/09 §2)."""
    rows = [
        tasks.ContactRow.from_row(r)
        for r in session.execute(text("select * from v_task_inputs where reconnect_due")).mappings()
    ]
    by_id = {r.contact_id: r for r in rows}
    owner = people.get("vaishnavi")
    for task in tasks.reconnect_tasks(rows):
        row, cycle = by_id[task.contact_id], task.due
        payload = {"task": "reconnect"}
        key = instruction_key(
            "task_done", [row.contact_id], [], "reconnect", cycle.isoformat() if cycle else ""
        )
        data = {
            "who": owner.first if owner else "team",
            "name": row.name,
            "company": row.company or row.name,
            "contact_id": row.contact_id,
            "last_touch": _short_day(row.last_step_date) if row.last_step_date else None,
            "reason": _reason(session, row.contact_id),
            "done": {
                "action": "task_done",
                "targets": [row.contact_id],
                "payload": payload,
                "key": key,
                "role": "task:reconnect",
                "row_key": _row_key(session, row.contact_id),
            },
        }
        card_id = f"m7-c{row.contact_id}-{(cycle or now.date()):%Y%m%d}"
        _queue(
            session,
            stats,
            "M7",
            data,
            card_id,
            due_at=now,
            to=[owner] if owner else [],
            is_reconnect=True,
            item=("contact", row.contact_id),
        )


def _reason(session: Session, contact_id: int) -> str | None:
    return session.execute(
        text(
            "select text from v_fact_working_on where entity_type = 'contact' "
            "and entity_id = :c order by observed_at desc limit 1"
        ),
        {"c": contact_id},
    ).scalar()


def _row_key(session: Session, contact_id: int) -> str:
    return session.execute(
        text("select sheet_row_key from contact where id = :c"), {"c": contact_id}
    ).scalar_one()


# ------------------------------------------------------------------ M8, M9 reminders


def reminders(session: Session, now: datetime, people: dict[str, Person], stats: PlanStats) -> None:
    """M8 (a reminder of a commitment) and M9 (a reminder a person set). Marked sent here."""
    by_id = {p.id: p for p in people.values()}
    rows = session.execute(
        text(
            "select r.id, r.for_user, r.text, r.commitment_id, c.text as c_text, c.due "
            "from reminder r left join commitment c on c.id = r.commitment_id "
            "where r.state = 'pending' and r.fire_at <= :now "
            "and (r.commitment_id is null or c.state = 'open') "
            "order by r.fire_at, r.id for update of r"
        ),
        {"now": now},
    ).all()
    for r in rows:
        person = by_id.get(r.for_user)
        who = person.first if person else "team"
        if r.commitment_id is not None:
            data = {
                "who": who,
                "commitment_id": r.commitment_id,
                "text": r.c_text,
                "day": clock.day_label(r.due) if r.due else None,
            }
            _queue(
                session,
                stats,
                "M8",
                data,
                f"m8-r{r.id}",
                due_at=now,
                to=[person] if person else [],
                item=("commitment", r.commitment_id),
            )
        else:
            data = {"who": who, "reminder_id": r.id, "text": r.text}
            _queue(
                session,
                stats,
                "M9",
                data,
                f"m9-r{r.id}",
                due_at=now,
                to=[person] if person else [],
                item=("reminder", r.id),
            )
        session.execute(text("update reminder set state = 'sent' where id = :i"), {"i": r.id})


# ------------------------------------------------------------------ M10 Saley's ideas


def ideas(session: Session, now: datetime, people: dict[str, Person], stats: PlanStats) -> None:
    """M10: every open proposal, once (D24). Sid for first touches (LinkedIn), else the owner."""
    rows = session.execute(
        text(
            "select p.id, p.type, p.payload, c.owner from proposal p "
            "left join contact c on c.id = p.entity_id "
            "where p.state = 'open' "
            "and not exists (select 1 from bot_post b where b.card_id = 'm10-p' || p.id) "
            "order by p.rank desc, p.id"
        )
    ).all()
    for r in rows:
        pay = r.payload or {}
        cell = (pay.get("cells") or [{}])[0]
        person = (
            people.get("sid") if r.type == "li_status" else _owner(people, r.owner, "vaishnavi")
        )
        label = w.ROLE_LABELS.get(str(cell.get("role")), str(cell.get("role") or ""))
        data = {
            "who": person.first if person else "team",
            "proposal_id": r.id,
            "type": r.type,
            "name": pay.get("name") or "",
            "company": pay.get("company") or "",
            "contact_id": pay.get("contact_id"),
            "writes": f"{label} = {cell.get('value')}" if cell.get("value") else None,
        }
        _queue(
            session,
            stats,
            "M10",
            data,
            f"m10-p{r.id}",
            due_at=now,
            to=[person] if person else [],
            item=("proposal", r.id),
        )


# ------------------------------------------------------------------ M2 needs a look


LOOK_SQL = (
    "select l.story_id, l.title, l.why, l.source_name, l.entity_type, l.entity_id, "
    "coalesce(l.account_name, l.entity_name) as company, l.entity_name, "
    "coalesce(l.published_at, l.first_seen_at) as at, l.importance, "
    "coalesce((select bool_or(s.is_p1) from v_contact_stage s where "
    "  (l.entity_type = 'contact' and s.id = l.entity_id) or "
    "  (l.entity_type = 'account' and s.account_id = l.entity_id)), false) as p1, "
    "(select array_agg(distinct s.owner) from v_contact_stage s where s.owner is not null and "
    "  ((l.entity_type = 'contact' and s.id = l.entity_id) or "
    "   (l.entity_type = 'account' and s.account_id = l.entity_id))) as owners "
    "from v_signal_linked l where l.created_at >= :since "
    "and not exists (select 1 from bot_post b where b.card_type = 'M2' "
    "  and b.payload -> 'acts' -> 'seen' -> 'args' -> 'ids' @> to_jsonb(l.story_id)) "
    "order by l.importance desc, l.story_id limit :n"
)


def needs_a_look(
    session: Session, now: datetime, people: dict[str, Person], stats: PlanStats
) -> None:
    """M2: one card per owner and day with the stories that matter to them."""
    since = now - timedelta(hours=LOOK_HOURS)
    per_owner: dict[str, dict[int, dict[str, Any]]] = {}
    for r in session.execute(text(LOOK_SQL), {"since": since, "n": LIST_TAKE * 4}):
        if (r.importance or 0) < LOOK_MIN and not r.p1:
            continue
        owners = [o for o in (r.owners or []) if o in people] or ["sid", "vaishnavi"]
        story = {
            "story_id": r.story_id,
            "title": r.title,
            "company": r.company or "",
            "why": r.why or "",
            "source": r.source_name or "",
            "day": _short_day(r.at.astimezone(clock.tz()).date()),
            "thumbnail": None,
            "entity": (r.entity_type, r.entity_id, r.entity_name),
        }
        for o in owners:
            per_owner.setdefault(o, {}).setdefault(r.story_id, story)
    for short, found in per_owner.items():
        person = people.get(short)
        stories = list(found.values())[:LIST_TAKE]
        if not stories or person is None:
            continue
        card_id = f"m2-{short}-{now:%Y%m%d}"
        kind, eid, ename = stories[0]["entity"]
        page = links.url("people" if kind == "contact" else "accounts", eid, card_id)
        data = {"who": person.first, "stories": stories, "page_label": ename, "page_url": page}
        _queue(session, stats, "M2", data, card_id, due_at=now, to=[person])


# ------------------------------------------------------------------ weekly and twice-weekly cards


def _at(now: datetime, setting: str, days: Sequence[int]) -> bool:
    """True on the given weekdays once the IST time in `setting` has come."""
    return now.weekday() in days and now.time() >= get_settings().clock_time(setting)


def suggestions(
    session: Session, now: datetime, people: dict[str, Person], stats: PlanStats
) -> None:
    """M3 (Tue, Fri): the best warm people to reach. Do it = a commitment of the presser."""
    if not _at(now, get_settings().suggestions_time, SUGGEST_DAYS):
        return
    card_id = f"m3-{now:%Y%m%d}"
    if _exists(session, card_id):
        return
    found = session.execute(
        text(
            "select kind, ref_id, account_id, contact_id, name, company, title, parts, evidence "
            "from v_suggest_warm order by ref_id limit 500"
        )
    ).mappings()
    seen = {
        r
        for (r,) in session.execute(
            text("select item_id from user_seen where item_type = 'suggest_warm'")
        )
    }
    picked = []
    for s in suggest.rank("warm", found):
        if s.ref_id in seen or len(picked) >= LIST_TAKE:
            continue
        item = web_signals.suggestion_item(s, set(), {})
        line = checked_line(item)
        if line is None or not item["reason"]:
            continue
        picked.append(
            {
                "proposal_id": s.ref_id,
                "name": s.name,
                "company": s.company or s.name,
                "reason": item["reason"],
                "step": line,
            }
        )
    if not picked:
        return
    to = [p for p in (people.get("sid"), people.get("vaishnavi")) if p]
    data = {"who": " and ".join(p.first for p in to) or "team", "people": picked}
    _queue(session, stats, "M3", data, card_id, due_at=now, to=to)


def events(session: Session, now: datetime, people: dict[str, Person], stats: PlanStats) -> None:
    """M4 (Wed): the events of the next three weeks, registered first."""
    if not _at(now, get_settings().events_time, (WEEKLY_DAY,)):
        return
    rows = session.execute(
        text(
            "select id, name, city, start, key_people, coalesce(registered, false) as registered "
            "from event where removed_at is null and start between :d0 and :d1 "
            "order by start, id limit :n"
        ),
        {"d0": now.date(), "d1": now.date() + timedelta(days=EVENTS_DAYS), "n": LIST_TAKE},
    ).all()
    if not rows:
        return
    data = {
        "who": " and ".join(p.first for p in _event_people(people)) or "team",
        "events": [
            {
                "event_id": r.id,
                "name": r.name,
                "city": r.city,
                "day": _short_day(r.start),
                "who_goes": r.key_people,
                "registered": bool(r.registered),
            }
            for r in rows
        ],
    }
    _queue(session, stats, "M4", data, f"m4-{now:%Y%m%d}", due_at=now, to=_event_people(people))


def _event_people(people: dict[str, Person]) -> list[Person]:
    return [people[s] for s in get_settings().event_user_shorts if s in people]


NEWCO_SQL = (
    "select a.id, a.name, a.industry, a.outreach_line, "
    "(select string_agg(c.name, ', ' order by c.name) from contact c "
    " where c.account_id = a.id and c.removed_at is null) as known "
    "from account a where a.removed_at is null and a.in_master and "
    "(select min(l.from_at) from stage_log l join contact c on c.id = l.contact_id "
    " where c.account_id = a.id) >= :week "
    "order by a.id limit :n"
)


def new_companies(
    session: Session, now: datetime, people: dict[str, Person], stats: PlanStats
) -> None:
    """M11 (Wed): accounts whose first person entered the sheet this week (stage_log)."""
    if not _at(now, get_settings().new_company_time, (WEEKLY_DAY,)):
        return
    week = _midnight(clock.week_start(now.date()))
    takers = [(s, people[s].first) for s in get_settings().writer_shorts if s in people]
    for r in session.execute(text(NEWCO_SQL), {"week": week, "n": LIST_TAKE}):
        fit = r.outreach_line or r.industry
        if not fit or not takers:
            continue
        data = {
            "account_id": r.id,
            "company": r.name,
            "fit": fit,
            "known": r.known,
            "first_step": bw.BTN_TAKE,
            "takers": takers,
        }
        _queue(
            session,
            stats,
            "M11",
            data,
            f"m11-a{r.id}",
            due_at=now,
            to=[people[s] for s, _ in takers],
            item=("account", r.id),
        )


def asks(session: Session, now: datetime, people: dict[str, Person], stats: PlanStats) -> None:
    """M14: a fact the worker needs from a person (open `ask` rows)."""
    by_id = {p.id: p for p in people.values()}
    rows = session.execute(
        text(
            "select id, to_user, question, entity_id from ask where answered_at is null "
            "order by asked_at, id limit :n"
        ),
        {"n": LIST_TAKE},
    ).all()
    for r in rows:
        person = by_id.get(r.to_user)
        data = {
            "who": person.first if person else "team",
            "ask_id": r.id,
            "question": r.question,
            "entity_type": "contact" if r.entity_id else "ask",
            "entity_id": r.entity_id or r.id,
        }
        _queue(
            session,
            stats,
            "M14",
            data,
            f"m14-k{r.id}",
            due_at=now,
            to=[person] if person else [],
            item=("ask", r.id),
        )


PRODUCERS: tuple[Callable[[Session, datetime, dict[str, Person], PlanStats], None], ...] = (
    meeting_briefs,
    after_meetings,
    reconnects,
    reminders,
    ideas,
    needs_a_look,
    suggestions,
    events,
    new_companies,
    asks,
)


def plan_cards(session: Session, now: datetime | None = None) -> PlanStats:
    """Run every producer. One producer that fails is logged; the others still run."""
    moment = (now or clock.now_ist()).astimezone(clock.tz())
    people = team(session)
    stats = PlanStats()
    failed: list[str] = []
    for producer in PRODUCERS:
        try:
            with session.begin_nested():
                producer(session, moment, people, stats)
        except Exception:
            log.exception("cards_plan_producer_failed", producer=producer.__name__)
            failed.append(producer.__name__)
    if failed:
        session.commit()  # keep the cards of the producers that worked
        raise RuntimeError(f"cards_plan: {', '.join(failed)} failed")
    return stats
