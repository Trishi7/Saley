"""saley.qa: the independent sheet recount behind `saley sheet verify` (gate step 6)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from saley.cli import app
from saley.core.clock import now_ist
from saley.qa import sheet_verify as qa
from saley.sales.sheet import build_tabs
from saley.sales.sync import run_sync
from saley.web.app import create_app
from tests.conftest import POC_HEADERS, SignedClient


@pytest.fixture
def get(synced: Session) -> Iterator[Any]:
    with SignedClient(create_app()) as client:

        def _get(path: str) -> dict[str, Any]:
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
            return response.json()

        yield _get


def test_verify_passes_on_a_fresh_sync(
    get: Any, raw_sheet: dict[str, Any], sheet_today: date
) -> None:
    report = qa.verify(raw_sheet, sheet_today, get)
    assert report.ok, [r for r in report.rows if not r[3]]
    metrics = {r[0] for r in report.rows}
    assert {"people_total", "money total = sum of list", "tile late = its list"} <= metrics
    assert "**PASS**" in report.markdown("t")


def test_verify_reports_diff_when_sheet_moves_ahead(
    get: Any, raw_sheet: dict[str, Any], sheet_today: date
) -> None:
    raw_sheet["Outreach PoCs"].pop()  # the sheet changed; the DB still has the old row
    report = qa.verify(raw_sheet, sheet_today, get)
    assert not report.ok
    diff = {r[0]: r for r in report.rows if not r[3]}
    assert "people_total" in diff
    assert "| people_total |" in report.markdown("t") and "DIFF" in report.markdown("t")


def test_stage_rules_are_written_again() -> None:
    row = {"Deal Status": "", "Status": "Demo", "Closure Prob%": ""}
    assert qa.stage(row) == "Qualified"
    assert qa.stage({"Deal Status": "Closed won"}) == "Won"
    assert qa.stage({"Closure Prob%": "0%"}) == "Lost"
    assert qa.stage({"Sid - LI Addition": "Connected"}) == "Connected"
    assert qa.stage({"First Contact": "VR"}) == "Contacted"
    assert qa.stage({}) == "Listed"


def test_cell_helpers() -> None:
    assert qa.prob("<50%") == 49 and qa.prob(0.6) == 60 and qa.prob("") is None
    assert qa.as_date("02.09.2026") == date(2026, 9, 2)
    assert qa.as_date(46000) == date(2025, 12, 9)
    assert qa.as_date("soon") is None
    assert qa.word("not a lead", "lead") is False and qa.word("lead", "lead")
    assert qa.truthy("Not sent") is False and qa.truthy(True)
    assert qa.table([["x"], ["Name", "Age"], ["A", 3]], "Name") == [{"Name": "A", "Age": 3}]
    assert qa.table([["x"]], "Name") == []


def test_cli_sheet_verify_writes_report(
    synced: Session, raw_sheet: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("saley.sales.sheet.fetch_raw", lambda: raw_sheet)
    out = tmp_path / "verify.md"
    result = CliRunner().invoke(app, ["sheet", "verify", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert result.output.strip().endswith("PASS")
    assert "| Metric | Sheet | API | Result |" in out.read_text(encoding="utf-8")


def test_cli_sheet_verify_fails_on_diff(
    synced: Session, raw_sheet: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = {k: [list(r) for r in v] for k, v in raw_sheet.items()}
    changed["Outreach PoCs"].append(list(changed["Outreach PoCs"][1]))
    changed["Outreach PoCs"][-1][POC_HEADERS.index("Sr No")] = 999
    monkeypatch.setattr("saley.sales.sheet.fetch_raw", lambda: changed)
    result = CliRunner().invoke(app, ["sheet", "verify"])
    assert result.exit_code == 1
    assert "DIFF" in result.output and result.output.strip().endswith("FAIL")


def test_cli_sheet_verify_refuses_live_sheet(monkeypatch: pytest.MonkeyPatch) -> None:
    from saley.sales.sheet import SheetNotCopy

    def refuse() -> None:
        raise SheetNotCopy("GTM_SHEET_IS_COPY is false.")

    monkeypatch.setattr("saley.sales.sheet.fetch_raw", refuse)
    result = CliRunner().invoke(app, ["sheet", "verify"])
    assert result.exit_code == 1
    assert "Refused" in result.output


def test_ste_lint_is_clean_on_the_repo() -> None:
    from saley.qa import ste_lint

    count, findings = ste_lint.lint()
    assert count > 100
    assert findings == []


def test_ste_lint_flags_long_text_and_banned_words(tmp_path: Path) -> None:
    from saley.qa import ste_lint

    assert ste_lint.visible_length("Of {n} marked P1") == len("Of xxxx marked P1")
    found = ste_lint.check("t", "x" * 61, ["ensure"])
    assert [f.problem for f in found] == ["over 60 characters"]
    found = ste_lint.check("t", "We ensure it works", ["ensure", "in order to"])
    assert [f.problem for f in found] == ["banned word 'ensure'"]
    assert ste_lint.check("t", "Measured {n} times", ["ensure"]) == []
    js = tmp_path / "w.js"
    js.write_text('window.W = {\n  a: "One \\"two\\"",\n  b: "Three"\n};\n', encoding="utf-8")
    assert ste_lint.js_strings(js) == [("w.js:a", 'One "two"'), ("w.js:b", "Three")]
    page = tmp_path / "p.html"
    page.write_text(
        '<p title="Tip">Hello <b>you</b></p><script>var x = "no";</script>', encoding="utf-8"
    )
    assert [t for _, t in ste_lint.html_strings(page)] == ["Tip", "Hello", "you"]
    words = tmp_path / "b.txt"
    words.write_text("# comment\nRobust\n\n", encoding="utf-8")
    assert ste_lint.banned_words(words) == ["robust"]


def test_ste_lint_reads_words_ts() -> None:
    """R5-S07: the lint reads the dashboard v2 catalogue (web/src/words.ts) and the text of
    web/index.html; the legacy static/words.js left at the cut-over (T16b, D224). No literal text
    in TSX is ESLint's job (react/jsx-no-literals, gate step 1); test_web_assets checks that
    every W key is used and exists."""
    from saley.qa import ste_lint

    strings = ste_lint.collect()
    sources = {source for source, _ in strings}
    texts = {text for _, text in strings}
    words_ts = (ste_lint.WEB_V2 / "src" / "words.ts").read_text(encoding="utf-8")
    ts_items = ste_lint.JS_STRING.findall(words_ts)
    assert len(ts_items) >= 10
    for key, value in ts_items:
        assert f"words.ts:{key}" in sources
        assert value in texts
    assert "web/index.html" in sources and "Saley" in texts  # the <title> of web/index.html
    assert not any(s.startswith(("words.js:", "index.html")) for s in sources)  # gone (D224)


def test_verify_fails_on_an_unreadable_sheet(get: Any, sheet_today: date) -> None:
    """A wrong or empty sheet must never pass as '0 = 0, OK' (silent-failure review)."""
    report = qa.verify({"Something else": [["x"]]}, sheet_today, get)
    assert not report.ok
    failed = {r[0] for r in report.rows if not r[3]}
    assert {"tab Outreach PoCs read", "people in the sheet"} <= failed
    assert "Nothing else compared" in report.markdown("t")


def test_missing_metric_is_a_diff() -> None:
    report = qa.Report()
    report.check("x", None, 0)
    assert not report.ok


def test_as_date_ignores_numbers_that_are_not_dates() -> None:
    assert qa.as_date(9_876_543_210) is None
    assert qa.as_date(float("nan")) is None
    assert qa.as_date(-1) is None


def test_get_all_pages_through_long_lists() -> None:
    rows = [{"n": i} for i in range(2500)]
    calls = []

    def fake(path: str) -> dict[str, Any]:
        calls.append(path)
        offset = int(path.split("offset=")[1])
        return {"items": rows[offset : offset + qa.PAGE], "total": len(rows)}

    assert qa.get_all(fake, "/api/accounts?filter=all") == rows
    assert len(calls) == 3 and calls[0].endswith("?filter=all&limit=1000&offset=0")


def test_deal_mid_counts_the_band_midpoint() -> None:
    """D42, own reading: a band counts its midpoint; one number counts as given."""
    assert qa.deal_mid("USD 30–50k") == 40000
    assert qa.deal_mid("$1.5M - $2M") == 1_750_000
    assert qa.deal_mid("USD 40,000") == 40000
    assert qa.deal_mid(25000) == 25000
    assert qa.deal_mid("TBD") is None
    assert qa.deal_mid("approx 25k (2026)") == 25000
    assert qa.deal_mid("999k-1b") == 500_499_500


@pytest.mark.parametrize(
    "cell",
    [
        "USD 30-50k",
        "USD 30–50k",
        "30k to 50k",
        "$30k-$50k",
        "50k+",
        "~$40k",
        "$1.5M - $2M",
        "100k-1m",
        "approx 25k (2026)",
        "999k-1b",
        "USD 40,000",
        "TBD",
        "",
    ],
)
def test_deal_mid_agrees_with_the_sync(cell: str) -> None:
    """The gate's own reading and the sync must agree, or step 6 shows a false DIFF."""
    from saley.sales.parse import deal_value

    assert qa.deal_mid(cell) == deal_value(cell)


