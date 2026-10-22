"""News service: APScheduler runs the editions in this process only (D70).

Why: the container and its compose entry exist from R1. R3 gives it work: four editions a day,
the arXiv and OpenAlex batches and the nightly clean. The production worker never fetches
news. D151 adds one dev exception (`news.jobs.register_dev_news`: the main-slot dev worker,
not the gate). One bounded live fetch is the separate `saley news fetch`.
Times are IST. Each job runs under its lock and leaves a job_runs row (worker.registry.run_job).
"""

from __future__ import annotations

import signal

from apscheduler.schedulers.blocking import BlockingScheduler

from saley.core.clock import tz
from saley.core.logging import get_logger
from saley.news.jobs import register_news
from saley.worker.registry import REGISTRY, run_job

log = get_logger("saley.news")


def build_scheduler() -> BlockingScheduler:
    """All news jobs on one blocking scheduler, in IST."""
    names = register_news()
    scheduler = BlockingScheduler(
        timezone=tz(), job_defaults={"coalesce": True, "max_instances": 1}
    )
    for name in names:
        spec = REGISTRY[name]
        scheduler.add_job(run_job, spec.trigger, args=[name], id=name, **spec.trigger_args)
    return scheduler


def run_forever() -> None:
    """Run until stopped."""
    from saley.core.settings import set_process
    from saley.worker.scheduler import add_alive

    set_process("news")
    scheduler = build_scheduler()
    add_alive(scheduler, "news")
    log.info("news_start", jobs=sorted(job.id for job in scheduler.get_jobs()))
    signal.signal(signal.SIGTERM, lambda *_: scheduler.shutdown(wait=False))
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("news_stop")
