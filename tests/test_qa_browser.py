"""The pure rules of the R5 browser checks (R5 T12, scripts/qa_browser/r5_rules.py).

Why: the browser checks run only in the gate, on a real page. These tests prove the rules they
apply (legend, links, coming-soon badges, pagers, themes, the /next entry) without a browser.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def rules() -> ModuleType:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        return importlib.import_module("qa_browser.r5_rules")
    finally:
        sys.path.remove(str(ROOT / "scripts"))


def test_entry_and_url_keep_the_legacy_form(rules: ModuleType) -> None:
    """--app next checks /next (D147); the legacy entry gives the R1-R4 URL /#today."""
    assert rules.entry_path("next") == "/next"
    assert rules.entry_path("legacy") == "/"
    assert rules.entry_path("other") == "/"
    base = "http://localhost:8090"
    assert rules.view_url(base, "/", "today") == f"{base}/#today"
    assert rules.view_url(base, "/next", "people") == f"{base}/next#people"


def test_legend_one_at_top_explains_every_tag(rules: ModuleType) -> None:
    good = {
        "legends": 1,
        "tags": ["late", "soon"],
        "refs": ["late"],
        "bare": [],
        "content": True,
        "above": True,
    }
    assert rules.legend_problems("blockers", good) == []
    assert rules.legend_problems("x", {"legends": 0}) == ["x: 0 legends, want 1"]
    assert rules.legend_problems("x", {**good, "legends": 2}) == ["x: 2 legends, want 1"]
    missing = rules.legend_problems("x", {**good, "refs": ["late", "p1"]})
    assert missing == ["x: tags not in the legend: ['p1']"]
    assert "the legend explains nothing" in rules.legend_problems("x", {**good, "tags": []})[0]
    assert "without swatch" in rules.legend_problems("x", {**good, "bare": ["late"]})[0]
    assert "not at the top" in rules.legend_problems("x", {**good, "above": False})[0]
    # A page with nothing after the legend is a fail, never a trivial pass (T12 review).
    empty = rules.legend_problems("x", {**good, "content": False, "above": False})
    assert empty == ["x: no page content after the legend"]


def test_links_never_underlined_never_raw_urls(rules: ModuleType) -> None:
    clean = [{"text": "Today", "href": "#today", "decoration": "none"}]
    assert rules.link_problems("today", clean) == []
    under = [{"text": "Open", "href": "#x", "decoration": "underline"}]
    assert rules.link_problems("v", under) == ["v: underlined link 'Open'"]
    raws = ("https://example.com/a", "www.example.com", "see ftp://x", "example.com/papers")
    for raw in (*raws, "arxiv.org/abs/2410.1"):
        found = rules.link_problems("v", [{"text": raw, "href": raw, "decoration": "none"}])
        assert found and "raw URL" in found[0], raw
    # Names and plain words are not URLs; an icon-only link has no text.
    for text in ("", "Sarvam AI", "Sarvam.ai", "Open the current page", "12/10"):
        link = {"text": text, "href": "#a", "decoration": "none"}
        assert rules.link_problems("v", [link]) == [], text


def test_hover_one_link_per_kind_never_underlined(rules: ModuleType) -> None:
    links = [{"kind": "rail"}, {"kind": "rail"}, {"kind": "tile"}, {}, {"kind": "card"}, {}]
    assert rules.hover_picks(links) == [0, 2, 3, 4]
    ok = [{"kind": "rail", "decoration": "none none"}]
    assert rules.hover_problems("v", ok) == []
    child = [{"kind": "card", "decoration": "none underline"}]
    assert rules.hover_problems("v", child) == ["v: card link underlined on hover"]


def soon_page(
    hosts: tuple = (), soon: tuple = (), gates: tuple = (), gated: tuple = ()
) -> dict[str, list[str]]:
    return {"hosts": list(hosts), "soon": list(soon), "gates": list(gates), "gated": list(gated)}


