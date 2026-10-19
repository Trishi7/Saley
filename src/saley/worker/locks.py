"""DB locks per job name with a 15-minute expiry (docs/02 Part H "Availability").

Why: two worker processes must never run the same job at once, and a crashed worker must
not block a job forever. The lock row is taken with one atomic upsert that only succeeds
when no row exists or the old lock has expired.
"""

from __future__ import annotations

import os
import socket
import uuid
from datetime import timedelta

from sqlalchemy import Engine, text

LOCK_EXPIRY = timedelta(minutes=15)


def new_holder() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


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
