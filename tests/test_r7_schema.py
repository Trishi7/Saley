"""R7 T1: migrations 0027 and 0028, the press door and the card sync (R7-S01..S04).

Why: a press in Discord and a click on the dashboard must be ONE write (D24, D48, D259), a
non-writer must be refused inside SQL (D257), and a write or an undo on either surface must tell
the bot which card to edit (NOTIFY saley_card).
"""

from __future__ import annotations

import json
from typing import Any

import psycopg
import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from tests.conftest import contact_id, make_card, press, put_card, require_ro_role, sql_call

RESOLVE = "select saley_proposal_resolve(:u, :id, 'do', null, null, :s, 0, 5, false)"


def _decisions(db: Session, where: str, **params: Any) -> int:
    return int(db.execute(text(f"select count(*) from decision where {where}"), params).scalar())


def _commitment(db: Session, owner: str = "sid") -> int:
    row = db.execute(
        text(
            "insert into commitment (owner, text, due, state) "
            "select id, 'Send the deck', current_date + 1, 'open' from users where short = :o "
            "returning id"
        ),
        {"o": owner},
    ).scalar_one()
    db.commit()
    return int(row)


def _proposal_card(db: Session, card_id: str) -> int:
    pid = make_card(db)
    put_card(
        db,
        card_id,
        {"do": {"door": "resolve", "args": {"proposal_id": str(pid), "choice": "do"}}},
        [f"proposal:{pid}"],
    )
    return pid


# ------------------------------------------------------------------ R7-S04


def test_migrations_and_owners(db: Session, test_db_url: str) -> None:
    """New tables and views exist; the press door is a definer that PUBLIC may not run."""
    names = set(
        db.execute(
            text(
                "select table_name from information_schema.tables where table_schema = 'public' "
                "and table_name = any(:n)"
            ),
            {
                "n": [
                    "bot_interaction",
                    "chat_message",
                    "chat_summary",
                    "chat_fact",
                    "discord_event_link",
                    "answer_intent",
                    "v_bot_card",
                    "v_bot_press",
                    "v_chat_recent",
                ]
            },
        ).scalars()
    )
    assert len(names) == 9
    definer, public = db.execute(
        text(
            "select p.prosecdef, p.proacl is null or exists (select 1 from aclexplode(p.proacl) a "
            "where a.grantee = 0 and a.privilege_type = 'EXECUTE') from pg_proc p "
            "where p.proname = 'saley_card_press'"
        )
    ).one()
    assert definer and not public
    sync_definer = db.execute(
        text("select prosecdef from pg_proc where proname = 'saley_card_sync'")
    ).scalar_one()
    assert sync_definer is False  # runs as the door that wrote the decision


def test_web_role_reads_the_bot_views_only(db: Session) -> None:
    require_ro_role()
    grants = {
        (r[0], r[1])
        for r in db.execute(
            text(
                "select table_name, privilege_type from information_schema.role_table_grants "
                "where grantee = 'saley_ro' and table_name in "
                "('v_bot_card','v_bot_press','v_chat_recent','bot_post','bot_interaction',"
                "'chat_message')"
            )
        )
    }
    assert grants == {
        ("v_bot_card", "SELECT"),
        ("v_bot_press", "SELECT"),
        ("v_chat_recent", "SELECT"),
    }


def test_the_chain_downgrades_to_0026_and_comes_back(test_db_url: str) -> None:
    from saley.cli import alembic_config
    from saley.core.db import dispose_engines

    dispose_engines()
    cfg = alembic_config(test_db_url)
    command.downgrade(cfg, "0026")
    command.upgrade(cfg, "head")


def test_commitment_done_takes_the_discord_surface(synced: Session) -> None:
    cid = _commitment(synced)
    result = sql_call(synced, "select saley_commitment_done('sid', :c, 'discord', 5)", c=cid)
    assert result["ok"] is True
    assert _decisions(synced, "surface = 'discord' and row_key = :k", k=f"commitment:{cid}") == 1


# ------------------------------------------------------------------ R7-S01


