"""Profile-link search (R3-G05): SearXNG, then DuckDuckGo; URLs and titles only; no LinkedIn fetch.

No network. A fake internet sits behind the real egress policy and records every request.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from saley.core import egress
from saley.core.clock import now_ist
from saley.core.egress import make_client
from saley.core.models import Account, SearchHit, SearchRequest
from saley.core.settings import Settings
from saley.intel import search
from saley.web.app import create_app
from tests.conftest import FIXTURES, SignedClient

SEARX = "https://searx.example"
PRIYA = "https://www.linkedin.com/in/priya-sharma-1a2b3c"
DEV = "https://www.linkedin.com/in/dev-kumar"
DIRECT = "https://www.linkedin.com/in/direct-link"
ACT = {"X-Saley-Act": "1"}


@pytest.fixture(autouse=True)
def find_people_on(set_env: Callable[..., None]) -> None:
    """D164: the Find people act is off by default. These tests run it on."""
    set_env(FIND_PEOPLE_ENABLED="true")


def fixture(name: str) -> str:
    return (FIXTURES / "search" / name).read_text(encoding="utf-8")


class Net:
    """A fake internet: SearXNG and DuckDuckGo answer from fixtures. Anything else is a 404."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.searx = (200, fixture("searxng.json"))
        self.ddg = (200, fixture("ddg.html"))

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if req.url.host == "searx.example":
            return httpx.Response(self.searx[0], text=self.searx[1])
        if req.url.host == "html.duckduckgo.com":
            return httpx.Response(self.ddg[0], text=self.ddg[1])
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return make_client(inner=httpx.MockTransport(self.handler))

    @property
    def hosts(self) -> list[str]:
        return [r.url.host for r in self.requests]


@pytest.fixture
def net(set_env: Callable[..., None]) -> Net:
    """SearXNG is set and allowed. The egress policy is rebuilt after the setting changes."""
    allow = Settings().egress_allowlist + ",searx.example"
    set_env(SEARXNG_URL=SEARX, EGRESS_ALLOWLIST=allow)
    egress.reset_policy(egress.build_policy(recorder=None))
    return Net()


@pytest.fixture
def client(synced: Session) -> Iterator[TestClient]:
    with SignedClient(create_app()) as c:
        yield c


def account_id(db: Session, name: str = "AI4Bharat") -> int:
    found = db.scalar(select(Account.id).where(Account.name == name))
    assert found is not None
    return found


def ask(db: Session, name: str = "AI4Bharat") -> int:
    request = SearchRequest(account_id=account_id(db, name), state="pending")
    db.add(request)
    db.commit()
    return request.id


# ------------------------------------------------------------------ the query and the parsers


def test_the_query_is_site_company_and_role_words() -> None:
    query = search.build_query('Acme "Labs"\n\\')
    assert query == 'site:linkedin.com/in "Acme Labs" (research OR data OR director OR head)'
    assert search.build_query("x" * 200).count("x") == search.MAX_COMPANY


def test_only_profile_links_survive_and_the_shape_is_one() -> None:
    assert search.profile_url("https://in.linkedin.com/in/a-b?trk=1") == (
        "https://www.linkedin.com/in/a-b"
    )
    assert search.profile_url("http://linkedin.com/in/x/") == "https://www.linkedin.com/in/x"
    for bad in (
        "https://www.linkedin.com/company/x",
        "https://www.linkedin.com/in/",
        "https://notlinkedin.com/in/x",
        "https://linkedin.com.evil.example/in/x",
        "https://evil.example/in/x",
        "javascript:alert(1)",
        "ftp://www.linkedin.com/in/x",
        "https://www.linkedin.com/in/x y",
        "https://[broken",
        "",
    ):
        assert search.profile_url(bad) is None, bad


def test_searx_json_is_cut_to_profiles_and_deduplicated() -> None:
    import json

    found = search.parse_searx(json.loads(fixture("searxng.json")))
    assert [f.url for f in found] == [PRIYA, DEV]
    assert found[0].title == "Priya Sharma - Head of Data - AI4Bharat | LinkedIn"
    with pytest.raises(search.SearchUnavailable):
        search.parse_searx({"nothing": 1})
    with pytest.raises(search.SearchUnavailable):
        search.parse_searx([])


