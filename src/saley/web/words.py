"""Every UI string the server sends, in ASD-STE100 style (R1.1 ticket A.7).

Why: gate step 3 lints one catalogue instead of text scattered through the code. Each string
is ≤ 60 characters ({name} placeholders count as 4) and uses no word from
docs/ste-banned-words.txt. Short sentences, active voice, one idea per line.
"""

from __future__ import annotations

# ------------------------------------------------------------------ Today tiles
TILE_DEALS = "Deals closed this quarter"
TILE_LIVE = "People in a live conversation"
TILE_BACKLOG = "Connected, no email yet"
TILE_P1 = "P1 waiting on LinkedIn"
TILE_WEEK = "New this week"
TILE_LATE = "Late items that block goals"
TILE_MONEY = "Money blocked by late items"

DAYS_LEFT = "{n} days left"
DAYS_LEFT_ONE = "1 day left"
LAST_DAY = "Last day of the quarter"
QUARTER_ENDED = "Quarter ended {n} days ago"
QUARTER_ENDED_ONE = "Quarter ended 1 day ago"
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
SRC_LIVE = "Outreach PoCs, stage rules"
SRC_BACKLOG = "Outreach PoCs, Sid - LI Addition"
SRC_P1 = "Outreach PoCs, Priority column"
SRC_WEEK = "Outreach PoCs, First Contact Date"
SRC_LATE = "Deliverables Checklist, Tentative Deadline"
SRC_MONEY = "Estd. Deal Size and late deliverables"
SRC_REPLY = "No source yet"

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
REPLY_NOTE = "No source yet"

# ------------------------------------------------------------------ errors (API)
ERR_SIGN_IN = "Sign in to use Saley."
ERR_ADMIN = "Sources and health is for admins."
ERR_FILTER = "That filter is not known."
ERR_PARAM = "Unknown parameter."
ERR_CHANGED = "Data changed. Reload the page."
ERR_SERVER = "Server error. The error is in the log."
ERR_CROSS_SITE = "Cross-site request refused."
ERR_NO_PERSON = "No such person."
ERR_NO_COMPANY = "No such company."
ERR_DAY = "That day is out of range."
ERR_CAT = "Use one of the seven topic names."
ERR_PAGE_OFFSET = "The offset must be from 0 to 1000."

# ------------------------------------------------------------------ act (R2, D24, D48)
SAVED = "Saved: {what}"
ERR_ACT_HEADER = "Missing header X-Saley-Act."
ERR_AS_HEADER = "X-Saley-As works with the mock sign-in only."
ERR_AS_UNKNOWN = "That user is not in the team."
ERR_LOCKED = "That column is read-only. Saley does not write it."
ERR_EMAIL_OFF = "Email writes are off (EMAIL_WRITE_ALLOWED)."
ERR_NOT_APPROVER = "Only approvers can do this."
ERR_COOLDOWN = "Already done by {who} at {t}"
ERR_WINDOW = "Undo window closed at {t}"
ERR_UNDONE = "Already undone."
ERR_NO_WRITE = "No such write."
ERR_NO_DECISION = "No such decision."
ERR_NO_CARD = "No such card."
ERR_NO_COMMITMENT = "No such commitment."
ERR_NOT_OWNER = "This is {name}'s commitment. Only they can mark it done."  # D155
ERR_NOT_SIGNER = "Only the person who marked it done can undo it."  # D155
ERR_EMAIL_FILLED = "That person already has an email."  # D154
ERR_CARD_CLOSED = "That card is not open."
ERR_VALUE = "That value is not allowed."
ERR_NO_DONE_VALUE = "This column has no done value yet."
ERR_NO_BEFORE = "The old value is not known. Cannot undo."
ERR_BAD_ACT = "The request is not valid."
ERR_SUPERSEDED = "A newer change is on this cell."
ERR_BUSY = "Busy. Try again."
ERR_TOO_BIG = "The request is too large."
ERR_HOST = "This address is not allowed in dev mode."
# / without a build (D145, D224): a plain line, never a trace.
APP_NOT_BUILT = "The dashboard is not built yet. Run the web build."
WRITES_OFF = "Sheet writes are off"
PENDING_NOTE = "Sheet updates in a few seconds"
WRITE_FAILED = "Sheet write failed: {n}. See Decisions."

