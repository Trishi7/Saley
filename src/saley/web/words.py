"""Every UI string the server sends, in ASD-STE100 style (R1.1 ticket A.7).

Why: gate step 3 lints one catalogue instead of text scattered through the code. Each string
is ≤ 60 characters ({name} placeholders count as 4) and uses no word from
docs/ste-banned-words.txt. Short sentences, active voice, one idea per line.
"""

from __future__ import annotations

# ------------------------------------------------------------------ Today tiles
TILE_DEALS = "Deals closed this quarter"
TILE_LIVE = "People in a live conversation"
TILE_BACKLOG = "Connected, no email sent yet"
TILE_P1 = "Priority people waiting on LinkedIn"
TILE_WEEK = "New people contacted this week"
TILE_LATE = "Late items that block goals"
TILE_MONEY = "Money blocked by late items"

DAYS_LEFT = "{n} days left"
LAST_DAY = "Last day of the quarter"
QUARTER_ENDED = "Quarter ended {n} days ago"
NO_DUE_DATE = "No due date in the goal tab"
LIVE_DETAIL = "Meeting done or emails going"
BACKLOG_DETAIL = "{n} of them have an email"
P1_DETAIL = "Of {n} marked P1"
WEEK_DETAIL = "Last week {n}"
LATE_DETAIL_TOTAL = "{n} late items in total"
NO_DATA = "No data yet. The sheet has not synced."
NO_TAB = "No data yet. Its sheet tab was never read."
MONEY_DETAIL = "Deals blocked: {n}"
MONEY_NO_VALUE = "Deals with no value on file: {n}"

# Where each number comes from (`source` on every tile, R1.1 ticket A.2).
SRC_DEALS = "Goal tab and Deal Status column"
SRC_LIVE = "Outreach PoCs, stage rules (D29)"
SRC_BACKLOG = "Outreach PoCs, Sid - LI Addition"
SRC_P1 = "Outreach PoCs, Priority column"
SRC_WEEK = "Outreach PoCs, First Contact Date"
SRC_LATE = "Deliverables Checklist, Tentative Deadline"
SRC_MONEY = "Estd. Deal Size and late deliverables"
SRC_REPLY = "No source until R2"

# ------------------------------------------------------------------ status bar
UPDATED = "Updated {t} IST · sheet {s} · news {n}"
WORKER_DOWN = "Worker not running since {t}"
SYNC_FAILING = "Sheet sync failing since {t}"
NEVER_SYNCED = "Sheet not synced yet"
TAB_STALE = "Old data: tab {t} not read"
TABS_STALE = "Old data: {n} tabs not read"

# ------------------------------------------------------------------ goals
WHY_LATE_ONE = "1 deliverable late"
WHY_LATE = "{n} deliverables late"
WHY_DEALS = "{won} of {need} deals won"
WHY_ACTIVE = "{n} active conversations, need {need}"
WHY_NO_TARGET = "Target has no number to count"
WEIGHTED_EMPTY = "≈ USD 0 · deal size fields are empty"
LINKED_BY = "industry rule"

# ------------------------------------------------------------------ money and values
NO_VALUE = "No value on file"
NONE_ON_FILE = "None on file"
REPLY_NOTE = "No source until R2"

# ------------------------------------------------------------------ errors (API)
ERR_SIGN_IN = "Sign-in arrives in R4. Use dev mode on a laptop."
ERR_ADMIN = "Sources and health is for admins."
ERR_FILTER = "Unknown filter: {x}"
ERR_PARAM = "Unknown parameter: {x}"
ERR_CHANGED = "Data changed. Reload the page."
ERR_SERVER = "Server error. The error is in the log."
ERR_CROSS_SITE = "Cross-site request refused."
ERR_NO_PERSON = "No such person."
ERR_NO_COMPANY = "No such company."

# ------------------------------------------------------------------ news (R3)
NEWS_NOTE = "News starts in R3."


def fill(template: str, **values: object) -> str:
    """Put values in a template. Kept tiny so the lint reads the templates, not the output."""
    return template.format(**values)
