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
HEALTH_SKIPPED = "Card runs with a skipped part in 24 h: {n}"

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
# R7 (D262): a chat line, cited by first name and time: "[Vaishnavi, Tue 12:40]".
CITE_CHAT = "[{who}, {day} {time}]"
LINE_CHAT = "- {text}"
HIDDEN = "(not shown in the channel)"

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
BTN_DASHBOARD = "The Inbox"  # D261: a link names the thing
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

# ------------------------------------------------------------------ R7 cards (docs/09 §5, 12b)
# Every card ends with this line, except M15 health, A2 refusals and thread posts (12b rule 4).
CLOSING = "Let me know if you want anything else from me."
THREAD_HEAD = "The rest, in the same order:"
THREAD_NOTE = "{n} more are in the thread under this card."
THREAD_NOTE_ONE = "1 more is in the thread under this card."
HELD_MONDAY = "I am holding {n} more for Monday."
HELD_TOMORROW = "I am holding {n} more for tomorrow."
LINK_PAGE = "{name}'s page"

# The footer after a press or a dashboard act (docs/09 §6). Edits keep the embed.
DONE_DISCORD = "Done by {who} in Discord at {t}"
DONE_DASHBOARD = "Done by {who} on the dashboard at {t}"
UNDO_LEFT = "Undo {n} min left"
BTN_UNDO = "Undo"
DONE_UNDONE = "Undone by {who}. The buttons are back."
ALREADY_BY = "Already done by {who} at {t}."
WINDOW_CLOSED = "The undo window closed at {t}."
WINDOW_NEW = "I can make a new change if you tell me."
DATA_DOWN = "I cannot reach our data right now."
DATA_BACK = "I will tell you when it is back."
BAD_DATE = "I need a date like 28 Oct."
BAD_PICK = "That choice is not on the card. I changed nothing."
NOT_ON_SHEET = "No longer on the sheet."
MOVED_TO = "Moved to {day}."
PRESS_FAILED = "I could not do that. I changed nothing."
SAVED_PRIVATE = "Saved. Thanks, {who}."

# A2: a press from someone who is not a writer (D257). Ephemeral, kind, names who can.
REFUSE_WRITE = "I can only change the sheet for {names}."
REFUSE_OFFER = "I can ask one of them for you."
BTN_ASK = "Ask {name}"
ASKED_IN_THREAD = "I asked {name} in a thread under the card."
ASK_THREAD = "{who} asks you to look at this card, {name}."
ASK_THREAD_NAME = "A question about this card"
CANNOT_ANSWER = "I do not have that. {person} might, or I can search the web."
CANNOT_WHICH = "Which do you want?"

# M1 daily brief (DM).
BRIEF_HI = "Hi {name}, good afternoon. {n} things today:"
BRIEF_HI_ONE = "Hi {name}, good afternoon. 1 thing today:"
BRIEF_AUTHOR = "Your day · {day}"
BRIEF_MORE_PAGE = "{n} more on your page."
LINK_TODAY = "Your Today page"

# M2 needs a look.
LOOK_HEAD = "{who}, {n} stories that matter for us:"
LOOK_HEAD_ONE = "{who}, a story that matters for us:"
LOOK_AUTHOR = "Needs a look · {company}"
FIELD_COMPANY = "Company"
FIELD_WHY = "Why it matters"
FIELD_SOURCE = "Source"
BTN_SEEN = "Seen it"
BTN_NOT_USEFUL = "Not useful"
LINK_SIGNALS = "Signals"

# M3 suggestions.
SUGGEST_HEAD = "{who}, {n} people we could reach this week:"
SUGGEST_HEAD_ONE = "{who}, one person we could reach this week:"
SUGGEST_AUTHOR = "Saley suggests"
SUGGEST_LINE = "{reason} · I suggest: {step}"
BTN_DO_IT = "Do it"
SELECT_DO = "Do it for one person"
BTN_FIND_SOON = "Find people · coming soon"
LINK_SUGGESTIONS = "Suggestions"

# M4 events.
EVENTS_HEAD = "{who}, {n} events are coming up. Registered first."
EVENTS_HEAD_ONE = "{who}, one event is coming up."
EVENTS_AUTHOR = "Events"
EVENT_LINE = "{city} · {day}"
EVENT_REGISTERED = "registered"
EVENT_WHO = "Who goes: {who}"
BTN_INTEREST = "Register interest"
SELECT_INTEREST = "Register interest in one event"
LINK_EVENTS = "Events"

