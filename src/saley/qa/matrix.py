"""Test matrices (docs/16-verification-standard.md §3): tests/matrix/<release>.yaml.

Why: a release is done only when every feature it promised has a row, and every row of this
release and of every earlier one passes. `--release R3` runs R1.1 + R2 + R3. A row says what
is proved, by which tool, and how; the gate never trusts a row it cannot resolve.

Row schema: id, feature, layer, what, tool, command, pass; optional `writes`.
`writes: test_tab` (D161) declares that a shell row writes to the scratch tab SALEY_TEST. It is
allowed only on a shell row whose command has `--test-tab`. The gate runs every other shell
row with sheet writes off (R5 T2), so no row reaches Outreach PoCs by a missing flag.
Tools:
- pytest  `pytest <node id> [<node id> ...]`: every node id ran in gate step 2 and passed.
          A parametrised test counts when every one of its cases passed.
- gate    `step <n>`: that gate step passed.
- browser `browser <check name>`: the named browser-QA check passed (gate step 8).
- ci      `ci <row id>`: the step of that name in the CI compose run for HEAD concluded
          `success` (R6, D241; `verify.py --ci` reads it). No result = a failure.
- shell   `<argv>`: the command exits 0. Used only for live checks (sheet copy, test channel).
          Exit 3 with "NOT CONFIGURED: Q-<id>" = PENDING on that open question (shown in the
          report and the ping); exit 3 without a question id is a failure.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
MATRIX_DIR = ROOT / "tests" / "matrix"
PLANS_DIR = ROOT / "docs" / "plans"
QUESTIONS = ROOT / "docs" / "QUESTIONS.md"
LAYERS = ("unit", "sheet", "api", "browser", "bot", "guardrails")
TOOLS = ("pytest", "gate", "browser", "ci", "shell")
FIELDS = ("id", "feature", "layer", "what", "tool", "command", "pass")
# D161: the only allowed `writes` value, and the flag its command must carry.
WRITES = ("test_tab",)
TEST_TAB_FLAG = "--test-tab"
RELEASE_RE = re.compile(r"^R\d+(\.\d+)*$")
FEATURE_RE = re.compile(r"^\|\s*(F-R[\d.]+-\d+)\s*\|", re.MULTILINE)
SHELL_TIMEOUT = 900
# A live shell check whose human-only need is missing exits 3 and names its question.
PENDING_EXIT = 3
# A failing shell row names its telling line, never a log line (R5 gate: four live rows failed
# with only "egress_call ..." in the note). The whole output goes to SHELL_LOGS/<row id>.log.
TELLING = re.compile(r"\bDIFF\b|Refused|NOT CONFIGURED|Error|error:|FAIL|Traceback")
LOG_LINE = re.compile(r"^\d{4}-\d{2}-\d{2}T\S+ \[")
SHELL_LOGS = ROOT / ".scratch" / "gate" / "shell"
PENDING_RE = re.compile(r"NOT CONFIGURED: (Q-[\w.-]+)")


@dataclass(frozen=True)
class Row:
    release: str
    id: str
    feature: str
    layer: str
    what: str
    tool: str
    command: str
    pass_rule: str
    writes: str | None = None  # D161: "test_tab" or None


@dataclass(frozen=True)
class Outcome:
    row: Row
    ok: bool
    note: str
    pending: str | None = None  # the open question id of a PENDING live row


def release_key(release: str) -> tuple[int, ...]:
    return tuple(int(n) for n in release[1:].split("."))


def releases_up_to(release: str, matrix_dir: Path = MATRIX_DIR) -> list[str]:
    """Matrix releases from the first one up to `release`, in order."""
    found = [p.stem for p in matrix_dir.glob("R*.yaml") if RELEASE_RE.match(p.stem)]
    return sorted((r for r in found if release_key(r) <= release_key(release)), key=release_key)


def load(release: str, matrix_dir: Path = MATRIX_DIR) -> tuple[list[Row], list[str]]:
    """(rows, schema errors) for `release` and every earlier matrix."""
    rows: list[Row] = []
    errors: list[str] = []
    for rel in releases_up_to(release, matrix_dir):
        path = matrix_dir / f"{rel}.yaml"
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            errors.append(f"{path.name}: not valid YAML ({exc.__class__.__name__})")
            continue
        if not isinstance(data, dict) or not isinstance(data.get("rows") or [], list):
            errors.append(f"{path.name}: needs `release:` and a `rows:` list")
            continue
        if data.get("release") != rel:
            errors.append(f"{path.name}: release must be {rel}")
        for n, raw in enumerate(data.get("rows") or [], start=1):
            problem = row_problem(raw)
            if problem:
                errors.append(f"{path.name} row {n}: {problem}")
                continue
            rows.append(
                Row(
                    release=rel,
                    id=str(raw["id"]),
                    feature=str(raw["feature"]),
                    layer=str(raw["layer"]),
                    what=str(raw["what"]),
                    tool=str(raw["tool"]),
                    command=str(raw["command"]).strip(),
                    pass_rule=str(raw["pass"]),
                    writes=str(raw["writes"]) if "writes" in raw else None,
                )
            )
    seen: set[str] = set()
    for r in rows:
        if r.id in seen:
            errors.append(f"duplicate row id {r.id}")
        seen.add(r.id)
    return rows, errors


def row_problem(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return "not a mapping"
    missing = [f for f in FIELDS if not str(raw.get(f) or "").strip()]
    if missing:
        return f"missing {', '.join(missing)}"
    if raw["layer"] not in LAYERS:
        return f"layer {raw['layer']!r} is not one of {', '.join(LAYERS)}"
    if raw["tool"] not in TOOLS:
        return f"tool {raw['tool']!r} is not one of {', '.join(TOOLS)}"
    command = str(raw["command"]).split()
    if raw["tool"] == "pytest" and (command[0] != "pytest" or len(command) < 2):
        return "a pytest row runs `pytest <node id> ...`"
    if raw["tool"] == "gate" and not re.fullmatch(r"step \d+", str(raw["command"]).strip()):
        return "a gate row runs `step <n>`"
    if raw["tool"] == "browser" and (command[0] != "browser" or len(command) != 2):
        return "a browser row runs `browser <check name>`"
    if raw["tool"] == "ci" and command != ["ci", str(raw["id"])]:
        return "a ci row runs `ci <its own row id>`"
    return writes_problem(raw, command)


def writes_problem(raw: dict[str, Any], command: list[str]) -> str | None:
    """D161: `writes` is optional. Only `test_tab`, only on a shell row with `--test-tab`."""
    if "writes" not in raw:
        return None
    if raw["writes"] not in WRITES:
        return f"writes {raw['writes']!r} is not one of {', '.join(WRITES)}"
    if raw["tool"] != "shell" or TEST_TAB_FLAG not in command:
        return f"writes: test_tab is allowed only on a shell row with {TEST_TAB_FLAG}"
    return None


def own_problems(
    release: str, matrix_dir: Path = MATRIX_DIR, plans_dir: Path = PLANS_DIR
) -> list[str]:
    """The release itself needs its matrix and a plan with feature rows (review: a missing
    plan or matrix must never pass silently on the earlier matrices)."""
    out = []
    if not (matrix_dir / f"{release}.yaml").exists():
        out.append(f"tests/matrix/{release}.yaml is missing")
    if not (plans_dir / f"{release}.md").exists():
        out.append(f"docs/plans/{release}.md is missing")
    elif not plan_features(release, plans_dir):
        out.append(f"docs/plans/{release}.md has no `| F-{release}-NN |` feature rows")
    return out


def plan_features(release: str, plans_dir: Path = PLANS_DIR) -> list[str]:
    """Feature ids (`| F-R2-01 | ...` table rows) in docs/plans/<release>.md."""
    path = plans_dir / f"{release}.md"
    if not path.exists():
        return []
    return sorted(set(FEATURE_RE.findall(path.read_text(encoding="utf-8"))))


def features_without_rows(
    release: str, rows: Iterable[Row], plans_dir: Path = PLANS_DIR
) -> list[str]:
    covered = {r.feature for r in rows if r.release == release}
    return [f for f in plan_features(release, plans_dir) if f not in covered]


def junit_results(path: Path) -> dict[str, bool]:
    """Node id ('tests/test_web.py::test_x[a]') → passed, from a pytest junit file."""
    out: dict[str, bool] = {}
    root = ET.parse(path).getroot()  # noqa: S314 - our own file
    for case in root.iter("testcase"):
        parts = (case.get("classname") or "").split(".")
        # `tests.test_x.TestA` is a test class inside tests/test_x.py.
        classes = []
        while parts and parts[-1][:1].isupper():
            classes.insert(0, parts.pop())
        node = "::".join(["/".join(parts) + ".py", *classes, case.get("name", "")])
        bad = any(case.find(tag) is not None for tag in ("failure", "error", "skipped"))
        out[node] = out.get(node, True) and not bad  # a duplicate case never hides a failure
    return out


def _pytest(row: Row, tests: dict[str, bool]) -> tuple[bool, str]:
    for target in row.command.split()[1:]:
        # A node id, its parametrised cases, or a whole file (`tests/test_x.py`).
        cases = [
            ok
            for node, ok in tests.items()
            if node == target or node.startswith((target + "[", target + "::"))
        ]
        if not cases:
            return False, f"{target} did not run"
        if not all(cases):
            return False, f"{target} failed"
    return True, "passed"


def open_questions(questions: Path | None = None) -> set[str]:
    """Ids of the questions in docs/QUESTIONS.md that are not resolved."""
    questions = questions or QUESTIONS
    if not questions.exists():
        return set()
    heads = re.findall(r"^## (Q-[\w.-]+) .*$", questions.read_text(encoding="utf-8"), re.M)
    text = questions.read_text(encoding="utf-8")
    return {q for q in heads if not re.search(rf"^## {re.escape(q)} .*\(resolved\)", text, re.M)}


def argv(command: str) -> list[str]:
    """`python ...` runs with this interpreter (the venv), never a PATH lookup."""
    args = shlex.split(command, posix=True)
    return [sys.executable, *args[1:]] if args and args[0] == "python" else args


def _shell(row: Row, env: dict[str, str] | None = None) -> tuple[bool, str]:
    """`env`, when given, is the whole child environment (the caller merges it, D113)."""
    try:
        proc = subprocess.run(  # noqa: S603 - commands from our own matrix files
            argv(row.command),
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SHELL_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"did not run: {exc.__class__.__name__}"
    text = (proc.stdout + proc.stderr).strip()
    last = text.splitlines()[-1:] or [""]
    pending = PENDING_RE.search(text)
    if proc.returncode == PENDING_EXIT and pending:
        # A live check that needs a human-only value (docs/16 §3): shown as PENDING with the
        # question id, never as a plain pass. The question must be open in QUESTIONS.md.
        q = pending.group(1)
        if q not in open_questions():
            return False, f"exit 3 names {q}, which is not an open question"
        return True, f"PENDING: not configured ({q})"
    if proc.returncode == 0:
        return True, f"exit 0 {last[0][:120]}".strip()
    _save_log(row, text)
    return False, f"exit {proc.returncode} {telling_line(text)[:160]}".strip()


def telling_line(text: str) -> str:
    """The line that says why a shell row failed: the last DIFF/Refused/error line, else the
    last line that is not a log line, else the last line."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        if TELLING.search(line) and not LOG_LINE.match(line):
            return line
    plain = [line for line in lines if not LOG_LINE.match(line)]
    return (plain or lines or [""])[-1]


