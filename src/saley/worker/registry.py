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
from saley.core.settings import get_settings
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
    # A job that runs every few seconds leaves no job_runs row when it did nothing.
    quiet: bool = False
    # D23, D46: runs only in a worker process, never beside the web in `saley dev`.
    worker_only: bool = False
    # D151: runs once at worker start (a "date" job added by the scheduler), never on a clock.
    start_only: bool = False
    # Settings names (upper case) the job needs and the process lacks (B3). Such a job is
    # disabled: never scheduled, never run. One `job_disabled` line says why.
    missing: tuple[str, ...] = ()

    @property
    def disabled(self) -> bool:
        return bool(self.missing)


class JobResult(str):
    """'ok', 'failed' or 'skipped', plus the note of THIS run in `.note`.

    A str subclass, so every `== "ok"` check still works. The command line prints the note of
    the run it started, not the newest job_runs row, which another worker may have written.
    """

    note: str | None

    def __new__(cls, status: str, note: str | None = None) -> JobResult:
        result = super().__new__(cls, status)
        result.note = note
        return result


QUIET_NOTE = "idle"
SKIPPED_NOTE = "another run holds the lock"
REGISTRY: dict[str, JobSpec] = {}


def register(
    name: str,
    trigger: str,
    *,
    lock: str | None = None,
    quiet: bool = False,
    worker_only: bool = False,
    start_only: bool = False,
    requires: str | None = None,
    **trigger_args: Any,
) -> Callable[[JobFunc], JobFunc]:
    """Register a job. `requires` names a service in SERVICE_REQUIREMENTS (`settings.require`).

    Why: a job that needs GTM_SHEET_ID must not start on a machine that has none (CI, a fresh
    clone). It would fail every run with a traceback. It is disabled at registration instead,
    with ONE plain log line (B3).
    """

    def wrap(func: JobFunc) -> JobFunc:
        missing = tuple(get_settings().missing_for(requires)) if requires else ()
        if missing:
            log.warning("job_disabled", job=name, missing=",".join(missing))
        REGISTRY[name] = JobSpec(
            name,
            func,
            trigger,
            trigger_args,
            lock or name,
            quiet=quiet,
            worker_only=worker_only,
            start_only=start_only,
            missing=missing,
        )
        return func

    return wrap


def _start_row(engine: Engine, name: str) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "insert into job_runs(job, status, started_at, slot) "
                "values (:j, 'running', :t, :slot) returning id"
            ),
            {"j": name, "t": now_ist(), "slot": get_settings().worker_slot},
        ).scalar_one()


def _finish_row(engine: Engine, run_id: int, status: str, note: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("update job_runs set status = :s, note = :n, finished_at = :t where id = :id"),
            {"s": status, "n": (note or "")[:500] or None, "t": now_ist(), "id": run_id},
        )


def run_job(name: str, engine: Engine | None = None) -> JobResult:
    """Run one registered job under its lock. Returns 'ok', 'failed' or 'skipped'."""
    engine = engine or rw_engine()
    spec = REGISTRY[name]
    if spec.disabled:  # B3: logged once at registration; no lock, no job_runs row, no run
        return JobResult("skipped", f"disabled: missing {','.join(spec.missing)}")
    slot = get_settings().worker_slot
    holder = locks.new_holder(slot)
    if not locks.acquire(engine, spec.lock, holder):
        log.info("job_skipped_locked", job=name, lock=spec.lock)
        if spec.quiet:
            return JobResult("skipped")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "insert into job_runs(job, status, started_at, finished_at, note, slot) "
                    "values (:j, 'skipped', :t, :t, 'another run holds the lock', :slot)"
                ),
                {"j": name, "t": now_ist(), "slot": slot},
            )
        return JobResult("skipped", SKIPPED_NOTE)
    run_id: int | None = None
    try:
        run_id = _start_row(engine, name)
        note = spec.func()
    except Exception as exc:
        log.exception("job_failed", job=name)
        if run_id is not None:
            _finish_safely(engine, run_id, "failed", f"{type(exc).__name__}: {exc}")
        return JobResult("failed", f"{type(exc).__name__}: {exc}"[:500])
    finally:
        try:
            locks.release(engine, spec.lock, holder)
        except Exception:  # the lock expires on its own; the run result still counts
            log.exception("job_lock_release_failed", job=name)
    if spec.quiet and note == QUIET_NOTE:
        _forget_run(engine, run_id)
        return JobResult("ok", note)
    _finish_safely(engine, run_id, "ok", note)
    log.info("job_ok", job=name, note=note)
    return JobResult("ok", note)


def _forget_run(engine: Engine, run_id: int) -> None:
    """A quiet job that did nothing leaves no row (43,000 rows a day otherwise)."""
    try:
        with engine.begin() as conn:
            conn.execute(text("delete from job_runs where id = :id"), {"id": run_id})
    except Exception:
        log.exception("job_runs_delete_failed", run_id=run_id)


def _finish_safely(engine: Engine, run_id: int, status: str, note: str | None) -> None:
    """Close the job_runs row. If even that fails, say so loudly; reap_stale() cleans up later."""
    try:
        _finish_row(engine, run_id, status, note)
    except Exception:
        log.exception("job_runs_update_failed", run_id=run_id, status=status)


def reap_stale(engine: Engine | None = None) -> int:
    """At worker start (one worker per slot): open runs of the old process of THIS slot failed.

    D113: another slot (the gate, `worker:gate`) may be alive on the same database; its runs
    and locks are real and stay.
    """
    engine = engine or rw_engine()
    slot = get_settings().worker_slot
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "update job_runs set status = 'failed', finished_at = now(), "
                "note = 'worker stopped during the run' "
                "where status = 'running' and slot = :slot"
            ),
            {"slot": slot},
        )
        # One worker per slot: locks left by this slot's previous process are dead. A holder
        # from before 0017 has no slot; it was the main worker's.
        conn.execute(
            text(
                "delete from job_locks where starts_with(holder, :p) "
                "or (:slot = 'worker:main' and position('@' in holder) = 0)"
            ),
            {"p": f"{slot}@", "slot": slot},
        )
    return result.rowcount
