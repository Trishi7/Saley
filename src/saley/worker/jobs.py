"""The jobs: sheet_sync, metrics_snapshot, parity_check, heartbeat, sheet_push, and the R2
intelligence jobs facts_refresh, proposals_refresh, commitments_scan, mail_poll,
notes_sync, reminders_tick (docs/01 §05), the R4 jobs weekly_pack (D134), bot_plan,
daily_brief and health_line (D128, D132, D133), and the R7 job cards_plan (D259)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from saley.bot import brief, queue
from saley.bot import health as bot_health
from saley.core.clock import now_ist
from saley.core.db import session_scope
from saley.core.logging import get_logger
from saley.core.settings import get_settings
from saley.intel import drive, mail
from saley.intel.commitments import record_unreadable, scan_folder, scan_notes
from saley.intel.facts import refresh_working_on
from saley.intel.metrics import snapshot, snapshot_scorecard
from saley.intel.notes_sync import sync_drive
from saley.intel.pack import PACK_HOUR, PACK_WEEKDAY, build_weekly_pack
from saley.intel.parity import run_parity
from saley.intel.proposals import refresh_proposals
from saley.intel.reminders import run_reminders
from saley.intel.search import run_search_requests
from saley.sales.outbox import count_due, hold_pending, push_pending
from saley.sales.sheet import read_sheet
from saley.sales.sync import run_sync
from saley.sales.writer import build_write_sheet
from saley.worker import cards_plan as cards_planner
from saley.worker import chat_jobs
from saley.worker.registry import QUIET_NOTE, register

log = get_logger("saley.worker.jobs")

HEARTBEAT_MINUTES = 5
SNAPSHOT_MAX_AGE = timedelta(hours=2)
MISSED_RUN_GRACE_SECONDS = 3600
# The pack is safe to run late (one per week), so a worker that was down at 14:00 still builds it.
PACK_MISFIRE_GRACE_SECONDS = 6 * 3600
BOT_PLAN_MINUTES = 5
CARDS_PLAN_MINUTES = 5  # docs/17 §5
ANSWER_INTENT_SECONDS = 2  # the bot waits BOT_INTENT_TIMEOUT_S (10) for the intent step
# The brief and the health line are one per day, so a late start is still worth running.
BRIEF_MISFIRE_GRACE_SECONDS = 6 * 3600
HEALTH_HOUR, HEALTH_MINUTE = 23, 45  # D133
CHAT_HOUR, CHAT_MINUTE = 23, 30  # D262: before the health card, so it counts tonight's run
WEEKDAYS = "mon-fri"
PACK_CATCHUP_MINUTES = 60
SNAPSHOT_DAY_CUTOFF_HOUR = 6  # a 23:50 run that starts before 06:00 belongs to the day before


class StaleData(RuntimeError):
    """The sheet has not synced recently; derived numbers would be stale."""


class ParityMismatch(RuntimeError):
    """The dashboard and the sheet disagree. Rows are in parity_checks."""


PARITY_MINUTES = 60
FACTS_MINUTES = 10
PROPOSALS_MINUTES = 5
NOTES_MINUTES = 10
REMINDER_MINUTES = 30
SEARCH_SECONDS = 15


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
        day = snapshot_day(now_ist())
        rows = snapshot(session, day)
        industries = snapshot_scorecard(session, day)  # D118: the history behind "last week N"
    if industries == 0:
        log.warning("scorecard_snapshot_empty", day=str(day))
    return f"{rows} metric rows · {industries} scorecard rows"


def snapshot_day(moment: datetime) -> date:
    """The day a 23:50 run stands for. A late run after midnight (misfire grace) still
    stamps the evening before, so it never takes the next day's place (D118)."""
    day = moment.date()
    return day - timedelta(days=1) if moment.hour < SNAPSHOT_DAY_CUTOFF_HOUR else day


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


def sheet_push() -> str:
    """Drain the write outbox into the sheet (D45). The only job that builds a write client.

    With SHEET_WRITE_ENABLED=false it only marks pending rows `held` (Q-R2-1): no credential
    is built and the sheet is not touched. An empty outbox is a quiet run (no job_runs row).
    """
    settings = get_settings()
    moment = now_ist()
    with session_scope() as session:
        if not settings.sheet_write_enabled:
            held = hold_pending(session)
            return f"Sheet writes are off · {held} held" if held else QUIET_NOTE
        if count_due(session, moment) == 0:
            return QUIET_NOTE
    sheet = build_write_sheet()
    stats = push_pending(sheet, now=moment, email_write_allowed=settings.email_write_allowed)
    return stats.note()


