"""Gate steps 1-6: static checks, tests with coverage, STE lint, sheet robustness, API abuse, sheet verify.

Why: these steps only run commands and read their result; they hold no server. Names that the tests
replace (`run`, `REPORTS`, ...) are read through `V`, the verify module, so a patch still reaches them.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from saley.qa import build_stamp, matrix
from verify_gate.base import (
    COVERAGE_MIN,
    IST,
    PY_DIRS,
    RELEASE_RE,
    ROOT,
    SALEY,
    Result,
    tail,
)
from verify_gate.host import V
from verify_gate.state import junit_path


def step_static(_: argparse.Namespace) -> Result:
    # First: no stray file in the tree (/tidy --check). The list goes into the note.
    code, out = V.run([sys.executable, "scripts/tidy.py", "--check"])
    if code:
        return Result(1, "FAIL", f"tidy --check: {tail(out, 20)}")
    files = [str(p) for d in PY_DIRS for p in (ROOT / d).rglob("*.py")]
    code, out = V.run([sys.executable, "-m", "py_compile", *files])
    if code:
        return Result(1, "FAIL", f"py_compile: {tail(out)}")
    code, out = V.run([sys.executable, "-m", "ruff", "check", "."])
    if code:
        return Result(1, "FAIL", f"ruff check: {tail(out)}")
    code, out = V.run([sys.executable, "-m", "ruff", "format", "--check", "."])
    if code:
        return Result(1, "FAIL", f"ruff format: {tail(out)}")
    web_fail = web_checks()
    if web_fail:
        return Result(1, "FAIL", web_fail)
    return Result(
        1,
        "PASS",
        f"no stray file; {len(files)} files compile; ruff check and format clean; "
        "eslint and tsc clean; web unit tests pass; build stamp matches web/",
    )


def web_deps_installed() -> bool:
    """True when `npm --prefix web ci` has run (its own function, so tests can stub it)."""
    return (ROOT / "web" / "node_modules").is_dir()


def web_checks() -> str | None:
    """The dashboard v2 part of step 1 (R5, D144, D145): eslint, tsc, the web unit tests
    (`npm --prefix web test`, node --test; CI runs them too), then the build stamp.

    Why the stamp and not a build: a build here would race the human's `--watch` and costs
    RAM (D142). The stamp proves dist was built from these exact sources; when it differs the
    gate fails and says how to fix it. Returns the failure note, or None on a pass.
    """
    npm = shutil.which("npm")
    if npm is None:
        return "npm not found: install Node 24 (web/.nvmrc), then npm --prefix web ci"
    if not V.web_deps_installed():
        return "web/node_modules is missing: run npm --prefix web ci"
    for name, script in (("eslint", "lint"), ("tsc", "typecheck"), ("web tests", "test")):
        code, out = V.run([npm, "--prefix", "web", "run", script])
        if code:
            return f"{name}: {tail(out)}"
    stamp = build_stamp.check()
    return None if stamp.ok else f"build stamp: {stamp.note}"


def junit_counts(path: Path) -> tuple[int, int]:
    """(tests, not passed) from a pytest junit file. A skipped test did not pass: a gate
    step must not go green on tests that never ran."""
    root = ET.parse(path).getroot()  # noqa: S314 - our own file
    suites = [root] if root.tag == "testsuite" else list(root)
    total = sum(int(s.get("tests", 0)) for s in suites)
    bad = sum(
        int(s.get("failures", 0)) + int(s.get("errors", 0)) + int(s.get("skipped", 0))
        for s in suites
    )
    return total, bad


def release_key(release: str) -> tuple[int, ...]:
    """'R1.10' -> (1, 10): releases sort as numbers, not as text."""
    return tuple(int(n) for n in release[1:].split("."))


def baseline_files(release: str) -> set[str] | None:
    """Source files of the newest release before this one (its manifest), or None."""
    older = []
    for p in V.REPORTS.glob("R*-files.txt"):
        name = p.name.removesuffix("-files.txt")
        if RELEASE_RE.match(name) and release_key(name) < release_key(release):
            older.append((release_key(name), p))
    if not older:
        return None
    lines = max(older)[1].read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def low_new_files(coverage: dict, release: str) -> list[str]:
    base = baseline_files(release) or set()
    low = []
    for name, data in coverage["files"].items():
        rel = name.replace("\\", "/")
        pct = data["summary"]["percent_covered"]
        if rel.startswith("src/") and rel not in base and pct < COVERAGE_MIN:
            low.append(f"{rel} {pct:.0f} %")
    return low


def step_tests(args: argparse.Namespace) -> Result:
    with tempfile.TemporaryDirectory() as tmp:
        junit, cov = Path(tmp) / "junit.xml", Path(tmp) / "cov.json"
        code, out = V.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-x",  # D142: stop at the first failure, free the RAM early
                "-p",
                "no:cacheprovider",
                "--cov=saley",
                f"--cov-report=json:{cov}",
                f"--junitxml={junit}",
            ],
            timeout=1800,
        )
        if not junit.exists():
            return Result(2, "FAIL", f"pytest did not run: {tail(out)}")
        total, bad = junit_counts(junit)
        coverage = json.loads(cov.read_text(encoding="utf-8")) if cov.exists() else None
        V.GATE_TMP.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(junit, junit_path(args.release))
    rows, errors = matrix.load(args.release)
    errors = matrix.own_problems(args.release) + errors
    if errors:
        return Result(2, "FAIL", f"matrix: {' / '.join(errors[:5])}")
    missing = matrix.features_without_rows(args.release, rows)
    if missing:
        return Result(2, "FAIL", f"features without a matrix row: {', '.join(missing)}")
    if code or bad:
        return Result(2, "FAIL", f"{bad} of {total} tests failed or skipped: {tail(out)}")
    if coverage is None:
        return Result(2, "FAIL", "no coverage report")
    pct = coverage["totals"]["percent_covered"]
    low = low_new_files(coverage, args.release)
    if low:
        return Result(2, "FAIL", f"new files under {COVERAGE_MIN:.0f} %: {', '.join(low)}")
    return Result(
        2,
        "PASS",
        f"{total} passed, coverage {pct:.0f} %, every new file >= 80 %, "
        f"{len(rows)} matrix rows ({', '.join(matrix.releases_up_to(args.release)) or 'none'})",
    )


def step_ste(_: argparse.Namespace) -> Result:
    code, out = V.run([sys.executable, "-m", "saley.qa.ste_lint"])
    return Result(3, "FAIL" if code else "PASS", tail(out, 8))


def step_robust(_: argparse.Namespace) -> Result:
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "robust.xml"
        code, out = V.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "tests/test_sales.py",
                "-k",
                "robust",
                f"--junitxml={junit}",
            ]
        )
        if not junit.exists():
            return Result(4, "FAIL", f"pytest did not run: {tail(out)}")
        total, bad = junit_counts(junit)
    if code or bad or not total:
        return Result(4, "FAIL", f"{bad} of {total} failed: {tail(out)}")
    return Result(4, "PASS", f"{total} robustness tests passed")


def abuse_report(junit: Path, release: str) -> tuple[int, int]:
    """Write <release>-api-abuse.md: one row per test, from its first docstring line."""
    sys.path.insert(0, str(ROOT))
    root = ET.parse(junit).getroot()  # noqa: S314 - our own file
    import tests.test_api_abuse as suite

    rows, failed = [], 0
    for case in root.iter("testcase"):
        name = case.get("name", "")
        fn = getattr(suite, name.split("[")[0], None)
        doc = (fn.__doc__ or name).strip().splitlines()[0] if fn else name
        bad = any(case.find(tag) is not None for tag in ("failure", "error", "skipped"))
        failed += bad
        rows.append(f"| {doc} | `{name}` | {'FAIL' if bad else 'PASS'} |")
    lines = [
        f"# API abuse suite · {release} · {datetime.now(IST):%d %b %Y %H:%M} IST",
        "",
        "Source: tests/test_api_abuse.py (gate step 5). Every row must pass.",
        "",
        "| Check | Test | Result |",
        "| --- | --- | --- |",
        *rows,
        "",
        f"**{len(rows) - failed} of {len(rows)} passed.**",
        "",
    ]
    (V.REPORTS / f"{release}-api-abuse.md").write_text("\n".join(lines), encoding="utf-8")
    return len(rows), failed


def step_abuse(args: argparse.Namespace) -> Result:
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "abuse.xml"
        code, out = V.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "tests/test_api_abuse.py",
                f"--junitxml={junit}",
            ]
        )
        if not junit.exists():
            return Result(5, "FAIL", tail(out))
        total, failed = abuse_report(junit, args.release)
    status = "FAIL" if code or failed or not total else "PASS"
    return Result(5, status, f"{total - failed} of {total} passed; {args.release}-api-abuse.md")


def clear_qa_leftovers() -> tuple[bool, str]:
    """Remove `qa:` seed rows that a stopped run left behind (D112).

    browser_qa tears its seed down in `finally`, but a run that is killed (low memory) never
    gets there, and the next parity check counts the seeded people. The teardown touches only
    rows marked `qa:` (D99). Returns (ok, note for the step).
    """
    code, out = V.run([sys.executable, "scripts/qa_seed.py", "teardown"])
    if code:
        return False, f"QA seed teardown failed: {tail(out)}"
    line = next((x for x in out.splitlines() if x.startswith("removed:")), "")
    removed = sum(int(n) for n in re.findall(r"(\d+) ", line))
    return True, f"; removed {removed} leftover QA seed rows first" if removed else ""


def step_sheet_verify(args: argparse.Namespace) -> Result:
    cleared, note = clear_qa_leftovers()
    if not cleared:
        return Result(6, "FAIL", note)
    out_file = V.REPORTS / f"{args.release}-sheet-verify.md"
    # R4: the in-process API call signs in through the mock provider (dev only, D116).
    code, out = V.run(
        [*SALEY, "sheet", "verify", "--out", str(out_file)], env={"AUTH_PROVIDER": "mock"}
    )
    diffs = [line for line in out.splitlines() if line.endswith(" · DIFF")]
    if code:
        return Result(6, "FAIL", f"{len(diffs)} DIFF: {' / '.join(diffs[:5]) or tail(out)}{note}")
    rows = sum(1 for line in out.splitlines() if line.endswith(" · OK"))
    return Result(6, "PASS", f"{rows} checks OK, 0 DIFF; {out_file.name}{note}")