def test_soon_badge_iff_feature_off_where_it_lives(rules: ModuleType) -> None:
    features = {"find_people": False, "email_write": True, "apollo": False}
    fp = ("find_people",)
    shown = {"today": soon_page(("find_people", "email_write"), fp, fp, fp), "events": soon_page()}
    # apollo is off, but its pages (Sources: admin only) were not visited: not demanded.
    assert rules.soon_problems(features, shown) == []
    assert rules.demanded(features, shown) == ["find_people"]
    wrong = {"today": soon_page(fp, ("find_people", "email_write"))}
    assert rules.soon_problems(features, wrong) == [
        "today: 'coming soon' on 'email_write', which is on"
    ]
    missing = {"today": soon_page(fp)}
    assert rules.soon_problems(features, missing) == [
        "today: 'find_people' is off but the page shows no badge"
    ]
    ungated = {"today": soon_page(fp, fp, fp)}
    assert rules.soon_problems(features, ungated) == [
        "today: 'find_people' is off but its place shows no badge"
    ]
    unknown = {"today": soon_page(soon=("teleport",))}
    assert "unknown feature 'teleport'" in rules.soon_problems(features, unknown)[0]
    visited = {"sources": soon_page(("apollo",), ("apollo",))}
    assert rules.soon_problems(features, visited) == []
    assert rules.demanded(features, visited) == ["apollo"]


def test_pagers_never_pass_on_no_pager_unless_allowed(rules: ModuleType) -> None:
    assert rules.pagers_verdict([], allow_none=False) == ["no pager verified on any page"]
    assert rules.pagers_verdict([], allow_none=True) == []
    assert rules.pagers_verdict(["today"], allow_none=False) == []


@pytest.mark.parametrize(
    ("pager", "problems"),
    [
        ({"total": 25, "offset": 0, "shown": 10, "rows": 10, "more": "15 more"}, []),
        ({"total": 25, "offset": 20, "shown": 5, "rows": 5, "more": "21–25 of 25"}, []),
        ({"total": 8, "offset": 0, "shown": 8, "rows": 8, "more": ""}, []),
        (
            {"total": 30, "offset": 0, "shown": 12, "rows": 12, "more": "18 more"},
            ["v: 12 rows on one page, want at most 10"],
        ),
        (
            {"total": 25, "offset": 0, "shown": 10, "rows": 9, "more": "15 more"},
            ["v: 9 rows but the pager says 10"],
        ),
        (
            {"total": 25, "offset": 0, "shown": 10, "rows": 10, "more": "14 more"},
            ["v: says 14 more, want 15"],
        ),
        (
            {"total": 25, "offset": 0, "shown": 10, "rows": 10, "more": ""},
            ["v: no 'N more' line (''), 15 not shown"],
        ),
    ],
)
def test_pager_ten_rows_and_n_more(rules: ModuleType, pager: dict, problems: list[str]) -> None:
    assert rules.pager_problems("v", pager) == problems


def test_pager_survives_missing_numbers(rules: ModuleType) -> None:
    assert rules.pager_problems("v", {"rows": 0}) == []


def test_theme_toggle_saves_and_survives_reload(rules: ModuleType) -> None:
    good = {
        "before_dark": False,
        "after_dark": True,
        "saved": "dark",
        "reload_dark": True,
        "before_bg": "rgb(250, 249, 246)",
        "after_bg": "rgb(19, 21, 25)",
    }
    assert rules.theme_problems(good) == []
    assert rules.theme_problems(
        {**good, "after_dark": False, "saved": "light", "reload_dark": False}
    ) == ["the toggle did not change the theme"]
    assert "not saved" in rules.theme_problems({**good, "saved": None})[0]
    assert rules.theme_problems({**good, "reload_dark": False}) == [
        "the theme was lost after a reload"
    ]
    assert rules.theme_problems({**good, "after_bg": good["before_bg"]}) == [
        "both themes paint the same background"
    ]


