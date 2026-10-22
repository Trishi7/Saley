"""The release gate `/verify` (.claude/commands/verify.md): runs steps 1-9, records 10-11, finalizes.

Why: a release is done only when every step has a result and the report says GATE: PASS.
Each step runs even when an earlier one fails, so one run shows every problem.
State lives in docs/test-reports/<release>-gate.json. The report has the GATE line at the top.
Its name (D236): a finalize with GATE: PASS writes docs/test-reports/<release>-final.md; every
other run (steps 1-9, a failed finalize) writes docs/test-reports/<release>-<date>.md.

Run:
  uv run python scripts/verify.py --release R1.1                 # steps 1-9
  uv run python scripts/verify.py --release R1.1 --record 10 PASS "reviewers: ..."
  uv run python scripts/verify.py --release R1.1 --finalize      # step 12
"""

from __future__ import annotations

# ruff: noqa: F401
import argparse
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from saley.qa import build_stamp, gate, matrix

# scripts/ for the verify_gate package (the same pattern as qa_browser in browser_qa.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_gate import ci as ci_compose
from verify_gate import host
from verify_gate.base import (
    ALLOWED_POST,
    ANOTHER_WORKER_WAIT_SECONDS,
    ASK_POST_PATH,
    CODE_DIRS,
    CODE_SUFFIXES,
    COVERAGE_MIN,
    DEV_START_SECONDS,
    ENV_KINDS,
    FINGERPRINT_SKIP_DIRS,
    GATE_TMP,
    IMPECCABLE_FROM,
    IMPECCABLE_SCRIPT,
    IST,
    KEY_FILE_NAMES,
    LOW_RAM_MB,
    NEWS_RUN_POST_PATH,
    POLL_SECONDS,
    PY_DIRS,
    QA_WIDTHS,
    R2_STEP9_KINDS,
    R5_APP_NAMES,
    R5_GROUP_MARKS,
    R5_STEP9_KINDS,
    RECORDED_STEPS,
    RELEASE_RE,
    REPORTS,
    ROOT,
    SALEY,
    SEEN_POST_PATH,
    STATUS_FILE,
    STEPS,
    Result,
    run,
    tail,
)
from verify_gate.clickpath import (
    click_path_result,
    r5_click_problems,
    ran_r5_app,
    stray_ask,
    stray_requests,
    stray_seen,
    waived_presses,
)
from verify_gate.matrix_run import (
    GATE_DB_KEYS,
    refresh_late_step_rows,
    run_matrix,
    shell_runner,
)
from verify_gate.report import (
    docs_problems,
    env_changes,
    env_rows,
    gate_line,
    impeccable_lines,
    impeccable_module,
    impeccable_needed,
    impeccable_scores,
    report_path,
    with_docs_check,
    write_manifest,
    write_report,
)
from verify_gate.server import (
    OPENER,
    _NoRedirect,
    _port_owner,
    browser_results,
    free_ram_mb,
    get_json,
    listening_pid,
    merge_notes,
    merge_qa,
    notes_text,
    port_free,
    port_owner,
    run_browser_qa,
    save_browser,
    serve_and_check,
    setting,
    sign_in,
    start_dev,
    step7_failed,
    step_dev_and_browser,
    stop_tree,
    sync_seconds,
    task_name,
    wait_fresh,
    worker_refused,
)
from verify_gate.state import (
    browser_path,
    code_files,
    code_fingerprint,
    current_release,
    fingerprint_path,
    junit_path,
    load,
    load_matrix,
    matrix_path,
    save,
    state_path,
)
from verify_gate.steps import (
    abuse_report,
    baseline_files,
    clear_qa_leftovers,
    junit_counts,
    low_new_files,
    release_key,
    step_abuse,
    step_robust,
    step_sheet_verify,
    step_static,
    step_ste,
    step_tests,
    web_checks,
    web_deps_installed,
)

# The gate code in verify_gate reads the names that tests replace (run, REPORTS, ...) from this
# module at call time, so `monkeypatch.setattr(verify, ...)` still reaches it.
host.bind(sys.modules[__name__])


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
    parser.add_argument(
        "--ci", action="store_true", help="read the compose rows from GitHub Actions (R6, D241)"
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
        if step not in RECORDED_STEPS or status not in ("PASS", "FAIL"):
            parser.error("--record takes step 10 or 11 and PASS or FAIL")
        results[step] = Result(step, status, note)
    elif args.finalize:
        tree = fingerprint_path(args.release)
        if not tree.exists() or tree.read_text(encoding="utf-8") != code_fingerprint():
            # Code changed after steps 1-9 ran (or they never ran): their results do not count.
            for n in range(1, 10):
                results[n] = Result(n, "FAIL", "code changed after this step ran: run /verify")
            matrix_path(args.release).unlink(missing_ok=True)
        save(args.release, results)  # the step 10 and 11 records stay as recorded
        results = with_docs_check(args.release, results)
        refresh_late_step_rows(args.release, results)
        report = write_report(args.release, results, final=True)
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
        ci_compose.compose_path(args.release).unlink(missing_ok=True)
        if args.ci:
            _ok, note = ci_compose.collect(args.release)
            print(f"ci: {note}", flush=True)
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
