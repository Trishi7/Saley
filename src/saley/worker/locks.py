"""DB locks per job name with a 15-minute expiry (docs/02 Part H "Availability").

Why: two worker processes must never run the same job at once, and a crashed worker must
not block a job forever. The lock row is taken with one atomic upsert that only succeeds
when no row exists or the old lock has expired.
"""

from __future__ import annotations

import os
import socket
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import Engine, text

from saley.core.logging import get_logger

log = get_logger("saley.worker")

LOCK_EXPIRY = timedelta(minutes=15)
# D246: a running job renews its lock this often, so a news edition that runs longer than the
# expiry keeps it and a second run cannot start beside it.
RENEW_EVERY = timedelta(minutes=5)


def new_holder(slot: str) -> str:
    """`<slot>@host:pid:id`. Lock names stay global, so two slots never run one job twice;
    the slot in the holder lets a starting worker free only its own dead locks (D113)."""
    return f"{slot}@{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def acquire(engine: Engine, name: str, holder: str, expiry: timedelta = LOCK_EXPIRY) -> bool:
    """True when this holder now owns the lock. Times come from the DB clock, never the host."""
    with engine.begin() as conn:
        got = conn.execute(
            text(
                "insert into job_locks(name, holder, acquired_at, expires_at) "
                "values (:n, :h, now(), now() + make_interval(secs => :s)) "
                "on conflict (name) do update set holder = excluded.holder, "
                "acquired_at = excluded.acquired_at, expires_at = excluded.expires_at "
                "where job_locks.expires_at < now() returning name"
            ),
            {"n": name, "h": holder, "s": expiry.total_seconds()},
        ).first()
    return got is not None


def renew(engine: Engine, name: str, holder: str, expiry: timedelta = LOCK_EXPIRY) -> bool:
    """Push the expiry out for a long job. False when the lock was lost."""
    with engine.begin() as conn:
        got = conn.execute(
            text(
                "update job_locks set expires_at = now() + make_interval(secs => :s) "
                "where name = :n and holder = :h returning name"
            ),
            {"n": name, "h": holder, "s": expiry.total_seconds()},
        ).first()
    return got is not None


def release(engine: Engine, name: str, holder: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("delete from job_locks where name = :n and holder = :h"), {"n": name, "h": holder}
        )


@contextmanager
def kept(
    engine: Engine, name: str, holder: str, every: timedelta = RENEW_EVERY
) -> Iterator[threading.Event]:
    """Renew the lock in a background thread while the block runs (D246, the lock heartbeat).

    Yields an Event that is set when a renewal found the lock gone (it expired, or another
    holder took it): `job_lock_lost` is logged once, and the job's result still counts. A
    renewal that fails on a database error is logged and tried again at the next beat.
    """
    stop, lost = threading.Event(), threading.Event()

    def beat() -> None:
        while not stop.wait(every.total_seconds()):
            try:
                if not renew(engine, name, holder):
                    lost.set()
                    log.error("job_lock_lost", lock=name)
                    return
            except Exception:  # the next beat tries again; the expiry is 3 beats away
                log.exception("job_lock_renew_failed", lock=name)

    thread = threading.Thread(target=beat, name=f"lock-{name}", daemon=True)
    thread.start()
    try:
        yield lost
    finally:
        stop.set()
        thread.join(timeout=5)
