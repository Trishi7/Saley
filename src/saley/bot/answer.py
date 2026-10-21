"""@Saley answers: one engine for Discord and the dashboard Ask card (R4 ticket 6, D131).

Why plain code: the LLM never picks a person or writes a fact into an answer. A fixed parser
turns a question into one of five lookups (docs/09 grammar); SQL on the `v_*` views builds the
rows; a template writes each line. Every line ends with a citation by NAME (D131), never a row
or id number. Before the text leaves, `intel.guard` checks it against the row values, so no
number, date, name, URL or email appears that the rows do not hold.

Seen is per person (D75). The seen filter runs in SQL, so only rows the person has not seen
are read: 5 are shown and marked seen with `saley_mark_seen`; the rest are counted ("M more");
the ones the person saw before are counted too ("K left out as seen by you"). Both counts are
exact. `more` repeats the last question of that person, which now skips the first 5. Every
exchange is logged with `saley_ask_log` BEFORE the items are marked seen. The footer is the
last line. The whole answer fits one Discord message (`ANSWER_MAX`).

The memory for `more` is per process and per surface: one bot process and one web worker run
today, so each keeps its own. A restart forgets it; `more` then asks for a question first.

The engine reads through one connection and writes through a `write(sql, params)` callable, so
the web role (read-only connection + SECURITY DEFINER calls) and the bot role use the same
code. Answers are never capped (D132): the caps apply to `bot_post` only.

Who may ask (D141): every Discord question needs the team allowlist, DISCORD_INSTRUCT_IDS. A
known user outside it gets one fixed line, with no data, no log and nothing marked seen. This
covers contact notes and facts, and also news and the asker's tasks: one simple rule is safer
than a list of questions. The dashboard path (`web/ask.py`) does not repeat the check: a
signed-in user already passed WEB_ALLOWED_EMAILS, which is the team. A fact that a teammate
heard from an outside person has low confidence (D108); its line ends with "(they said)".
"""

from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from saley.bot import identity
from saley.bot import words as bw
from saley.core import clock
from saley.core.logging import get_logger
from saley.core.settings import Settings
from saley.intel import guard

log = get_logger("saley.bot.answer")

PAGE = 5  # items in one answer; `more` shows the next 5
MAX_QUESTION = 300
DETAIL_MAX = 140
TITLE_MAX = 100
NAME_MAX = 60
COMPANY_MAX = 60
KIND_MAX = 30
STAGE_MAX = 30
_LIMITS = (None, NAME_MAX, DETAIL_MAX, TITLE_MAX, KIND_MAX)
LAST_KEPT = 256  # people whose last question `more` remembers
LOG_LEFT_MAX = 100_000  # saley_ask_log refuses a larger left_out
DISCORD_MAX = 1990  # one Discord message, with room under its 2000 limit
TEST_TAG = "[TEST] "  # the gateway adds this in test mode
ANSWER_MAX = DISCORD_MAX - len(TEST_TAG)
Writer = Callable[[str, dict[str, Any]], Any]

# The five lookups. Seen types: news shares `story` with the dashboard (D75); the lists of
# people and tasks have one type for each question, so one list never hides another.
SEEN_WHO, SEEN_WORKING, SEEN_P1 = "answer_who", "answer_working", "answer_p1"
SEEN_MEETING, SEEN_TASK, SEEN_STORY = "answer_due_meeting", "answer_due_task", "story"

