"""R3 stage 5 fixes in saley.news (A1 to A7, B1 to B8, B11, B12). One regression test per finding.

No network: feeds come through an httpx MockTransport behind the real egress policy. LLM answers
come from a fake client. Tests that touch the clock pass `now` in; none sleeps.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from typing import Any

import anthropic
import httpx
import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.orm import Session

from saley.core.clock import now_ist, tz
from saley.core.db import session_scope
from saley.core.egress import make_client
from saley.core.models import (
    Account,
    NewsEdition,
    NewsSeen,
    NewsSent,
    NewsSource,
    NewsSourceRun,
    NewsStory,
    Signal,
    User,
    UserSeen,
)
from saley.news import clean, editions, fetch, health, jobs, scorer, signals, sources, store
from saley.news.clean import canonical_url, fingerprint, url_hash
from saley.news.fetch import FeedItem
from saley.qa.sheet_verify import FUNNEL_KEYS, FUNNEL_SQL
from tests.conftest import FixtureLLM
from tests.test_news import (
    GOOGLE_BROAD,
    OPENALEX_WORKS,
    Web,
    add_story,
    fetch_named,
    score_now,
    seed,
    sid,
    stories,
    three_stories,
)

HINDI_A = "भारत सरकार ने नई एआई नीति की घोषणा की"
HINDI_B = "दिल्ली में बारिश से सड़कें जलमग्न हुईं"


@pytest.fixture(autouse=True)
def _clean(db: Session) -> Iterator[None]:
    yield


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _s: None)


def ingest(items: list[FeedItem], *, now: datetime | None = None, name: str = GOOGLE_BROAD):
    with session_scope() as s:
        source = s.scalars(select(NewsSource).where(NewsSource.name == name)).one()
        return store.ingest_items(s, source, items, now=now or now_ist())


def down(_content: str) -> dict[str, Any]:
    raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://x.example"))


# ------------------------------------------------------------------ A1 fingerprint


def test_a_title_with_no_real_word_has_no_fingerprint() -> None:
    assert fingerprint("") == "" and fingerprint("the a of") == "" and fingerprint("!!! ???") == ""
    assert fingerprint(HINDI_A) != "" and fingerprint(HINDI_A) != fingerprint(HINDI_B)
    assert fingerprint("सरकार नीति घोषणा") == fingerprint("घोषणा नीति सरकार")  # order is free


def test_two_different_hindi_titles_do_not_merge(db: Session) -> None:
    seed()
    counts = ingest(
        [
            FeedItem(HINDI_A, "https://example.in/a", "", "NDTV"),
            FeedItem(HINDI_B, "https://example.in/b", "", "Aaj Tak"),
        ]
    )
    assert (counts.new, counts.dropped_copy) == (2, 0)
    assert len(stories(db)) == 2


def test_titles_with_no_word_at_all_never_merge_each_other(db: Session) -> None:
    seed()
    counts = ingest(
        [
            FeedItem("!!!", "https://example.in/1", "", "A"),
            FeedItem("???", "https://example.in/2", "", "B"),
        ]
    )
    assert (counts.new, counts.dropped_copy) == (2, 0)
    assert {s.fingerprint for s in stories(db)} == {
        f"url:{url_hash('https://example.in/1')}",
        f"url:{url_hash('https://example.in/2')}",
    }


def test_the_same_hindi_title_from_two_outlets_merges(db: Session) -> None:
    seed()
    counts = ingest(
        [
            FeedItem(HINDI_A, "https://ndtv.example/a", "", "NDTV"),
            FeedItem(HINDI_A, "https://aajtak.example/a", "", "Aaj Tak"),
        ]
    )
    assert (counts.new, counts.dropped_copy) == (1, 1)
    (story,) = stories(db)
    assert sorted(story.outlets) == ["Aaj Tak", "NDTV"]


# ------------------------------------------------------------------ B4 canonical url


def test_canonical_url_survives_a_bad_port_and_keeps_ipv6() -> None:
    assert canonical_url(" http://example.com:99999/x ") == "http://example.com:99999/x"
    assert url_hash("http://example.com:99999/x")  # no exception
    assert canonical_url("http://[2001:db8::1]:8080/a/") == "https://[2001:db8::1]:8080/a"
    assert canonical_url("http://[2001:db8::1]/a") == "https://[2001:db8::1]/a"


def test_source_output_and_oc_are_stripped_only_on_google() -> None:
    other = canonical_url("https://example.com/p?source=rss&output=1&oc=3&id=7")
    assert other == "https://example.com/p?id=7&oc=3&output=1&source=rss"
    google = canonical_url("https://news.google.com/rss/articles/x?oc=5&hl=en-IN&source=rss&id=7")
    assert google == "https://news.google.com/rss/articles/x?id=7"
    assert canonical_url("https://example.com/p?fbclid=1&utm_x=2") == "https://example.com/p"


# ------------------------------------------------------------------ A2 scorer


def test_a_network_error_carries_young_stories_to_the_next_edition(
    db: Session, set_env: Callable[..., None]
) -> None:
    set_env(ANTHROPIC_API_KEY="sk-test-not-real", SCORER_BATCH="1")
    three_stories()
    report = score_now(FixtureLLM(down))
    assert (report.scored_rules, report.carried, report.calls) == (0, 3, 1)
    assert report.llm_down and "LLMError" in report.notes
    assert [s.scored_at for s in stories(db)] == [None, None, None]


def test_a_story_older_than_the_carry_limit_gets_rules_and_waits_for_the_llm(
    db: Session, set_env: Callable[..., None]
) -> None:
    set_env(ANTHROPIC_API_KEY="sk-test-not-real")
    old = add_story("Old voice story", first_seen_at=now_ist() - timedelta(hours=20))
    young = add_story("Young voice story")
    report = score_now(FixtureLLM(down))
    assert (report.scored_rules, report.carried) == (1, 1)
    by_id = {s.id: s for s in stories(db)}
    assert (by_id[old].scorer_backend, by_id[old].rescore_due) == ("rules", True)
    assert by_id[young].scored_at is None


def test_bad_output_is_retried_once_then_rules_without_a_rescore(
    db: Session, set_env: Callable[..., None]
) -> None:
    set_env(ANTHROPIC_API_KEY="sk-test-not-real")
    add_story("Voice story one")
    bad = {"items": [{"id": 1}]}
    report = score_now(FixtureLLM([bad, bad]))
    assert (report.calls, report.scored_rules) == (2, 1)
    (story,) = stories(db)
    assert (story.scorer_backend, story.rescore_due) == ("rules", False)


def test_bad_output_then_a_good_answer_is_an_llm_score(
    db: Session, set_env: Callable[..., None]
) -> None:
    set_env(ANTHROPIC_API_KEY="sk-test-not-real")
    add_story("Voice story one")
    good = {"items": [{"id": 1, "importance": 3, "impact": 1, "tag": "Market", "why": ""}]}
    report = score_now(FixtureLLM([{"items": [{"id": 1}]}, good]))
    assert (report.calls, report.scored_llm, report.scored_rules) == (2, 1, 0)


def test_rules_scored_stories_are_rescored_by_the_llm_once(
    db: Session, set_env: Callable[..., None]
) -> None:
    set_env(ANTHROPIC_API_KEY="sk-test-not-real", LLM_DAILY_CALL_CAP="0")
    ids = three_stories()
    paused = score_now(FixtureLLM(["score_batch_3"]))
    assert paused.paused and paused.scored_rules == 3
    assert {(s.scorer_backend, s.rescore_due) for s in stories(db)} == {("rules", True)}
    with session_scope() as s:
        s.add(
            Signal(
                story_id=ids[0],
                entity_type="account",
                entity_id=1,
                match_kind="name_exact",
                importance=1,
                impact=0,
                tag="Market",
            )
        )
    set_env(LLM_DAILY_CALL_CAP="100")
    fake = FixtureLLM(["score_batch_3"])
    back = score_now(fake)
    assert (back.rescored, back.calls, back.scored_rules) == (3, 1, 0)
    rows = stories(db)
    assert {(s.scorer_backend, s.rescore_due) for s in rows} == {("llm", False)}
    db.expire_all()
    signal = db.scalars(select(Signal)).one()
    assert (signal.importance, signal.tag) == (rows[0].importance, rows[0].tag)  # follows
    again = score_now(fake)
    assert (again.rescored, again.calls) == (0, 0) and len(fake.calls) == 1  # never twice


def test_a_missing_key_gives_rules_then_the_key_brings_one_rescore(
    db: Session, set_env: Callable[..., None]
) -> None:
    set_env(ANTHROPIC_API_KEY="")
    three_stories()
    first = score_now()  # the real client factory refuses an empty key
    assert first.scored_rules == 3 and first.llm_down
    set_env(ANTHROPIC_API_KEY="sk-test-not-real")
    second = score_now(FixtureLLM(["score_batch_3"]))
    assert second.rescored == 3
    assert {s.scorer_backend for s in stories(db)} == {"llm"}


def test_a_transient_error_during_rescore_keeps_the_stories_due(
    db: Session, set_env: Callable[..., None]
) -> None:
    set_env(ANTHROPIC_API_KEY="sk-test-not-real", LLM_DAILY_CALL_CAP="0")
    three_stories()
    score_now(FixtureLLM(["score_batch_3"]))
    set_env(LLM_DAILY_CALL_CAP="100")
    score_now(FixtureLLM(down))
    assert {(s.scorer_backend, s.rescore_due) for s in stories(db)} == {("rules", True)}


def test_an_explicit_rules_backend_is_not_rescored(
    db: Session, set_env: Callable[..., None]
) -> None:
    set_env(SCORER_BACKEND="rules")
    three_stories()
    score_now()
    assert {s.rescore_due for s in stories(db)} == {False}


def test_edition_backend_lists_every_backend() -> None:
    both = scorer.ScoreReport(scored_llm=1, scored_rules=2)
    assert editions._backends(both) == "llm,rules"
    assert editions._backends(scorer.ScoreReport(scored_rules=1)) == "rules"
    assert editions._backends(scorer.ScoreReport()) is None and editions._backends(None) is None


def test_scorer_summary_for_health(db: Session, set_env: Callable[..., None]) -> None:
    set_env(ANTHROPIC_API_KEY="sk-test-not-real", LLM_DAILY_CALL_CAP="100")
    now = now_ist()
    empty = scorer_summary_now()
    assert empty["rules_share_24h"] is None and empty["last_backends"] == []
    add_story("A", importance=2, impact=0, tag="Market", scored_at=now, scorer_backend="rules")
    add_story("B", importance=2, impact=0, tag="Market", scored_at=now, scorer_backend="rules")
    add_story("C", importance=3, impact=1, tag="Market", scored_at=now, scorer_backend="llm")
    with session_scope() as s:
        s.add(NewsEdition(slot="12:00", family="news", scorer_backend="llm,rules"))
    summary = scorer_summary_now()
    assert (summary["scored_24h"], summary["rules_24h"]) == (3, 2)
    assert summary["rules_share_24h"] == 0.667
    assert summary["last_backends"] == ["llm", "rules"] and summary["paused"] is False
    set_env(LLM_DAILY_CALL_CAP="0")
    paused = scorer_summary_now()
    assert paused["paused"] and paused["paused_text"] == "LLM paused until tomorrow"


def scorer_summary_now() -> dict[str, Any]:
    with session_scope() as s:
        return scorer.scorer_summary(s, now_ist())


# ------------------------------------------------------------------ A4 edition and fetch


def test_an_error_while_storing_is_still_recorded(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed()

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("disk full at https://db.example/x?key=secret")

    monkeypatch.setattr(editions, "ingest_items", boom)
    report = fetch_named(Web(), GOOGLE_BROAD)
    assert report.status == "error" and report.error is not None
    assert report.error.startswith("RuntimeError: disk full") and "secret" not in report.error
    db.expire_all()
    run = db.scalars(select(NewsSourceRun)).one()
    assert (run.status, run.error) == ("error", report.error)
    source = db.get(NewsSource, sid(GOOGLE_BROAD))
    assert source is not None and source.fail_streak == 1 and source.backoff_until is not None
    assert source.last_error == report.error and stories(db) == []


def test_edition_status_comes_from_the_outcomes() -> None:
    ok = editions.SourceReport("a", "news", "ok")
    bad = editions.SourceReport("b", "news", "error")
    assert editions.EditionReport(1, "x", "news", [ok, ok]).status == "ok"
    assert editions.EditionReport(1, "x", "news", [ok, bad]).status == "partial"
    assert editions.EditionReport(1, "x", "news", [bad, bad]).status == "failed"
    assert editions.EditionReport(1, "x", "news", [ok], error="E").status == "failed"
    assert editions.EditionReport(1, "x", "news").status == "ok"


def test_a_failing_score_step_still_closes_the_edition(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed()

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("score exploded")

    monkeypatch.setattr(editions, "score_pending", boom)
    with pytest.raises(RuntimeError, match="score exploded"):
        editions.run_edition("12:00", "news", client=Web().client())
    db.expire_all()
    edition = db.scalars(select(NewsEdition)).one()
    assert edition.finished_at is not None and edition.status == "failed"
    assert edition.note is not None and edition.note.startswith("RuntimeError: score exploded")


def test_an_edition_whose_sources_all_failed_is_failed(db: Session) -> None:
    seed()
    web = Web()
    web.status["export.arxiv.org"] = 500
    report = editions.run_edition("10:00", "arxiv", client=web.client(), score=False)
    db.expire_all()
    assert report.status == "failed"
    assert db.scalars(select(NewsEdition.status)).one() == "failed"


# ------------------------------------------------------------------ A5, A6, B7, B12 retention


def old_scored_story(title: str, days: int, **fields: Any) -> int:
    return add_story(
        title,
        importance=2,
        impact=0,
        tag="Market",
        scored_at=now_ist() - timedelta(days=days),
        scorer_backend="llm",
        first_seen_at=now_ist() - timedelta(days=days),
        **fields,
    )


def test_cleanup_of_an_unscored_story_keeps_the_url_memory(db: Session) -> None:
    seed()
    web = Web()
    fetch_named(web, GOOGLE_BROAD, conditional=False)
    with session_scope() as s:
        s.execute(update(NewsStory).values(first_seen_at=now_ist() - timedelta(days=2)))
    with session_scope() as s:
        removed = store.cleanup(s, now_ist())
    assert removed["stories_unscored"] == 3 and removed["news_stories"] == 3
    assert stories(db) == []
    db.expire_all()
    seen = list(db.scalars(select(NewsSeen)))
    assert len(seen) == 3 and {r.story_id for r in seen} == {None}
    again = fetch_named(web, GOOGLE_BROAD, conditional=False)
    assert (again.counts.dropped_seen, again.counts.new) == (3, 0)


def test_the_funnel_still_has_its_runs_for_a_day_60_days_back(db: Session) -> None:
    seed()
    then = now_ist() - timedelta(days=60)
    items = [
        FeedItem(f"Voice model story {i}", f"https://example.com/{i}", "", "W") for i in range(3)
    ]
    with session_scope() as s:
        source = s.scalars(select(NewsSource).where(NewsSource.name == GOOGLE_BROAD)).one()
        store.ingest_items(s, source, items, now=then)
        s.add(NewsSourceRun(source_id=source.id, started_at=then, items_seen=5, items_new=3))
        s.execute(
            update(NewsStory).values(
                importance=2, impact=0, tag="Market", scored_at=then, scorer_backend="llm"
            )
        )
    with session_scope() as s:
        store.cleanup(s, now_ist())
    row = db.execute(
        text("select fetched, deduped from v_news_funnel_day where day = :d"),
        {"d": then.date()},
    ).one()
    assert row.fetched >= row.deduped == 3
    assert store.RUN_DAYS == store.STORY_DAYS == 90


def test_funnel_day_boundary_is_the_exact_ist_midnight(db: Session) -> None:
    seed()
    day = date(2026, 3, 10)
    last = datetime(2026, 3, 10, 23, 59, 59, tzinfo=tz())
    first = datetime(2026, 3, 11, 0, 0, 0, tzinfo=tz())
    for when, n in ((last, 1), (first, 2)):
        ingest([FeedItem(f"Voice model story {n}", f"https://example.com/{n}", "", "W")], now=when)
    with session_scope() as s:
        s.add(NewsSourceRun(source_id=sid(GOOGLE_BROAD), started_at=last, items_seen=1))
        s.add(NewsSourceRun(source_id=sid(GOOGLE_BROAD), started_at=first, items_seen=2))
    for d, deduped, fetched in ((day, 1, 1), (day + timedelta(days=1), 1, 2)):
        view = (
            db.execute(text("select * from v_news_funnel_day where day = :d"), {"d": d})
            .mappings()
            .one()
        )
        by_sql = {k: db.execute(text(FUNNEL_SQL[k]), {"d": d}).scalar_one() for k in FUNNEL_KEYS}
        assert (view["deduped"], view["fetched"]) == (deduped, fetched)
        assert {k: int(view[k]) for k in FUNNEL_KEYS} == {k: int(v) for k, v in by_sql.items()}


def test_new_indexes_exist_and_the_unused_one_is_gone(db: Session) -> None:
    names = set(db.scalars(text("select indexname from pg_indexes where schemaname = 'public'")))
    assert {
        "ix_search_request_account",
        "ix_news_dropped_source",
        "ix_search_hit_request",
        "ix_news_stories_first_source",
        "ix_news_stories_edition",
        "ix_news_stories_day_ist",
        "ix_news_source_runs_day_ist",
    } <= names
    assert "ix_user_seen_item" not in names


def test_cleanup_keeps_a_story_with_a_signal_a_person_acted_on(db: Session) -> None:
    acted = old_scored_story("Acted on story", 100)
    plain = old_scored_story("Plain old story", 100)
    with session_scope() as s:
        for story_id, state in ((acted, "in_action"), (plain, "new")):
            s.add(
                Signal(
                    story_id=story_id,
                    entity_type="account",
                    entity_id=1,
                    match_kind="name_exact",
                    importance=2,
                    state=state,
                )
            )
    with session_scope() as s:
        removed = store.cleanup(s, now_ist())
    assert removed["stories_old"] == 1
    assert [x.id for x in stories(db)] == [acted]
    assert "stories_old 0" in jobs.news_cleanup() and "user_seen 0" in jobs.news_cleanup()


def test_cleanup_prunes_user_seen_of_gone_items(synced: Session) -> None:
    user = synced.scalars(select(User.id)).first()
    assert user is not None
    live = old_scored_story("Still here", 10)
    now = now_ist()
    old = now - timedelta(days=120)
    with session_scope() as s:
        s.add_all(
            [
                UserSeen(user_id=user, item_type="story", item_id=live, seen_at=old),
                UserSeen(user_id=user, item_type="story", item_id=9999, seen_at=old),
                UserSeen(user_id=user, item_type="story", item_id=9998, seen_at=now),
                UserSeen(user_id=user, item_type="signal", item_id=9997, seen_at=old),
                UserSeen(user_id=user, item_type="suggest_newco", item_id=9996, seen_at=old),
            ]
        )
    with session_scope() as s:
        removed = store.cleanup(s, now)
    assert removed["user_seen"] == 2
    left = set(synced.scalars(select(UserSeen.item_id)))
    assert left == {live, 9998, 9996}
    synced.execute(text("delete from user_seen"))
    synced.commit()


def test_cleanup_keeps_a_stale_story_that_was_sent(db: Session) -> None:
    sent = add_story("Sent already", first_seen_at=now_ist() - timedelta(days=2))
    add_story("Not sent", first_seen_at=now_ist() - timedelta(days=2))
    with session_scope() as s:
        s.add(NewsSent(story_id=sent, channel="digest"))
    with session_scope() as s:
        removed = store.cleanup(s, now_ist())
    assert removed["stories_unscored"] == 1
    assert [x.id for x in stories(db)] == [sent]


# ------------------------------------------------------------------ A7 linking


def test_link_scored_still_reaches_a_new_story_after_500_scored_ones(db: Session) -> None:
    now = now_ist()
    filler = [
        {
            "title": f"Filler story {i}",
            "url": f"https://example.com/f{i}",
            "url_hash": f"h{i}",
            "fingerprint": f"f{i}",
            "importance": 2,
            "impact": 0,
            "tag": "Market",
            "scored_at": now - timedelta(hours=3),
            "scorer_backend": "llm",
            "first_seen_at": now - timedelta(hours=3, seconds=i),
        }
        for i in range(signals.MAX_STORIES + 1)
    ]
    with session_scope() as s:
        s.execute(insert(NewsStory), filler)
        s.add(Account(name="Acme Labs", name_key="acme", sheet_row_key="acc:acme"))
    fresh = add_story(
        "Acme Labs raises funding",
        importance=3,
        impact=1,
        tag="Opportunity",
        scored_at=now,
        scorer_backend="llm",
        first_seen_at=now,
    )
    with session_scope() as s:
        stats = signals.link_scored(s, now=now)
    assert stats.stories == signals.MAX_STORIES and stats.linked == 1
    db.expire_all()
    assert db.scalars(select(Signal.story_id)).all() == [fresh]


# ------------------------------------------------------------------ B1 streamed body cap


class Chunks(httpx.SyncByteStream):
    """A body that counts how many chunks the reader pulled."""

    def __init__(self, total: int, size: int) -> None:
        self.total, self.size, self.sent = total, size, 0

    def __iter__(self) -> Iterator[bytes]:
        for _ in range(self.total):
            self.sent += 1
            yield b"x" * self.size


def test_a_huge_feed_is_cut_while_it_streams(db: Session) -> None:
    seed()
    body = Chunks(total=200, size=100_000)  # 20 MB, no Content-Length

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=body)

    with make_client(inner=httpx.MockTransport(handler)) as client:
        report = editions.fetch_one(sid(GOOGLE_BROAD), client, limit=5)
    assert report.error == "body too large"
    assert body.sent <= fetch.MAX_BODY_BYTES // 100_000 + 2  # stopped near the cap, not at 200


def test_a_declared_size_over_the_cap_is_refused_before_reading(db: Session) -> None:
    seed()
    body = Chunks(total=1, size=10)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": "99999999"}, stream=body)

    with make_client(inner=httpx.MockTransport(handler)) as client:
        report = editions.fetch_one(sid(GOOGLE_BROAD), client, limit=5)
    assert report.error == "body too large" and body.sent == 0


def test_capped_requests_keep_status_headers_and_body(db: Session) -> None:
    from saley.core.egress import request_capped

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, headers={"x-a": "b"}, content=b"nope")

    with make_client(inner=httpx.MockTransport(handler)) as client:
        response = request_capped(client, "GET", "https://news.google.com/x", max_bytes=10)
    assert (response.status_code, response.headers["x-a"], response.content) == (404, "b", b"nope")


# ------------------------------------------------------------------ B3 race


def test_a_url_another_run_just_stored_is_dropped_as_seen(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed()
    link = "https://example.com/race"
    with session_scope() as s:
        s.add(NewsSeen(url_hash=url_hash(link), fingerprint="f", seen_at=now_ist()))
    real_get = Session.get

    def blind(self: Session, entity: Any, key: Any, *a: Any, **k: Any) -> Any:
        return None if entity is NewsSeen else real_get(self, entity, key, *a, **k)

    monkeypatch.setattr(Session, "get", blind)  # the check missed it: the insert must not crash
    counts = ingest([FeedItem("Voice model race story", link, "", "W")])
    assert (counts.new, counts.dropped_seen) == (0, 1)
    assert stories(db) == []


def test_the_fingerprint_lock_is_held_until_the_transaction_ends(db: Session) -> None:
    from saley.core.db import rw_engine

    with session_scope() as s:
        assert store._recent_story(s, "fp-lock", now_ist()) is None
        with rw_engine().connect() as other:
            free = other.execute(
                text("select pg_try_advisory_xact_lock(hashtext('fp-lock'))")
            ).scalar_one()
            assert free is False  # the first transaction still holds it
    with rw_engine().connect() as other:
        assert other.execute(
            text("select pg_try_advisory_xact_lock(hashtext('fp-lock'))")
        ).scalar_one()


# ------------------------------------------------------------------ B5 midnight


def test_stories_use_the_run_start_even_when_the_fetch_crosses_midnight(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed()
    start = datetime(2026, 10, 19, 23, 59, 58, tzinfo=tz())
    ticks = iter(start + timedelta(seconds=3 * n) for n in range(50))
    monkeypatch.setattr(editions, "now_ist", lambda: next(ticks))
    fetch_named(Web(), GOOGLE_BROAD)
    db.expire_all()
    run = db.scalars(select(NewsSourceRun)).one()
    rows = stories(db)
    assert rows and run.finished_at is not None
    assert run.finished_at.astimezone(tz()).date() != run.started_at.astimezone(tz()).date()
    assert {r.first_seen_at for r in rows} == {run.started_at}


# ------------------------------------------------------------------ B8 stale sources


def add_run(source_id: int, days_ago: float, status: str = "ok", new: int = 0) -> None:
    with session_scope() as s:
        s.add(
            NewsSourceRun(
                source_id=source_id,
                started_at=now_ist() - timedelta(days=days_ago),
                status=status,
                items_new=new,
            )
        )


def test_stale_sources_and_304_forever(db: Session) -> None:
    seed()
    quiet, busy, frozen, young = (
        sid(GOOGLE_BROAD),
        sid("Google News · hires · IN"),
        sid("arXiv · cs.CL cs.AI cs.LG cs.CY eess.AS cs.SD"),
        sid(OPENALEX_WORKS),
    )
    for days in (5, 4, 1):
        add_run(quiet, days)  # answers ok, brings nothing for 5 days
    add_run(busy, 5)
    add_run(busy, 1, new=2)  # something new yesterday
    for days in (5, 4, 3.5, 1):
        add_run(frozen, days, status="304")
    add_run(young, 1)
    with session_scope() as s:
        found = {f.name: f for f in health.stale_sources(s, now_ist(), 3)}
    assert set(found) == {GOOGLE_BROAD, "arXiv · cs.CL cs.AI cs.LG cs.CY eess.AS cs.SD"}
    assert found[GOOGLE_BROAD].flags == (health.NO_NEW,) and found[GOOGLE_BROAD].quiet_days == 5
    assert found["arXiv · cs.CL cs.AI cs.LG cs.CY eess.AS cs.SD"].flags == (
        health.NO_NEW,
        health.NOT_MODIFIED,
    )
    with session_scope() as s:
        assert health.stale_sources(s, now_ist(), 6) == []


def test_family_freshness_is_per_family(db: Session) -> None:
    seed()
    web = Web()
    fetch_named(web, GOOGLE_BROAD)
    fetch_named(web, "arXiv · cs.CL cs.AI cs.LG cs.CY eess.AS cs.SD")
    with session_scope() as s:
        s.add(NewsEdition(slot="06:30", family="news", finished_at=now_ist(), status="ok"))
        fresh = health.family_freshness(s, now_ist())
    assert set(fresh) == set(sources.FAMILIES)
    assert (
        fresh["news"]["last_story_at"] is not None and fresh["news"]["last_edition_status"] == "ok"
    )
    assert fresh["arxiv"]["last_story_at"] is not None
    assert fresh["openalex"]["last_story_at"] is None
    assert fresh["openalex"]["hours_since_story"] is None


# ------------------------------------------------------------------ B11 openalex and errors


def test_openalex_window_starts_from_the_ist_day(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed()
    monkeypatch.setattr(fetch, "today_ist", lambda: date(2026, 1, 15))
    web = Web()
    fetch_named(web, OPENALEX_WORKS)
    assert "from_publication_date:2026-01-01" in web.requests[0].url.params["filter"]


def test_error_text_is_class_and_a_short_message_without_links() -> None:
    err = httpx.ConnectError("refused by https://api.example/x?mailto=a@b.c&key=1 now")
    assert sources.short_error(err) == "ConnectError: refused by <api.example> now"
    assert sources.short_error(RuntimeError("")) == "RuntimeError"
    assert len(sources.short_error(RuntimeError("x" * 500))) == len("RuntimeError: ") + 120


def test_the_stored_error_has_the_message(db: Session) -> None:
    seed()

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=req)

    with make_client(inner=httpx.MockTransport(handler)) as client:
        report = editions.fetch_one(sid(GOOGLE_BROAD), client, limit=5)
    assert report.error == "ConnectError: no route"


def test_title_words_keep_combining_marks_inside_the_word() -> None:
    assert clean.title_words("Café «Ünï» है") == ["café", "ünï", "है"]
