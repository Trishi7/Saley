# 09 · Bot messages (v2, R7)

> Status: AGREED (23 Oct 2026, chat). This version replaces the R4 text of this file. The R4 shapes (daily brief, action card, pack, blocker note, answer, health line) stay and get the v2 look. New in v2: cards for every dashboard notification, roles and writers, Discord components, threads, deep links, chat memory for answers. Mock: `docs/18-bot-mock.html` (open it in a browser). Voice: `docs/12b-voice.md`. Build: `docs/17-bot-trd.md`, `docs/plans/R7.md`.

## 1. What the bot is
Saley in Discord is the same Saley as the dashboard. One database, one set of SQL doors, one voice. The bot does two jobs:

1. **Pings.** One Discord card for each dashboard notification (reminder, meeting, news, event, suggestion, new company, reconnect, commitment, blocker, pack, health). A press on a card is the same write as a click on the dashboard. The card edits itself when the dashboard acts first.
2. **Answers.** `@Saley <question>` in a channel, a thread or a DM. The bot reads the channel history it has stored, the sheet copy, notes and news, and answers from sources only. It never assumes.

The bot has no schedule of its own. The worker decides when a card is due. The bot delivers it, hears the press, and calls the SQL door.

## 2. People and roles
| Short | Person | Gets | May write the sheet |
| --- | --- | --- | --- |
| sid | Sid, CEO | First touches: LinkedIn connect, LinkedIn note, email 1. News. Events. Meeting briefs for his meetings. | yes |
| vaishnavi | Vaishnavi | Everything after the first touch: follow-ups, meeting prep, after-meeting steps, reconnects, commitments. News. Events. | yes |
| kushal | Kushal | Health, approvals, events. Answers. | no |
| trishi | Trishi | Health, approvals. Answers. | no |

- Writers come from `DISCORD_WRITE_IDS` (sid, vaishnavi). A press or an instruction from anyone else gets a polite ephemeral refusal that names the writers, plus an **Ask Sid / Ask Vaishnavi** button that posts the card into a thread with a mention. Nothing is written.
- Everyone in `DISCORD_USER_MAP` may ask questions. Answers about a person's own items (brief, reminders) are ephemeral unless the asker says "post it".
- `DISCORD_APPROVER_IDS` and `DISCORD_INSTRUCT_IDS` stay for the R4 instruction grammar. In v2 the writers list is the one that decides sheet writes; see D257.

## 3. Channels
| Channel | Use | Env |
| --- | --- | --- |
| saley-test-v2 | Every card and answer while `TEST_MODE=true`. `[TEST]` prefix. Health line stays here. | DISCORD_TEST_CHANNEL_ID |
| API Sales | Team cards in prod, after one week in the test channel. | DISCORD_SALES_CHANNEL_ID |
| DM | Daily brief and owner-only cards. Built, off until `DM_ENABLED=true` and the person is in `DM_USERS`. | users.discord_id |
| Threads | Long answers (> `ANSWER_THREAD_LINES` = 12 lines) go to a private thread under the question. Card overflow (people or stories beyond 5) goes to a public thread under the card. | — |

## 4. Caps and timing
- 4 cards per person per day (`BOT_POSTS_PER_PERSON_PER_DAY`). DMs and mentions count together. A channel card with no mention counts for nobody.
- 2 h gap per person (`BOT_POST_GAP_MINUTES`). Meeting briefs under 24 h skip the gap.
- Personal hours from `USER_HOURS`. Weekend silent except meeting briefs and reconnects.
- A card never drops content. Max 5 people and 5 stories in the card; the rest go to a thread under it, in one post, in the same order.
- When the cap holds cards back, the last card of the day ends with: `I am holding N more for Monday.` (or "for tomorrow"). The held cards keep their place.
- Priority when cards compete for one person: M5 meeting brief → M8 commitment nudge → M7 reconnect → M2 needs a look → M3 suggestions → M11 new company → M4 events. Reminders (M9) keep their own time.
- The daily brief (M1) and the weekly pack (M12) do not count against the cap. The blocker note (M13) follows the pack by `BLOCKER_NOTE_DELAY_MINUTES`.

## 5. Message catalogue
Each row is one card type. "Door" is the SQL function both surfaces call. Buttons: one row, at most 4, in this order: primary act, secondary act, dismiss, link. More choices → one select menu instead of buttons. Link buttons are deep links into the dashboard (section 7). The link button text names the thing ("Sujith's page", "The brief", "This week's pack"), never "Open".