_WHO = re.compile(
    r"who (?:at|in|from) (?P<company>.{1,80}?) (?:works?|is working|are working) on "
    r"(?P<topic>.{1,80})",
    re.IGNORECASE,
)
_NEWS = re.compile(r"(?:what(?: is|'s) the )?news since (?P<day>.{1,30})", re.IGNORECASE)
_WORKING = re.compile(r"what(?: is|'s) (?P<person>.{1,80}?) working on", re.IGNORECASE)
_P1 = re.compile(r"which p1s? (?:have|has) no e-?mail(?: address)?", re.IGNORECASE)
_DUE = re.compile(r"what(?: is|'s) due this week", re.IGNORECASE)
_MORE = re.compile(r"more", re.IGNORECASE)
_DAY_MONTH = re.compile(r"(\d{1,2})(?:st|nd|rd|th)? ([a-z]{3,9})(?: (\d{4}))?")
_MONTH_DAY = re.compile(r"([a-z]{3,9}) (\d{1,2})(?:st|nd|rd|th)?(?: (\d{4}))?")
_ISO_DAY = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_WEEKDAYS = {
    name: number
    for number, names in enumerate(
        [
            ("monday", "mon"),
            ("tuesday", "tue", "tues"),
            ("wednesday", "wed"),
            ("thursday", "thu", "thur", "thurs"),
            ("friday", "fri"),
            ("saturday", "sat"),
            ("sunday", "sun"),
        ]
    )
    for name in names
}
_BRACKETS_AND_CONTROLS = re.compile(r"[\[\]\x00-\x08\x0b-\x1f\x7f]")
_INVISIBLE = re.compile("[​-‏‪-‮⁦-⁩]")  # zero-width and bidi
_MENTION = re.compile(r"<[@#!&][^>\s]{0,40}>?|@(?=everyone|here)")
_MARKDOWN = re.compile(r"[*_~`|\\]")
MAX_DAYS_BACK = 366
LOW_CONFIDENCE_MAX = 2  # D108: `said_will` facts are stored with confidence 2; sheet facts use 3


@dataclass(frozen=True)
class Parsed:
    """A question the grammar knows: the lookup and its arguments."""

    intent: str  # who | news | working | p1 | due | more
    args: tuple[tuple[str, str], ...] = ()

    def arg(self, name: str) -> str:
        return dict(self.args)[name]


@dataclass(frozen=True)
class Item:
    """One answer line, the id that is marked seen, and every value the line was built from."""

    seen_type: str
    key: int
    line: str
    facts: tuple[str, ...]
    sort: tuple[Any, ...] = ()  # only the due week merges two lookups and sorts by this


@dataclass(frozen=True)
class Fetched:
    """The unseen rows of a lookup (at most one page) and the exact counts around them."""

    items: tuple[Item, ...]
    start: datetime
    end: datetime
    fresh: int | None = None  # unseen rows in all; None means `len(items)`
    left_out: int = 0  # rows this person has seen
    state: bool = False  # a current-state lookup: its window is the time of the read


@dataclass(frozen=True)
class Answer:
    text: str
    intent: str
    known: bool  # False: the unknown line, a refusal or a prompt; True: an answer from rows
    shown: int = 0
    more: int = 0
    left_out: int = 0


# ------------------------------------------------------------------ parse


def _tidy(raw: str) -> str:
    """One line, no control characters, no closing mark. Empty text stays empty.

    The raw text is cut first, so a huge input costs nothing; a cut question is over the
    limit anyway and gets the unknown line.
    """
    cut = _INVISIBLE.sub("", raw[: MAX_QUESTION * 2])
    clean = " ".join(_BRACKETS_AND_CONTROLS.sub(" ", cut).split())
    return clean.rstrip("?.! ").strip()


def parse_day(raw: str, today: date) -> date | None:
    """A day word, a weekday, `15 Oct` or an ISO date. Future days and old days are refused."""
    word = raw.strip().casefold().rstrip("?.! ")
    found: date | None = None
    try:
        if word == "today":
            found = today
        elif word == "yesterday":
            found = today - timedelta(days=1)
        elif word in _WEEKDAYS:
            found = today - timedelta(days=(today.weekday() - _WEEKDAYS[word]) % 7 or 7)
        elif iso := _ISO_DAY.fullmatch(word):
            found = date(int(iso[1]), int(iso[2]), int(iso[3]))
        elif m := _DAY_MONTH.fullmatch(word):
            found = _named_day(int(m[1]), m[2], m[3], today)
        elif m := _MONTH_DAY.fullmatch(word):
            found = _named_day(int(m[2]), m[1], m[3], today)
    except ValueError:
        return None
    if found is None or found > today or (today - found).days > MAX_DAYS_BACK:
        return None
    return found