@pytest.mark.parametrize("case", ["proposal", "proposal_dashboard_first", "commitment"])
def test_card_press_one_door(writers: Session, synced: Session, case: str) -> None:
    db = synced
    if case == "commitment":
        cid = _commitment(db)
        put_card(
            db,
            "m8-k1",
            {"done": {"door": "commitment_done", "args": {"commitment_id": cid}}},
            [f"commitment:{cid}"],
            card_type="M8",
        )
        assert press(db, "m8-k1", "sid", "done")["result"] == "ok"
        again = sql_call(db, "select saley_commitment_done('sid', :c, 'dashboard', 5)", c=cid)
        assert again["ok"] and again["already"]
        assert (
            _decisions(db, "kind = 'commitment_done' and row_key = :k", k=f"commitment:{cid}") == 1
        )
        return
    pid = _proposal_card(db, "m10-p1")
    if case == "proposal":
        first = press(db, "m10-p1", "sid", "do")
        assert first["result"] == "ok" and first["write_id"]
        second = sql_call(db, RESOLVE, u="vaishnavi", id=pid, s="dashboard")
        assert second == {"ok": False, "code": "already", "by": "Sid", "at": second["at"]}
    else:
        assert sql_call(db, RESOLVE, u="vaishnavi", id=pid, s="dashboard")["ok"] is True
        late = press(db, "m10-p1", "sid", "do")
        assert late["result"] == "already" and late["by"] == "Vaishnavi"
    assert _decisions(db, "proposal_id = :p and kind = 'column_write'", p=pid) == 1
    presses = db.execute(text("select result from bot_interaction where card_id = 'm10-p1'"))
    assert [r[0] for r in presses] == (["ok"] if case == "proposal" else ["already"])


# ------------------------------------------------------------------ R7-S02


def test_non_writer_refused_in_sql(writers: Session, synced: Session) -> None:
    """kushal is an approver on the dashboard, but not a Discord writer (D257)."""
    pid = _proposal_card(synced, "m10-p2")
    result = press(synced, "m10-p2", "kushal", "do")
    assert result["result"] == "refused" and result["code"] == "not_writer"
    assert _decisions(synced, "proposal_id = :p", p=pid) == 0
    state = synced.execute(text("select state from proposal where id = :p"), {"p": pid}).scalar()
    assert state == "open"
    row = synced.execute(
        text("select result, code from bot_interaction where card_id = 'm10-p2'")
    ).one()
    assert tuple(row) == ("refused", "not_writer")


def test_unknown_user_and_bad_input_are_refused(writers: Session, synced: Session) -> None:
    _proposal_card(synced, "m10-p3")
    assert press(synced, "m10-p3", "nobody", "do")["result"] == "refused"
    assert press(synced, "m10-p3", "sid", "launch")["code"] == "bad_action"
    assert press(synced, "no-such-card", "sid", "do")["code"] == "not_found"
    bad = sql_call(synced, "select saley_card_press('m10-p3', 'sid', 'do', '{}', 'dashboard')")
    assert bad["code"] == "bad_request"
    upper = sql_call(synced, "select saley_card_press('M10 p3', 'sid', 'do', '{}', 'discord')")
    assert upper["code"] == "bad_request"


# ------------------------------------------------------------------ every door


def test_doors_without_a_writer(writers: Session, synced: Session) -> None:
    """Seen, close and the answer of the person who was asked need no writer (Q-R7-1)."""
    db = synced
    put_card(
        db, "m2-s1", {"seen": {"door": "mark_seen", "args": {"item_type": "event", "ids": [7]}}}
    )
    assert press(db, "m2-s1", "kushal", "seen")["result"] == "ok"
    assert db.execute(text("select count(*) from user_seen where item_id = 7")).scalar() == 1
    put_card(db, "m3-x1", {"skip": {"door": "close", "args": {}}}, card_type="M3")
    assert press(db, "m3-x1", "trishi", "skip")["result"] == "ok"
    ask = db.execute(
        text(
            "insert into ask (to_user, question) select id, 'Who leads the pilot?' from users "
            "where short = 'kushal' returning id"
        )
    ).scalar_one()
    db.commit()
    args = {"kind": "answer", "ask_id": ask, "entity_type": "ask", "entity_id": ask}
    put_card(db, "m14-a1", {"answer": {"door": "fact", "args": args}}, card_type="M14")
    assert press(db, "m14-a1", "trishi", "answer", text="Priya")["code"] == "not_asked"
    assert press(db, "m14-a1", "kushal", "answer", text="Priya")["result"] == "ok"
    fact = db.execute(text("select source_type, author, value from fact")).one()
    assert fact[0] == "teammate" and fact[1] == "kushal" and fact[2] == {"text": "Priya"}
    assert db.execute(text("select answered_at is not null from ask")).scalar() is True


