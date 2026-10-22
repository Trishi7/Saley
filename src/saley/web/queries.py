"""Read-only SQL for the dashboard. Every function reads views with the saley_ro role.

Why: docs/08 — one view per endpoint, filters and search on top. Filters are a closed
list; free text goes in a bound ILIKE parameter with its wildcards escaped, so input can
never change the SQL. Every row carries its sheet row key as evidence (S38).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, text

from saley.core.logging import scrub_text
from saley.sales.stage import STAGES

MAX_Q = 100
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000

PEOPLE_FILTERS = {
    "all": "true",
    "backlog": "is_backlog",
    "p1_waiting": "is_p1_waiting",
    "in_conversation": "in_conversation",
    "reconnect_due": "reconnect_due",
    "contacted_this_week": "contacted_this_week",
    "contacted_last_week": "contacted_last_week",
    "p1": "is_p1",
    # R5 T5: the Today funnel bars. "Reached" counts a person who is at this stage or past it.
    "reached_contacted": "stage_rank >= 2",
    "reached_connected": "stage_rank >= 3",
    "reached_talking": "stage_rank >= 4",
    "reached_met": "stage_rank >= 5",
}
ACCOUNT_FILTERS = {
    "all": "true",
    "p1": "p1_count > 0",
    "has_connection": "connected > 0",
}
DECISIONS_FILE = Path(__file__).resolve().parents[3] / "docs" / "DECISIONS.md"


class BadFilter(ValueError):
    """The filter is not one of the known names."""


def like(q: str | None) -> str | None:
    """User text → safe ILIKE pattern. None when there is nothing to search."""
    if q is None:
        return None
    q = q.strip()[:MAX_Q]
    if not q:
        return None
    return "%" + re.sub(r"([\\%_])", r"\\\1", q) + "%"


def rows(conn: Connection, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def last_job(conn: Connection, job: str) -> datetime | None:
    return conn.execute(
        text("select max(finished_at) from v_job_runs where job = :j and status = 'ok'"), {"j": job}
    ).scalar()


def last_any_job(conn: Connection) -> datetime | None:
    return conn.execute(
        text("select max(finished_at) from v_job_runs where status = 'ok'")
    ).scalar()


def last_started(conn: Connection, job: str) -> datetime | None:
    """When the job last started, ok or not: the worker's sign of life."""
    return conn.execute(
        text("select max(started_at) from v_job_runs where job = :j"), {"j": job}
    ).scalar()


def tab_states(conn: Connection) -> tuple[list[str], set[str]]:
    """(titles of stale tabs, kinds never read once). Both mean the numbers may be old or empty."""
    found = rows(conn, "select tab, title, status, last_good_sync_id from sheet_tab_status")
    stale = sorted(r["title"] or r["tab"] for r in found if r["status"] != "ok")
    unread = {r["tab"] for r in found if r["last_good_sync_id"] is None}
    return stale, unread


def current_sync_id(conn: Connection) -> int | None:
    """The snapshot this request reads (D40)."""
    return conn.execute(text("select max(id) from sheet_snapshot")).scalar()


def _evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in items:
        item["evidence"] = {"row": item.pop("sheet_row_key", None)}
    return items


# ------------------------------------------------------------------ today


def today_numbers(conn: Connection) -> dict[str, Any]:
    return dict(conn.execute(text("select * from v_today_user")).mappings().one())


# ------------------------------------------------------------------ people


def people_where(filter_name: str) -> tuple[str, dict[str, Any]]:
    if filter_name in PEOPLE_FILTERS:
        return PEOPLE_FILTERS[filter_name], {}
    if filter_name.startswith("stage:") and filter_name[6:] in STAGES:
        return "stage = :f_stage", {"f_stage": filter_name[6:]}
    if re.fullmatch(r"account:\d{1,9}", filter_name):
        return "account_id = :f_account", {"f_account": int(filter_name[8:])}
    raise BadFilter(filter_name)


