"""The release gate `/verify` (.claude/commands/verify.md): runs steps 1-9, records 10-11, finalizes.

Why: a release is done only when every step has a result and the report says GATE: PASS.
Each step runs even when an earlier one fails, so one run shows every problem.
State lives in docs/test-reports/<release>-gate.json; the report is
docs/test-reports/<release>-<date>.md with the GATE line at the top.

Run:
  uv run python scripts/verify.py --release R1.1                 # steps 1-9
  uv run python scripts/verify.py --release R1.1 --record 10 PASS "reviewers: ..."
  uv run python scripts/verify.py --release R1.1 --finalize      # step 12
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "docs" / "test-reports"
IST = timezone(timedelta(hours=5, minutes=30))
COVERAGE_MIN = 80.0
DEV_START_SECONDS = 180
POLL_SECONDS = 2
SALEY = (sys.executable, "-m", "saley.cli")  # the venv python: no PATH lookup
RELEASE_RE = re.compile(r"^R\d+(\.\d+)*$")
CODE_DIRS = ("src", "tests", "scripts", "migrations")
CODE_SUFFIXES = {".py", ".js", ".html", ".css", ".toml", ".ini", ".txt", ".yaml"}
PY_DIRS = ("src", "tests", "scripts", "migrations")
STEPS = {
    1: ("Static: tidy --check, py_compile, ruff check, ruff format --check", "verification-loop"),
    2: ("Tests on saley_test, coverage, new files >= 80 %", "pytest, tdd-guide"),
    3: ("STE lint: <= 60 characters, no banned words", "saley.qa.ste_lint"),
    4: ("Sheet robustness tests", "python-testing"),
    5: ("API contract and abuse suite", "security-review"),
    6: ("saley sheet verify", "dashboard-parity"),
    7: ("saley dev: first sync within SHEET_SYNC_SECONDS", "saley dev"),
    8: ("Browser QA, read-only", "browser-qa"),
    9: ("Click-path audit: no non-GET request", "click-path-audit"),
    10: ("Reviewers on the release diff", "code, python, database, security, silent-failure"),
    11: ("Docs: README, .env.example, docs/08 appendix, STATUS", "doc-updater"),
}


@dataclass
class Result:
    step: int
    status: str  # PASS | FAIL
    note: str
    seconds: float = 0.0


def run(cmd: list[str], timeout: int = 900) -> tuple[int, str]:
    proc = subprocess.run(  # noqa: S603 - fixed commands from this file
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def tail(text: str, lines: int = 6) -> str:
    return " / ".join(text.splitlines()[-lines:])


# ------------------------------------------------------------------ steps 1-6


def step_static(_: argparse.Namespace) -> Result:
    # First: no stray file in the tree (/tidy --check). The list goes into the note.
    code, out = run([sys.executable, "scripts/tidy.py", "--check"])
    if code:
        return Result(1, "FAIL", f"tidy --check: {tail(out, 20)}")
    files = [str(p) for d in PY_DIRS for p in (ROOT / d).rglob("*.py")]
    code, out = run([sys.executable, "-m", "py_compile", *files])
    if code:
        return Result(1, "FAIL", f"py_compile: {tail(out)}")
    code, out = run([sys.executable, "-m", "ruff", "check", "."])
    if code:
        return Result(1, "FAIL", f"ruff check: {tail(out)}")
    code, out = run([sys.executable, "-m", "ruff", "format", "--check", "."])
    if code:
        return Result(1, "FAIL", f"ruff format: {tail(out)}")
    return Result(
        1, "PASS", f"no stray file; {len(files)} files compile; ruff check and format clean"
    )


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
    for p in REPORTS.glob("R*-files.txt"):
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
        code, out = run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
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
    if code or bad:
        return Result(2, "FAIL", f"{bad} of {total} tests failed or skipped: {tail(out)}")
    if coverage is None:
        return Result(2, "FAIL", "no coverage report")
    pct = coverage["totals"]["percent_covered"]
    low = low_new_files(coverage, args.release)
    if low:
        return Result(2, "FAIL", f"new files under {COVERAGE_MIN:.0f} %: {', '.join(low)}")
    return Result(2, "PASS", f"{total} passed, coverage {pct:.0f} %, every new file >= 80 %")


def step_ste(_: argparse.Namespace) -> Result:
    code, out = run([sys.executable, "-m", "saley.qa.ste_lint"])
    return Result(3, "FAIL" if code else "PASS", tail(out, 8))


def step_robust(_: argparse.Namespace) -> Result:
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "robust.xml"
        code, out = run(
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
    (REPORTS / f"{release}-api-abuse.md").write_text("\n".join(lines), encoding="utf-8")
    return len(rows), failed


def step_abuse(args: argparse.Namespace) -> Result:
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "abuse.xml"
        code, out = run(
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


def step_sheet_verify(args: argparse.Namespace) -> Result:
    out_file = REPORTS / f"{args.release}-sheet-verify.md"
    code, out = run([*SALEY, "sheet", "verify", "--out", str(out_file)])
    diffs = [line for line in out.splitlines() if line.endswith(" · DIFF")]
    if code:
        return Result(6, "FAIL", f"{len(diffs)} DIFF: {' / '.join(diffs[:5]) or tail(out)}")
    rows = sum(1 for line in out.splitlines() if line.endswith(" · OK"))
    return Result(6, "PASS", f"{rows} checks OK, 0 DIFF; {out_file.name}")


# ------------------------------------------------------------------ steps 7-9


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def listening_pid(netstat: str, port: int) -> int | None:
    """PID of the LISTENING socket on `port` in `netstat -ano` output (Windows)."""
    for line in netstat.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
            return int(parts[4]) if parts[4].isdigit() else None
    return None


def task_name(tasklist: str) -> str | None:
    """'name.exe (service A, B)' from `tasklist /svc /fo csv /nh` output (Windows)."""
    for line in tasklist.splitlines():
        cells = [c.strip('"') for c in re.findall(r'"[^"]*"', line)]
        if len(cells) >= 3:
            services = "" if cells[2] in ("N/A", "") else f" (service {cells[2]})"
            return cells[0] + services
    return None


def port_owner(port: int) -> str:
    """Who holds the port, so the step 7 error names the program to stop (Q-R1.1-2)."""
    try:
        return _port_owner(port)
    except (OSError, subprocess.SubprocessError):  # no netstat / lsof, or it hung
        return "an unknown program"


def _port_owner(port: int) -> str:
    if os.name == "nt":
        code, out = run(["netstat", "-ano"], timeout=30)  # IPv4 and IPv6 listeners
        pid = listening_pid(out, port) if code == 0 else None
        if pid is None:
            return "an unknown program"
        code, out = run(["tasklist", "/svc", "/fo", "csv", "/nh", "/fi", f"PID eq {pid}"], 30)
        name = task_name(out) if code == 0 else None
        return f"{name or 'an unknown program'}, PID {pid}"
    code, out = run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpc"], timeout=30)
    fields = dict((ln[0], ln[1:]) for ln in out.splitlines() if ln[:1] in ("p", "c"))
    if code != 0 or "p" not in fields:
        return "an unknown program"
    return f"{fields.get('c', 'a program')}, PID {fields['p']}"


def setting(name: str, fallback: int) -> int:
    """One integer setting, read the way the app reads it (.env included)."""
    code, out = run(
        [
            sys.executable,
            "-c",
            f"from saley.core.settings import get_settings;print(get_settings().{name})",
        ]
    )
    return int(out.splitlines()[-1]) if code == 0 and out.strip() else fallback


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 - localhost only
        return json.load(r)


def sync_seconds() -> int:
    return setting("sheet_sync_seconds", 60)


def wait_fresh(
    base: str, started: datetime, limit: int, proc: subprocess.Popen | None = None
) -> tuple[bool, str]:
    """True when /api/status is `ok` (no stale tab) with a sync after `started`, within `limit` s."""
    deadline = time.monotonic() + DEV_START_SECONDS
    last = "no answer"
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False, f"saley dev stopped (exit {proc.returncode}): {last}"
        try:
            status = get_json(f"{base}/api/status")
        except OSError as exc:
            last = str(exc)
            time.sleep(POLL_SECONDS)
            continue
        last = status.get("message", "")
        at = status.get("last_computed")
        if status.get("state") == "ok" and at and datetime.fromisoformat(at) >= started:
            age = (datetime.now(IST) - datetime.fromisoformat(at)).total_seconds()
            return age <= limit, f"{last} (age {age:.0f} s, limit {limit} s)"
        time.sleep(POLL_SECONDS)
    return False, f"no fresh sync in {DEV_START_SECONDS} s: {last}"


def start_dev(port: int, log_path: Path) -> subprocess.Popen:
    """The child keeps its own copy of the log handle; ours closes at once."""
    with log_path.open("w", encoding="utf-8") as log:
        return subprocess.Popen(  # noqa: S603 - fixed command
            [*SALEY, "dev", "--port", str(port)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT
        )


def step_dev_and_browser(args: argparse.Namespace) -> list[Result]:
    base = f"http://localhost:{args.port}"
    if not port_free(args.port):
        note = (
            f"port {args.port} is in use by {port_owner(args.port)}; saley dev cannot start. "
            "Stop that program, or set WEB_PORT."
        )
        return [
            Result(7, "FAIL", note),
            Result(8, "FAIL", "not run: step 7 failed"),
            Result(9, "FAIL", "not run: step 7 failed"),
        ]
    log_path = REPORTS / f"{args.release}-dev.log"
    limit = sync_seconds()
    started = datetime.now(IST)
    proc = start_dev(args.port, log_path)
    try:
        time.sleep(POLL_SECONDS)
        if proc.poll() is not None and "Another worker" in log_path.read_text(
            encoding="utf-8", errors="replace"
        ):
            time.sleep(2 * limit + POLL_SECONDS)  # the last worker ages out, then try once more
            started = datetime.now(IST)
            proc = start_dev(args.port, log_path)
        fresh, note = wait_fresh(base, started, limit, proc)
        results = [Result(7, "PASS" if fresh else "FAIL", note)]
        if not fresh:
            return [
                *results,
                Result(8, "FAIL", "not run: step 7 failed"),
                Result(9, "FAIL", "not run: step 7 failed"),
            ]
        code, out = run(
            [sys.executable, "scripts/browser_qa.py", "--base", base, "--release", args.release],
            timeout=1800,
        )
        return [*results, *browser_results(code, out)]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


def browser_results(code: int, out: str) -> list[Result]:
    line = next((x for x in reversed(out.splitlines()) if x.startswith("{")), "")
    try:
        qa = json.loads(line)
    except json.JSONDecodeError:
        return [
            Result(8, "FAIL", f"browser QA crashed: {tail(out)}"),
            Result(9, "FAIL", "not run: browser QA crashed"),
        ]
    findings = qa.get("findings", ["no findings list"])
    clicks = [f for f in findings if f.startswith("click-path")]
    others = [f for f in findings if not f.startswith("click-path")]
    ship = qa.get("verdict") == "SHIP" and code == 0
    r8 = Result(
        8,
        "PASS" if ship else "FAIL",
        f"verdict {qa.get('verdict')}, {qa.get('checks', 0)} checks, {qa.get('screens', 0)} "
        f"screenshots in {qa.get('dir', '-')}" + (f"; {' / '.join(others[:5])}" if others else ""),
    )
    ok9 = ship and not clicks and not qa.get("non_get", ["unknown"])
    r9 = Result(
        9,
        "PASS" if ok9 else "FAIL",
        "no form, no submit button, 0 non-GET requests" if ok9 else " / ".join(clicks[:5]),
    )
    return [r8, r9]


# ------------------------------------------------------------------ state and report


def state_path(release: str) -> Path:
    return REPORTS / f"{release}-gate.json"


def load(release: str) -> dict[int, Result]:
    path = state_path(release)
    if not path.exists():
        return {}
    return {int(k): Result(**v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def save(release: str, results: dict[int, Result]) -> None:
    data = {str(k): asdict(v) for k, v in sorted(results.items())}
    state_path(release).write_text(json.dumps(data, indent=2), encoding="utf-8")


def code_fingerprint() -> str:
    """Hash of every code file. Steps 1-9 count only for the code they ran on."""
    digest = hashlib.sha256()
    for d in CODE_DIRS:
        for p in sorted((ROOT / d).rglob("*")):
            if p.is_file() and p.suffix in CODE_SUFFIXES and "__pycache__" not in p.parts:
                digest.update(str(p.relative_to(ROOT)).replace("\\", "/").encode())
                digest.update(p.read_bytes())
    for name in ("pyproject.toml", "uv.lock", "alembic.ini"):
        digest.update((ROOT / name).read_bytes())
    return digest.hexdigest()


def fingerprint_path(release: str) -> Path:
    return REPORTS / f"{release}-gate.tree"


def env_changes(release: str, status_file: Path | None = None) -> list[str]:
    status = (status_file or ROOT / "docs" / "STATUS.md").read_text(encoding="utf-8")
    out = []
    for kind in ("NEW", "CHANGED-default", "RETIRED"):
        rows = re.findall(rf"^\| {re.escape(release)} \| {kind} \| (.*?) \|$", status, re.MULTILINE)
        out.append(f"- {kind}: {'; '.join(rows) if rows else 'none'}")
    return out


def gate_line(results: dict[int, Result]) -> str:
    for n in sorted(STEPS):
        r = results.get(n)
        if r is None or r.status != "PASS":
            return f"GATE: FAIL — step {n}"
    return "GATE: PASS"


def write_report(release: str, results: dict[int, Result], final: bool) -> Path:
    gate = gate_line(results) if final else "GATE: not final (steps 10-12 open)"
    lines = [
        gate,
        "",
        f"# /verify {release} · {datetime.now(IST):%d %b %Y %H:%M} IST",
        "",
        "| Step | Check | Skill or agent | Result | Note |",
        "| --- | --- | --- | --- | --- |",
    ]
    for n, (name, tool) in STEPS.items():
        r = results.get(n)
        result, note = (r.status, r.note.replace("|", "/")) if r else ("OPEN", "")
        lines.append(f"| {n} | {name} | {tool} | {result} | {note} |")
    lines.append(f"| 12 | Finalize | scripts/verify.py | {'DONE' if final else 'OPEN'} | {gate} |")
    lines += ["", "## Summary", "", gate, "", "Env changes:", *env_changes(release), ""]
    path = REPORTS / f"{release}-{datetime.now(IST):%Y-%m-%d}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_manifest(release: str) -> None:
    files = sorted(
        str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "src").rglob("*.py")
    )
    header = (
        f"# Source files at release {release} (baseline for the gate's new-file coverage rule)."
    )
    (REPORTS / f"{release}-files.txt").write_text("\n".join([header, *files, ""]), encoding="utf-8")


AUTOMATED: list[Callable[[argparse.Namespace], Result]] = [
    step_static,
    step_tests,
    step_ste,
    step_robust,
    step_abuse,
    step_sheet_verify,
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True)
    parser.add_argument("--port", type=int, default=None, help="Default: WEB_PORT.")
    parser.add_argument("--record", nargs=3, metavar=("STEP", "STATUS", "NOTE"))
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args(argv)
    if not RELEASE_RE.match(args.release):
        parser.error("--release looks like R1 or R1.1")
    if args.port is None:
        args.port = setting("web_port", 8090)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles: print the "·" as is
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    REPORTS.mkdir(parents=True, exist_ok=True)
    results = load(args.release)
    if args.record:
        step, status, note = int(args.record[0]), args.record[1].upper(), args.record[2]
        if step not in (10, 11) or status not in ("PASS", "FAIL"):
            parser.error("--record takes step 10 or 11 and PASS or FAIL")
        results[step] = Result(step, status, note)
    elif args.finalize:
        tree = fingerprint_path(args.release)
        if not tree.exists() or tree.read_text(encoding="utf-8") != code_fingerprint():
            # Code changed after steps 1-9 ran (or they never ran): their results do not count.
            for n in range(1, 10):
                results[n] = Result(n, "FAIL", "code changed after this step ran: run /verify")
        report = write_report(args.release, results, final=True)
        save(args.release, results)
        gate = gate_line(results)
        if gate == "GATE: PASS":
            write_manifest(args.release)
        print(f"{gate} · {report.name}")
        return 0 if gate == "GATE: PASS" else 1
    else:
        results = {}  # a new run starts from step 1: old results never count
        fingerprint_path(args.release).write_text(code_fingerprint(), encoding="utf-8")
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        for fn in AUTOMATED:
            t0 = time.monotonic()
            r = fn(args)
            r.seconds = round(time.monotonic() - t0, 1)
            results[r.step] = r
            print(f"step {r.step}: {r.status} · {r.note}", flush=True)
        for r in step_dev_and_browser(args):
            results[r.step] = r
            print(f"step {r.step}: {r.status} · {r.note}", flush=True)
    save(args.release, results)
    report = write_report(args.release, results, final=False)
    print(f"report: docs/test-reports/{report.name}")
    failed = [r.step for r in results.values() if r.status != "PASS"]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
