"""R4 ticket 7: the instruction parser, reminders, notes, the card, the edit route and the grants.

Why: `tests/test_bot.py` holds the four matrix rows (R4-C02, C05, E01, E02). These tests cover the
edges around them: the closed grammar, who may do what, the buttons the card sends, and the
narrow door the dashboard editor uses (`POST /api/proposal/{id}/edit`, D129).
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import discord
import pytest
import structlog
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from saley.bot import card, deliver, instruct
from saley.bot import words as bw
from saley.bot.gateway import SaleyBot
from saley.bot.instruct import Instruction
from saley.bot.ping import Question
from saley.core import clock
from saley.core.db import rw_engine
from saley.core.settings import get_settings
from saley.web.app import create_app
from tests.conftest import (
    STRANGER_ID,
    TEAM_IDS,
    SignedClient,
    contact_id,
    make_card,
    require_ro_role,
)
from tests.test_bot import (
    TEST_CH,
    TUE,
    Recorder,
    at,
    interaction_double,
    outbox,
    queue_row,
    state_of,
    writes,
)

H = {"X-Saley-Act": "1"}
NOW = at(TUE, 12)  # Tuesday 20 Oct 2026, 12:00 IST


# ------------------------------------------------------------------ the grammar


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("remind me tomorrow to call Sid", Instruction("remind", when="tomorrow", body="call Sid")),
        (
            "Remind me in 2 days to send the deck",
            Instruction("remind", when="in 2 days", body="send the deck"),
        ),
        (
            "remind me friday to ask Sid to call Alex",
            Instruction("remind", when="friday", body="ask Sid to call Alex"),
        ),
        ("cancel reminder 7", Instruction("cancel", n=7)),
        ("approve 12", Instruction("approve", n=12)),
        ("APPROVE 12", Instruction("approve", n=12)),
        ("reject 12 because the call moved", Instruction("reject", n=12, body="the call moved")),
        ("reject 12", Instruction("reject", n=12)),
        (
            "note: uses vLLM about Alex Search",
            Instruction("note", body="uses vLLM", person="Alex Search"),
        ),
        (
            "note: asked about pricing about Alex Search.",
            Instruction("note", body="asked about pricing", person="Alex Search"),
        ),
    ],
)
def test_the_grammar_is_a_closed_parser(raw: str, want: Instruction) -> None:
    assert instruct.parse(raw) == want


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "approve",
        "approve x",
        "approve 12 now",
        "approve 12\nreject 13",
        "approve " + "9" * 30,
        "reject because",
        "remind me",
        "remind me tomorrow",
        "note: nothing",
        "cancel reminder",
        "who at Sarvam works on evals",
        "news since Wednesday",
        "please approve 12",
    ],
)
def test_anything_else_is_not_an_instruction(raw: str) -> None:
    assert instruct.parse(raw) is None


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("tomorrow", at(TUE + timedelta(days=1), 10)),
        ("Tomorrow", at(TUE + timedelta(days=1), 10)),
        ("today", at(TUE, 17)),
        ("friday", at(TUE + timedelta(days=3), 10)),
        ("on monday", at(TUE + timedelta(days=6), 10)),
        ("next tuesday", at(TUE + timedelta(days=7), 10)),  # the same day name is next week
        ("next friday", at(TUE + timedelta(days=10), 10)),  # the Friday of the week after
        ("next monday", at(TUE + timedelta(days=6), 10)),
        ("2027-12-01", None),  # past MAX_DAYS: the ISO form has the same limit
        ("in 3 days", at(TUE + timedelta(days=3), 10)),
        ("in 5 hours", at(TUE, 17)),
        ("2026-11-02", at(TUE + timedelta(days=13), 10)),
        ("in 0 days", None),
        ("in 400 days", None),
        ("in 100 hours", None),
        ("2026-13-40", None),
        ("soon", None),
        ("next year", None),
        ("", None),
    ],
)
def test_when_has_fixed_forms_and_is_never_guessed(raw: str, want: datetime | None) -> None:
    assert instruct.parse_when(raw, NOW) == want


# ------------------------------------------------------------------ reminders and notes


def test_remind_me_and_cancel_reminder_belong_to_the_author(team: Session) -> None:
    reply = say_at(team, "sid", "remind me tomorrow to call Sid back")
    assert reply == "Reminder 1 is set for Wed 21 Oct, from 10:00 IST."
    row = team.execute(
        text(
            "select r.text, r.fire_at, r.state, r.kind, u.short "
            "from reminder r join users u on u.id = r.for_user"
        )
    ).one()
    assert (row.text, row.fire_at, row.state, row.kind, row.short) == (
        "call Sid back",
        at(TUE + timedelta(days=1), 10),
        "pending",
        "due_day",
        "sid",
    )
    # Another person cannot cancel it: it is "not found" for them.
    assert say_at(team, "kushal", "cancel reminder 1") == "I do not have reminder 1 for you."
    assert team.execute(text("select state from reminder")).scalar_one() == "pending"
    assert say_at(team, "sid", "cancel reminder 1") == "Reminder 1 is cancelled."
    assert team.execute(text("select state from reminder")).scalar_one() == "skipped"
    assert say_at(team, "sid", "cancel reminder 1") == "I do not have reminder 1 for you."


def test_remind_me_refuses_a_bad_time_a_past_time_and_unsafe_text(team: Session) -> None:
    assert say_at(team, "sid", "remind me next year to call") == bw.REMIND_BAD_WHEN
    assert say_at(team, "sid", "remind me 2026-10-19 to call") == bw.REMIND_PAST
    mention = say_at(team, "sid", "remind me tomorrow to ping <@123456789012345678>")
    assert mention == bw.TEXT_MENTION
    assert say_at(team, "sid", "remind me tomorrow to tell @everyone") == bw.TEXT_MENTION
    assert say_at(team, "sid", "remind me tomorrow to " + "x" * 201) == bw.TEXT_LONG
    assert team.execute(text("select count(*) from reminder")).scalar_one() == 0


def test_note_becomes_a_fact_with_its_author(team: Session) -> None:
    reply = say_at(team, "kushal", "note: runs vLLM for serving about alex search")
    company = team.execute(
        text(
            "select a.name from contact c join account a on a.id = c.account_id "
            "where c.name = 'Alex Search'"
        )
    ).scalar_one()
    assert reply == f"Saved your note about Alex Search at {company}."
    fact = team.execute(
        text(
            "select entity_type, entity_id, kind, value, source_type, source_ref, author from fact"
        )
    ).one()
    alex = team.execute(text("select id from contact where name = 'Alex Search'")).scalar_one()
    assert (fact.entity_type, fact.entity_id, fact.kind, fact.author) == (
        "contact",
        alex,
        "note",
        "kushal",
    )
    assert (fact.value, fact.source_type, fact.source_ref) == (
        {"text": "runs vLLM for serving"},
        "teammate",
        "discord",
    )
    # The person is never guessed: an unknown name is a question back, nothing is written.
    assert say_at(team, "kushal", "note: hello about Nobody Here") == (
        "I do not know Nobody Here. Use the full name."
    )
    assert (
        say_at(team, "kushal", "note: hi <@123456789012345678> about Alex Search")
        == bw.TEXT_MENTION
    )
    assert team.execute(text("select count(*) from fact")).scalar_one() == 1


def test_only_the_instruct_list_may_remind_or_note(team: Session, set_env) -> None:
    set_env(DISCORD_INSTRUCT_IDS=TEAM_IDS["sid"])  # Kushal may approve but not instruct here
    expected = "Sorry, only Sid can ask me to do that. I changed nothing."
    assert say_at(team, "kushal", "remind me tomorrow to call") == expected
    assert say_at(team, "kushal", "note: x about Alex Search") == expected
    assert say_at(team, "kushal", "cancel reminder 1") == expected
    assert team.execute(text("select count(*) from reminder")).scalar_one() == 0
    assert team.execute(text("select count(*) from fact")).scalar_one() == 0
    # A stranger gets None: the question path answers them with one line and no data.
    assert (
        instruct.instruct_reply(team, STRANGER_ID, "remind me tomorrow to call", get_settings())
        is None
    )


def say_at(db: Session, who: str, raw: str) -> str | None:
    return instruct.instruct_reply(db, int(TEAM_IDS[who]), raw, get_settings(), now=NOW)


# ------------------------------------------------------------------ the gateway


def test_the_gateway_tries_an_instruction_before_the_question_engine(team: Session) -> None:
    pid = make_card(team)
    asked = Question(f"approve {pid}", int(TEAM_IDS["sid"]))
    assert SaleyBot._reply_text(asked).startswith(f"Done. Card {pid} is saved.")
    assert len(writes(team, pid)) == 1
    # A stranger's instruction is the one-line answer and writes nothing.
    other = make_card(team, "David Gamez")
    stranger = SaleyBot._reply_text(Question(f"approve {other}", STRANGER_ID))
    assert stranger == bw.UNKNOWN_AUTHOR
    assert writes(team, other) == [] and state_of(team, other) == "open"


# ------------------------------------------------------------------ the card


def test_card_text_is_a_fixed_template_with_the_draft_in_a_code_block(team: Session) -> None:
    pid = make_card(team, value="done `x`")
    body = card.render_card(card.load_card(team, pid))
    assert body.splitlines()[0] == f"Card {pid}: Meeting with Alex Search has passed"
    assert "Why: The status still says booked." in body
    assert body.endswith("```\ndone 'x'\n```")  # a backtick cannot close the block
    assert card.load_card(team, 99999) is None


def test_card_buttons_are_do_edit_skip_and_a_dashboard_link() -> None:
    [row] = card.components(42, "http://localhost:8090/")
    assert row["type"] == 1
    buttons = row["components"]
    assert [b.get("custom_id") for b in buttons] == [
        "card:42:do",
        "card:42:edit",
        "card:42:skip",
        None,
    ]
    assert [b["emoji"]["name"] for b in buttons[:3]] == ["✅", "✏️", "❌"]
    # D261: the link names the thing.
    assert [b["label"] for b in buttons] == ["Do it", "Edit", "Skip", "The Inbox"]
    assert buttons[3]["style"] == 5 and buttons[3]["url"] == "http://localhost:8090/"
    # The custom_id pattern is the one the gateway registers.
    assert all(re.fullmatch(card.ID_RE, b["custom_id"]) for b in buttons[:3])


def test_a_card_goes_through_bot_post_once_and_carries_its_buttons(team: Session, set_env) -> None:
    set_env(
        DISCORD_BOT_TOKEN="tkn",
        DISCORD_TEST_CHANNEL_ID=TEST_CH,
        DISCORD_SALES_CHANNEL_ID="200000000000000002",
    )
    pid = make_card(team)
    first = card.queue_card(team, pid, now=NOW)
    assert first is not None and card.queue_card(team, pid, now=NOW) is None  # once per card
    team.commit()
    row = team.execute(
        text("select shape, channel, state, item_type, item_id, body from bot_post")
    ).one()
    assert (row.shape, row.channel, row.state, row.item_type, row.item_id) == (
        "action_card",
        "sales",
        "queued",
        "proposal",
        pid,
    )
    team.execute(text("update bot_post set state = 'ready'"))
    plain = queue_row(team, shape="pack_summary", body="Weekly pack")
    team.commit()
    fake = Recorder()
    stats = deliver.deliver_ready(client=fake.client(), now=NOW)
    assert stats.sent == 2
    by_content = {req[2]["content"]: req[2] for req in fake.requests}
    sent = next(v for k, v in by_content.items() if "Meeting with" in k)
    assert sent["content"].startswith("[TEST] Card ")
    assert [b.get("custom_id") for b in sent["components"][0]["components"]][:3] == [
        f"card:{pid}:do",
        f"card:{pid}:edit",
        f"card:{pid}:skip",
    ]
    assert "components" not in by_content["[TEST] Weekly pack"] and plain
    # A closed card is not queued.
    team.execute(text("update proposal set state = 'dismissed'"))
    team.commit()
    assert card.queue_card(team, pid, now=NOW) is None


def test_do_it_press_and_skip_press_use_the_same_door(team: Session) -> None:
    settings = get_settings()
    one, two = make_card(team), make_card(team, "David Gamez")
    sid = int(TEAM_IDS["sid"])
    done = card.resolve_card(team, settings, sid, one, "do")
    assert done.saved and done.text.startswith(f"Done. Card {one} is saved.")
    skipped = card.resolve_card(team, settings, sid, two, "skip", reason="not now")
    assert skipped == card.CardReply(f"Skipped. Card {two} is closed.", True)
    assert [w.surface for w in writes(team)] == ["discord", "discord"]
    # A second press on a closed card names the first saver.
    again = card.resolve_card(team, settings, int(TEAM_IDS["kushal"]), one, "skip")
    assert re.fullmatch(r"Already done by Sid at \d\d:\d\d", again.text) and not again.saved
    # Trishi and a stranger are refused politely and nothing is written.
    third = make_card(team, "Sshubam Verma")
    for author in (int(TEAM_IDS["trishi"]), STRANGER_ID):
        assert not card.resolve_card(team, settings, author, third, "do").saved
    assert writes(team, third) == [] and state_of(team, third) == "open"
    with pytest.raises(ValueError):
        card.resolve_card(team, settings, sid, third, "drop table")


def test_a_card_without_a_cell_cannot_be_saved(team: Session) -> None:
    pid = make_card(team)
    team.execute(text("update proposal set payload = '{}'::jsonb"))
    team.commit()
    reply = card.resolve_card(team, get_settings(), int(TEAM_IDS["sid"]), pid, "do")
    assert reply == card.CardReply(f"Card {pid} has no text I can save.")
    assert writes(team) == []


def test_a_bad_cell_value_is_refused_by_sql_too(team: Session) -> None:
    """Defence in depth: a date card takes only an ISO date, even if the bot checks nothing."""
    pid = make_card(team, role="meeting_date", value="2026-10-30")
    reply = card.resolve_card(
        team, get_settings(), int(TEAM_IDS["sid"]), pid, "edit", edited="next week"
    )
    assert reply == card.CardReply(bw.CARD_BAD_VALUE)
    ok = card.resolve_card(
        team, get_settings(), int(TEAM_IDS["sid"]), pid, "edit", edited="2026-11-02"
    )
    assert ok.saved and writes(team, pid)[0].after == "2026-11-02"


# ------------------------------------------------------------------ POST /api/proposal/{id}/edit


@pytest.fixture
def web(team: Session):
    with SignedClient(create_app()) as client:
        yield client


def edit(client: TestClient, card_id: int | str, body: object = None, who: str = "sid", **headers):
    return client.post(
        f"/api/proposal/{card_id}/edit",
        json={"text": "booked for Friday"} if body is None else body,
        headers={**H, "X-Saley-As": who, **headers},
    )


def test_edit_route_saves_once_with_a_signature(web: TestClient, team: Session) -> None:
    pid = make_card(team)
    r = edit(web, pid)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"ok", "proposal_id", "already", "write_id", "undo_until", "label"}
    assert body["ok"] and body["proposal_id"] == pid and body["already"] is False
    assert body["label"].startswith("Saved: ")
    [row] = writes(team, pid)
    assert (row.kind, row.surface, row.short, row.after) == (
        "column_write",
        "dashboard",
        "sid",
        "booked for Friday",
    )
    assert outbox(team) == [("pending", "meeting_status", "booked for Friday")]
    assert state_of(team, pid) == "acted"


def test_edit_route_guards(web: TestClient, team: Session) -> None:
    pid = make_card(team)
    with TestClient(web.app) as bare:  # not signed in
        r = bare.post(f"/api/proposal/{pid}/edit", json={"text": "x"}, headers=H)
        assert r.status_code == 401 and r.json() == {"detail": "Sign in to use Saley."}
    assert web.post(f"/api/proposal/{pid}/edit", json={"text": "x"}).status_code == 403  # no header
    for evil in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
        assert edit(web, pid, **evil).status_code == 403
    team.execute(text("insert into users(short, name, role) values ('vee', 'Vee', 'viewer')"))
    team.commit()
    try:
        viewer = edit(web, pid, who="vee")
        assert viewer.status_code == 403 and viewer.json() == {
            "detail": "Only approvers can do this."
        }
    finally:
        team.execute(text("delete from users where short = 'vee'"))
        team.commit()
    assert edit(web, 999999).status_code == 404
    assert edit(web, 999999).json() == {"detail": "No such card."}
    bad_bodies = [
        {},
        {"text": ""},
        {"text": "   "},
        {"text": "x" * 201},
        {"text": 5},
        {"text": None},
        {"text": "ok", "role": "meeting_date"},
        {"text": "ok", "value": "x"},
        [1],
        "text",
    ]
    for body in bad_bodies:
        r = edit(web, pid, body)
        assert r.status_code == 422, body
    for value in ("=SUM(1)", "+1", "-1", "@x", "tab\there"):
        r = edit(web, pid, {"text": value})
        assert r.status_code == 422 and r.json() == {"detail": "That value is not allowed."}, value
    assert web.post(f"/api/proposal/{pid}/edit", content=b"not json", headers=H).status_code == 422
    assert edit(web, pid, {"text": "x" * 40_000}).status_code == 413
    assert web.post("/api/proposal/x/edit", json={"text": "x"}, headers=H).status_code == 422
    assert web.post("/api/proposal/0/edit", json={"text": "x"}, headers=H).status_code == 422
    for method in ("get", "put", "patch", "delete"):
        assert getattr(web, method)(f"/api/proposal/{pid}/edit").status_code in (404, 405)
    assert writes(team, pid) == [] and state_of(team, pid) == "open"  # nothing got through


def test_edit_route_second_save_is_409_and_a_closed_card_is_409(
    web: TestClient, team: Session
) -> None:
    pid = make_card(team)
    assert edit(web, pid).status_code == 200
    second = edit(web, pid, {"text": "other"}, who="kushal")
    assert second.status_code == 409
    assert re.fullmatch(r"Already done by Sid at \d\d:\d\d", second.json()["detail"])
    # An expired card has no first saver: it is "not open".
    expired = make_card(team, "David Gamez")
    team.execute(text("update proposal set state = 'expired' where id = :i"), {"i": expired})
    team.commit()
    closed = edit(web, expired)
    assert closed.status_code == 409 and closed.json() == {"detail": "That card is not open."}
    assert len(writes(team)) == 1


# ------------------------------------------------------------------ SQL function and grants


def test_resolve_function_is_definer_with_a_fixed_search_path(team: Session) -> None:
    row = team.execute(
        text(
            "select p.prosecdef, p.proconfig, "
            "exists (select 1 from aclexplode(p.proacl) a where a.grantee = 0) as public_exec "
            "from pg_proc p where p.proname = 'saley_proposal_resolve' "
            "and p.pronamespace = 'public'::regnamespace"
        )
    ).one()
    assert row.prosecdef is True and not row.public_exec
    assert any(c.startswith("search_path=public") and "pg_temp" in c for c in row.proconfig)
    body = team.execute(
        text("select prosrc from pg_proc where proname = 'saley_proposal_resolve'")
    ).scalar_one()
    assert "for update" in body  # the first saver wins by a row lock (D129)


def test_saley_ro_may_execute_the_resolve_function_and_nothing_more() -> None:
    require_ro_role()
    from sqlalchemy import create_engine

    from tests.conftest import RO_TEST_URL

    ro = create_engine(RO_TEST_URL["url"])
    try:
        with ro.connect() as conn:
            assert conn.execute(
                text(
                    "select has_function_privilege('saley_ro', "
                    "'saley_proposal_resolve(text,bigint,text,text,text,text,"
                    "integer,integer,boolean)', "
                    "'EXECUTE')"
                )
            ).scalar_one()
            assert not conn.execute(
                text("select has_table_privilege('saley_ro', 'proposal', 'SELECT, UPDATE')")
            ).scalar_one()
    finally:
        ro.dispose()


def test_act_functions_accept_the_discord_surface_and_refuse_others(team: Session) -> None:
    from tests.conftest import contact_id, sql_act

    alex = contact_id(team, "Alex Search")
    cell = [{"role": "li_connected_date", "value": "2026-10-19"}]
    ok = sql_act(team, "sid", cell, [alex], surface="discord")
    assert ok["ok"] is True
    write_id = ok["items"][0]["write_id"]
    undone = team.execute(
        text("select saley_undo('sid', :i, 'discord')"), {"i": write_id}
    ).scalar_one()
    assert undone["ok"] is True
    bad = sql_act(team, "sid", cell, [alex], surface="telegram")
    assert bad == {"ok": False, "code": "bad_request"}
    resolve = "select saley_proposal_resolve('sid', 1, 'do', null, null, :s, 2, 10, false)"
    assert team.execute(text(resolve), {"s": "chat"}).scalar_one() == {
        "ok": False,
        "code": "bad_request",
    }


def test_dashboard_do_it_waits_for_the_card_lock(team: Session) -> None:
    """`saley_act` takes the proposal row lock, so a click cannot slip past a Discord save."""
    body = team.execute(
        text(
            "select pg_get_functiondef('saley_act(text,text,text,jsonb,jsonb,text,"
            "integer,integer,boolean)'::regprocedure)"
        )
    ).scalar_one()
    assert "for update" in body and "'discord'" in body


def test_json_payload_of_a_card_is_data_not_instructions(team: Session) -> None:
    """A card draft that looks like an instruction is saved as plain cell text, nothing more."""
    pid = make_card(team, value="approve 5")
    draft, _ = card.edit_draft(team, get_settings(), int(TEAM_IDS["sid"]), pid)
    assert draft == "approve 5"
    body = card.render_card(card.load_card(team, pid))
    assert instruct.parse(body) is None  # the card text parses as nothing


# ------------------------------------------------------------------ review fixes (ticket 7)


def sid_id() -> int:
    return int(TEAM_IDS["sid"])


def act_click(client: TestClient, person: int, card_id: int, who: str, value: str = "completed"):
    """The dashboard "Do it" of a card: POST /api/act with the card id in the payload."""
    body = {
        "action": "write",
        "target_ids": [person],
        "payload": {"role": "meeting_status", "value": value, "proposal_id": card_id},
    }
    return client.post("/api/act", json=body, headers={**H, "X-Saley-As": who})


def test_a_stale_dashboard_do_it_after_a_discord_skip_writes_nothing(
    web: TestClient, team: Session
) -> None:
    """D129: Skip on Discord, then a stale dashboard Do it. One decision, no outbox row."""
    pid = make_card(team)
    alex = contact_id(team, "Alex Search")
    assert card.resolve_card(team, get_settings(), sid_id(), pid, "skip").saved
    when = clock.hhmm(writes(team, pid)[0].at)
    late = act_click(web, alex, pid, "kushal")
    assert late.status_code == 409
    assert late.json() == {"detail": f"Already done by Sid at {when}"}
    assert [(w.kind, w.short) for w in writes(team, pid)] == [("dismiss", "sid")]
    assert outbox(team) == [] and state_of(team, pid) == "dismissed"


def test_a_discord_answer_after_a_dashboard_do_it_names_the_first_saver(
    web: TestClient, team: Session
) -> None:
    """The other order: the dashboard writes, then Skip and a second Do it are refused."""
    pid = make_card(team)
    alex = contact_id(team, "Alex Search")
    first = act_click(web, alex, pid, "kushal")
    assert first.status_code == 200 and first.json()["already"] is False
    when = clock.hhmm(writes(team, pid)[0].at)
    skipped = card.resolve_card(team, get_settings(), sid_id(), pid, "skip")
    assert skipped == card.CardReply(f"Already done by Kushal at {when}", closed=True)
    # Another value on an acted card is refused; the same instruction is the D48 no-op.
    other = act_click(web, alex, pid, "sid", value="something else")
    assert other.status_code == 409 and other.json() == {
        "detail": f"Already done by Kushal at {when}"
    }
    same = act_click(web, alex, pid, "kushal")
    assert same.status_code == 200 and same.json()["already"] is True
    assert [(w.kind, w.surface) for w in writes(team, pid)] == [("column_write", "dashboard")]
    assert len(outbox(team)) == 1


def test_a_press_that_cannot_get_the_card_lock_says_busy(team: Session) -> None:
    """The bot waits 2 s for the card row, then answers "Busy" (it was dead code before)."""
    pid = make_card(team)
    with rw_engine().connect() as holder:
        holder.execute(text("select id from proposal where id = :i for update"), {"i": pid})
        began = time.monotonic()
        try:
            reply = card.resolve_card(team, get_settings(), sid_id(), pid, "do")
            waited = time.monotonic() - began
        finally:
            holder.rollback()
    assert reply == card.CardReply(bw.CARD_BUSY)
    assert 1.5 < waited < 8
    assert writes(team, pid) == [] and state_of(team, pid) == "open"
    # The lock is free again: the same press now works.
    assert card.resolve_card(team, get_settings(), sid_id(), pid, "do").saved


# ---- the SQL door checks the value rules too


@pytest.mark.parametrize(
    "bad", ["=SUM(1)", "+1", "-1", "@x", "a\u202eb", "a\u2028b", "a\u2029b", "a\u200fb", "a\tb"]
)
def test_sql_refuses_formulas_controls_and_bidi_marks(team: Session, bad: str) -> None:
    pid = make_card(team)
    call = "select saley_proposal_resolve('sid', :i, 'edit', :t, null, 'discord', 2, 10, false)"
    result = team.execute(text(call), {"i": pid, "t": bad}).scalar_one()
    team.commit()
    assert result == {"ok": False, "code": "bad_value"}
    assert writes(team, pid) == [] and state_of(team, pid) == "open"


def test_sql_checks_dates_for_real_and_normalises_first_contact(
    web: TestClient, team: Session
) -> None:
    call = "select saley_proposal_resolve('sid', :i, 'edit', :t, null, 'discord', 2, 10, false)"
    day = make_card(team, role="meeting_date", value="2026-10-30")
    for bad in ("2026-02-30", "2026-13-01", "2026-1-1", "20261102"):
        assert team.execute(text(call), {"i": day, "t": bad}).scalar_one()["code"] == "bad_value"
    team.rollback()
    assert writes(team, day) == []
    assert team.execute(text(call), {"i": day, "t": "2026-11-02"}).scalar_one()["ok"] is True
    team.commit()
    # First Contact: "true" is saved as TRUE, the key the dashboard builds (D48): same write.
    david = make_card(team, "David Gamez", role="first_contact", value="true")
    assert card.resolve_card(team, get_settings(), sid_id(), david, "do").saved
    assert writes(team, david)[0].after == "TRUE"
    body = {
        "action": "write",
        "target_ids": [contact_id(team, "David Gamez")],
        "payload": {"role": "first_contact", "value": "true", "proposal_id": david},
    }
    again = web.post("/api/act", json=body, headers={**H, "X-Saley-As": "sid"})
    assert again.status_code == 200 and again.json()["already"] is True
    assert len(writes(team, david)) == 1


@pytest.mark.parametrize(
    "bad", ["a\u202eb", "a\u2028b", "a\u200fb", "=1", "+1", "@a", "-1", "x" * 201]
)
def test_clean_text_has_the_same_rules_as_sql(bad: str) -> None:
    assert card.clean_text(bad) is None


def test_clean_text_keeps_plain_text() -> None:
    assert card.clean_text("  booked for Friday  ") == "booked for Friday"
    assert card.clean_text("x" * 200) == "x" * 200


# ---- replies and logs


def test_describe_never_says_done_for_an_empty_result() -> None:
    with structlog.testing.capture_logs() as logs:
        empty = card.describe({"ok": True, "items": []}, 5, "do")
        missing = card.describe({"ok": True}, 5, "edit")
    assert empty == missing == card.CardReply(bw.CARD_FAILED)
    assert [entry["event"] for entry in logs] == ["card_result_no_items"] * 2


def test_describe_logs_a_code_it_does_not_know_and_words_not_allowed() -> None:
    with structlog.testing.capture_logs() as logs:
        reply = card.describe({"ok": False, "code": "surprise"}, 5, "do")
    assert reply == card.CardReply(bw.CARD_FAILED)
    assert [(e["event"], e["log_level"], e["code"]) for e in logs] == [
        ("card_unmapped_code", "warning", "surprise")
    ]
    assert card.describe({"ok": False, "code": "not_allowed"}, 5, "do") == card.CardReply(
        bw.CARD_NOT_ALLOWED
    )
    assert card.describe({"ok": False, "code": "already"}, 5, "do").text == (
        "Already done by ? at —"
    )


def test_a_database_fault_is_logged_with_its_traceback(team: Session, monkeypatch) -> None:
    pid = make_card(team)
    real = team.execute

    def broken(statement, *args, **kwargs):
        if "saley_proposal_resolve" in str(statement):
            raise DBAPIError("select", {}, Exception("boom"))
        return real(statement, *args, **kwargs)

    monkeypatch.setattr(team, "execute", broken)
    with structlog.testing.capture_logs() as logs:
        reply = card.resolve_card(team, get_settings(), sid_id(), pid, "do")
    assert reply == card.CardReply(bw.CARD_FAILED)
    [failed] = [e for e in logs if e["event"] == "card_resolve_failed"]
    assert failed["exc_info"] is True and failed["card"] == pid


def test_moment_reads_a_naive_time_as_utc_and_refuses_the_rest() -> None:
    assert card._moment("2026-10-20T12:00:00+00:00") == "17:30"
    assert card._moment("2026-10-20T12:00:00+05:30") == "12:00"
    assert card._moment("2026-10-20T12:00:00") == "17:30"  # naive: the database zone is UTC
    assert card._moment(datetime(2026, 10, 20, 12, 0)) == "17:30"
    assert card._moment("not a time") == "—"
    assert card._moment(None) == "—"
    assert card._moment(5) == "—"


# ---- the Discord buttons, with a mocked interaction (no Discord)


def http_error() -> discord.HTTPException:
    return discord.HTTPException(SimpleNamespace(status=500, reason="boom"), "boom")


def press_button(who: str, pid: int, act: str, monkeypatch=None, order=None):
    button = card.CardButton(pid, act)
    interaction = interaction_double(who)
    if order is not None:
        interaction.response.defer.side_effect = lambda **_: order.append("defer")
    asyncio.run(button.callback(interaction))
    return interaction


def test_do_it_and_skip_defer_first_then_answer_in_a_follow_up(team: Session, monkeypatch) -> None:
    order: list[str] = []
    real = card._press

    def spy(author_id: int, proposal_id: int, act: str):
        order.append("db")
        return real(author_id, proposal_id, act)

    monkeypatch.setattr(card, "_press", spy)
    one, two = make_card(team), make_card(team, "David Gamez")
    done = press_button("sid", one, "do", order=order)
    assert order == ["defer", "db"]
    done.response.defer.assert_called_once_with(ephemeral=True)
    done.response.send_message.assert_not_awaited()
    args, kwargs = done.followup.send.await_args
    assert args[0].startswith(f"Done. Card {one} is saved.") and kwargs["ephemeral"] is False
    assert kwargs["allowed_mentions"].everyone is False
    done.message.edit.assert_awaited_once_with(view=None)
    skipped = press_button("kushal", two, "skip")
    args, kwargs = skipped.followup.send.await_args
    assert args[0] == f"Skipped. Card {two} is closed." and kwargs["ephemeral"] is False
    skipped.message.edit.assert_awaited_once_with(view=None)
    assert [(w.kind, w.surface, w.short) for w in writes(team)] == [
        ("column_write", "discord", "sid"),
        ("dismiss", "discord", "kushal"),
    ]


def test_a_reply_that_fails_is_logged_and_never_raised(team: Session) -> None:
    pid = make_card(team)
    button = card.CardButton(pid, "do")
    interaction = interaction_double("sid")
    interaction.followup.send.side_effect = http_error()
    with structlog.testing.capture_logs() as logs:
        asyncio.run(button.callback(interaction))  # must not raise
    [failed] = [e for e in logs if e["event"] == "card_reply_failed"]
    assert (failed["card"], failed["outcome"]) == (pid, "saved")
    assert len(writes(team, pid)) == 1  # the write happened; only the words were lost
    interaction.message.edit.assert_awaited_once_with(view=None)


def test_a_failed_defer_writes_nothing(team: Session, monkeypatch) -> None:
    pid = make_card(team)
    monkeypatch.setattr(card, "_press", lambda *a: pytest.fail("db work after a failed defer"))
    interaction = interaction_double("sid")
    interaction.response.defer.side_effect = http_error()
    with structlog.testing.capture_logs() as logs:
        asyncio.run(card.CardButton(pid, "do").callback(interaction))
    assert [e["event"] for e in logs] == ["card_defer_failed"]
    interaction.followup.send.assert_not_awaited()
    assert writes(team, pid) == []


def test_buttons_go_when_a_card_is_already_done_or_closed(team: Session) -> None:
    pid = make_card(team)
    assert card.resolve_card(team, get_settings(), sid_id(), pid, "do").saved
    late = press_button("kushal", pid, "skip")
    args, kwargs = late.followup.send.await_args
    assert re.fullmatch(r"Already done by Sid at \d\d:\d\d", args[0]) and kwargs["ephemeral"]
    late.message.edit.assert_awaited_once_with(view=None)
    # A refusal for a person who may not press leaves the buttons for the others.
    fresh = make_card(team, "David Gamez")
    refused = press_button("trishi", fresh, "do")
    assert refused.followup.send.await_args.kwargs["ephemeral"] is True
    refused.message.edit.assert_not_awaited()
    assert writes(team, fresh) == []
    # A card that is gone: "not open" and no buttons.
    team.execute(text("update proposal set state = 'expired' where id = :i"), {"i": fresh})
    team.commit()
    gone = press_button("sid", fresh, "do")
    assert gone.followup.send.await_args.args[0] == f"Card {fresh} is not open."
    gone.message.edit.assert_awaited_once_with(view=None)


def test_edit_opens_the_modal_as_the_first_answer(team: Session) -> None:
    pid = make_card(team, value="the draft")
    opened = press_button("sid", pid, "edit")
    opened.response.defer.assert_not_awaited()
    opened.response.send_modal.assert_awaited_once()
    [modal] = opened.response.send_modal.await_args.args
    assert isinstance(modal, card.EditModal) and modal.draft.default == "the draft"
    # A person who may not edit gets a private line, not a modal.
    refused = press_button("trishi", pid, "edit")
    refused.response.send_modal.assert_not_awaited()
    args, kwargs = refused.response.send_message.await_args
    assert args[0].startswith("Sorry, only") and kwargs["ephemeral"] is True
    # A closed card: private line, and the buttons are removed.
    assert card.resolve_card(team, get_settings(), sid_id(), pid, "skip").saved
    shut = press_button("sid", pid, "edit")
    assert shut.response.send_message.await_args.args[0] == f"Card {pid} is not open."
    shut.message.edit.assert_awaited_once_with(view=None)
    assert writes(team, pid)[0].kind == "dismiss"


def test_the_modal_reply_failure_is_logged(team: Session) -> None:
    pid = make_card(team)
    interaction = interaction_double("sid")
    interaction.response.send_modal.side_effect = http_error()
    with structlog.testing.capture_logs() as logs:
        asyncio.run(card.CardButton(pid, "edit").callback(interaction))
    assert [(e["event"], e["outcome"]) for e in logs] == [("card_reply_failed", "modal")]


# ---- reminders


def test_the_reminder_reply_does_not_promise_an_exact_time(team: Session) -> None:
    reply = say_at(team, "sid", "remind me friday to send the deck")
    assert reply == "Reminder 1 is set for Fri 23 Oct, from 10:00 IST."
    assert len(reply) <= 60


def test_a_person_may_have_20_reminders_waiting(team: Session) -> None:
    for n in range(20):
        assert say_at(team, "sid", f"remind me tomorrow to task {n}").startswith("Reminder ")
    assert say_at(team, "sid", "remind me tomorrow to one more") == (
        "You have 20 reminders waiting. Cancel one first."
    )
    assert say_at(team, "kushal", "remind me tomorrow to one more").startswith("Reminder ")
    assert say_at(team, "sid", "cancel reminder 1") == "Reminder 1 is cancelled."
    assert say_at(team, "sid", "remind me tomorrow to one more").startswith("Reminder ")


def test_the_same_reminder_twice_is_one_reminder(team: Session) -> None:
    assert say_at(team, "sid", "remind me tomorrow to call Sid").startswith("Reminder 1")
    assert say_at(team, "sid", "remind me tomorrow to call Sid") == bw.REMIND_DUPLICATE
    assert say_at(team, "kushal", "remind me tomorrow to call Sid").startswith("Reminder ")
    assert say_at(team, "sid", "remind me friday to call Sid").startswith("Reminder ")
    assert team.execute(text("select count(*) from reminder")).scalar_one() == 3


def test_a_far_iso_date_is_refused_like_in_n_days(team: Session) -> None:
    assert say_at(team, "sid", "remind me 2027-12-01 to call") == bw.REMIND_BAD_WHEN
    assert say_at(team, "sid", "remind me in 400 days to call") == bw.REMIND_BAD_WHEN
    assert say_at(team, "sid", "remind me 2026-12-01 to call").startswith("Reminder ")


def test_next_day_is_the_week_after_not_the_nearest_day(team: Session) -> None:
    assert say_at(team, "sid", "remind me next friday to x") == (
        "Reminder 1 is set for Fri 30 Oct, from 10:00 IST."
    )
    assert say_at(team, "sid", "remind me friday to y") == (
        "Reminder 2 is set for Fri 23 Oct, from 10:00 IST."
    )


# ---- near misses go to a hint, not to the question engine


@pytest.mark.parametrize(
    ("raw", "hint"),
    [
        ("approve", bw.HINT_APPROVE),
        ("approve the one for Alex", bw.HINT_APPROVE),
        ("approve 12 now", bw.HINT_APPROVE),
        ("reject", bw.HINT_REJECT),
        ("Reject 12 because", bw.HINT_REJECT),
        ("remind me", bw.HINT_REMIND),
        ("remind me tomorrow", bw.HINT_REMIND),
        ("cancel", bw.HINT_CANCEL),
        ("cancel the reminder", bw.HINT_CANCEL),
        ("note: nothing", bw.HINT_NOTE),
        ("note:uses vLLM", bw.HINT_NOTE),
    ],
)
def test_a_near_miss_gets_a_one_line_hint(team: Session, raw: str, hint: str) -> None:
    assert say_at(team, "sid", raw) == hint
    assert "\n" not in hint and len(hint) <= 60
    asked = Question(raw, sid_id())
    assert SaleyBot._reply_text(asked) == hint  # the gateway never falls through to the engine


def test_hints_follow_the_allowlists_and_plain_questions_pass_through(team: Session) -> None:
    assert say_at(team, "trishi", "approve") == bw.NOT_APPROVER.format(
        names="Kushal, Sid and Vaishnavi"
    )
    assert say_at(team, "trishi", "remind me") == bw.HINT_REMIND  # Trishi may instruct
    assert instruct.instruct_reply(team, STRANGER_ID, "approve", get_settings(), now=NOW) is None
    for plain in ("who at Sarvam works on evals", "please remind me later", "notes on Alex"):
        assert say_at(team, "sid", plain) is None


# ---- notes


def test_a_note_names_the_company_and_a_second_copy_the_same_day_is_refused(team: Session) -> None:
    company = team.execute(
        text(
            "select a.name from contact c join account a on a.id = c.account_id "
            "where c.name = 'Alex Search'"
        )
    ).scalar_one()
    raw = "note: runs vLLM in production about Alex Search"
    assert say_at(team, "kushal", raw) == f"Saved your note about Alex Search at {company}."
    assert say_at(team, "sid", raw) == "I already have that note for Alex Search today."
    assert team.execute(text("select count(*) from fact")).scalar_one() == 1
    # A new day is a new note; a different text the same day is too.
    tomorrow = instruct.instruct_reply(
        team, int(TEAM_IDS["kushal"]), raw, get_settings(), now=NOW + timedelta(days=1)
    )
    assert tomorrow.startswith("Saved your note")
    assert say_at(team, "kushal", "note: asked for a demo about Alex Search").startswith("Saved")
    assert team.execute(text("select count(*) from fact")).scalar_one() == 3


# ---- the migration is strict in both directions


def test_the_downgrade_asserts_every_pattern_like_the_upgrade() -> None:
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent / "migrations" / "versions" / "0022_card_resolve.py"
    )
    spec = importlib.util.spec_from_file_location("m0022", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    up, down = module._patch(module.PATCHED, module.SWAPS), module._unpatch()
    swaps = len(module.PATCHED) + 3  # the surface swap on four functions, three more on saley_act
    assert up.count("pattern not found") == down.count("pattern not found") == swaps