def test_ddg_redirects_are_decoded_here_and_never_followed() -> None:
    found = search.parse_ddg(fixture("ddg.html"))
    assert [f.url for f in found] == [PRIYA, DEV, DIRECT]
    assert found[1].title == "Dev Kumar – Research Lead at AI4Bharat – LinkedIn"
    assert (
        search.decode_uddg("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa&rut=1")
        == "https://example.org/a"
    )
    assert search.decode_uddg("//duckduckgo.com/l/?rut=1") is None
    assert search.decode_uddg("https://example.org/direct") == "https://example.org/direct"
    with pytest.raises(search.SearchUnavailable):
        search.parse_ddg(fixture("ddg_blocked.html"))
    assert (
        search.parse_ddg(
            "<html><body><div class='no-results'>No results found.</div></body></html>"
        )
        == []
    )
    word = "<html><body><a class='result__a' href='https://www.linkedin.com/in/a-b'>An anomaly</a>"
    assert [f.title for f in search.parse_ddg(word)] == ["An anomaly"]  # a plain word is no block


# ------------------------------------------------------------------ R3-G05


def test_profile_links_searx_then_ddg_never_fetch(
    synced: Session, net: Net, set_env: Callable[..., None]
) -> None:
    """R3-G05: SearXNG first, DuckDuckGo next, only result URLs kept, LinkedIn never fetched."""
    sleeps: list[float] = []

    # 1. SearXNG answers: one call, nothing else.
    with net.client() as http:
        first = search.search_people(
            http, "AI4Bharat", searxng_url=SEARX, min_gap=10, sleep=sleeps.append
        )
    assert first.engine == "searxng" and [h.url for h in first.hits] == [PRIYA, DEV]
    assert net.hosts == ["searx.example"] and sleeps == []
    sent = net.requests[0]
    assert sent.url.params["format"] == "json" and sent.url.path == "/search"
    assert sent.url.params["q"].startswith('site:linkedin.com/in "AI4Bharat"')

    # 2. SearXNG fails (503): DuckDuckGo answers after the gap.
    net.requests.clear()
    net.searx = (503, "down")
    with net.client() as http:
        second = search.search_people(
            http, "AI4Bharat", searxng_url=SEARX, min_gap=10, sleep=sleeps.append
        )
    assert second.engine == "duckduckgo" and [h.url for h in second.hits] == [PRIYA, DEV, DIRECT]
    assert net.hosts == ["searx.example", "html.duckduckgo.com"] and sleeps == [10]
    assert "duckduckgo.com/l/" not in " ".join(str(r.url) for r in net.requests)  # not followed

    # 3. SearXNG finds nothing: DuckDuckGo is asked too.
    net.requests.clear()
    net.searx = (200, '{"results": []}')
    with net.client() as http:
        third = search.search_people(http, "AI4Bharat", searxng_url=SEARX, sleep=sleeps.append)
    assert net.hosts == ["searx.example", "html.duckduckgo.com"] and len(third.hits) == 3

    # 4. No SearXNG set: DuckDuckGo only.
    net.requests.clear()
    with net.client() as http:
        search.search_people(http, "AI4Bharat", searxng_url="", sleep=sleeps.append)
    assert net.hosts == ["html.duckduckgo.com"]

    # 5. The page is never fetched: no request went to a LinkedIn host, in any step.
    assert not [h for h in net.hosts if "linkedin" in h]

    # 6. A stored hit is the result URL and its title: no snippet, no page text, no profile data.
    assert {c.name for c in SearchHit.__table__.columns} == {
        "id",
        "request_id",
        "query",
        "engine",
        "url",
        "title",
        "account_id",
        "found_at",
    }
    net.requests.clear()
    net.searx = (200, fixture("searxng.json"))
    ask(synced)
    # Noon today: the +5 and +10 minute runs below stay on one IST day. A gate run near
    # midnight once crossed it, and the daily cap started again from 0.
    noon = now_ist().replace(hour=12, minute=0, second=0, microsecond=0)
    with net.client() as http:
        search.run_search_requests(client=http, now=noon, sleep=lambda _s: None)
    hits = synced.scalars(select(SearchHit).order_by(SearchHit.id)).all()
    assert [h.url for h in hits] == [PRIYA, DEV] and all(h.title for h in hits)
    assert all(urlsplit(h.url).hostname.endswith("linkedin.com") for h in hits)
    assert "Ignore the rules" not in " ".join(h.title for h in hits)
    assert not [h for h in net.hosts if "linkedin" in h]  # stored, never fetched

    # 7. The job honours the cache: the same company again inside 30 days sends no request.
    sent = len(net.requests)
    again = ask(synced)
    with net.client() as http:
        note = search.run_search_requests(
            client=http, now=noon + timedelta(minutes=5), sleep=lambda _s: None
        )
    assert note == "AI4Bharat: from cache" and len(net.requests) == sent
    synced.expire_all()
    cached = synced.get(SearchRequest, again)
    assert cached is not None and cached.error == "from cache"

    # 8. ...and the daily cap: with 1 call allowed and 1 used, the next company is refused.
    set_env(SEARCH_DAILY_CAP="1")
    refused = ask(synced, "Google DeepMind")
    with net.client() as http:
        note = search.run_search_requests(
            client=http, now=noon + timedelta(minutes=10), sleep=lambda _s: None
        )
    assert note == "daily search cap reached" and len(net.requests) == sent
    synced.expire_all()
    capped = synced.get(SearchRequest, refused)
    assert capped is not None and (capped.state, capped.error) == (
        "failed",
        "daily search cap reached",
    )