def facts_refresh() -> str:
    """ "Working on" facts from the About PoC and Research Paper Link cells (R2, docs/03 §6.3)."""
    with session_scope() as session:
        return refresh_working_on(session).note()


def proposals_refresh() -> str:
    """Make Inbox cards from the synced sheet (once each). Expire cards that no longer hold."""
    with session_scope() as session:
        return refresh_proposals(session).note()


def commitments_scan() -> str:
    """Promises in the notes folder (MEETING_NOTES_DIR). A folder with no notes is a quiet run."""
    with session_scope() as session:
        stats = scan_folder(session, Path(get_settings().meeting_notes_dir))
    return QUIET_NOTE if stats.notes == 0 and stats.unreadable == 0 else stats.note()


def notes_sync() -> str:
    """Read the Drive notes folder (D81). With no folder id set the job is skipped."""
    folder_id = get_settings().meeting_notes_drive_folder_id.strip()
    if not folder_id:
        return QUIET_NOTE
    client = drive.build_client()
    with session_scope() as session:
        return sync_drive(session, client, folder_id).note()


def mail_poll() -> str:
    """Promises in the cc mailbox. With MAIL_USER empty the job is skipped (Q-R2-3)."""
    settings = get_settings()
    if not mail.configured(settings):
        return QUIET_NOTE
    notes, problems = mail.read_notes(settings)
    with session_scope() as session:
        stats = scan_notes(session, notes)
        stats.unreadable = record_unreadable(session, problems, "mail")
    return stats.note()


def reminders_tick() -> str:
    """At most one reminder before the due day and one on it, then the pack (C05)."""
    with session_scope() as session:
        stats = run_reminders(session)
    return stats.note() if stats.sent or stats.packed else QUIET_NOTE


def weekly_pack() -> str:
    """Build this week's pack and queue its two posts (D117, D130). A second run does nothing."""
    with session_scope() as session:
        return build_weekly_pack(session).note()


def weekly_pack_catchup() -> str:
    """Hourly: a worker that was down all Monday still builds this week's pack. Idle when the
    pack exists or it is before Monday 14:00. A stale sheet fails the run, as the cron does."""
    with session_scope() as session:
        stats = build_weekly_pack(session)
    return stats.note() if stats.built else QUIET_NOTE


def bot_plan() -> str:
    """Apply the bot caps to the queue: queued to ready, deferred or dropped (D132)."""
    with session_scope() as session:
        stats = queue.plan_queue(session)
    # Raise after the commit so the stale drops stay. A failed run shows in the health line.
    if stats.waiting or stats.stale:
        raise RuntimeError(
            f"bot posts not sent: {stats.waiting} ready over 30 min, {stats.stale} dropped as "
            "stale. Is the bot running?"
        )
    return stats.note() if stats.changed else QUIET_NOTE


def cards_plan() -> str:
    """One card per dashboard notification (D259). The caps decide when each goes (bot_plan)."""
    with session_scope() as session:
        stats = cards_planner.plan_cards(session)
    # A skipped producer keeps its job_runs row, so the health line can count it.
    return stats.note() if stats.queued or stats.skipped else QUIET_NOTE


def chat_summary() -> str:
    """D262: retention, then one summary per channel-day (LLM_MID, DATA blocks, checked facts)."""
    with session_scope() as session:
        return chat_jobs.run_chat_summary(session).note()


def answer_intent() -> str:
    """The intent step for @Saley answers (docs/17 §5): the bot waits at most 10 s for it."""
    with session_scope() as session:
        done = chat_jobs.run_answer_intents(session)
    return f"{done} intents" if done else QUIET_NOTE


def daily_brief() -> str:
    """Queue one DM brief per person in DM_USERS. With DM_ENABLED=false it sends nothing (D128)."""
    with session_scope() as session:
        return brief.queue_briefs(session).note()


def health_line() -> str:
    """Queue the daily health post for the test channel (D133). Weekends stay silent."""
    with session_scope() as session:
        return bot_health.queue_health_line(session).note()


def search_requests() -> str:
    """Run one "Find people" request a human asked for (R11, D77). Idle when none is waiting."""
    return run_search_requests()


