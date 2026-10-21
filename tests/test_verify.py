"""scripts/verify.py: the gate's bookkeeping (state, GATE line, coverage rule, report)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def verify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_script", ROOT / "scripts" / "verify.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_script"] = module
    spec.loader.exec_module(module)
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(module, "REPORTS", reports)
    return module


def all_pass(verify: ModuleType) -> dict:
    return {n: verify.Result(n, "PASS", "ok") for n in verify.STEPS}


def test_gate_passes_only_when_every_step_passed(verify: ModuleType) -> None:
    results = all_pass(verify)
    rows = [{"id": "R2-A01", "ok": True}]
    assert verify.gate_line(results, rows) == "GATE: PASS"
    results[8] = verify.Result(8, "FAIL", "console error")
    assert verify.gate_line(results, rows) == "GATE: FAIL — step 8"
    del results[8]
    assert verify.gate_line(results, rows) == "GATE: FAIL — step 8"  # a missing step is a fail


def test_gate_names_the_first_failing_matrix_row(verify: ModuleType) -> None:
    results = all_pass(verify)
    rows = [
        {"id": "R2-A01", "ok": True},
        {"id": "R2-W03", "ok": False},
        {"id": "R2-W04", "ok": False},
    ]
    assert verify.gate_line(results, rows) == (
        "GATE: FAIL — matrix · first failing matrix row R2-W03"
    )
    results[2] = verify.Result(2, "FAIL", "x")
    assert verify.gate_line(results, rows) == (
        "GATE: FAIL — step 2 · first failing matrix row R2-W03"
    )
    assert verify.gate_line(all_pass(verify)) == (
        "GATE: FAIL — matrix · first failing matrix row not run"
    )


def test_default_release_is_current_in_status(verify: ModuleType, tmp_path: Path) -> None:
    status = tmp_path / "STATUS.md"
    status.write_text("# Status\n\nCurrent release: R2\n", encoding="utf-8")
    assert verify.current_release(status) == "R2"
    status.write_text("# Status\n", encoding="utf-8")
    assert verify.current_release(status) is None


def test_run_matrix_writes_rows(verify: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify, "GATE_TMP", verify.REPORTS / "gate")
    results = {8: verify.Result(8, "PASS", "ok")}
    rows = verify.run_matrix("R1.1", results)
    by_id = {r["id"]: r for r in rows}
    assert by_id["R1.1-B01"]["ok"] is True  # gate step 8 passed
    assert by_id["R1.1-B02"]["ok"] is False  # step 9 did not run
    assert by_id["R1.1-U01"]["note"].endswith("did not run")  # no junit in this run
    assert verify.load_matrix("R1.1") == rows


def test_record_and_finalize(verify: ModuleType) -> None:
    state = {
        str(n): {"step": n, "status": "PASS", "note": "ok", "seconds": 1.0} for n in range(1, 10)
    }
    verify.state_path("R9").write_text(json.dumps(state), encoding="utf-8")
    verify.fingerprint_path("R9").write_text(verify.code_fingerprint(), encoding="utf-8")
    verify.matrix_path("R9").write_text(
        json.dumps(
            [
                {
                    "id": "R9-A01",
                    "release": "R9",
                    "layer": "api",
                    "what": "a row",
                    "ok": True,
                    "note": "passed",
                }
            ]
        ),
        encoding="utf-8",
    )
    assert verify.main(["--release", "R9", "--finalize"]) == 1
    assert verify.main(["--release", "R9", "--record", "10", "PASS", "reviewers clean"]) == 0
    assert verify.main(["--release", "R9", "--record", "11", "pass", "docs updated"]) == 0
    assert verify.main(["--release", "R9", "--finalize"]) == 0
    report = next(verify.REPORTS.glob("R9-2*.md")).read_text(encoding="utf-8")
    assert report.splitlines()[0] == "GATE: PASS"
    assert "Env changes:" in report and "- NEW:" in report
    assert "| R9-A01 | R9 | api | a row | PASS | passed |" in report
    assert (verify.REPORTS / "R9-files.txt").exists()
    with pytest.raises(SystemExit):
        verify.main(["--release", "R9", "--record", "7", "PASS", "no"])
    with pytest.raises(SystemExit):
        verify.main(["--release", "../x", "--finalize"])


def test_finalize_refuses_results_from_older_code(verify: ModuleType) -> None:
    """Steps 1-9 count only for the code they ran on (python review)."""
    state = {
        str(n): {"step": n, "status": "PASS", "note": "ok", "seconds": 1.0} for n in range(1, 12)
    }
    verify.state_path("R8").write_text(json.dumps(state), encoding="utf-8")
    verify.fingerprint_path("R8").write_text("older code", encoding="utf-8")
    assert verify.main(["--release", "R8", "--finalize"]) == 1
    report = next(verify.REPORTS.glob("R8-2*.md")).read_text(encoding="utf-8")
    assert report.splitlines()[0].startswith("GATE: FAIL — step 1")
    assert "code changed after this step ran" in report


def test_skipped_tests_do_not_pass(verify: ModuleType, tmp_path: Path) -> None:
    junit = tmp_path / "s.xml"
    junit.write_text('<testsuite tests="4" failures="0" errors="0" skipped="1"/>', encoding="utf-8")
    assert verify.junit_counts(junit) == (4, 1)


def test_baseline_is_the_newest_older_release(verify: ModuleType) -> None:
    for name in ("R1", "R1.2", "R1.10", "R2"):
        (verify.REPORTS / f"{name}-files.txt").write_text(f"src/{name}.py\n", encoding="utf-8")
    assert verify.baseline_files("R1.11") == {"src/R1.10.py"}
    assert verify.baseline_files("R1.2") == {"src/R1.py"}
    assert verify.baseline_files("R3") == {"src/R2.py"}


def test_new_files_under_80_fail(verify: ModuleType) -> None:
    (verify.REPORTS / "R0-files.txt").write_text("# base\nsrc/saley/old.py\n", encoding="utf-8")
    coverage = {
        "files": {
            "src\\saley\\old.py": {"summary": {"percent_covered": 10.0}},
            "src/saley/new.py": {"summary": {"percent_covered": 79.0}},
            "src/saley/good.py": {"summary": {"percent_covered": 95.0}},
        }
    }
    assert verify.low_new_files(coverage, "R1") == ["src/saley/new.py 79 %"]
    assert verify.baseline_files("R0") is None  # its own manifest is not a baseline


def test_junit_counts(verify: ModuleType, tmp_path: Path) -> None:
    junit = tmp_path / "j.xml"
    junit.write_text(
        '<testsuites><testsuite tests="3" failures="1" errors="1"/></testsuites>', encoding="utf-8"
    )
    assert verify.junit_counts(junit) == (3, 2)


def test_browser_results_split_steps_8_and_9(verify: ModuleType) -> None:
    ship = json.dumps(
        {
            "verdict": "SHIP",
            "checks": 9,
            "findings": [],
            "non_get": [],
            "act_posts": 0,
            "screens": 3,
            "dir": "d",
        }
    )
    r8, r9 = verify.browser_results(0, "noise\n" + ship)
    assert (r8.status, r9.status) == ("PASS", "PASS")
    block = json.dumps(
        {
            "verdict": "BLOCK",
            "checks": 9,
            "findings": ["click-path: 1 forms"],
            "non_get": ["POST /x"],
            "screens": 3,
            "dir": "d",
        }
    )
    r8, r9 = verify.browser_results(1, block)
    assert (r8.status, r9.status) == ("FAIL", "FAIL")
    r8, r9 = verify.browser_results(1, "Traceback: boom")
    assert r8.status == "FAIL" and "crashed" in r8.note
    r8, r9 = verify.browser_results(1, json.dumps({"verdict": "BLOCK", "findings": ["x"]}))
    assert (r8.status, r9.status) == ("FAIL", "FAIL")


BASE = "http://localhost:8090"
ALLOWED = [
    f"POST {BASE}/api/act",
    f"POST {BASE}/api/undo/12",
    f"POST {BASE}/api/decision/3/confirm",
    f"POST {BASE}/api/proposal/4/dismiss",
    f"POST {BASE}/api/commitment/5/done",
]


def qa_json(**changes: object) -> str:
    base = {
        "verdict": "SHIP",
        "checks": 9,
        "findings": [],
        "non_get": ALLOWED,
        "act_posts": len(ALLOWED),
        "screens": 3,
        "dir": "d",
    }
    return json.dumps(base | changes)


def test_step_9_allows_exactly_the_five_posts_from_controls(verify: ModuleType) -> None:
    r8, r9 = verify.browser_results(0, qa_json())
    assert (r8.status, r9.status) == ("PASS", "PASS")
    assert "5 POSTs" in r9.note and "[data-act]" in r9.note


@pytest.mark.parametrize(
    "bad",
    [
        f"POST {BASE}/api/other",
        f"POST {BASE}/api/act/extra",
        f"POST {BASE}/api/undo/abc",
        f"POST {BASE}/api/undo/1?x=1",
        f"POST {BASE}/api/decision/1/delete",
        f"PUT {BASE}/api/act",
        f"DELETE {BASE}/api/undo/1",
        f"PATCH {BASE}/api/commitment/1/done",
        f"POST {BASE}/api/commitment/1/done/extra",
        "POST",
    ],
)
def test_step_9_fails_any_other_non_get(verify: ModuleType, bad: str) -> None:
    posts = [*ALLOWED, bad]
    r8, r9 = verify.browser_results(0, qa_json(non_get=posts, act_posts=len(posts)))
    assert r8.status == "PASS" and r9.status == "FAIL"
    assert "not an allowed POST" in r9.note


def test_step_9_allows_the_card_edit_post_as_a_control(verify: ModuleType) -> None:
    """The inline editor of an action card posts to /api/proposal/<id>/edit (D129)."""
    edit = f"POST {BASE}/api/proposal/4/edit"
    posts = [*ALLOWED, edit]
    assert verify.stray_requests(posts) == []
    _, r9 = verify.browser_results(0, qa_json(non_get=posts, act_posts=len(posts)))
    assert r9.status == "PASS"
    bad = [f"POST {BASE}/api/proposal/4/edit/x", f"POST {BASE}/api/proposal/x/edit"]
    assert verify.stray_requests(bad) == bad


SEEN = [f"POST {BASE}/api/seen", f"POST {BASE}/api/seen"]


def test_step_9_allows_the_seen_post_as_its_own_source(verify: ModuleType) -> None:
    """A list tells Saley what it showed (D75). That POST has no button: it is counted apart."""
    r8, r9 = verify.browser_results(0, qa_json(seen_posts=SEEN))
    assert (r8.status, r9.status) == ("PASS", "PASS")
    assert "5 POSTs" in r9.note and "2 seen marks" in r9.note


def test_step_9_still_needs_the_control_count_to_match_with_seen_marks(verify: ModuleType) -> None:
    _, r9 = verify.browser_results(0, qa_json(seen_posts=SEEN, act_posts=4))
    assert r9.status == "FAIL" and "control presses" in r9.note


@pytest.mark.parametrize(
    "bad",
    [
        f"POST {BASE}/api/seen?x=1",
        f"POST {BASE}/api/seen/1",
        f"POST {BASE}/api/act",
        f"PUT {BASE}/api/seen",
        f"GET {BASE}/api/seen",
        f"POST {BASE}/api/other",
        "POST",
    ],
)
def test_step_9_fails_a_seen_mark_that_is_anything_else(verify: ModuleType, bad: str) -> None:
    _, r9 = verify.browser_results(0, qa_json(seen_posts=[*SEEN, bad]))
    assert r9.status == "FAIL" and "seen" in r9.note


def test_step_9_does_not_let_a_seen_post_hide_in_the_control_list(verify: ModuleType) -> None:
    """The seen path is not a control path: in `non_get` it is a stray request."""
    posts = [*ALLOWED, f"POST {BASE}/api/seen"]
    _, r9 = verify.browser_results(0, qa_json(non_get=posts, act_posts=len(posts)))
    assert r9.status == "FAIL" and "not an allowed POST" in r9.note
    assert verify.stray_requests(ALLOWED) == []
    assert verify.stray_seen([f"POST {BASE}/api/seen"]) == []
    assert verify.stray_seen([f"POST {BASE}/api/undo/1"]) == [f"POST {BASE}/api/undo/1"]


def test_step_9_rejects_a_seen_list_that_is_not_a_list(verify: ModuleType) -> None:
    _, r9 = verify.browser_results(0, qa_json(seen_posts="many"))
    assert r9.status == "FAIL" and "seen" in r9.note


def test_step_9_fails_a_post_not_made_by_a_control(verify: ModuleType) -> None:
    """Five allowed POSTs but only four control presses: one came from somewhere else."""
    _, r9 = verify.browser_results(0, qa_json(act_posts=4))
    assert r9.status == "FAIL" and "control presses" in r9.note
    _, missing = verify.browser_results(0, qa_json(act_posts=None))
    assert missing.status == "FAIL"


def test_step_9_fails_on_forms_and_a_missing_list(verify: ModuleType) -> None:
    block = qa_json(verdict="BLOCK", findings=["click-path: today: 1 forms on the page"])
    r8, r9 = verify.browser_results(1, block)
    assert (r8.status, r9.status) == ("FAIL", "FAIL")
    assert "1 forms" in r9.note
    _, nolist = verify.browser_results(0, json.dumps({"verdict": "SHIP", "findings": []}))
    assert nolist.status == "FAIL" and "no list" in nolist.note
    assert verify.stray_requests(ALLOWED) == []
    assert verify.stray_requests([f"GET {BASE}/api/act"]) == [f"GET {BASE}/api/act"]


def test_stop_tree_ends_the_whole_tree_on_windows(
    verify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    class Proc:
        pid = 4242

        def __init__(self) -> None:
            self.events: list[str] = []

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            self.events.append("terminate")

        def wait(self, timeout: int) -> int:
            self.events.append("wait")
            return 0

        def kill(self) -> None:
            self.events.append("kill")

    monkeypatch.setattr(verify.subprocess, "run", lambda argv, **kw: calls.append(argv))
    monkeypatch.setattr(verify.os, "name", "nt")
    proc = Proc()
    verify.stop_tree(proc)
    assert calls == [["taskkill", "/PID", "4242", "/T", "/F"]] and proc.events == ["wait"]
    monkeypatch.setattr(verify.os, "name", "posix")
    verify.stop_tree(proc)
    assert proc.events == ["wait", "terminate", "wait"]


def test_gate_dev_server_never_pushes_to_the_sheet(
    verify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Browser QA presses controls: the gate's saley dev keeps every write held."""
    seen: dict = {}

    def fake_popen(cmd: list[str], **kwargs: object) -> str:
        seen.update(cmd=cmd, **kwargs)
        return "proc"

    monkeypatch.setenv("SHEET_WRITE_ENABLED", "true")
    monkeypatch.setattr(verify.subprocess, "Popen", fake_popen)
    gate_env = {"WORKER_SLOT": "worker:gate", "SHEET_WRITE_ENABLED": "true"}
    assert verify.start_dev(8091, verify.REPORTS / "x-dev.log", gate_env) == "proc"
    assert seen["env"]["SHEET_WRITE_ENABLED"] == "false"
    assert seen["env"]["WORKER_SLOT"] == "worker:gate"  # D113
    assert seen["cmd"][-3:] == ["dev", "--port", "8091"]