def test_meeting_status_from_a_list_and_undo(writers: Session, synced: Session) -> None:
    db = synced
    cid = contact_id(db, "Alex Search")
    row_key = db.execute(
        text("select sheet_row_key from contact where id = :c"), {"c": cid}
    ).scalar()
    args = {"targets": [cid], "role": "meeting_status", "values": ["Completed", "No show"]}
    put_card(
        db,
        "m6-c1",
        {"status": {"door": "act", "args": args}},
        [f"poc:{row_key}:meeting_status"],
        card_type="M6",
    )
    assert press(db, "m6-c1", "vaishnavi", "status", value="Rescheduled")["code"] == "bad_value"
    done = press(db, "m6-c1", "vaishnavi", "status", value="No show")
    assert done["result"] == "ok" and done["write_id"]
    card = db.execute(
        text("select done_by, done_surface from bot_post where card_id = 'm6-c1'")
    ).one()
    assert tuple(card) == ("Vaishnavi", "discord")
    assert press(db, "m6-c1", "sid", "status", value="Completed")["result"] == "already"
    assert press(db, "m6-c1", "vaishnavi", "undo")["result"] == "ok"
    reopened = db.execute(text("select done_at from bot_post where card_id = 'm6-c1'")).scalar()
    assert reopened is None
    assert press(db, "m6-c1", "vaishnavi", "undo")["code"] == "nothing_to_undo"


def test_undo_after_the_window_is_refused(writers: Session, synced: Session) -> None:
    pid = _proposal_card(synced, "m10-p4")
    assert press(synced, "m10-p4", "sid", "do")["result"] == "ok"
    synced.execute(text("update bot_post set undo_until = now() - interval '1 minute'"))
    synced.commit()
    late = press(synced, "m10-p4", "sid", "undo")
    assert late["result"] == "refused" and late["code"] == "window_closed" and late["at"]
    assert _decisions(synced, "proposal_id = :p and undone_at is null", p=pid) == 1


def test_commitment_date_next_step_fact_and_later(writers: Session, synced: Session) -> None:
    db = synced
    cid = _commitment(db)
    put_card(db, "m8-k2", {"move": {"door": "commitment_due", "args": {"commitment_id": cid}}})
    assert press(db, "m8-k2", "sid", "move", date="28 Oct")["code"] == "bad_date"
    assert press(db, "m8-k2", "sid", "move", date="2026-02-30")["code"] == "bad_date"
    assert press(db, "m8-k2", "vaishnavi", "move", date="2026-10-28")["code"] == "not_owner"
    assert press(db, "m8-k2", "sid", "move", date="2026-10-28")["result"] == "ok"
    due = db.execute(text("select due::text from commitment where id = :c"), {"c": cid}).scalar()
    assert due == "2026-10-28"

    person = contact_id(db, "Alex Search")
    step = {"door": "next_step", "args": {"targets": [person], "values": ["Completed"]}}
    put_card(db, "m6-c2", {"next": step}, card_type="M6")
    ok = press(
        db, "m6-c2", "vaishnavi", "next", text="Send pricing", date="2026-10-30", status="Completed"
    )
    assert ok["result"] == "ok"
    made = db.execute(
        text("select text, due::text, source_type from commitment where source_ref = 'm6-c2'")
    ).one()
    assert tuple(made) == ("Send pricing", "2026-10-30", "discord")

    note = {"door": "fact", "args": {"kind": "note", "entity_type": "contact", "entity_id": person}}
    put_card(db, "m5-c1", {"note": note}, card_type="M5")
    assert press(db, "m5-c1", "sid", "note", text="\x07bell")["code"] == "bad_value"
    assert press(db, "m5-c1", "sid", "note", text="Bring the eval plan")["result"] == "ok"

    put_card(db, "m7-c1", {"later": {"door": "later", "args": {"days": 7}}}, card_type="M7")
    assert press(db, "m7-c1", "vaishnavi", "later")["result"] == "ok"
    copies = db.execute(
        text(
            "select state, due_at > now() + interval '6 days', card_id, payload from bot_post "
            "where card_id like 'm7-c1-l%'"
        )
    ).all()
    assert [tuple(c[:2]) for c in copies] == [("queued", True)]
    copy_id, payload = copies[0][2], copies[0][3]
    assert "m7-c1" not in json.dumps(payload).replace(copy_id, "")  # every id is the copy's
    assert press(db, copy_id, "vaishnavi", "later")["result"] == "ok"  # the copy acts itself