def _named_day(day: int, month_word: str, year: str | None, today: date) -> date | None:
    month = guard.MONTHS.get(month_word)
    if month is None:
        return None
    if year:
        return date(int(year), month, day)
    candidate = date(today.year, month, day)
    return candidate if candidate <= today else date(today.year - 1, month, day)


def parse(raw: str, today: date) -> Parsed | None:
    """The question grammar of docs/09. Anything else is None (the unknown line)."""
    question = _tidy(raw)
    if not question or len(question) > MAX_QUESTION:
        return None
    if _MORE.fullmatch(question):
        return Parsed("more")
    if m := _WHO.fullmatch(question):
        return Parsed("who", (("company", m["company"].strip()), ("topic", m["topic"].strip())))
    if m := _NEWS.fullmatch(question):
        day = parse_day(m["day"], today)
        return Parsed("news", (("day", day.isoformat()),)) if day else None
    if m := _WORKING.fullmatch(question):
        return Parsed("working", (("person", m["person"].strip()),))
    if _P1.fullmatch(question):
        return Parsed("p1")
    if _DUE.fullmatch(question):
        return Parsed("due")
    return None


# ------------------------------------------------------------------ text helpers


def _clean(value: object, limit: int | None = None) -> str:
    """A value as one short line that is safe to post.

    Why: row text is data. Square brackets would break a citation. Mentions, Discord markdown,
    zero-width marks and bidi controls could ping people or hide text. `limit` keeps one line
    short enough for the whole answer to fit one Discord message (see `ANSWER_MAX`).
    """
    out = _INVISIBLE.sub("", str(value))
    out = _MENTION.sub(" ", out)
    out = " ".join(_BRACKETS_AND_CONTROLS.sub(" ", _MARKDOWN.sub(" ", out)).split())
    if limit is not None and len(out) > limit:
        out = out[: limit - 1].rstrip() + "…"
    return out


def _facts(*values: object) -> tuple[str, ...]:
    """Every value a line was built from, as the guard reads them (raw, cleaned, capped, ISO)."""
    out: list[str] = []
    for value in values:
        if value is None or value == "":
            continue
        raw = value.isoformat() if isinstance(value, date | datetime) else str(value)
        out += [raw, *(_clean(raw, limit) for limit in _LIMITS)]
    return tuple(dict.fromkeys(out))


def _like(value: str) -> str:
    """A contains-pattern; the wildcards of the user's text are plain characters."""
    return "%" + re.sub(r"([\\%_])", r"\\\1", value) + "%"


def _ist_day(moment: datetime) -> date:
    return moment.astimezone(clock.tz()).date()


def _short_day(day: date) -> str:
    """'18 Oct': the date part of a note citation (D131)."""
    return f"{day.day} {day:%b}"


def _window_text(fetched: Fetched) -> str:
    """A range for news and the week; for a current-state lookup, the time of the read."""
    start, end = fetched.start, fetched.end
    if fetched.state:
        return bw.WINDOW_STATE.format(time=clock.hhmm(end))
    if start.date() == end.date():
        return bw.WINDOW_SAME_DAY.format(
            day=clock.day_label(start.date()), start=clock.hhmm(start), end=clock.hhmm(end)
        )
    return bw.WINDOW_DAYS.format(
        start=clock.day_label(start.date()), end=clock.day_label(end.date())
    )


def _footer(window: str, shown: int, more: int, left_out: int) -> str:
    parts = [
        bw.FOOT_WINDOW.format(window=window),
        bw.FOOT_SHOWN.format(n=shown),
        bw.FOOT_MORE.format(n=more),
        bw.FOOT_LEFT.format(n=left_out),
    ]
    return bw.FOOT_JOIN.join(parts)


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=clock.tz())


# ------------------------------------------------------------------ lookups (SQL builds the rows)


@dataclass(frozen=True, eq=False)
class Lookup:
    """One SQL lookup: the rows of one seen type. `source` is `from ... where ...` text."""

    seen_type: str
    key: str  # SQL expression of the id that is marked seen
    columns: str
    source: str
    order: str
    build: Callable[[Any], Item]


_seen_test_sql = (
    "exists (select 1 from v_user_seen sn where sn.user_short = :me "
    "and sn.item_type = :stype and sn.item_id = KEY)"
)