# ------------------------------------------------------------------ the browser seed (R2 phase 7)


def _counts(db: Session) -> dict[str, int]:
    tables = ("contact", "decision", "proposal", "commitment", "sync_outbox", "reminder")
    return {t: db.execute(text(f"select count(*) from {t}")).scalar_one() for t in tables}


def test_seed_makes_marked_rows_and_teardown_removes_all_of_them(synced: Session) -> None:
    from saley.qa import seed

    before = _counts(synced)
    info = seed.seed(synced)
    synced.commit()
    assert set(info.contacts) == {seed.RESEARCH_KEY, seed.RECONNECT_KEY, *seed.CARD_KEYS}
    assert len(info.cards) == 2 and len(info.decisions) == 2 and info.commitment
    assert "4 people" in info.summary()
    keys = synced.execute(text("select sheet_row_key from contact where name like 'QA Seed%'"))
    assert all(k.startswith("qa:") for (k,) in keys)

    with SignedClient(create_app()) as client:
        mine = client.get("/api/today?user=vaishnavi").json()["my_tasks"]["items"]
        research = [i for i in mine if i["name"] == "QA Seed Research"]
        assert [i["kind"] for i in research] == ["add_email"]
        qa = client.get("/api/today?user=qa").json()["my_tasks"]["items"]
        assert any(i["kind"] == "commitment" and "qa:" in i["title"] for i in qa)
        sid = client.get("/api/today?user=sid").json()["my_tasks"]["items"]
        assert [i["kind"] for i in sid if i["name"] == "QA Seed Reconnect"] == ["reconnect"]
        assert {i["name"] for i in sid if i["kind"] == "status_check"} >= {
            "QA Seed Meeting A",
            "QA Seed Meeting B",
        }
        inbox = client.get("/api/inbox").json()["items"]
        assert sum(1 for i in inbox if i["kind"] == "card" and "QA Seed" in i["title"]) == 2
        assert sum(1 for i in inbox if i["kind"] == "confirm") >= 2

    removed = seed.teardown(synced)
    synced.commit()
    assert removed["contacts"] == 4 and removed["cards"] == 2 and removed["commitments"] == 2
    assert _counts(synced) == before
    assert sum(seed.teardown(synced).values()) == 0  # a second teardown does nothing


def test_seed_gives_the_research_person_papers_and_a_working_on_source(synced: Session) -> None:
    """R5 T14 (people_drawer): "2 papers" with links from the seeded URLs only, and a
    working_on line with its source; teardown removes the fact with the person."""
    from saley.qa import seed

    info = seed.seed(synced)
    synced.commit()
    person = info.contacts[seed.RESEARCH_KEY]
    with SignedClient(create_app()) as client:
        item = client.get(f"/api/people/{person}").json()["item"]
    assert item["papers_label"] == "2 papers"
    assert [p["href"] for p in item["papers"]] == list(seed.PAPER_URLS)
    assert all(p["text"].startswith("qa.invalid/qa-seed-") for p in item["papers"])
    assert item["working_on"]["found"] and item["working_on"]["source"].startswith("note ")
    assert item["follow_up_by"]["rule"] == "the follow-up rule"
    seed.teardown(synced)
    synced.commit()
    left = synced.execute(
        text("select count(*) from fact where value ->> 'text' = :t"), {"t": seed.WORKING_ON_TEXT}
    ).scalar_one()
    assert left == 0


