"""News jobs for the `saley news` process (D70), plus one narrow dev exception (D151).

Why: the worker registry runs the sheet jobs. News jobs register here, in the same registry
type, so locks and job_runs rows work the same way. `register_news()` is never called by the
worker, `saley dev` or the gate: only `saley news` schedules the editions.
Old cause of "No news job has run yet." on the laptop: `saley dev` starts web and worker only.
D151 amends D70 with `register_dev_news()`: under SALEY_ENV=dev and slot `worker:main` the worker
also has `news_requests` (Run news now), `news_topic_backfill`, and one edition at start. The
gate slot and any other env never get them. Nothing here is on a clock in the worker.
Times are IST (the scheduler runs in SALEY_TZ): editions from NEWS_EDITIONS, arXiv 10:00,
OpenAlex 07:30, cleanup 03:00 (docs/plans/R3.md §3).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import text

from saley.core.clock import now_ist
from saley.core.db import session_scope
from saley.core.logging import get_logger
from saley.core.settings import Settings, get_settings
from saley.news.editions import run_edition
from saley.news.sources import FAMILY_ARXIV, FAMILY_NEWS, FAMILY_OPENALEX, short_error
from saley.news.store import cleanup
from saley.news.topic import backfill_topics
from saley.worker.registry import QUIET_NOTE, REGISTRY, register

log = get_logger("saley.news.jobs")

ARXIV_SLOT = "10:00"
OPENALEX_SLOT = "07:30"
CLEANUP_SLOT = "03:00"
TOPIC_BACKFILL_MINUTES = 10  # idle once every story has a topic
NEWS_LOCK = "news_edition"
REQUESTS_JOB = "news_requests"
START_JOB = "news_start"
BACKFILL_JOB = "news_topic_backfill"
REQUEST_SECONDS = 30
START_MAX_AGE = timedelta(hours=6)  # D151: the start-up edition runs when the last is older
START_SLOT, MANUAL_SLOT = "start", "manual"  # edition labels; free text in news_editions.slot
MAIN_SLOT = "worker:main"


def _hour_minute(slot: str) -> tuple[int, int]:
    hour, minute = slot.split(":")
    return int(hour), int(minute)


def _run_checked(slot: str, family: str) -> str:
    """One edition. A run where every source failed is a failed job, not a quiet success."""
    report = run_edition(slot, family)
    if report.sources and len(report.failed) == len(report.sources):
        raise RuntimeError(f"every source failed: {report.note()}")
    return report.note()


def _edition_job(slot: str, family: str) -> Callable[[], str]:
    def job() -> str:
        return _run_checked(slot, family)

    job.__name__ = f"news_{family}_{slot.replace(':', '')}"
    return job


def news_cleanup() -> str:
    with session_scope() as session:
        removed = cleanup(session, now_ist())
    return " · ".join(f"{table} {n}" for table, n in removed.items())


def news_topic_backfill() -> str:
    """Give every story without a topic one (R5 T7, D156). Idle when none lacks one.

    Quiet and idempotent: it reads only rows with a null topic, so a second run does nothing and
    leaves no job_runs row. It fetches nothing. The rules live in saley.news.topic only.
    """
    with session_scope() as session:
        done = backfill_topics(session)
    return f"{done} stories" if done else QUIET_NOTE


def news_start() -> str:
    """D151: one edition at dev worker start when the newest one is over 6 h old (or none).

    Failed editions do not count, so a restart after a failure tries again. It runs through
    `run_job` under the `news_edition` lock, like a scheduled edition. Idle (no job_runs row)
    when the last edition is fresh.
    """
    with session_scope() as session:
        last = session.scalar(
            text(
                "select max(started_at) from news_editions where family = :f and status <> 'failed'"
            ),
            {"f": FAMILY_NEWS},
        )
    if last is not None and now_ist() - last < START_MAX_AGE:
        return QUIET_NOTE
    return _run_checked(START_SLOT, FAMILY_NEWS)


def _claim_request() -> int | None:
    """Take the oldest queued "Run news now" row and mark it running (D150, D151).

    `FOR SKIP LOCKED` means two processes never take the same row. The unique index on the open
    state already allows one open request, so this is a second guard.
    """
    with session_scope() as session:
        # This job holds the news_edition lock (run_job), so no edition of ours runs now: a row
        # still `running` was left by a worker that stopped. Close it, so the button is free.
        session.execute(
            text(
                "update news_run_request set state = 'failed', finished_at = now(), "
                "note = 'worker stopped' where state = 'running'"
            )
        )
        found = session.execute(
            text(
                "select id from news_run_request where state = 'queued' "
                "order by id limit 1 for update skip locked"
            )
        ).first()
        if found is None:
            return None
        session.execute(
            text("update news_run_request set state = 'running', started_at = now() where id = :i"),
            {"i": found[0]},
        )
        return int(found[0])


def _finish_request(request_id: int, state: str, note: str) -> None:
    """Close our own running row only. A row closed meanwhile (stale cleanup) keeps its state."""
    with session_scope() as session:
        result = session.execute(
            text(
                "update news_run_request set state = :s, finished_at = now(), note = :n "
                "where id = :i and state = 'running'"
            ),
            {"s": state, "n": note[:500], "i": request_id},
        )
    if not result.rowcount:
        log.warning("news_request_closed_before_finish", request_id=request_id, state=state)


def news_requests() -> str:
    """Run the edition an admin asked for (Run news now). Idle when no request waits.

    The job holds the `news_edition` lock (run_job), so it never runs beside a scheduled
    edition: a busy lock leaves the row queued and the next tick tries again. A failure marks
    the row failed with a short note and is raised, so job_runs shows it too.
    """
    request_id = _claim_request()
    if request_id is None:
        return QUIET_NOTE
    try:
        note = _run_checked(MANUAL_SLOT, FAMILY_NEWS)
    except Exception as exc:
        try:
            _finish_request(request_id, "failed", short_error(exc))
        except Exception:
            # Keep the edition's own error: it is the one job_runs must show.
            log.exception("news_request_finish_failed", request_id=request_id)
        raise
    _finish_request(request_id, "done", note)
    return f"request {request_id}: {note}"


def dev_news_enabled(settings: Settings | None = None) -> bool:
    """D151: the dev worker of the main slot only. Never the gate slot, never another env."""
    settings = settings or get_settings()
    return settings.is_dev and settings.worker_slot == MAIN_SLOT


def register_dev_news() -> list[str]:
    """The D151 exception to D70. Returns the names it added (none outside dev + main slot)."""
    if not dev_news_enabled():
        return []
    register(REQUESTS_JOB, "interval", lock=NEWS_LOCK, quiet=True, seconds=REQUEST_SECONDS)(
        news_requests
    )
    register(BACKFILL_JOB, "interval", quiet=True, minutes=TOPIC_BACKFILL_MINUTES)(
        news_topic_backfill
    )
    register(START_JOB, "interval", lock=NEWS_LOCK, quiet=True, start_only=True, minutes=1)(
        news_start
    )
    return [REQUESTS_JOB, BACKFILL_JOB, START_JOB]


def start_job_names() -> list[str]:
    """The jobs the dev worker queues once at start (D151). Empty when the exception is off."""
    settings = get_settings()
    if not (dev_news_enabled(settings) and settings.news_run_at_start):
        return []
    return [START_JOB, BACKFILL_JOB]


def register_news() -> list[str]:
    """Add the news jobs to the registry. Returns their names."""
    names = []
    for slot in get_settings().edition_slots:
        hour, minute = _hour_minute(slot)
        name = f"news_edition_{hour:02d}{minute:02d}"
        register(name, "cron", lock=NEWS_LOCK, hour=hour, minute=minute)(
            _edition_job(slot, FAMILY_NEWS)
        )
        names.append(name)
    for name, slot, family in (
        ("news_arxiv", ARXIV_SLOT, FAMILY_ARXIV),
        ("news_openalex", OPENALEX_SLOT, FAMILY_OPENALEX),
    ):
        hour, minute = _hour_minute(slot)
        register(name, "cron", lock=NEWS_LOCK, hour=hour, minute=minute)(_edition_job(slot, family))
        names.append(name)
    hour, minute = _hour_minute(CLEANUP_SLOT)
    register("news_cleanup", "cron", lock="news_cleanup", hour=hour, minute=minute)(news_cleanup)
    names.append("news_cleanup")
    register(BACKFILL_JOB, "interval", quiet=True, minutes=TOPIC_BACKFILL_MINUTES)(
        news_topic_backfill
    )
    names.append(BACKFILL_JOB)
    register(REQUESTS_JOB, "interval", lock=NEWS_LOCK, quiet=True, seconds=REQUEST_SECONDS)(
        news_requests
    )
    names.append(REQUESTS_JOB)
    return names


def news_job_names() -> list[str]:
    return sorted(name for name in REGISTRY if name.startswith("news_"))
