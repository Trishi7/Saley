"""The `saley` CLI: commands exist and the Discord commands refuse outside the test loop."""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Any

import pytest
from sqlalchemy import Engine, text
from typer.testing import CliRunner

from saley.cli import app
from saley.core.settings import get_settings, set_process

runner = CliRunner()


def test_help_lists_every_command() -> None:
    out = runner.invoke(app, ["--help"]).output
    for name in (
        "web",
        "bot",
        "worker",
        "news",
        "sync",
        "metrics",
        "db-upgrade",
        "health",
        "discord",
    ):
        assert name in out


def test_discord_send_requires_test_flag() -> None:
    result = runner.invoke(app, ["discord", "send", "hello"])
    assert result.exit_code == 1
    assert "--test" in result.output


def test_discord_roundtrip_requires_test_flag() -> None:
    result = runner.invoke(app, ["discord", "roundtrip", "--case", "pack"])
    assert result.exit_code == 1
    assert "--test" in result.output


def test_discord_roundtrip_exits_1_when_not_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    from saley.bot import roundtrip

    bad = roundtrip.RoundtripResult("pack", "555", matched=True, deleted=False)
    monkeypatch.setattr(roundtrip, "run", lambda case: bad)
    result = runner.invoke(app, ["discord", "roundtrip", "--test", "--case", "pack"])
    assert result.exit_code == 1
    assert "deleted NO" in result.output


def test_discord_read_requires_test_flag() -> None:
    result = runner.invoke(app, ["discord", "read", "--last", "2"])
    assert result.exit_code == 1