def _save_log(row: Row, text: str) -> None:
    try:
        SHELL_LOGS.mkdir(parents=True, exist_ok=True)
        (SHELL_LOGS / f"{row.id}.log").write_text(text, encoding="utf-8")
    except OSError:
        pass  # the note still names the telling line


def evaluate(
    rows: Iterable[Row],
    tests: dict[str, bool],
    steps: dict[int, str],
    browser: dict[str, bool],
    run_shell: Callable[[Row], tuple[bool, str]] = _shell,
    ci: dict[str, str] | None = None,
) -> list[Outcome]:
    """`ci`: row id -> step conclusion of the CI compose run (None: the gate ran without --ci)."""
    out = []
    for row in rows:
        if row.tool == "pytest":
            ok, note = _pytest(row, tests)
        elif row.tool == "gate":
            n = int(row.command.split()[1])
            ok, note = steps.get(n) == "PASS", f"step {n} {steps.get(n, 'not run')}"
        elif row.tool == "browser":
            name = row.command.split()[1]
            ok = browser.get(name) is True
            note = "passed" if ok else ("failed" if name in browser else "not checked")
        elif row.tool == "ci":
            ok, note = _ci(row, ci)
        else:
            ok, note = run_shell(row)
        found = re.match(r"PENDING: not configured \((Q-[\w.-]+)\)", note)
        out.append(Outcome(row, ok, note, found.group(1) if ok and found else None))
    return out


def _ci(row: Row, ci: dict[str, str] | None) -> tuple[bool, str]:
    if ci is None:
        return False, "no CI result: run the gate with --ci after the push (D241)"
    conclusion = ci.get(row.id)
    if conclusion is None:
        return False, "the CI compose run has no step for this row"
    return conclusion == "success", f"CI step {conclusion}"


def first_failure(outcomes: Iterable[Outcome]) -> str | None:
    return next((o.row.id for o in outcomes if not o.ok), None)