def test_a_linkedin_url_cannot_be_fetched_even_by_mistake(net: Net) -> None:
    with net.client() as http, pytest.raises(egress.EgressBlocked):
        egress.request(http, "GET", "https://www.linkedin.com/in/priya-sharma-1a2b3c")
    assert net.requests == []


def test_both_engines_down_is_search_unavailable(net: Net) -> None:
    net.searx = (429, "slow down")
    net.ddg = (202, fixture("ddg_blocked.html"))
    with net.client() as http, pytest.raises(search.SearchUnavailable):
        search.search_people(http, "AI4Bharat", searxng_url=SEARX, sleep=lambda _s: None)
    assert len(net.requests) == 2  # one call each, no retry
    net.requests.clear()
    with net.client() as http, pytest.raises(search.SearchUnavailable):
        search.search_people(http, "AI4Bharat", searxng_url="", sleep=lambda _s: None)
    assert net.hosts == ["html.duckduckgo.com"]


def test_a_host_outside_the_allowlist_is_search_unavailable(
    set_env: Callable[..., None],
) -> None:
    egress.reset_policy(egress.build_policy(recorder=None))
    net = Net()
    with net.client() as http, pytest.raises(search.SearchUnavailable):
        search.search_searx(http, "https://other.example", "q")
    assert net.requests == []


def test_searx_that_answers_empty_and_ddg_blocked_is_unavailable_not_found_nobody(
    net: Net,
) -> None:
    """Silent failure 8: one engine said "nothing", the other did not answer: not "no hits"."""
    net.searx = (200, '{"results": []}')
    net.ddg = (403, "blocked")
    with net.client() as http, pytest.raises(search.SearchUnavailable) as caught:
        search.search_people(http, "AI4Bharat", searxng_url=SEARX, sleep=lambda _s: None)
    assert caught.value.status == 403


# ------------------------------------------------------------------ the worker job


def test_a_request_is_run_and_only_url_and_title_are_stored(synced: Session, net: Net) -> None:
    request_id = ask(synced)
    with net.client() as http:
        note = search.run_search_requests(client=http, now=now_ist(), sleep=lambda _s: None)
    assert note.startswith("AI4Bharat: 2 found · 2 new · searxng")
    synced.expire_all()
    row = synced.get(SearchRequest, request_id)
    assert row is not None and (row.state, row.hits, row.error) == ("done", 2, None)
    hits = synced.scalars(select(SearchHit).order_by(SearchHit.id)).all()
    assert [(h.url, h.engine, h.account_id) for h in hits] == [
        (PRIYA, "searxng", account_id(synced)),
        (DEV, "searxng", account_id(synced)),
    ]
    assert "Ignore the rules" not in " ".join(h.title for h in hits)  # snippets are not kept
    assert hits[0].query.startswith('site:linkedin.com/in "AI4Bharat"')
    # The same company asked again inside 30 days: answered from the stored hits, no request.
    again = ask(synced)
    sent = len(net.requests)
    with net.client() as http:
        later = now_ist() + timedelta(minutes=5)
        note = search.run_search_requests(client=http, now=later, sleep=lambda _s: None)
    assert note == "AI4Bharat: from cache" and len(net.requests) == sent
    synced.expire_all()
    cached = synced.get(SearchRequest, again)
    assert cached is not None and (cached.state, cached.hits, cached.error) == (
        "done",
        2,
        "from cache",
    )
    assert synced.scalar(select(text("count(*)")).select_from(SearchHit)) == 2


