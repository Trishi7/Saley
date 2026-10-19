"""saley.intel: metric snapshots and the parity check."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from saley.core.models import MetricSnapshot, ParityCheck
from saley.intel.metrics import snapshot
from saley.intel.parity import run_parity, sheet_counts, view_counts
from saley.sales.sheet import build_tabs


def _metric(db: Session, name: str, scope: str = "all") -> Any:
    return db.scalars(
        select(MetricSnapshot.value).where(
            MetricSnapshot.metric == name, MetricSnapshot.scope_type == scope
        )
    ).all()


def test_snapshot_writes_team_goal_account_event_rows(synced: Session, sheet_today: date) -> None:
    written = snapshot(synced, sheet_today)
    synced.commit()
    assert written == synced.scalar(select(func.count()).select_from(MetricSnapshot))
    assert _metric(synced, "first_contacts_week") == [1]
    assert _metric(synced, "reply_rate_pct") == [None]
    # connected 3 of LI requested 4 (Sent + 3 Connected)
    assert float(_metric(synced, "connect_rate_pct")[0]) == 75.0
    assert len(_metric(synced, "days_to_due", "goal")) == 5
    assert _metric(synced, "meetings_booked", "event") == [0]
    assert _metric(synced, "coverage_pct", "account")


def test_snapshot_rerun_same_day_replaces(synced: Session, sheet_today: date) -> None:
    first = snapshot(synced, sheet_today)
    synced.commit()
    second = snapshot(synced, sheet_today)
    synced.commit()
    assert first == second == synced.scalar(select(func.count()).select_from(MetricSnapshot))


def test_parity_matches_after_sync(
    synced: Session, raw_sheet: dict[str, list[list[Any]]], sheet_today: date
) -> None:
    results = run_parity(synced, build_tabs(raw_sheet), sheet_today)
    synced.commit()
    assert all(r.ok for r in results), [r for r in results if not r.ok]
    counts = {r.metric: r.sheet_value for r in results}
    assert counts["people_total"] == 7
    assert counts["late_deliverables"] == 2
    assert counts["packages_not_ready"] == 1
    assert synced.scalar(select(func.count()).select_from(ParityCheck)) == len(results)


def test_parity_detects_a_mismatch(
    synced: Session, raw_sheet: dict[str, list[list[Any]]], sheet_today: date
) -> None:
    synced.execute(text("update contact set removed_at = now() where name = 'Ro One'"))
    synced.commit()
    results = {r.metric: r for r in run_parity(synced, build_tabs(raw_sheet), sheet_today)}
    assert not results["people_total"].ok
    assert results["people_total"].sheet_value == 7
    assert results["people_total"].view_value == 6


def test_sheet_counts_are_independent_of_the_db(
    raw_sheet: dict[str, list[list[Any]]], sheet_today: date
) -> None:
    counts = sheet_counts(build_tabs(raw_sheet), sheet_today)
    assert counts["stage:Connected"] == 1
    assert counts["p1_total"] == 2


def test_view_counts_on_empty_db(db: Session) -> None:
    assert view_counts(db)["people_total"] == 0


def test_parity_skips_metrics_of_a_stale_tab(
    db: Session, raw_sheet: dict[str, list[list[Any]]], sheet_today: date
) -> None:
    from saley.core.clock import now_ist
    from saley.sales.sync import run_sync

    run_sync(db, build_tabs(raw_sheet), now_ist())
    db.commit()
    raw_sheet["Outreach PoCs"][0] = [
        "Person" if h == "Name" else h for h in raw_sheet["Outreach PoCs"][0]
    ]
    raw_sheet["Outreach PoCs"].pop()  # the sheet now differs, but poc is stale
    data = build_tabs(raw_sheet)
    stats = run_sync(db, data, now_ist())
    results = run_parity(db, data, sheet_today, stale=stats.stale)
    db.commit()
    assert all(r.ok for r in results)
    assert not any(r.metric == "people_total" for r in results)
    notes = set(db.scalars(select(ParityCheck.note).where(ParityCheck.metric == "tab:poc")))
    assert notes == {"skipped: tab stale"}