def _seen_test(key: str) -> str:
    """SQL that is true when :me has seen the item `key`. `key` is a column of ours."""
    return _seen_test_sql.replace("KEY", key)


def _run(conn: Connection, user: str, lookup: Lookup, params: dict[str, Any]) -> tuple[Any, ...]:
    """The first PAGE unseen items, how many rows are unseen, how many this person has seen.

    Why: the seen filter is in SQL, so a cap never hides an unseen row behind seen ones, and
    both counts are exact.
    """
    bound = {**params, "me": user, "stype": lookup.seen_type, "limit": PAGE}
    rows = conn.execute(
        text(
            f"select {lookup.columns} {lookup.source} and not {_seen_test(lookup.key)} "
            f"order by {lookup.order} limit :limit"
        ),
        bound,
    ).all()
    seen = _seen_test("b.k")
    counted = conn.execute(
        text(
            f"select count(*) filter (where not {seen}) as fresh, "  # noqa: S608 - fixed text
            f"count(*) filter (where {seen}) as seen "
            f"from (select distinct {lookup.key} as k {lookup.source}) b"
        ),
        bound,
    ).one()
    return tuple(lookup.build(r) for r in rows), counted.fresh, counted.seen


def _fetched(
    conn: Connection,
    user: str,
    lookups: tuple[Lookup, ...],
    params: dict[str, Any],
    start: datetime,
    end: datetime,
    *,
    state: bool = False,
) -> Fetched:
    results = [_run(conn, user, lookup, params) for lookup in lookups]
    items = [item for found, _fresh, _seen in results for item in found]
    if len(lookups) > 1:
        items.sort(key=lambda i: i.sort)
    return Fetched(
        tuple(items),
        start,
        end,
        fresh=sum(r[1] for r in results),
        left_out=sum(r[2] for r in results),
        state=state,
    )


def _person_item(seen_type: str, row: Any) -> Item:
    """A line about one person: the newest working_on note, else the About cell, else a gap."""
    name, company = _clean(row.name, NAME_MAX), _clean(row.company, COMPANY_MAX)
    if row.working_text:
        detail = _clean(row.working_text, DETAIL_MAX)
        if row.confidence is not None and row.confidence <= LOW_CONFIDENCE_MAX:
            detail = f"{detail} {bw.THEY_SAID}"
        cite = bw.CITE_NOTE.format(date=_short_day(_ist_day(row.observed_at)), company=company)
        stamp: object = _ist_day(row.observed_at)
    else:
        detail = _clean(row.about, DETAIL_MAX) if row.about else bw.NO_NOTE
        cite = bw.CITE_SHEET.format(person=name)
        stamp = None
    line = bw.LINE_PERSON.format(name=name, company=company, detail=detail)
    return Item(
        seen_type,
        row.id,
        f"{line} {cite}",
        _facts(row.name, row.company, row.about, row.working_text, stamp),
    )


_person_columns_sql = (
    "c.id, c.name, c.company, c.about, w.text as working_text, w.observed_at, w.confidence"
)
_person_from_sql = (
    "from v_contact_stage c "
    "left join v_fact_working_on w on w.entity_type = 'contact' and w.entity_id = c.id "
)


def fetch_who(conn: Connection, parsed: Parsed, now: datetime, user: str) -> Fetched:
    lookup = Lookup(
        SEEN_WHO,
        "c.id",
        _person_columns_sql,
        _person_from_sql + "where c.company ilike :company escape '\\' "
        "and (c.about ilike :topic escape '\\' or w.text ilike :topic escape '\\' "
        "or c.title ilike :topic escape '\\')",
        "c.stage_rank desc, c.name, c.id",
        lambda r: _person_item(SEEN_WHO, r),
    )
    params = {"company": _like(parsed.arg("company")), "topic": _like(parsed.arg("topic"))}
    return _fetched(conn, user, (lookup,), params, _day_start(now.date()), now, state=True)


