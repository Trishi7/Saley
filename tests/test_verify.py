"""scripts/verify.py: the gate's bookkeeping (state, GATE line, coverage rule, report)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

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
    assert verify.gate_line(results) == "GATE: PASS"
    results[8] = verify.Result(8, "FAIL", "console error")
    assert verify.gate_line(results) == "GATE: FAIL — step 8"
    del results[8]
    assert verify.gate_line(results) == "GATE: FAIL — step 8"  # a missing step is a fail


def test_record_and_finalize(verify: ModuleType) -> None:
    state = {
        str(n): {"step": n, "status": "PASS", "note": "ok", "seconds": 1.0} for n in range(1, 10)
    }
    verify.state_path("R9").write_text(json.dumps(state), encoding="utf-8")
    verify.fingerprint_path("R9").write_text(verify.code_fingerprint(), encoding="utf-8")
    assert verify.main(["--release", "R9", "--finalize"]) == 1
    assert verify.main(["--release", "R9", "--record", "10", "PASS", "reviewers clean"]) == 0
    assert verify.main(["--release", "R9", "--record", "11", "pass", "docs updated"]) == 0
    assert verify.main(["--release", "R9", "--finalize"]) == 0
    report = next(verify.REPORTS.glob("R9-2*.md")).read_text(encoding="utf-8")
    assert report.splitlines()[0] == "GATE: PASS"
    assert "Env changes:" in report and "- NEW:" in report
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
    assert report.splitlines()[0] == "GATE: FAIL — step 1"
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
        {"verdict": "SHIP", "checks": 9, "findings": [], "non_get": [], "screens": 3, "dir": "d"}
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


def test_busy_port_fails_step_7_clearly(
    verify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(verify, "port_free", lambda port: False)
    monkeypatch.setattr(verify, "port_owner", lambda port: "NIWebServer.exe, PID 4321")
    args = type("A", (), {"port": 8090, "release": "R9"})()
    r7, r8, r9 = verify.step_dev_and_browser(args)
    assert r7.status == "FAIL" and "in use by NIWebServer.exe, PID 4321" in r7.note
    assert "WEB_PORT" in r7.note
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


def test_default_port_comes_from_web_port(
    verify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, int] = {}
    monkeypatch.setattr(verify, "setting", lambda name, fallback: 8123)
    monkeypatch.setattr(verify, "AUTOMATED", [])
    monkeypatch.setattr(
        verify, "step_dev_and_browser", lambda args: seen.update(port=args.port) or []
    )
    monkeypatch.setattr(verify, "fingerprint_path", lambda release: verify.REPORTS / "x.tree")
    monkeypatch.setattr(verify, "save", lambda release, results: None)
    monkeypatch.setattr(verify, "write_report", lambda *a, **k: Path("x.md"))
    verify.main(["--release", "R9"])
    assert seen["port"] == 8123


def test_env_changes_read_from_status(verify: ModuleType, tmp_path: Path) -> None:
    status = tmp_path / "STATUS.md"
    status.write_text(
        "| R7 | NEW | `A_KEY` |\n| R7 | RETIRED | `OLD_KEY` |\n| R6 | NEW | `B` |\n",
        encoding="utf-8",
    )
    lines = verify.env_changes("R7", status)
    assert lines == ["- NEW: `A_KEY`", "- CHANGED-default: none", "- RETIRED: `OLD_KEY`"]