# ------------------------------------------------------------------ R7-S03


def _listen(test_db_url: str) -> psycopg.Connection:
    u = make_url(test_db_url)
    conn = psycopg.connect(
        host=u.host,
        port=u.port or 5432,
        user=u.username,
        password=u.password,
        dbname=u.database,
        autocommit=True,
    )
    conn.execute("listen saley_card")
    return conn


def _heard(conn: psycopg.Connection) -> list[str]:
    return [n.payload for n in conn.notifies(timeout=1, stop_after=10)]


def test_notify_on_decision_and_undo(synced: Session, test_db_url: str) -> None:
    """A dashboard write and its undo each NOTIFY the card id; the card closes and opens."""
    db = synced
    pid = _proposal_card(db, "m10-p5")
    put_card(db, "m2-other", {}, ["proposal:999999"], card_type="M2")
    listener = _listen(test_db_url)
    try:
        result = sql_call(db, RESOLVE, u="vaishnavi", id=pid, s="dashboard")
        assert _heard(listener) == ["m10-p5"]
        card = db.execute(
            text(
                "select done_by, done_surface, done_write_id from bot_post where card_id = 'm10-p5'"
            )
        ).one()
        write_id = result["items"][0]["write_id"]
        assert tuple(card) == ("Vaishnavi", "dashboard", write_id)
        undo = sql_call(db, "select saley_undo_group('vaishnavi', :w, 'dashboard')", w=write_id)
        assert undo["ok"] is True
        assert _heard(listener) == ["m10-p5"]
        assert (
            db.execute(text("select done_at from bot_post where card_id = 'm10-p5'")).scalar()
            is None
        )
    finally:
        listener.close()


def test_a_cell_write_closes_only_cards_about_that_cell(synced: Session, test_db_url: str) -> None:
    db = synced
    cid = contact_id(db, "Alex Search")
    row_key = db.execute(
        text("select sheet_row_key from contact where id = :c"), {"c": cid}
    ).scalar()
    put_card(db, "m6-c3", {}, [f"poc:{row_key}:meeting_status"], card_type="M6")
    put_card(db, "m5-c3", {}, [f"poc:{row_key}:li_connected_date"], card_type="M5")
    listener = _listen(test_db_url)
    try:
        cells = [{"role": "meeting_status", "value": "Completed"}]
        db.execute(
            text(
                "select saley_act('sid', 'dashboard', 'write', cast(:t as jsonb), "
                "cast(:p as jsonb), :k, 0, 5, false)"
            ),
            {"t": json.dumps([cid]), "p": json.dumps({"cells": cells}), "k": "k" * 20},
        )
        db.commit()
        assert _heard(listener) == ["m6-c3"]
    finally:
        listener.close()


def test_a_pick_must_be_one_the_card_offers(writers: Session, synced: Session) -> None:
    """M11 take it (a short name) and M4 interest (an event id from the select)."""
    db = synced
    account = db.execute(text("select id from account order by id limit 1")).scalar_one()
    take = {"kind": "owner_claim", "entity_type": "account", "entity_id": account}
    put_card(
        db, "m11-a1", {"take": {"door": "fact", "args": {**take, "values": ["sid", "vaishnavi"]}}}
    )
    assert press(db, "m11-a1", "sid", "take", value="kushal")["code"] == "bad_value"
    assert press(db, "m11-a1", "sid", "take", value="vaishnavi")["result"] == "ok"
    pick = {"kind": "interest", "entity_type": "event", "text": "interest", "values": ["41", "42"]}
    put_card(db, "m4-w1", {"interest": {"door": "fact", "args": pick}}, card_type="M4")
    assert press(db, "m4-w1", "vaishnavi", "interest", value="43")["code"] == "bad_value"
    assert press(db, "m4-w1", "vaishnavi", "interest", value="42")["result"] == "ok"
    rows = db.execute(text("select entity_type, entity_id, value from fact order by id")).all()
    assert [tuple(r) for r in rows] == [
        ("account", account, {"text": "vaishnavi"}),
        ("event", 42, {"text": "interest"}),
    ]
