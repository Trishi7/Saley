"""saley.qa.matrix: the test-matrix loader and evaluator of the release gate (docs/16 §3)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from saley.qa import matrix

ROW = """
  - id: {id}
    feature: {feature}
    layer: {layer}
    what: a check
    tool: {tool}
    command: {command}
    pass: it passes
"""


def write_matrix(d: Path, release: str, *rows: dict[str, str]) -> None:
    body = "".join(
        ROW.format(**{"feature": f"F-{release}-01", "layer": "unit", "tool": "pytest", **r})
        for r in rows
    )
    (d / f"{release}.yaml").write_text(f"release: {release}\nrows:\n{body}", encoding="utf-8")


@pytest.fixture
def mdir(tmp_path: Path) -> Path:
    d = tmp_path / "matrix"
    d.mkdir()
    write_matrix(d, "R1.1", {"id": "A1", "command": "pytest tests/test_a.py::test_one"})
    write_matrix(d, "R2", {"id": "B1", "tool": "gate", "command": "step 8"})
    write_matrix(d, "R3", {"id": "C1", "tool": "browser", "command": "browser inbox_card"})
    return d


def test_loader_runs_this_release_and_every_earlier_one(mdir: Path) -> None:
    rows, errors = matrix.load("R2", mdir)
    assert errors == []
    assert [(r.release, r.id) for r in rows] == [("R1.1", "A1"), ("R2", "B1")]
    assert [r.id for r in matrix.load("R3", mdir)[0]] == ["A1", "B1", "C1"]
    assert matrix.load("R1.1", mdir)[0][0].id == "A1"


def test_releases_sort_as_numbers(tmp_path: Path) -> None:
    for rel in ("R1.10", "R1.2", "R2"):
        write_matrix(tmp_path, rel, {"id": rel, "command": "pytest x.py"})
    assert matrix.releases_up_to("R1.10", tmp_path) == ["R1.2", "R1.10"]


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        ({"id": "X", "layer": "ui", "command": "pytest a.py"}, "layer"),
        ({"id": "X", "tool": "curl", "command": "curl x"}, "tool"),
        ({"id": "X", "command": "python -m pytest"}, "pytest row"),
        ({"id": "X", "tool": "gate", "command": "eight"}, "gate row"),
        ({"id": "X", "tool": "browser", "command": "browser"}, "browser row"),
    ],
)
def test_bad_rows_are_schema_errors(tmp_path: Path, row: dict[str, str], problem: str) -> None:
    write_matrix(tmp_path, "R2", row)
    rows, errors = matrix.load("R2", tmp_path)
    assert rows == [] and problem in errors[0]


def test_missing_field_duplicate_id_and_wrong_release(tmp_path: Path) -> None:
    (tmp_path / "R2.yaml").write_text(
        "release: R3\nrows:\n  - id: A\n    layer: unit\n", encoding="utf-8"
    )
    write_matrix(
        tmp_path,
        "R1.1",
        {"id": "D", "command": "pytest a.py"},
        {"id": "D", "command": "pytest b.py"},
    )
    _, errors = matrix.load("R2", tmp_path)
    joined = " / ".join(errors)
    assert "release must be R2" in joined
    assert "missing feature, what, tool, command, pass" in joined
    assert "duplicate row id D" in joined


def test_invalid_yaml_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "R2.yaml").write_text("release: [R2\n", encoding="utf-8")
    assert "not valid YAML" in matrix.load("R2", tmp_path)[1][0]


def test_every_plan_feature_needs_a_row(tmp_path: Path, mdir: Path) -> None:
    (tmp_path / "R2.md").write_text(
        "| Feature | What |\n| --- | --- |\n| F-R2-01 | writes |\n| F-R2-02 | undo |\n",
        encoding="utf-8",
    )
    rows, _ = matrix.load("R2", mdir)
    assert matrix.plan_features("R2", tmp_path) == ["F-R2-01", "F-R2-02"]
    assert matrix.features_without_rows("R2", rows, tmp_path) == ["F-R2-02"]
    assert matrix.plan_features("R9", tmp_path) == []


def test_junit_results_and_pytest_rows(tmp_path: Path) -> None:
    junit = tmp_path / "j.xml"
    junit.write_text(
        '<testsuites><testsuite name="pytest">'
        '<testcase classname="tests.test_a" name="test_one"/>'
        '<testcase classname="tests.test_a" name="test_p[x]"/>'
        '<testcase classname="tests.test_a" name="test_p[y]"><failure/></testcase>'
        '<testcase classname="tests.test_b" name="test_skip"><skipped/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    tests = matrix.junit_results(junit)
    assert tests["tests/test_a.py::test_one"] is True
    assert tests["tests/test_b.py::test_skip"] is False

    def row(command: str, tool: str = "pytest") -> matrix.Row:
        return matrix.Row("R2", "X", "F", "unit", "w", tool, command, "p")

    cases = [
        row("pytest tests/test_a.py::test_one"),
        row("pytest tests/test_a.py::test_p"),
        row("pytest tests/test_a.py::test_none"),
        row("pytest tests/test_b.py::test_skip"),
        row("pytest tests/test_a.py"),
        row("step 8", "gate"),
        row("step 9", "gate"),
        row("browser inbox_card", "browser"),
        row("browser other", "browser"),
        row("saley discord roundtrip --test", "shell"),
    ]
    out = matrix.evaluate(
        cases,
        tests,
        {8: "PASS", 9: "FAIL"},
        {"inbox_card": True},
        run_shell=lambda r: (False, "exit 1"),
    )
    assert [o.ok for o in out] == [
        True,
        False,
        False,
        False,
        False,
        True,
        False,
        True,
        False,
        False,
    ]
    assert [o.note for o in out][2:4] == [
        "tests/test_a.py::test_none did not run",
        "tests/test_b.py::test_skip failed",
    ]
    assert out[8].note == "not checked"
    assert matrix.first_failure(out) == "X"
    assert matrix.first_failure(out[:1]) is None


def test_shell_rows_run_the_command(tmp_path: Path) -> None:
    ok_row = matrix.Row("R2", "S", "F", "bot", "w", "shell", "python -c \"print('hi')\"", "p")
    bad_row = matrix.Row("R2", "S", "F", "bot", "w", "shell", "no-such-program-xyz", "p")
    assert matrix._shell(ok_row) == (True, "exit 0 hi")
    assert matrix._shell(bad_row)[0] is False


def test_shell_rows_get_the_extra_env() -> None:
    cmd = "python -c \"import os; print(os.environ['SALEY_X'])\""
    row = matrix.Row("R4", "S", "F", "bot", "w", "shell", cmd, "p")
    assert matrix._shell(row, {**os.environ, "SALEY_X": "gate"}) == (True, "exit 0 gate")


def test_repo_matrices_are_valid() -> None:
    """Every matrix in tests/matrix loads with no schema error."""
    rows, errors = matrix.load("R99")
    assert errors == [] and rows


def test_shell_row_pending_needs_a_question_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    questions = tmp_path / "QUESTIONS.md"
    questions.write_text(
        "## Q-R2-1 · 2026-10-19 · Editor on the copy (open)\n"
        "## Q-R2-9 · 2026-10-19 · Old one (resolved)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(matrix, "QUESTIONS", questions)
    assert matrix.open_questions(questions) == {"Q-R2-1"}

    def row(code: str) -> matrix.Row:
        return matrix.Row("R2", "S", "F", "sheet", "w", "shell", f'python -c "{code}"', "p")

    pending = row("print('NOT CONFIGURED: Q-R2-1'); raise SystemExit(3)")
    assert matrix._shell(pending) == (True, "PENDING: not configured (Q-R2-1)")
    no_id = row("print('NOT CONFIGURED'); raise SystemExit(3)")
    assert matrix._shell(no_id)[0] is False
    resolved = row("print('NOT CONFIGURED: Q-R2-9'); raise SystemExit(3)")
    assert matrix._shell(resolved) == (False, "exit 3 names Q-R2-9, which is not an open question")
    out = matrix.evaluate([pending], {}, {}, {})
    assert (out[0].ok, out[0].pending) == (True, "Q-R2-1")


def test_python_runs_with_this_interpreter() -> None:
    import sys

    assert matrix.argv("python -m saley.cli x") == [sys.executable, "-m", "saley.cli", "x"]
    assert matrix.argv("git status") == ["git", "status"]


def test_the_release_needs_its_own_matrix_and_plan(tmp_path: Path, mdir: Path) -> None:
    plans = tmp_path / "plans"
    plans.mkdir()
    assert matrix.own_problems("R5", mdir, plans) == [
        "tests/matrix/R5.yaml is missing",
        "docs/plans/R5.md is missing",
    ]
    (plans / "R2.md").write_text("| **F-R2-01** | bold id |\n", encoding="utf-8")
    assert matrix.own_problems("R2", mdir, plans) == [
        "docs/plans/R2.md has no `| F-R2-NN |` feature rows"
    ]
    (plans / "R2.md").write_text("| F-R2-01 | ok |\n", encoding="utf-8")
    assert matrix.own_problems("R2", mdir, plans) == []


def test_top_level_list_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "R2.yaml").write_text("- id: A\n", encoding="utf-8")
    assert "needs `release:` and a `rows:` list" in matrix.load("R2", tmp_path)[1][0]


def test_junit_classes_and_duplicates(tmp_path: Path) -> None:
    junit = tmp_path / "j.xml"
    junit.write_text(
        "<testsuite>"
        '<testcase classname="tests.test_a.TestA" name="test_y"/>'
        '<testcase classname="tests.test_a" name="test_d"><failure/></testcase>'
        '<testcase classname="tests.test_a" name="test_d"/>'
        "</testsuite>",
        encoding="utf-8",
    )
    tests = matrix.junit_results(junit)
    assert tests["tests/test_a.py::TestA::test_y"] is True
    assert tests["tests/test_a.py::test_d"] is False


def write_raw(d: Path, release: str, row: str) -> None:
    (d / f"{release}.yaml").write_text(f"release: {release}\nrows:\n{row}", encoding="utf-8")


SHELL_ROW = """
  - id: W1
    feature: F-R5-01
    layer: sheet
    what: a live write
    tool: {tool}
    command: {command}
    pass: exit 0
{writes}"""


def test_writes_test_tab_is_allowed_on_a_test_tab_shell_row(tmp_path: Path) -> None:
    """D161: a shell row declares its test-tab write; rows without it carry writes=None."""
    write_raw(
        tmp_path,
        "R5",
        SHELL_ROW.format(
            tool="shell",
            command="python -m saley.cli sheet roundtrip --test-tab --case done-undo",
            writes="    writes: test_tab\n",
        ),
    )
    rows, errors = matrix.load("R5", tmp_path)
    assert errors == [] and rows[0].writes == "test_tab"
    write_raw(tmp_path, "R5", SHELL_ROW.format(tool="shell", command="git status", writes=""))
    assert matrix.load("R5", tmp_path)[0][0].writes is None


@pytest.mark.parametrize(
    ("tool", "command", "writes", "problem"),
    [
        ("shell", "python -m saley.cli sheet roundtrip --test-tab", "outreach", "not one of"),
        ("shell", "python -m saley.cli sheet roundtrip", "test_tab", "allowed only"),
        ("shell", "python -m saley.cli sheet roundtrip --test-tabs", "test_tab", "allowed only"),
        ("pytest", "pytest tests/test_a.py --test-tab", "test_tab", "allowed only"),
    ],
)
def test_writes_is_refused_outside_test_tab_shell_rows(
    tmp_path: Path, tool: str, command: str, writes: str, problem: str
) -> None:
    write_raw(
        tmp_path,
        "R5",
        SHELL_ROW.format(tool=tool, command=command, writes=f"    writes: {writes}\n"),
    )
    rows, errors = matrix.load("R5", tmp_path)
    assert rows == [] and problem in errors[0]


def test_repo_test_tab_shell_rows_declare_writes() -> None:
    """D161: every live test-tab shell row (R2, R3, R5) declares `writes: test_tab`."""
    rows, _ = matrix.load("R99")
    tab_rows = [r for r in rows if r.tool == "shell" and "--test-tab" in r.command.split()]
    assert tab_rows and all(r.writes == "test_tab" for r in tab_rows)


def test_a_failing_shell_row_names_its_telling_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5 gate: four live rows failed with only an egress log line in the note. The note now
    names the DIFF/Refused/error line, and the whole output is saved per row."""
    log = "2026-10-21T17:44:50Z [info     ] egress_call host=sheets"
    text = "\n".join([log, "undo restores the cell · old · new · DIFF", log])
    assert matrix.telling_line(text) == "undo restores the cell · old · new · DIFF"
    assert matrix.telling_line("\n".join(["plain end", log])) == "plain end"
    monkeypatch.setattr(matrix, "SHELL_LOGS", tmp_path)
    code = "import sys; print('step 1 2 DIFF'); print('" + log + "'); sys.exit(1)"
    row = matrix.Row("R5", "R5-W09", "F", "sheet", "w", "shell", f'python -c "{code}"', "p")
    ok, note = matrix._shell(row)
    assert ok is False and note == "exit 1 step 1 2 DIFF"
    assert "egress_call" in (tmp_path / "R5-W09.log").read_text(encoding="utf-8")