def test_discord_refuses_when_test_mode_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_MODE", "false")
    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["discord", "send", "--test", "hello"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 1
    assert "TEST_MODE" in result.output


def test_sync_without_once_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GTM_SHEET_ID", "x")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "x.json")
    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["sync"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 1
    assert "--once" in result.output


def test_sync_names_missing_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GTM_SHEET_ID", "")
    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["sync", "--once"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 1
    assert "GTM_SHEET_ID" in result.output


def test_worker_once_unknown_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GTM_SHEET_ID", "x")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "x.json")
    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["worker", "--once", "nope"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 1
    assert "heartbeat" in result.output


def test_dev_refuses_when_another_worker_is_alive(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D43: `saley dev` must not fight a running worker over job locks."""
    monkeypatch.setenv("GTM_SHEET_ID", "x")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "x.json")
    get_settings.cache_clear()
    with engine.begin() as conn:
        conn.execute(
            text("insert into job_runs(job, status, started_at) values ('heartbeat', 'ok', now())")
        )
    try:
        result = runner.invoke(app, ["dev"])
    finally:
        get_settings.cache_clear()
        with engine.begin() as conn:
            conn.execute(text("truncate job_runs"))
    assert result.exit_code == 1
    assert "--force" in result.output


class FakeChild:
    """A stand-in for the worker child process: no real process in a unit test."""

    pid = 4242

    def __init__(self, argv: list[str], alive: bool = True, stubborn: bool = False) -> None:
        self.argv = argv
        self.code: int | None = None if alive else 7
        self.stubborn = stubborn
        self.events: list[str] = []

    @property
    def returncode(self) -> int | None:
        return self.code

    def poll(self) -> int | None:
        return self.code

    def terminate(self) -> None:
        self.events.append("terminate")

    def wait(self, timeout: float | None = None) -> int:
        if self.stubborn and "kill" not in self.events:
            raise subprocess.TimeoutExpired(self.argv, timeout or 0)
        self.code = 0
        self.events.append("wait")
        return 0

    def kill(self) -> None:
        self.events.append("kill")


@pytest.fixture
def dev_env(monkeypatch: pytest.MonkeyPatch):
    """`saley dev` with no real child, no real server and no real sleep."""
    import uvicorn

    from saley import cli

    seen: dict[str, Any] = {"children": []}

    def popen(argv: list[str], **kwargs: Any) -> FakeChild:
        child = FakeChild(argv, alive=seen.get("alive", True), stubborn=seen.get("stubborn", False))
        seen["children"].append(child)
        seen["popen_kwargs"] = kwargs
        return child

    monkeypatch.setenv("GTM_SHEET_ID", "x")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "x.json")
    monkeypatch.setenv("WEB_PORT", "8123")
    get_settings.cache_clear()
    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    monkeypatch.setattr(cli, "_is_windows", lambda: False)
    monkeypatch.setattr(cli, "port_in_use", lambda port: False)

    class FakeConfig:
        """uvicorn.Config probes the platform with subprocess.Popen, which is faked here."""

        def __init__(self, app: str, **kwargs: Any) -> None:
            self.app, self.host, self.port = app, kwargs["host"], kwargs["port"]

    class FakeServer:
        """Stands in for uvicorn.Server: it records the config and runs the test's hook."""

        def __init__(self, config: Any) -> None:
            self.config = config
            self.should_exit = False

        def run(self) -> None:
            seen["web"] = {"host": self.config.host, "port": self.config.port}
            seen.get("on_run", lambda server: None)(self)

    monkeypatch.setattr(uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr(cli, "CHILD_WATCH_SECONDS", 0.01)
    yield seen
    get_settings.cache_clear()


def test_dev_runs_the_worker_as_a_child_process_and_stops_it(
    engine: Engine, dev_env: dict[str, Any]
) -> None:
    """D46: web in this process, `saley worker` as a child; the child stops with the web."""
    result = runner.invoke(app, ["dev"])
    assert result.exit_code == 0, result.output
    (child,) = dev_env["children"]
    assert child.argv == [sys.executable, "-m", "saley.cli", "worker"]
    assert (dev_env["web"]["host"], dev_env["web"]["port"]) == ("127.0.0.1", 8123)
    assert "env" not in dev_env["popen_kwargs"]  # the child inherits the same environment
    assert child.events == ["terminate", "wait"]


def test_dev_starts_web_and_scheduler(engine: Engine, dev_env: dict[str, Any]) -> None:
    """Matrix R1.1-U01: the web serves on 127.0.0.1:WEB_PORT and the scheduler runs.

    Since D46 the scheduler runs in the `saley worker` child, never in the web process.
    """
    result = runner.invoke(app, ["dev"])
    assert result.exit_code == 0, result.output
    assert (dev_env["web"]["host"], dev_env["web"]["port"]) == ("127.0.0.1", 8123)
    (child,) = dev_env["children"]
    assert child.argv[-2:] == ["saley.cli", "worker"]  # `saley worker` runs the scheduler


def test_dev_force_skips_the_recent_worker_check_and_keeps_old_rows(
    engine: Engine, dev_env: dict[str, Any]
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("insert into job_runs(job, status, started_at) values ('heartbeat', 'ok', now())")
        )
    try:
        result = runner.invoke(app, ["dev", "--force"])
    finally:
        with engine.begin() as conn:
            conn.execute(text("truncate job_runs"))
    assert result.exit_code == 0, result.output
    assert dev_env["children"][0].argv[-1] == "--no-reap"


def test_dev_stops_the_child_when_the_web_stops_with_an_error(
    engine: Engine, dev_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(server: Any) -> None:
        raise KeyboardInterrupt

    dev_env["on_run"] = crash
    result = runner.invoke(app, ["dev"])
    assert result.exit_code != 0
    assert dev_env["children"][0].events == ["terminate", "wait"]


def test_dev_kills_a_child_that_ignores_terminate(engine: Engine, dev_env: dict[str, Any]) -> None:
    dev_env["stubborn"] = True
    result = runner.invoke(app, ["dev"])
    assert result.exit_code == 0, result.output
    assert dev_env["children"][0].events == ["terminate", "kill", "wait"]


def test_dev_reports_a_worker_that_stops_at_start(engine: Engine, dev_env: dict[str, Any]) -> None:
    dev_env["alive"] = False
    result = runner.invoke(app, ["dev"])
    assert result.exit_code == 1
    assert "worker stopped at start (exit 7)" in result.output
    assert "web" not in dev_env  # the web never started


def test_dev_stops_the_whole_child_tree_on_windows(
    engine: Engine, dev_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """H3: `terminate()` ends one process on Windows and can orphan the worker: use taskkill."""
    from saley import cli

    calls: list[list[str]] = []
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kw: calls.append(argv))
    monkeypatch.setattr(cli, "_is_windows", lambda: True)
    result = runner.invoke(app, ["dev"])
    assert result.exit_code == 0, result.output
    assert calls == [["taskkill", "/PID", "4242", "/T", "/F"]]
    assert dev_env["children"][0].events == ["wait"]  # no terminate(): the tree is already gone


def test_dev_exits_1_loudly_when_the_worker_dies_while_the_web_runs(
    engine: Engine, dev_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """H3: a web without its worker never syncs or pushes a write: stop, exit 1, log it."""
    from saley import cli

    logged: list[tuple[str, dict[str, Any]]] = []

    class Recorder:
        def error(self, event: str, **fields: Any) -> None:
            logged.append((event, fields))

    monkeypatch.setattr(cli, "log", Recorder())

    def worker_dies_then_wait_for_stop(server: Any) -> None:
        dev_env["children"][0].code = 9
        deadline = time.monotonic() + 5
        while not server.should_exit and time.monotonic() < deadline:
            time.sleep(0.01)

    dev_env["on_run"] = worker_dies_then_wait_for_stop
    result = runner.invoke(app, ["dev"])
    assert result.exit_code == 1
    assert "The worker stopped (exit 9)" in result.output
    assert logged == [("worker_child_exited", {"exit_code": 9})]


def test_dev_exit_is_clean_when_the_web_stops_first(
    engine: Engine, dev_env: dict[str, Any]
) -> None:
    result = runner.invoke(app, ["dev"])
    assert result.exit_code == 0, result.output
    assert "worker stopped" not in result.output


def test_run_once_prints_the_note_of_this_run_not_the_newest_row(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M12: another worker may write a newer job_runs row; the command shows its own note."""
    from saley.worker import jobs, registry

    monkeypatch.setattr(jobs, "register_all", lambda: None)
    monkeypatch.setattr(registry, "run_job", lambda job: registry.JobResult("ok", "my own note"))
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into job_runs(job, status, started_at, note) "
                "values ('metrics_snapshot', 'ok', now() + interval '1 minute', 'someone else')"
            )
        )
    try:
        result = runner.invoke(app, ["metrics", "--once"])
    finally:
        with engine.begin() as conn:
            conn.execute(text("truncate job_runs"))
    assert result.exit_code == 0, result.output
    assert "metrics_snapshot: ok · my own note" in result.output
    assert "someone else" not in result.output


def test_run_job_result_is_a_string_with_its_note() -> None:
    from saley.worker.registry import JobResult

    result = JobResult("failed", "ValueError: x")
    assert result == "failed" and result.note == "ValueError: x"
    assert JobResult("ok").note is None


def test_worker_no_reap_reaches_the_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    from saley.worker import scheduler as sched

    seen: list[bool] = []
    monkeypatch.setenv("GTM_SHEET_ID", "x")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "x.json")
    get_settings.cache_clear()
    monkeypatch.setattr(sched, "run_forever", lambda reap=True: seen.append(reap))
    try:
        assert runner.invoke(app, ["worker"]).exit_code == 0
        assert runner.invoke(app, ["worker", "--no-reap"]).exit_code == 0
    finally:
        set_process("unknown")
        get_settings.cache_clear()
    assert seen == [True, False]


def test_the_dev_web_process_never_imports_the_sheet_writer() -> None:
    """D46: in `saley dev` the web process has no write client. Check in a clean interpreter."""
    code = (
        "import sys, saley.cli, saley.web.app;"
        "bad = [m for m in ('saley.sales.writer', 'saley.sales.outbox') if m in sys.modules];"
        "print(bad);"
        "raise SystemExit(1 if bad else 0)"
    )
    done = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr or done.stdout


def test_background_scheduler_runs_first_jobs_now(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first jobs run at once. sheet_sync is a stub: no sheet, no key, no traceback (B3)."""
    from saley.worker import jobs
    from saley.worker.scheduler import start_background

    monkeypatch.setenv("GTM_SHEET_ID", "x")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "x.json")
    get_settings.cache_clear()
    monkeypatch.setattr(jobs, "sheet_sync", lambda: "stub sync")
    scheduler = start_background()
    try:
        ids = {job.id for job in scheduler.get_jobs()}
        assert {"sheet_sync", "heartbeat"} <= ids  # the "first_*" jobs may be gone: they ran
        deadline = time.monotonic() + 10
        done: set[str] = set()
        while time.monotonic() < deadline and done != {"heartbeat", "sheet_sync"}:
            time.sleep(0.2)
            with engine.connect() as conn:
                done = {
                    r[0]
                    for r in conn.execute(
                        text(
                            "select job from job_runs "
                            "where status = 'ok' and finished_at is not null"
                        )
                    )
                } & {"heartbeat", "sheet_sync"}
        assert done == {"heartbeat", "sheet_sync"}
    finally:
        scheduler.shutdown(wait=False)
        with engine.begin() as conn:
            conn.execute(text("truncate job_runs, job_locks"))
        get_settings.cache_clear()


def test_roundtrip_n_below_two_is_refused() -> None:
    result = runner.invoke(
        app, ["sheet", "roundtrip", "--test-tab", "--case", "parity", "--n", "1"]
    )
    assert result.exit_code != 0


# ------------------------------------------------------------------ R5-L08 (Q-N5)


def test_dev_and_web_refuse_a_port_another_server_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows a second server can bind a taken port (SO_REUSEADDR) and split the requests,
    so one sign-in starts in one process and ends in another. Both commands refuse at start."""
    import socket

    from saley import cli

    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    port = holder.getsockname()[1]
    try:
        assert cli.port_in_use(port) is True
        monkeypatch.setenv("WEB_PORT", str(port))
        monkeypatch.setenv("GTM_SHEET_ID", "x")
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "x.json")
        monkeypatch.setattr("saley.worker.scheduler.recent_worker", lambda window: False)
        get_settings.cache_clear()
        for command in (["web"], ["dev", "--port", str(port)]):
            result = runner.invoke(app, command)
            assert result.exit_code == 1, command
            assert f"Port {port} is in use" in result.output, command
    finally:
        holder.close()
        get_settings.cache_clear()
    assert cli.port_in_use(port) is False