def test_busy_port_fails_step_7_clearly(
    verify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(verify, "port_free", lambda port: False)
    monkeypatch.setattr(verify, "port_owner", lambda port: "NIWebServer.exe, PID 4321")
    monkeypatch.setattr(verify, "setting", lambda name, fallback: 8090)
    monkeypatch.setattr(verify.gate, "plan", lambda: verify.gate.GatePlan(8091, "x_test", {}))
    args = type("A", (), {"port": None, "release": "R9"})()
    r7, r8, r9 = verify.step_dev_and_browser(args)
    assert r7.status == "FAIL" and "in use by NIWebServer.exe, PID 4321" in r7.note
    assert "port 8091" in r7.note and "GATE_PORT" in r7.note  # D113: never WEB_PORT
    assert r8.status == r9.status == "FAIL"


def test_port_owner_parsers(verify: ModuleType) -> None:
    netstat = (
        "  Proto  Local Address          Foreign Address        State           PID\n"
        "  TCP    0.0.0.0:18090          0.0.0.0:0              LISTENING       11\n"
        "  TCP    127.0.0.1:8090         127.0.0.1:5000         ESTABLISHED     12\n"
        "  TCP    0.0.0.0:8090           0.0.0.0:0              LISTENING       4321\n"
    )
    assert verify.listening_pid(netstat, 8090) == 4321
    assert verify.listening_pid(netstat, 9999) is None
    tasklist = '"NIWebServer.exe","4321","NIApplicationWebServer"\n'
    assert verify.task_name(tasklist) == "NIWebServer.exe (service NIApplicationWebServer)"
    assert verify.task_name('"python.exe","7","N/A"') == "python.exe"
    assert verify.task_name("INFO: No tasks are running.") is None


def test_port_owner_unknown_when_tools_fail(
    verify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(verify, "run", lambda cmd, timeout=900: (1, ""))
    assert verify.port_owner(8090) == "an unknown program"

    def missing(cmd: list[str], timeout: int = 900) -> tuple[int, str]:
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(verify, "run", missing)
    assert verify.port_owner(8090) == "an unknown program"


def test_d113_the_port_is_never_web_port(
    verify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(verify, "setting", lambda name, fallback: 8091)
    monkeypatch.setattr(verify.gate, "plan", lambda: verify.gate.GatePlan(8091, "x_test", {}))
    args = type("A", (), {"port": None, "release": "R9"})()
    r7, _r8, _r9 = verify.step_dev_and_browser(args)
    assert r7.status == "FAIL" and "is WEB_PORT; set GATE_PORT" in r7.note

    def bad_plan() -> None:
        raise verify.gate.GateConfigError("GATE_PORT (8090) must differ from WEB_PORT")

    monkeypatch.setattr(verify.gate, "plan", bad_plan)
    r7, _r8, _r9 = verify.step_dev_and_browser(args)
    assert r7.status == "FAIL" and r7.note.startswith("gate settings: GATE_PORT")


def test_env_changes_read_from_status(verify: ModuleType, tmp_path: Path) -> None:
    status = tmp_path / "STATUS.md"
    status.write_text(
        "| R7 | NEW | `A_KEY` |\n| R7 | RETIRED | `OLD_KEY` |\n| R6 | NEW | `B` |\n",
        encoding="utf-8",
    )
    lines = verify.env_changes("R7", status)
    assert lines == ["- NEW: `A_KEY`", "- CHANGED-default: none", "- RETIRED: `OLD_KEY`"]


# ------------------------------------------------------------------ M10 polling


def test_wait_fresh_keeps_polling_after_a_broken_answer(
    verify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-started server can send a body that is not JSON: poll again, do not crash."""
    from datetime import datetime

    started = datetime.now(verify.IST)
    good = {
        "state": "ok",
        "message": "Updated",
        "last_computed": datetime.now(verify.IST).isoformat(),
    }
    answers: list = [
        json.JSONDecodeError("Expecting value", "", 0),
        ValueError("bad"),
        TypeError("not a dict"),
        OSError("refused"),
        ["not", "a", "dict"],  # .get on a list: AttributeError
        {"state": "ok", "last_computed": "not a date"},
        good,
    ]

    def fake_get(url: str) -> dict:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(verify, "get_json", fake_get)
    monkeypatch.setattr(verify.time, "sleep", lambda s: None)
    ok, note = verify.wait_fresh("http://x", started, limit=60)
    assert ok is True and answers == []
    assert "age" in note


def test_wait_fresh_reports_the_last_error_when_time_runs_out(
    verify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime

    def broken(url: str) -> dict:
        raise json.JSONDecodeError("Expecting value", "", 0)

    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(verify, "get_json", broken)
    monkeypatch.setattr(verify.time, "sleep", lambda s: None)
    monkeypatch.setattr(verify.time, "monotonic", lambda: next(clock))
    ok, note = verify.wait_fresh("http://x", datetime.now(verify.IST), limit=60)
    assert ok is False and "JSONDecodeError" in note


class _Proc:
    def __init__(self, exits_after: int | None = None) -> None:
        self.polls = 0
        self.exits_after = exits_after

    def poll(self) -> int | None:
        self.polls += 1
        if self.exits_after is not None and self.polls > self.exits_after:
            return 1
        return None


def test_the_another_worker_refusal_is_found_even_when_it_comes_late(
    verify: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "dev.log"
    log.write_text("starting\nAnother worker ran in the last minutes.\n", encoding="utf-8")
    monkeypatch.setattr(verify.time, "sleep", lambda s: None)
    assert verify.worker_refused(_Proc(exits_after=5), log) is True  # not at the first look
    log.write_text("crashed for another reason\n", encoding="utf-8")
    assert verify.worker_refused(_Proc(exits_after=2), log) is False


def test_a_healthy_start_is_not_held_up(
    verify: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "dev.log"
    log.write_text("INFO: Uvicorn running on http://127.0.0.1:8000\n", encoding="utf-8")
    monkeypatch.setattr(verify.time, "sleep", lambda s: pytest.fail("no wait when the web is up"))
    assert verify.worker_refused(_Proc(), log) is False


def test_the_watch_ends_after_the_limit_when_nothing_happens(
    verify: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "dev.log"
    log.write_text("", encoding="utf-8")
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(verify.time, "sleep", lambda s: None)
    monkeypatch.setattr(verify.time, "monotonic", lambda: next(clock))
    assert verify.worker_refused(_Proc(), log) is False


# ------------------------------------------------------------------ D112 low memory


def qa_line(**over: object) -> str:
    base = {
        "verdict": "SHIP",
        "checks": 10,
        "findings": [],
        "non_get": [],
        "seen_posts": [],
        "act_posts": 0,
        "named": {"views": True},
        "screens": 20,
        "dir": "docs/test-reports/R9-screens",
    }
    return "noise\n" + json.dumps({**base, **over})


def test_d112_the_per_width_runs_merge_into_one_result(verify: ModuleType) -> None:
    full = qa_line(
        checks=100,
        non_get=[f"POST {BASE}/api/act"],
        act_posts=1,
        sign_ins=1,
        named={"views": True, "tiles": True},
    )
    code, out = verify.merge_qa([(0, full), (0, qa_line(sign_ins=1)), (0, qa_line())])
    merged = json.loads(out)
    assert merged["sign_ins"] == 2  # D162: the count survives the per-width merge
    assert code == 0 and merged["verdict"] == "SHIP"
    assert (merged["checks"], merged["screens"], merged["act_posts"]) == (120, 60, 1)
    assert merged["named"] == {"views": True, "tiles": True}
    r8, r9 = verify.browser_results(code, out)
    assert (r8.status, r9.status) == ("PASS", "PASS")
    # One width that fails fails the views check and the step.
    bad = qa_line(verdict="BLOCK", findings=["390: overflow"], named={"views": False})
    code, out = verify.merge_qa([(0, full), (1, bad), (0, qa_line())])
    merged = json.loads(out)
    assert code == 1 and merged["verdict"] == "BLOCK" and merged["named"]["views"] is False
    assert verify.browser_results(code, out)[0].status == "FAIL"
    # A crashed width is a crash of the whole step, never a pass.
    code, out = verify.merge_qa([(0, full), (1, "Traceback: MemoryError")])
    assert code == 1 and verify.browser_results(code, out)[0].note.startswith("browser QA crashed")


def test_d112_low_memory_runs_one_browser_process_per_width(
    verify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], timeout: int, env: dict | None = None) -> tuple[int, str]:
        calls.append(cmd)
        return 0, qa_line()

    monkeypatch.setattr(verify, "run", fake_run)
    verify.run_browser_qa(BASE, "R4", low=False)  # before R5: the legacy run only
    assert len(calls) == 1 and "--widths" not in calls[0]
    calls.clear()
    verify.run_browser_qa(BASE, "R4", low=True)
    tails = [c[c.index("--widths") :] for c in calls]
    assert tails == [
        ["--widths", "1440"],
        ["--widths", "1024", "--views-only"],
        ["--widths", "390", "--views-only"],
    ]


def test_r5_gate_adds_the_next_app_run_one_width_per_process(
    verify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """From R5 on the gate also runs `--app next` (D147, T12 review), merged into one result;
    under low memory each app runs one width per process. R1.1-R4 runs stay as they were."""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], timeout: int, env: dict | None = None) -> tuple[int, str]:
        calls.append(cmd)
        app = cmd[cmd.index("--app") + 1] if "--app" in cmd else "legacy"
        return 0, qa_line(named={f"views_{app}": True}, notes={"pagers": [app]})

    monkeypatch.setattr(verify, "run", fake_run)
    assert verify.R5_APP_ENTRY == "next"
    assert [verify.runs_r5_group(r) for r in ("R1.1", "R4", "R5", "R5.1", "R10", "bad")] == [
        False,
        False,
        True,
        True,
        True,
        False,
    ]
    code, out = verify.run_browser_qa(BASE, "R5", low=False)
    assert [c.count("--app") for c in calls] == [0, 1]
    assert calls[1][calls[1].index("--app") + 1] == "next"
    merged = json.loads(out)
    assert code == 0 and merged["named"] == {"views_legacy": True, "views_next": True}
    assert merged["notes"] == {"pagers": ["legacy", "next"]}
    calls.clear()
    verify.run_browser_qa(BASE, "R5", low=True)
    shape = [("--app" in c, c[c.index("--widths") :]) for c in calls]
    assert shape == [
        (False, ["--widths", "1440"]),
        (False, ["--widths", "1024", "--views-only"]),
        (False, ["--widths", "390", "--views-only"]),
        (True, ["--widths", "1440"]),
        (True, ["--widths", "1024", "--views-only"]),
        (True, ["--widths", "390", "--views-only"]),
    ]
    # T16 flips one switch: then no extra /next run is made.
    monkeypatch.setattr(verify, "R5_APP_ENTRY", "")
    calls.clear()
    verify.run_browser_qa(BASE, "R5", low=False)
    assert len(calls) == 1 and "--app" not in calls[0]


def test_merge_keeps_notes_and_the_step_note_shows_them(verify: ModuleType) -> None:
    a = qa_line(notes={"pagers": ["no pager yet on 15 pages"], "themes": ["ok"]})
    b = qa_line(notes={"pagers": ["today"]})
    code, out = verify.merge_qa([(0, a), (0, b), (0, qa_line(notes="junk"))])
    merged = json.loads(out)
    assert merged["notes"] == {"pagers": ["no pager yet on 15 pages", "today"], "themes": ["ok"]}
    r8, _ = verify.browser_results(code, out)
    assert "notes: pagers: no pager yet on 15 pages, today" in r8.note
    assert "themes: ok" in r8.note
    assert "notes:" not in verify.browser_results(0, qa_line())[0].note


def test_d112_free_ram_is_a_number_or_none(verify: ModuleType) -> None:
    free = verify.free_ram_mb()
    assert free is None or (isinstance(free, int) and free >= 0)


def test_d112_browser_qa_reads_only_the_docs_08_widths() -> None:
    spec = importlib.util.spec_from_file_location(
        "browser_qa_script", ROOT / "scripts" / "browser_qa.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.parse_widths("1440,1024,390") == (1440, 1024, 390)
    assert module.parse_widths(" 390 ") == (390,)
    for bad in ("", "800", "1440,abc", "-1"):
        assert module.parse_widths(bad) == ()


def test_d112_step_6_removes_leftover_qa_seed_rows_first(
    verify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    envs: list[dict[str, str] | None] = []
    answers = {
        "qa_seed.py": (0, "removed: 0 seen, 2 contacts, 1 accounts"),
        "verify": (0, "people_total · 724 · 724 · OK"),
    }

    def fake_run(
        cmd: list[str], timeout: int = 900, env: dict[str, str] | None = None
    ) -> tuple[int, str]:
        calls.append(cmd)
        envs.append(env)
        return answers["qa_seed.py"] if "scripts/qa_seed.py" in cmd else answers["verify"]

    monkeypatch.setattr(verify, "run", fake_run)
    args = type("A", (), {"release": "R9"})()
    result = verify.step_sheet_verify(args)
    assert "scripts/qa_seed.py" in calls[0] and "verify" in calls[1]  # teardown runs first
    assert envs[1] == {"AUTH_PROVIDER": "mock"}  # R4: the in-process call signs in as the dev user
    assert result.status == "PASS" and result.note.endswith("removed 3 leftover QA seed rows first")
    answers["qa_seed.py"] = (0, "removed: 0 seen, 0 contacts")
    assert "leftover" not in verify.step_sheet_verify(args).note
    answers["qa_seed.py"] = (1, "Refused: not the dev database")
    failed = verify.step_sheet_verify(args)
    assert failed.status == "FAIL" and "teardown failed" in failed.note


# ------------------------------------------------------------------ D113 the gate's own server


def test_d113_gate_runs_while_a_dev_server_is_up_on_web_port(
    verify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dev server listens on WEB_PORT the whole time; steps 7-9 pass on GATE_PORT."""
    import socket

    dev = socket.socket()
    dev.bind(("127.0.0.1", 0))
    dev.listen()
    web_port = dev.getsockname()[1]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        gate_port = probe.getsockname()[1]
    env = {"WORKER_SLOT": "worker:gate", "DATABASE_URL": "postgresql://u:p@h/saley_test"}
    seen: dict = {"started": [], "qa_env": None, "closed": False}

    class Held:
        def close(self) -> None:
            seen["closed"] = True

    def fake_start(port: int, log_path: Path, gate_env: dict) -> str:
        seen["started"].append((port, gate_env["WORKER_SLOT"]))
        return "proc"

    def fake_qa(base: str, release: str, low: bool, env: dict | None = None) -> tuple[int, str]:
        seen["qa_env"] = env
        seen["qa_base"] = base
        return 0, qa_line(non_get=[], act_posts=0)

    monkeypatch.setattr(verify, "setting", lambda name, fallback: web_port)
    monkeypatch.setattr(verify.gate, "plan", lambda: verify.gate.GatePlan(gate_port, "x_test", env))
    monkeypatch.setattr(verify.gate, "hold_and_reset", lambda plan: Held())
    monkeypatch.setattr(verify, "start_dev", fake_start)
    monkeypatch.setattr(verify, "worker_refused", lambda proc, log: False)
    monkeypatch.setattr(verify, "wait_fresh", lambda *a: (True, "Updated 10:00 IST"))
    monkeypatch.setattr(verify, "run_browser_qa", fake_qa)
    monkeypatch.setattr(verify, "stop_tree", lambda proc: None)
    monkeypatch.setattr(verify, "free_ram_mb", lambda: 8000)
    args = type("A", (), {"port": None, "release": "R9", "low_memory": False})()
    try:
        r7, r8, r9 = verify.step_dev_and_browser(args)
        assert not verify.port_free(web_port)  # the dev server was never stopped
    finally:
        dev.close()
    assert (r7.status, r8.status, r9.status) == ("PASS", "PASS", "PASS")
    assert seen["started"] == [(gate_port, "worker:gate")]
    assert seen["qa_base"] == f"http://localhost:{gate_port}" and seen["qa_env"] == env
    assert seen["closed"] is True  # the test-database lock is freed
    assert f"port {gate_port}, worker:gate" in r7.note


ASKS = [f"POST {BASE}/api/ask", f"POST {BASE}/api/ask"]


def test_step_9_allows_the_ask_post_as_its_own_source(verify: ModuleType) -> None:
    """The Ask card sends a typed question (R4, D131). That POST has no [data-act] control."""
    r8, r9 = verify.browser_results(0, qa_json(ask_posts=ASKS))
    assert (r8.status, r9.status) == ("PASS", "PASS")
    assert "5 POSTs" in r9.note and "2 Ask questions" in r9.note


@pytest.mark.parametrize(
    "bad",
    [
        f"POST {BASE}/api/ask?x=1",
        f"POST {BASE}/api/ask/1",
        f"POST {BASE}/api/seen",
        f"PUT {BASE}/api/ask",
        f"GET {BASE}/api/ask",
        "POST",
    ],
)
def test_step_9_fails_an_ask_post_that_is_anything_else(verify: ModuleType, bad: str) -> None:
    _, r9 = verify.browser_results(0, qa_json(ask_posts=[*ASKS, bad]))
    assert r9.status == "FAIL" and "Ask" in r9.note


def test_step_9_does_not_let_an_ask_post_hide_in_the_control_list(verify: ModuleType) -> None:
    posts = [*ALLOWED, f"POST {BASE}/api/ask"]
    _, r9 = verify.browser_results(0, qa_json(non_get=posts, act_posts=len(posts)))
    assert r9.status == "FAIL" and "not an allowed POST" in r9.note
    assert verify.stray_ask([f"POST {BASE}/api/ask"]) == []
    _, bad = verify.browser_results(0, qa_json(ask_posts="many"))
    assert bad.status == "FAIL" and "Ask" in bad.note


def test_shell_rows_use_the_gate_database_only(verify: ModuleType, monkeypatch) -> None:
    """R4-Q02 writes an ask log in a rolled-back transaction: it must hit saley_test (D113)."""
    env = {
        "DATABASE_URL": "t",
        "DATABASE_URL_ADMIN": "t",
        "DATABASE_URL_RO": "r",
        "SHEET_WRITE_ENABLED": "false",
        "AUTH_PROVIDER": "mock",
    }
    monkeypatch.setattr(verify.gate, "plan", lambda: SimpleNamespace(env=env))
    seen: list = []
    monkeypatch.setattr(verify.matrix, "_shell", lambda row, extra: seen.append(extra))
    verify.shell_runner()(SimpleNamespace(writes=None))
    (child,) = seen
    assert {k: child[k] for k in env if k in child} == {
        "DATABASE_URL": "t",
        "DATABASE_URL_ADMIN": "t",
        "DATABASE_URL_RO": "r",
        "SHEET_WRITE_ENABLED": "false",  # R5-X01: a row without `writes: test_tab`
    }
    assert "PATH" in child or "Path" in child  # the rest of the environment is kept


def test_shell_rows_fail_without_a_gate_plan(verify: ModuleType, monkeypatch) -> None:
    def bad() -> None:
        raise verify.gate.GateConfigError("GATE_PORT (8090) must differ from WEB_PORT")

    monkeypatch.setattr(verify.gate, "plan", bad)
    seen: list = []
    monkeypatch.setattr(verify.matrix, "_shell", lambda row, extra: seen.append(extra))
    ok, note = verify.shell_runner()(SimpleNamespace(writes=None))
    assert seen == [] and not ok and "gate settings" in note


def test_wait_fresh_signs_in_once_on_401(
    verify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4 (D116): /api/status needs a session; the gate signs in by the mock, then asks again."""
    import urllib.error
    from datetime import datetime

    started = datetime.now(verify.IST)  # before the sync time, as in a real run (no flake)
    good = {
        "state": "ok",
        "message": "Updated",
        "last_computed": datetime.now(verify.IST).isoformat(),
    }
    answers: list = [urllib.error.HTTPError("u", 401, "no session", {}, None), good]
    signed: list[str] = []

    def fake_get(url: str) -> dict:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(verify, "get_json", fake_get)
    monkeypatch.setattr(verify, "sign_in", signed.append)
    monkeypatch.setattr(verify.time, "sleep", lambda s: None)
    ok, _ = verify.wait_fresh("http://x", started, limit=60)
    assert ok is True and signed == ["http://x"] and answers == []


def test_wait_fresh_signs_in_once(verify: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """R5-X03 (D162): a second 401 after the sign-in is the last error; no second /auth/mock."""
    import urllib.error
    from datetime import datetime

    def deny(url: str) -> dict:
        raise urllib.error.HTTPError(url, 401, "no session", {}, None)  # type: ignore[arg-type]

    signed: list[str] = []
    sleeps: list[float] = []
    clock = iter([0.0, 0.0, 1.0, 2.0, 3.0, 10_000.0, 10_000.0])
    monkeypatch.setattr(verify, "get_json", deny)
    monkeypatch.setattr(verify, "sign_in", signed.append)
    monkeypatch.setattr(verify.time, "sleep", sleeps.append)
    monkeypatch.setattr(verify.time, "monotonic", lambda: next(clock))
    ok, note = verify.wait_fresh("http://x", datetime.now(verify.IST), limit=60)
    assert ok is False and signed == ["http://x"]
    assert len(sleeps) >= 2  # it kept polling after the second 401
    assert "401" in note  # the second 401 is reported as the last error


def test_mock_signins_only_before_first_press() -> None:
    """R5-X02 (D162): POST /auth/mock is set-up. After a control press it is a finding."""
    spec = importlib.util.spec_from_file_location(
        "browser_qa_script_x02", ROOT / "scripts" / "browser_qa.py"
    )
    assert spec and spec.loader
    qa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qa)
    run = qa.Run()
    handlers: dict[str, object] = {}

    class _Page:
        def on(self, event: str, fn: object) -> None:
            handlers[event] = fn

    class _Post:
        method = "POST"
        headers = {"x-saley-act": "1"}  # noqa: RUF012
        url = "http://localhost:8091/auth/mock"

    qa.watch(_Page(), run)
    handlers["request"](_Post())
    assert run.findings == [] and len(run.sign_ins) == 1
    run.act_clicks = 1
    handlers["request"](_Post())
    assert run.findings == ["sign-in after a control press"] and len(run.sign_ins) == 2


def test_wait_fresh_tries_a_failed_sign_in_again(
    verify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sign-in that fails (a 500 while the server starts) does not use up the one sign-in."""
    import urllib.error
    from datetime import datetime

    started = datetime.now(verify.IST)  # before the status time, as on a real start
    good = {
        "state": "ok",
        "message": "Updated",
        "last_computed": datetime.now(verify.IST).isoformat(),
    }
    no_session = urllib.error.HTTPError("u", 401, "no session", {}, None)
    answers: list = [no_session, no_session, good]
    tries: list[str] = []

    def fake_get(url: str) -> dict:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def flaky_sign_in(base: str) -> None:
        tries.append(base)
        if len(tries) == 1:
            raise urllib.error.HTTPError("u", 500, "starting", {}, None)

    monkeypatch.setattr(verify, "get_json", fake_get)
    monkeypatch.setattr(verify, "sign_in", flaky_sign_in)
    monkeypatch.setattr(verify.time, "sleep", lambda s: None)
    ok, _ = verify.wait_fresh("http://x", started, limit=60)
    assert ok is True and len(tries) == 2 and answers == []


# ------------------------------------------------------------------ step 1: dashboard v2 (R5 T10)


def _web_run(verify: ModuleType, monkeypatch: pytest.MonkeyPatch, codes: dict[str, int]) -> list:
    """Fake `run`: python steps pass; npm scripts answer the given exit code. No npm runs."""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], timeout: int = 900, env: dict | None = None) -> tuple[int, str]:
        calls.append(cmd)
        if cmd[0] == "/bin/npm":
            return codes.get(cmd[-1], 0), f"{cmd[-1]} output"
        return 0, "ok"

    monkeypatch.setattr(verify, "run", fake_run)
    monkeypatch.setattr(verify.shutil, "which", lambda name: "/bin/npm")
    return calls


def test_step_1_runs_eslint_tsc_and_passes_on_a_fresh_stamp(
    verify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _web_run(verify, monkeypatch, {})
    monkeypatch.setattr(verify.build_stamp, "check", lambda: SimpleNamespace(ok=True, note="ok"))
    result = verify.step_static(SimpleNamespace())
    assert result.status == "PASS", result.note
    npm = [c[1:] for c in calls if c[0] == "/bin/npm"]
    assert npm == [["--prefix", "web", "run", "lint"], ["--prefix", "web", "run", "typecheck"]]
    assert "build stamp" in result.note


def test_step_1_fails_on_a_stale_or_missing_build(
    verify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _web_run(verify, monkeypatch, {})
    stale = SimpleNamespace(ok=False, note="Run npm --prefix web run build.")
    monkeypatch.setattr(verify.build_stamp, "check", lambda: stale)
    result = verify.step_static(SimpleNamespace())
    assert result.status == "FAIL"
    assert "Run npm --prefix web run build" in result.note


def test_step_1_fails_on_eslint_or_tsc_or_no_npm(
    verify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(verify.build_stamp, "check", lambda: SimpleNamespace(ok=True, note="ok"))
    _web_run(verify, monkeypatch, {"lint": 1})
    assert verify.step_static(SimpleNamespace()).note.startswith("eslint:")
    _web_run(verify, monkeypatch, {"typecheck": 2})
    assert verify.step_static(SimpleNamespace()).note.startswith("tsc:")
    _web_run(verify, monkeypatch, {})
    monkeypatch.setattr(verify.shutil, "which", lambda name: None)
    result = verify.step_static(SimpleNamespace())
    assert result.status == "FAIL" and "Node 24" in result.note


def test_fingerprint_reads_web_sources_but_not_installs_builds_or_keys(
    verify: ModuleType, tmp_path: Path
) -> None:
    for rel in (
        "web/src/App.tsx",
        "web/package-lock.json",
        "web/node_modules/x/index.js",
        "web/dist/a.js",
        "web/svc-write.json",
        "web/notes.md",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    names = [p.relative_to(tmp_path).as_posix() for p in verify.code_files(tmp_path / "web")]
    assert names == ["web/package-lock.json", "web/src/App.tsx"]
