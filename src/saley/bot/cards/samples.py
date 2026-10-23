"""One sample of every card type, for the card tests and the live round trips (R7-K01, R7-L01).

Why: the round trip posts each card type to the TEST channel and deletes it at once, and the
tests check the card rules on the same input. These rows are test data, marked as such: the
names belong to no real person or company, and every post carries the `[TEST]` tag.
The list cards hold 7 items, so the thread rule (5 in the card, 2 in the thread) is used.
"""

from __future__ import annotations

from typing import Any

PEOPLE = [
    ("Test Person One", "Test Company A"),
    ("Test Person Two", "Test Company B"),
    ("Test Person Three", "Test Company C"),
    ("Test Person Four", "Test Company D"),
    ("Test Person Five", "Test Company E"),
    ("Test Person Six", "Test Company F"),
    ("Test Person Seven", "Test Company G"),
]

SAMPLES: dict[str, dict[str, Any]] = {
    "M1": {
        "name": "Sid",
        "day": "Thu 23 Oct",
        "lines": [
            "Meeting with Test Person One (Test Company A) on Fri 24 Oct",
            "Reconnect with Test Person Two is due",
            "One suggestion for Test Company C",
            "Commitment: send the deck",
        ],
        "more": 2,
    },
    "M2": {
        "who": "Sid",
        "stories": [
            {
                "story_id": 100 + i,
                "title": f"{company} opens an evals call",
                "company": company,
                "why": f"They want partners for an evaluation programme, by {name}'s note.",
                "source": "example.org",
                "day": "14 Oct",
                "thumbnail": None,
            }
            for i, (name, company) in enumerate(PEOPLE)
        ],
        "page_label": "Test Person One",
        "page_url": None,
    },
    "M3": {
        "who": "Vaishnavi",
        "people": [
            {
                "ref_id": 200 + i,
                "name": name,
                "company": company,
                "reason": "replied to our first email",
                "step": f"Add {name} ({company}) to pipeline",
            }
            for i, (name, company) in enumerate(PEOPLE)
        ],
    },
    "M4": {
        "who": "Sid",
        "events": [
            {
                "event_id": 300 + i,
                "name": f"Test Event {i + 1}",
                "city": "Bangalore",
                "day": "28 Oct",
                "who_goes": PEOPLE[i][0],
                "registered": i % 2 == 1,
            }
            for i in range(7)
        ],
    },
    "M5": {
        "who": "Vaishnavi",
        "name": "Test Person One",
        "company": "Test Company A",
        "title": "Professor, AI",
        "stage": "Qualified",
        "day": "Fri 24 Oct",
        "contact_id": 412,
        "thumbnail": None,
        "working_on": "A student attention study pilot",
        "touches": [
            {"label": "Email 3", "day": "1 Oct"},
            {"label": "Note", "day": "14 Oct"},
            {"label": "Reply", "day": "18 Oct"},
        ],
        "package": "Evals pilot",
        "questions": ["Which cohort size does the pilot need?"],
        "risk": "no deal value on the sheet yet.",
    },
    "M6": {
        "who": "Vaishnavi",
        "name": "Test Person One",
        "company": "Test Company A",
        "contact_id": 412,
        "row_key": "Outreach PoCs:412",
        "statuses": ["completed", "no show", "rescheduled"],
        "no_show": "no show",
    },
    "M7": {
        "who": "Vaishnavi",
        "name": "Test Person Two",
        "company": "Test Company B",
        "contact_id": 413,
        "last_touch": "email 2 on 2 Oct",
        "reason": "the pilot ended last week, by Sid's note on 2 Oct",
        "done": {
            "action": "task_done",
            "targets": [413],
            "payload": {"task": "reconnect"},
            "key": "0" * 64,
            "role": "task:reconnect",
            "row_key": "Outreach PoCs:413",
        },
    },
    "M8": {"who": "Sid", "commitment_id": 9, "text": "send the deck", "day": "Fri 24 Oct"},
    "M9": {"who": "Kushal", "reminder_id": 5, "text": "call Test Person Three"},
    "M10": {
        "who": "Sid",
        "proposal_id": 12,
        "type": "li_status",
        "name": "Test Person Four",
        "company": "Test Company D",
        "contact_id": 415,
        "writes": "Sid - LI Addition = Connected",
    },
    "M11": {
        "account_id": 77,
        "company": "Test Company F",
        "fit": "Launched evaluation tooling last week",
        "known": None,
        "first_step": "a first email to the head of evals",
        "takers": [("sid", "Sid"), ("vaishnavi", "Vaishnavi")],
    },
    "M12": {
        "day": "27 Oct",
        "week": "2026-W44",
        "lines": [
            {"rag": "green", "label": "Met", "value": "31", "last": 27},
            {"rag": "amber", "label": "Qualified", "value": "9", "last": 8},
            {"rag": "red", "label": "Deals closed", "value": "1 of 3", "last": None},
        ],
        "funnel": "+4 contacted, +1 met",
        "top": ["Test Company A: the NDA is the blocker", "Test Company B: reconnect on Thu"],
        "idea": "a shorter NDA could help us",
    },
    "M13": {
        "items": [{"owner": "Kushal", "item": f"Test item {i + 1}", "goal": "G1"} for i in range(7)]
    },
    "M14": {
        "who": "Kushal",
        "ask_id": 3,
        "question": "Who signs on the Test Company A side?",
        "entity_type": "contact",
        "entity_id": 412,
    },
    "M15": {
        "day": "Thu 23 Oct",
        "jobs_ok": 2021,
        "jobs_failed": 0,
        "llm": 212,
        "cap": 400,
        "egress": 0,
        "sent": 9,
        "held": 2,
        "failed": 0,
        "presses": {"ok": 4, "already": 1, "refused": 0},
        "listen": "23:40",
        "dms_off": True,
    },
    "A1": {
        "text": "- Test Person One at Test Company A: a pilot [sheet: Test Person One]\n"
        "Window: sheet as of 12:02 · 1 shown · 0 more · 0 left out as seen by you.",
        "more": True,
    },
    "A2": {"writers": [("sid", "Sid"), ("vaishnavi", "Vaishnavi")]},
}

CARD_IDS = {t: f"{t.lower()}-test-sample" for t in SAMPLES}
