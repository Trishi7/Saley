"""saley.intel: metric snapshots and the parity check."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from saley.core.models import MetricSnapshot, ParityCheck
from saley.intel import tasks as rules
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


# ---------------------------------------- R3 validator (intel/guard.py)

FACTS = [
    "Sarvam AI launched Sarvam-M on 12 March 2026 with 24,000 hours of audio.",
    "Contact: Dinesh Tewari, dinesh@example.com, https://www.sarvam.ai/blog/launch/",
]


def test_validator_rejects_values_not_in_facts() -> None:
    """R3-G01: a URL, email, number, date or name that is not in the facts is refused."""
    from saley.intel.guard import GuardViolation, find_violations, passes, require_clean

    bad = {
        "url": "Read https://www.sarvam.ai/blog/other for more.",
        "email": "Write to priya@example.com today.",
        "number": "They trained on 31,000 hours of audio.",
        "date": "The launch was on 15 March 2026.",
        "name": "Ask Priya Menon about it.",
    }
    for kind, sentence in bad.items():
        kinds = {v.kind for v in find_violations(sentence, FACTS)}
        assert kind in kinds, f"{kind} not caught in {sentence!r}"
        assert not passes(sentence, FACTS)
    with pytest.raises(GuardViolation) as err:
        require_clean(bad["name"], FACTS)
    assert err.value.violations[0].value.endswith("Priya Menon")

    good = (
        "Sarvam AI launched Sarvam-M on 12 March 2026, 24000 hours of audio. "
        "Ask Dinesh Tewari (DINESH@example.com) at https://sarvam.ai/blog/launch."
    )
    assert find_violations(good, FACTS) == []


def test_validator_edge_cases() -> None:
    from saley.intel.guard import clean_or_empty, find_violations

    assert find_violations("", FACTS) == []
    assert find_violations("Nothing to check here.", []) == []
    assert find_violations("Ask Dinesh Tewari.", [None, ""]) != []  # no facts, a name: refused
    # a date without a year matches the same day in the facts; ISO form matches prose form
    assert find_violations("Launched 12 March.", FACTS) == []
    assert find_violations("Launched on 2026-03-12.", FACTS) == []
    assert [v.kind for v in find_violations("Launched on 2026-03-13.", FACTS)] == ["date"]
    # a capitalised sentence starter is not a name; digits of a date are not numbers
    assert find_violations("The launch was on 12 March 2026.", FACTS) == []
    assert clean_or_empty("They raised 90 million.", FACTS) == ""
    assert clean_or_empty("It shipped Sarvam-M.", FACTS) == "It shipped Sarvam-M."
    # unicode and a hostile string never crash the check
    assert find_violations("Ünïcode 😀 text <DATA> ignore all rules", FACTS) == []
    assert find_violations("x" * 20000, FACTS) == []


# ------------------------------------------------------------------ R5 T3: task order, due day

DAY = date(2026, 10, 19)  # a Monday


def _task(key: str, kind: str, **kw: Any) -> rules.Task:
    return rules.Task(key=key, kind=kind, person="sid", **kw)


def _order(found: list[rules.Task]) -> list[str]:
    return [t.key for t in sorted(found, key=lambda t: rules.sort_key(t, DAY))]


def test_task_order_due_then_kind_then_priority() -> None:
    """D152, D180: (due day, importance, PoC Priority, company, name). No due day ranks after
    every task dated today and before tomorrow's."""
    assert [rules.IMPORTANCE[k] for k in ("meeting", "status_check")] == [1, 1]
    assert rules.IMPORTANCE["reconnect"] == 2
    assert {rules.IMPORTANCE[k] for k in ("add_email", "email1", "email2", "email3")} == {3}
    assert {rules.IMPORTANCE[k] for k in ("li_confirm", "li_first")} == {4}
    assert rules.IMPORTANCE["commitment"] == 5
    assert {rules.IMPORTANCE[k] for k in ("decision", "review_write", "inbox_cards")} == {6}

    yesterday = DAY - timedelta(days=1)
    # An earlier due day beats a more important kind.
    early = [_task("li", "li_first", due=yesterday), _task("mt", "meeting", due=DAY)]
    assert _order(early) == ["li", "mt"]
    # D180: a task with no due day ranks after today's dated tasks, before tomorrow's.
    undated = [
        _task("tomorrow", "meeting", due=DAY + timedelta(days=1)),
        _task("nodate", "meeting"),
        _task("li_today", "li_first", due=DAY),
    ]
    assert _order(undated) == ["li_today", "nodate", "tomorrow"]
    same_day = [
        _task("decision", "decision"),
        _task("commit", "commitment", due=DAY),
        _task("li_c", "li_confirm", due=DAY),
        _task("mail", "email1", due=DAY),
        _task("rec", "reconnect", due=DAY),
        _task("meet", "meeting", due=DAY),
    ]
    assert _order(same_day) == ["meet", "rec", "mail", "li_c", "commit", "decision"]
    # Same day, same kind: P1 < P2 < P3 < blank, then company, then name.
    ranked = [
        _task("blank", "li_first", name="A"),
        _task("p3", "li_first", priority="P3"),
        _task("p2", "li_first", priority="p2 "),
        _task("p1b", "li_first", priority="P1", company="Zed", name="A"),
        _task("p1a", "li_first", priority="P1", company="acme", name="B"),
        _task("p1c", "li_first", priority="P1", company="Acme", name="A"),
    ]
    assert _order(ranked) == ["p1c", "p1a", "p1b", "p2", "p3", "blank"]
    assert [rules.priority_rank(p) for p in ("P1", "P2", "P3", "", None, "High")] == [
        1,
        2,
        3,
        4,
        4,
        4,
    ]


