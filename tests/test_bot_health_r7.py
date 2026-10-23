"""R7-H01: the M15 health card, the Sources & health Discord block, `saley bot status`.

All three read v_bot_health (docs/17 §10), so their numbers are the same.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from saley.bot import components
from saley.bot import health as bot_health
from saley.bot import words as bw
from saley.cli import app
from saley.core import clock
from saley.web import words as w
from saley.web.app import create_app
from tests.conftest import SignedClient, put_card


def _seed(db: Session) -> None:
    put_card(db, "m2-h1", {}, card_type="M2")
    db.execute(
        text("update bot_post set sent_at = now(), edited_at = now() where card_id = 'm2-h1'")
    )
    put_card(db, "m3-h2", {}, card_type="M3", state="deferred")
    db.execute(
        text(
            "insert into bot_interaction (card_id, action, surface, result, latency_ms) values "
            "('m2-h1', 'seen', 'discord', 'ok', 40), ('m2-h1', 'seen', 'discord', 'already', 60), "
            "('m2-h1', 'do', 'discord', 'refused', 80)"
        )
    )
    db.commit()


def _fields(card: dict) -> dict[str, str]:
    embed = card["message"]["embeds"][0]
    return {f["name"]: f["value"] for f in embed["fields"]}


def test_m15_is_green_when_ok_and_red_after_a_failed_job(db: Session) -> None:
    _seed(db)
    now = clock.now_ist()
    data = bot_health.health_data(db, now)
    assert (data["sent"], data["held"], data["failed"]) == (1, 1, 0)
    assert data["presses"] == {"ok": 1, "already": 1, "refused": 1}
    assert data["dms_off"] is True and data["listen"] != "—"
    from saley.bot import cards

    card = cards.render("M15", data, "m15-x")
    assert card["message"]["embeds"][0]["color"] == components.COLOURS["ok"]
    fields = _fields(card)
    assert fields[bw.FIELD_CARDS] == "1 sent · 1 held · 0 failed"
    assert fields[bw.FIELD_PRESSES] == "1 ok · 1 already · 1 refused"
    assert fields[bw.FIELD_DMS] == bw.DMS_OFF
    db.execute(
        text(
            "insert into job_runs (job, status, started_at, finished_at) "
            "values ('x', 'failed', now(), now())"
        )
    )
    db.commit()
    red = cards.render("M15", bot_health.health_data(db, now), "m15-y")
    assert red["message"]["embeds"][0]["color"] == components.COLOURS["cannot"]


def test_the_sources_block_and_bot_status_show_the_same_lines(db: Session) -> None:
    _seed(db)
    with SignedClient(create_app()) as client:
        health = client.get("/api/health/full")
    assert health.status_code == 200, health.text
    rows = {r["label"]: r["value"] for r in health.json()["discord"]["rows"]}
    assert rows[w.DISCORD_SENT] == "1" and rows[w.DISCORD_HELD] == "1"
    assert rows[w.DISCORD_PRESSES] == "1 ok · 1 already · 1 refused"
    assert rows[w.DISCORD_P50] == "60 ms" and rows[w.DISCORD_DMS] == w.DISCORD_OFF
    out = CliRunner().invoke(app, ["bot", "status"])
    assert out.exit_code == 0, out.output
    for label, value in rows.items():
        assert f"{label}: {value}" in out.output
    assert "Bot heartbeat:" in out.output


def test_the_health_card_is_queued_once_a_weekday(db: Session) -> None:
    stats = bot_health.queue_health_line(db)
    again = bot_health.queue_health_line(db)
    db.commit()
    weekday = clock.now_ist().weekday() < 5
    assert stats.queued == int(weekday) and again.queued == 0
    if weekday:
        row = db.execute(text("select card_type, shape, channel from bot_post")).one()
        assert tuple(row) == ("M15", bot_health.SHAPE, "test")
