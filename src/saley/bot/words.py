"""Every bot sentence, in ASD-STE100 style (D127, docs/09).

Why: gate step 3 lints the UPPER_CASE strings of `saley.bot`. Each string is at most 60
characters ({name} placeholders count as 4) and uses no word from docs/ste-banned-words.txt.
Never the word "overdue" (C05). The brief greets by first name, with no time of day (D139).
"""

from __future__ import annotations

# An author the bot does not know gets this one line and no data (D135).
UNKNOWN_AUTHOR = "Sorry, I do not know you yet. The team can add you."

# Daily brief (D128): head, up to 3 lines, the rest, the link.
BRIEF_HEAD = "Hi {name}. {n} things today:"
BRIEF_HEAD_ONE = "Hi {name}. 1 thing today:"
BRIEF_APPROVALS = "{n} cards wait for your approval."
BRIEF_APPROVALS_ONE = "1 card waits for your approval."
BRIEF_MEETING = "Meeting: {who} at {company} on {day}"
BRIEF_MEETING_NO_COMPANY = "Meeting: {who} on {day}"
BRIEF_REST = "{n} more on the dashboard."
BRIEF_LINK = "Open the dashboard: {url}"

# Health line (D133): the test channel, 23:45 IST.
DMS_OFF = "DMs off"
HEALTH_HEAD = "Health {day}"
HEALTH_JOBS = "Jobs in 24 h: {ok} ok, {failed} failed"
HEALTH_LLM = "LLM calls today: {n} of {cap}"
HEALTH_EGRESS = "Egress errors today: {n}"
HEALTH_POSTS = "Bot posts: {dropped} dropped in 24 h, {waiting} waiting over 30 min"

# A DM that dev mode sends to the test channel instead (the person gets nothing).
REDIRECT_PREFIX = "to {who}: "

# Answers to @Saley questions (D131, docs/09 grammar). Each line ends with a citation by name.
UNKNOWN_HAVE = "I do not have that."
UNKNOWN_NEXT = "I can search the web, or ask {person}. Which?"
UNKNOWN_PERSON = "the team"
ANSWER_FAILED = "I could not check that answer. Ask again later."
NO_MORE = "Ask a question first. Then say more."
# D141: answers about contacts are for the team on DISCORD_INSTRUCT_IDS.
TEAM_ONLY = "I can answer that for the Membrane team only."
# D141: a fact that a teammate heard from an outside person (confidence 2 or less, D108).
THEY_SAID = "(they said)"
NOTHING_NEW = "Nothing new for you."
NOTHING_FOUND = "Nothing found."
MORE_HINT = "Say more for the next {n}."
NO_NOTE = "no note on file"

# The footer is always the same four parts, joined by " · " (docs/09 per-user seen).
FOOT_WINDOW = "Window: {window}"
FOOT_SHOWN = "{n} shown"
FOOT_MORE = "{n} more"
FOOT_LEFT = "{n} left out as seen by you."
FOOT_JOIN = " · "
WINDOW_SAME_DAY = "{day} {start}–{end} IST"
WINDOW_DAYS = "{start}–{end} IST"
WINDOW_STATE = "sheet as of {time}"

# One line for each kind of item.
LINE_PERSON = "- {name} at {company}: {detail}"
LINE_P1 = "- {name} at {company}, stage {stage}"
LINE_NEWS = "- {day}: {kind}, importance {n}"
LINE_NEWS_PLAIN = "- {day}: {kind}"
NEWS_KIND = "news"
LINE_TASK = "- {text}, due {day}"
LINE_MEETING = "- Meeting with {name} at {company} on {day}"

# Citations by name, never a row or id number (D131).
CITE_SHEET = "[sheet: {person}]"
CITE_NOTE = "[note {date}, {company}]"
CITE_NOTE_OWNER = "[note {date}, {person}]"
CITE_NEWS = "[news: {title}]"

# Instructions and the action card (R4 ticket 7, D24, D129, D135). A refusal names the people who
# may do it by their short names, never by id.
NOT_APPROVER = "Sorry, only {names} can approve. I did not change anything."
NOT_INSTRUCTOR = "Sorry, only {names} can ask me to do that. I changed nothing."
NAMES_FALLBACK = "the approvers"
NAMES_AND = " and "
NAMES_JOIN = ", "
CARD_DONE = "Done. Card {n} is saved."
CARD_UNDO = "Undo on the dashboard until {t} IST."
CARD_SKIPPED = "Skipped. Card {n} is closed."
CARD_ALREADY = "Already done by {who} at {t}"
CARD_ALREADY_PLAIN = "Already done. I changed nothing."
CARD_NONE = "I do not have card {n}."
CARD_CLOSED = "Card {n} is not open."
CARD_NO_DRAFT = "Card {n} has no text I can save."
CARD_BAD_VALUE = "That value is not allowed. I changed nothing."
CARD_BAD_TEXT = "That text is not allowed. I changed nothing."
CARD_LOCKED = "That column is locked. I changed nothing."
CARD_NO_PERSON = "That person is not on the sheet. I changed nothing."
CARD_BUSY = "Busy. Try again."
CARD_FAILED = "I could not save that. I changed nothing."
CARD_NOT_ALLOWED = "Sorry, you cannot do that. I changed nothing."
NEED_REASON = "Say why. Write: reject {n} because and the reason."

# The card itself: a title, one why line and the draft text in a code block.
CARD_HEAD = "Card {n}: {title}"
CARD_WHY = "Why: {why}"
CARD_DRAFT = "Draft text:"
TITLE_MEETING = "Meeting with {name} has passed"
TITLE_LI = "{name} is connected on LinkedIn"
TITLE_OTHER = "Idea for {name}"
WHY_MEETING = "The status still says {status}."
WHY_LI = "The sheet still says {status}."
WHY_OTHER = "Saley suggests this change."
BTN_DO = "Do it"
BTN_EDIT = "Edit"
BTN_SKIP = "Skip"
BTN_DASHBOARD = "Open dashboard"
MODAL_TITLE = "Edit the text"
MODAL_LABEL = "Text to save"

# Reminders and notes from chat.
REMIND_SET = "Reminder {n} is set for {day}, from {time} IST."
REMIND_BAD_WHEN = "Say when. Example: remind me tomorrow to call Sid."
REMIND_PAST = "That time is past. I changed nothing."
REMIND_CANCELLED = "Reminder {n} is cancelled."
REMIND_NONE = "I do not have reminder {n} for you."
REMIND_TOO_MANY = "You have {n} reminders waiting. Cancel one first."
REMIND_DUPLICATE = "I already have that reminder. I changed nothing."
NOTE_SAVED = "Saved your note about {who} at {company}."
NOTE_DUPLICATE = "I already have that note for {who} today."
HINT_APPROVE = "To approve a card, write: approve 12 (the card number)."
HINT_REJECT = "To skip a card, write: reject 12 because and the reason."
HINT_REMIND = "To set a reminder, write: remind me friday to call Sid."
HINT_CANCEL = "To cancel a reminder, write: cancel reminder 3."
HINT_NOTE = "To save a note, write: note: the fact about Full Name."
NOTE_NO_PERSON = "I do not know {who}. Use the full name."
NOTE_MANY = "I know more than one {who}. Use the full name."
TEXT_MENTION = "I cannot keep a mention in the text."
TEXT_LONG = "That is too long. Make it shorter."
