"""The `saley` CLI: commands exist and the Discord commands refuse outside the test loop."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text
from typer.testing import CliRunner

from saley.cli import app
from saley.core.settings import get_settings

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


def test_dev_starts_web_and_scheduler(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """`saley dev` starts the background scheduler, serves on 127.0.0.1:WEB_PORT, stops both."""
    import uvicorn

    from saley.worker import scheduler as sched

    calls: dict[str, object] = {}

    class FakeScheduler:
        def shutdown(self, wait: bool) -> None:
            calls["shutdown"] = wait

    monkeypatch.setenv("GTM_SHEET_ID", "x")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "x.json")
    monkeypatch.setenv("WEB_PORT", "8123")
    get_settings.cache_clear()
    monkeypatch.setattr(sched, "start_background", lambda reap=True: FakeScheduler())
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.update(k))
    try:
        result = runner.invoke(app, ["dev"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    assert (calls["host"], calls["port"], calls["shutdown"]) == ("127.0.0.1", 8123, False)


def test_background_scheduler_runs_first_jobs_now(engine: Engine) -> None:
    from saley.worker.scheduler import start_background

    scheduler = start_background()
    try:
        ids = {job.id for job in scheduler.get_jobs()}
        assert {"first_heartbeat", "first_sheet_sync", "sheet_sync", "heartbeat"} & ids
    finally:
        scheduler.shutdown(wait=False)
        with engine.begin() as conn:
            conn.execute(text("truncate job_runs, job_locks"))