# The person drawer shows these (D39: the real email and links). Never the raw cells.
PERSON_FIELDS = (
    "id, sheet_row_key, name, title, account_id, company, industry, stage, owner, owner_source, "
    "priority, is_p1, city, email, has_email, next_step, li_status, li_url, paper_url, about, "
    "notes, x_handle, connected_with_vr, first_contact_date, li_connected_date, meeting_date, "
    "meeting_status, reconnect_due_on"
)

PEOPLE_SEARCH = (
    "(name ilike :q escape '\\' or company ilike :q escape '\\' or title ilike :q escape '\\' "
    "or city ilike :q escape '\\' or coalesce(industry, '') ilike :q escape '\\')"
)


def people(
    conn: Connection, filter_name: str, q: str | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    where, params = people_where(filter_name)
    pattern = like(q)
    if pattern:
        where += " and " + PEOPLE_SEARCH
        params["q"] = pattern
    total = conn.execute(
        text(f"select count(*) from v_contact_stage where {where}"), params
    ).scalar_one()
    items = rows(
        conn,
        f"""
        select p.id, p.sheet_row_key, p.name, p.company, p.account_id, p.title, p.stage, p.owner,
               p.priority, p.is_p1, p.city, p.has_email, p.email_status, p.next_step, p.about,
               p.industry, p.li_status, p.reconnect_due_on,
               (select jsonb_build_object('text', f.text, 'url', f.url,
                         'source_type', f.source_type, 'observed_at', f.observed_at)
                  from v_fact_working_on f
                 where f.entity_type = 'contact' and f.entity_id = p.id) as working_on_raw,
               coalesce((select jsonb_agg(jsonb_build_object('type', t.type, 'date', t.date))
                         from (select type, date from v_touch where contact_id = p.id
                               order by date desc nulls last, id desc limit 3) t), '[]')
                 as last_touches
        from v_contact_stage p
        where {where}
        order by p.stage_rank desc, p.company, p.name, p.id
        limit :limit offset :offset
        """,
        **params,
        limit=limit,
        offset=offset,
    )
    return _evidence(items), int(total)


def person(conn: Connection, contact_id: int) -> dict[str, Any] | None:
    found = rows(conn, f"select {PERSON_FIELDS} from v_contact_stage where id = :id", id=contact_id)
    if not found:
        return None
    item = found[0]
    item["working_on_raw"] = working_on_raw(conn, contact_id)
    item["touches"] = rows(
        conn,
        "select type, date, outcome, source from v_touch where contact_id = :id "
        "order by date desc nulls last, id desc",
        id=contact_id,
    )
    item["stage_history"] = rows(
        conn,
        "select stage, from_at, reason from v_stage_log where contact_id = :id "
        "order by from_at desc, id desc",
        id=contact_id,
    )
    item["opportunity"] = (
        rows(
            conn,
            "select package_text, status, prob, size_usd, size_text, deal_status "
            "from v_opportunity "
            "where contact_id = :id",
            id=contact_id,
        )
        or [None]
    )[0]
    return _evidence([item])[0]


def working_on_raw(conn: Connection, contact_id: int) -> dict[str, Any] | None:
    """The newest live working_on fact of a person (docs/03 §3.2), or None."""
    found = rows(
        conn,
        "select text, url, source_type, observed_at from v_fact_working_on "
        "where entity_type = 'contact' and entity_id = :id",
        id=contact_id,
    )
    return found[0] if found else None


# ------------------------------------------------------------------ my tasks inputs (R2)


def task_inputs(conn: Connection) -> list[dict[str, Any]]:
    """One row per live person with the columns the task rules read (v_task_inputs).

    `email_added` (D154): an `email_write` decision for the person is live (not undone, its
    sheet push not failed) and is less than a day old. It hides the Add email task from the
    write until the next sheet read shows the email. The day bound stops an old write from
    hiding the task for good if the cell is cleared later.
    """
    return rows(
        conn,
        "select t.*, exists (select 1 from v_decision d where d.contact_id = t.contact_id "
        "and d.kind = 'column_write' and d.role = 'email' and d.undone_at is null "
        "and coalesce(d.sync_state, '') <> 'failed' "
        "and d.at > now() - interval '1 day') as email_added from v_task_inputs t",
    )


def proposed_decisions(conn: Connection) -> list[dict[str, Any]]:
    """Strategy decisions that wait for a confirm, oldest first."""
    return rows(
        conn,
        "select id, choice, user_name, at from v_decision "
        "where kind = 'strategy_decision' and state = 'proposed' order by at, id",
    )


def open_card_count(conn: Connection) -> int:
    return conn.execute(
        text("select count(*) from v_inbox where item_kind = 'proposal'")
    ).scalar_one()


WINDOW_WRITES_CAP = 50


def writes_in_undo_window(conn: Connection, signer: str | None = None) -> list[dict[str, Any]]:
    """Writes that can still be undone, newest first: by one signer, or by anyone (None).

    Per signer, so that newer writes of other people never push a person's own tick out.
    """
    return rows(
        conn,
        "select id, user_short, user_name, role, contact_id, person, undo_until from v_decision "
        "where kind in ('column_write', 'task_done') and undo_state = 'open' "
        "and (cast(:signer as text) is null or user_short = :signer) "
        "order by at desc, id desc limit :cap",
        signer=signer,
        cap=WINDOW_WRITES_CAP,
    )


def commitments_due(conn: Connection, day: Any) -> list[dict[str, Any]]:
    return rows(
        conn,
        "select id, owner_short, text, due, source_type, state from v_commitment "
        "where state = 'open' and due = :day order by id",
        day=day,
    )


# ------------------------------------------------------------------ accounts


def accounts_where(filter_name: str) -> tuple[str, dict[str, Any]]:
    if filter_name in ACCOUNT_FILTERS:
        return ACCOUNT_FILTERS[filter_name], {}
    if re.fullmatch(r"goal:G\d{1,2}", filter_name):
        return ":f_goal = any(goal_codes)", {"f_goal": filter_name[5:]}
    if filter_name.startswith("industry:") and 0 < len(filter_name) - 9 <= MAX_Q:
        return "coalesce(industry, '') ilike :f_ind escape '\\'", {"f_ind": like(filter_name[9:])}
    raise BadFilter(filter_name)


def accounts(
    conn: Connection, filter_name: str, q: str | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    where, params = accounts_where(filter_name)
    pattern = like(q)
    if pattern:
        where += " and (name ilike :q escape '\\' or coalesce(industry, '') ilike :q escape '\\' "
        where += "or coalesce(geo, '') ilike :q escape '\\')"
        params["q"] = pattern
    total = conn.execute(
        text(f"select count(*) from v_account_coverage where {where}"), params
    ).scalar_one()
    items = rows(
        conn,
        f"""
        select id, sheet_row_key, name, industry, geo, funding, in_master, goal_codes, people,
               contacted, connected, emailed, p1_count, threads, coverage_pct, warmth
        from v_account_coverage where {where}
        order by saley_stage_rank(warmth) desc, connected desc, people desc, name, id
        limit :limit offset :offset
        """,
        **params,
        limit=limit,
        offset=offset,
    )
    return _evidence(items), int(total)


def industry_coverage(conn: Connection) -> list[dict[str, Any]]:
    return rows(
        conn,
        """
        select industry, people, contacted,
               case when people > 0 then round(100.0 * contacted / people)::int end as pct
        from v_segment_scorecard order by (industry = 'No industry'), people desc
    """,
    )


def account(conn: Connection, account_id: int) -> dict[str, Any] | None:
    found = rows(conn, "select * from v_account_coverage where id = :id", id=account_id)
    if not found:
        return None
    item = found[0]
    item["people_list"] = _evidence(
        rows(
            conn,
            "select id, sheet_row_key, name, title, stage, priority, has_email, next_step "
            "from v_contact_stage where account_id = :id order by stage_rank desc, name, id "
            "limit 200",
            id=account_id,
        )
    )
    item["touches"] = rows(
        conn,
        "select c.name, t.type, t.date from v_touch t "
        "join v_contact_stage c on c.id = t.contact_id "
        "where c.account_id = :id "
        "order by t.date desc nulls last, t.id desc limit 30",
        id=account_id,
    )
    item["stage_history"] = rows(
        conn,
        "select c.name, l.stage, l.from_at, l.reason from v_stage_log l "
        "join v_contact_stage c on c.id = l.contact_id where c.account_id = :id "
        "order by l.from_at desc, l.id desc limit 30",
        id=account_id,
    )
    return _evidence([item])[0]


# ------------------------------------------------------------------ goals, blockers, scorecard


def goals(conn: Connection, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
    total = conn.execute(text("select count(*) from v_goal_status")).scalar_one()
    items = rows(
        conn,
        "select * from v_goal_status order by position limit :limit offset :offset",
        limit=limit,
        offset=offset,
    )
    return _evidence(items), int(total)


def blockers(conn: Connection, q: str | None, limit: int, offset: int) -> dict[str, Any]:
    """Late deliverables (most late first) and packages not ready. Both paged."""
    pattern = like(q)
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    cond = ""
    pcond = ""
    if pattern:
        cond = " where item ilike :q escape '\\' or coalesce(owner, '') ilike :q escape '\\'"
        pcond = " where name ilike :q escape '\\'"
        params["q"] = pattern
    late_total = conn.execute(
        text(f"select count(*) from v_deliverable_late{cond}"), params
    ).scalar_one()
    late = rows(
        conn,
        f"select id, sheet_row_key, item, owner, due, late_by_days, status, blocks_goals, "
        f"blocks_motions from v_deliverable_late{cond} "
        f"order by late_by_days desc, item, id limit :limit offset :offset",
        **params,
    )
    packages_total = conn.execute(
        text(f"select count(*) from v_package_not_ready{pcond}"), params
    ).scalar_one()
    packages = rows(
        conn,
        f"select id, sheet_row_key, name, purpose, pct, status from v_package_not_ready{pcond} "
        f"order by pct nulls first, name, id limit :limit offset :offset",
        **params,
    )
    return {
        "late": _evidence(late),
        "late_total": int(late_total),
        "packages": _evidence(packages),
        "packages_total": int(packages_total),
    }


def money_summary(conn: Connection) -> dict[str, int]:
    """Total of known deal values, number of deals, and how many have no value (D42)."""
    row = (
        conn.execute(
            text(
                "select coalesce(sum(size_usd), 0) as total_usd, count(*) as deals, "
                "count(*) filter (where size_usd is null) as no_value from v_money_blocked"
            )
        )
        .mappings()
        .one()
    )
    return {k: int(v) for k, v in row.items()}


def money_blocked(conn: Connection, limit: int, offset: int) -> list[dict[str, Any]]:
    return _evidence(
        rows(
            conn,
            "select contact_id, sheet_row_key, name, company, stage, size_usd, size_text, "
            "deliverable, owner, late_by_days, more_blocking from v_money_blocked "
            "order by size_usd desc nulls last, late_by_days desc, name, contact_id "
            "limit :limit offset :offset",
            limit=limit,
            offset=offset,
        )
    )


def scorecard(conn: Connection, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
    total = conn.execute(text("select count(*) from v_segment_scorecard")).scalar_one()
    items = rows(
        conn,
        "select * from v_segment_scorecard order by (industry = 'No industry'), people desc, "
        "industry limit :limit offset :offset",
        limit=limit,
        offset=offset,
    )
    return items, int(total)


def scorecard_last_week(conn: Connection, day: Any) -> dict[str, dict[str, Any]]:
    """The snapshot rows of one day by industry (D118). A day with none gives an empty dict."""
    found = rows(conn, "select * from v_scorecard_history where day = :d", d=day)
    return {r["industry"]: r for r in found}


def weekly_pack(conn: Connection, week: Any | None) -> dict[str, Any] | None:
    """One pack by its Monday, or the newest when `week` is None. Views only (D114)."""
    where = "where week_start = :w" if week is not None else ""
    found = rows(
        conn,
        f"select week_start, built_at, body from v_weekly_pack {where} "
        "order by week_start desc limit 1",
        **({"w": week} if week is not None else {}),
    )
    return found[0] if found else None


def pack_neighbours(conn: Connection, week: Any) -> dict[str, Any]:
    """The Mondays of the packs just before and just after `week` (None when there is none).

    Why: the week picker moves only to a week that has a pack, so a normal click never asks
    for a missing week and never logs a 404 in the browser (R5 T17, D229).
    """
    found = rows(
        conn,
        "select (select max(week_start) from v_weekly_pack where week_start < :w) as prev_week, "
        "(select min(week_start) from v_weekly_pack where week_start > :w) as next_week",
        w=week,
    )
    return found[0]


def health(conn: Connection) -> dict[str, Any]:
    jobs = rows(
        conn,
        """
        select distinct on (job) job, status, started_at, finished_at, note
        from v_job_runs order by job, started_at desc
    """,
    )
    latest_run = conn.execute(
        text("select run_id from v_parity_checks order by at desc limit 1")
    ).scalar()
    parity = (
        rows(
            conn,
            "select metric, sheet_value::int, view_value::int, ok, note from v_parity_checks "
            "where run_id = :r order by ok, metric",
            r=latest_run,
        )
        if latest_run
        else []
    )
    quality = rows(
        conn,
        """
        select problem, n from v_data_quality_open order by n desc, problem
    """,
    )
    missing_industry = conn.execute(
        text("select count(*) from v_contact_stage where coalesce(sheet_industry, '') = ''")
    ).scalar_one()
    egress = rows(
        conn,
        "select host, calls, errors from v_egress_today order by calls desc, host",
    )
    llm = (
        conn.execute(
            text("""
        select calls, cached from v_llm_today
    """)
        )
        .mappings()
        .one()
    )
    for job in jobs:
        job["note"] = scrub_text(job["note"])  # an error text may hold a connection string
    tabs = rows(
        conn,
        "select tab, title, status, missing_headers, new_headers, stale_since, reason "
        "from sheet_tab_status order by (status = 'ok'), tab",
    )
    return {
        "tabs": tabs,
        "jobs": jobs,
        "parity": parity,
        "parity_run": latest_run,
        "quality": quality,
        "missing_industry": missing_industry,
        "egress": egress,
        "llm": dict(llm),
    }


# ------------------------------------------------------------------ search


def decisions(q: str | None, path: Path = DECISIONS_FILE) -> list[dict[str, str]]:
    """Rows of docs/DECISIONS.md that match q. The decision table arrives in R2."""
    if not q or not path.exists():
        return []
    needle = q.strip().casefold()[:MAX_Q]
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or not re.fullmatch(r"D\d+(–D\d+)?", cells[0]):
            continue
        if needle in line.casefold():
            out.append({"id": cells[0], "date": cells[1], "by": cells[2], "text": cells[3]})
    return out


SEARCH_GROUP_LIMIT = 10


def _match(name: str, q: str) -> list[int] | None:
    """[start, length] of q inside name (case folded), for bold text. None if not in name."""
    at = name.casefold().find(q.strip().casefold())
    return [at, len(q.strip())] if at >= 0 else None


def _group(
    conn: Connection, count_sql: str, sql: str, pattern: str
) -> tuple[list[dict[str, Any]], int]:
    total = conn.execute(text(count_sql), {"q": pattern}).scalar_one()
    return rows(conn, sql, q=pattern, limit=SEARCH_GROUP_LIMIT), int(total)


def search(conn: Connection, q: str) -> dict[str, Any]:
    """People, Companies, Goals, Deliverables and Decisions that match q (R1.1 ticket A.6)."""
    pattern = like(q)
    empty: dict[str, Any] = {"items": [], "total": 0}
    if not pattern:
        return {
            "groups": {k: dict(empty) for k in GROUPS},
            "total": 0,
            "decisions_available": DECISIONS_FILE.exists(),
        }
    groups: dict[str, Any] = {}
    people, n = _group(
        conn,
        f"select count(*) from v_contact_stage where {PEOPLE_SEARCH}",
        f"select id, name, company, title, stage, owner from v_contact_stage "
        f"where {PEOPLE_SEARCH} order by stage_rank desc, name, id limit :limit",
        pattern,
    )
    groups["people"] = {
        "total": n,
        "items": [
            {
                "type": "person",
                "id": p["id"],
                "name": p["name"],
                "context": " · ".join(x for x in (p["title"], p["company"]) if x),
                "pill": p["stage"],
                "owner": p["owner"],
                "match": _match(p["name"], q),
            }
            for p in people
        ],
    }
    company_where = (
        "name ilike :q escape '\\' or coalesce(industry, '') ilike :q escape '\\' "
        "or coalesce(geo, '') ilike :q escape '\\'"
    )
    companies, n = _group(
        conn,
        f"select count(*) from v_account_coverage where {company_where}",
        f"select id, name, industry, geo, people, warmth from v_account_coverage "
        f"where {company_where} order by people desc, name, id limit :limit",
        pattern,
    )
    groups["companies"] = {
        "total": n,
        "items": [
            {
                "type": "company",
                "id": c["id"],
                "name": c["name"],
                "context": " · ".join(
                    x for x in (c["industry"], c["geo"], f"{c['people']} people") if x
                ),
                "pill": c["warmth"],
                "owner": None,
                "match": _match(c["name"], q),
            }
            for c in companies
        ],
    }
    goal_where = "text ilike :q escape '\\' or coalesce(dependencies, '') ilike :q escape '\\'"
    goals_found, n = _group(
        conn,
        f"select count(*) from v_goal_status where {goal_where}",
        f"select id, code, text, due, rag, owner from v_goal_status where {goal_where} "
        f"order by position limit :limit",
        pattern,
    )
    groups["goals"] = {
        "total": n,
        "items": [
            {
                "type": "goal",
                "id": g["id"],
                "name": g["text"],
                "context": f"{g['code']} · due {g['due']:%d %b}" if g["due"] else g["code"],
                "pill": g["rag"],
                "owner": g["owner"],
                "match": _match(g["text"], q),
            }
            for g in goals_found
        ],
    }
    deliverable_where = "item ilike :q escape '\\' or coalesce(dependency, '') ilike :q escape '\\'"
    found, n = _group(
        conn,
        f"select count(*) from v_deliverable where ({deliverable_where})",
        f"select id, item, dependency, status, due from v_deliverable "
        f"where ({deliverable_where}) order by due nulls last, item, id "
        f"limit :limit",
        pattern,
    )
    groups["deliverables"] = {
        "total": n,
        "items": [
            {
                "type": "deliverable",
                "id": d["id"],
                "name": d["item"],
                "context": " · ".join(
                    x for x in (d["status"], f"due {d['due']:%d %b}" if d["due"] else None) if x
                ),
                "pill": d["status"],
                "owner": d["dependency"],
                "match": _match(d["item"], q),
            }
            for d in found
        ],
    }
    decided = decisions(q)
    groups["decisions"] = {
        "total": len(decided),
        "items": [
            {
                "type": "decision",
                "id": d["id"],
                "name": f"{d['id']} · {d['text']}"[:160],
                "context": f"{d['by']} · {d['date']}",
                "pill": None,
                "owner": None,
                "match": _match(f"{d['id']} · {d['text']}"[:160], q),
            }
            for d in decided[:SEARCH_GROUP_LIMIT]
        ],
    }
    return {
        "groups": groups,
        "total": sum(g["total"] for g in groups.values()),
        "decisions_available": DECISIONS_FILE.exists(),
    }


GROUPS = ("people", "companies", "goals", "deliverables", "decisions")