def fetch_working(conn: Connection, parsed: Parsed, now: datetime, user: str) -> Fetched:
    lookup = Lookup(
        SEEN_WORKING,
        "c.id",
        _person_columns_sql,
        _person_from_sql + "where c.name ilike :person escape '\\'",
        "(lower(c.name) = lower(:exact)) desc, c.name, c.id",
        lambda r: _person_item(SEEN_WORKING, r),
    )
    params = {"person": _like(parsed.arg("person")), "exact": parsed.arg("person")}
    return _fetched(conn, user, (lookup,), params, _day_start(now.date()), now, state=True)


def _p1_item(row: Any) -> Item:
    name = _clean(row.name, NAME_MAX)
    line = bw.LINE_P1.format(
        name=name, company=_clean(row.company, COMPANY_MAX), stage=_clean(row.stage, STAGE_MAX)
    )
    return Item(
        SEEN_P1,
        row.id,
        f"{line} {bw.CITE_SHEET.format(person=name)}",
        _facts(row.name, row.company, row.stage),
    )


def fetch_p1(conn: Connection, parsed: Parsed, now: datetime, user: str) -> Fetched:
    lookup = Lookup(
        SEEN_P1,
        "id",
        "id, name, company, stage",
        "from v_contact_stage where is_p1 and not has_email",
        "stage_rank desc, company, name, id",
        _p1_item,
    )
    return _fetched(conn, user, (lookup,), {}, _day_start(now.date()), now, state=True)


def _news_item(row: Any) -> Item:
    kind = _clean(row.tag, KIND_MAX) if row.tag else bw.NEWS_KIND
    day = clock.day_label(_ist_day(row.at))
    if row.importance is None:
        line = bw.LINE_NEWS_PLAIN.format(day=day, kind=kind)
    else:
        line = bw.LINE_NEWS.format(day=day, kind=kind, n=row.importance)
    cite = bw.CITE_NEWS.format(title=_clean(row.title, TITLE_MAX))
    return Item(
        SEEN_STORY,
        row.story_id,
        f"{line} {cite}",
        _facts(row.title, row.tag, row.importance, _ist_day(row.at)),
    )


def fetch_news(conn: Connection, parsed: Parsed, now: datetime, user: str) -> Fetched:
    since = _day_start(date.fromisoformat(parsed.arg("day")))
    lookup = Lookup(
        SEEN_STORY,
        "story_id",
        "story_id, title, tag, importance, coalesce(published_at, first_seen_at) as at",
        "from v_signal_all where coalesce(published_at, first_seen_at) >= :since",
        "importance desc nulls last, coalesce(published_at, first_seen_at) desc, story_id desc",
        _news_item,
    )
    return _fetched(conn, user, (lookup,), {"since": since}, since, now)


def _task_item(row: Any) -> Item:
    line = bw.LINE_TASK.format(text=_clean(row.text, DETAIL_MAX), day=clock.day_label(row.due))
    cite = bw.CITE_NOTE_OWNER.format(
        date=_short_day(row.due), person=_clean(row.owner_name, NAME_MAX)
    )
    facts = _facts(row.text, row.due, row.owner_name)
    return Item(SEEN_TASK, row.id, f"{line} {cite}", facts, (row.due, 0, row.id))


def _meeting_item(row: Any) -> Item:
    name = _clean(row.name, NAME_MAX)
    line = bw.LINE_MEETING.format(
        name=name, company=_clean(row.company, COMPANY_MAX), day=clock.day_label(row.meeting_date)
    )
    facts = _facts(row.name, row.company, row.meeting_date)
    return Item(
        SEEN_MEETING,
        row.id,
        f"{line} {bw.CITE_SHEET.format(person=name)}",
        facts,
        (row.meeting_date, 1, row.id),
    )


_DUE_TASKS = Lookup(
    SEEN_TASK,
    "id",
    "id, text, due, owner_name",
    "from v_commitment where owner_short = :me and state = 'open' and due between :ws and :we",
    "due, id",
    _task_item,
)
_DUE_MEETINGS = Lookup(
    SEEN_MEETING,
    "id",
    "id, name, company, meeting_date",
    "from v_contact_stage where meeting_date between :ws and :we "
    "and coalesce(lower(meeting_status), '') not like 'complete%' "
    "and coalesce(lower(meeting_status), '') not like 'cancel%'",
    "meeting_date, id",
    _meeting_item,
)


