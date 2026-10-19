"""saley.sales: column map, parsing, stage rules, sheet reader and the sync."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from saley.core.clock import now_ist
from saley.core.models import (
    Account,
    Alias,
    Contact,
    DataQuality,
    Deliverable,
    Event,
    Goal,
    Opportunity,
    Package,
    SheetTabStatus,
    StageLog,
    Touch,
)
from saley.core.settings import get_settings
from saley.sales import columns, parse
from saley.sales.sheet import SheetNotCopy, build_tabs, fetch_raw, match_tab
from saley.sales.stage import StageInputs, derive_stage
from saley.sales.sync import SheetStructureError, depends_on, owner_for, run_sync
from tests.conftest import POC_HEADERS, poc_row, serial

# ------------------------------------------------------------------ columns


def test_todays_headers_map_every_role() -> None:
    res = columns.resolve(POC_HEADERS)
    assert res.unmapped_roles == ()
    assert res.writes_allowed
    assert res.letters()["connected_with_vr"] == "Q"
    assert res.letters()["x_handle"] == "AK"


def test_moved_and_renamed_columns_still_map() -> None:
    headers = list(reversed(POC_HEADERS))
    headers[headers.index("Email id")] = "  EMAIL  "
    res = columns.resolve(headers)
    assert res.unmapped_roles == ()
    assert headers[res.columns["email"]] == "  EMAIL  "
    assert headers[res.columns["prospect_status"]] == "Status"
    assert headers[res.columns["deal_status"]] == "Deal Status"


def test_missing_restricted_role_refuses_writes() -> None:
    headers = [h for h in POC_HEADERS if h != "Deal Status"]
    res = columns.resolve(headers)
    assert "deal_status" in res.unmapped_roles
    assert not res.writes_allowed
    with pytest.raises(columns.WritesRefused, match="deal_status"):
        columns.assert_writable(res, "meeting_status")


def test_locks_are_by_role() -> None:
    res = columns.resolve(POC_HEADERS)
    assert columns.assert_writable(res, "meeting_date") == POC_HEADERS.index("Meeting Date")
    for locked in ("connected_with_vr", "email_1_sent", "deal_size", "name", "reconnect_date"):
        with pytest.raises(columns.WritesRefused, match="read-only"):
            columns.assert_writable(res, locked)


# ------------------------------------------------------------------ parsing


@pytest.mark.parametrize(
    ("raw", "expected", "problem"),
    [
        (46302, date(2026, 10, 7), None),
        ("02.09.2026", date(2026, 9, 2), parse.TEXT_DATE),
        ("7/10/2026", date(2026, 10, 7), parse.TEXT_DATE),
        ("2026-10-07", date(2026, 10, 7), parse.TEXT_DATE),
        ("7 Oct 2026", date(2026, 10, 7), parse.TEXT_DATE),
        ("", None, None),
        (None, None, None),
        ("soon", None, parse.BAD_DATE),
        (True, None, parse.BAD_DATE),
        (5, None, parse.BAD_DATE),
    ],
)
def test_parse_date(raw: Any, expected: date | None, problem: str | None) -> None:
    assert parse.parse_date(raw) == (expected, problem)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (False, False),
        ("TRUE", True),
        ("FALSE", False),
        ("VR", True),
        ("Sid", True),
        ("", False),
        ("no", False),
        ("Not sent", False),
        (None, False),
        (1, True),
        (0, False),
    ],
)
def test_is_truthy(raw: Any, expected: bool) -> None:
    assert parse.is_truthy(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("USD 30–50k", (30000, 50000)),
        ("USD 10-20k", (10000, 20000)),
        ("$1.5M", (1500000, 1500000)),
        (25000, (25000, 25000)),
        ("", (None, None)),
        ("TBD", (None, None)),
    ],
)
def test_money_band(raw: Any, expected: tuple[int | None, int | None]) -> None:
    assert parse.parse_money_band(raw) == expected


@pytest.mark.parametrize(
    ("raw", "value", "band"),
    [
        ("USD 30–50k", 40000, True),
        ("$1.5M - $2M", 1_750_000, True),
        ("USD 40,000", 40000, False),
        (25000, 25000, False),
        ("TBD", None, False),
        ("", None, False),
    ],
)
def test_deal_value_counts_the_band_midpoint(raw: Any, value: int | None, band: bool) -> None:
    """D42: a band counts its midpoint, not its upper end; exact values count as given."""
    assert (parse.deal_value(raw), parse.is_band(raw)) == (value, band)


def test_pct_and_names() -> None:
    assert parse.parse_pct(0.33) == 33
    assert parse.parse_pct("60%") == 60
    assert parse.parse_pct(45) == 45
    assert parse.norm_name("  Google   DeepMind ") == "google deepmind"
    assert parse.clean_text(12.0) == "12"


# ------------------------------------------------------------------ stage rules


@pytest.mark.parametrize(
    ("inputs", "stage"),
    [
        (StageInputs(), "Listed"),
        (StageInputs(first_contact=True, li_status="Sent"), "Contacted"),
        (StageInputs(first_contact=True, li_status="Connected"), "Connected"),
        (StageInputs(li_status="Not connected", first_contact=True), "Contacted"),
        (StageInputs(li_status="Connected", email_sent=True), "Engaged"),
        (StageInputs(li_dm_sent=True), "Engaged"),
        (StageInputs(email_sent=True, meeting_status="booked"), "Meeting"),
        (StageInputs(meeting_date=date(2026, 10, 1), prospect_status="Demo"), "Qualified"),
        # D29 (confirmed): Qualified and Meeting sit above Parked.
        (StageInputs(prospect_status="Lead", reconnect_date=date(2026, 12, 1)), "Qualified"),
        (StageInputs(meeting_status="Completed", reconnect_date=date(2026, 12, 1)), "Meeting"),
        (StageInputs(email_sent=True, reconnect_date=date(2026, 12, 1)), "Parked"),
        (StageInputs(first_contact=True, reconnect_date=date(2026, 12, 1)), "Parked"),
        (StageInputs(reconnect_date=date(2026, 12, 1), deal_status="Closed won"), "Won"),
        (StageInputs(prospect_status="Lead", deal_status="Lost"), "Lost"),
        # D31 words and Closure Prob.
        (StageInputs(deal_status="Dead"), "Lost"),
        (StageInputs(deal_status="Closed lost"), "Lost"),
        (StageInputs(prospect_status="Lead", closure_prob=0), "Lost"),
        (StageInputs(prospect_status="Quote sent"), "Qualified"),
        (StageInputs(prospect_status="Deal"), "Qualified"),
        (StageInputs(meeting_status="Completed", closure_prob=50), "Qualified"),
        (StageInputs(meeting_status="Completed", closure_prob=49), "Meeting"),
        (StageInputs(deal_status="In progress", meeting_status="TBD"), "Meeting"),
    ],
)
def test_derive_stage(inputs: StageInputs, stage: str) -> None:
    assert derive_stage(inputs)[0] == stage


# ------------------------------------------------------------------ reader


def test_tabs_found_by_alias() -> None:
    assert match_tab("Outreach PoCs").kind == "poc"
    assert match_tab("Sept2026_Sales Strategy").kind == "strategy"
    assert match_tab("Q4-OND2026-Goal Setting").kind == "goals"
    assert match_tab("Random notes") is None


def test_header_row_can_be_below_a_title_row(raw_sheet: dict[str, list[list[Any]]]) -> None:
    raw_sheet["Outreach PoCs"].insert(0, ["Outreach list, updated weekly"])
    data = build_tabs(raw_sheet)
    assert data["poc"].header_row == 2
    assert data["poc"].rows[0][0] == 3


def test_reader_refuses_live_sheet(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setenv("GTM_SHEET_IS_COPY", "false")
    get_settings.cache_clear()
    try:
        with pytest.raises(SheetNotCopy):
            fetch_raw(lambda: pytest.fail("must not open the sheet"))
    finally:
        get_settings.cache_clear()


# ------------------------------------------------------------------ sync


def _sync(db: Session, raw: dict[str, list[list[Any]]], when: datetime | None = None) -> Any:
    stats = run_sync(db, build_tabs(raw), when or now_ist())
    db.commit()
    return stats


def _contact(db: Session, name: str) -> Contact:
    return db.scalars(select(Contact).where(Contact.name == name)).one()


def test_first_sync_builds_entities(db: Session, raw_sheet: dict[str, list[list[Any]]]) -> None:
    stats = _sync(db, raw_sheet)
    assert db.scalar(select(func.count()).select_from(Contact)) == 7
    assert db.scalar(select(func.count()).select_from(Goal)) == 5
    assert stats.stage_changes == 7
    assert _contact(db, "Dinesh Tewari").stage == "Connected"
    assert _contact(db, "Sshubam Verma").stage == "Engaged"
    assert _contact(db, "David Gamez").stage == "Qualified"
    assert _contact(db, "Pat Park").stage == "Parked"
    assert _contact(db, "Ro One").stage == "Listed"
    goal = db.scalars(select(Goal).where(Goal.code == "G1")).one()
    assert (goal.value_min, goal.value_max, goal.need) == (30000, 50000, 3)


def test_sync_is_idempotent(db: Session, raw_sheet: dict[str, list[list[Any]]]) -> None:
    _sync(db, raw_sheet)
    again = _sync(db, raw_sheet)
    assert (again.inserted, again.changed, again.touches, again.stage_changes) == (0, 0, 0, 0)
    assert db.scalar(select(func.count()).select_from(StageLog)) == 7


def test_company_names_never_merge(db: Session, raw_sheet: dict[str, list[list[Any]]]) -> None:
    _sync(db, raw_sheet)
    names = set(db.scalars(select(Account.name)))
    assert {"Google", "Google DeepMind", "Rosebud AI", "Rosebud wellness"} <= names
    assert _contact(db, "Alex Search").account_id != _contact(db, "Dinesh Tewari").account_id


def test_approved_alias_maps_but_never_google_to_deepmind(
    db: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    _sync(db, raw_sheet)
    sarvam = db.scalars(select(Account).where(Account.name == "Sarvam AI")).one()
    deepmind = db.scalars(select(Account).where(Account.name == "Google DeepMind")).one()
    db.add_all(
        [
            Alias(account_id=sarvam.id, alias="sarvam", approved_by="Vaishnavi"),
            Alias(account_id=deepmind.id, alias="google", approved_by="someone"),
        ]
    )
    db.commit()
    raw_sheet["Outreach PoCs"].append(poc_row(sr_no=8, company="Sarvam", name="New Person"))
    _sync(db, raw_sheet)
    assert _contact(db, "New Person").account_id == sarvam.id
    assert _contact(db, "Alex Search").account_id != deepmind.id
    problems = set(db.scalars(select(DataQuality.problem)))
    assert "alias_never_merge" in problems


def test_master_pipeline_accounts(db: Session, raw_sheet: dict[str, list[list[Any]]]) -> None:
    _sync(db, raw_sheet)
    ai4b = db.scalars(select(Account).where(Account.name == "AI4Bharat")).one()
    assert ai4b.in_master and ai4b.industry == "AI Labs"
    google = db.scalars(select(Account).where(Account.name == "Google")).one()
    assert not google.in_master


def test_changed_step_cell_becomes_a_touch_and_moves_stage(
    db: Session, raw_sheet: dict[str, list[list[Any]]], sheet_today: date
) -> None:
    _sync(db, raw_sheet)
    rows = raw_sheet["Outreach PoCs"]
    row = rows[1]
    row[POC_HEADERS.index("1st Email Sent")] = True
    row[POC_HEADERS.index("1st Email Date")] = serial(sheet_today)
    stats = _sync(db, raw_sheet)
    dinesh = _contact(db, "Dinesh Tewari")
    assert stats.touches == 1
    assert dinesh.stage == "Engaged"
    touch = db.scalars(
        select(Touch).where(Touch.contact_id == dinesh.id, Touch.type == "email1")
    ).one()
    assert touch.date == sheet_today
    history = db.scalars(
        select(StageLog.stage).where(StageLog.contact_id == dinesh.id).order_by(StageLog.id)
    ).all()
    assert history == ["Connected", "Engaged"]


def test_new_sr_no_with_same_name_and_company_is_the_same_person(
    db: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    _sync(db, raw_sheet)
    before = _contact(db, "Dinesh Tewari")
    raw_sheet["Outreach PoCs"][1][0] = 101
    stats = _sync(db, raw_sheet)
    after = _contact(db, "Dinesh Tewari")
    assert after.id == before.id
    assert after.sheet_row_key == "Outreach PoCs:101:dinesh tewari"
    assert stats.inserted == 0


def test_removed_row_is_marked_not_deleted(
    db: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    _sync(db, raw_sheet)
    raw_sheet["Outreach PoCs"].pop()  # Pat Park
    stats = _sync(db, raw_sheet)
    assert stats.removed >= 1
    assert _contact(db, "Pat Park").removed_at is not None


def test_dates_and_data_quality(db: Session, raw_sheet: dict[str, list[list[Any]]]) -> None:
    _sync(db, raw_sheet)
    assert _contact(db, "Alex Search").first_contact_date == date(2026, 9, 2)
    problems = {(d.row_key, d.problem) for d in db.scalars(select(DataQuality))}
    assert ("Outreach PoCs:2:alex search", "text_date") in problems
    assert ("Outreach PoCs:6:ro two", "bad_date") in problems
    raw_sheet["Outreach PoCs"][6][POC_HEADERS.index("First Contact Date")] = ""
    _sync(db, raw_sheet)
    bad = db.scalars(
        select(DataQuality).where(
            DataQuality.problem == "bad_date", DataQuality.row_key == "Outreach PoCs:6:ro two"
        )
    ).one()
    assert bad.resolved_at is not None


def test_duplicate_row_is_flagged(db: Session, raw_sheet: dict[str, list[list[Any]]]) -> None:
    raw_sheet["Outreach PoCs"].append(list(raw_sheet["Outreach PoCs"][1]))
    _sync(db, raw_sheet)
    assert db.scalar(select(func.count()).select_from(Contact)) == 7
    assert "duplicate_row" in set(db.scalars(select(DataQuality.problem)))


def test_owner_rule_d31(monkeypatch: pytest.MonkeyPatch) -> None:
    assert owner_for("VR", None, None) == ("vaishnavi", "first_contact")
    assert owner_for("Sid", None, None) == ("sid", "first_contact")
    assert owner_for("TRUE", "Sent", None) == ("sid", "li_addition")
    assert owner_for("TRUE", None, "LI") == ("sid", "first_contact_type")
    assert owner_for("TRUE", None, "Email") == ("vaishnavi", "default")
    assert owner_for(None, None, None) == ("vaishnavi", "default")


def test_opportunity_packages_events_deliverables(
    db: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    _sync(db, raw_sheet)
    opp = db.scalars(select(Opportunity)).one()
    assert (opp.prob, opp.size_usd, opp.status) == (60, 30000, "Lead >50%")
    pkg = db.scalars(select(Package).where(Package.name == "Hinglish STT training set")).one()
    assert (pkg.pct, pkg.ready) == (33, False)
    event = db.scalars(select(Event)).one()
    assert (event.start, event.end) == (date(2026, 10, 20), date(2026, 10, 21))
    nda = db.scalars(select(Deliverable).where(Deliverable.item == "NDA")).one()
    goals = {g.id: g.code for g in db.scalars(select(Goal))}
    assert sorted(goals[i] for i in nda.blocks_goal_ids) == ["G1", "G2", "G3"]
    overview = db.scalars(select(Deliverable).where(Deliverable.item.like("Pulse%"))).one()
    assert sorted(goals[i] for i in overview.blocks_goal_ids) == ["G1", "G4"]
    assert len(overview.blocks_strategy_ids) == 1


def test_depends_on_matching() -> None:
    assert depends_on("NDA", "NDA, MSA, Overview doc")
    assert depends_on("Pulse Product Overview Document", "NDA, MSA, Overview doc")
    assert not depends_on("MSA", "Legal compliances")
    assert not depends_on("Brochure", None)


def test_week_flags_use_first_contact_date(
    db: Session, raw_sheet: dict[str, list[list[Any]]], sheet_today: date
) -> None:
    _sync(db, raw_sheet)
    touch = db.scalars(
        select(Touch)
        .join(Contact)
        .where(Contact.name == "Dinesh Tewari", Touch.type == "first_contact")
    ).one()
    assert touch.date == sheet_today - timedelta(days=sheet_today.weekday())


# ---------------------------------------- review fixes: parsing and stage words


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Not contacted", False),
        ("pending", False),
        ("TBD", False),
        ("To do", False),
        ("VR", True),
        ("Sid", True),
        ("Sent", True),
    ],
)
def test_truthy_respects_negative_words(raw: str, expected: bool) -> None:
    assert parse.is_truthy(raw) is expected


@pytest.mark.parametrize(
    ("deal", "status", "stage"),
    [
        ("Unsigned", None, "Listed"),
        ("Not won", None, "Listed"),
        ("Closed won", None, "Won"),
        (None, "Not a lead", "Listed"),
        (None, "Lead >50%", "Qualified"),
        ("Lost", None, "Lost"),
    ],
)
def test_stage_words_are_whole_and_not_negated(
    deal: str | None, status: str | None, stage: str
) -> None:
    assert derive_stage(StageInputs(deal_status=deal, prospect_status=status))[0] == stage


def test_numbers_too_big_for_the_db_become_empty() -> None:
    assert parse.parse_int("9876543210") is None
    assert parse.parse_money_band("call 9876543210") == (None, None)
    assert parse.parse_money_band("$1.5M - $2M") == (1_500_000, 2_000_000)
    assert parse.parse_money_band("Q4 2026-27") == (None, None)
    assert parse.parse_date(float("nan")) == (None, parse.BAD_DATE)
    assert parse.parse_pct(1) == 100  # a percent cell stores 100 % as 1


# ---------------------------------------- review fixes: sync guards


def test_robust_renamed_required_header_keeps_last_good_data(
    db: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    """D41: a renamed required header stops that tab only; its last good rows stay."""
    _sync(db, raw_sheet)
    raw_sheet["Outreach PoCs"][0] = ["Person" if h == "Name" else h for h in POC_HEADERS]
    raw_sheet["Deliverables Checklist"][1][1] = "NDA v2"  # another tab still syncs
    stats = _sync(db, raw_sheet)
    assert stats.stale == ["poc"]
    live = select(func.count()).select_from(Contact).where(Contact.removed_at.is_(None))
    assert db.scalar(live) == 7
    assert "NDA v2" in set(db.scalars(select(Deliverable.item)))
    status = db.get(SheetTabStatus, "poc")
    assert (status.status, status.missing_headers) == ("stale", ["name"])
    assert status.stale_since is not None
    assert stats.note().startswith("PARTIAL: poc stale")


def test_mass_removal_is_refused(db: Session, raw_sheet: dict[str, list[list[Any]]]) -> None:
    for i in range(8, 20):
        raw_sheet["Outreach PoCs"].append(poc_row(sr_no=i, company="Bulk Co", name=f"Person {i}"))
    run_sync(db, build_tabs(raw_sheet), now_ist())
    db.commit()
    raw_sheet["Outreach PoCs"] = raw_sheet["Outreach PoCs"][:3]
    with pytest.raises(SheetStructureError, match="would be removed"):
        run_sync(db, build_tabs(raw_sheet), now_ist())
    db.rollback()


def test_account_leaving_master_is_no_longer_in_master(
    db: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    run_sync(db, build_tabs(raw_sheet), now_ist())
    db.commit()
    raw_sheet["Master Pipeline"] = [r for r in raw_sheet["Master Pipeline"] if r[0] != "Sarvam AI"]
    run_sync(db, build_tabs(raw_sheet), now_ist())
    db.commit()
    sarvam = db.scalars(select(Account).where(Account.name == "Sarvam AI")).one()
    assert (sarvam.in_master, sarvam.removed_at) == (False, None)  # a PoC still points to it


def test_not_done_deliverable_stays_late(
    db: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    raw_sheet["Deliverables Checklist"][3][7] = "Not done"  # Brochure
    run_sync(db, build_tabs(raw_sheet), now_ist())
    db.commit()
    late = set(db.scalars(text("select item from v_deliverable_late")))
    assert "Brochure" in late


# ---------------------------------------- review fixes: silent failures and real-sheet shapes


def test_headers_with_extra_words_map_by_prefix() -> None:
    from saley.sales.sheet import TAB_BY_KIND

    goal_roles = TAB_BY_KIND["goals"].roles
    res = columns.resolve(
        ["", "Goal Setting", "Q4 Target (by 31-Dec-2026)", "Total Leads (>50% confidence)"],
        goal_roles,
    )
    assert {"target", "total_leads", "text"} <= set(res.columns)
    master = columns.resolve(
        ["Company", "Dates", "Approx. Funding (till date, 17th Aug'26)"],
        TAB_BY_KIND["master"].roles,
    )
    assert "funding" in master.columns and master.columns.get("sr_no") is None


def test_couldnt_find_is_not_a_linkedin_request() -> None:
    from saley.sales.stage import li_requested

    assert not li_requested("Couldn't Find")
    assert li_requested("Sent")


def test_error_cells_are_never_true() -> None:
    assert not parse.is_truthy("#REF!")
    assert not parse.is_truthy("#N/A")
    assert parse.is_error_cell("#VALUE!")


def test_month_first_event_range() -> None:
    from saley.sales.sync import parse_event_dates

    start, end, _ = parse_event_dates("October 20–21,2026", date(2026, 10, 18))
    assert (start, end) == (date(2026, 10, 20), date(2026, 10, 21))


def test_robust_missing_stage_column_makes_the_tab_stale(
    db: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    raw_sheet["Outreach PoCs"][0] = ["Deal state" if h == "Deal Status" else h for h in POC_HEADERS]
    stats = _sync(db, raw_sheet)
    assert stats.stale == ["poc"]
    assert db.scalar(select(func.count()).select_from(Contact)) == 0  # never half a tab
    assert db.get(SheetTabStatus, "poc").missing_headers == ["deal_status"]


def test_small_tab_mass_removal_is_refused(
    db: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    from saley.sales.sync import SheetStructureError

    for i in range(10):
        raw_sheet["AI Events & Summits"].append(
            [100 + i, "Paris", f"Event {i}", "", "", "", "", "", "", ""]
        )
    _sync(db, raw_sheet)
    raw_sheet["AI Events & Summits"] = raw_sheet["AI Events & Summits"][:2]
    with pytest.raises(SheetStructureError, match="would be removed"):
        run_sync(db, build_tabs(raw_sheet), now_ist())
    db.rollback()


def test_no_deadline_and_duplicate_goal_are_flagged(
    db: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    raw_sheet["Deliverables Checklist"].append(
        [9, "Pitch deck", "Design", "P1", "", "", "", "In progress", "", ""]
    )
    raw_sheet["Q4-OND2026-Goal Setting"].insert(2, list(raw_sheet["Q4-OND2026-Goal Setting"][1]))
    _sync(db, raw_sheet)
    problems = {(d.row_key.split(":")[0], d.problem) for d in db.scalars(select(DataQuality))}
    assert ("Deliverables Checklist", "no_deadline") in problems
    assert ("Goal Setting", "duplicate_row") in problems
    codes = [g.code for g in db.scalars(select(Goal).order_by(Goal.position))]
    assert codes == ["G1", "G2", "G3", "G4", "G5"]


def test_goal_need_reads_only_a_leading_number() -> None:
    from saley.sales.sync import leading_int

    assert leading_int("1 deal closed") == 1
    assert leading_int(">50% closure probability with at least one lab") is None
    assert leading_int("50% of target") is None


def test_legal_compliances_means_items_owned_by_legal() -> None:
    deps = "Legal compliances, API readiness, Pulse Product Overview Doc"
    assert depends_on("NDA - to be prepared and vetted by legal", deps, "Legal")
    assert not depends_on("External Dashboard - UI/UX", deps, "Design")
    assert depends_on("Pulse Product Overview Document", deps, "Design")


def test_not_available_is_an_empty_date() -> None:
    assert parse.parse_date("not available") == (None, None)


# ---------------------------------------- D29–D35 confirmed (Trishi, 18 Oct)


@pytest.mark.parametrize(
    ("raw", "expected"), [(">50%", 50), ("<50%", 49), ("0%", 0), (0.6, 60), ("", None)]
)
def test_closure_prob(raw: Any, expected: int | None) -> None:
    assert parse.parse_prob(raw) == expected


def test_goal_map_overrides_the_text_rule(
    db: Session,
    raw_sheet: dict[str, list[list[Any]]],
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from saley.sales import goalmap, sync

    path = tmp_path / "map.yaml"
    path.write_text("goals:\n  G5:\n    - Brochure\n    - Not a real item\n", encoding="utf-8")
    monkeypatch.setattr(sync, "load_goal_map", lambda: goalmap.load_goal_map(path))
    _sync(db, raw_sheet)
    goals = {g.id: g.code for g in db.scalars(select(Goal))}
    brochure = db.scalars(select(Deliverable).where(Deliverable.item == "Brochure")).one()
    nda = db.scalars(select(Deliverable).where(Deliverable.item == "NDA")).one()
    assert [goals[i] for i in brochure.blocks_goal_ids] == ["G5"]
    assert "G5" not in {goals[i] for i in nda.blocks_goal_ids}  # G5 listed: text rule off
    assert {goals[i] for i in nda.blocks_goal_ids} == {"G1", "G2", "G3"}  # others keep it
    problems = {(d.row_key, d.problem) for d in db.scalars(select(DataQuality))}
    assert ("G5", "map_unknown_item") in problems


def test_goal_map_file_errors_are_loud(tmp_path: Any) -> None:
    from saley.sales import goalmap

    bad = tmp_path / "bad.yaml"
    bad.write_text("goals:\n  G1: NDA\n", encoding="utf-8")
    with pytest.raises(goalmap.GoalMapError):
        goalmap.load_goal_map(bad)
    assert goalmap.load_goal_map(tmp_path / "missing.yaml") == {}
    assert goalmap.load_goal_map(goalmap.MAP_FILE) == {}  # the shipped file has no overrides


def test_david_gamez_case_is_qualified() -> None:
    meeting_lead_reconnect = StageInputs(
        first_contact=True,
        li_status="Connected",
        meeting_date=date(2026, 9, 1),
        meeting_status="Completed",
        prospect_status="Lead",
        closure_prob=50,
        reconnect_date=date(2026, 10, 30),
    )
    assert derive_stage(meeting_lead_reconnect)[0] == "Qualified"


@pytest.mark.parametrize(
    ("first_contact", "li_status", "fc_type", "expected"),
    [
        ("Inside sales", "No", None, ("vaishnavi", "default")),
        ("TRUE", "Not sent", "Calling", ("vaishnavi", "default")),
        ("TRUE", "Couldn't Find", "LI", ("sid", "first_contact_type")),
        ("VR", "Sent", "LI", ("vaishnavi", "first_contact")),
    ],
)
def test_owner_rule_edge_cases(
    first_contact: str, li_status: str, fc_type: str | None, expected: tuple[str, str]
) -> None:
    assert owner_for(first_contact, li_status, fc_type) == expected


@pytest.mark.parametrize(
    ("deal", "stage"),
    [("Won't proceed", "Listed"), ("Not closed won", "Listed"), ("Closed won", "Won")],
)
def test_won_words_respect_negation(deal: str, stage: str) -> None:
    assert derive_stage(StageInputs(deal_status=deal))[0] == stage


@pytest.mark.parametrize(("raw", "expected"), [("<1%", 1), ("<=50%", 50)])
def test_closure_prob_edges(raw: str, expected: int) -> None:
    assert parse.parse_prob(raw) == expected


def test_goal_map_full_names_and_unknown_goal(tmp_path: Any) -> None:
    from saley.sales import goalmap

    path = tmp_path / "map.yaml"
    path.write_text("goals:\n  g4:\n    - External Dashboard - Demo for PoCs\n", encoding="utf-8")
    assert goalmap.load_goal_map(path) == {"G4": {"external dashboard - demo for pocs"}}


# ---------------------------------------- R1.1 sheet robustness (gate step 4: -k robust)


def _contacts(db: Session) -> dict[str, Contact]:
    return {c.name: c for c in db.scalars(select(Contact).where(Contact.removed_at.is_(None)))}


def test_robust_inserted_column_changes_nothing(
    db: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    first = _sync(db, raw_sheet)
    before = {n: (c.stage, c.email, c.title) for n, c in _contacts(db).items()}
    for row in raw_sheet["Outreach PoCs"]:
        row.insert(3, "Team notes" if row is raw_sheet["Outreach PoCs"][0] else "x")
    again = _sync(db, raw_sheet)
    assert {n: (c.stage, c.email, c.title) for n, c in _contacts(db).items()} == before
    assert (again.changed, again.inserted, again.removed) == (0, 0, 0)
    assert db.get(SheetTabStatus, "poc").new_headers == ["Team notes"]
    assert again.sync_id != first.sync_id  # the sheet content changed


def test_robust_renamed_optional_header_is_listed_as_new(
    db: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    _sync(db, raw_sheet)
    raw_sheet["Outreach PoCs"][0] = [
        "Tweet handle" if h == "X handle (or website)" else h for h in POC_HEADERS
    ]
    stats = _sync(db, raw_sheet)
    assert stats.stale == []
    status = db.get(SheetTabStatus, "poc")
    assert status.status == "ok" and status.new_headers == ["Tweet handle"]


def test_robust_deleted_row_removes_one(db: Session, raw_sheet: dict[str, list[list[Any]]]) -> None:
    _sync(db, raw_sheet)
    del raw_sheet["Outreach PoCs"][5]  # Ro One
    _sync(db, raw_sheet)
    people = _contacts(db)
    assert len(people) == 6 and "Ro One" not in people


def test_robust_moved_row_keeps_its_key(db: Session, raw_sheet: dict[str, list[list[Any]]]) -> None:
    _sync(db, raw_sheet)
    before = _contacts(db)["Dinesh Tewari"]
    rows = raw_sheet["Outreach PoCs"]
    rows.append(rows.pop(1))  # move Dinesh to the bottom
    stats = _sync(db, raw_sheet)
    after = _contacts(db)["Dinesh Tewari"]
    assert after.id == before.id
    assert after.sheet_row_key == "Outreach PoCs:1:dinesh tewari"
    assert (stats.inserted, stats.removed, stats.stage_changes) == (0, 0, 0)


def test_robust_value_changed_mid_sync_uses_the_snapshot_read(
    db: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    """The sync writes exactly what it read; an edit after the read lands in the next sync."""
    data = build_tabs(raw_sheet)
    raw_sheet["Outreach PoCs"][1][POC_HEADERS.index("Designation")] = "Changed after the read"
    first = run_sync(db, data, now_ist())
    db.commit()
    assert _contacts(db)["Dinesh Tewari"].title == "Research Program Manager"
    second = _sync(db, raw_sheet)
    assert _contacts(db)["Dinesh Tewari"].title == "Changed after the read"
    assert second.sync_id > first.sync_id


def test_robust_identical_sheet_keeps_the_sync_id(
    db: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    first = _sync(db, raw_sheet)
    second = _sync(db, raw_sheet)
    assert first.sync_id == second.sync_id is not None


def test_robust_master_stale_freezes_people_and_accounts(
    db: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    _sync(db, raw_sheet)
    accounts_before = {a.name: (a.in_master, a.removed_at) for a in db.scalars(select(Account))}
    raw_sheet["Master Pipeline"][0] = ["Firm", *raw_sheet["Master Pipeline"][0][1:]]
    raw_sheet["Outreach PoCs"] = raw_sheet["Outreach PoCs"][:3]  # would remove people
    stats = _sync(db, raw_sheet)
    assert stats.stale == ["master", "poc"]
    assert db.get(SheetTabStatus, "poc").reason == "waits for Master Pipeline"
    assert len(_contacts(db)) == 7
    assert {a.name: (a.in_master, a.removed_at) for a in db.scalars(select(Account))} == (
        accounts_before
    )


def test_robust_missing_tab_is_stale_not_a_crash(
    db: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    del raw_sheet["AI Events & Summits"]
    stats = _sync(db, raw_sheet)
    assert stats.stale == ["events"]
    assert db.get(SheetTabStatus, "events").reason == "tab not found"


def test_robust_all_core_tabs_stale_fails(
    db: Session, raw_sheet: dict[str, list[list[Any]]]
) -> None:
    from saley.sales.sync import SheetStructureError

    for title in (
        "Outreach PoCs",
        "Master Pipeline",
        "Q4-OND2026-Goal Setting",
        "Deliverables Checklist",
    ):
        del raw_sheet[title]
    with pytest.raises(SheetStructureError, match="GTM_SHEET_ID"):
        run_sync(db, build_tabs(raw_sheet), now_ist())
    db.rollback()


def test_robust_known_unused_headers_are_not_new(raw_sheet: dict[str, list[list[Any]]]) -> None:
    raw_sheet["Master Pipeline"][0] = raw_sheet["Master Pipeline"][0] + ["Dates", "Brand new"]
    tab = build_tabs(raw_sheet)["master"]
    assert tab.new_headers == ["Brand new"]


def test_exact_tab_title_wins_over_a_lookalike(raw_sheet: dict) -> None:
    """A tab named like the real one but placed first must not become the source."""
    from saley.sales.sheet import build_tabs

    copy = {"Outreach PoCs copy": [["Name"], ["Ghost"]], **raw_sheet}
    data = build_tabs(copy)
    assert data["poc"].title == "Outreach PoCs"


def test_findings_of_a_stale_tab_stay_open(db: Session, raw_sheet: dict) -> None:
    from saley.core.clock import now_ist
    from saley.sales.sheet import build_tabs
    from saley.sales.sync import run_sync

    deliverables = raw_sheet["Deliverables Checklist"]
    run_sync(db, build_tabs(raw_sheet), now_ist())
    db.commit()
    db.execute(
        text(
            "insert into data_quality(tab, row_key, column_role, problem, first_seen, last_seen) "
            "values ('Deliverables Checklist', 'k1', 'deadline', 'no_deadline', "
            "now() - interval '1 day', now() - interval '1 day')"
        )
    )
    db.commit()
    before = 1
    header = next(r for r in deliverables if "Action Item" in r)
    header[header.index("Action Item")] = "Renamed"
    run_sync(db, build_tabs(raw_sheet), now_ist())
    db.commit()
    after = db.execute(
        text(
            "select count(*) from data_quality where tab = 'Deliverables Checklist' "
            "and resolved_at is null"
        )
    ).scalar_one()
    assert before > 0 and after == before
