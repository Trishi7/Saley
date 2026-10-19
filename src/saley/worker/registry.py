"""Job registry and runner: lock → job_runs row → run → ok/failed → release.

Why: every job run leaves a row in job_runs (top bar times, health view). A failure is
logged and stored with its error type; it never kills the scheduler.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, text

from saley.core.clock import now_ist
from saley.core.db import rw_engine
from saley.core.logging import get_logger
from saley.worker import locks

log = get_logger("saley.worker")

JobFunc = Callable[[], str | None]


@dataclass(frozen=True)
class JobSpec:
    name: str
    func: JobFunc
    trigger: str  # "interval" or "cron"
    trigger_args: dict[str, Any]
    lock: str  # jobs that write the same rows share one lock name


REGISTRY: dict[str, JobSpec] = {}


def register(
    name: str, trigger: str, *, lock: str | None = None, **trigger_args: Any
) -> Callable[[JobFunc], JobFunc]:
    def wrap(func: JobFunc) -> JobFunc:
        REGISTRY[name] = JobSpec(name, func, trigger, trigger_args, lock or name)
        return func

    return wrap


def _start_row(engine: Engine, name: str) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "insert into job_runs(job, status, started_at) "
                "values (:j, 'running', :t) returning id"
            ),
            {"j": name, "t": now_ist()},
        ).scalar_one()


def _finish_row(engine: Engine, run_id: int, status: str, note: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("update job_runs set status = :s, note = :n, finished_at = :t where id = :id"),
            {"s": status, "n": (note or "")[:500] or None, "t": now_ist(), "id": run_id},
        )


def run_job(name: str, engine: Engine | None = None) -> str:
    """Run one registered job under its lock. Returns 'ok', 'failed' or 'skipped'."""
    engine = engine or rw_engine()
    spec = REGISTRY[name]
    holder = locks.new_holder()
    if not locks.acquire(engine, spec.lock, holder):
        log.info("job_skipped_locked", job=name, lock=spec.lock)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "insert into job_runs(job, status, started_at, finished_at, note) "
                    "values (:j, 'skipped', :t, :t, 'another run holds the lock')"
                ),
                {"j": name, "t": now_ist()},
            )
        return "skipped"
    run_id: int | None = None
    try:
        run_id = _start_row(engine, name)
        note = spec.func()
    except Exception as exc:
        log.exception("job_failed", job=name)
        if run_id is not None:
            _finish_safely(engine, run_id, "failed", f"{type(exc).__name__}: {exc}")
        return "failed"
    finally:
        try:
            locks.release(engine, spec.lock, holder)
        except Exception:  # the lock expires on its own; the run result still counts
            log.exception("job_lock_release_failed", job=name)
    _finish_safely(engine, run_id, "ok", note)
    log.info("job_ok", job=name, note=note)
    return "ok"


def _finish_safely(engine: Engine, run_id: int, status: str, note: str | None) -> None:
    """Close the job_runs row. If even that fails, say so loudly; reap_stale() cleans up later."""
    try:
        _finish_row(engine, run_id, status, note)
    except Exception:
        log.exception("job_runs_update_failed", run_id=run_id, status=status)


def reap_stale(engine: Engine | None = None) -> int:
    """At worker start (one worker per deployment): open runs of the old process failed."""
    engine = engine or rw_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "update job_runs set status = 'failed', finished_at = now(), "
                "note = 'worker stopped during the run' "
                "where status = 'running'"
            )
        )
        # One worker process: locks left by the previous one are dead.
        conn.execute(text("delete from job_locks"))
    return result.rowcount