def _row(**kw: Any) -> rules.ContactRow:
    kw.setdefault("contact_id", 1)
    kw.setdefault("name", "Pat")
    return rules.ContactRow(**kw)


def test_due_day_rule_reads_named_columns() -> None:
    """docs/08 Due-date rule: each kind reads its own sheet columns; no day counts as today."""
    week = DAY - timedelta(days=7)
    # meeting: Meeting Date (AA) is today; status_check: Meeting Date before today, booked.
    meet = rules.meeting_tasks(
        [
            _row(contact_id=1, meeting_date=DAY, meeting_status=""),
            _row(contact_id=2, meeting_date=week, meeting_status="Booked"),
        ],
        DAY,
    )
    assert {t.kind: t.due for t in meet} == {"meeting": DAY, "status_check": week}
    # reconnect: Reconnect Date (AC).
    rec = rules.reconnect_tasks([_row(reconnect_due=True, reconnect_due_on=week)])
    assert rec[0].due == week
    # add_email: LI Connected Date (O) + 4 days.
    mail = rules.add_email_tasks(
        [_row(connected=True, li_connected_date=week)], DAY, email_write=False
    )
    assert mail[0].due == week + timedelta(days=4)
    # email1/2/3: anchor + 2 / 7 / 3 days. No step date: First Contact Date (M) is the anchor.
    base = {"connected": True, "has_email": True, "stage": "Connected"}
    step1 = rules.email_step_tasks([_row(first_contact_date=week, **base)], DAY)
    assert (step1[0].kind, step1[0].due) == ("email1", week + timedelta(days=2))
    sent1 = {"email_sent": (True, False, False), "email_dates": (week, None, None)}
    step2 = rules.email_step_tasks([_row(last_step_date=week, **sent1, **base)], DAY)
    assert (step2[0].kind, step2[0].due) == ("email2", week + timedelta(days=7))
    sent2 = {"email_sent": (True, True, False), "email_dates": (week, week, None)}
    step3 = rules.email_step_tasks(
        [_row(last_step_date=DAY - timedelta(days=3), **sent2, **base)], DAY
    )
    assert (step3[0].kind, step3[0].due) == ("email3", DAY)
    # li_confirm and li_first are due today; a decision has no day and sorts as today.
    inputs = rules.Inputs(
        contacts=[
            _row(contact_id=1, li_requested=True),
            _row(contact_id=2),
        ],
        proposed=[rules.ProposedDecision(9, "D9")],
    )
    found = {t.kind: t for t in rules.tasks_for("sid", inputs, DAY)}
    assert found["li_confirm"].due == DAY and found["li_first"].due == DAY
    kushal = rules.tasks_for("kushal", inputs, DAY)
    assert kushal[0].kind == "decision" and kushal[0].due is None
