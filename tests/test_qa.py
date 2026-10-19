"""saley.qa: the independent sheet recount behind `saley sheet verify` (gate step 6)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from saley.cli import app
from saley.qa import sheet_verify as qa
from saley.web.app import create_app
from tests.conftest import POC_HEADERS


@pytest.fixture
def get(synced: Session) -> Iterator[Any]:
    with TestClient(create_app()) as client:

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