def test_seeding_twice_leaves_one_set(synced: Session) -> None:
    from saley.qa import seed

    seed.seed(synced)
    seed.seed(synced)
    synced.commit()
    assert (
        synced.execute(text("select count(*) from contact where name like 'QA Seed%'")).scalar_one()
        == 4
    )
    assert synced.execute(text("select count(*) from commitment")).scalar_one() == 2
    assert synced.scalar(text("select count(*) from users where short = 'qaadmin'")) == 1
    seed.teardown(synced)
    synced.commit()


def test_the_sync_neither_removes_nor_merges_qa_rows(synced: Session, raw_sheet: dict) -> None:
    from saley.qa import seed

    seed.seed(synced)
    synced.commit()
    run_sync(synced, build_tabs(raw_sheet), now_ist())
    synced.commit()
    alive = synced.execute(
        text("select count(*) from contact where sheet_row_key like 'qa:%' and removed_at is null")
    ).scalar_one()
    assert alive == 4
    seed.teardown(synced)
    synced.commit()


def test_a_qa_write_fails_before_any_cell_is_written(synced: Session) -> None:
    """The seeded people are not in the sheet: a push for them writes nothing (safety rule)."""
    from saley.qa import seed
    from saley.sales.outbox import push_pending
    from saley.sales.writer import FakeSheet
    from tests.conftest import sql_act

    info = seed.seed(synced)
    synced.commit()
    person = info.contacts[seed.CARD_KEYS[0]]
    result = sql_act(synced, "trishi", [{"role": "meeting_status", "value": "completed"}], [person])
    assert result["ok"]
    sheet = FakeSheet({"Outreach PoCs": [["Name"], ["Ann"]]})
    synced.execute(
        text(
            "insert into sheet_tab_status(tab, title, status) "
            "values ('poc', 'Outreach PoCs', 'ok') "
            "on conflict (tab) do update set title = excluded.title"
        )
    )
    synced.commit()
    stats = push_pending(sheet, now=now_ist())
    assert stats.done == 0
    assert not [c for c in sheet.calls if c[0].startswith("write")]
    assert sheet.tabs["Outreach PoCs"] == [["Name"], ["Ann"]]
    synced.expire_all()
    seed.teardown(synced)
    synced.commit()


