"""Hourly parity check: dashboard numbers vs the same numbers recounted from the sheet copy.

Why: the dashboard must never show a number the sheet does not support (dashboard-parity
skill, docs/08 "Parity"). The sheet side is recounted here from raw cells with its own small
rules, not with the SQL views, so a bug in the sync or a view shows up as a mismatch.
Stage counts reuse saley.sales.stage.derive_stage: they check the sync and the views, not
the stage rules themselves (tests/test_sales.py covers those).
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.core.clock import today_ist
from saley.core.logging import get_logger
from saley.core.models import ParityCheck
from saley.sales.parse import clean_text, is_truthy, norm_name, parse_date, parse_prob
from saley.sales.sheet import SheetData, TabData
from saley.sales.stage import StageInputs, derive_stage
from saley.sales.sync import is_done

log = get_logger("saley.intel.parity")


@dataclass(frozen=True)
class ParityResult:
    metric: str
    sheet_value: int | None
    view_value: int | None

    @property
    def ok(self) -> bool:
        return self.sheet_value is not None and self.sheet_value == self.view_value


def _connected(status: Any) -> bool:
    low = (clean_text(status) or "").casefold()
    return low.startswith("connected") or low.startswith("accepted")


def _people(tab: TabData) -> list[list[Any]]:
    """Unique named rows by (Sr No, name), as the sync keys them."""
    seen, rows = set(), []
    for _, row in tab.rows:
        name = clean_text(tab.cell(row, "name"))
        key = (clean_text(tab.cell(row, "sr_no")), norm_name(name))
        if name and key not in seen:
            seen.add(key)
            rows.append(row)
    return rows


def sheet_counts(
    data: SheetData, today: date, skip: frozenset[str] = frozenset()
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    poc = data.get("poc")
    people = _people(poc) if poc is not None and "poc" not in skip else []
    for row in people:
        assert poc is not None

        def cell(role: str, r: list[Any] = row, tab: TabData = poc) -> Any:
            return tab.cell(r, role)

        connected = _connected(cell("sid_li_added"))
        emails = [
            is_truthy(cell(f"email_{i}_sent")) or parse_date(cell(f"email_{i}_date"))[0] is not None
            for i in (1, 2, 3)
        ]
        dm = is_truthy(cell("li_dm_sent")) or parse_date(cell("li_dm_date"))[0] is not None
        meeting_date = parse_date(cell("meeting_date"))[0]
        meeting = meeting_date is not None or bool(clean_text(cell("meeting_status")))
        contacted = is_truthy(cell("first_contact")) or connected
        counts["people_total"] += 1
        counts["contacted"] += contacted
        counts["connected"] += connected
        counts["emailed"] += any(emails) or dm
        counts["meetings"] += meeting
        counts["p1_total"] += (clean_text(cell("poc_priority")) or "").upper().startswith("P1")
        stage, _ = derive_stage(
            StageInputs(
                first_contact=contacted,
                li_status=clean_text(cell("sid_li_added")),
                email_sent=any(emails),
                li_dm_sent=dm,
                meeting_date=meeting_date,
                meeting_status=clean_text(cell("meeting_status")),
                prospect_status=clean_text(cell("prospect_status")),
                deal_status=clean_text(cell("deal_status")),
                closure_prob=parse_prob(cell("closure_prob")),
                reconnect_date=parse_date(cell("reconnect_date"))[0],
            )
        )
        counts[f"stage:{stage}"] += 1

    deliverables = data.get("deliverables")
    rows = deliverables.rows if deliverables is not None and "deliverables" not in skip else []
    for _, row in rows:
        assert deliverables is not None
        if not clean_text(deliverables.cell(row, "item")):
            continue
        due = parse_date(deliverables.cell(row, "deadline"))[0]
        status = (clean_text(deliverables.cell(row, "status")) or "").casefold()
        done = parse_date(deliverables.cell(row, "done"))[0]
        late = due is not None and due < today and done is None and not is_done(status)
        counts["late_deliverables"] += late

    packages = data.get("packages")
    if packages is not None and "packages" not in skip:
        for _, row in packages.rows:
            label = clean_text(packages.cell(row, "package")) or clean_text(
                packages.cell(row, "name")
            )
            ready = clean_text(packages.cell(row, "ready"))
            not_ready = ready is not None and ready.casefold() in {
                "no",
                "false",
                "n",
                "not started",
            }
            counts["packages_not_ready"] += bool(label) and not_ready
    return dict(counts)


def view_counts(session: Session) -> dict[str, int]:
    row = (
        session.execute(
            text("""
        select count(*) as people_total,
               count(*) filter (where contacted) as contacted,
               count(*) filter (where connected) as connected,
               count(*) filter (where emailed) as emailed,
               count(*) filter (where meeting_date is not null
                                or coalesce(meeting_status, '') <> '') as meetings,
               count(*) filter (where is_p1) as p1_total
        from v_contact_stage
    """)
        )
        .mappings()
        .one()
    )
    counts = dict(row)
    for stage, n in session.execute(
        text("select stage, count(*) from v_contact_stage group by stage")
    ):
        counts[f"stage:{stage}"] = n
    counts["late_deliverables"] = session.scalar(text("select count(*) from v_deliverable_late"))
    counts["packages_not_ready"] = session.scalar(text("select count(*) from v_package_not_ready"))
    return {k: int(v or 0) for k, v in counts.items()}


def compare(sheet: dict[str, int], views: dict[str, int]) -> list[ParityResult]:
    """One result per metric. A metric missing on one side is a mismatch (None), never a 0.

    Exception: a stage with no people is absent from a GROUP BY, so a missing `stage:` key
    is a true 0.
    """
    names = sorted(set(sheet) | set(views))

    def side(values: dict[str, int], name: str) -> int | None:
        return values.get(name, 0 if name.startswith("stage:") else None)

    return [ParityResult(n, side(sheet, n), side(views, n)) for n in names]


def metric_tab(metric: str) -> str:
    """Which sheet tab a parity metric comes from."""
    return {"late_deliverables": "deliverables", "packages_not_ready": "packages"}.get(
        metric, "poc"
    )


def run_parity(
    session: Session, data: SheetData, today: date | None = None, stale: Iterable[str] = ()
) -> list[ParityResult]:
    """Compare and store one row per metric in parity_checks.

    Metrics of a stale tab are stored as skipped: the dashboard keeps that tab's last good
    data on purpose (D41), so a difference there is expected, not a mismatch.
    """
    skip = frozenset(stale)
    all_results = compare(sheet_counts(data, today or today_ist(), skip), view_counts(session))
    results = [r for r in all_results if metric_tab(r.metric) not in skip]
    run_id = uuid.uuid4().hex[:12]
    for r in results:
        session.add(
            ParityCheck(
                run_id=run_id,
                metric=r.metric,
                sheet_value=r.sheet_value,
                view_value=r.view_value,
                ok=r.ok,
                note=None if r.ok else "mismatch",
            )
        )
    for tab in sorted(skip & {"poc", "deliverables", "packages"}):
        session.add(
            ParityCheck(run_id=run_id, metric=f"tab:{tab}", ok=True, note="skipped: tab stale")
        )
    bad = [r.metric for r in results if not r.ok]
    if bad:
        log.warning("parity_mismatch", run_id=run_id, metrics=bad)
    else:
        log.info("parity_ok", run_id=run_id, metrics=len(results), skipped=sorted(skip))
    return results