# M5 meeting brief (Components v2).
MEET_HEAD = "Hi {who}, you meet {name} on {day}."
MEET_HEAD_MORE = "Here is what we have, so you can walk in relaxed."
MEET_TITLE = "{company} · {name}"
MEET_TOUCHES = "Last touches"
MEET_PACKAGE = "Package: {package}"
MEET_QUESTIONS = "Questions we could ask"
MEET_RISK = "One risk: {risk}"
RISK_NO_DEAL = "no deal value on the sheet yet."
RISK_NO_EMAIL = "no email on the sheet yet."
RISK_NO_PACKAGE = "no package on the sheet yet."
BTN_NOTE = "Add a note"
LINK_BRIEF = "The brief"
MODAL_NOTE = "A note about {name}"
MODAL_NOTE_LABEL = "Your note"

# M6 after the meeting.
AFTER_HEAD = "How did it go with {company}, {who}?"
AFTER_ASK = "Add the next step when you have a moment."
AFTER_OFFER = "I will put it on the sheet."
BTN_NEXT = "Add next step"
BTN_NO_SHOW = "No show"
MODAL_NEXT = "Next step · {name}"
MODAL_NEXT_LABEL = "Next step"
MODAL_DATE_LABEL = "Date"
MODAL_DATE_HINT = "28 Oct"
MODAL_STATUS_LABEL = "Meeting status"

# M7 reconnect.
RECONNECT_HEAD = "{who}, it is time to reconnect with {name} ({company})."
RECONNECT_LAST = "Last touch: {touch}"
RECONNECT_WHY = "Why now: {reason}"
RECONNECT_ASK = "Do you want to reconnect this week?"
BTN_DONE = "Done"
BTN_WEEK = "Move a week"

# M8 commitment nudge.
NUDGE = "When you have a moment: {text}, due {day}."
NUDGE_NO_DAY = "When you have a moment: {text}."
BTN_MOVE_DATE = "Move date"
LINK_COMMITMENTS = "Commitments"
MODAL_DUE = "A new date"

# M9 reminder.
REMIND_HEAD = "{who}, a reminder for you:"
FIELD_REMINDER = "Reminder"
BTN_SNOOZE = "Snooze a day"

# M10 one-click idea.
IDEA_LI = "{who}, you sent {name} a LinkedIn request. Did they accept?"
IDEA_MEETING = "{who}, did the meeting with {name} happen?"
IDEA_OTHER = "{who}, one idea for {name}:"
IDEA_AUTHOR = "Saley's idea · one press"
FIELD_WRITES = "Writes"
BTN_YES_CONNECTED = "Yes, connected"
BTN_YES_HAPPENED = "Yes, it happened"
BTN_YES = "Yes, do it"
BTN_NOT_YET = "Not yet"

# M11 new company.
NEWCO_HEAD = "{company} is new on the sheet this week."
NEWCO_AUTHOR = "New company"
FIELD_FIT = "Why it fits"
FIELD_KNOWN = "Who we know"
FIELD_FIRST = "First step"
BTN_TAKE = "Take it"
SELECT_TAKE = "Who takes it?"
NOBODY_YET = "nobody yet"

# M12 weekly pack (Components v2).
PACK_HEAD = "Hi team, here is where we stand this week."
PACK_TITLE = "Week of {day}"
PACK_LINE = "{rag} {label}: {value}"
PACK_LAST = "(last week {n})"
PACK_FUNNEL = "Funnel this week: {delta}"
PACK_TOP = "Top 3 this week"
PACK_IDEA = "One idea: {idea}"
LINK_PACK = "This week's pack"

# M13 blocker note.
BLOCKER_HEAD = "Late items that block goals this week:"
BLOCKER_LINE = "{owner} · {item} · blocks {goal}"
LINK_BLOCKERS = "Blockers"

# M14 ask.
ASK_LINE = "Quick one, {who}: {question}"
BTN_ANSWER = "Answer"
MODAL_ANSWER = "Your answer"

# M15 health (no closing line).
HEALTH_AUTHOR = "Health · {day}"
FIELD_JOBS = "Jobs in 24 h"
FIELD_JOBS_VALUE = "{ok} ok · {failed} failed"
FIELD_LLM = "LLM calls today"
FIELD_LLM_VALUE = "{n} of {cap}"
FIELD_EGRESS = "Egress errors"
FIELD_CARDS = "Cards today"
FIELD_CARDS_VALUE = "{sent} sent · {held} held · {failed} failed"
FIELD_PRESSES = "Presses today"
FIELD_PRESSES_VALUE = "{ok} ok · {already} already · {refused} refused"
FIELD_LISTEN = "Card sync"
FIELD_SKIPPED = "Card runs with a skipped part"
FIELD_DMS = "DMs"
LINK_HEALTH = "Sources & health"

# A1 answer.
BTN_MORE = "More"
BTN_DISMISS = "Dismiss"
REST_IN_THREAD = "The rest is in our thread."
ANSWER_THREAD = "Your answer, {who}"