def test_the_seed_refuses_outside_dev_and_without_an_account(
    synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from saley.core.settings import get_settings
    from saley.qa import seed

    monkeypatch.setenv("SALEY_ENV", "prod")
    get_settings.cache_clear()
    try:
        with pytest.raises(seed.SeedRefused, match="SALEY_ENV=dev"):
            seed.seed(synced)
    finally:
        monkeypatch.setenv("SALEY_ENV", "dev")
        get_settings.cache_clear()
    synced.execute(text("update account set removed_at = now()"))
    with pytest.raises(seed.SeedRefused, match="No account"):
        seed.seed(synced)
    synced.rollback()


def test_the_seed_script_runs_seed_then_teardown(
    synced: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("qa_seed_script", root / "scripts" / "qa_seed.py")
    assert spec and spec.loader
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    monkeypatch.setattr(script, "session_scope", lambda: _same(synced))
    assert script.main(["seed"]) == 0
    assert (
        synced.execute(text("select count(*) from contact where name like 'QA Seed%'")).scalar_one()
        == 4
    )
    assert script.main(["teardown"]) == 0
    assert (
        synced.execute(text("select count(*) from contact where name like 'QA Seed%'")).scalar_one()
        == 0
    )


class _same:
    """A session_scope stand-in that hands out the test session and commits on exit."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def __enter__(self) -> Session:
        return self.session

    def __exit__(self, *exc: object) -> None:
        self.session.commit()


# ------------------------------------------------------------------ R3: the news seed (phase 7)

NEWS_TABLES = (
    "account",
    "news_sources",
    "news_stories",
    "signal",
    "search_request",
    "search_hit",
    "user_seen",
    "sync_outbox",
)


def _news_counts(db: Session) -> dict[str, int]:
    return {t: db.execute(text(f"select count(*) from {t}")).scalar_one() for t in NEWS_TABLES}


def test_seed_makes_news_a_linked_story_a_newco_and_a_search_hit(synced: Session) -> None:
    from saley.qa import seed

    before = _news_counts(synced)
    info = seed.seed(synced)
    synced.commit()
    assert len(info.stories) == 2 and info.account and "2 stories" in info.summary()
    rows = synced.execute(
        text("select title, url, importance, scorer_backend from news_stories order by id")
    ).all()
    qa_rows = [r for r in rows if r.url.startswith("https://qa.invalid/")]
    assert len(qa_rows) == 2 and all(r.title.startswith("QA Seed") for r in qa_rows)
    assert all(r.scorer_backend == "rules" for r in qa_rows)  # no LLM call, ever
    source = synced.execute(text("select name, active from news_sources where name like 'QA%'"))
    assert [tuple(r) for r in source] == [("QA Seed Wire", False)]  # off: no job fetches it
    linked = synced.execute(
        text("select entity_name, verify, importance from v_signal_linked where title like 'QA%'")
    ).all()
    assert [tuple(r) for r in linked] == [("QA Seed Co", True, 5)]
    account = synced.execute(
        text("select name, sheet_row_key, people from v_account_coverage where name like 'QA%'")
    ).one()
    assert tuple(account) == ("QA Seed Co", "qa:account:newco", 0)
    hit = synced.execute(
        text("select url, title from v_search_hit where account_id = :a"), {"a": info.account}
    ).one()
    assert hit.url.startswith("https://www.linkedin.com/in/qa-seed") and "QA Seed" in hit.title

    with SignedClient(create_app()) as client:
        newco = client.get("/api/suggest/newco").json()["items"]
        row = next(i for i in newco if i["subject"] == "QA Seed Co")
        assert row["hits"] and row["hits"][0]["form"]["needs"] == []
        look = client.get("/api/today").json()["needs_look"]["items"]
        assert any(i["title"].startswith("QA Seed") for i in look)

    removed = seed.teardown(synced)
    synced.commit()
    assert removed["stories"] == 2 and removed["accounts"] == 1 and removed["sources"] == 1
    assert _news_counts(synced) == before
    assert sum(seed.teardown(synced).values()) == 0


def test_hide_other_news_marks_real_stories_for_the_qa_user_only(synced: Session) -> None:
    """D226: before `signals_pages` the real stories and signals are seen by the QA user, so the
    New tab shows the two seeded stories on page 1; nobody real is marked; teardown removes it."""
    from saley.qa import seed
    from tests.test_signals import add_story

    real = add_story(synced, "A real market story", importance=5)
    seed.seed(synced)
    synced.commit()
    assert seed.hide_other_news(synced) >= 1
    assert seed.hide_other_news(synced) == 0  # a second call adds nothing
    synced.commit()
    rows = synced.execute(
        text("select user_short, item_type, item_id from v_user_seen where item_type = 'story'")
    ).all()
    assert {(r.user_short, r.item_id) for r in rows} == {(seed.QA_USER, real.id)}
    with SignedClient(create_app()) as client:
        new = client.get("/api/signals?view=new", headers={"X-Saley-As": seed.QA_USER}).json()[
            "items"
        ]
        assert sorted(i["title"] for i in new) == sorted([seed.LINKED_TITLE, seed.QUIET_TITLE])
    seed.teardown(synced)
    synced.commit()
    left = synced.execute(text("select count(*) from user_seen where query_id = 'qa-setup'"))
    assert left.scalar_one() == 0


def test_seed_gives_events_topics_and_suggestion_lines(
    synced: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    """R5 T15: the seeded Funding story (signals_categories), three QA events with City and
    Venue, the registered one first though it starts last (events_page), a one-line text on
    each Suggestions card, and a sheet sync that never removes a `qa:` event."""
    from saley.qa import seed

    info = seed.seed(synced)
    synced.commit()
    assert len(info.events) == 3 and "3 events" in info.summary()
    topics = synced.execute(
        text("select title, topic from news_stories where url like 'https://qa.invalid/%'")
    ).all()
    assert {r.title: r.topic for r in topics}[seed.LINKED_TITLE] == "Funding"
    run_sync(synced, build_tabs(raw_sheet), now_ist())  # the sheet has no qa: event
    synced.commit()
    with SignedClient(create_app()) as client:
        events = client.get("/api/events?view=new&when=upcoming").json()["items"]
        mine = [e for e in events if e["name"].startswith("QA Seed")]
        assert [(e["name"], e["city"], e["registered"]) for e in mine] == [
            ("QA Seed Summit", "Bangalore", True),
            ("QA Seed Meetup", "London", False),
            ("QA Seed Expo", "Other", False),
        ]
        assert mine[0]["venue"] == seed.EVENTS[0][2]
        funding = client.get("/api/signals?view=new&cat=Funding").json()
        assert any(i["title"] == seed.LINKED_TITLE for i in funding["items"])
        newco = client.get("/api/suggest/newco?view=new").json()["items"]
        row = next(i for i in newco if i["subject"] == "QA Seed Co")
        assert row["line"] and row["line"].startswith("Add QA Seed Co")
    removed = seed.teardown(synced)
    synced.commit()
    assert removed["events"] == 3
    assert synced.execute(text(seed.QA_EVENTS)).all() == []


def test_reset_seen_clears_only_the_qa_items(synced: Session) -> None:
    from saley.qa import seed

    info = seed.seed(synced)
    real = synced.execute(
        text(
            "insert into news_stories (title, url, url_hash, fingerprint) "
            "values ('Real', 'https://real.example/r', 'h', 'f') returning id"
        )
    ).scalar_one()
    rows = [
        ("story", info.stories[0]),
        ("signal", 999_999),
        ("story", real),
        ("suggest_newco", info.account),
    ]
    for kind, item in rows:
        synced.execute(
            text(
                "insert into user_seen (user_id, item_type, item_id) "
                "select id, :k, :i from users where short = 'trishi'"
            ),
            {"k": kind, "i": item},
        )
    synced.commit()
    seed.reset_seen(synced)
    left = synced.execute(text("select item_type, item_id from user_seen order by 1, 2")).all()
    assert [tuple(r) for r in left] == [("signal", 999_999), ("story", real)]
    synced.execute(text("delete from user_seen"))
    synced.execute(text("delete from news_stories where id = :i"), {"i": real})
    seed.teardown(synced)
    synced.commit()


def test_teardown_removes_a_row_add_made_on_the_qa_company(synced: Session) -> None:
    from saley.qa import seed
    from tests.conftest import sql_row_add

    info = seed.seed(synced)
    synced.commit()
    before = _counts(synced)
    hit = synced.execute(
        text("select url from v_search_hit where account_id = :a"), {"a": info.account}
    ).scalar_one()
    result = sql_row_add(
        synced,
        "qa",  # L5: the teardown removes only the throwaway user's own row adds
        info.account,
        {"name": "QA Seed Person", "li_url": hit, "designation": "Head of Data"},
    )
    assert result["ok"], result
    assert _counts(synced)["sync_outbox"] == before["sync_outbox"] + 1
    undone = synced.execute(
        text("select saley_undo('qa', :w, 'dashboard')"), {"w": result["items"][0]["write_id"]}
    ).scalar_one()
    synced.commit()
    assert undone["ok"], undone
    seed.teardown(synced)
    synced.commit()
    left = synced.execute(text("select count(*) from decision where kind = 'row_add'")).scalar_one()
    assert left == 0 and _counts(synced)["sync_outbox"] == before["sync_outbox"]


def test_the_sync_keeps_the_qa_company(synced: Session, raw_sheet: dict) -> None:
    from saley.qa import seed

    seed.seed(synced)
    synced.commit()
    run_sync(synced, build_tabs(raw_sheet), now_ist())
    synced.commit()
    gone = synced.execute(
        text("select removed_at from account where sheet_row_key = 'qa:account:newco'")
    ).scalar_one()
    assert gone is None
    seed.teardown(synced)
    synced.commit()


# ------------------------------------------------------------------ R3: the news funnel check


class FixedCounter:
    """A counter that returns set numbers, to prove the check compares and does not trust."""

    def __init__(self, numbers: dict[str, int], latest: date | None = None) -> None:
        self.numbers = numbers
        self.latest = latest

    def counts(self, day: date) -> dict[str, int]:
        return dict(self.numbers)

    def latest_day(self) -> date | None:
        return self.latest


def test_news_funnel_is_counted_from_the_tables_and_compared(
    get: Any, raw_sheet: dict[str, Any], sheet_today: date, synced: Session
) -> None:
    from saley.core.clock import now_ist
    from saley.core.models import NewsSource, NewsSourceRun, NewsStory, Signal

    source = NewsSource(name="Lane", kind="google", url="https://news.example/rss")
    synced.add(source)
    synced.flush()
    synced.add(
        NewsSourceRun(
            source_id=source.id, items_seen=5, items_new=2, dropped_seen=1, dropped_gate=2
        )
    )
    for i in range(2):
        story = NewsStory(
            title=f"s{i}", url=f"https://x.test/{i}", url_hash=f"h{i}", fingerprint=f"f{i}"
        )
        story.importance, story.impact, story.tag = 4, 1, "Market"
        story.scored_at, story.scorer_backend = now_ist(), "rules"
        synced.add(story)
        synced.flush()
    synced.add(
        Signal(
            story_id=story.id, entity_type="account", entity_id=1, match_kind="alias", importance=4
        )
    )
    synced.commit()

    report = qa.verify(raw_sheet, sheet_today, get)
    assert report.ok, [r for r in report.rows if not r[3]]
    rows = {r[0]: r for r in report.rows}
    today = f"{sheet_today:%d %b}"
    assert rows[f"news fetched {today}"][1:3] == (5, 5)
    assert rows[f"news deduped {today}"][1:3] == (2, 2)
    assert rows[f"news linked {today}"][1:3] == (1, 1)
    assert rows[f"news dropped_gate {today}"][1:3] == (2, 2)
    assert rows[f"news funnel goes down {today}"][3] is True
    assert "health funnel = funnel endpoint" in rows


def test_news_funnel_check_fails_when_the_api_differs_from_the_tables(
    get: Any, raw_sheet: dict[str, Any], sheet_today: date
) -> None:
    wrong = dict.fromkeys(qa.FUNNEL_KEYS, 0) | {"scored": 7, "deduped": 7, "fetched": 9}
    report = qa.verify(raw_sheet, sheet_today, get, FixedCounter(wrong))
    assert not report.ok
    failed = {r[0] for r in report.rows if not r[3]}
    assert {f"news scored {sheet_today:%d %b}", f"news fetched {sheet_today:%d %b}"} <= failed


def test_news_funnel_check_also_looks_at_the_latest_day_with_news(
    get: Any, raw_sheet: dict[str, Any], sheet_today: date
) -> None:
    from datetime import timedelta

    earlier = sheet_today - timedelta(days=3)
    zeros = dict.fromkeys(qa.FUNNEL_KEYS, 0)
    report = qa.verify(raw_sheet, sheet_today, get, FixedCounter(zeros, earlier))
    assert report.ok
    assert f"news tried {earlier:%d %b}" in {r[0] for r in report.rows}


def test_a_funnel_that_goes_up_is_a_failure() -> None:
    report = qa.Report()
    steps = {"fetched": 1, "deduped": 3, "scored": 2, "linked": 0}
    counter = FixedCounter(dict.fromkeys(qa.FUNNEL_KEYS, 0) | steps)

    def get(path: str) -> dict[str, Any]:
        keys = [{"key": k, "n": counter.numbers[k]} for k in qa.FUNNEL_KEYS]
        return {"steps": keys, "news": {"steps": keys}}

    qa.check_news(report, get, counter, date(2026, 10, 19))
    broken = [r[0] for r in report.rows if not r[3]]
    assert broken == ["news funnel goes down 19 Oct"]


# ------------------------------------------------------------------ A11: the QA user


QA_HEADERS = {"X-Saley-Act": "1", "X-Saley-As": "qa"}


def _qa_users(db: Session) -> list[tuple[str, str, str]]:
    return [
        tuple(r) for r in db.execute(text("select short, name, role from users where short = 'qa'"))
    ]


def test_the_seed_makes_a_throwaway_qa_approver(synced: Session) -> None:
    from saley.qa import seed

    users = synced.scalar(text("select count(*) from users"))
    seed.seed(synced)
    synced.commit()
    assert _qa_users(synced) == [("qa", "QA Seed", "approver")]
    with SignedClient(create_app()) as client:
        people = [p["short"] for p in client.get("/api/me").json()["people"]]
        assert "qa" in people  # "View as" can pick the QA user
        assert client.get("/api/signals", headers={"X-Saley-As": "qa"}).status_code == 200
    seed.seed(synced)  # seeding again leaves exactly one qa user
    synced.commit()
    assert len(_qa_users(synced)) == 1
    seed.teardown(synced)
    synced.commit()
    assert _qa_users(synced) == [] and synced.scalar(text("select count(*) from users")) == users


def test_teardown_removes_the_ask_exchange_of_the_qa_user(synced: Session) -> None:
    """R4-B03: the Ask card check logs a bot_exchange row for qa; the FK must not block teardown."""
    from saley.qa import seed

    seed.seed(synced)
    synced.commit()
    owned = "select id from users where short = 'qa'"
    synced.execute(
        text(
            "insert into bot_exchange (user_id, surface, text, answer) "
            f"select id, 'dashboard', 'q', 'a' from users where id in ({owned})"
        )
    )
    synced.commit()
    removed = seed.teardown(synced)
    synced.commit()
    assert removed["qa_exchanges"] == 1 and removed["qa_user"] == 1
    assert _qa_users(synced) == []
    assert synced.scalar(text("select count(*) from bot_exchange")) == 0


def test_teardown_deletes_the_qa_user_with_its_seen_rows_and_decisions(synced: Session) -> None:
    from saley.qa import seed

    info = seed.seed(synced)
    synced.commit()
    real = synced.execute(
        text(
            "insert into news_stories (title, url, url_hash, fingerprint) "
            "values ('Real', 'https://real.example/r', 'h', 'f') returning id"
        )
    ).scalar_one()
    synced.execute(
        text(
            "insert into user_seen (user_id, item_type, item_id) "
            "select id, 'story', :i from users where short = 'trishi'"
        ),
        {"i": real},
    )
    synced.commit()
    with SignedClient(create_app()) as client:
        marked = client.post(
            "/api/seen",
            json={"item_type": "story", "ids": [real, info.stories[0]]},
            headers=QA_HEADERS,
        )
        assert marked.json()["marked"] == 2  # a REAL story, marked for the QA user only
        synced.execute(  # D155: only the owner marks it done, so the QA user takes the promise
            text(
                "update commitment set owner = (select id from users where short = 'qa') "
                "where id = :i"
            ),
            {"i": info.commitment},
        )
        synced.commit()
        done = client.post(f"/api/commitment/{info.commitment}/done", headers=QA_HEADERS)
        assert done.status_code == 200 and done.json()["write_id"]
        undo = client.post(f"/api/undo/{done.json()['write_id']}", headers=QA_HEADERS)
        assert undo.status_code == 200
    owned = "select id from users where short = 'qa'"
    assert synced.scalar(text(f"select count(*) from user_seen where user_id in ({owned})")) == 2
    assert synced.scalar(text(f"select count(*) from decision where user_id in ({owned})")) >= 1

    removed = seed.teardown(synced)
    synced.commit()
    assert removed["qa_user"] == 1 and removed["qa_seen_own"] == 1  # the story mark went with reset
    assert _qa_users(synced) == []
    # The real person's mark is untouched; so are real stories.
    left = synced.execute(
        text("select u.short, s.item_id from user_seen s join users u on u.id = s.user_id")
    ).all()
    assert [tuple(r) for r in left] == [("trishi", real)]
    assert synced.scalar(text("select count(*) from decision where undone_by is not null")) == 0
    synced.execute(text("delete from user_seen"))
    synced.execute(text("delete from news_stories where id = :i"), {"i": real})
    synced.commit()


def test_seed_gives_another_owners_promise_and_a_qa_admin(synced: Session) -> None:
    """R5 T16a (R5-C03, R5-N09): a `qa:` promise owned by Kushal with no due day (no reminder,
    no Today task, not in the pack) refuses the QA user's Done with the owner line and writes
    nothing; the throwaway admin signs "Run news now"; teardown removes both, request first."""
    from saley.qa import seed

    users = synced.scalar(text("select count(*) from users"))
    info = seed.seed(synced)
    synced.commit()
    assert info.other_commitment and "2 commitments" in info.summary()
    row = synced.execute(
        text(
            "select u.short, c.due, c.text from commitment c join users u on u.id = c.owner "
            "where c.id = :i"
        ),
        {"i": info.other_commitment},
    ).one()
    assert (row.short, row.due) == (seed.OTHER_OWNER, None) and row.text.startswith("qa:")
    admin = synced.execute(text("select role, name from users where short = 'qaadmin'")).one()
    assert tuple(admin) == ("admin", seed.QA_ADMIN_NAME)
    name = synced.scalar(text("select name from users where short = :o"), {"o": seed.OTHER_OWNER})
    with SignedClient(create_app()) as client:
        refused = client.post(f"/api/commitment/{info.other_commitment}/done", headers=QA_HEADERS)
        assert refused.status_code == 403
        line = f"This is {name}'s commitment. Only they can mark it done."
        assert refused.json()["detail"] == line
        listed = client.get(f"/api/commitments?owner={seed.OTHER_OWNER}").json()["items"]
        assert [c["state"] for c in listed if c["id"] == info.other_commitment] == ["open"]
        kushal = client.get(f"/api/today?user={seed.OTHER_OWNER}").json()["my_tasks"]["items"]
        assert not any(i["kind"] == "commitment" for i in kushal)  # no due day: no task
        run = client.post(
            "/api/admin/news/run", headers={"X-Saley-Act": "1", "X-Saley-As": seed.QA_ADMIN}
        )
        assert run.status_code == 200
        state = client.get("/api/admin/news/run", headers={"X-Saley-As": seed.QA_ADMIN}).json()
        assert state["latest"]["requested_by"] == seed.QA_ADMIN and state["open"] is True
    done = synced.scalar(
        text("select count(*) from decision where kind = 'commitment_done' and row_key = :k"),
        {"k": f"commitment:{info.other_commitment}"},
    )
    assert done == 0
    assert synced.scalar(text("select count(*) from reminder")) == 0
    removed = seed.teardown(synced)
    synced.commit()
    assert removed["qa_news_requests"] == 1 and removed["qa_admin"] == 1
    assert synced.scalar(text("select count(*) from news_run_request")) == 0
    assert synced.scalar(text("select count(*) from users")) == users


def _browser_qa() -> Any:
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "browser_qa_script", root / "scripts" / "browser_qa.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_group_that_ran_no_check_fails() -> None:
    run = _browser_qa().Run()
    run.group("silent", lambda: None)
    run.group("passes", lambda: run.expect(True, "never shown"))
    run.group("fails", lambda: run.expect(False, "boom"))
    assert run.named == {"silent": False, "passes": True, "fails": False}
    assert run.findings == ["silent: no check ran", "boom"]


class _Req:
    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.headers = headers or {"accept": "application/json", "x-saley-as": "kushal"}


class _Route:
    def __init__(self) -> None:
        self.verdict: tuple[str, dict[str, str] | None] | None = None

    def continue_(self, headers: dict[str, str] | None = None) -> None:
        self.verdict = ("continue", headers)

    def abort(self) -> None:
        self.verdict = ("abort", None)


def test_every_api_call_of_the_browser_run_is_made_as_the_qa_user() -> None:
    qa = _browser_qa()
    run = qa.Run()
    handle = qa.guard(run)
    route = _Route()
    handle(route, _Req("http://localhost:8090/api/signals?tab=all"))
    assert route.verdict is not None and route.verdict[0] == "continue"
    assert route.verdict[1]["x-saley-as"] == "qa" and route.verdict[1]["accept"]
    route = _Route()
    handle(route, _Req("http://127.0.0.1:8090/api/seen"))
    assert route.verdict == ("continue", {**_Req("").headers, "x-saley-as": "qa"})
    route = _Route()
    handle(route, _Req("http://localhost:8090/static/app/assets/index.js"))
    assert route.verdict == ("continue", None)  # static files are left alone
    route = _Route()
    handle(route, _Req("https://www.linkedin.com/in/x"))
    assert route.verdict == ("abort", None) and run.blocked == ["https://www.linkedin.com/in/x"]


def test_the_run_counts_seen_marks_that_real_people_got(synced: Session) -> None:
    qa = _browser_qa()
    from saley.qa import seed

    seed.seed(synced)
    since = qa.db_now()
    for short in ("qa", "trishi"):
        synced.execute(
            text(
                "insert into user_seen (user_id, item_type, item_id, seen_at) "
                "select id, 'story', 777, clock_timestamp() from users where short = :s"
            ),
            {"s": short},
        )
    synced.commit()
    assert qa.real_seen_marks(since) == 1  # only trishi counts; the QA user is the point
    synced.execute(text("delete from user_seen where item_id = 777"))
    seed.teardown(synced)
    synced.commit()


# ------------------------------------------------------------------ D113 the gate's own server


def test_d113_gate_plan_uses_the_test_database_and_its_own_slot() -> None:
    from saley.core.settings import Settings
    from saley.qa import gate

    s = Settings(
        database_url="postgresql+psycopg://saley:pw@localhost:5432/saley",
        database_url_ro="postgresql+psycopg://saley_ro:ro@localhost:5432/saley",
        test_database_url="postgresql+psycopg://saley:pw@localhost:5432/saley_test",
        web_port=8090,
        gate_port=8091,
    )
    plan = gate.plan(s)
    assert (plan.port, plan.database) == (8091, "saley_test")
    assert plan.env["WORKER_SLOT"] == "worker:gate" and plan.env["SHEET_WRITE_ENABLED"] == "false"
    assert plan.env["DATABASE_URL"].endswith("/saley_test")
    assert plan.env["DATABASE_URL_ADMIN"] == plan.env["DATABASE_URL"]
    assert plan.env["DATABASE_URL_RO"].startswith("postgresql+psycopg://saley_ro:ro@")
    assert plan.env["DATABASE_URL_RO"].endswith("/saley_test")


@pytest.mark.parametrize(
    ("over", "words"),
    [
        ({"gate_port": 8090}, "must differ from WEB_PORT"),
        ({"test_database_url": "postgresql+psycopg://s:p@h/saley"}, "must end in _test"),
        (
            {
                "database_url": "postgresql+psycopg://s:p@h/x_test",
                "test_database_url": "postgresql+psycopg://s:p@h/x_test",
            },
            "must not be the dev database",
        ),
    ],
)
def test_d113_gate_plan_refuses_unsafe_settings(over: dict, words: str) -> None:
    from saley.core.settings import Settings
    from saley.qa import gate

    base = {
        "database_url": "postgresql+psycopg://s:p@h/saley",
        "test_database_url": "postgresql+psycopg://s:p@h/saley_test",
        "web_port": 8090,
        "gate_port": 8091,
    }
    with pytest.raises(gate.GateConfigError, match=words):
        gate.plan(Settings(**{**base, **over}))


def test_d113_hold_and_reset_locks_then_rebuilds_and_frees_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from saley.qa import gate

    class Conn:
        def __init__(self) -> None:
            self.sql: list[str] = []
            self.closed = False

        def execute(self, sql: str, params: object = None) -> None:
            self.sql.append(sql)

        def close(self) -> None:
            self.closed = True

    made: list[Conn] = []
    upgrades: list[str] = []

    def connect(**kwargs: object) -> Conn:
        assert kwargs["dbname"] == "saley_test" and kwargs["autocommit"] is True
        made.append(Conn())
        return made[-1]

    monkeypatch.setattr(gate.psycopg, "connect", connect)
    monkeypatch.setattr(
        gate.command,
        "upgrade",
        lambda cfg, rev: upgrades.append(cfg.get_main_option("sqlalchemy.url")),
    )
    plan = gate.GatePlan(
        8091, "saley_test", {"DATABASE_URL": "postgresql+psycopg://s:p@h/saley_test"}
    )
    held = gate.hold_and_reset(plan)
    assert held is made[0] and not held.closed  # the caller keeps the lock while it serves
    assert held.sql[1] == "select pg_advisory_lock(%s)"
    assert held.sql[2:4] == ["drop schema public cascade", "create schema public"]
    assert "grant usage on schema public to saley_ro" in held.sql[4]
    assert upgrades == ["postgresql+psycopg://s:p@h/saley_test"]

    def broken(cfg: object, rev: str) -> None:
        raise RuntimeError("migration failed")

    monkeypatch.setattr(gate.command, "upgrade", broken)
    with pytest.raises(RuntimeError):
        gate.hold_and_reset(plan)
    assert made[1].closed is True  # a failed reset frees the lock at once


def test_a_report_with_zero_checks_is_not_a_pass() -> None:
    """Gate step 6 must not pass on an empty comparison (silent-failure review, R4)."""
    report = qa.Report()
    assert report.ok is False
    report.check("people", 1, 1)
    assert report.ok is True


def test_the_seed_makes_a_qa_pack_and_teardown_removes_it(synced: Session) -> None:
    """R4-B02: without a pack the page loads a 404, a console error in browser QA."""
    from saley.qa import seed as qa_seed

    qa_seed.seed(synced)
    week = synced.execute(text("select week_start from v_weekly_pack")).scalars().all()
    assert week == [qa_seed.QA_PACK_WEEK]
    removed = qa_seed.teardown(synced)
    synced.commit()
    assert removed["pack"] == 1
    assert synced.execute(text("select count(*) from weekly_pack")).scalar_one() == 0


def test_the_mock_sign_in_is_kept_apart_from_control_presses() -> None:
    """R4 step 9: the gate signs in by POST /auth/mock; that is set-up, not a control press."""
    qa = _browser_qa()
    run = qa.Run()
    handlers: dict[str, Any] = {}

    class _Page:
        def on(self, event: str, fn: Any) -> None:
            handlers[event] = fn

    qa.watch(_Page(), run)

    class _Post:
        method = "POST"

        def __init__(self, url: str) -> None:
            self.url = url
            self.headers = {"x-saley-act": "1"}

    handlers["request"](_Post("http://localhost:8091/auth/mock"))
    handlers["request"](_Post("http://localhost:8091/api/act"))
    assert run.sign_ins == ["POST http://localhost:8091/auth/mock"]
    assert run.non_get == ["POST http://localhost:8091/api/act"]


def test_shell_rows_writes_off_unless_test_tab(monkeypatch: pytest.MonkeyPatch) -> None:
    """R5-X01 (D161): shell rows run with sheet writes off unless test_tab."""
    import importlib.util
    import sys

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "verify_script_x01", root / "scripts" / "verify.py"
    )
    assert spec is not None and spec.loader is not None
    verify = importlib.util.module_from_spec(spec)
    sys.modules["verify_script_x01"] = verify
    spec.loader.exec_module(verify)
    env = {"DATABASE_URL": "t", "DATABASE_URL_ADMIN": "t", "DATABASE_URL_RO": "r"}
    monkeypatch.setattr(verify.gate, "plan", lambda: SimpleNamespace(env=env))
    monkeypatch.setenv("SHEET_WRITE_ENABLED", "true")
    seen: list[tuple[object, dict[str, str]]] = []
    monkeypatch.setattr(verify.matrix, "_shell", lambda row, e: seen.append((row, e)) or (True, ""))
    runner = verify.shell_runner()
    runner(SimpleNamespace(writes=None))
    runner(SimpleNamespace(writes="test_tab"))
    (_, plain), (_, tab) = seen
    assert plain["SHEET_WRITE_ENABLED"] == "false"
    assert tab["SHEET_WRITE_ENABLED"] == "true"  # the row owns its flag; only the test tab is hit
    assert plain["DATABASE_URL"] == "t"

    def bad() -> None:
        raise verify.gate.GateConfigError("same port")

    monkeypatch.setattr(verify.gate, "plan", bad)
    seen.clear()
    ok, note = verify.shell_runner()(SimpleNamespace(writes="test_tab"))
    assert not ok and "gate settings" in note and seen == []


def test_browser_qa_script_runs_main_as_a_script() -> None:
    """The T2 split once dropped the main guard: the gate then got no JSON and no verdict."""
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    out = subprocess.run(  # noqa: S603 - our own script, fixed arguments
        [sys.executable, str(root / "scripts" / "browser_qa.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0 and "--base" in out.stdout


def test_sheet_verify_checks_funnel(get: Any, raw_sheet: dict[str, Any], sheet_today: date) -> None:
    report = qa.verify(raw_sheet, sheet_today, get)
    rows = {r[0]: r for r in report.rows if r[0].startswith("funnel ")}
    assert set(rows) == {f"funnel {k}" for k in ("listed", *qa.FUNNEL_FROM)}
    assert all(r[3] and r[1] is not None for r in rows.values())
    assert rows["funnel listed"][1] == qa.recount(raw_sheet, sheet_today)[0]["people_total"]

    def moved(path: str) -> dict[str, Any]:
        body = get(path)
        if path.startswith("/api/today"):
            body = {**body, "funnel": [{**s, "n": s["n"] + 1} for s in body["funnel"]]}
        return body

    bad = qa.verify(raw_sheet, sheet_today, moved)
    assert not bad.ok
    assert {r[0] for r in bad.rows if not r[3] and r[0].startswith("funnel ")} == set(rows)
    assert "DIFF" in bad.markdown("t")


def test_sheet_verify_funnel_missing_from_the_api_is_a_diff(
    get: Any, raw_sheet: dict[str, Any], sheet_today: date
) -> None:
    def without(path: str) -> dict[str, Any]:
        body = get(path)
        return (
            {k: v for k, v in body.items() if k != "funnel"}
            if path.startswith("/api/today")
            else body
        )

    report = qa.verify(raw_sheet, sheet_today, without)
    assert not report.ok
    assert all(not r[3] for r in report.rows if r[0].startswith("funnel "))


def test_funnel_recount_is_cumulative() -> None:
    from collections import Counter

    sheet = Counter({"people_total": 9, "stage:Contacted": 2, "stage:Meeting": 1, "stage:Won": 1})
    assert qa.funnel_recount(sheet) == {
        "listed": 9,
        "contacted": 4,
        "connected": 2,
        "talking": 2,
        "met": 2,
        "won": 1,
    }


def test_hide_other_news_refuses_a_database_that_is_not_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T16b review: only the gate's `_test` database; never the dev database."""

    from saley.qa import seed

    class FakeSession:
        def execute(self, *_a: object, **_k: object) -> object:
            class Answer:
                def scalar_one(self) -> str:
                    return "saley"

            return Answer()

    with pytest.raises(RuntimeError, match="_test"):
        seed.hide_other_news(FakeSession())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text",
    [
        "No source until R2",
        "sheet writes off: Q-R2-1",
        "stage rules (D29)",
        "R1.1 gate",
        "Outreach PoCs:412:Priya Rao",
    ],
)
def test_ste_lint_refuses_internal_ids_in_ui_text(text: str) -> None:
    """The human, 21 Oct (Q-R5-14): no R<n>, Q-, D<n> or tab:row: text in any UI string."""
    from saley.qa import ste_lint

    found = ste_lint.check("words.py:X", text, [])
    assert any("internal id" in f.problem for f in found), text


@pytest.mark.parametrize(
    "text", ["Q4 goals", "P1 waiting on LinkedIn", "Run news now", "Due 14:00", "R&D lead"]
)
def test_ste_lint_keeps_plain_words_with_letters_and_digits(text: str) -> None:
    from saley.qa import ste_lint

    assert not [f for f in ste_lint.check("words.py:X", text, []) if "internal id" in f.problem]


def test_ste_lint_id_exemption_is_named_and_explained() -> None:
    from saley.qa import ste_lint

    # Q-R5-16: no exceptions; the old tooltip text is flagged like any other id.
    assert ste_lint.check("words.py:FOLLOW_UP_RULE", "from rule D31", [])
    assert not hasattr(ste_lint, "ID_ALLOWED")
