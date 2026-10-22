"""Glue between the gate and the test matrix (`saley.qa.matrix`): run the rows, refresh late rows.

Why: matrix rows run after steps 1-9, and the late step rows are re-read at finalize (D236).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable

from saley.qa import gate, matrix
from verify_gate import ci
from verify_gate.base import (
    RECORDED_STEPS,
    Result,
)
from verify_gate.state import browser_path, junit_path, load_matrix, matrix_path

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
    ci_steps = ci.load(release)  # None unless this run had --ci (R6, D241)
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
        for o in matrix.evaluate(rows, tests, steps, named, run_shell=shell_runner(), ci=ci_steps)
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


def refresh_late_step_rows(release: str, results: dict[int, Result]) -> None:
    """Re-read the matrix rows that run `step 10` or `step 11` (R5-D01) from the final results.

    Why (D236): the matrix is evaluated after step 9, before the human records steps 10 and 11,
    so such a row always read "not run" and the gate could never pass. The finalize knows them.
    """
    saved = load_matrix(release)
    if saved is None:
        return
    rows, _errors = matrix.load(release)
    late = {}
    for row in rows:
        if row.tool == "gate" and int(row.command.split()[1]) >= min(RECORDED_STEPS):
            late[row.id] = int(row.command.split()[1])
    for entry in saved:
        n = late.get(entry["id"])
        if n is None:
            continue
        r = results.get(n)
        entry["ok"] = r is not None and r.status == "PASS"
        entry["note"] = f"step {n} {r.status if r else 'not run'}"
    matrix_path(release).write_text(json.dumps(saved, indent=1), encoding="utf-8")
