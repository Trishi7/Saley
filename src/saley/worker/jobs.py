"""The R1 jobs: sheet_sync, metrics_snapshot, parity_check, heartbeat (docs/01 §05)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text

from saley.core.clock import now_ist
from saley.core.db import session_scope
from saley.core.settings import get_settings
from saley.intel.metrics import snapshot
from saley.intel.parity import run_parity
from saley.sales.sheet import read_sheet
from saley.sales.sync import run_sync
from saley.worker.registry import register

HEARTBEAT_MINUTES = 5
SNAPSHOT_MAX_AGE = timedelta(hours=2)
MISSED_RUN_GRACE_SECONDS = 3600


class StaleData(RuntimeError):
    """The sheet has not synced recently; derived numbers would be stale."""


class ParityMismatch(RuntimeError):
    """The dashboard and the sheet disagree. Rows are in parity_checks."""


PARITY_MINUTES = 60


def sheet_sync() -> str:
    data = read_sheet()
    with session_scope() as session:
        stats = run_sync(session, data)
    return stats.note()


def metrics_snapshot() -> str:
    """Refuse to store today's history from data older than SNAPSHOT_MAX_AGE."""
    with session_scope() as session:
        last = session.scalar(
            text("select max(finished_at) from job_runs where job = 'sheet_sync' and status = 'ok'")
        )
        if last is None or now_ist() - last > SNAPSHOT_MAX_AGE:
            raise StaleData(f"last good sheet sync: {last or 'never'}; snapshot not written")
        rows = snapshot(session)
    return f"{rows} metric rows"


def parity_check() -> str:
    """Read the sheet once, sync it, then compare. A mismatch fails the run (rows kept)."""
    data = read_sheet()
    with session_scope() as session:
        stats = run_sync(session, data)
        results = run_parity(session, data, stale=stats.stale)
    bad = [r.metric for r in results if not r.ok]
    if bad:
        raise ParityMismatch(f"{len(bad)} of {len(results)} differ: {', '.join(bad)}")
    if not results:
        raise ParityMismatch("no metric compared: every source tab is stale")
    if stats.stale:
        # Never a plain "all match" when tabs were left out (silent-failure review).
        return f"PARTIAL: {len(results)} checks match · skipped tabs: {', '.join(stats.stale)}"
    return f"{len(results)} checks · all match"


def heartbeat() -> str:
    return "worker alive"


def register_all() -> None:
    """Register with triggers read from settings at start, not at import."""
    settings = get_settings()
    register("sheet_sync", "interval", seconds=settings.sheet_sync_seconds)(sheet_sync)
    register(
        "metrics_snapshot",
        "cron",
        hour=23,
        minute=50,
        misfire_grace_time=MISSED_RUN_GRACE_SECONDS,
    )(metrics_snapshot)
    # Parity syncs too, so it shares the sheet_sync lock: the two never write at once.
    register("parity_check", "interval", lock="sheet_sync", minutes=PARITY_MINUTES)(parity_check)
    register("heartbeat", "interval", minutes=HEARTBEAT_MINUTES)(heartbeat)