# Write state of one decision (Decisions view) and the fixed reason of a failed write.
SYNC_REASON_CELL = "The cell changed. Saley did not put the old value back."
SYNC_REASON_ROW = "The person was not found in the sheet."
SYNC_REASON_LOCKED = "That column is read-only."
SYNC_REASON_FORMULA = "The value starts with a formula sign."
SYNC_REASON_NOTE = "The person is in the sheet. The signature note failed."
SYNC_REASON_OTHER = "The sheet did not accept the write."
COMMITMENT_LABEL = "Commitment done"

# Short names of the writable columns, for the Saved toast.
ROLE_LABELS = {
    "first_contact": "First Contact",
    "first_contact_type": "First Contact Type",
    "first_contact_date": "First Contact Date",
    "sid_li_added": "Sid - LI Addition",
    "li_connected_date": "LI Connected Date",
    "meeting_date": "Meeting Date",
    "meeting_status": "Meeting Status",
    "email": "Email",
    "row_add": "Person added",
}
TASK_LABEL = "Task done"

# ------------------------------------------------------------------ my tasks (R2, D31)
MY_TASKS_EMPTY = "No tasks for you today."
TASKS_MORE = "{n} more"
TASK_LI_CONFIRM = "Confirm LinkedIn connection: {name}"
TASK_LI_FIRST = "Send first LinkedIn request: {name}"
TASK_RECONNECT = "Reconnect with {name}"
TASK_MEETING = "Meeting today: {name}"
TASK_STATUS = "Confirm meeting status: {name}"
TASK_ADD_EMAIL = "Add email for {name}"  # D154
TASK_NO_DATE = "No date"  # D180: a task with no due day does not count as today
TASK_EMAIL1 = "Send email 1 to {name}"
TASK_EMAIL2 = "Send email 2 to {name}"
TASK_EMAIL3 = "Send email 3 to {name}"
TASK_DECISION = "Confirm decision {id}"
TASK_INBOX_CARDS = "{n} cards wait in the Inbox"
TASK_REVIEW_WRITE = "{who} saved {what} for {name}"
TASK_COMMITMENT = "Due today: {text}"
WHY_LI_CONFIRM = "LinkedIn request sent, no connected date"
WHY_LI_FIRST = "Priority pick. No first contact yet"
WHY_RECONNECT = "Reconnect due on {day}"
WHY_MEETING = "The meeting date is today"
WHY_STATUS = "The meeting was on {day}. Status is {status}"
WHY_ADD_EMAIL = "Connected {n} days ago. No email on file"
SOON_LABEL = "Coming soon"  # D154: the field is off while EMAIL_WRITE_ALLOWED is false
WHY_EMAIL_STEP = "Step due on {day}"
WHY_REVIEW = "You can undo until {t}"
WHY_DECISION_BY = "Proposed by {who} on {day}"
WHY_COMMITMENT = "From {source}"

# ------------------------------------------------------------------ inbox cards (R2)
CARD_MARK_COMPLETED = "Mark meeting as completed? {name}"
CARD_MARK_COMPLETED_WHY = "Meeting date {day} has passed. Status is {status}"
CARD_LI_CONNECTED = "Mark LinkedIn as connected? {name}"
CARD_LI_CONNECTED_WHY = "LI Connected Date is {day}. Status is {status}"
BTN_MARK_COMPLETED = "Mark completed"
BTN_MARK_CONNECTED = "Mark connected"
INBOX_CONFIRM = "Confirm decision {id}"
INBOX_EMPTY = "No items in the Inbox."

# ------------------------------------------------------------------ commitments and reminders
SRC_NOTE = "Meeting note"
SRC_MAIL = "Mail"
SRC_OTHER = "Other source"
REMIND_BEFORE = "Due tomorrow: {text}"
REMIND_DUE_DAY = "Due today: {text}"
MAIL_NOT_CONFIGURED = "mail: not configured"
MAIL_READING = "mail: reading"
MAIL_FAILING = "mail: last poll failed"
PDF_UNREADABLE = "PDF note could not be read"
SRC_REMOVED = "{source} (source removed)"
NOTES_NOT_CONFIGURED = "notes: Drive not configured"
NOTES_WAITING = "notes: waiting for the first sync"
NOTES_FILES = "meeting notes: {n} files, last change {t}"
NOTES_FILES_NO_CHANGE = "meeting notes: {n} files"
NOTES_NOT_SHARED = "notes: folder not shared with {sa} (folder {id})"
NOTES_NOT_SHARED_NO_SA = "notes: folder not shared with the service account ({id})"
NOTES_API_OFF = "notes: Drive API is off for the project"
NOTES_FAILING = "notes: last sync failed"

