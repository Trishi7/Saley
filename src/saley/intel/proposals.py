"""Proposals: Saley's own ideas as one-click Inbox cards (R2, D24, plan 3.6).

Why: an idea shows ONCE as a card with one primary button, never as a pile. Rules (plain code)
make the cards. `card_key` is stable and UNIQUE, so an idea is never inserted twice in any state:
a dismissed or acted card never returns, even when the sheet still looks the same.

R2 rules:
- `meeting_status`: the meeting date has passed and the status is still booked. Button: write
  Meeting Status = completed. Key: row key + meeting date.
- `li_status`: LI Connected Date is set but Sid - LI Addition still says the request is open.
  Button: write Sid - LI Addition = Connected. Key: row key + connected date.
Decisions that wait for a confirm are NOT proposals: they show in the Inbox straight from the
decision table (state proposed), so they are never listed twice.

A card whose idea no longer holds (the sheet changed, or the human wrote the cell) is expired.
A card is never made for a write the human already asked for: an open outbox row or a live
decision for the same cell hides the idea.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from saley.core.clock import today_ist
from saley.core.logging import get_logger

log = get_logger("saley.intel.proposals")

BOOKED_WORDS = ("booked", "scheduled", "confirmed")
MEETING_TYPE = "meeting_status"
LI_TYPE = "li_status"
CARD_TYPES = (MEETING_TYPE, LI_TYPE)
RANK = {MEETING_TYPE: 10, LI_TYPE: 20}

# `d.at` in team time: a decision made the day of the date (or later) already answers the idea.
_ASKED = """
  not exists (
    select 1 from decision d
    where d.row_key = t.sheet_row_key and d.role = :role and d.kind = 'column_write'
      and d.undone_at is null
      and (d.at at time zone 'Asia/Kolkata')::date >= :since)
  and not (:role = any(t.open_roles))
"""

MEETING_SQL = f"""
select t.contact_id, t.sheet_row_key, t.name, t.company, t.meeting_date as since,
       t.meeting_status as status
from v_task_inputs t
where t.meeting_date < :today
  and lower(trim(coalesce(t.meeting_status, ''))) = any(:booked)
  and {_ASKED.replace(":since", "t.meeting_date")}
"""

LI_SQL = f"""
select t.contact_id, t.sheet_row_key, t.name, t.company, t.li_connected_date as since,
       t.li_status as status
from v_task_inputs t
where t.li_connected_date is not null and t.li_requested and not t.connected
  and {_ASKED.replace(":since", "t.li_connected_date")}
"""


@dataclass
class ProposalStats:
    added: int = 0
    expired: int = 0

    def note(self) -> str:
        return f"{self.added} new cards · {self.expired} expired"


def _card(kind: str, row: dict, role: str, value: str, button: str) -> dict:
    day = row["since"].isoformat()
    return {
        "type": kind,
        "card_key": f"{kind}:{row['sheet_row_key']}:{day}",
        "contact_id": row["contact_id"],
        "payload": {
            "contact_id": row["contact_id"],
            "row": row["sheet_row_key"],
            "name": row["name"],
            "company": row["company"],
            "day": day,
            "status": row["status"],
            "button": button,
            "cells": [{"role": role, "value": value}],
        },
        "evidence": {"row": row["sheet_row_key"], "rule": kind},
        "rank": RANK[kind],
    }


def candidates(session: Session, today: date) -> list[dict]:
    """Every card the rules want today, with its stable key."""
    out: list[dict] = []
    meeting_rows = session.execute(
        text(MEETING_SQL), {"today": today, "booked": list(BOOKED_WORDS), "role": "meeting_status"}
    ).mappings()
    out += [
        _card(MEETING_TYPE, dict(r), "meeting_status", "completed", "mark_completed")
        for r in meeting_rows
    ]
    li_rows = session.execute(text(LI_SQL), {"role": "sid_li_added"}).mappings()
    out += [_card(LI_TYPE, dict(r), "sid_li_added", "Connected", "mark_connected") for r in li_rows]
    return out


def refresh_proposals(session: Session, today: date | None = None) -> ProposalStats:
    """Insert new cards (never twice), and expire open cards whose idea no longer holds."""
    today = today or today_ist()
    stats = ProposalStats()
    wanted = candidates(session, today)
    for card in wanted:
        result = session.execute(
            text(
                "insert into proposal (type, card_key, entity_type, entity_id, payload, "
                "evidence, rank) values (:type, :key, 'contact', :cid, cast(:payload as jsonb), "
                "cast(:evidence as jsonb), :rank) on conflict (card_key) do nothing"
            ),
            {
                "type": card["type"],
                "key": card["card_key"],
                "cid": card["contact_id"],
                "payload": json.dumps(card["payload"]),
                "evidence": json.dumps(card["evidence"]),
                "rank": card["rank"],
            },
        )
        stats.added += result.rowcount
    expired = session.execute(
        text(
            "update proposal set state = 'expired' where state = 'open' "
            "and type = any(:types) and card_key <> all(:keys)"
        ),
        {"types": list(CARD_TYPES), "keys": [c["card_key"] for c in wanted]},
    )
    stats.expired = expired.rowcount
    log.info("proposals_refreshed", added=stats.added, expired=stats.expired)
    return stats