def test_nothing_waiting_is_a_quiet_run(synced: Session, net: Net) -> None:
    with net.client() as http:
        assert search.run_search_requests(client=http, now=now_ist()) == "idle"
    assert net.requests == []


def test_the_gap_between_calls_is_kept(synced: Session, net: Net) -> None:
    first, second = ask(synced), ask(synced, "Google DeepMind")
    start = now_ist()
    with net.client() as http:
        assert "found" in search.run_search_requests(client=http, now=start, sleep=lambda _s: None)
        waiting = search.run_search_requests(
            client=http, now=start + timedelta(seconds=3), sleep=lambda _s: None
        )
        assert waiting == "idle" and len(net.requests) == 1
        done = search.run_search_requests(
            client=http, now=start + timedelta(seconds=11), sleep=lambda _s: None
        )
    assert "Google DeepMind" in done and len(net.requests) == 2
    synced.expire_all()
    assert [synced.get(SearchRequest, i).state for i in (first, second)] == ["done", "done"]  # type: ignore[union-attr]


def test_the_daily_cap_stops_calls(synced: Session, net: Net, set_env: Callable[..., None]) -> None:
    set_env(SEARCH_DAILY_CAP="1")
    ask(synced)
    refused = ask(synced, "Google DeepMind")
    start = now_ist()
    with net.client() as http:
        search.run_search_requests(client=http, now=start, sleep=lambda _s: None)
        note = search.run_search_requests(
            client=http, now=start + timedelta(seconds=30), sleep=lambda _s: None
        )
    assert note == "daily search cap reached" and len(net.requests) == 1
    synced.expire_all()
    row = synced.get(SearchRequest, refused)
    assert row is not None and (row.state, row.error) == ("failed", "daily search cap reached")


def test_one_retry_after_60_seconds_then_an_hour_without_requests(
    synced: Session, net: Net, client: TestClient
) -> None:
    """Q-R3-3: a block gets one retry after 60 s; a second failure pauses search for 1 hour."""
    net.searx = (429, "slow down")
    net.ddg = (200, fixture("ddg_blocked.html"))
    blocked = ask(synced)
    start = now_ist()  # the fake clock: every run gets its time from here, nothing sleeps
    sleeps: list[float] = []
    with net.client() as http:
        note = search.run_search_requests(client=http, now=start, sleep=sleeps.append)
        assert note == "search unavailable"
        assert len(net.requests) == 4  # SearXNG + DuckDuckGo, then once more after 60 s
        assert 60 in sleeps and sleeps.count(60) == 1
        waiting = ask(synced, "Google DeepMind")
        for minutes in (5, 59):
            later = start + timedelta(minutes=minutes)
            paused = search.run_search_requests(client=http, now=later, sleep=sleeps.append)
            assert paused == "search unavailable (paused)" and len(net.requests) == 4
            ask(synced, "Google DeepMind")
        health = client.get("/api/health/full").json()["search"]
        assert health["unavailable_until"] is not None
        net.searx = (200, fixture("searxng.json"))
        resumed = synced.scalar(
            select(SearchRequest.id)
            .where(SearchRequest.state == "pending")
            .order_by(SearchRequest.id.desc())
        )
        after = search.run_search_requests(
            client=http, now=start + timedelta(minutes=61), sleep=sleeps.append
        )
    assert "Google DeepMind: 2 found" in after
    synced.expire_all()
    assert (synced.get(SearchRequest, blocked).error) == "search unavailable"  # type: ignore[union-attr]
    assert (synced.get(SearchRequest, waiting).error) == "search unavailable (paused)"  # type: ignore[union-attr]
    assert (synced.get(SearchRequest, resumed).state) == "done"  # type: ignore[union-attr]