@pytest.fixture
def browser_qa(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    path = ROOT / "scripts" / "browser_qa.py"
    spec = importlib.util.spec_from_file_location("browser_qa_r5", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakePage:
    def set_default_timeout(self, ms: int) -> None:
        self.timeout = ms

    def route(self, pattern: str, handler: object) -> None:
        self.routed = pattern


def test_browser_qa_cli_runs_the_r5_checks_on_next(
    browser_qa: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--app next runs sign-in, views and the R5 checks of the matrix on /next; the
    --allow-no-pager flag reaches `pagers` only; the default app stays the legacy one."""
    parser = browser_qa.build_parser()
    argv = ["--base", "http://localhost:8090", "--release", "R5"]
    args = parser.parse_args([*argv, "--app", "next", "--allow-no-pager"])
    assert (args.app, args.allow_no_pager) == ("next", True)
    default = parser.parse_args(argv)
    assert (default.app, default.allow_no_pager) == ("legacy", False)
    with pytest.raises(SystemExit):
        parser.parse_args([*argv, "--app", "other"])

    calls: list[tuple] = []

    def sign_in(page: object, base: str, run: object) -> None:
        calls.append(("sign_in",))

    def views(page: object, base: str, run: object, shots: Path, width: int, entry: str) -> None:
        calls.append(("views", width, entry))

    monkeypatch.setattr(browser_qa, "guard", lambda run: None)
    monkeypatch.setattr(browser_qa, "watch", lambda page, run: None)
    monkeypatch.setattr(browser_qa, "sign_in_page", sign_in)
    monkeypatch.setattr(browser_qa, "views", views)

    def fake(name: str) -> object:
        def check(page: object, base: str, run: object, entry: str, *extra: object) -> None:
            run.checks += 1  # type: ignore[attr-defined]
            calls.append((name, entry, *extra))

        return check

    for name in browser_qa.R5_CHECKS:
        monkeypatch.setattr(browser_qa.checks_r5, name, fake(name))
    base = "http://localhost:8090"
    browser_qa.check_all_next(
        FakePage(), base, browser_qa.Run(), ROOT, (1440,), False, "/next", True
    )
    assert calls[:2] == [("sign_in",), ("views", 1440, "/next")]
    assert calls[2:] == [
        ("csp_clean", "/next"),
        ("legend_every_page", "/next"),
        ("no_underlined_links", "/next"),
        ("coming_soon_badges", "/next"),
        ("themes", "/next"),
        ("pagers", "/next", True),
        ("today_funnel", "/next"),
        ("no_page_search", "/next"),
        ("done_undo", "/next"),
        ("needs_look_cards", "/next"),
        ("add_email", "/next"),
        ("goals_cards", "/next"),
        ("accounts_page", "/next"),
        ("people_drawer", "/next"),
        ("seen_tabs", "/next"),
        ("signals_categories", "/next"),
        ("events_page", "/next"),
        ("no_view_as", "/next"),
        ("load_error_retry", "/next"),
    ]
    matrix = (ROOT / "tests" / "matrix" / "R5.yaml").read_text(encoding="utf-8")
    for name in browser_qa.R5_CHECKS:
        assert f"browser {name}" in matrix, name
    calls.clear()
    browser_qa.check_all_next(FakePage(), base, browser_qa.Run(), ROOT, (390,), True, "/next")
    assert [c[0] for c in calls] == ["sign_in", "views"]  # views-only: the extra widths (D112)


# ---------------------------------------------------------------- T13: Today checks

FUNNEL_API = [
    {"key": "listed", "n": 40},
    {"key": "contacted", "n": 12},
    {"key": "won", "n": None},
]


def test_funnel_bars_equal_the_api_counts(rules: ModuleType) -> None:
    """R5-T02: one bar per step in order; the count is the API's, unknown is "—"."""
    bars = [
        {"key": "listed", "value": "40", "href": "#people?filter=all"},
        {"key": "contacted", "value": "12", "href": "#people?filter=reached_contacted"},
        {"key": "won", "value": "—", "href": "#people?filter=stage%3AWon"},
    ]
    assert rules.funnel_problems(FUNNEL_API, bars) == []
    wrong = [dict(bars[0], value="0"), bars[1], dict(bars[2], href="")]
    found = rules.funnel_problems(FUNNEL_API, wrong)
    assert any("listed" in f and "'0'" in f for f in found)
    assert any("won" in f and "no link" in f for f in found)
    assert rules.funnel_problems(FUNNEL_API, bars[:2]) != []
    assert rules.funnel_problems([], bars) == ["/api/today has no funnel"]


def test_blockers_line_has_count_label_and_button(rules: ModuleType) -> None:
    """R5-T02: one line in the tile area, API count and label, an "Open blockers" Button."""
    api = {"count": 2, "label": "2 late items block goals", "link": {"view": "blockers"}}
    good = {
        "lines": 1,
        "inside_tiles": True,
        "count": "2",
        "text": "2 late items block goals\nOpen blockers",
        "button": "Open blockers",
        "button_slot": "button",
        "href": "#blockers",
    }
    assert rules.blockers_problems(api, good) == []
    assert rules.blockers_problems({**api, "count": None}, {**good, "count": "—"}) == []
    assert rules.blockers_problems(api, {**good, "lines": 0}) == ["0 blockers lines, want 1"]
    bad = {**good, "inside_tiles": False, "count": "0", "button_slot": None, "href": "#x"}
    assert len(rules.blockers_problems(api, bad)) == 4
    assert rules.blockers_problems(None, good) == ["/api/today has no blockers_line"]


def test_no_page_search_input(rules: ModuleType) -> None:
    """R5-T03: the Ask field is not a search box; a search input or label is."""
    ask = {"id": "ask-input", "type": "text", "label": "Your question", "placeholder": "Ask"}
    assert rules.page_search_problems("today", [ask]) == []
    typed = {"id": "q", "type": "search", "label": "", "placeholder": ""}
    named = {"id": "f", "type": "text", "label": "Search people", "placeholder": ""}
    assert len(rules.page_search_problems("goals", [typed, named])) == 2


def test_undo_in_place_without_timer_bar_or_toast(rules: ModuleType) -> None:
    """R5-T09: the same button, one Undo, no "4:59", no #toast-undo, no toast."""
    good = {
        "same_button": True,
        "undo": 1,
        "text": "Reconnect\nUndo",
        "bars": 0,
        "toasts": 0,
        "focused": True,
    }
    assert rules.undo_row_problems(good) == []
    bad = {"same_button": False, "undo": 2, "text": "Undo (4:59)", "bars": 1, "toasts": 1}
    assert len(rules.undo_row_problems(bad)) == 6  # focus lost too
    assert rules.undo_row_problems({**good, "focused": False}) == [
        "focus left the button after Done"
    ]
    assert rules.undo_row_problems({**good, "text": "Due Fri 17 Oct"}) == []


def test_undo_turns_back_into_done_in_place_with_focus(rules: ModuleType) -> None:
    """R5-T09 after Undo: the same (marked) button is Done again, no Undo, focus kept."""
    assert rules.done_back_problems({"same_button": True, "undo": 0, "focused": True}) == []
    found = rules.done_back_problems({"same_button": False, "undo": 1, "focused": False})
    assert len(found) == 3


def test_look_cards_one_link_with_badge(rules: ModuleType) -> None:
    """R5-T13: each card one link, its kind badge, no inner link or button."""
    api = [{"kind": "news"}, {"kind": "blocker"}]
    card = {"tag": "A", "href": "#signals", "badge_text": "News", "inner": 0}
    cards = [
        {**card, "kind": "news", "badge": "news"},
        {**card, "kind": "blocker", "badge": "blocker", "badge_text": "Blocker"},
    ]
    assert rules.look_card_problems(api, cards) == []
    broken = [{**cards[0], "tag": "DIV", "inner": 1}, {**cards[1], "badge": None}]
    found = rules.look_card_problems(api, broken)
    assert len(found) == 3
    assert rules.look_card_problems(api, cards[:1]) == ["1 cards, API 2"]
    assert rules.look_card_problems([], []) == []


def test_today_controls_are_buttons_to_allowed_posts(rules: ModuleType) -> None:
    """Gate step 9 on the new Today: Done, Undo, email Save and Ask are plain buttons aimed at
    an allowed POST path; anything else is a finding."""
    allowed = {"/api/act", "/api/undo/7", "/api/ask"}.__contains__
    good = [
        {"act": "done", "type": "button", "endpoint": "/api/act"},
        {"act": "undo", "type": "button", "endpoint": "/api/undo/7"},
        {"act": "ask", "type": "button", "endpoint": "/api/ask"},
    ]
    assert rules.control_problems("today", good, allowed) == []
    bad = [
        {"act": "done", "type": "submit", "endpoint": "/api/act"},
        {"act": "email-save", "type": "button", "endpoint": "/api/admin/x"},
    ]
    assert len(rules.control_problems("today", bad, allowed)) == 2


# ---------------------------------------------------------------- D207: R5-V01, R5-V02


def test_load_error_and_view_as_rules(rules: ModuleType) -> None:
    """R5-V01: a failed status load names the data, the reason and a Retry button, never the
    raw "Failed to fetch"; after Retry the Updated line is back. R5-V02: no picker hooks, no
    "View as" words, and Today asks /api/today with no user= (D207)."""
    good = {
        "text": "Could not load the status line. The server did not answer.",
        "retry_tag": "BUTTON",
    }
    assert rules.load_error_problems(good) == []
    raw = {"text": "Could not load: Failed to fetch", "retry_tag": "BUTTON"}
    problems = rules.load_error_problems(raw)
    assert len(problems) == 3 and "raw browser text" in problems[2]
    assert rules.load_error_problems({**good, "retry_tag": "A"}) == ["no Retry button (got 'A')"]
    assert rules.load_error_problems({}) != []
    back = {"text": "Updated 14:02 IST · sheet 14:00", "retry_visible": False}
    assert rules.recovered_problems(back) == []
    assert len(rules.recovered_problems({"text": good["text"], "retry_visible": True})) == 2
    assert rules.recovered_problems({}) != []

    hooks = {
        "#act-as": 0,
        "#viewing-as": 0,
        "#who": 0,
        "header [data-person], [aria-pressed][data-person]": 0,
    }
    clean = {"hooks": hooks, "words": "Search Updated 14:02", "today_urls": ["/api/today"]}
    assert rules.view_as_problems(clean) == []
    picker = {
        **clean,
        "hooks": {**hooks, "header [data-person], [aria-pressed][data-person]": 3},
        "words": "Viewing as Sid",
    }
    assert rules.view_as_problems(picker) == [
        "header [data-person], [aria-pressed][data-person] is on the page (3)",
        "the top bar says 'Viewing as'",
    ]
    other = {**clean, "today_urls": ["http://localhost/api/today?tasks_offset=10&user=sid"]}
    assert rules.view_as_problems(other) == [
        "Today asked for another person: http://localhost/api/today?tasks_offset=10&user=sid"
    ]
    assert rules.view_as_problems({**clean, "today_urls": []}) == [
        "the page did not load /api/today"
    ]


def test_with_query_and_abort_noise(rules: ModuleType) -> None:
    """today_list_of sets `user=` once (an old value is replaced, other params stay); the
    check drops only the console lines of the requests it aborted on purpose."""
    base = "http://localhost:8090/api/today"
    assert rules.with_query(base, "user", "sid") == f"{base}?user=sid"
    assert rules.with_query(f"{base}?tasks_offset=10&user=vee", "user", "sid") == (
        f"{base}?tasks_offset=10&user=sid"
    )
    noise = rules.ABORT_NOISE
    found = [noise, "console: other", noise]
    assert rules.drop_abort_noise(found, 1) == ["console: other", noise]
    assert rules.drop_abort_noise(found, 0) == found


# ---------------------------------------------------------------- T14: goals, accounts, people

GOAL = {
    "code": "G1",
    "progress": {"won": 3, "need": 2, "pct": 150},
    "active": [{"id": i} for i in range(6)],
    "blockers": [{"short": "x"}],
}
CARD = {
    "code": "G1",
    "ring": "150%\n3 won of 2 needed",
    "people_box": True,
    "people_rows": 4,
    "people_more": 2,
    "blockers_box": True,
    "blockers_rows": 1,
    "blockers_more": None,
}


def test_goal_cards_ring_rows_and_more(rules: ModuleType) -> None:
    """R5-G02: the ring shows the real percent (also above 100) or "—"; each list shows its
    first rows and "+N more" counts the rest; a card per goal, in API order."""
    assert rules.ring_text({"pct": 150}) == "150%"
    assert rules.ring_text({"pct": None}) == "—" and rules.ring_text(None) == "—"
    assert rules.goal_card_problems([GOAL], [CARD]) == []
    assert rules.goal_card_problems([], []) == ["/api/goals has no goal"]
    no_need = {**GOAL, "progress": {"won": 0, "need": None, "pct": None}}
    assert rules.goal_card_problems([no_need], [CARD]) == [
        "G1: ring shows '150%\\n3 won of 2 needed', want '—'"
    ]
    bad = {**CARD, "people_rows": 6, "people_more": None, "blockers_more": 1}
    assert rules.goal_card_problems([GOAL], [bad]) == [
        "G1: 6 people rows, want 4 of 6",
        "G1: '+N more' for people says None, want 2",
        "G1: a '+N more' Button for blockers with nothing hidden",
    ]
    assert rules.goal_card_problems([GOAL, {**GOAL, "code": "G2"}], [CARD]) == [
        "goal cards ['G1'], want ['G1', 'G2']"
    ]
    assert rules.goal_card_problems([GOAL], [{**CARD, "blockers_box": False}]) == [
        "G1: no blockers list"
    ]


def test_more_button_expands_in_place(rules: ModuleType) -> None:
    assert rules.expand_problems(4, 6, 2, "BUTTON") == []
    assert rules.expand_problems(4, 4, 2, "A") == [
        "'+N more' is a 'A', not a button",
        "'+N more' opened 0 rows, want 2",
    ]


ACCOUNT = {
    "id": 7,
    "name": "Acme",
    "people_label": "13 people · 9 P1",
    "stage_counts": {"Listed": 4, "Engaged": 9, "Won": 0},
}


def test_account_rows_label_and_stage_bar(rules: ModuleType) -> None:
    """R5-A03: each row reads `people_label` and has one stage bar equal to `stage_counts`
    (a stage with 0 people has no segment)."""
    row = {
        "id": "7",
        "label": "13 people · 9 P1",
        "bars": 1,
        "segments": {"Listed": 4, "Engaged": 9},
    }
    assert rules.account_row_problems([ACCOUNT], [row]) == []
    assert rules.account_row_problems([], []) == ["/api/accounts has no account"]
    assert rules.account_row_problems([ACCOUNT], []) == ["Acme: no row"]
    wrong = {**row, "label": "P1 · 13", "bars": 0, "segments": {"Listed": 4}}
    assert rules.account_row_problems([ACCOUNT], [wrong]) == [
        "Acme: shows 'P1 · 13', API '13 people · 9 P1'",
        "Acme: 0 stage bars, want 1",
        "Acme: stage bar {'Listed': 4}, API {'Listed': 4, 'Engaged': 9}",
    ]
    legend = [{"tag": "Qualified"}, {"tag": "Single-threaded"}]
    assert rules.legend_names_problems(legend, "Qualified A deal. Single-threaded One.") == []
    assert rules.legend_names_problems(legend, "Qualified") == [
        "the legend does not name 'Single-threaded'"
    ]
    assert rules.legend_names_problems([], "x") == ["/api/accounts has no tags_legend"]


def test_timeline_strip_and_company_drawer(rules: ModuleType) -> None:
    """R5-A03 / R5-P03: one strip, at most 12 month rows in API order; an empty line when the
    API has no touch. The company drawer never says Evidence."""
    months = [{"month": f"2026-{m:02d}", "touches": []} for m in range(10, 0, -1)]
    months[0]["touches"] = [{"day": "2026-10-02", "type": "email1"}]
    names = [m["month"] for m in months]
    assert rules.timeline_problems("d", months, {"strips": 1, "months": names}) == []
    assert rules.timeline_problems("d", months, {"strips": 0}) == [
        "d: 0 [data-timeline] strips, want 1"
    ]
    many = {"strips": 1, "months": names + ["x"] * 3}
    assert rules.timeline_problems("d", months, many)[0] == "d: 13 month rows, want at most 12"
    quiet = [{"month": "2026-10", "touches": []}]
    assert rules.timeline_problems("d", quiet, {"strips": 1, "months": [], "empty": True}) == []
    assert rules.timeline_problems("d", quiet, {"strips": 1, "months": []}) == [
        "d: no touches but no empty line"
    ]
    assert rules.company_drawer_problems({"text": "People\nTouches"}) == []
    assert rules.company_drawer_problems({"text": "Evidence\nrow 12"}) == [
        "the company drawer shows 'Evidence'"
    ]


PERSON = {
    "papers_label": "2 papers",
    "papers": [
        {"text": "qa.invalid/qa-seed-speech-evals", "href": "https://qa.invalid/papers/a"},
        {"text": "A stored title", "href": "https://qa.invalid/papers/b"},
    ],
    "working_on": {"found": True, "source": "note Tue 20 Oct"},
}


def _link(text: str, href: str, **extra: str) -> dict:
    return {
        "text": text,
        "href": href,
        "rel": "noopener noreferrer",
        "target": "_blank",
        "decoration": "none",
        **extra,
    }


def test_person_drawer_follow_up_papers_and_source(rules: ModuleType) -> None:
    """R5-P03: "Follow-up by" with a D31 tooltip, the papers Button reads `papers_label`, its
    popover lists the API links (plain, new tab, rel noopener), and the source shows."""
    links = [_link(p["text"], p["href"]) for p in PERSON["papers"]]
    facts = {
        "label": "Follow-up by",
        "tip": "from rule D31",
        "tooltip": "from rule D31",
        "papers_text": "2 papers",
        "links": links,
        "working_source": "Source: note Tue 20 Oct",
    }
    assert rules.person_drawer_problems(PERSON, facts) == []
    bad = {
        **facts,
        "label": "Owner",
        "tooltip": "",
        "papers_text": "Papers",
        "links": [_link("x", "https://qa.invalid/papers/a", rel="", decoration="underline")],
        "working_source": "",
    }
    found = rules.person_drawer_problems(PERSON, bad)
    assert found[0] == "no 'Follow-up by' label ('Owner')"
    assert "the Follow-up by tooltip does not say D31" in found
    assert "papers reads 'Papers', API '2 papers'" in found
    assert any(f.startswith("popover links") for f in found)
    assert "paper link 'https://qa.invalid/papers/a' has no rel noopener" in found
    assert "paper link 'https://qa.invalid/papers/a' is underlined" in found
    assert found[-1] == "working_on source 'note Tue 20 Oct' not shown"
    none = {"papers_label": "0 papers", "papers": [], "working_on": {"found": False}}
    plain = {"label": "Follow-up by", "tip": "D31", "tooltip": "D31", "papers_text": "0 papers"}
    assert rules.person_drawer_problems(none, plain) == []


def test_seeded_person_needs_papers_and_a_button(rules: ModuleType) -> None:
    """R5-P03: the check fails on a broken seed instead of comparing [] with []."""
    assert rules.seeded_person_problems(PERSON, {"has_papers_button": True}) == []
    assert rules.seeded_person_problems({"papers": []}, {}) == [
        "the seeded person has no papers in /api/people",
        "no papers Button in the drawer",
    ]


# ---------------------------------------------------------------- T15: Signals, Suggestions, Events

SEEN_API = {
    "new_total": 12,
    "seen_total": 40,
    "items": [{"id": i, "seen": False} for i in range(10)],
}


def _seen_facts(**change: object) -> dict:
    facts = {
        "tabs": {"new": "12", "seen": "40"},
        "pressed": "new",
        "rows": 10,
        "text": "New 12 Seen earlier 40",
        "nothing_new": False,
    }
    return {**facts, **change}


def test_seen_tabs_equal_the_answer_and_say_nothing_new(rules: ModuleType) -> None:
    """R5-N03: tab counts are the answer's, at most 10 rows, no "left out", the empty New line."""
    assert rules.seen_tab_problems("signals", SEEN_API, _seen_facts()) == []
    bad = _seen_facts(tabs={"new": "10"}, rows=11, text="207 you saw are left out")
    found = rules.seen_tab_problems("signals", SEEN_API, bad)
    assert "signals: tabs ['new'], want new and seen" in found
    assert "signals: the new tab says '10', the API new_total is 12" in found
    assert "signals: the seen tab says '', the API seen_total is 40" in found
    assert "signals: 11 rows on one page, want at most 10" in found
    assert "signals: a 'left out' line is on the page" in found
    empty = {"new_total": 0, "seen_total": 40, "items": []}
    nothing = _seen_facts(
        tabs={"new": "0", "seen": "40"}, rows=0, nothing_new=True, text=rules.NOTHING_NEW
    )
    assert rules.seen_tab_problems("suggest", empty, nothing) == []
    silent = {**nothing, "nothing_new": False, "text": ""}
    assert rules.seen_tab_problems("suggest", empty, silent) == [
        f"suggest: the empty New tab does not say '{rules.NOTHING_NEW}'"
    ]
    on_seen = {**nothing, "pressed": "seen"}
    assert rules.seen_tab_problems("suggest", empty, on_seen) == [
        f"suggest: '{rules.NOTHING_NEW}' shows with items or on Seen earlier"
    ]
    assert rules.seen_tab_problems("x", {}, _seen_facts()) == [
        "x: the page asked no list (no answer to compare)"
    ]


def test_seen_post_marks_exactly_the_shown_unseen_items(rules: ModuleType) -> None:
    """R5-N03: one POST /api/seen with the legacy body and the unseen ids of the page."""
    items = [{"id": 1, "seen": False}, {"id": 2, "seen": True}, {"id": 3, "seen": False}]
    good = [{"item_type": "story", "ids": [3, 1]}]
    assert rules.seen_post_problems("signals", "story", items, good) == []
    assert rules.seen_post_problems("signals", "story", items, []) == [
        "signals: 0 seen marks of story, want 1"
    ]
    wrong = [{"item_type": "story", "ids": [1, 2, 3], "query_id": "x"}]
    found = rules.seen_post_problems("signals", "story", items, wrong)
    assert found == [
        "signals: the mark sent [1, 2, 3], shown [1, 3]",
        "signals: the mark body has extra keys ['query_id']",
    ]
    seen_only = [{"id": 2, "seen": True}]
    assert rules.seen_post_problems("s", "story", seen_only, []) == []
    assert rules.seen_post_problems("s", "story", seen_only, good) == [
        "s: 1 seen marks for a page with nothing unseen"
    ]


def test_category_chips_are_the_six_names_never_letters(rules: ModuleType) -> None:
    """R5-N05 (D156)."""
    good = ["All", "Hiring", "Funding", "Product launch", "Research", "Partnership", "Event"]
    assert rules.category_chip_problems(good) == []
    found = rules.category_chip_problems(["All", "A", "B", "Funding"])
    assert found[0].startswith("category chips ['All', 'A', 'B', 'Funding']")
    assert found[1] == "category chips with a source letter: ['A', 'B']"


def test_picking_funding_asks_cat_funding_and_shows_only_funding(rules: ModuleType) -> None:
    """R5-N05: the request carries the name; a seeded story exists; rows are all Funding."""
    urls = [
        "http://localhost:8090/api/signals?tab=all&range=week&cat=Funding&view=new",
        "http://localhost:8090/api/signals?tab=all&range=week&cat=Funding&view=seen",
    ]
    answers = [{"new_total": 0, "seen_total": 1}, {"new_total": 0, "seen_total": 1}]
    assert rules.category_pick_problems("Funding", urls, answers, ["Funding"]) == []
    letter = ["http://localhost:8090/api/signals?cat=B&view=new"]
    found = rules.category_pick_problems("Funding", letter, [{"new_total": 0}], ["Hiring"])
    assert found == [
        "no /api/signals request with cat=Funding: ['B']",
        "a request asked a source letter: cat=['B']",
        "cat=Funding gave no story (the QA seed has one)",
        "cat=Funding: rows of other categories ['Hiring']",
    ]
    assert "cat=Funding: no row on New or Seen earlier" in rules.category_pick_problems(
        "Funding", urls, answers, []
    )


EVENT_ROWS = [
    {
        "name": "QA Seed Summit",
        "registered": True,
        "badge": True,
        "city": "Bangalore",
        "venue": "QA Hall, 1 MG Road, Bengaluru 560001",
    },
    {
        "name": "QA Seed Meetup",
        "registered": False,
        "badge": False,
        "city": "London",
        "venue": "QA Hub, 2 Kings Cross, London N1",
    },
    {
        "name": "QA Seed Expo",
        "registered": False,
        "badge": False,
        "city": "Other",
        "venue": "QA Centre, 3 Main Street, Berlin",
    },
]


def test_events_city_venue_registered_first_and_speaker_soon(rules: ModuleType) -> None:
    """R5-E02: City and Venue columns, registered first with the tag, three city names, soon."""
    facts = {"headers": ["Event", "City", "Venue", "Dates"], "rows": EVENT_ROWS, "soon": 1}
    assert rules.events_problems(facts) == []
    flipped = [EVENT_ROWS[1], {**EVENT_ROWS[0], "badge": False, "city": "Bengaluru"}]
    found = rules.events_problems({"headers": ["Event", "Location"], "rows": flipped, "soon": 0})
    assert found == [
        "no City column: ['Event', 'Location']",
        "no Venue column: ['Event', 'Location']",
        "registered events are not first: [False, True]",
        "Registered tag wrong on: ['QA Seed Summit']",
        "City values outside ['Bangalore', 'London', 'Other']: ['Bengaluru']",
        'no [data-soon="speaker_match"] badge',
    ]
    only_registered = {**facts, "rows": EVENT_ROWS[:1]}
    assert rules.events_problems(only_registered) == [
        "the page needs a registered and an unregistered row: [True]"
    ]
    assert rules.events_problems({**facts, "rows": []}) == ["no event row"]


def test_seeded_events_keep_city_venue_and_order(rules: ModuleType) -> None:
    """R5-E02: the QA events of saley.qa.seed show as the rule says; the registered one first."""
    from saley.qa.seed import EVENTS

    seeded = list(EVENTS)
    assert rules.seeded_event_problems(EVENT_ROWS, seeded) == []
    moved = [EVENT_ROWS[1], {**EVENT_ROWS[0], "venue": "QA Hall"}]
    assert rules.seeded_event_problems(moved, seeded) == [
        "QA Seed Summit: city 'Bangalore', venue 'QA Hall'",
        "the registered seeded event 'QA Seed Summit' is not first",
    ]
    assert rules.seeded_event_problems(EVENT_ROWS[1:], seeded) == [
        "the seeded event 'QA Seed Summit' is not on the page"
    ]