def fetch_due(conn: Connection, parsed: Parsed, now: datetime, user: str) -> Fetched:
    """This week (Monday to Sunday): the asker's open tasks and the meetings of the sheet.

    A task has no company, so its citation names the person who owns it.
    """
    week_start = clock.week_start(now.date())
    week_end = week_start + timedelta(days=6)
    return _fetched(
        conn,
        user,
        (_DUE_TASKS, _DUE_MEETINGS),
        {"ws": week_start, "we": week_end},
        _day_start(week_start),
        _day_start(week_end) + timedelta(hours=23, minutes=59),
    )


FETCHERS: dict[str, Callable[[Connection, Parsed, datetime, str], Fetched]] = {
    "who": fetch_who,
    "working": fetch_working,
    "p1": fetch_p1,
    "news": fetch_news,
    "due": fetch_due,
}


# ------------------------------------------------------------------ the last question (for `more`)

_LAST: OrderedDict[tuple[str, str], Parsed] = OrderedDict()
_LAST_LOCK = threading.Lock()


def forget_last() -> None:
    with _LAST_LOCK:
        _LAST.clear()


def _remember(surface: str, user: str, parsed: Parsed) -> None:
    with _LAST_LOCK:
        _LAST[(surface, user)] = parsed
        _LAST.move_to_end((surface, user))
        while len(_LAST) > LAST_KEPT:
            _LAST.popitem(last=False)


def _recall(surface: str, user: str) -> Parsed | None:
    with _LAST_LOCK:
        return _LAST.get((surface, user))


# ------------------------------------------------------------------ text of one answer


def _render(kept: tuple[Item, ...], more: int, left_out: int, window: str, empty: str) -> str:
    """Item lines, then the hint, then the footer. The footer is always the last line."""
    lines = [i.line for i in kept] or [empty]
    if more:
        lines.append(bw.MORE_HINT.format(n=min(more, PAGE)))
    lines.append(_footer(window, len(kept), more, left_out))
    return "\n".join(lines)


def compose(fetched: Fetched, *, left_out: int, fresh: int) -> tuple[str, tuple[Item, ...], int]:
    """The answer text, the items it shows and the count that is left to show.

    Why: the text must fit one Discord message and the log (D132). The caps on each value keep
    five lines inside `ANSWER_MAX`; if a line still does not fit, it moves to "more".
    """
    window = _window_text(fetched)
    empty = bw.NOTHING_NEW if left_out else bw.NOTHING_FOUND
    page = tuple(fetched.items[:PAGE])
    for count in range(len(page), -1, -1):
        kept = page[:count]
        more = max(fresh - len(kept), 0)
        reply = _render(kept, more, left_out, window, empty)
        if len(reply) <= ANSWER_MAX:
            break
    return reply, kept, more


# ------------------------------------------------------------------ seen and log


def _mark_seen(write: Writer, user: str, intent: str, shown: Iterable[Item]) -> None:
    by_type: dict[str, set[int]] = {}
    for item in shown:
        by_type.setdefault(item.seen_type, set()).add(item.key)
    for seen_type, keys in by_type.items():
        result = write(
            "select saley_mark_seen(:u, :t, cast(:ids as bigint[]), :q)",
            {
                "u": user,
                "t": seen_type,
                "ids": "{" + ",".join(str(k) for k in sorted(keys)) + "}",
                "q": f"ask-{intent}",
            },
        )
        if not result.get("ok"):
            raise RuntimeError(f"saley_mark_seen refused: {result.get('code')}")


@dataclass(frozen=True)
class _Turn:
    """Who asked, where and what: the fixed part of one exchange log row."""

    write: Writer
    user: str
    surface: str
    question: str