def heartbeat() -> str:
    return "worker alive"


def register_all() -> None:
    """Register with triggers read from settings at start, not at import."""
    settings = get_settings()
    register("sheet_sync", "interval", requires="sync", seconds=settings.sheet_sync_seconds)(
        sheet_sync
    )
    register(
        "metrics_snapshot",
        "cron",
        hour=23,
        minute=50,
        misfire_grace_time=MISSED_RUN_GRACE_SECONDS,
    )(metrics_snapshot)
    # Parity syncs too, so it shares the sheet_sync lock: the two never write at once.
    register(
        "parity_check", "interval", lock="sheet_sync", requires="sync", minutes=PARITY_MINUTES
    )(parity_check)
    register("heartbeat", "interval", minutes=HEARTBEAT_MINUTES)(heartbeat)
    # Own lock: a push may run while a sync reads. The sync skips cells with an open row.
    register(
        "sheet_push",
        "interval",
        lock="sheet_write",
        quiet=True,
        worker_only=True,
        # Silent failure 11: a push with no credentials would fail every run. With writes off
        # it only holds rows, which needs none.
        requires="sheet_write" if settings.sheet_write_enabled else None,
        seconds=settings.sheet_push_seconds,
    )(sheet_push)
    # Intelligence jobs share the sheet_sync lock where they read what the sync writes.
    register("facts_refresh", "interval", lock="sheet_sync", minutes=FACTS_MINUTES)(facts_refresh)
    register("proposals_refresh", "interval", lock="sheet_sync", minutes=PROPOSALS_MINUTES)(
        proposals_refresh
    )
    register("commitments_scan", "interval", quiet=True, minutes=NOTES_MINUTES)(commitments_scan)
    register("mail_poll", "interval", quiet=True, minutes=settings.mail_poll_minutes)(mail_poll)
    register(
        "notes_sync",
        "interval",
        quiet=True,
        # Silent failure 11: with a folder id the job needs the key file; without one it idles.
        requires="notes_drive" if settings.meeting_notes_drive_folder_id.strip() else None,
        minutes=settings.notes_sync_minutes,
    )(notes_sync)
    # D134: Monday 14:00 IST. Idempotent on week_start, so a misfire retry is safe.
    register(
        "weekly_pack",
        "cron",
        day_of_week=PACK_WEEKDAY,
        hour=PACK_HOUR,
        minute=0,
        misfire_grace_time=PACK_MISFIRE_GRACE_SECONDS,
    )(weekly_pack)
    register(
        "weekly_pack_catchup",
        "interval",
        lock="weekly_pack",
        quiet=True,
        worker_only=True,
        minutes=PACK_CATCHUP_MINUTES,
    )(weekly_pack_catchup)
    register("reminders_tick", "interval", quiet=True, minutes=REMINDER_MINUTES)(reminders_tick)
    register("search_requests", "interval", quiet=True, seconds=SEARCH_SECONDS)(search_requests)
    register("bot_plan", "interval", lock="bot_post", quiet=True, minutes=BOT_PLAN_MINUTES)(
        bot_plan
    )
    # D259: cards_plan shares the bot_post lock with bot_plan; card ids make it idempotent.
    register("cards_plan", "interval", lock="bot_post", quiet=True, minutes=CARDS_PLAN_MINUTES)(
        cards_plan
    )
    register(
        "chat_summary",
        "cron",
        hour=CHAT_HOUR,
        minute=CHAT_MINUTE,
        misfire_grace_time=BRIEF_MISFIRE_GRACE_SECONDS,
    )(chat_summary)
    register("answer_intent", "interval", quiet=True, seconds=ANSWER_INTENT_SECONDS)(answer_intent)
    # D128: the brief goes out at DAILY_BRIEF_TIME on weekdays. It is idempotent per day.
    register(
        "daily_brief",
        "cron",
        lock="bot_post",
        day_of_week=WEEKDAYS,
        hour=settings.brief_time.hour,
        minute=settings.brief_time.minute,
        misfire_grace_time=BRIEF_MISFIRE_GRACE_SECONDS,
    )(daily_brief)
    register(
        "health_line",
        "cron",
        lock="bot_post",
        day_of_week=WEEKDAYS,
        hour=HEALTH_HOUR,
        minute=HEALTH_MINUTE,
        misfire_grace_time=BRIEF_MISFIRE_GRACE_SECONDS,
    )(health_line)
