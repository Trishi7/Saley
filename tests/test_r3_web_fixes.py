"""R3 pipeline stage 5, part 2: the web fixes (A1 to A6, A10, A12).

No network, no live LLM. Rows are inserted; the web role reads views only, so every check here
goes through the real routes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
import structlog
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from saley.core.clock import hhmm, now_ist, today_ist
from saley.core.models import (
    Account,
    LlmCall,
    NewsEdition,
    NewsSource,
    NewsSourceRun,
    SearchHit,
    SearchRequest,
)
from saley.intel import suggest
from saley.web import queries
from saley.web import signals as web_signals
from saley.web import words as w
from saley.web.app import create_app, days_text
from tests.conftest import SignedClient
from tests.test_signals import add_people, add_story, event, link_all, reset_events

H = {"X-Saley-Act": "1"}
SID = {"X-Saley-As": "sid"}
VAISHNAVI = {"X-Saley-As": "vaishnavi"}


@pytest.fixture
def client(synced: Session) -> Iterator[TestClient]:
    with SignedClient(create_app()) as c:
        yield c


def health_news(client: TestClient) -> dict[str, Any]:
    return client.get("/api/health/full").json()["news"]


def edition(db: Session, family: str, ago: timedelta, status: str = "ok") -> None:
    db.add(NewsEdition(slot="06:30", family=family, finished_at=now_ist() - ago, status=status))
    db.commit()


# ------------------------------------------------------------------ A1 health


def test_health_shows_the_share_scored_by_rules(client: TestClient, synced: Session) -> None:
    empty = health_news(client)["scorer"]
    assert empty["line"] == "Nothing scored in 24 h" and empty["rules_percent"] is None
    add_story(synced, "Rules story")
    llm_story = add_story(synced, "Model story")
    llm_story.scorer_backend = "llm"
    synced.commit()
    scorer = health_news(client)["scorer"]
    assert (scorer["scored_24h"], scorer["rules_24h"], scorer["rules_percent"]) == (2, 1, 50)
    assert scorer["line"] == "Scored by rules: 50 % in 24 h" and scorer["paused"] is False
    kinds = [line["kind"] for line in health_news(client)["watch"]]
    assert kinds[0] == "scorer"


def test_health_says_the_llm_is_paused_until_tomorrow(
    client: TestClient, synced: Session, set_env: Callable[..., None]
) -> None:
    set_env(LLM_DAILY_CALL_CAP="2")
    synced.add_all(LlmCall(job="t", model="m", input_hash=f"h{i}") for i in range(2))
    synced.commit()
    news = health_news(client)
    assert news["scorer"]["paused"] is True
    assert news["scorer"]["paused_text"] == "LLM paused until tomorrow"
    assert {"kind": "paused", "text": "LLM paused until tomorrow"} in news["watch"]
    # A cached answer or a failed call does not use the budget.
    synced.execute(text("update llm_calls set cached = true where input_hash = 'h0'"))
    synced.commit()
    assert health_news(client)["scorer"]["paused"] is False


def test_health_lists_sources_with_no_new_items(
    client: TestClient, synced: Session, set_env: Callable[..., None]
) -> None:
    set_env(NEWS_STALE_DAYS="3")
    quiet = NewsSource(name="Quiet lane", kind="google", url="https://news.example/q")
    busy = NewsSource(name="Busy lane", kind="google", url="https://news.example/b")
    frozen = NewsSource(
        name="Frozen feed", kind="rss", url="https://news.example/f", extractor="arxiv"
    )
    synced.add_all([quiet, busy, frozen])
    synced.flush()
    for days in (5, 1):
        synced.add(NewsSourceRun(source_id=quiet.id, started_at=now_ist() - timedelta(days=days)))
    synced.add(NewsSourceRun(source_id=busy.id, started_at=now_ist() - timedelta(days=5)))
    synced.add(
        NewsSourceRun(source_id=busy.id, started_at=now_ist() - timedelta(days=1), items_new=2)
    )
    for days in (5, 2):
        synced.add(
            NewsSourceRun(
                source_id=frozen.id, started_at=now_ist() - timedelta(days=days), status="304"
            )
        )
    synced.commit()
    stale = {s["name"]: s for s in health_news(client)["stale_sources"]}
    assert set(stale) == {"Quiet lane", "Frozen feed"}
    assert stale["Quiet lane"]["lines"] == ["Quiet lane: no new items for 5 days"]
    assert stale["Frozen feed"]["flags"] == ["no_new_items", "not_modified_forever"]
    assert stale["Frozen feed"]["family"] == "arxiv"
    texts = [line["text"] for line in health_news(client)["watch"] if line["kind"] == "stale"]
    assert "Frozen feed: only 304 answers. The feed may be frozen." in texts


def test_one_day_of_quiet_reads_as_one_day() -> None:
    assert w.fill_count(w.NEWS_STALE_NO_NEW_ONE, w.NEWS_STALE_NO_NEW, 1, name="X") == (
        "X: no new items for 1 day"
    )


def test_the_top_bar_goes_old_per_family_not_per_any_news_job(
    client: TestClient, synced: Session
) -> None:
    synced.execute(
        text(
            "insert into job_runs(job, status, started_at, finished_at) "
            "values ('news_edition_0630', 'ok', now(), now())"
        )
    )
    synced.commit()
    first = client.get("/api/status").json()
    assert first["news"] == "—" and first["news_old"] == []  # no edition ran: a dash
    assert client.get("/api/signals").json()["stale"] is True  # a news job is not enough

    edition(synced, "news", timedelta(minutes=5))
    edition(synced, "openalex", timedelta(hours=5))
    edition(synced, "arxiv", timedelta(days=3))
    bar = client.get("/api/status").json()
    news_time = hhmm(
        synced.scalar(select(NewsEdition.finished_at).where(NewsEdition.family == "news"))
    )
    assert bar["news"] == f"{news_time} · arXiv old" and bar["news_old"] == ["arXiv"]
    assert f"news {news_time} · arXiv old" in bar["message"]
    assert client.get("/api/signals").json()["stale"] is True  # arXiv is past its limit

    edition(synced, "arxiv", timedelta(hours=1))
    assert client.get("/api/status").json()["news_old"] == []
    assert client.get("/api/signals").json()["stale"] is False


def test_an_old_news_edition_is_old_even_when_arxiv_is_fresh(
    client: TestClient, synced: Session
) -> None:
    edition(synced, "news", timedelta(hours=12))
    edition(synced, "arxiv", timedelta(hours=1))
    edition(synced, "openalex", timedelta(hours=1))
    status = client.get("/api/status").json()
    assert status["news"].endswith("(old)") and status["news_old"] == ["News editions"]
    families = {f["family"]: f for f in health_news(client)["families"]}
    assert families["news"]["stale"] is True and families["arxiv"]["stale"] is False
    assert families["news"]["line"].startswith("News editions: no good run since ")


def test_a_failed_edition_is_not_a_good_run(client: TestClient, synced: Session) -> None:
    edition(synced, "news", timedelta(minutes=5), status="failed")
    news = {f["family"]: f for f in health_news(client)["families"]}["news"]
    assert news["stale"] is True and news["last_edition_status"] == "failed"
    assert news["line"] == "News editions: no good run yet"
    edition(synced, "news", timedelta(minutes=2), status="partial")
    again = {f["family"]: f for f in health_news(client)["families"]}["news"]
    assert again["stale"] is False  # partial means some sources worked


def test_health_shows_why_the_last_search_failed(client: TestClient, synced: Session) -> None:
    account = synced.scalar(select(Account.id).limit(1))
    synced.add(
        SearchRequest(
            account_id=account,
            state="failed",
            error="search unavailable",
            reason="http 429",
            http_status=429,
            finished_at=now_ist(),
        )
    )
    synced.commit()
    body = client.get("/api/health/full").json()["search"]
    assert body["last_failure"] == "Last search failure: http 429 (HTTP 429)"
    assert body["last_http_status"] == 429
    synced.execute(text("update search_request set http_status = null, reason = 'DuckDuckGo page'"))
    synced.commit()
    again = client.get("/api/health/full").json()["search"]
    assert again["last_failure"] == "Last search failure: DuckDuckGo page"


def test_a_search_with_no_reason_has_no_failure_line(client: TestClient) -> None:
    body = client.get("/api/health/full").json()["search"]
    assert body["last_failure"] is None and body["last_http_status"] is None


def test_notes_partial_shows_the_reason(
    client: TestClient, synced: Session, set_env: Callable[..., None]
) -> None:
    set_env(MEETING_NOTES_DRIVE_FOLDER_ID="FOLDER_X")
    synced.execute(
        text(
            "insert into notes_sync_state (key, folder_id, state, detail, files, checked_at) "
            "values ('drive', 'FOLDER_X', 'partial', '2 folders not listed: a, b', 3, now())"
        )
    )
    synced.commit()
    notes = client.get("/api/health/full").json()["notes"]
    assert notes["state"] == "partial"
    assert notes["message"] == "meeting notes: folder list cut. 2 folders not listed: a, b"
    synced.execute(text("update notes_sync_state set detail = null"))
    synced.commit()
    bare = client.get("/api/health/full").json()["notes"]["message"]
    assert bare == "meeting notes: folder list cut"


def test_the_cleanup_counts_show_in_the_job_line(client: TestClient, synced: Session) -> None:
    note = "news_seen 1 · stories_old 2 · stories_unscored 3 · news_stories 5 · user_seen 4"
    synced.execute(
        text(
            "insert into job_runs(job, status, started_at, finished_at, note) "
            "values ('news_cleanup', 'ok', now(), now(), :n)"
        ),
        {"n": note},
    )
    synced.commit()
    jobs = {j["job"]: j for j in client.get("/api/health/full").json()["jobs"]}
    assert jobs["news_cleanup"]["note"] == note


# ------------------------------------------------------------------ A2 day bounds


@pytest.mark.parametrize(
    "path",
    [
        "/api/signals?day=9999-12-31",
        "/api/signals?day=0001-01-01",
        "/api/news/funnel?day=9999-12-31",
        "/api/news/funnel?day=0001-01-01",
    ],
)
def test_a_day_far_from_today_is_a_422_not_a_500(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 422 and r.json() == {"detail": "That day is out of range."}


def test_a_day_inside_five_years_works(client: TestClient) -> None:
    today = today_ist()
    for day in (today, today - timedelta(days=5 * 365), today + timedelta(days=365)):
        assert client.get(f"/api/signals?day={day.isoformat()}").status_code == 200
        assert client.get(f"/api/news/funnel?day={day.isoformat()}").status_code == 200
    far = today - timedelta(days=5 * 366 + 1)
    assert client.get(f"/api/signals?day={far.isoformat()}").status_code == 422


# ------------------------------------------------------------------ A3 Today sections


def test_needs_look_total_is_the_true_count(client: TestClient, synced: Session) -> None:
    reset_events(synced)  # the sheet has an event this week; count signals only
    synced.execute(
        text(
            "insert into news_stories (title, url, url_hash, fingerprint, kind, outlets, "
            "outlet_count, first_seen_at, published_at, importance, impact, tag, why, scored_at, "
            "scorer_backend) select 'Story ' || g, 'https://wire.test/' || g, md5(g::text), "
            "md5('f' || g), 'news', '{Wire}', 1, now(), now(), 5, 1, 'Market', '', now(), 'rules' "
            "from generate_series(1, 205) g"
        )
    )
    synced.execute(
        text(
            "insert into signal (story_id, entity_type, entity_id, match_kind, tag, importance, "
            "impact, confidence, verify, state) select id, 'account', "
            "(select min(id) from account), 'name_exact', 'Market', 5, 1, 3, false, 'new' "
            "from news_stories"
        )
    )
    synced.commit()
    block = client.get("/api/today?user=sid").json()["needs_look"]
    assert block["total"] == 205 and len(block["items"]) == web_signals.LOOK_SHOWN
    assert block["window"]["label"] == "Stories of 7 days. Events in 3 days."
    assert block["window"]["from"] < block["window"]["to"]
    assert block["left_out_seen"] == 0 and block["left_out_note"] is None


def test_saley_suggests_hides_what_this_person_saw(
    client: TestClient, synced: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    add_people(synced, raw_sheet)
    block = client.get("/api/today?user=sid", headers=SID).json()["saley_suggests"]
    ids = [(i["kind"], i["id"]) for i in block["items"]]
    assert any(kind == "newco" for kind, _ in ids)
    assert block["window"]["label"].startswith("Sheet as of ") and block["left_out_seen"] == 0
    newco = next(i for i in block["items"] if i["kind"] == "newco")
    seen = client.post(
        "/api/seen",
        json={"item_type": "suggest_newco", "ids": [newco["id"]]},
        headers={**H, **SID},
    )
    assert seen.json()["marked"] == 1
    mine = client.get("/api/today?user=sid", headers=SID).json()["saley_suggests"]
    assert newco["id"] not in [i["id"] for i in mine["items"] if i["kind"] == "newco"]
    assert mine["left_out_seen"] == 1
    assert mine["left_out_note"] == "1 you saw is left out. Show it."
    other = client.get("/api/today?user=sid", headers=VAISHNAVI).json()["saley_suggests"]
    assert newco["id"] in [i["id"] for i in other["items"]] and other["left_out_seen"] == 0


def test_needs_look_keeps_the_small_case(client: TestClient, synced: Session) -> None:
    reset_events(synced)
    add_story(synced, "Sarvam AI raises a round", importance=5)
    link_all(synced)
    block = client.get("/api/today?user=sid").json()["needs_look"]
    assert block["total"] == 1 and len(block["items"]) == 1


# ------------------------------------------------------------------ A5 SQL paging


def test_events_page_and_seen_filter_run_in_sql(
    client: TestClient, synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_events(synced, *[event(f"e:{i}", f"Summit {i}", "London", i + 1) for i in range(5)])
    ids = [r[0] for r in synced.execute(text("select id from event order by start, id"))]
    seen = client.post(
        "/api/seen", json={"item_type": "event", "ids": ids[:2]}, headers={**H, **SID}
    )
    assert seen.json()["marked"] == 2
    sizes: list[int] = []
    real = queries.rows

    def spy(conn: Any, sql: str, **params: Any) -> list[dict[str, Any]]:
        found = real(conn, sql, **params)
        sizes.append(len(found))
        return found

    monkeypatch.setattr(queries, "rows", spy)
    page = client.get("/api/events?limit=2&offset=1", headers=SID).json()
    assert [e["name"] for e in page["items"]] == ["Summit 3", "Summit 4"]
    assert page["total"] == 3 and page["left_out_seen"] == 2
    assert max(sizes) <= 2  # the page, never the whole table
    everything = client.get("/api/events?include_seen=true&limit=50", headers=SID).json()
    assert everything["total"] == 5 and everything["left_out_seen"] == 0
    assert [e["seen"] for e in everything["items"]] == [True, True, False, False, False]
    past = client.get("/api/events?when=past", headers=SID).json()
    assert past["total"] == 0 and past["window"]["label"] == "Before today"


def test_suggestions_rank_a_capped_set_and_say_so(
    client: TestClient,
    synced: Session,
    raw_sheet: dict[str, list[list[Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_people(synced, raw_sheet)
    assert client.get("/api/suggest/warm").json()["total"] >= 2
    monkeypatch.setattr(web_signals, "SUGGEST_RANK_CAP", 1)
    with structlog.testing.capture_logs() as logs:
        body = client.get("/api/suggest/warm").json()
    assert body["total"] == 1
    assert any(e["event"] == "suggest_rank_capped" for e in logs)


# ------------------------------------------------------------------ A6 X-Saley-As on GETs


@pytest.mark.parametrize("path", ["/api/signals", "/api/events", "/api/suggest/warm", "/api/today"])
def test_an_unknown_view_as_name_is_a_403_on_a_get(client: TestClient, path: str) -> None:
    bad = client.get(path, headers={"X-Saley-As": "ghost"})
    assert bad.status_code == 403 and bad.json() == {"detail": "That user is not in the team."}
    assert client.get(path, headers=SID).status_code == 200
    assert client.get(path).status_code == 200  # no header: the dev user, as before
    assert client.get(path, headers={"X-Saley-As": "Not Valid!"}).status_code == 403


def test_the_post_door_answers_the_same_for_an_unknown_name(client: TestClient) -> None:
    body = {"item_type": "story", "ids": [1]}
    bad = client.post("/api/seen", json=body, headers={**H, "X-Saley-As": "ghost"})
    assert bad.status_code == 403


# ------------------------------------------------------------------ A10 find people vs seen


def test_new_search_hits_make_a_seen_company_unseen_again(
    client: TestClient, synced: Session
) -> None:
    account = synced.scalar(select(Account).where(Account.name == "AI4Bharat"))
    assert account is not None
    posted = client.post(
        "/api/seen", json={"item_type": "suggest_newco", "ids": [account.id]}, headers={**H, **SID}
    )
    assert posted.json()["marked"] == 1
    hidden = client.get("/api/suggest/newco", headers=SID).json()
    assert hidden["items"] == [] and hidden["left_out_seen"] == 1

    request = SearchRequest(account_id=account.id, state="done", hits=1, finished_at=now_ist())
    synced.add(request)
    synced.flush()
    synced.add(
        SearchHit(
            request_id=request.id,
            query="q",
            engine="duckduckgo",
            url="https://www.linkedin.com/in/priya-sharma",
            title="Priya Sharma - Head of Data - AI4Bharat | LinkedIn",
            account_id=account.id,
            found_at=now_ist(),  # after the mark; the session's own now() would be older
        )
    )
    synced.commit()
    back = client.get("/api/suggest/newco", headers=SID).json()
    assert [i["subject"] for i in back["items"]] == ["AI4Bharat"]
    assert back["items"][0]["seen"] is False and back["items"][0]["hits"]
    assert back["left_out_seen"] == 0
    today = client.get("/api/today?user=sid", headers=SID).json()["saley_suggests"]
    assert any(i["subject"] == "AI4Bharat" for i in today["items"])

    # The person sees the new hits and the page marks the company again: now it is seen.
    client.post(
        "/api/seen", json={"item_type": "suggest_newco", "ids": [account.id]}, headers={**H, **SID}
    )
    again = client.get("/api/suggest/newco", headers=SID).json()
    assert again["items"] == [] and again["left_out_seen"] == 1


def test_a_company_without_new_hits_stays_hidden(client: TestClient, synced: Session) -> None:
    account = synced.scalar(select(Account).where(Account.name == "AI4Bharat"))
    assert account is not None
    synced.add(
        SearchHit(
            query="q",
            engine="duckduckgo",
            url="https://www.linkedin.com/in/old",
            title="Old",
            account_id=account.id,
        )
    )
    synced.commit()
    client.post(
        "/api/seen", json={"item_type": "suggest_newco", "ids": [account.id]}, headers={**H, **SID}
    )
    synced.execute(text("update search_hit set found_at = now() - interval '1 hour'"))
    synced.commit()
    body = client.get("/api/suggest/newco", headers=SID).json()
    assert body["items"] == [] and body["left_out_seen"] == 1


# ------------------------------------------------------------------ A12 plural words


def test_fill_count_uses_the_singular_for_exactly_one() -> None:
    assert w.fill_count("1 day", "{n} days", 1) == "1 day"
    assert w.fill_count("1 day", "{n} days", 0) == "0 days"
    assert w.fill_count("1 day", "{n} days", 2) == "2 days"
    assert w.fill_count("{who}: 1 day", "{who}: {n} days", 3, who="X") == "X: 3 days"


def test_days_and_events_read_right_for_one_day() -> None:
    assert days_text(1) == "1 day left" and days_text(2) == "2 days left"
    assert days_text(0) == "Last day of the quarter"
    assert (
        days_text(-1) == "Quarter ended 1 day ago" and days_text(-3) == "Quarter ended 3 days ago"
    )
    assert web_signals.days_text(1) == "In 1 day" and web_signals.days_text(5) == "In 5 days"
    assert web_signals.days_text(-1) == "1 day ago" and web_signals.days_text(-4) == "4 days ago"


def test_part_labels_read_right_for_one() -> None:
    label = web_signals.part_label
    assert label(suggest.Part("news", 1, 1)) == "1 story in 30 days"
    assert label(suggest.Part("news", 1, 3)) == "3 stories in 30 days"
    assert label(suggest.Part("recent", 1, 1)) == "Paper news 1 day old"
    assert label(suggest.Part("recent", 1, 4)) == "Paper news 4 days old"
    assert label(suggest.Part("goal_fit", 1, 1)) == "Fits 1 goal"
    assert label(suggest.Part("goal_fit", 1, 3)) == "Fits 3 goals"
    assert label(suggest.Part("connections", 1, 1)) == "1 connected at the company"


def test_left_out_reads_right_for_one() -> None:
    assert web_signals.left_out(0) is None
    assert web_signals.left_out(1) == "1 you saw is left out. Show it."
    assert web_signals.left_out(2) == "2 you saw are left out. Show them."


def test_the_page_words_have_a_singular_for_stories() -> None:
    from pathlib import Path

    words = Path("src/saley/web/static/words.js").read_text(encoding="utf-8")
    assert 'storiesCountOne: "1 story"' in words and 'storiesCount: "{n} stories"' in words
    assert "window.fmtCount = function (one, many, n, values)" in words
    signals_js = Path("src/saley/web/static/signals.js").read_text(encoding="utf-8")
    assert "fmtCount(W.storiesCountOne, W.storiesCount, d.total)" in signals_js