| Id | Card | When | To | Shape | Buttons | Door |
| --- | --- | --- | --- | --- | --- | --- |
| M1 | Daily brief | `DAILY_BRIEF_TIME` 14:00 IST weekdays | DM, each person in `DM_USERS` | Embed, colour grey. "Hi Sid. 3 things today:" then ≤ 3 lines with the item and why. "N more on your page." | *Today page* (link) | `saley_mark_seen` |
| M2 | Needs a look | A dashboard notification of kind news/paper/signal that matters to a person (linked signal ≥ 7, or a P1 account) | Channel, mention the owner; Sid and Vaishnavi both for shared accounts | Embed, blue. Title = the story. Fields: Company, Why it matters, Source. Thumbnail from the source when it exists. ≤ 5 stories per card, rest in a thread. | *Seen it* · *Not useful* · *Sujith's page* | `saley_mark_seen`, `saley_dismiss` |
| M3 | Suggestions | Tue and Fri 14:30 IST | Channel, mention the owner | Embed, green. ≤ 5 people, each: name · company · the one reason · what Saley suggests. | *Do it* (select menu when > 1) · *Skip* · *Find people* (disabled, "coming soon, Apollo") · *Suggestions* (link) | `saley_proposal_resolve` |
| M4 | Events | Wed 15:00 IST | Channel, Sid + Vaishnavi (Kushal when he is in `EVENT_USERS`) | Embed, purple. ≤ 5 events: name · city · date · who from our list goes. "Registered" first. | *Register interest* · *Skip* · *Events* (link) | `saley_act` (event interest) |
| M5 | Meeting brief | 24 h before Meeting Date (and again 2 h before if moved) | Channel, mention who takes it | **Components v2** container: company section with thumbnail, separator, person and last 3 touches, separator, package, 3 questions, 1 risk. Colour yellow. | *The brief* (link) · *Add a note* (modal) | `saley_act` (note) |
| M6 | After the meeting | Meeting Date + 3 h | Channel, mention who took it | Embed, yellow. "How did it go with Acme?" | *Add next step* (modal: next step, date, meeting status select) · *No show* · *Acme's page* | `saley_act` (next step, status) |
| M7 | Reconnect | Reconnect Date due; weekend allowed | Channel, Vaishnavi | Embed, grey. Person · company · last touch · the reason to reconnect (from notes or news). | *Done* · *Move a week* · *Person's page* | `saley_act`, `saley_li_requested` |
| M8 | Commitment nudge | due − 1 d and due day | Channel, owner | Embed, grey. "When you have a moment: <commitment>, due <date>." | *Done* · *Move date* (modal) · *Commitments* (link) | `saley_commitment_done` |
| M9 | Reminder | reminder.fire_at | Channel or DM, owner | Plain line with one embed field. | *Done* · *Snooze a day* | reminder state |
| M10 | One-click idea | Saley sees one step that fits one press (e.g. LinkedIn request sent 3 d ago → "Did Sujith accept?") | Channel, Sid for first touches, Vaishnavi after | Embed, green. One line, one idea. | *Yes, connected* · *Not yet* · *Sujith's page* | `saley_li_requested`, `saley_act` |
| M11 | New company | Wed 15:30 IST, when a new account entered the sheet this week | Channel, Sid + Vaishnavi | Embed, blue. Company · why it fits · who we know · first step. | *Take it* (select: Sid / Vaishnavi) · *Skip* · *Company page* | `saley_act` (owner) |
| M12 | Weekly pack | Mon 14:00 IST, pinned, previous pin removed | Channel, no mention | **Components v2**: scoreboard RAG, funnel delta, top 3 actions, one plan idea. Colour grey. | *This week's pack* (link) | — |
| M13 | Blocker note | Mon 16:00 IST | Channel, mention each owner once | Embed, red. ≤ 5 late items: owner · item · what goal it blocks. Never "overdue". | *Blockers* (link) | — |
| M14 | Ask | Worker needs a fact | Channel, the person | "Quick one: <question>" | *Answer* (modal) | fact(source=teammate) |
| M15 | Health | 23:45 IST | Test channel | One embed, green when ok, red when a job failed. Jobs, LLM calls vs cap, egress errors, cards sent/held, "DMs off". | *Sources & health* (link) | — |
| A1 | Answer | `@Saley <question>` | Where asked | Answer first, citations by name, then the window line. > 12 lines → private thread with the first 3 lines in the channel. | *More* · *Dismiss* | `saley_ask_log`, `saley_mark_seen` |
| A2 | Refusal / cannot | Non-writer press, unknown question, data missing, Postgres down | Ephemeral | One kind line that says what Saley cannot do and who can. | *Ask Sid* / *Ask Vaishnavi* when a writer can | — |

