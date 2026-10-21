"""scripts/impeccable_report.py: the Impeccable score file is complete before the gate passes
(R5-I01, D196)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "impeccable_report", ROOT / "scripts" / "impeccable_report.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ir = _load()


def full(score: int = 3) -> dict:
    entry = {"scores": dict.fromkeys(ir.DIMENSIONS, score), "critical_open": 0}
    return {"pages": {p: {t: json.loads(json.dumps(entry)) for t in ir.THEMES} for p in ir.PAGES}}


@pytest.fixture
def reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(ir, "REPORTS", tmp_path)
    return tmp_path


def test_a_complete_file_passes_and_prints_the_table(
    reports: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (reports / "R5-impeccable.json").write_text(json.dumps(full()), encoding="utf-8")
    assert ir.main(["--check", "--release", "R5"]) == 0
    assert ir.main(["--table", "--release", "R5"]) == 0
    out = capsys.readouterr().out
    assert "| today | dark | 3 | 3 | 3 | 3 | 3 | 15/20 | Good |" in out
    assert "detector step was not run" in out


def test_missing_file_page_theme_or_dimension_fails(reports: Path) -> None:
    assert ir.main(["--check", "--release", "R5"]) == 1  # no file
    data = full()
    del data["pages"]["health"]
    del data["pages"]["today"]["dark"]
    del data["pages"]["people"]["light"]["scores"]["theming"]
    data["pages"]["events"]["light"]["scores"]["performance"] = 5
    data["pages"]["inbox"]["light"]["scores"]["integrity"] = True
    found = ir.problems(data)
    assert "health: no scores" in found
    assert "today dark: no scores" in found
    assert "people light: theming must be a whole number 0-4" in found
    assert "events light: performance must be a whole number 0-4" in found
    assert "inbox light: integrity must be a whole number 0-4" in found
    (reports / "R5-impeccable.json").write_text(json.dumps(data), encoding="utf-8")
    assert ir.main(["--check", "--release", "R5"]) == 1


def test_an_open_critical_finding_fails() -> None:
    data = full()
    data["pages"]["goals"]["light"]["critical_open"] = 2
    assert ir.problems(data) == ["goals light: 2 critical finding(s) still open"]


@pytest.mark.parametrize(
    ("score", "name"),
    [
        (20, "Excellent"),
        (18, "Excellent"),
        (14, "Good"),
        (10, "Acceptable"),
        (6, "Poor"),
        (5, "Critical"),
        (0, "Critical"),
    ],
)
def test_rating_bands_follow_audit_md(score: int, name: str) -> None:
    assert ir.band(score) == name