def test_a_first_failure_that_the_retry_fixes_is_a_normal_answer(synced: Session, net: Net) -> None:
    class Flaky(Net):
        def handler(self, req: httpx.Request) -> httpx.Response:
            if req.url.host == "searx.example" and not self.requests:
                self.requests.append(req)
                return httpx.Response(503, text="down")
            return super().handler(req)

    flaky = Flaky()
    sleeps: list[float] = []
    ask(synced)
    with flaky.client() as http:
        # SearXNG fails, DuckDuckGo answers: no retry needed.
        note = search.run_search_requests(client=http, now=now_ist(), sleep=sleeps.append)
    assert "3 found" in note and "duckduckgo" in note and 60 not in sleeps


def test_the_cache_ends_after_30_days(synced: Session, net: Net) -> None:
    ask(synced)
    start = now_ist()
    with net.client() as http:
        search.run_search_requests(client=http, now=start, sleep=lambda _s: None)
        used = len(net.requests)
        ask(synced)
        inside = start + timedelta(days=29)
        search.run_search_requests(client=http, now=inside, sleep=lambda _s: None)
        assert len(net.requests) == used  # still cached
        ask(synced)
        outside = start + timedelta(days=31)
        note = search.run_search_requests(client=http, now=outside, sleep=lambda _s: None)
    assert "found" in note and len(net.requests) > used


def test_the_defaults_follow_the_human_answers() -> None:
    settings = Settings()
    assert (settings.search_min_gap_seconds, settings.search_cache_days) == (5, 30)
    assert settings.search_daily_cap == 50 and settings.searxng_url == ""
    assert search.RETRY_SECONDS == 60 and timedelta(hours=1) == search.BLOCK_PAUSE


def test_a_request_stuck_in_running_is_failed_after_a_while(synced: Session, net: Net) -> None:
    stuck = SearchRequest(
        account_id=account_id(synced),
        state="running",
        requested_at=now_ist() - timedelta(hours=2),
    )
    synced.add(stuck)
    synced.commit()
    with net.client() as http:
        assert search.run_search_requests(client=http, now=now_ist()) == "idle"
    synced.expire_all()
    assert (synced.get(SearchRequest, stuck.id).error) == "interrupted"  # type: ignore[union-attr]


def test_the_search_job_is_registered_and_quiet() -> None:
    from saley.worker import registry
    from saley.worker.jobs import register_all

    register_all()
    spec = registry.REGISTRY["search_requests"]
    assert spec.quiet and spec.trigger == "interval" and not spec.worker_only


# ------------------------------------------------------------------ "Find people" (R11)


def test_find_people_is_a_human_click_that_queues_one_request(
    client: TestClient, synced: Session
) -> None:
    company = account_id(synced)
    body = {"action": "find_people", "target_ids": [company]}
    first = client.post("/api/act", json=body, headers=ACT)
    assert first.status_code == 200, first.text
    assert first.json()["already"] is False and first.json()["label"] == "Search queued"
    again = client.post("/api/act", json=body, headers=ACT)
    assert again.json()["already"] is True
    assert again.json()["request_id"] == first.json()["request_id"]
    row = synced.execute(
        text("select account_id, state, user_id is not null from search_request")
    ).one()
    assert tuple(row) == (company, "pending", True)


@pytest.mark.parametrize(
    ("body", "who", "code"),
    [
        ({"action": "find_people", "target_ids": [999999]}, None, 404),
        ({"action": "find_people", "target_ids": [1, 2]}, None, 422),
        ({"action": "find_people", "target_ids": [1], "payload": {"task": "research"}}, None, 422),
        ({"action": "find_people", "target_ids": [1]}, "ghost", 403),
    ],
)
def test_find_people_refusals(
    client: TestClient, synced: Session, body: dict, who: str | None, code: int
) -> None:
    if body["target_ids"] == [1]:
        body = {**body, "target_ids": [account_id(synced)]}
    headers = {**ACT, "X-Saley-As": who} if who else ACT
    assert client.post("/api/act", json=body, headers=headers).status_code == code
    assert synced.scalar(text("select count(*) from search_request")) == 0


