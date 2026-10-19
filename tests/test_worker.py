"""saley.worker: DB locks with expiry, job_runs rows, failures, heartbeat, schedule."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import Engine, text

from saley.core.clock import now_ist
from saley.worker import locks, registry
from saley.worker.jobs import register_all
from saley.worker.scheduler import build_scheduler


@pytest.fixture
def clean(engine: Engine):
    yield engine
    with engine.begin() as conn:
        conn.execute(text("truncate job_runs, job_locks restart identity"))


def test_lock_is_exclusive_until_released(clean: Engine) -> None:
    assert locks.acquire(clean, "j", "a")
    assert not locks.acquire(clean, "j", "b")
    locks.release(clean, "j", "a")
    assert locks.acquire(clean, "j", "b")


def test_expired_lock_is_taken_over(clean: Engine) -> None:
    assert locks.acquire(clean, "j", "dead", expiry=timedelta(minutes=-1))
    assert locks.acquire(clean, "j", "alive")
    with clean.connect() as conn:
        assert (
            conn.execute(text("select holder from job_locks where name = 'j'")).scalar() == "alive"
        )


def test_lock_expiry_is_15_minutes() -> None:
    assert timedelta(minutes=15) == locks.LOCK_EXPIRY


def _rows(engine: Engine, job: str) -> list[tuple[str, str | None]]:
    with engine.connect() as conn:
        return [
            tuple(r)
            for r in conn.execute(
                text("select status, note from job_runs where job = :j order by id"), {"j": job}
            )
        ]


def test_run_job_writes_ok_row(clean: Engine) -> None:
    registry.register("t_ok", "interval", minutes=1)(lambda: "did it")
    assert registry.run_job("t_ok", clean) == "ok"
    assert _rows(clean, "t_ok") == [("ok", "did it")]


def test_failed_job_is_recorded_and_lock_released(clean: Engine) -> None:
    def boom() -> str:
        raise ValueError("bad cell")

    registry.register("t_fail", "interval", minutes=1)(boom)
    assert registry.run_job("t_fail", clean) == "failed"
    assert _rows(clean, "t_fail") == [("failed", "ValueError: bad cell")]
    assert locks.acquire(clean, "t_fail", "next")


def test_locked_job_is_skipped(clean: Engine) -> None:
    registry.register("t_lock", "interval", minutes=1)(lambda: "x")
    locks.acquire(clean, "t_lock", "other")
    assert registry.run_job("t_lock", clean) == "skipped"
    assert _rows(clean, "t_lock")[0][0] == "skipped"


def test_heartbeat_and_schedule() -> None:
    scheduler = build_scheduler()
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) >= {"sheet_sync", "metrics_snapshot", "parity_check", "heartbeat"}
    assert jobs["heartbeat"].trigger.interval == timedelta(minutes=5)
    assert jobs["parity_check"].trigger.interval == timedelta(hours=1)
    assert str(jobs["metrics_snapshot"].trigger.timezone) == "Asia/Kolkata"
    assert "hour='23'" in str(jobs["metrics_snapshot"].trigger)
    assert "minute='50'" in str(jobs["metrics_snapshot"].trigger)


def test_heartbeat_job_runs(clean: Engine) -> None:
    register_all()
    assert registry.run_job("heartbeat", clean) == "ok"
    assert _rows(clean, "heartbeat") == [("ok", "worker alive")]


# ---------------------------------------- review fixes: worker


def test_lock_renew_and_lost_lock(clean: Engine) -> None:
    assert locks.acquire(clean, "r", "a")
    assert locks.renew(clean, "r", "a")
    assert not locks.renew(clean, "r", "b")


def test_reap_stale_running_rows(clean: Engine) -> None:
    with clean.begin() as conn:
        conn.execute(
            text("insert into job_runs(job, status, started_at) values ('x', 'running', :t)"),
            {"t": now_ist() - timedelta(hours=1)},
        )
    assert registry.reap_stale(clean) == 1


# ---------------------------------------- review fixes: honest job status


def test_parity_mismatch_fails_the_job(clean: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from saley.intel.parity import ParityResult
    from saley.worker import jobs

    monkeypatch.setattr(jobs, "read_sheet", lambda: None)
    monkeypatch.setattr(jobs, "run_sync", lambda session, data: SimpleNamespace(stale=[]))
    monkeypatch.setattr(
        jobs, "run_parity", lambda session, data, stale=(): [ParityResult("people_total", 7, 6)]
    )
    register_all()
    assert registry.run_job("parity_check", clean) == "failed"
    status, note = _rows(clean, "parity_check")[0]
    assert status == "failed" and "people_total" in (note or "")


def test_metrics_refuse_stale_data(clean: Engine) -> None:
    register_all()
    assert registry.run_job("metrics_snapshot", clean) == "failed"
    assert "never" in (_rows(clean, "metrics_snapshot")[0][1] or "")


def test_parity_shares_the_sync_lock() -> None:
    register_all()
    assert registry.REGISTRY["parity_check"].lock == "sheet_sync"


def test_parity_with_stale_tabs_says_partial(
    clean: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from saley.intel.parity import ParityResult
    from saley.worker import jobs

    monkeypatch.setattr(jobs, "read_sheet", lambda: None)
    monkeypatch.setattr(
        jobs, "run_sync", lambda session, data: SimpleNamespace(stale=["deliverables"])
    )
    monkeypatch.setattr(
        jobs, "run_parity", lambda session, data, stale=(): [ParityResult("people_total", 7, 7)]
    )
    register_all()
    assert registry.run_job("parity_check", clean) == "ok"
    note = _rows(clean, "parity_check")[0][1] or ""
    assert note.startswith("PARTIAL:") and "deliverables" in note and "all match" not in note


def test_parity_with_nothing_compared_fails(clean: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from saley.worker import jobs

    monkeypatch.setattr(jobs, "read_sheet", lambda: None)
    monkeypatch.setattr(
        jobs, "run_sync", lambda session, data: SimpleNamespace(stale=["poc", "deliverables"])
    )
    monkeypatch.setattr(jobs, "run_parity", lambda session, data, stale=(): [])
    register_all()
    assert registry.run_job("parity_check", clean) == "failed"


def test_parity_missing_metric_is_a_mismatch() -> None:
    from saley.intel.parity import compare

    results = {r.metric: r for r in compare({"people_total": 3}, {"stage:Won": 0})}
    assert not results["people_total"].ok and results["people_total"].view_value is None
    assert results["stage:Won"].ok  # a stage with nobody in it is a true 0