# ------------------------------------------------------------------ working on (R2)
SRC_ABOUT = "Outreach PoCs, About PoC"
SRC_PAPER = "Outreach PoCs, Research Paper Link"
WORKING_PAPER = "Research paper link"

# ------------------------------------------------------------------ news and signals (R3)
NEWS_NOTE = "Counts for one day. Click a number to open its list."

# News health (A1): scoring, old sources, one line per family, the top bar.
NEWS_SCORER_RULES = "Scored by rules: {p} % in 24 h"
NEWS_SCORER_NONE = "Nothing scored in 24 h"
NEWS_LLM_PAUSED = "LLM paused until tomorrow"
NEWS_RESCORE = "{n} stories wait for an LLM score"
NEWS_STALE_NO_NEW = "{name}: no new items for {n} days"
NEWS_STALE_NO_NEW_ONE = "{name}: no new items for 1 day"
NEWS_STALE_304 = "{name}: only 304 answers. The feed may be frozen."
NEWS_FAMILY_NEWS = "News editions"
NEWS_FAMILY_ARXIV = "arXiv"
NEWS_FAMILY_OPENALEX = "OpenAlex"
NEWS_FAMILY_OLD = "{name}: no good run since {t}"
NEWS_FAMILY_NEVER = "{name}: no good run yet"
BAR_NEWS_OLD = "{t} (old)"
BAR_FAMILY_OLD = "{name} old"
SEARCH_FAIL = "Last search failure: {reason}"
SEARCH_FAIL_STATUS = "Last search failure: {reason} (HTTP {status})"
NOTES_PARTIAL = "meeting notes: folder list cut. {why}"
NOTES_PARTIAL_NO_WHY = "meeting notes: folder list cut"
NEWS_TRIED = "Sources tried"
NEWS_ANSWERED = "Sources that answered"
NEWS_FETCHED = "Items pulled"
NEWS_DROP_SEEN = "Dropped: seen before"
NEWS_DROP_COPY = "Dropped: duplicate story"
NEWS_DROP_GATE = "Dropped: not relevant"
NEWS_DEDUPED = "Stored as stories"
NEWS_SCORED = "Scored"
NEWS_SCORED_4 = "Scored 4 or 5"
NEWS_LINKED = "About our companies or people"
NEWS_SENT = "Sent to Discord"
NEWS_SENT_NOTE = "Digest not started yet"  # the human, 21 Oct (Q-R5-14)
NEWS_DAY_LABEL = "News on {day}"
SOURCE_PROBLEM = "Last answer: {status}"
SOURCE_NO_ANSWER = "No answer yet"

WINDOW_DAY = "Last 24 hours"
WINDOW_WEEK = "Last 7 days"
WINDOW_MONTH = "Last 30 days"
WINDOW_YEAR = "Last 12 months"
WINDOW_UPCOMING = "From today on"
WINDOW_LOOK = "Stories of 7 days. Events in 3 days."
WINDOW_PAST = "Before today"
WINDOW_SHEET = "Sheet as of {t} IST"
WINDOW_NO_SYNC = "Sheet not synced yet"
EMPTY_LIST = "No items. Last computed {t}."
NOTHING_NEW = "Nothing new since your last visit"
NO_HINT_YET = "no hint yet"
VERIFY_NOTE = "One match only. Check it."
NOT_BUILT = "Not built yet"
EVENT_TODAY = "Today"
EVENT_IN_DAYS = "In {n} days"
EVENT_IN_DAYS_ONE = "In 1 day"
EVENT_AGO = "{n} days ago"
EVENT_AGO_ONE = "1 day ago"
EVENT_NO_DATE = "Date not set"

