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
import http.cookiejar
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from saley.qa import build_stamp, gate, matrix

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "docs" / "test-reports"
GATE_TMP = ROOT / ".scratch" / "gate"  # junit and browser results of the current run
IST = timezone(timedelta(hours=5, minutes=30))
COVERAGE_MIN = 80.0
DEV_START_SECONDS = 180
POLL_SECONDS = 2
ANOTHER_WORKER_WAIT_SECONDS = 20  # how long to watch a fresh `saley dev` for a worker refusal
SALEY = (sys.executable, "-m", "saley.cli")  # the venv python: no PATH lookup
RELEASE_RE = re.compile(r"^R\d+(\.\d+)*$")
# Step 9: the only requests that are not GET are these POSTs (the card edit D129 and the admin news run D150 included), each made by one press of a
# [data-act] control (scripts/browser_qa.py keeps the same list). Anything else fails the step.
ALLOWED_POST = re.compile(
    r"^/api/(act|undo/\d+|decision/\d+/confirm|proposal/\d+/(dismiss|edit)|commitment/\d+/done|admin/news/run)$"
)
# One more known POST is not a control (R3, D75): after a list is drawn, the page tells Saley which
# items it showed. It has no button, so it is counted apart (`seen_posts`) and has one fixed path.
SEEN_POST_PATH = "/api/seen"
# A second known POST that is not a control (R4 ticket 6, D131): the Ask card sends a typed
# question. It changes only user_seen and bot_exchange, is counted apart (`ask_posts`) and has one
# fixed path. In `non_get` it is a stray request, like a seen mark.
ASK_POST_PATH = "/api/ask"
# D112: below this much free RAM, browser QA runs one width per browser process.
LOW_RAM_MB = 3072
QA_WIDTHS = (1440, 1024, 390)  # the first width runs every group, the others the views only
CODE_DIRS = ("src", "tests", "scripts", "migrations", "web")
CODE_SUFFIXES = {
    *(".py", ".js", ".html", ".css", ".toml", ".ini", ".txt", ".yaml"),
    *(".ts", ".tsx", ".mjs", ".json"),  # the dashboard v2 (R5, D144)
}
# Never part of the fingerprint: installed or built trees, and key files (CLAUDE.md: never read).
FINGERPRINT_SKIP_DIRS = {"__pycache__", "node_modules", "dist"}
KEY_FILE_NAMES = ("*-write.json", "*service-account*", "*credentials*.json", "*-key.json")
PY_DIRS = ("src", "tests", "scripts", "migrations")
STEPS = {
    1: (
        "Static: tidy --check, py_compile, ruff, eslint, tsc, build stamp",
        "verification-loop",
    ),
    2: ("Tests, coverage, new files >= 80 %, a matrix row per feature", "pytest, tdd-guide"),
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


def run(cmd: list[str], timeout: int = 900, env: dict[str, str] | None = None) -> tuple[int, str]:
    """`env` adds to this process's environment (the gate's database and slot, D113)."""
    proc = subprocess.run(  # noqa: S603 - fixed commands from this file
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env={**os.environ, **env} if env else None,
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
    web_fail = web_checks()
    if web_fail:
        return Result(1, "FAIL", web_fail)
    return Result(
        1,
        "PASS",
        f"no stray file; {len(files)} files compile; ruff check and format clean; "
        "eslint and tsc clean; build stamp matches web/",
    )


def web_checks() -> str | None:
    """The dashboard v2 part of step 1 (R5, D144, D145): eslint, tsc, then the build stamp.

    Why the stamp and not a build: a build here would race the human's `--watch` and costs
    RAM (D142). The stamp proves dist was built from these exact sources; when it differs the
    gate fails and says how to fix it. Returns the failure note, or None on a pass.
    """
    npm = shutil.which("npm")
    if npm is None:
        return "npm not found: install Node 24 (web/.nvmrc), then npm --prefix web ci"
    if not (ROOT / "web" / "node_modules").is_dir():
        return "web/node_modules is missing: run npm --prefix web ci"
    for name, script in (("eslint", "lint"), ("tsc", "typecheck")):
        code, out = run([npm, "--prefix", "web", "run", script])
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
        GATE_TMP.mkdir(parents=True, exist_ok=True)
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


def clear_qa_leftovers() -> tuple[bool, str]:
    """Remove `qa:` seed rows that a stopped run left behind (D112).

    browser_qa tears its seed down in `finally`, but a run that is killed (low memory) never
    gets there, and the next parity check counts the seeded people. The teardown touches only
    rows marked `qa:` (D99). Returns (ok, note for the step).
    """
    code, out = run([sys.executable, "scripts/qa_seed.py", "teardown"])
    if code:
        return False, f"QA seed teardown failed: {tail(out)}"
    line = next((x for x in out.splitlines() if x.startswith("removed:")), "")
    removed = sum(int(n) for n in re.findall(r"(\d+) ", line))
    return True, f"; removed {removed} leftover QA seed rows first" if removed else ""


def step_sheet_verify(args: argparse.Namespace) -> Result:
    cleared, note = clear_qa_leftovers()
    if not cleared:
        return Result(6, "FAIL", note)
    out_file = REPORTS / f"{args.release}-sheet-verify.md"
    # R4: the in-process API call signs in through the mock provider (dev only, D116).
    code, out = run(
        [*SALEY, "sheet", "verify", "--out", str(out_file)], env={"AUTH_PROVIDER": "mock"}
    )
    diffs = [line for line in out.splitlines() if line.endswith(" · DIFF")]
    if code:
        return Result(6, "FAIL", f"{len(diffs)} DIFF: {' / '.join(diffs[:5]) or tail(out)}{note}")
    rows = sum(1 for line in out.splitlines() if line.endswith(" · OK"))
    return Result(6, "PASS", f"{rows} checks OK, 0 DIFF; {out_file.name}{note}")


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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None  # the mock sign-in answers 303; only its cookie matters


# One cookie jar for the gate's own server: every /api route needs a session (R4, D116).
OPENER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _NoRedirect
)


def sign_in(base: str) -> None:
    """Sign in through the mock provider (the gate web runs AUTH_PROVIDER=mock on loopback)."""
    request = urllib.request.Request(f"{base}/auth/mock", data=b"", method="POST")  # noqa: S310
    try:
        OPENER.open(request, timeout=10)
    except urllib.error.HTTPError as exc:
        if exc.code != 303:
            raise


def get_json(url: str) -> dict:
    with OPENER.open(url, timeout=10) as r:  # localhost only
        return json.load(r)


def sync_seconds() -> int:
    return setting("sheet_sync_seconds", 60)


def wait_fresh(
    base: str, started: datetime, limit: int, proc: subprocess.Popen | None = None
) -> tuple[bool, str]:
    """True when /api/status is `ok` (no stale tab) with a sync after `started`, within `limit` s."""
    deadline = time.monotonic() + DEV_START_SECONDS
    last = "no answer"
    signed_in = False  # D162: sign in at most once per call
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False, f"saley dev stopped (exit {proc.returncode}): {last}"
        try:
            try:
                status = get_json(f"{base}/api/status")
            except urllib.error.HTTPError as exc:
                if exc.code != 401 or signed_in:
                    raise  # a second 401 after the sign-in is a last error: poll on, no re-post
                sign_in(base)  # no session yet: sign in once, then ask again
                signed_in = True  # only after a good sign-in: a failed one may be tried again
                status = get_json(f"{base}/api/status")
            last = status.get("message", "")
            at = status.get("last_computed")
            if status.get("state") == "ok" and at and datetime.fromisoformat(at) >= started:
                age = (datetime.now(IST) - datetime.fromisoformat(at)).total_seconds()
                return age <= limit, f"{last} (age {age:.0f} s, limit {limit} s)"
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            # A half-started server can answer with a broken body (JSONDecodeError is a
            # ValueError): keep polling, as for a refused connection (M10).
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(POLL_SECONDS)
    return False, f"no fresh sync in {DEV_START_SECONDS} s: {last}"


def start_dev(port: int, log_path: Path, gate_env: dict[str, str]) -> subprocess.Popen:
    """The child keeps its own copy of the log handle; ours closes at once.

    D113: the gate's server runs on GATE_PORT against saley_test as worker slot `worker:gate`
    (`gate_env`, from `saley.qa.gate.plan`). It never pushes to the sheet: browser QA presses
    controls, and a write test belongs only in the SALEY_TEST scratch tab (docs/16 §3). An
    environment value wins over .env, so the worker child keeps every write `held`.
    """
    env = {**os.environ, **gate_env, "SHEET_WRITE_ENABLED": "false"}
    with log_path.open("w", encoding="utf-8") as log:
        return subprocess.Popen(  # noqa: S603 - fixed command
            [*SALEY, "dev", "--port", str(port)],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
        )


def step7_failed(note: str) -> list[Result]:
    return [
        Result(7, "FAIL", note),
        Result(8, "FAIL", "not run: step 7 failed"),
        Result(9, "FAIL", "not run: step 7 failed"),
    ]


def step_dev_and_browser(args: argparse.Namespace) -> list[Result]:
    """Steps 7-9 on the gate's own server (D113): GATE_PORT, saley_test, `worker:gate`.

    WEB_PORT, the dev database and the dev worker are never touched, so a human's own
    `saley dev` may keep running. The test-database lock is held until the server stops.
    """
    try:
        plan = gate.plan()
    except gate.GateConfigError as exc:
        return step7_failed(f"gate settings: {exc}")
    port = args.port or plan.port
    if port == setting("web_port", 8090):
        return step7_failed(f"the gate port {port} is WEB_PORT; set GATE_PORT")
    if not port_free(port):
        return step7_failed(
            f"port {port} is in use by {port_owner(port)}; the gate's server cannot start. "
            "Stop that program, or set GATE_PORT."
        )
    try:
        held = gate.hold_and_reset(plan)
    except Exception as exc:  # the note names the database, never the URL (it has a password)
        return step7_failed(f"gate database {plan.database}: {type(exc).__name__}")
    try:
        return serve_and_check(args, port, plan.env)
    finally:
        held.close()  # frees the test-database lock for the next pytest run


def serve_and_check(args: argparse.Namespace, port: int, env: dict[str, str]) -> list[Result]:
    base = f"http://localhost:{port}"
    log_path = REPORTS / f"{args.release}-dev.log"
    limit = sync_seconds()
    started = datetime.now(IST)
    proc = start_dev(port, log_path, env)
    try:
        if worker_refused(proc, log_path):
            time.sleep(2 * limit + POLL_SECONDS)  # the last worker ages out, then try once more
            started = datetime.now(IST)
            proc = start_dev(port, log_path, env)
        fresh, note = wait_fresh(base, started, limit, proc)
        if not fresh:
            return step7_failed(note)
        free = free_ram_mb()
        low = args.low_memory or (free is not None and free < LOW_RAM_MB)
        code, out = run_browser_qa(base, args.release, low, env)
        save_browser(args.release, out)
        found = browser_results(code, out)
        if low:
            note8 = f"; low memory ({free if free is not None else '?'} MB free): one width per run"
            found[0] = Result(8, found[0].status, found[0].note + note8)
        return [Result(7, "PASS", f"{note} · port {port}, {env.get('WORKER_SLOT')}"), *found]
    finally:
        stop_tree(proc)


def free_ram_mb() -> int | None:
    """Free physical memory in MB, or None when the platform does not say (D112)."""
    if os.name == "nt":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [  # the ctypes layout of MEMORYSTATUSEX
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.ullAvailPhys // (1024 * 1024))
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


# The R5 browser checks (tests/matrix/R5.yaml, tool browser) run on the new app. During the
# strangler (D147) it lives at /next, so the gate adds a run with `--app next`.
# T16 cut-over: set to "" so the R5 checks run on / (then no extra run is made here).
R5_APP_ENTRY = "next"
R5_FIRST = (5,)  # releases from R5 on run the R5 group too (release_key("R5") == (5,))


def runs_r5_group(release: str) -> bool:
    """True from R5 on while the R5 app has its own entry (D147). A bad id gives False."""
    try:
        key = release_key(release)
    except ValueError:
        return False
    return bool(R5_APP_ENTRY) and key >= R5_FIRST


def run_browser_qa(
    base: str, release: str, low: bool, env: dict[str, str] | None = None
) -> tuple[int, str]:
    """One browser run, or (low memory, D112) one browser process per width, merged.

    From R5 on, the same again with `--app <R5_APP_ENTRY>` for the R5 checks, merged into the
    one result. `env` points the QA seed and the seen-mark check at the gate's database (D113).
    """
    cmd = [sys.executable, "scripts/browser_qa.py", "--base", base, "--release", release]
    apps = [cmd]
    if runs_r5_group(release):
        apps.append([*cmd, "--app", R5_APP_ENTRY])
    parts = []
    for app in apps:
        if not low:
            parts.append(run(app, timeout=1800, env=env))
            continue
        for i, width in enumerate(QA_WIDTHS):
            extra = ["--widths", str(width)] + (["--views-only"] if i else [])
            parts.append(run([*app, *extra], timeout=1800, env=env))
    return parts[0] if len(parts) == 1 else merge_qa(parts)


def merge_notes(runs: list[dict]) -> dict[str, list[str]]:
    """Notes of every run (what each check verified), joined per check name."""
    notes: dict[str, list[str]] = {}
    for qa in runs:
        raw = qa.get("notes")
        if not isinstance(raw, dict):
            continue
        for name, items in raw.items():
            if isinstance(items, list):
                notes.setdefault(str(name), []).extend(str(x) for x in items)
    return notes


def merge_qa(parts: list[tuple[int, str]]) -> tuple[int, str]:
    """Merge the JSON lines of the per-width runs. A crashed part makes the whole a crash."""
    runs = []
    for code, out in parts:
        line = next((x for x in reversed(out.splitlines()) if x.startswith("{")), "")
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            return code or 1, out
    named: dict[str, bool] = {}
    for qa in runs:
        for name, ok in (qa.get("named") or {}).items():
            named[name] = named.get(name, True) and bool(ok)
    merged = {
        "verdict": "SHIP" if all(q.get("verdict") == "SHIP" for q in runs) else "BLOCK",
        "checks": sum(int(q.get("checks", 0)) for q in runs),
        "findings": [f for q in runs for f in q.get("findings", ["no findings list"])],
        "non_get": [x for q in runs for x in q.get("non_get", [])],
        "seen_posts": [x for q in runs for x in q.get("seen_posts", [])],
        "ask_posts": [x for q in runs for x in q.get("ask_posts", [])],
        "act_posts": sum(int(q.get("act_posts", 0)) for q in runs),
        "sign_ins": sum(int(q.get("sign_ins", 0)) for q in runs),  # D162, one run per width
        "named": named,
        "notes": merge_notes(runs),
        "screens": sum(int(q.get("screens", 0)) for q in runs),
        "dir": runs[0].get("dir", "-") if runs else "-",
    }
    code = max((c for c, _ in parts), default=1)
    return code, json.dumps(merged)


def notes_text(qa: dict) -> str:
    """'; notes: pagers: no pager yet · themes: ...' (short), or '' when there are none."""
    notes = merge_notes([qa])
    if not notes:
        return ""
    return "; notes: " + " · ".join(f"{k}: {', '.join(v)[:120]}" for k, v in notes.items())


def worker_refused(proc: subprocess.Popen, log_path: Path) -> bool:
    """True when `saley dev` stopped with "Another worker ..." within a short watch (M10).

    The refusal can take longer than one poll to appear (a slow start), so the log is read until
    the process exits or ANOTHER_WORKER_WAIT_SECONDS pass.
    """
    deadline = time.monotonic() + ANOTHER_WORKER_WAIT_SECONDS
    while True:
        exited = proc.poll() is not None
        seen = log_path.read_text(encoding="utf-8", errors="replace")
        if exited:
            return "Another worker" in seen
        if "Uvicorn running" in seen or time.monotonic() >= deadline:
            return False  # the web is up, so the worker check has passed
        time.sleep(POLL_SECONDS)


def stop_tree(proc: subprocess.Popen) -> None:
    """Stop `saley dev` and the worker it started as a child (D46).

    On Windows `terminate()` ends only the parent, so the child worker would keep running; the
    whole tree is ended with taskkill. Elsewhere SIGTERM reaches the parent, which stops its child.
    """
    if os.name == "nt" and proc.poll() is None:
        subprocess.run(  # noqa: S603 - fixed argv, our own pid
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],  # noqa: S607
            capture_output=True,
            check=False,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()


def save_browser(release: str, out: str) -> None:
    """Keep the browser-QA JSON: matrix rows with tool `browser` read its named checks."""
    line = next((x for x in reversed(out.splitlines()) if x.startswith("{")), "{}")
    GATE_TMP.mkdir(parents=True, exist_ok=True)
    browser_path(release).write_text(line, encoding="utf-8")


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
        f"screenshots in {qa.get('dir', '-')}"
        + (f"; {' / '.join(others[:5])}" if others else "")
        + notes_text(qa),
    )
    return [r8, click_path_result(qa, clicks, ship)]


def stray_requests(non_get: list[str]) -> list[str]:
    """Non-GET requests that are not exactly one of the allowed POSTs (method, path, no query)."""
    stray = []
    for entry in non_get:
        method, _, url = entry.partition(" ")
        parts = urlsplit(url)
        if method != "POST" or parts.query or not ALLOWED_POST.match(parts.path):
            stray.append(entry)
    return stray


def stray_seen(seen_posts: list[str]) -> list[str]:
    """Seen marks that are not exactly `POST /api/seen` (no query, no extra path)."""
    stray = []
    for entry in seen_posts:
        method, _, url = entry.partition(" ")
        parts = urlsplit(url)
        if method != "POST" or parts.query or parts.path != SEEN_POST_PATH:
            stray.append(entry)
    return stray


def stray_ask(ask_posts: list[str]) -> list[str]:
    """Question posts that are not exactly `POST /api/ask` (no query, no extra path)."""
    stray = []
    for entry in ask_posts:
        method, _, url = entry.partition(" ")
        parts = urlsplit(url)
        if method != "POST" or parts.query or parts.path != ASK_POST_PATH:
            stray.append(entry)
    return stray


def click_path_result(qa: dict, clicks: list[str], ship: bool) -> Result:
    """Step 9: allowed POSTs only, each from a [data-act] control; no form, no submit button.

    The seen marks of the lists (R3) are a second, separate source with one fixed path. They do
    not count as control presses, and a seen path inside `non_get` is a stray request.
    """
    non_get = qa.get("non_get")
    if not isinstance(non_get, list):
        return Result(9, "FAIL", "browser QA gave no list of non-GET requests")
    seen_posts = qa.get("seen_posts", [])
    if not isinstance(seen_posts, list):
        return Result(9, "FAIL", "browser QA gave no valid list of seen marks")
    ask_posts = qa.get("ask_posts", [])
    if not isinstance(ask_posts, list):
        return Result(9, "FAIL", "browser QA gave no valid list of Ask posts")
    stray = stray_requests(non_get)
    uncounted = qa.get("act_posts") != len(non_get)
    problems = [*clicks[:5]]
    bad_seen = stray_seen(seen_posts)
    if bad_seen:
        problems.append(f"seen marks that are not POST {SEEN_POST_PATH}: {bad_seen[:3]}")
    bad_ask = stray_ask(ask_posts)
    if bad_ask:
        problems.append(f"Ask posts that are not POST {ASK_POST_PATH}: {bad_ask[:3]}")
    if stray:
        problems.append(f"requests that are not an allowed POST: {stray[:3]}")
    if uncounted:
        problems.append(
            f"{len(non_get)} non-GET requests but {qa.get('act_posts')} control presses"
        )
    if ship and not problems:
        seen_note = f"; {len(seen_posts)} seen marks after list render" if seen_posts else ""
        seen_note += f"; {len(ask_posts)} Ask questions" if ask_posts else ""
        sign_ins = qa.get("sign_ins")
        seen_note += f"; {sign_ins} sign-in" if type(sign_ins) is int else ""  # not a JSON true
        return Result(
            9,
            "PASS",
            f"{len(non_get)} POSTs, all allowed, each from a [data-act] control{seen_note}; "
            "no form, no submit button",
        )
    return Result(9, "FAIL", " / ".join(problems) or "browser QA did not ship")


# ------------------------------------------------------------------ state and report


def state_path(release: str) -> Path:
    return REPORTS / f"{release}-gate.json"


def junit_path(release: str) -> Path:
    return GATE_TMP / f"{release}-junit.xml"


def browser_path(release: str) -> Path:
    return GATE_TMP / f"{release}-browser.json"


def matrix_path(release: str) -> Path:
    return REPORTS / f"{release}-matrix.json"


GATE_DB_KEYS = ("DATABASE_URL", "DATABASE_URL_ADMIN", "DATABASE_URL_RO")


def shell_runner() -> Callable[[matrix.Row], tuple[bool, str]]:
    """Shell rows read and write saley_test, never the dev database (D113). Only the
    database keys change: a roundtrip still uses the test channel and the sheet test tab."""
    try:
        env = {k: v for k, v in gate.plan().env.items() if k in GATE_DB_KEYS}
    except gate.GateConfigError as exc:
        note = f"not run: gate settings ({exc})"  # never fall back to the dev database
        return lambda row: (False, note)
    child = {**os.environ, **env}

    def run_row(row: matrix.Row) -> tuple[bool, str]:
        # R5 D161: a row writes to the sheet only if it declares `writes: test_tab`.
        per_row = child if row.writes == "test_tab" else {**child, "SHEET_WRITE_ENABLED": "false"}
        return matrix._shell(row, per_row)

    return run_row


def run_matrix(release: str, results: dict[int, Result]) -> list[dict]:
    """Every row of this release and the earlier ones, after steps 1-9 (docs/16 §3)."""
    rows, errors = matrix.load(release)
    tests = matrix.junit_results(junit_path(release)) if junit_path(release).exists() else {}
    named: dict[str, bool] = {}
    if browser_path(release).exists():
        try:
            named = json.loads(browser_path(release).read_text(encoding="utf-8")).get("named", {})
        except (json.JSONDecodeError, AttributeError):
            named = {}  # browser rows then read "not checked" and fail
    steps = {n: r.status for n, r in results.items()}
    out = [
        {
            "id": o.row.id,
            "release": o.row.release,
            "layer": o.row.layer,
            "what": o.row.what,
            "ok": o.ok,
            "note": o.note,
            "pending": o.pending,
        }
        for o in matrix.evaluate(rows, tests, steps, named, run_shell=shell_runner())
    ]
    out += [
        {
            "id": "schema",
            "release": release,
            "layer": "-",
            "what": e,
            "ok": False,
            "note": "matrix error",
        }
        for e in errors
    ]
    matrix_path(release).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def load_matrix(release: str) -> list[dict] | None:
    path = matrix_path(release)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def current_release(status_file: Path | None = None) -> str | None:
    """STATUS.md line 'Current release: R2' (the chain's default)."""
    text = (status_file or ROOT / "docs" / "STATUS.md").read_text(encoding="utf-8")
    found = re.search(r"^Current release: (R[\d.]+)", text, re.MULTILINE)
    return found.group(1) if found else None


def load(release: str) -> dict[int, Result]:
    path = state_path(release)
    if not path.exists():
        return {}
    return {int(k): Result(**v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def save(release: str, results: dict[int, Result]) -> None:
    data = {str(k): asdict(v) for k, v in sorted(results.items())}
    state_path(release).write_text(json.dumps(data, indent=2), encoding="utf-8")


def code_files(top: Path) -> list[Path]:
    """Code files under `top`, sorted. node_modules and dist are pruned, not walked: web/
    holds tens of thousands of installed files. Key files are never opened."""
    found: list[Path] = []
    for here, dirs, names in os.walk(top):
        dirs[:] = [d for d in dirs if d not in FINGERPRINT_SKIP_DIRS]
        for name in names:
            p = Path(here) / name
            if p.suffix in CODE_SUFFIXES and not any(p.match(k) for k in KEY_FILE_NAMES):
                found.append(p)
    return sorted(found)


def code_fingerprint() -> str:
    """Hash of every code file. Steps 1-9 count only for the code they ran on."""
    digest = hashlib.sha256()
    for d in CODE_DIRS:
        for p in code_files(ROOT / d):
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


def gate_line(results: dict[int, Result], rows: list[dict] | None = None) -> str:
    """PASS only when steps 1-11 passed and every matrix row passed (rows=None: not run)."""
    bad_row = next((r["id"] for r in rows if not r["ok"]), None) if rows is not None else "not run"
    row_note = f" · first failing matrix row {bad_row}" if bad_row else ""
    for n in sorted(STEPS):
        r = results.get(n)
        if r is None or r.status != "PASS":
            return f"GATE: FAIL — step {n}{row_note}"
    if bad_row:
        return f"GATE: FAIL — matrix{row_note}"
    return "GATE: PASS"


def write_report(release: str, results: dict[int, Result], final: bool) -> Path:
    rows = load_matrix(release)
    gate = gate_line(results, rows) if final else "GATE: not final (steps 10-12 open)"
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
    lines += ["", "## Test matrix", ""]
    if rows is None:
        lines.append("Not run.")
    else:
        passed = sum(r["ok"] for r in rows)
        lines += [
            f"{passed} of {len(rows)} rows passed "
            f"({', '.join(matrix.releases_up_to(release)) or 'no matrix'}).",
            "",
            "| Row | Release | Layer | What | Result | Note |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for r in rows:
            what, note = r["what"].replace("|", "/"), r["note"].replace("|", "/")
            result = ("PENDING" if r.get("pending") else "PASS") if r["ok"] else "FAIL"
            lines.append(
                f"| {r['id']} | {r['release']} | {r['layer']} | {what} | {result} | {note} |"
            )
    pending = [f"{r['id']} ({r['pending']})" for r in rows or [] if r.get("pending")]
    lines += ["", "## Summary", "", gate, ""]
    if pending:
        lines += [f"PENDING live rows (open questions): {', '.join(pending)}", ""]
    lines += ["Env changes:", *env_changes(release), ""]
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
    parser.add_argument("--release", help="Default: 'Current release' in docs/STATUS.md.")
    parser.add_argument("--port", type=int, default=None, help="Default: GATE_PORT (D113).")
    parser.add_argument("--record", nargs=3, metavar=("STEP", "STATUS", "NOTE"))
    parser.add_argument("--finalize", action="store_true")
    parser.add_argument(
        "--low-memory", action="store_true", help="browser QA one width per run (D112)"
    )
    args = parser.parse_args(argv)
    args.release = args.release or current_release()
    if not args.release or not RELEASE_RE.match(args.release):
        parser.error("--release looks like R1 or R1.1")
    for stream in (sys.stdout, sys.stderr):  # Windows consoles: print the "·" as is
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    REPORTS.mkdir(parents=True, exist_ok=True)
    results = load(args.release)
    bad: list[str] = []
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
            matrix_path(args.release).unlink(missing_ok=True)
        report = write_report(args.release, results, final=True)
        save(args.release, results)
        gate = gate_line(results, load_matrix(args.release))
        if gate == "GATE: PASS":
            write_manifest(args.release)
        print(f"{gate} · {report.name}")
        return 0 if gate == "GATE: PASS" else 1
    else:
        results = {}  # a new run starts from step 1: old results never count
        for old in (
            junit_path(args.release),
            browser_path(args.release),
            matrix_path(args.release),
        ):
            old.unlink(missing_ok=True)
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
        rows = run_matrix(args.release, results)
        bad = [r["id"] for r in rows if not r["ok"]]
        print(
            f"matrix: {len(rows) - len(bad)} of {len(rows)} rows passed"
            + (f"; failing: {', '.join(bad[:10])}" if bad else ""),
            flush=True,
        )
    save(args.release, results)
    report = write_report(args.release, results, final=False)
    print(f"report: docs/test-reports/{report.name}")
    failed = [r.step for r in results.values() if r.status != "PASS"]
    return 1 if failed or bad else 0


if __name__ == "__main__":
    sys.exit(main())
