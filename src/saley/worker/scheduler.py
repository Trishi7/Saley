"""APScheduler for the worker container, or beside the web in `saley dev`. Times are IST."""

from __future__ import annotations

import signal
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import BaseScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import text

from saley.core.clock import tz
from saley.core.db import rw_engine
from saley.core.logging import get_logger
from saley.core.settings import get_settings
from saley.news.jobs import register_dev_news, start_job_names
from saley.worker.jobs import register_all
from saley.worker.registry import REGISTRY, reap_stale, run_job

log = get_logger("saley.worker")


def build_scheduler(background: bool = False) -> BaseScheduler:
    """All registered jobs on one scheduler. Background mode runs beside the web (saley dev)."""
    register_all()
    register_dev_news()  # D151: dev + worker:main only; adds nothing in the gate or in production
    kind = BackgroundScheduler if background else BlockingScheduler
    scheduler = kind(timezone=tz(), job_defaults={"coalesce": True, "max_instances": 1})
    for spec in REGISTRY.values():
        if spec.disabled:  # B3: registration logged why; it is never scheduled
            continue
        if spec.start_only:  # D151: queued once at start by queue_start_runs, never on a clock
            continue
        if background and spec.worker_only:
            # D23, D46: `saley dev` runs beside the web. Writes push from `saley worker` only.
            log.warning("job_not_in_dev", job=spec.name, hint="run `uv run saley worker`")
            continue
        scheduler.add_job(
            run_job, spec.trigger, args=[spec.name], id=spec.name, **spec.trigger_args
        )
    return scheduler


def _first_runs() -> list[str]:
    """The jobs that run once at start: heartbeat and sheet_sync, unless disabled (B3)."""
    return [name for name in ("heartbeat", "sheet_sync") if not REGISTRY[name].disabled]


def queue_start_runs(scheduler: BaseScheduler) -> list[str]:
    """D151: queue the one-time news jobs as "date" jobs, so the sheet jobs never wait for them.

    `misfire_grace_time=None`: a slow first sync must not drop the news run. The news_start job
    checks the age of the newest edition itself. The gate slot and any other env get no jobs.
    """
    names = start_job_names()
    for name in names:
        scheduler.add_job(
            run_job,
            "date",
            run_date=datetime.now(tz()),
            args=[name],
            id=f"first_{name}",
            misfire_grace_time=None,
        )
    return names


def recent_worker(window: timedelta) -> bool:
    """True when a worker of this slot started a job inside the window: it is alive (D113)."""
    with rw_engine().connect() as conn:
        return bool(
            conn.execute(
                text(
                    "select 1 from job_runs where slot = :slot "
                    "and started_at > now() - make_interval(secs => :s) limit 1"
                ),
                {"s": window.total_seconds(), "slot": get_settings().worker_slot},
            ).first()
        )


def start_background(reap: bool = True) -> BaseScheduler:
    """For `saley dev`: clean up, start the scheduler, run heartbeat and sync at once.

    `reap=False` (`saley dev --force`): another worker may still be alive. Its running rows
    and its locks are real, so they stay; the locks keep the two from running one job twice.
    """
    scheduler = build_scheduler(background=True)
    reaped = reap_stale() if reap else 0
    if reaped:
        log.warning("job_runs_reaped", rows=reaped)
    scheduler.start()
    now = datetime.now(tz())
    for name in _first_runs():
        scheduler.add_job(run_job, "date", run_date=now, args=[name], id=f"first_{name}")
    log.info("worker_start", jobs=sorted(REGISTRY), mode="background")
    return scheduler


def run_forever(reap: bool = True) -> None:
    """The worker loop. `reap=False` (`saley dev --force`): another worker may be alive."""
    scheduler = build_scheduler()
    log.info("worker_start", jobs=sorted(REGISTRY))
    try:
        reaped = reap_stale() if reap else 0
        if reaped:
            log.warning("job_runs_reaped", rows=reaped)
        for name in _first_runs():
            run_job(name)
    except Exception:
        # The DB may be starting. The scheduler retries on its own clock.
        log.exception("worker_first_runs_failed")
    queue_start_runs(scheduler)
    signal.signal(signal.SIGTERM, lambda *_: scheduler.shutdown(wait=False))
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("worker_stop")
