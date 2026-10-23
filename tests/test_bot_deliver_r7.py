"""R7-I02: card delivery on a fake Discord (httpx MockTransport; no socket opens).

7 people on one card: 5 in the card, 2 in a thread, none dropped. M12 is pinned and the old
pin removed. A Components v2 card carries the [TEST] tag in its first block. A card edit keeps
the embed and changes only the footer and the buttons; a deleted message makes the row `gone`.
"""

from __future__ import annotations

import itertools
import json
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.bot import cards, components, deliver, queue
from saley.bot import words as bw
from saley.bot.cards.samples import SAMPLES
from saley.core import clock, egress
from tests import test_bot as tb

bot_env = tb.bot_env
clean_posts = tb.clean_posts


class FakeDiscord:
    """Answers every call with a fresh 18-digit id; records (method, path, body)."""

    def __init__(self, status: dict[str, int] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.ids = itertools.count(900000000000000001)
        self.status = status or {}

    def handler(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else {}
        self.calls.append((req.method, req.url.path, body))
        code = self.status.get(req.method, 200)
        if code != 200:
            return httpx.Response(code, json={})
        if req.method in ("PUT", "DELETE"):
            return httpx.Response(204)
        return httpx.Response(200, json={"id": str(next(self.ids))})

    def client(self) -> httpx.Client:
        return egress.make_client(inner=httpx.MockTransport(self.handler))

    def posts(self, suffix: str = "/messages") -> list[dict[str, Any]]:
        return [b for m, p, b in self.calls if m == "POST" and p.endswith(suffix)]


def _ready(db: Session, card_type: str, card_id: str, data: dict[str, Any] | None = None) -> int:
    payload = cards.render(card_type, data or SAMPLES[card_type], card_id)
    pid = queue.insert_card(db, payload, due_at=clock.now_ist(), channel="sales")
    assert pid is not None
    db.execute(text("update bot_post set state = 'ready' where id = :i"), {"i": pid})
    db.commit()
    return pid


def _row(db: Session, pid: int) -> Any:
    db.expire_all()
    return db.execute(text("select * from bot_post where id = :i"), {"i": pid}).one()


def test_seven_people_five_in_the_card_two_in_the_thread(bot_env, clean_posts: Session) -> None:
    pid = _ready(clean_posts, "M3", "m3-seven")
    fake = FakeDiscord()
    stats = deliver.deliver_ready(client=fake.client())
    assert stats.sent == 1
    card, *thread_posts = fake.posts()
    assert card["content"].startswith("[TEST] ")
    names = [f["name"] for f in card["embeds"][0]["fields"]]
    assert len(names) == 5
    threads = [p for m, p, _ in fake.calls if p.endswith("/threads")]
    assert len(threads) == 1 and len(thread_posts) == 1
    rest = thread_posts[0]["content"]
    assert "Test Person Six" in rest and "Test Person Seven" in rest
    assert rest.startswith("[TEST] ") and thread_posts[0]["allowed_mentions"] == {"parse": []}
    row = _row(clean_posts, pid)
    assert row.state == "sent" and row.discord_message_id and row.thread_id
    assert row.discord_channel_id == int(tb.TEST_CH)  # TEST_MODE: the sales card goes to test


def test_the_pack_is_pinned_and_the_old_pin_removed(bot_env, clean_posts: Session) -> None:
    fake = FakeDiscord()
    old = _ready(clean_posts, "M12", "m12-old")
    deliver.deliver_ready(client=fake.client())
    new = _ready(clean_posts, "M12", "m12-new")
    deliver.deliver_ready(client=fake.client())
    old_row, new_row = _row(clean_posts, old), _row(clean_posts, new)
    puts = [p for m, p, _ in fake.calls if m == "PUT"]
    deletes = [p for m, p, _ in fake.calls if m == "DELETE"]
    assert puts[-1].endswith(f"/pins/{new_row.discord_message_id}")
    assert deletes == [
        f"/api/v10/channels/{old_row.discord_channel_id}/pins/{old_row.discord_message_id}"
    ]
    assert (old_row.pinned, new_row.pinned) == (False, True)


def test_components_v2_carry_the_tag_in_the_first_block(bot_env, clean_posts: Session) -> None:
    _ready(clean_posts, "M5", "m5-v2")
    fake = FakeDiscord()
    deliver.deliver_ready(client=fake.client())
    (body,) = fake.posts()
    assert body["flags"] == components.COMPONENTS_V2 and "content" not in body
    first = body["components"][0]
    assert first["type"] == components.V2_TEXT and first["content"].startswith("[TEST]")


def test_the_held_line_ends_the_card(bot_env, clean_posts: Session) -> None:
    pid = _ready(clean_posts, "M8", "m8-held")
    clean_posts.execute(
        text("update bot_post set held_reason = :h where id = :i"),
        {"h": "I am holding 2 more for Monday.", "i": pid},
    )
    clean_posts.commit()
    fake = FakeDiscord()
    deliver.deliver_ready(client=fake.client())
    assert fake.posts()[0]["content"].endswith("I am holding 2 more for Monday.")


def test_edit_keeps_the_embed_and_shows_who_did_it(bot_env, clean_posts: Session) -> None:
    pid = _ready(clean_posts, "M10", "m10-edit")
    fake = FakeDiscord()
    deliver.deliver_ready(client=fake.client())
    sent = fake.posts()[0]
    clean_posts.execute(
        text(
            "update bot_post set done_by = 'Sid', done_at = now(), done_surface = 'dashboard', "
            "done_write_id = 1, undo_until = now() + interval '4 minutes' where id = :i"
        ),
        {"i": pid},
    )
    clean_posts.commit()
    assert deliver.edit_card("m10-edit", client=fake.client()) == "edited"
    method, path, body = fake.calls[-1]
    row = _row(clean_posts, pid)
    assert method == "PATCH" and path.endswith(f"/messages/{row.discord_message_id}")
    footer = body["embeds"][-1]["footer"]["text"]
    assert footer.startswith("Done by Sid on the dashboard at ") and footer.endswith(
        "Undo 4 min left"
    )
    assert body["embeds"][0]["title"] == sent["embeds"][0]["title"]  # the embed stays
    labels = [b["label"] for b in components.buttons_of(body)]
    assert labels == [bw.BTN_UNDO, "Test Person Four's page"]
    # The undo window closes: the undo button goes, the footer loses the undo part.
    later = clock.now_ist() + timedelta(minutes=10)
    deliver.edit_card("m10-edit", client=fake.client(), now=later)
    body = fake.calls[-1][2]
    assert "Undo" not in body["embeds"][-1]["footer"]["text"]
    assert [b["label"] for b in components.buttons_of(body)] == ["Test Person Four's page"]


def test_a_deleted_message_makes_the_row_gone_with_no_repost(bot_env, clean_posts: Session) -> None:
    pid = _ready(clean_posts, "M9", "m9-gone")
    deliver.deliver_ready(client=FakeDiscord().client())
    gone = FakeDiscord(status={"PATCH": 404})
    assert deliver.edit_card("m9-gone", client=gone.client()) == "gone"
    assert _row(clean_posts, pid).state == "gone"
    assert deliver.edit_card("m9-gone", client=gone.client()) == "skipped"
    assert not gone.posts()  # never posted again


def test_a_thread_failure_never_resends_the_card(bot_env, clean_posts: Session) -> None:
    pid = _ready(clean_posts, "M13", "m13-fail", {"items": SAMPLES["M13"]["items"]})

    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if req.url.path.endswith("/threads"):
            return httpx.Response(500, json={})
        return httpx.Response(200, json={"id": "900000000000000099"})

    deliver.deliver_ready(client=egress.make_client(inner=httpx.MockTransport(handler)))
    assert _row(clean_posts, pid).state == "sent"
    assert sum(p.endswith("/messages") for p in calls) == 1


class EchoDiscord:
    """Keeps each posted or patched body and answers a GET with it, as Discord does."""

    def __init__(self, drop_field: bool = False) -> None:
        self.stored: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.drop_field = drop_field

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req.method)
        mid = "900000000000000777"
        if req.method == "POST":
            self.stored[mid] = json.loads(req.content)
            return httpx.Response(200, json={"id": mid})
        if req.method == "PATCH":  # Discord answers an edit with the edited message
            self.stored[mid] = json.loads(req.content)
            return httpx.Response(200, json={"id": mid, **self.stored[mid]})
        if req.method == "GET":
            body = json.loads(json.dumps(self.stored[mid]))
            if self.drop_field and body.get("embeds"):
                body["embeds"][0].pop("fields", None)  # Discord lost a part of the card
            return httpx.Response(200, json={"id": mid, **body})
        return httpx.Response(204)


def test_every_card_case_round_trips_offline(bot_env) -> None:
    from saley.bot import roundtrip

    bot_env(EGRESS_RATE_PER_HOST_PER_MIN="100000")  # 85 calls; the live run waits instead
    egress.reset_policy(egress.build_policy(recorder=None))

    for case in roundtrip.CARD_CASES:
        fake = EchoDiscord()
        result = roundtrip.run(
            case, client=egress.make_client(inner=httpx.MockTransport(fake.handler))
        )
        assert result.ok, case
        assert fake.calls == ["POST", "GET", "PATCH", "DELETE"], case


def test_a_card_that_comes_back_different_fails(bot_env) -> None:
    from saley.bot import roundtrip

    fake = EchoDiscord(drop_field=True)
    result = roundtrip.run("m8", client=egress.make_client(inner=httpx.MockTransport(fake.handler)))
    assert result.matched is True  # M8 has no fields: nothing to lose
    fake = EchoDiscord(drop_field=True)
    result = roundtrip.run(
        "m15", client=egress.make_client(inner=httpx.MockTransport(fake.handler))
    )
    assert result.matched is False and not result.ok and result.deleted
