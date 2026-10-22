"""Constants, the step result and the command runner of the release gate.

Why: every gate module needs these, and none of them needs the CLI (`scripts/verify.py`).
`ROOT` is the repo root.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "docs" / "test-reports"
STATUS_FILE = ROOT / "docs" / "STATUS.md"
ENV_KINDS = ("NEW", "CHANGED-default", "RETIRED")
# R5 brief, Gate: the Impeccable audit score goes into the test report. From R5 on the scores
# file must be complete (scripts/impeccable_report.py --check), or step 11 fails (D236).
IMPECCABLE_FROM = "R5"
IMPECCABLE_SCRIPT = ROOT / "scripts" / "impeccable_report.py"
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
# R5 T16a (R5-K01, R5-K02): when the R5 app ran its click path, step 9 also needs these presses
# (the `data-act` names of the new app) and one admin "Run news now" POST among the controls.
R5_STEP9_KINDS = frozenset({"done", "undo", "commitment-done", "run-news"})
# T16b review L2: since the cut-over the one run on `/` also holds the R2/R3 presses
# (browser_qa core.CONTROL_KINDS minus the R5 ones), and step 9 demands them itself.
R2_STEP9_KINDS = frozenset({"confirm", "primary", "dismiss", "add"})
R5_GROUP_MARKS = ("commitment_owner_toast", "run_news_now")  # named checks only the R5 app runs
NEWS_RUN_POST_PATH = "/api/admin/news/run"
# browser_qa.py `app`: "/" is the one app since the cut-over (D224); "next" was the strangler's
# R5 run (D147), kept so an older record still reads as an R5 run.
R5_APP_NAMES = frozenset({"/", "next"})
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
RECORDED_STEPS = (10, 11)  # reviewers and docs: recorded with --record, not run by this script
STEPS = {
    1: (
        "Static: tidy --check, py_compile, ruff, eslint, tsc, web tests, build stamp",
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
    11: (
        "Docs: README, .env.example, docs/08, STATUS env rows, Impeccable scores",
        "doc-updater",
    ),
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