# Score parts of the Suggestions lists (S07-S10). {n} is a count from the sheet.
PART_CONNECTIONS = "{n} connected at the company"
PART_EMAIL = "Email on file"
PART_DECISION_ROLE = "Decision role"
PART_P1 = "Marked P1"
PART_PAPER = "Has a paper"
PART_ROLE_BUDGET = "Budget role"
PART_ROLE_RESEARCH = "Research role"
PART_ROLE_DATA = "Data role"
PART_ROLE_USER = "User role"
PART_GOAL_FIT = "Fits {n} goals"
PART_GOAL_FIT_ONE = "Fits 1 goal"
PART_FUNDING = "Funding on file"
PART_NEWS = "{n} stories in 30 days"
PART_NEWS_ONE = "1 story in 30 days"
PART_TOP_STORY = "A story rated 4 or 5"
PART_TOPIC = "Topic fits our work"
PART_RECENT = "Paper news {n} days old"
PART_RECENT_ONE = "Paper news 1 day old"
PART_CITY_EVENT = "City has an event"
PART_OTHER = "Other fact"
SUGGEST_VIA = "Connected: {names}"
SUGGEST_CAPPED = "This list shows the first {n} rows."

SEARCH_NONE = "Not searched yet"
SEARCH_PENDING = "Search is waiting"
SEARCH_RUNNING = "Searching now"
SEARCH_DONE = "Search is done"
SEARCH_FAILED = "Search unavailable. Try later."
SEARXNG_SET = "SearXNG: set. DuckDuckGo if it fails."
SEARXNG_NOT_SET = "SearXNG: not configured. DuckDuckGo only."
APOLLO_OFF = "Apollo: off. The pipeline is not built."
FIND_QUEUED = "Search queued"
FIND_ALREADY = "A search is already queued"
ROW_ADDED = "Person added to the sheet"
ERR_DUPLICATE = "That person is already in the sheet or queued."
ERR_DUPLICATE_BY = "Already added by {who}"  # D111
ERR_TOO_MANY = "Too many searches are waiting. Try later."
ERR_ROW_LOCKED = "That column is not part of a new row."
ERR_LINK = "That link is not from a search result."

# Today · needs a look, Saley suggests (R3 phase 7)
LOOK_SIGNAL = "{who} · rated {n} of 5"
LOOK_EVENT = "{city} · {when}"
REPLIES_NOT_CONFIGURED = "Replies: mail not configured"


def fill(template: str, **values: object) -> str:
    """Put values in a template. Kept tiny so the lint reads the templates, not the output."""
    return template.format(**values)


def fill_count(singular: str, plural: str, n: int, **values: object) -> str:
    """The singular template for exactly 1, else the plural one ("1 day", "2 days")."""
    return (singular if n == 1 else plural).format(n=n, **values)


# ------------------------------------------------------------------ sign-in (R4, D114-D116)
SIGNIN_TITLE = "Sign in to Saley"
SIGNIN_MOCK = "Sign in as {who}"
SIGNIN_GOOGLE = "Sign in with Google"
SIGNIN_REFUSED = "Your email is not on the list."
SIGNIN_REFUSED_HELP = "Ask the admin to add it to the Saley list."
# Q-N5: the failure page names the reason in plain words, never only "Try again".
SIGNIN_EXPIRED = "Sign-in link expired, start again."
SIGNIN_EXPIRED_HELP = "Start and finish the sign-in at the same address: {origin}"
SIGNIN_STOPPED = "Google stopped the sign-in. Start again."
SIGNIN_CODE_REFUSED = "Google did not accept the sign-in code. Start again."
SIGNIN_BAD_CLIENT = "Google refused the client id or secret. Tell the admin."
SIGNIN_REDIRECT_MISMATCH = "The sign-in address and Google setup differ. Tell the admin."
SIGNIN_CODE_OLD = "The sign-in code expired or was used. Start again."
SIGNIN_NO_ANSWER = "Google did not answer. Wait a minute, then start again."
SIGNIN_UNVERIFIED = "Your Google email is not verified."
SIGNIN_BAD_ANSWER = "The answer from Google failed a check. Start again."
SIGNIN_NO_USER = "Your email is listed, but no team member matches it."
SIGNIN_NOT_SET = "Sign-in is not set up."
SIGNIN_BACK = "Back to sign-in"
SIGNIN_LIMIT = "Too many tries. Wait a minute."


