"""Report writing and finalize helpers: GATE line, docs check, Impeccable scores, manifest.

Why: the report is the gate's one output, so what goes into it (D236 names, env list) is in one file.
"""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime
from pathlib import Path
from types import ModuleType

from saley.qa import matrix
from verify_gate.base import (
    ENV_KINDS,
    IMPECCABLE_FROM,
    IMPECCABLE_SCRIPT,
    IST,
    ROOT,
    STEPS,
    Result,
)
from verify_gate.host import V
from verify_gate.state import load_matrix
from verify_gate.steps import release_key


def env_rows(release: str, status_file: Path | None = None) -> dict[str, list[str]]:
    """The release's rows in the STATUS.md env log, per kind (a kind with no row maps to [])."""
    status = (status_file or V.STATUS_FILE).read_text(encoding="utf-8")
    return {
        kind: re.findall(
            rf"^\| {re.escape(release)} \| {kind} \| (.*?) \|\r?$", status, re.MULTILINE
        )
        for kind in ENV_KINDS
    }


def env_changes(release: str, status_file: Path | None = None) -> list[str]:
    rows = env_rows(release, status_file)
    return [f"- {kind}: {'; '.join(rows[kind]) if rows[kind] else 'none'}" for kind in ENV_KINDS]


def impeccable_module() -> ModuleType:
    """scripts/impeccable_report.py, loaded by path: scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location("impeccable_report", IMPECCABLE_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {IMPECCABLE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def impeccable_needed(release: str) -> bool:
    return release_key(release) >= release_key(IMPECCABLE_FROM)


def impeccable_scores(release: str) -> tuple[str | None, list[str]]:
    """(the score table, gaps) from docs/test-reports/<release>-impeccable.json.

    Why: the same rules as `impeccable_report.py --check` (every page, both themes, every
    dimension, no open critical finding), so the gate cannot pass on a partial audit.
    """
    path = V.REPORTS / f"{release}-impeccable.json"
    if not path.is_file():
        return None, [f"{path.name} is missing"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None, [f"{path.name} is not JSON"]
    module = impeccable_module()
    gaps = module.problems(data)
    return (None if gaps else module.table(data)), gaps


def docs_problems(release: str) -> list[str]:
    """What the step-11 docs check proves by itself (D236): the STATUS.md env log has a NEW,
    a CHANGED-default and a RETIRED row for the release (the report summary ends with them),
    and, from R5 on, the Impeccable scores file is complete."""
    rows = env_rows(release)
    found = [f"docs/STATUS.md env log has no {release} {k} row" for k in ENV_KINDS if not rows[k]]
    if impeccable_needed(release):
        found += [f"Impeccable: {gap}" for gap in impeccable_scores(release)[1][:5]]
    return found


def with_docs_check(release: str, results: dict[int, Result]) -> dict[int, Result]:
    """A copy of the results in which a recorded step-11 PASS turns FAIL when docs_problems
    finds a gap. The saved state keeps the human's record, so a fix needs no new --record."""
    r11 = results.get(11)
    if r11 is None or r11.status != "PASS":
        return dict(results)
    found = docs_problems(release)
    if not found:
        return dict(results)
    note = f"docs check: {'; '.join(found)} (recorded: {r11.note})"
    return {**results, 11: Result(11, "FAIL", note, r11.seconds)}


def report_path(release: str, gate_text: str) -> Path:
    """D236: <release>-final.md on GATE: PASS, else <release>-<date>.md (one per day)."""
    if gate_text == "GATE: PASS":
        return V.REPORTS / f"{release}-final.md"
    return V.REPORTS / f"{release}-{datetime.now(IST):%Y-%m-%d}.md"


def impeccable_lines(release: str) -> list[str]:
    if not impeccable_needed(release):
        return []
    table, gaps = impeccable_scores(release)
    body = [table] if table else [f"FAIL: {'; '.join(gaps[:20])}"]
    return ["", "## Impeccable audit", "", *body]


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
    lines += impeccable_lines(release)
    lines += ["", "## Summary", "", gate, ""]
    if pending:
        lines += [f"PENDING live rows (open questions): {', '.join(pending)}", ""]
    # The summary ends with the env list (R5 brief, Gate; CLAUDE.md release rule).
    lines += ["Env changes:", *env_changes(release), ""]
    path = report_path(release, gate)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_manifest(release: str) -> None:
    files = sorted(
        str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "src").rglob("*.py")
    )
    header = (
        f"# Source files at release {release} (baseline for the gate's new-file coverage rule)."
    )
    (V.REPORTS / f"{release}-files.txt").write_text(
        "\n".join([header, *files, ""]), encoding="utf-8"
    )
