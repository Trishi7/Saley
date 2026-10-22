"""Test-only rows for the browser check (R2 phase 7, `scripts/qa_seed.py`).

Why: the click-path check must press real controls (Done, Undo, Confirm, Dismiss, Mark done)
on a dev database that may hold no such rows today. Pressing a control on a real person could
reach the real sheet. So the check seeds its own rows and removes them again:

- Every contact has a row key that starts with `qa:` and a name that starts with "QA Seed".
  Such a person is not in the sheet. The sync ignores the `qa:` prefix (no removal, no merge),
  and the worker's push cannot find the name in the sheet, so a write for it fails before any
  cell is written (`outbox._locate` raises "the row was not found in the sheet"). The Done on
  the reconnect task is a `task_done`: it never creates an outbox row at all. The research task
  is an `add_email` task (D154): it has no Done button, and the check asserts that.
- The seeded promise belongs to the QA user: only the owner may tick a commitment (D155).
- Decisions, commitments and cards are marked with `qa:` in their text or key.
- R3 news rows: one source "QA Seed Wire" (inactive, so no job fetches it), two scored stories on
  `https://qa.invalid/`, one signal that links the first story to the company "QA Seed Co" (row
  key `qa:account:newco`, no people, so it is a "new company" suggestion), and one search hit for
  it. The scorer backend is `rules`: no LLM call. The hit is a made-up public-profile URL; nothing
  fetches it. A person added to "QA Seed Co" can never reach the sheet: the worker refuses an
  append for a company that starts with "QA Seed" (`saley.sales.outbox`), and the check runs
  with SHEET_WRITE_ENABLED=false, so the write stays held and Undo cancels it.
- A throwaway user `qa` (role approver, name "QA Seed"): the browser check runs as this person
  (View as `qa` on every GET and POST), so the pages it opens mark items "seen" for `qa` and for
  nobody real (A11). `teardown` deletes the user, its decisions and all its `user_seen` rows.
  Seen rows that earlier runs left for real people cannot be told apart (they carry no QA mark),
  so they stay.
- `teardown` removes every such row in one transaction. `seed` runs it first, so a crashed run
  leaves nothing behind for the next one.
- R5 T14 (people_drawer, R5-P03): the research person also has two paper links on
  `https://qa.invalid/` (the paper text is derived from the URL, never invented) and one
  "working on" fact from a meeting note, so the drawer shows "N papers" and a source. The
  fact goes with the person (`facts` step of `teardown`).
- R5 T15 (signals_categories, events_page): each seeded story gets its topic from the real rule
  (`topic_for`; the linked story is Funding), and three upcoming events `qa:event:*` (one
  registered, cities Bengaluru, London and Berlin) give the Events check a registered row, an
  unregistered row and each City value. The sheet sync keeps a `qa:event:*` row only while
  the seed is active (`sync.qa_seed_active`: user `qa` exists, under 6 h old); a stale one
  is removed.
  Teardown removes the events, their metric rows and their seen marks.
- R5 T16a (commitment_owner_toast, R5-C03): a second `qa:` promise owned by another team member
  (Kushal, as the seeded decisions), with NO due day: no reminder is ever planned for it, it is
  no Today task and it is not in the pack. The QA user presses Done on it and must get the
  owner's 403 line; nothing is written.
- R5 T16a (run_news_now, R5-N09): a throwaway ADMIN `qaadmin` ("QA Seed Admin"). The check
  sends "Run news now" as this person, so the request row is signed by a QA user and never by
  the real admin. `teardown` deletes its requests first, then the user. The gate's worker slot
  never registers the job that runs a request (D151), so the gate fetches no news.
- Dev only: the seed refuses to run when SALEY_ENV is not dev.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.core.clock import now_ist, today_ist
from saley.core.settings import get_settings
from saley.intel.pack import build_body
from saley.intel.proposals import candidates
from saley.news.clean import fingerprint, url_hash
from saley.news.topic import topic_for
from saley.sales.parse import norm_name
from saley.sales.rowadd import QA_NAME_PREFIX
from saley.sales.sync import QA_EVENT_PREFIX, QA_ROW_PREFIX, QA_SEED_USER

PREFIX = QA_ROW_PREFIX
QA_USER = QA_SEED_USER  # sync.qa_seed_active looks for this user
QA_USER_NAME = "QA Seed"
QA_ID = "select id from users where short = :qa"
QA_DECISIONS = f"select id from decision where user_id in ({QA_ID})"
RESEARCH_KEY = f"{PREFIX}poc:research"
RECONNECT_KEY = f"{PREFIX}poc:reconnect"
CARD_KEYS = (f"{PREFIX}poc:meeting-a", f"{PREFIX}poc:meeting-b")
NAMES = {
    RESEARCH_KEY: "QA Seed Research",
    RECONNECT_KEY: "QA Seed Reconnect",
    CARD_KEYS[0]: "QA Seed Meeting A",
    CARD_KEYS[1]: "QA Seed Meeting B",
}
DECISIONS = (f"{PREFIX}D1", f"{PREFIX}D2")
COMMITMENT_TEXT = f"{PREFIX} send the QA note"
# R5 T16a: a promise of another person (D155: only its owner may mark it done).
OTHER_COMMITMENT_TEXT = f"{PREFIX} a promise of another person"
OTHER_OWNER = "kushal"
# R5 T16a: the throwaway admin that signs "Run news now" in the browser check (D150).
QA_ADMIN = "qaadmin"
QA_ADMIN_NAME = "QA Seed Admin"
# A Monday long ago: /api/pack shows the newest pack, so a real pack always wins. The seed
# pack lets browser QA draw a real body (R4-B02); with no pack at all the answer is 200 `empty`
# (D229), never a 404.
QA_PACK_WEEK = date(2000, 1, 3)
# Both QA rows are due long ago: Today shows 10 tasks a page sorted by due day (D152), so a
# recent QA row falls behind the real sheet rows and browser QA cannot find it.
RESEARCH_AGE_DAYS = 3650  # R06: connected more than 3 days ago
RECONNECT_AGE_DAYS = 3650  # R14: last step + 45 days is past, so the reconnect task is due
MEETING_AGE_DAYS = 3
# R5 T14: two papers and a working_on fact on the research person (people_drawer check).
PAPER_URLS = (
    "https://qa.invalid/papers/qa-seed-speech-evals",
    "https://qa.invalid/papers/qa-seed-agents",
)
WORKING_ON_TEXT = f"{PREFIX} evals of speech models"
LIKE = f"{PREFIX}%"
CARD_RE = f"^[a-z_]+:{PREFIX}"  # a card key is `<type>:<row key>:<day>`; only qa rows match

# R3 news rows (phase 7). The URL host `qa.invalid` can never resolve: nothing can fetch it.
NEWS_HOST = "https://qa.invalid/"
SOURCE_NAME = f"{QA_NAME_PREFIX} Wire"
COMPANY_NAME = f"{QA_NAME_PREFIX} Co"
COMPANY_KEY = f"{PREFIX}account:newco"
PERSON_NAME = f"{QA_NAME_PREFIX} Person"
HIT_URL = "https://www.linkedin.com/in/qa-seed-person"
HIT_TITLE = f"{PERSON_NAME} - Head of Data - {COMPANY_NAME} | LinkedIn"
LINKED_TITLE = f"{QA_NAME_PREFIX}: {COMPANY_NAME} raises a new funding round"
QUIET_TITLE = f"{QA_NAME_PREFIX}: A quiet note on speech models"
LINKED_AGE = timedelta(hours=3)
QUIET_AGE = timedelta(minutes=30)
STORIES = f"select id from news_stories where url like '{NEWS_HOST}%'"
SIGNALS = f"select id from signal where story_id in ({STORIES})"
COMPANIES = f"select id from account where sheet_row_key like '{PREFIX}%'"
QA_CONTACTS = f"select id from contact where sheet_row_key like '{PREFIX}%'"
# R5 T15 (events_page, R5-E02): three upcoming events, more than 3 days away (so Today's
# "needs a look" never shows them). One is registered and starts LAST, so "registered first"
# is a real test of the order; the cities are one each of Bangalore (by its alias Bengaluru),
# London and Other. The venues are made-up addresses: nothing fetches them.
EVENT_PREFIX = QA_EVENT_PREFIX
EVENTS = (
    # (key, name, location, days from today, registered)
    (
        f"{EVENT_PREFIX}summit",
        f"{QA_NAME_PREFIX} Summit",
        "QA Hall, 1 MG Road, Bengaluru 560001",
        40,
        True,
    ),
    (
        f"{EVENT_PREFIX}meetup",
        f"{QA_NAME_PREFIX} Meetup",
        "QA Hub, 2 Kings Cross, London N1",
        20,
        False,
    ),
    (
        f"{EVENT_PREFIX}expo",
        f"{QA_NAME_PREFIX} Expo",
        "QA Centre, 3 Main Street, Berlin",
        30,
        False,
    ),
)
QA_EVENTS = f"select id from event where sheet_row_key like '{EVENT_PREFIX}%'"
# `reason` of a row_add decision is "<account id>:<lower-case name>" (saley_act)
# L5: also only the throwaway user's own decisions, so a real add never matches by its name.
ROW_ADDS = (
    "select id from decision where kind = 'row_add' "
    f"and reason ~ '^[0-9]+[:]{QA_NAME_PREFIX.lower()} ' and user_id in ({QA_ID})"
)
SOURCE_IDS = "select id from news_sources where name = :sname and url = :surl"
ROW_ADD_UNDOS = f"select id from decision where undo_of in ({ROW_ADDS})"


class SeedRefused(RuntimeError):
    """The seed runs only on a dev database with a synced account."""


@dataclass(frozen=True)
class SeedInfo:
    contacts: dict[str, int]
    cards: list[int]
    decisions: list[int]
    commitment: int
    stories: list[int] = field(default_factory=list)
    account: int | None = None
    events: list[int] = field(default_factory=list)
    other_commitment: int | None = None  # R5 T16a: owned by another team member

    def summary(self) -> str:
        return (
            f"{len(self.contacts)} people · {len(self.cards)} cards · "
            f"{len(self.decisions)} decisions · "
            f"{1 + (self.other_commitment is not None)} commitments · "
            f"{len(self.stories)} stories · 1 company · {len(self.events)} events"
        )


def hide_other_news(session: Session) -> int:
    """Mark every story and signal that is NOT a QA row as seen by the QA user only.

    Why (R5 T16b, D226): the new Signals page shows New 10 a page, ranked. Real stories of the
    gate database can push a seeded story past the first page, and `signals_pages` must find
    both seeded stories on New. The rows belong to the QA user (`teardown` deletes all of its
    `user_seen` rows); nobody real is marked (A11). Returns the rows added.

    Only on a database whose name ends in `_test` (the gate's, D113): run by mistake against
    the dev database it would add one seen row per real story for the QA user (T16b review).
    """
    database = session.execute(text("select current_database()")).scalar_one()
    if not str(database).endswith("_test"):
        raise RuntimeError(f"hide_other_news runs only on a _test database, not {database}")
    added = 0
    for item_type, ids in (
        ("story", f"select id from news_stories where id not in ({STORIES})"),
        ("signal", f"select id from signal where id not in ({SIGNALS})"),
    ):
        result = session.execute(
            text(
                "insert into user_seen (user_id, item_type, item_id, query_id) "
                f"select ({QA_ID}), :t, x.id, 'qa-setup' from ({ids}) x "
                "on conflict do nothing"
            ),
            {"qa": QA_USER, "t": item_type},
        )
        added += result.rowcount or 0
    return added


def reset_seen(session: Session) -> int:
    """Forget that anyone saw a QA item, so the check starts from a list with all items new.

    Why: the page marks every list it shows as seen (D75). The `views` check opens Signals,
    Events and Suggestions before `signals_pages` does, so without this reset the seeded rows
    would already be "left out". Rows of real stories are not touched.
    """
    result = session.execute(
        text(
            "delete from user_seen where "
            f"(item_type = 'story' and item_id in ({STORIES})) "
            f"or (item_type = 'signal' and item_id in ({SIGNALS})) "
            f"or (item_type = 'suggest_newco' and item_id in ({COMPANIES})) "
            f"or (item_type = 'event' and item_id in ({QA_EVENTS})) "
            "or (item_type in ('suggest_warm', 'suggest_best', 'suggest_res') "
            f"and item_id in ({QA_CONTACTS}))"
        )
    )
    return result.rowcount or 0


def teardown(session: Session) -> dict[str, int]:
    """Remove every row the seed made. Safe to run twice; touches nothing without `qa:`."""
    removed: dict[str, int] = {"seen": reset_seen(session)}
    # L5: the seeded source ids are read now, before its stories go; no `name like` delete.
    params: dict[str, Any] = {
        "p": LIKE,
        "c": CARD_RE,
        "qa": QA_USER,
        "qadmin": QA_ADMIN,
        "pw": QA_PACK_WEEK,
        "src": [
            row[0]
            for row in session.execute(
                text(SOURCE_IDS), {"sname": SOURCE_NAME, "surl": f"{NEWS_HOST}rss"}
            )
        ],
    }
    steps = (
        (
            "row_add_audit",
            "delete from sync_audit where outbox_id in (select id from sync_outbox "
            f"where decision_id in ({ROW_ADDS}) or decision_id in ({ROW_ADD_UNDOS}))",
        ),
        (
            "row_add_outbox",
            f"delete from sync_outbox where decision_id in ({ROW_ADDS}) "
            f"or decision_id in ({ROW_ADD_UNDOS})",
        ),
        ("row_add_undos", f"delete from decision where id in ({ROW_ADD_UNDOS})"),
        ("row_adds", f"delete from decision where id in ({ROW_ADDS})"),
        (
            "audit",
            "delete from sync_audit where outbox_id in "
            "(select id from sync_outbox where row_key like :p)",
        ),
        ("outbox", "delete from sync_outbox where row_key like :p"),
        (
            "decisions",
            "delete from decision where row_key like :p or choice like :p "
            "or proposal_id in (select id from proposal where card_key ~ :c)",
        ),
        (
            "reminders",
            "delete from reminder where commitment_id in "
            "(select id from commitment where text like :p)",
        ),
        ("commitments", "delete from commitment where text like :p"),
        ("cards", "delete from proposal where card_key ~ :c"),
        (
            "facts",
            "delete from fact where entity_type = 'contact' and entity_id in "
            "(select id from contact where sheet_row_key like :p)",
        ),
        ("contacts", "delete from contact where sheet_row_key like :p"),
        ("stories", f"delete from news_stories where id in ({STORIES})"),  # signals cascade
        ("hits", f"delete from search_hit where account_id in ({COMPANIES})"),
        ("searches", f"delete from search_request where account_id in ({COMPANIES})"),
        ("accounts", f"delete from account where id in ({COMPANIES})"),
        (
            "event_metrics",
            f"delete from metric_snapshot where scope_type = 'event' and scope_id in ({QA_EVENTS})",
        ),
        ("events", f"delete from event where id in ({QA_EVENTS})"),
        ("sources", "delete from news_sources where id = any(cast(:src as bigint[]))"),
        ("pack", "delete from weekly_pack where week_start = :pw"),
        *_qa_user_steps(),
    )
    for name, sql in steps:
        result = session.execute(text(sql), params)
        removed[name] = result.rowcount or 0
    return removed


def _qa_user_steps() -> tuple[tuple[str, str], ...]:
    """Remove the throwaway user and everything that points at it (A11), last of all."""
    return (
        (
            "qa_audit",
            "delete from sync_audit where outbox_id in (select id from sync_outbox "
            f"where decision_id in ({QA_DECISIONS}) or decision_id in "
            f"(select id from decision where undo_of in ({QA_DECISIONS})))",
        ),
        (
            "qa_outbox",
            f"delete from sync_outbox where decision_id in ({QA_DECISIONS}) or decision_id in "
            f"(select id from decision where undo_of in ({QA_DECISIONS}))",
        ),
        (
            "qa_decisions",
            f"delete from decision where id in ({QA_DECISIONS}) or undo_of in ({QA_DECISIONS})",
        ),
        ("qa_undone", f"update decision set undone_by = null where undone_by in ({QA_ID})"),
        (
            "qa_confirmed",
            f"update decision set confirmed_by = null where confirmed_by in ({QA_ID})",
        ),
        ("qa_searches", f"update search_request set user_id = null where user_id in ({QA_ID})"),
        ("qa_reminders", f"delete from reminder where for_user in ({QA_ID})"),
        ("qa_asks", f"delete from ask where to_user in ({QA_ID})"),
        ("qa_seen_own", f"delete from user_seen where user_id in ({QA_ID})"),
        # R4 ticket 10: the Ask card check logs one exchange for the QA user (bot_exchange.user_id).
        ("qa_exchanges", f"delete from bot_exchange where user_id in ({QA_ID})"),
        ("qa_user", "delete from users where short = :qa"),
        # R5 T16a: the throwaway admin and the news requests it signed.
        (
            "qa_news_requests",
            "delete from news_run_request where requested_by in "
            "(select id from users where short = :qadmin)",
        ),
        ("qa_admin", "delete from users where short = :qadmin"),
    )


def _insert_contact(
    session: Session, account_id: int, key: str, today: date, **cells: object
) -> int:
    name = NAMES[key]
    return session.execute(
        text(
            "insert into contact (account_id, sheet_row_key, name, name_key, stage, synced_at, "
            "contacted, connected, li_connected_date, meeting_date, meeting_status, paper_url) "
            "values (:a, :k, :n, :nk, :stage, now(), :contacted, :connected, :li, :md, :ms, "
            ":paper) returning id"
        ),
        {
            "a": account_id,
            "k": key,
            "n": name,
            "nk": norm_name(name),
            "stage": cells.get("stage", "Contacted"),
            "contacted": True,
            "connected": cells.get("connected", False),
            "li": cells.get("li_connected_date"),
            "md": cells.get("meeting_date"),
            "ms": cells.get("meeting_status"),
            "paper": cells.get("paper_url"),
        },
    ).scalar_one()


def _insert_working_on(session: Session, contact_id: int) -> None:
    """A meeting-note fact, so the drawer shows "Working on" with its source "note <day>"."""
    session.execute(
        text(
            "insert into fact (entity_type, entity_id, kind, value, source_type, author) "
            "values ('contact', :i, 'working_on', cast(:v as jsonb), 'meeting_note', :qa)"
        ),
        {"i": contact_id, "v": json.dumps({"text": WORKING_ON_TEXT}), "qa": QA_USER},
    )


def _insert_pack(session: Session, today: date) -> None:
    """One pack with the real body shape, so the pack page draws a pack and loads no 404."""
    body = build_body(session, QA_PACK_WEEK, today)
    session.execute(
        text("insert into weekly_pack (week_start, body) values (:w, cast(:b as jsonb))"),
        {"w": QA_PACK_WEEK, "b": json.dumps(body, default=str)},
    )


def seed(session: Session, today: date | None = None) -> SeedInfo:
    """Make the rows. Clears an old seed first. The caller commits."""
    if not get_settings().is_dev:
        raise SeedRefused("The QA seed runs only with SALEY_ENV=dev.")
    today = today or today_ist()
    account_id = session.execute(
        text("select id from account where removed_at is null order by id limit 1")
    ).scalar()
    if account_id is None:
        raise SeedRefused("No account in the database. Run `saley sync --once` first.")
    teardown(session)
    session.execute(
        text("insert into users (short, name, role) values (:s, :n, 'approver')"),
        {"s": QA_USER, "n": QA_USER_NAME},
    )
    session.execute(
        text("insert into users (short, name, role) values (:s, :n, 'admin')"),
        {"s": QA_ADMIN, "n": QA_ADMIN_NAME},
    )
    _insert_pack(session, today)

    contacts = {
        RESEARCH_KEY: _insert_contact(
            session,
            account_id,
            RESEARCH_KEY,
            today,
            stage="Connected",
            connected=True,
            li_connected_date=today - timedelta(days=RESEARCH_AGE_DAYS),
            paper_url=" ".join(PAPER_URLS),
        )
    }
    _insert_working_on(session, contacts[RESEARCH_KEY])
    # A reconnect task is Sid's, has a Done button and is a `task_done`: it never reaches the
    # outbox. The research row above is an `add_email` task now (D154) and has no Done button.
    contacts[RECONNECT_KEY] = _insert_contact(
        session,
        account_id,
        RECONNECT_KEY,
        today,
        stage="Engaged",
        li_connected_date=today - timedelta(days=RECONNECT_AGE_DAYS),
    )
    for key in CARD_KEYS:
        contacts[key] = _insert_contact(
            session,
            account_id,
            key,
            today,
            meeting_date=today - timedelta(days=MEETING_AGE_DAYS),
            meeting_status="Booked",
        )
    cards = _insert_cards(session, today)
    decisions = [
        session.execute(
            text(
                "insert into decision (kind, choice, reason, state, user_id) "
                "select 'strategy_decision', :c, 'qa: seeded for the browser check', "
                "'proposed', id from users where short = 'kushal' returning id"
            ),
            {"c": choice},
        ).scalar_one()
        for choice in DECISIONS
    ]
    commitment = session.execute(
        text(
            "insert into commitment (owner, text, due, source_type) "
            "select id, :t, :d, 'meeting_note' from users where short = :o "
            "returning id"
        ),
        {"t": COMMITMENT_TEXT, "d": today, "o": QA_USER},
    ).scalar_one()
    other = session.execute(
        text(
            "insert into commitment (owner, text, due, source_type) "
            "select id, :t, null, 'meeting_note' from users where short = :o "
            "returning id"
        ),
        {"t": OTHER_COMMITMENT_TEXT, "o": OTHER_OWNER},
    ).scalar_one()
    account, stories = _insert_news(session)
    events = _insert_events(session, today)
    return SeedInfo(
        contacts, cards, decisions, commitment, stories, account, events, other_commitment=other
    )


def _insert_events(session: Session, today: date) -> list[int]:
    """R5 T15: the three QA events of `EVENTS` (registered first, City and Venue)."""
    return [
        session.execute(
            text(
                'insert into event (sheet_row_key, name, city, start, "end", registered) '
                "values (:k, :n, :c, :d, :d, :r) returning id"
            ),
            {"k": key, "n": name, "c": where, "d": today + timedelta(days=days), "r": registered},
        ).scalar_one()
        for key, name, where, days, registered in EVENTS
    ]


def _insert_cards(session: Session, today: date) -> list[int]:
    """The cards the real rule wants for the two QA meetings, so the worker never expires them."""
    ids: list[int] = []
    for card in candidates(session, today):
        if not str(card["payload"]["row"]).startswith(PREFIX):
            continue
        ids.append(
            session.execute(
                text(
                    "insert into proposal (type, card_key, entity_type, entity_id, payload, "
                    "evidence, rank) values (:t, :k, 'contact', :cid, cast(:p as jsonb), "
                    "cast(:e as jsonb), :r) returning id"
                ),
                {
                    "t": card["type"],
                    "k": card["card_key"],
                    "cid": card["contact_id"],
                    "p": json.dumps(card["payload"]),
                    "e": json.dumps(card["evidence"]),
                    "r": card["rank"],
                },
            ).scalar_one()
        )
    return ids


def _insert_story(
    session: Session, source_id: int, title: str, slug: str, age: timedelta, score: tuple[Any, ...]
) -> int:
    importance, impact, tag = score
    url = f"{NEWS_HOST}{slug}"
    return session.execute(
        text(
            "insert into news_stories (title, summary, url, url_hash, fingerprint, kind, outlets, "
            "outlet_count, first_source_id, first_seen_at, published_at, importance, impact, tag, "
            "why, scored_at, scorer_backend, topic) values (:t, '', :u, :h, :f, 'news', "
            "cast(:o as text[]), 1, :s, :at, :at, :i, :m, :g, '', now(), 'rules', :topic) "
            "returning id"
        ),
        {
            "t": title,
            "u": url,
            "h": url_hash(url),
            "f": fingerprint(title),
            "o": [SOURCE_NAME],
            "s": source_id,
            "at": now_ist() - age,
            "i": importance,
            "m": impact,
            "g": tag,
            # R5 T15 (signals_categories): the real rule names the topic ("raises a new
            # funding round" is Funding), so the Funding chip has one seeded story to show.
            "topic": topic_for(title),
        },
    ).scalar_one()


def _insert_news(session: Session) -> tuple[int, list[int]]:
    """The company, one search hit, two scored stories and one signal (see the module text)."""
    source_id = session.execute(
        text(
            "insert into news_sources (name, kind, lane, url, active, categories) "
            "values (:n, 'google', 'qa', :u, false, cast('{}' as text[])) returning id"
        ),
        {"n": SOURCE_NAME, "u": f"{NEWS_HOST}rss"},
    ).scalar_one()
    account = session.execute(
        text(
            "insert into account (name, name_key, sheet_row_key, in_master, industry, geo, "
            "funding) values (:n, :k, :r, false, 'AI Labs', 'India', '$12M') returning id"
        ),
        {"n": COMPANY_NAME, "k": norm_name(COMPANY_NAME), "r": COMPANY_KEY},
    ).scalar_one()
    request = session.execute(
        text(
            "insert into search_request (account_id, state, hits, finished_at) "
            "values (:a, 'done', 1, now()) returning id"
        ),
        {"a": account},
    ).scalar_one()
    session.execute(
        text(
            "insert into search_hit (request_id, query, engine, url, title, account_id) "
            "values (:r, 'qa seed', 'duckduckgo', :u, :t, :a)"
        ),
        {"r": request, "u": HIT_URL, "t": HIT_TITLE, "a": account},
    )
    linked = _insert_story(
        session, source_id, LINKED_TITLE, "story-linked", LINKED_AGE, (5, 2, "Opportunity")
    )
    quiet = _insert_story(
        session, source_id, QUIET_TITLE, "story-quiet", QUIET_AGE, (2, 0, "Market")
    )
    session.execute(
        text(
            "insert into signal (story_id, entity_type, entity_id, match_kind, tag, importance, "
            "impact, confidence, verify, action_hint, state) "
            "values (:s, 'account', :a, 'name_exact', 'Opportunity', 5, 2, 1, true, :h, 'new')"
        ),
        {"s": linked, "a": account, "h": f"Ask {COMPANY_NAME} about the new round."},
    )
    return account, [linked, quiet]