# ------------------------------------------------------------------ weekly pack (R4, D117, D130)
# "last week N" is the snapshot of the day 7 days before. Never a week number such as W41.
LAST_WEEK = "last week {n}"
NO_SNAPSHOT_LAST_WEEK = "no snapshot last week"
PACK_NOT_FOUND = "No pack for that week."
PACK_BAD_WEEK = "The week must be a Monday."
PACK_TITLE = "Weekly pack · week of {day}"
PACK_TEAM_LINE = "{label}: {value} ({last})"
PACK_NO_VALUE = "no data"
PACK_M_CONTACTED = "People contacted this week"
PACK_M_ORGS = "Organisations touched this week"
PACK_M_CONNECT = "LinkedIn connect rate %"
PACK_M_MEETINGS = "Meetings per 100 contacted"
PACK_LATE_COUNT = "Late items that block goals: {n}"
PACK_PACKED_COUNT = "Reminders moved to this pack: {n}"
PACK_TASKS_LINE = "Next tasks: {who}"
PACK_TASKS_NONE = "Next tasks: none"
PACK_SCORE_LINE = "Industries in the Scorecard: {n}"
PACK_LINK = "Open the pack: {url}"
PACK_NO_OWNER = "no owner on file"
BLOCKER_NOTE_HEAD = "Late items that block goals:"
BLOCKER_NOTE_LINE = "{item} · {owner} · blocks {goals}"

# ------------------------------------------------------------------ Today read model (R5 T5)
FUNNEL_LISTED = "In the list"
FUNNEL_CONTACTED = "Contacted"
FUNNEL_CONNECTED = "Connected"
FUNNEL_TALKING = "Talking now"
FUNNEL_MET = "Met"
FUNNEL_WON = "Won"
BLOCKERS_LINE_NONE = "No late item blocks goals"
BLOCKERS_LINE_ONE = "1 late item blocks goals"
BLOCKERS_LINE = "{n} late items block goals"
LOOK_BLOCKER_WHY_ONE = "1 day late · blocks {goals}"
LOOK_BLOCKER_WHY = "{n} days late · blocks {goals}"
LOOK_WAITING_WHY = "Waiting for your answer · {n} h"
SUGGEST_LINE = "Add {name} ({company}) to pipeline — {reason}"
SUGGEST_LINE_NO_COMPANY = "Add {name} to pipeline — {reason}"
GOAL_NOTE_STAGE = "At stage {stage}"

# ------------------------------------------------------------------ Accounts, People, Events (T6)
PEOPLE_LABEL = "{n} people · {p1} P1"
PEOPLE_LABEL_ONE = "1 person · {p1} P1"
TAG_QUALIFIED = "Qualified"
TAG_ENGAGED = "Engaged"
TAG_SINGLE = "Single-threaded"
TAG_QUALIFIED_MEANS = "At least one person is at stage Qualified"
TAG_ENGAGED_MEANS = "Two or more people are in a thread"
TAG_SINGLE_MEANS = "One person is in a thread"
TAG_SOURCE = "Stage rules and the thread count of the account"
FOLLOW_UP_RULE = "the follow-up rule"  # Q-R5-16: no decision id in UI text
OWNER_SRC_FIRST = "First Contact"
OWNER_SRC_LI = "Sid - LI Addition"
OWNER_SRC_TYPE = "First Contact Type"
OWNER_SRC_DEFAULT = "default rule"
PAPERS_ONE = "1 paper"
PAPERS = "{n} papers"
NOTE_SOURCE = "note {day}"
CITY_BANGALORE = "Bangalore"
CITY_LONDON = "London"
CITY_OTHER = "Other"
ERR_FIND_OFF = "Find people is off for now."  # D164
# Run news now (R5 T8, D150, D151): each line is at most 60 characters.
ERR_NEWS_ADMIN = "Only an admin can run news now."
ERR_NEWS_BUSY = "A news run is already waiting or running."
ERR_NEWS_COOLDOWN = "Wait {minutes} min before the next news run."
NEWS_RUN_QUEUED = "News run queued."
NEWS_RUN_IDLE = "No news run was asked for yet."

# Sources & health · Discord (R7, docs/17 §10). The same lines print in `saley bot status`.
DISCORD_SENT = "Cards sent today"
DISCORD_HELD = "Cards held by the caps"
DISCORD_FAILED = "Cards that failed today"
DISCORD_PRESSES = "Presses today"
DISCORD_PRESSES_VALUE = "{ok} ok · {already} already · {refused} refused"
DISCORD_P50 = "Press time, middle value"
DISCORD_MS = "{n} ms"
DISCORD_LAST_SENT = "Last card sent"
DISCORD_LAST_SYNC = "Last card edit"
DISCORD_CHAT = "Chat lines kept"
DISCORD_SUMMARY = "Last chat summary"
DISCORD_DMS = "DMs"
DISCORD_ON = "on"
DISCORD_OFF = "off"
