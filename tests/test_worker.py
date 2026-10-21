"""saley.worker: DB locks with expiry, job_runs rows, failures, heartbeat, schedule."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from types import SimpleNamespace

import pytest
import structlog
from sqlalchemy import Engine, text

from saley.core.clock import now_ist
from saley.worker import locks, registry
from saley.worker.jobs import register_all
from saley.worker.scheduler import build_scheduler


@pytest.fixture(autouse=True)
def sheet_settings(set_env: Callable[..., None]) -> None:
    """The sheet jobs are enabled only where GTM_SHEET_ID and the key file are set (B3)."""
    set_env(GTM_SHEET_ID="sheet-x", GOOGLE_SERVICE_ACCOUNT_FILE="key.json")


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


def test_quiet_job_leaves_no_row_when_idle(clean: Engine) -> None:
    """A job that runs every 2 s and did nothing must not fill job_runs."""
    notes = iter([registry.QUIET_NOTE, "pushed 1 · failed 0 · retry 0"])
    registry.register("t_quiet", "interval", quiet=True, seconds=2)(lambda: next(notes))
    assert registry.run_job("t_quiet", clean) == "ok"
    assert _rows(clean, "t_quiet") == []
    assert registry.run_job("t_quiet", clean) == "ok"
    assert _rows(clean, "t_quiet") == [("ok", "pushed 1 · failed 0 · retry 0")]


def test_quiet_job_skipped_by_lock_leaves_no_row(clean: Engine) -> None:
    registry.register("t_quiet_lock", "interval", quiet=True, seconds=2)(lambda: "x")
    assert locks.acquire(clean, "t_quiet_lock", "other")
    assert registry.run_job("t_quiet_lock", clean) == "skipped"
    assert _rows(clean, "t_quiet_lock") == []


def test_sheet_push_runs_only_in_a_worker_process() -> None:
    """D23, D46: `saley dev` (background scheduler) never schedules the write job."""
    register_all()
    spec = registry.REGISTRY["sheet_push"]
    assert (spec.lock, spec.quiet, spec.worker_only) == ("sheet_write", True, True)
    background = {j.id for j in build_scheduler(background=True).get_jobs()}
    foreground = {j.id for j in build_scheduler(background=False).get_jobs()}
    assert "sheet_push" not in background
    assert "sheet_push" in foreground and "sheet_sync" in background


# ---------------------------------------- B3: a job without its settings is disabled


def test_a_sheet_job_without_its_settings_is_disabled_with_one_line(
    clean: Engine, set_env: Callable[..., None]
) -> None:
    set_env(GTM_SHEET_ID="", GOOGLE_SERVICE_ACCOUNT_FILE="")
    with structlog.testing.capture_logs() as logs:
        register_all()
    disabled = [e for e in logs if e["event"] == "job_disabled"]
    assert {e["job"] for e in disabled} == {"sheet_sync", "parity_check"}
    sync = next(e for e in disabled if e["job"] == "sheet_sync")
    assert sync["missing"] == "GTM_SHEET_ID,GOOGLE_SERVICE_ACCOUNT_FILE"
    assert all(e["log_level"] == "warning" and "exc_info" not in e for e in disabled)
    assert registry.REGISTRY["sheet_sync"].disabled is True
    assert registry.REGISTRY["heartbeat"].disabled is False


def test_a_disabled_job_is_never_scheduled_and_never_runs(
    clean: Engine, set_env: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    from saley.worker import jobs

    set_env(GTM_SHEET_ID="")
    ran: list[str] = []
    monkeypatch.setattr(jobs, "sheet_sync", lambda: ran.append("sync") or "x")
    ids = {j.id for j in build_scheduler().get_jobs()}
    assert "sheet_sync" not in ids and "parity_check" not in ids and "heartbeat" in ids
    background = {j.id for j in build_scheduler(background=True).get_jobs()}
    assert "sheet_sync" not in background
    result = registry.run_job("sheet_sync", clean)
    assert result == "skipped" and "GTM_SHEET_ID" in (result.note or "")
    assert ran == [] and _rows(clean, "sheet_sync") == []  # no run, no job_runs row


def test_the_log_line_comes_once_per_registration_not_per_run(
    clean: Engine, set_env: Callable[..., None]
) -> None:
    set_env(GTM_SHEET_ID="")
    register_all()
    with structlog.testing.capture_logs() as logs:
        for _ in range(3):
            registry.run_job("sheet_sync", clean)
    assert [e for e in logs if e["event"] == "job_disabled"] == []


def test_the_first_runs_skip_a_disabled_sheet_sync(
    set_env: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    from saley.worker import scheduler as sched

    set_env(GTM_SHEET_ID="")
    register_all()
    assert sched._first_runs() == ["heartbeat"]
    set_env(GTM_SHEET_ID="sheet-x")
    register_all()
    assert sched._first_runs() == ["heartbeat", "sheet_sync"]


def test_the_sheet_jobs_stay_enabled_where_the_settings_exist(clean: Engine) -> None:
    register_all()
    assert registry.REGISTRY["sheet_sync"].missing == ()
    assert {"sheet_sync", "parity_check"} <= {j.id for j in build_scheduler().get_jobs()}


# ---------------------------------------- D113 worker slots


def test_d113_a_worker_reaps_and_counts_only_its_own_slot(
    clean: Engine, set_env: Callable[..., None]
) -> None:
    from saley.worker.scheduler import recent_worker

    old = now_ist() - timedelta(minutes=1)
    with clean.begin() as conn:
        for slot in ("worker:main", "worker:gate"):
            conn.execute(
                text(
                    "insert into job_runs(job, status, started_at, slot) "
                    "values ('x', 'running', :t, :s)"
                ),
                {"t": old, "s": slot},
            )
    assert locks.acquire(clean, "sheet_sync", locks.new_holder("worker:main"))
    assert locks.acquire(clean, "sheet_push", locks.new_holder("worker:gate"))

    set_env(WORKER_SLOT="worker:gate")
    assert recent_worker(timedelta(minutes=5)) is True
    assert registry.reap_stale(clean) == 1  # only the gate's own open run
    with clean.connect() as conn:
        left = dict(conn.execute(text("select slot, status from job_runs")).all())
        holders = [h for (h,) in conn.execute(text("select holder from job_locks order by name"))]
    assert left == {"worker:main": "running", "worker:gate": "failed"}
    assert [h.split("@")[0] for h in holders] == ["worker:main"]  # the dev worker's lock stays

    with clean.begin() as conn:
        conn.execute(text("delete from job_runs where slot = 'worker:gate'"))
    assert recent_worker(timedelta(minutes=5)) is False  # the main worker does not block the gate
    # Lock names stay global: the gate cannot run a job the main worker holds right now.
    assert not locks.acquire(clean, "sheet_sync", locks.new_holder("worker:gate"))


def test_d113_the_slot_name_is_checked(set_env: Callable[..., None]) -> None:
    from pydantic import ValidationError

    from saley.core.settings import Settings

    assert Settings().worker_slot == "worker:main" and Settings().gate_port == 8091
    for bad in ("main", "worker:", "worker:Gate!", "worker:" + "x" * 31):
        with pytest.raises(ValidationError):
            Settings(worker_slot=bad)


def test_d113_main_frees_its_old_unslotted_locks_and_slot_names_are_literal(
    clean: Engine, set_env: Callable[..., None]
) -> None:
    assert locks.acquire(clean, "old", "laptop:123:abcd1234")  # a holder from before 0017
    assert locks.acquire(clean, "under", locks.new_holder("worker:a_b"))
    assert locks.acquire(clean, "other", locks.new_holder("worker:axb"))
    set_env(WORKER_SLOT="worker:a_b")
    registry.reap_stale(clean)
    with clean.connect() as conn:
        names = sorted(n for (n,) in conn.execute(text("select name from job_locks")))
    assert names == ["old", "other"]  # `_` is no wildcard; the gate never frees main's locks
    set_env(WORKER_SLOT="worker:main")
    registry.reap_stale(clean)
    with clean.connect() as conn:
        names = sorted(n for (n,) in conn.execute(text("select name from job_locks")))
    assert names == ["other"]


# ---------------------------------------- R5 T8: news once at dev worker start (D151, amends D70)

NEWS_DEV_JOBS = {"news_requests", "news_topic_backfill", "news_start"}


@pytest.fixture
def news_registry():
    """No news_* entry before or after: other tests build schedulers in the same process."""

    def clear() -> None:
        for name in [n for n in registry.REGISTRY if n.startswith("news_")]:
            del registry.REGISTRY[name]

    clear()
    yield
    clear()


def _news_ids(background: bool = False) -> set[str]:
    return {j.id for j in build_scheduler(background=background).get_jobs()} & NEWS_DEV_JOBS


def _fake_edition(calls: list[tuple[str, str]], engine: Engine):
    from saley.news.editions import EditionReport

    def run(slot: str, family: str) -> EditionReport:
        calls.append((slot, family))
        with engine.begin() as conn:
            conn.execute(
                text("insert into news_editions (slot, family, status) values (:s, :f, 'ok')"),
                {"s": slot, "f": family},
            )
        return EditionReport(1, slot, family)

    return run


def test_dev_worker_runs_news_once_main_slot_only(
    clean: Engine,
    set_env: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
    news_registry: None,
) -> None:
    """R5-N06 (D151): dev + worker:main fetches once at start, and only when the last is old."""
    from saley.news import jobs as news_jobs
    from saley.worker.scheduler import queue_start_runs

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(news_jobs, "run_edition", _fake_edition(calls, clean))
    scheduler = build_scheduler()
    ids = {j.id for j in scheduler.get_jobs()}
    # Dev adds the request job and the backfill. The clocked editions stay in `saley news` only.
    assert {"news_requests", "news_topic_backfill"} <= ids
    assert "news_start" not in ids and not [i for i in ids if i.startswith("news_edition")]
    assert queue_start_runs(scheduler) == ["news_start", "news_topic_backfill"]
    assert scheduler.get_job("first_news_start") is not None
    # No edition yet: the start job fetches once. A fresh one makes the next start idle.
    assert registry.run_job("news_start", clean) == "ok"
    assert calls == [("start", "news")]
    assert registry.run_job("news_start", clean) == "ok" and len(calls) == 1
    with clean.begin() as conn:
        conn.execute(text("update news_editions set started_at = now() - interval '7 hours'"))
    assert registry.run_job("news_start", clean) == "ok" and len(calls) == 2
    # A failed edition does not count: the next start tries again.
    with clean.begin() as conn:
        conn.execute(text("update news_editions set status = 'failed'"))
    assert registry.run_job("news_start", clean) == "ok" and len(calls) == 3
    with clean.begin() as conn:
        conn.execute(text("truncate news_editions restart identity cascade"))


def test_gate_slot_and_other_envs_never_fetch(
    clean: Engine, set_env: Callable[..., None], news_registry: None
) -> None:
    from saley.worker.scheduler import queue_start_runs

    set_env(WORKER_SLOT="worker:gate")
    scheduler = build_scheduler()
    assert _news_ids() == set() and queue_start_runs(scheduler) == []
    assert not [n for n in registry.REGISTRY if n.startswith("news_")]
    set_env(WORKER_SLOT="worker:main", SALEY_ENV="prod")
    scheduler = build_scheduler()
    assert _news_ids() == set() and queue_start_runs(scheduler) == []
    assert _news_ids(background=True) == set()
    assert not [n for n in registry.REGISTRY if n.startswith("news_")]


def test_news_run_at_start_false_keeps_the_request_job_but_not_the_start(
    clean: Engine, set_env: Callable[..., None], news_registry: None
) -> None:
    from saley.worker.scheduler import queue_start_runs

    set_env(NEWS_RUN_AT_START="false")
    scheduler = build_scheduler()
    assert {"news_requests", "news_topic_backfill"} <= {j.id for j in scheduler.get_jobs()}
    assert queue_start_runs(scheduler) == []  # Run news now still works; nothing fetches by itself


def test_run_forever_queues_the_news_start_after_the_first_runs(
    monkeypatch: pytest.MonkeyPatch, news_registry: None
) -> None:
    from saley.worker import scheduler as sched

    order: list[str] = []

    class Fake:
        def add_job(self, func, trigger, **kwargs) -> None:
            order.append(f"add:{kwargs['id']}")

        def start(self) -> None:
            order.append("start")
            raise KeyboardInterrupt

        def shutdown(self, wait: bool = False) -> None:
            pass

    def build(background: bool = False) -> Fake:
        register_all()
        return Fake()

    monkeypatch.setattr(sched, "build_scheduler", build)
    monkeypatch.setattr(sched, "reap_stale", lambda: 0)
    monkeypatch.setattr(sched, "_first_runs", lambda: ["heartbeat"])
    monkeypatch.setattr(sched, "run_job", lambda name: order.append(f"run:{name}"))
    monkeypatch.setattr(sched.signal, "signal", lambda *_a: None)
    monkeypatch.setattr(sched, "start_job_names", lambda: ["news_start", "news_topic_backfill"])
    sched.run_forever()
    assert order == [
        "run:heartbeat",
        "add:first_news_start",
        "add:first_news_topic_backfill",
        "start",
    ]
