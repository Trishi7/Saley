"""Daily metric snapshots (S30, docs/06 §7). Runs at 23:50 IST and on `saley metrics --once`.

Why: the weekly pack and scorecard deltas need history. A snapshot is one row per
(day, scope, metric), upserted, so a re-run on the same day replaces the values.
13 of the 15 metrics come from the sheet. Reply rate is stored as null until mail exists
(D5). The two agent metrics (proposal accept rate, actions closed on time) need proposals,
which arrive in R2, so they are not stored yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from saley.core.clock import today_ist, week_start
from saley.core.logging import get_logger
from saley.core.models import MetricSnapshot

log = get_logger("saley.intel.metrics")

ALL = ("all", 0)
SKIPPED = ("proposal_accept_rate", "actions_closed_on_time")


@dataclass(frozen=True)
class Metric:
    scope_type: str
    scope_id: int
    metric: str
    value: Decimal | float | int | None


def _team_metrics(session: Session, day: date) -> list[Metric]:
    start = week_start(day)
    row = (
        session.execute(
            text("""
        select
          (select count(*) from touch t join contact c on c.id = t.contact_id
            where c.removed_at is null and t.type = 'first_contact'
              and t.date between :start and :day) as first_contacts_week,
          (select count(distinct c.account_id) from touch t join contact c on c.id = t.contact_id
            where c.removed_at is null and t.type = 'first_contact'
              and t.date between :start and :day) as orgs_touched_week,
          (select case when count(*) filter (where li_requested) > 0
                  then round(100.0 * count(*) filter (where connected)
                             / count(*) filter (where li_requested), 1) end
             from v_contact_stage) as connect_rate_pct,
          (select case when count(*) filter (where contacted) > 0
                  then round(100.0 * count(*) filter (where meeting_date is not null
                                                     or coalesce(meeting_status, '') <> '')
                             / count(*) filter (where contacted), 2) end
             from v_contact_stage) as meetings_per_100,
          (select percentile_cont(0.5) within group (order by email1_date - li_connected_date)
             from v_contact_stage
            where email1_date is not null and li_connected_date is not null
              and email1_date >= li_connected_date) as follow_up_lag_days
        """),
            {"start": start, "day": day},
        )
        .mappings()
        .one()
    )
    metrics = [Metric(*ALL, name, value) for name, value in row.items()]
    metrics.append(Metric(*ALL, "reply_rate_pct", None))
    return metrics


def _goal_metrics(session: Session) -> list[Metric]:
    rows = session.execute(
        text("select id, active_count, weighted_usd, days_left, blocker_count from v_goal_status")
    ).mappings()
    out = []
    for r in rows:
        out += [
            Metric("goal", r["id"], "active_conversations", r["active_count"]),
            Metric("goal", r["id"], "weighted_pipeline_usd", r["weighted_usd"]),
            Metric("goal", r["id"], "days_to_due", r["days_left"]),
            Metric("goal", r["id"], "blocking_deliverables_open", r["blocker_count"]),
        ]
    return out


def _account_metrics(session: Session) -> list[Metric]:
    """Only accounts with at least one contacted person, to keep ~100 rows a day."""
    rows = session.execute(
        text(
            "select id, coverage_pct, saley_stage_rank(warmth) as warmth_rank "
            "from v_account_coverage where contacted > 0"
        )
    ).mappings()
    out = []
    for r in rows:
        out += [
            Metric("account", r["id"], "coverage_pct", r["coverage_pct"]),
            Metric("account", r["id"], "warmth_rank", r["warmth_rank"]),
        ]
    return out


def _event_metrics(session: Session) -> list[Metric]:
    """Meetings within ±2 days of the event, with people in the event city."""
    rows = session.execute(
        text("""
        select e.id,
               count(c.id) filter (
                 where c.meeting_date between e.start - 2 and coalesce(e."end", e.start) + 2
                   and e.city is not null and strpos(lower(c.city), lower(e.city)) > 0
               ) as meetings
        from event e left join v_contact_stage c on true
        where e.removed_at is null and e.start is not null
        group by e.id
    """)
    ).mappings()
    return [Metric("event", r["id"], "meetings_booked", r["meetings"]) for r in rows]


def compute(session: Session, day: date) -> list[Metric]:
    return (
        _team_metrics(session, day)
        + _goal_metrics(session)
        + _account_metrics(session)
        + _event_metrics(session)
    )


def snapshot(session: Session, day: date | None = None) -> int:
    """Compute and upsert today's metrics. Returns the number of rows written."""
    day = day or today_ist()
    metrics = compute(session, day)
    for m in metrics:
        stmt = insert(MetricSnapshot).values(
            day=day, scope_type=m.scope_type, scope_id=m.scope_id, metric=m.metric, value=m.value
        )
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["day", "scope_type", "scope_id", "metric"],
                set_={"value": stmt.excluded.value},
            )
        )
    log.info("metrics_snapshot", day=str(day), rows=len(metrics), skipped=list(SKIPPED))
    return len(metrics)