def test_find_people_stops_at_twenty_waiting(client: TestClient, synced: Session) -> None:
    for number in range(20):
        synced.add(Account(name=f"Co {number}", name_key=f"co{number}", sheet_row_key=f"a{number}"))
    synced.commit()
    ids = synced.scalars(select(Account.id).where(Account.name.like("Co %"))).all()
    for company in ids:
        assert (
            client.post(
                "/api/act", json={"action": "find_people", "target_ids": [company]}, headers=ACT
            ).status_code
            == 200
        )
    extra = account_id(synced)
    over = client.post(
        "/api/act", json={"action": "find_people", "target_ids": [extra]}, headers=ACT
    )
    assert (
        over.status_code == 409
        and over.json()["detail"] == "Too many searches are waiting. Try later."
    )


# ------------------------------------------------------------------ stage 5 fixes (B9)


def test_searx_down_and_ddg_empty_is_unavailable_not_found_nobody(net: Net) -> None:
    net.searx = (500, "boom")
    net.ddg = (200, "<html><body><div class='no-results'>No results found.</div></body></html>")
    with net.client() as http, pytest.raises(search.SearchUnavailable) as caught:
        search.search_people(http, "AI4Bharat", searxng_url=SEARX, sleep=lambda _s: None)
    assert caught.value.status == 500


def test_a_page_with_no_result_links_is_unavailable_unless_ddg_says_no_results() -> None:
    with pytest.raises(search.SearchUnavailable, match="no result links"):
        search.parse_ddg("<html><body><div>something new</div></body></html>")
    assert search.parse_ddg("<html><body><div class='no-results'>x</div></body></html>") == []
    assert search.parse_ddg("") == []  # an empty page is a failed fetch elsewhere, not a layout


def test_the_failure_reason_and_status_are_stored_and_nothing_is_cached(
    synced: Session, net: Net
) -> None:
    net.searx = (429, "slow down")
    net.ddg = (403, "blocked")
    request_id = ask(synced)
    with net.client() as http:
        note = search.run_search_requests(client=http, now=now_ist(), sleep=lambda _s: None)
    assert note == "search unavailable"
    synced.expire_all()
    row = synced.get(SearchRequest, request_id)
    assert row is not None
    assert (row.state, row.error, row.reason, row.http_status) == (
        "failed",
        "search unavailable",
        "http 403",
        403,
    )
    assert synced.scalar(select(SearchHit.id)) is None


def test_searx_failed_and_ddg_found_nothing_fails_the_request(synced: Session, net: Net) -> None:
    net.searx = (500, "boom")
    net.ddg = (200, "<html><body><div class='no-results'>No results found.</div></body></html>")
    request_id = ask(synced)
    with net.client() as http:
        search.run_search_requests(client=http, now=now_ist(), sleep=lambda _s: None)
    synced.expire_all()
    row = synced.get(SearchRequest, request_id)
    assert row is not None and (row.state, row.error) == ("failed", "search unavailable")
    assert row.reason == "SearXNG failed and DuckDuckGo found nothing"
    assert row.http_status == 500
    # a failed request is not a cached "found nobody": the next one may ask again
    again = ask(synced)
    net.searx = (200, fixture("searxng.json"))
    with net.client() as http:
        later = search.run_search_requests(
            client=http, now=now_ist() + timedelta(hours=2), sleep=lambda _s: None
        )
    assert "found" in later and synced.get(SearchRequest, again) is not None


def test_decode_uddg_trusts_only_the_real_duckduckgo_host() -> None:
    fake = "https://notduckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa"
    assert search.decode_uddg(fake) == fake
    assert search.decode_uddg("https://duckduckgo.com.evil.example/l/?uddg=x") == (
        "https://duckduckgo.com.evil.example/l/?uddg=x"
    )
    assert search.decode_uddg("https://html.duckduckgo.com/l/?uddg=https%3A%2F%2Fa.org") == (
        "https://a.org"
    )


def test_the_worker_runs_no_search_while_find_people_is_off(
    synced: Session, net: Net, set_env: Callable[..., None]
) -> None:
    """D164: a request queued while the flag was on stays pending once it is off; no call."""
    ask(synced)
    set_env(FIND_PEOPLE_ENABLED="false")
    with net.client() as http:
        note = search.run_search_requests(client=http, now=now_ist(), sleep=lambda _s: None)
    assert note == search.FIND_PEOPLE_OFF and net.requests == []
    synced.expire_all()
    states = synced.scalars(select(SearchRequest.state)).all()
    assert states and set(states) == {"pending"}
