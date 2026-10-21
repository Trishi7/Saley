"""Accounts read model: stage counts, "13 people · 9 P1", tags and the timeline (R5 T6).

Why a module of its own: app.py is near the line ceiling. Tags are plain rules on stored
counts. Nothing here guesses a stage or a thread.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection

from saley.web import queries, timeline
from saley.web import words as w

ENGAGED_MIN_THREADS = 2


def people_label(people: int, p1: int) -> str:
    return w.fill_count(w.PEOPLE_LABEL_ONE, w.PEOPLE_LABEL, people, p1=p1)


def tags(stage_counts: dict[str, int], threads: int) -> list[str]:
    """Qualified: a person at stage Qualified. Engaged: 2+ threads. Single-threaded: 1."""
    found = []
    if stage_counts.get("Qualified", 0) > 0:
        found.append(w.TAG_QUALIFIED)
    if threads >= ENGAGED_MIN_THREADS:
        found.append(w.TAG_ENGAGED)
    elif threads == 1:
        found.append(w.TAG_SINGLE)
    return found


def legend() -> list[dict[str, str]]:
    """What each tag means and where it comes from, for the legend at the top of the page."""
    return [
        {"tag": w.TAG_QUALIFIED, "meaning": w.TAG_QUALIFIED_MEANS, "source": w.TAG_SOURCE},
        {"tag": w.TAG_ENGAGED, "meaning": w.TAG_ENGAGED_MEANS, "source": w.TAG_SOURCE},
        {"tag": w.TAG_SINGLE, "meaning": w.TAG_SINGLE_MEANS, "source": w.TAG_SOURCE},
    ]


def stage_counts(conn: Connection, ids: list[int]) -> dict[int, dict[str, int]]:
    """{account id: {stage: people}} in one query. An account without people has {}."""
    found: dict[int, dict[str, int]] = {i: {} for i in ids}
    if not ids:
        return found
    for r in queries.rows(
        conn,
        "select account_id, stage, count(*) as n from v_contact_stage "
        "where account_id = any(:ids) group by account_id, stage",
        ids=ids,
    ):
        found[r["account_id"]][r["stage"]] = int(r["n"])
    return found


def decorate(conn: Connection, items: list[dict[str, Any]]) -> None:
    """Add stage_counts, people_label and tags to each account, in place."""
    counts = stage_counts(conn, [i["id"] for i in items])
    for item in items:
        by_stage = counts[item["id"]]
        item["stage_counts"] = by_stage
        item["people_label"] = people_label(item["people"], item["p1_count"])
        item["tags"] = tags(by_stage, item["threads"])


def decorate_detail(conn: Connection, item: dict[str, Any]) -> None:
    """The detail adds the timeline. The Evidence row stays: the old page reads it (T6 note)."""
    decorate(conn, [item])
    item["timeline"] = timeline.for_account(conn, item["id"])
    item["evidence_hidden"] = True
