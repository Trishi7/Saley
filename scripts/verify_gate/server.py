"""Gate steps 7-9: the gate's own `saley dev`, the browser QA run and its JSON result.

Why: everything that starts, polls or stops the server sits in one file. Names that the tests
replace (`run`, `get_json`, `start_dev`, ...) are read through `V`, the verify module.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from saley.qa import gate
from verify_gate.base import (
    ANOTHER_WORKER_WAIT_SECONDS,
    DEV_START_SECONDS,
    IST,
    LOW_RAM_MB,
    POLL_SECONDS,
    QA_WIDTHS,
    ROOT,
    SALEY,
    Result,
    tail,
)
from verify_gate.clickpath import click_path_result
from verify_gate.host import V
from verify_gate.state import browser_path


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
        code, out = V.run(["netstat", "-ano"], timeout=30)  # IPv4 and IPv6 listeners
        pid = listening_pid(out, port) if code == 0 else None
        if pid is None:
            return "an unknown program"
        code, out = V.run(["tasklist", "/svc", "/fo", "csv", "/nh", "/fi", f"PID eq {pid}"], 30)
        name = task_name(out) if code == 0 else None
        return f"{name or 'an unknown program'}, PID {pid}"
    code, out = V.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpc"], timeout=30)
    fields = dict((ln[0], ln[1:]) for ln in out.splitlines() if ln[:1] in ("p", "c"))
    if code != 0 or "p" not in fields:
        return "an unknown program"
    return f"{fields.get('c', 'a program')}, PID {fields['p']}"


def setting(name: str, fallback: int) -> int:
    """One integer setting, read the way the app reads it (.env included)."""
    code, out = V.run(
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
    return V.setting("sheet_sync_seconds", 60)


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
                status = V.get_json(f"{base}/api/status")
            except urllib.error.HTTPError as exc:
                if exc.code != 401 or signed_in:
                    raise  # a second 401 after the sign-in is a last error: poll on, no re-post
                V.sign_in(base)  # no session yet: sign in once, then ask again
                signed_in = True  # only after a good sign-in: a failed one may be tried again
                status = V.get_json(f"{base}/api/status")
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
    if port == V.setting("web_port", 8090):
        return step7_failed(f"the gate port {port} is WEB_PORT; set GATE_PORT")
    if not V.port_free(port):
        return step7_failed(
            f"port {port} is in use by {V.port_owner(port)}; the gate's server cannot start. "
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
    log_path = V.REPORTS / f"{args.release}-dev.log"
    limit = sync_seconds()
    started = datetime.now(IST)
    proc = V.start_dev(port, log_path, env)
    try:
        if V.worker_refused(proc, log_path):
            time.sleep(2 * limit + POLL_SECONDS)  # the last worker ages out, then try once more
            started = datetime.now(IST)
            proc = V.start_dev(port, log_path, env)
        fresh, note = V.wait_fresh(base, started, limit, proc)
        if not fresh:
            return step7_failed(note)
        free = V.free_ram_mb()
        low = args.low_memory or (free is not None and free < LOW_RAM_MB)
        code, out = V.run_browser_qa(base, args.release, low, env)
        save_browser(args.release, out)
        found = browser_results(code, out)
        if low:
            note8 = f"; low memory ({free if free is not None else '?'} MB free): one width per run"
            found[0] = Result(8, found[0].status, found[0].note + note8)
        return [Result(7, "PASS", f"{note} · port {port}, {env.get('WORKER_SLOT')}"), *found]
    finally:
        V.stop_tree(proc)


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


def run_browser_qa(
    base: str, release: str, low: bool, env: dict[str, str] | None = None
) -> tuple[int, str]:
    """One browser run, or (low memory, D112) one browser process per width, merged.

    Since the cut-over (T16b, D224) one run on `/` covers the R1.1-R5 checks; there is no
    second `--app` run. `env` points the QA seed and the seen-mark check at the gate's database
    (D113).
    """
    cmd = [sys.executable, "scripts/browser_qa.py", "--base", base, "--release", release]
    if not low:
        return V.run(cmd, timeout=1800, env=env)
    parts = []
    for i, width in enumerate(QA_WIDTHS):
        extra = ["--widths", str(width)] + (["--views-only"] if i else [])
        parts.append(V.run([*cmd, *extra], timeout=1800, env=env))
    return merge_qa(parts)


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
        "act_kinds": sorted({str(k) for q in runs for k in q.get("act_kinds", [])}),
        "waived": {
            str(k): str(v)
            for q in runs
            if isinstance(q.get("waived"), dict)
            for k, v in q["waived"].items()
        },
        "apps": sorted({str(q["app"]) for q in runs if q.get("app")}),
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
    V.GATE_TMP.mkdir(parents=True, exist_ok=True)
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