def _log(
    turn: _Turn,
    *,
    intent: str,
    refs: dict[str, Any],
    answer: str,
    window: tuple[datetime, datetime] | None,
    left_out: int | None,
) -> None:
    """Write the exchange. A falsy id is a refusal: the caller must not mark anything seen."""
    result = turn.write(
        "select saley_ask_log(:u, :s, :t, :i, cast(:r as jsonb), :a, :wf, :wt, :lo)",
        {
            "u": turn.user,
            "s": turn.surface,
            "t": turn.question[:MAX_QUESTION],
            "i": intent,
            "r": json.dumps(refs),
            "a": answer,
            "wf": window[0] if window else None,
            "wt": window[1] if window else None,
            "lo": None if left_out is None else min(left_out, LOG_LEFT_MAX),
        },
    )
    if not result:
        raise RuntimeError("saley_ask_log refused")


# ------------------------------------------------------------------ the engine


def _reply_only(turn: _Turn, intent: str, reply: str) -> Answer:
    """A line with no rows behind it: the unknown line, a prompt or a refusal."""
    _log(turn, intent=intent, refs={}, answer=reply, window=None, left_out=None)
    return Answer(reply, intent, known=False)


def _finish(turn: _Turn, parsed: Parsed, fetched: Fetched) -> Answer:
    """Check, log, then mark seen. The log comes first: a failed log must not lose items."""
    fresh = len(fetched.items) if fetched.fresh is None else fetched.fresh
    reply, shown, more = compose(fetched, left_out=fetched.left_out, fresh=fresh)
    try:
        if shown:
            guard.require_clean(
                "\n".join(i.line for i in shown), [f for i in shown for f in i.facts]
            )
    except guard.GuardViolation as exc:
        # A rule broke, not the user. Say nothing from the rows; the log names the kinds only.
        log.error(
            "answer_refused", intent=parsed.intent, kinds=sorted({v.kind for v in exc.violations})
        )
        return _reply_only(turn, parsed.intent, bw.ANSWER_FAILED)
    refs = {"shown": [[i.seen_type, i.key] for i in shown], "more": more}
    _log(
        turn,
        intent=parsed.intent,
        refs=refs,
        answer=reply,
        window=(fetched.start, fetched.end),
        left_out=fetched.left_out,
    )
    _mark_seen(turn.write, turn.user, parsed.intent, shown)
    return Answer(
        reply, parsed.intent, known=True, shown=len(shown), more=more, left_out=fetched.left_out
    )


def respond(
    read: Connection,
    write: Writer,
    *,
    user: str,
    raw: str,
    surface: str,
    now: datetime | None = None,
) -> Answer:
    """Answer one question from `user`. `surface` is `discord` or `dashboard`."""
    moment = (now or clock.now_ist()).astimezone(clock.tz())
    turn = _Turn(write, user, surface, _tidy(raw))
    parsed = parse(turn.question, moment.date())
    if parsed is None:
        return _reply_only(turn, "unknown", _unknown())
    is_more = parsed.intent == "more"
    if is_more:
        parsed = _recall(surface, user)
        if parsed is None:
            return _reply_only(turn, "more", bw.NO_MORE)
    fetched = FETCHERS[parsed.intent](read, parsed, moment, user)
    if not is_more:
        _remember(surface, user, parsed)  # only after a good fetch
    return _finish(turn, parsed, fetched)


def _unknown() -> str:
    return f"{bw.UNKNOWN_HAVE} {bw.UNKNOWN_NEXT.format(person=bw.UNKNOWN_PERSON)}"


def discord_reply(session: Session, author_id: int, raw: str, settings: Settings) -> str:
    """The reply for one Discord question. An unknown author gets one line and no data (D135)."""
    who = identity.identify(session, author_id, settings)
    line = identity.reply_for(who)
    if line is not None or who.short is None:
        return line or bw.UNKNOWN_AUTHOR  # an unknown author: one line, no data, no log
    if not who.can_instruct:
        return bw.TEAM_ONLY  # D141: not on the team list; no data, no log, nothing seen

    def write(sql: str, params: dict[str, Any]) -> Any:
        return session.execute(text(sql), params).scalar_one()

    try:
        answer = respond(session.connection(), write, user=who.short, raw=raw, surface="discord")
    except Exception:
        session.rollback()  # the log and the seen marks are one unit
        raise
    session.commit()
    return answer.text