Out of R7: email drafts and email sending (next week, D258). The R4 action card with the draft in a code block stays for proposals that are not email.

## 6. Card behaviour
- **Act once.** A press calls the door with `surface='discord'`, the presser's user id and the card id. The door returns `ok`, `already` (with who and when) or `refused`. The bot edits the card: buttons go, footer says `Done by Sid in Discord at 11:24 · Undo 4 min left`. After `UNDO_MINUTES` the footer loses the undo part.
- **Sync.** When the dashboard acts on the same item, the worker sends `NOTIFY saley_card` with the card id. The bot listens and edits the card within 5 s: `Done by Sid on the dashboard at 11:24 · Undo 4 min left`. Undo on either side brings the buttons back.
- **Old card.** A press on a card whose item is already done answers ephemeral "Already done by Sid at 11:24" and edits the card.
- **Reactions** are read-only. 👀 means nothing to the data.
- **Pins**: only the weekly pack. The bot unpins its previous pack first.
- **Edits keep the embed.** The bot never reposts a card to change its state.

## 7. Deep links
- Form: `PUBLIC_URL/<page>/<id>?card=<card_id>`. Examples: `/people/412?card=d881`, `/today?card=p12`, `/pack/2026-W43`.
- Sign-in keeps `next` on the same site only. After sign-in the page opens and scrolls to the card, which is outlined for 3 s.
- On the laptop `PUBLIC_URL` is `http://localhost:8090`; on the server `https://saley.membrane.social`. The bot process reads `PUBLIC_URL` only.

## 8. Answers (A1)
- Sources, in order: chat memory (this channel, 90 days), sheet copy views, notes, news, events. Never the open web unless the asker says "search".
- Citations by name: `[sheet: Sujith]`, `[note 14 Oct, ARTPARK]`, `[news: <title>]`, `[Vaishnavi, Tue 12:40]` for a chat line. Never a row or id number.
- Never a guess. Missing → "I do not have that. Sid might, or I can search the web. Which?"
- Personal data in an answer (phone, personal email) is ephemeral only.
- Long answer (> `ANSWER_THREAD_LINES`): first 3 lines in the channel, the rest in a private thread with the asker; the channel line says "the rest is in our thread".
- Window line last: `Window: Fri 17 Oct 00:00–14:10 IST · 5 shown · 12 more · 0 left out as seen by you.`
- Chat memory: every message in the channels the bot is in goes to `chat_message` (author short code, time, text, reply-to, thread). A nightly job writes `chat_summary` per channel-day and pulls `chat_fact` lines (who said what about which company). Retention `CHAT_RETENTION_DAYS` = 90. Backfill on first start: the last 14 days of each channel, once.

## 9. Discord features the bot uses
Embeds (author, title, fields, thumbnail, footer, colour by type), Components v2 (container, section with thumbnail, separator, media gallery) for M5 and M12, buttons (primary blue, secondary grey, success green, danger red, link grey with ↗), select menus, modals (M6, M8, M14, "Add a note"), ephemeral messages, public and private threads, message edit, pin and unpin, scheduled events (M4 may create one when the team registers), reactions read-only.

Colours: news blue `#3B82F6`, event purple `#8B5CF6`, blocker red `#EF4444`, suggestion green `#22C55E`, meeting yellow `#EAB308`, pack and brief grey `#6B7280`, ok green, cannot red.

## 10. Rules that do not change
- Column locks by role and the approval model (D24) apply to every press.
- The bot container has no LLM key and no sheet key (docs/17 §3). Card text is built by the worker. Answers are built by the worker too; the bot only carries the question and the reply.
- No invented values. Every number, name, date and link in a card comes from a row.
- STE words from `docs/ste-banned-words.txt`; the extra bot words are in `docs/12b-voice.md`.
