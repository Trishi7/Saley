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
from saley.worker.jobs import register_all
from saley.worker.registry import REGISTRY, reap_stale, run_job

log = get_logger("saley.worker")


def build_scheduler(background: bool = False) -> BaseScheduler:
    """All registered jobs on one scheduler. Background mode runs beside the web (saley dev)."""
    register_all()
    kind = BackgroundScheduler if background else BlockingScheduler
    scheduler = kind(timezone=tz(), job_defaults={"coalesce": True, "max_instances": 1})
    for spec in REGISTRY.values():
        scheduler.add_job(
            run_job, spec.trigger, args=[spec.name], id=spec.name, **spec.trigger_args
        )
    return scheduler


def recent_worker(window: timedelta) -> bool:
    """True when some worker started a job inside the window: another worker is alive."""
    with rw_engine().connect() as conn:
        return bool(
            conn.execute(
                text(
                    "select 1 from job_runs where started_at > now() - make_interval(secs => :s) "
                    "limit 1"
                ),
                {"s": window.total_seconds()},
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
    for name in ("heartbeat", "sheet_sync"):
        scheduler.add_job(run_job, "date", run_date=now, args=[name], id=f"first_{name}")
    log.info("worker_start", jobs=sorted(REGISTRY), mode="background")
    return scheduler


def run_forever() -> None:
    scheduler = build_scheduler()
    log.info("worker_start", jobs=sorted(REGISTRY))
    try:
        reaped = reap_stale()
        if reaped:
            log.warning("job_runs_reaped", rows=reaped)
        for name in ("heartbeat", "sheet_sync"):
            run_job(name)
    except Exception:
        # The DB may be starting. The scheduler retries on its own clock.
        log.exception("worker_first_runs_failed")
    signal.signal(signal.SIGTERM, lambda *_: scheduler.shutdown(wait=False))
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("worker_stop")
