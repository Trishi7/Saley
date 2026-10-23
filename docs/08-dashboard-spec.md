# 08 · Dashboard spec

Base: docs/07-dashboard-mock.html. This file is the contract for `saley/web`. One view = one endpoint = one SQL view. Every line carries an evidence link. Every list shows "last computed".

## Who it is for
Sid and Vaishnavi use this dashboard instead of the sheet. Saley is the only writer of the sheet. Kushal views and decides. Trishi is admin. Every word on the page must make sense to Sid and Vaishnavi at a glance: no rule ids, no week numbers, no table names. Say "last week 91", not "W41: 91".

## Shell
- Left rail: Run the plan (Today, Q4 goals, Accounts, People, Suggestions) · Watch (Signals, Blockers, Events) · Team (Inbox, Commitments, Decisions, Weekly pack, Scorecard) · Sources and health (admin). Money blocked and Search are pages without a rail link: the Blockers tile and the global search open them. Below 768 px the rail folds into a menu Button that opens the same links in a left drawer (D198).
- Top bar on every page: Saley, global search ("Search everything: a company, a person, a city, a news story"), "Updated HH:MM IST · sheet HH:MM · news HH:MM", the theme toggle (light or dark; the default follows the system and the choice stays in the browser, D163), the signed-in email, Sign out. Nothing else (D207): no "View as" and no "Viewing as" person tabs, in any mode. Every page shows the signed-in person's own data; Today asks `/api/today` with no `?user=`. A test that needs another person sends the `X-Saley-As` header with the mock provider only (D50, D116); there is no UI for it.
- A failed load names the data and the reason, with a Retry button (a button, `data-retry`): "Could not load {what}." plus "The server did not answer." for a network error, or the server's own line for an HTTP error. Never the raw browser text ("Failed to fetch"). After a network error the Updated line tries again by itself every 30 s, so a server restart heals it.
- The Updated bar turns red (D43): "Worker not running since HH:MM" when no sync started in 3 × SHEET_SYNC_SECONDS; "Sheet sync failing since HH:MM" when syncs run but none succeeds; "Old data: tab … not read" when a tab is stale (D41). On a laptop, `saley dev` runs web and worker in one process on `WEB_PORT` (default 8090).
- Search (R5, D213): global search in the top bar opens the Search page: people, companies, goals, deliverables, decisions, with links. Only Accounts ("Find a company") and People ("Find a person") keep a page search box. Today and Q4 goals have none (R5 brief). The other pages filter with toggle Buttons.
- Every number tile is a link. It opens the tab with the filter set. Every tile carries a `source` (the sheet tab or table it counts). A number that is not known shows "—", never 0; a tile whose sheet tab was never read says so.
- Subtitle under every page title: one plain sentence for Sid and Vaishnavi (see mock).
- Design rules on every page (R5 brief, D197, D201): numbers first, as tiles or charts; lists second. Every internal link is a Button or a clickable card, never an underlined URL. A legend at the top of the page explains every tag and colour on it (see "Legends"; it stays open on the phone, D230). Long lists show 10 a page with a pager and an "N more" count. One colour per stage, the same on every page (D201, D203). Dark and light themes both.
- Off features (D164): `/api/me.features` holds `find_people` (`FIND_PEOPLE_ENABLED`), `email_write` (`EMAIL_WRITE_ALLOWED`), `speaker_match` (off: S12 is not built), `apollo` (`APOLLO_ENABLED` and a credit cap above 0), `mail_replies` (mail set up), `dms` (`DM_ENABLED`). Where an off feature lives, the page shows a "Coming soon" badge in its place, and the server refuses its act.
- Phone (below 640 px): every Button, field and select is at least 44 × 44 px (D232). Wide tables move the key number next to the name (D235).
- Auth: Google sign-in. `WEB_ALLOWED_EMAILS`. Roles from `users.role`: viewer, approver, admin. Approver sees decide buttons. Admin sees Sources and health.
- Dev: `SALEY_ENV=dev` also needs a session. On loopback the mock provider signs in as `DEV_USER_EMAIL` (D116).
- Laptop first. Phone readable. No horizontal scroll except tables.
- Stack (R5, D144, D145, D224): FastAPI serves `/api/*` JSON and, at `/`, the dashboard app: React 19 + TypeScript + Tailwind, built by Vite from `web/` into `src/saley/web/dist/` (served under `/static/app/`). `/` needs a session (else 303 to `/signin`) and puts a fresh CSP style nonce into the page and the header (D146). Hash routes (`/#today`, `/#people?filter=all`) pick the view. The sign-in and message pages are server HTML with one stylesheet, `/static/signin.css`, which copies the app tokens (D234). No Node at runtime: Node 24 builds the app (laptop, CI, the Docker web stage). Each page is its own lazy chunk (D233). Components: shadcn/ui and the bklit funnel chart, vendored as source; fonts Geist and Geist Mono, bundled (no CDN; D148, D188, D189). The legacy static app (plain HTML + JS files) and `/next` were removed at the cut-over (R5 T16b, D224).
## Writes and the approval model (decision D24)
- An instruction from an approver IS the approval. A button click on the dashboard or a chat instruction writes at once. Nothing waits for a second yes. Nothing is asked twice across chat and dashboard.
- Saley's own ideas (rules, suggestions) show once as a card with one primary button. The click writes. They never pile up in a queue.
- Every write stores a signature (user, time, surface) in `decision` and an `undo_until` = now + UNDO_MINUTES (default 5, D143; was 10). An Undo button shows until then. Undo reverts the cell and logs it.
- Endpoints: `POST /api/act` with `{action, target_ids[], payload?}` for task ticks, one-click cards, add-to-pipeline, mark done, fill industry. `POST /api/decision/{id}/confirm` for strategy decisions. `POST /api/undo/{write_id}`. All require role approver (Kushal, Sid, Vaishnavi) or admin.
- The Inbox holds only: Saley's open questions to the team, the team's questions to Saley, reminders, and strategy decisions that need a confirm. Never a write the human already asked for.

## Views

| # | View | Endpoint | SQL view | Fields | Filters | Actions | Reqs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Today | GET /api/today?tasks_offset= | v_today_user, v_contact_stage | **Order (R5): legend → where we stand → your tasks → needs a look → Saley suggests → Ask.** Where we stand: a funnel chart with counts: In the list → Contacted → Connected → Talking now → Met → Won (a person counts at each stage reached or passed, D170); each bar opens People with its filter. Then 4 tiles, each a link (D202): Deals closed this quarter (of 3), Connected, no email yet, P1 waiting on LinkedIn, New this week (vs last week). Inside the tile area, one blockers line: "N late items block goals" with a Button "Open blockers". Your tasks today: built per signed-in person from the rules due today (R05 pick 5, R06 find email, R08 brief, R09 next steps, R10 deal ask, R13 email/research/DM/call steps, R14 reconnect, commitments due, decisions to confirm); 10 a page with a pager and "N more"; order and due days in "Today: task order, Done and Undo" below. Done writes the sheet cell(s) with a value the column already holds and logs the signature (D153); the same button then becomes Undo in place for `UNDO_MINUTES`, with no timer text and no toast (D204); a reload keeps the Undo. A step-block task records the tick and writes no step cell (D49); a booked meeting past its date is a Sid task (D59). Add email (was "find an email"): no Done; the Button opens the person drawer at the Email field; the task leaves the list when a valid email is saved (D154, D169, D181). Needs a look (D158, D172, D173): whole-card links, no "Open" link, each with a type badge: News (linked story, importance 4 or more), Event (3 days or less away), Blocker (top 3 late items that block a goal), Waiting (your Inbox ask over 24 h). Prospect replies show "Coming soon" while mail is off. The old "Blocked or waiting" card is removed (it only redirected). Saley suggests: up to 3 new companies and people, each one line, for example "Add Priya Rao (Sarvam) to pipeline — hiring for evals" (template checked by the value guard, D173, D217); Find people shows "Coming soon" while it is off (D164). The Ask card is at the foot (R4). | none: the signed-in person (D207) | Done, Undo, Add email (Save), open a card or a bar, Ask | S03 S22 S42 S36 R05 R06 R08 R09 R10 R13 R14 |
| 2 | Q4 goals | GET /api/goals | v_goal_status | One uniform card per goal (D212): header (code · value band · due day, goal text, state tag On track / At risk / Blocked, days left, the why line) → a ring of won ÷ need ("—" without a need; over 100 % shows the real number; D159, D171, D211) → the people as a compact table (name · company · stage badge · one-line note, cut with a tooltip; first 4 rows) → the blockers list (first 3 rows, sorted by days late). "+N more" is a Button that opens the rest in place. Weighted pipeline in USD, or a note. | none (no page search) | none | S03 S04 S54 R04 |
| 3 | Accounts | GET /api/accounts?filter=&q= | v_account_coverage | company, industry, goal, "13 people · 9 P1" with a stage bar of the people per stage (`stage_counts`), tags Qualified, Engaged, Single-threaded (D174), contacted, connected, emailed, coverage %, warmth. Industry bars on top. Company drawer: people (name, title, stage; no Evidence column), the touch timeline (one row per month, newest first, 12 months, a dot per touch type, the detail on hover; at most 200 touches, D175), stage history. | all · p1 · has_connection · goal:Gn · industry:X · search ("Find a company", D213) | open the company drawer; open a person | S15 S49 R13 R10 |
| 4 | People | GET /api/people?filter=&q= | v_contact_stage | name, company, title, stage badge, working_on + source + date, email status, next step, priority, city. Person drawer (one component for Today, Accounts and People, D214): the real email as a `mailto:` link (only when the cell holds an address), else the Email field with Save (behind `EMAIL_WRITE_ALLOWED`); LinkedIn and website as https links in a new tab; "Follow-up by" (was Owner) with the tooltip "from rule D31" (D176); research papers as "N papers" with a popover of short links (title, cut; never invented); "Working on" with its source (the sheet column name or "note <date>"); the same touch timeline as Accounts; empty = "None on file" (D39). | backlog · p1_waiting · in_conversation · reconnect_due · stage:X · search ("Find a person", D213) | open the person drawer; save an email | S29 S14 R06 R13 R14 |
| 5 | Suggestions | GET /api/suggest/{warm,best,newco,res}?view=new\|seen | v_suggest_* | New and Seen earlier tabs with counts, 10 a page (D157, D216). Per row: one line of what Saley suggests (D217), subject, reason, via, evidence. Find people shows "Coming soon" while `FIND_PEOPLE_ENABLED` is false; Apollo the same (D164). | kind tab, New or Seen earlier | Add (a prefilled form → row_add; the Button becomes Undo in place), Find people when on | S07–S10 R02 R05 R11 |
| 6 | Signals | GET /api/signals?tab=all\|linked&range=&cat=&src=&tag=&view=new\|seen | v_signal_all, v_signal_linked | New and Seen earlier tabs with counts, 10 a page; an empty New tab says "Nothing new since your last visit" (D157, D216). All: headline, outlets, source, category, tag, importance, impact, published · Linked: + entity, action hint, state. Categories by name: Hiring, Funding, Product launch, Research, Partnership, Opportunity, Event (never letters; D156, D177, D239: Opportunity is checked just before Event). | range, category, source, tag, entity | Linked: Add to action · Dismiss · Ask | R01 |
| 7 | Blockers | GET /api/blockers | v_deliverable_late | item, owner, due, late by, blocks (goals, deals, motions), status, money blocked. Goal links come from the goal's Dependencies text, or from docs/goal-deliverable-map.yaml for the goals listed there (D34). Money blocked (D42): a tile on this page (Today keeps 6 tiles) and a list at GET /api/money-blocked: deal · company · deal value · blocking deliverable (+N) · owner. A deal-size band counts its midpoint ("USD 30–50k" → "≈ USD 40k (USD 30–50k)"); an exact value counts as given; no value = "No value on file", never 0. Total = sum of the list. | none | Remind (creates a polite ask) | R04 R12 |
| 8 | Events | GET /api/events?when=&view=new\|seen | v_event_targets | New and Seen earlier tabs (D216). Columns: event, City (Bangalore, London or Other), Venue (the full address), dates, days left, registered (a badge), people in city, connected, P1, speaker matches ("Coming soon"). Registered events first, then by start date (D160, D175). | upcoming · past | none (the "Build target list" act is not built, D218) | R03 S11 S12 |
| 9 | Inbox | GET /api/inbox | ask, reminder, decision(proposed) | Saley's questions to the team with age, team questions to Saley, reminders, strategy decisions to confirm. No writes the human already asked for. | type (`card`, `confirm`, `ask`, `reminder`) | Confirm and Dismiss are built in R2. Answer and Snooze are not built. A reminder leaves the Inbox after its due day (D63). A "LI likely connected" card writes on one click (D60). | S35 S36 S28 S45 |
| 10 | Commitments | GET /api/commitments | commitment | owner, text, due, source, state, reminders sent | owner, state (`open`, `done`, `dropped`, `all`; default `open`) | Mark done: only the owner (D155). Anyone else gets the toast "This is <name>'s commitment. Only they can mark it done." and nothing is written. Done is signed; the Button becomes Undo in place for `UNDO_MINUTES`, also after a reload (D220). Done skips the reminders still waiting; Undo puts them back (D66). | S42 |
| 11 | Decisions | GET /api/decisions | decision | id, text, state (proposed/confirmed/active), approver, time, surface, applies to | person, surface, undo (`open`, `undone`, `undo_failed`, `closed`), state | Confirm (approver) on a proposed strategy decision. Undo on a column write, task tick or commitment Done while its undo window is open. A failed sheet write shows its state and a fixed reason. | S45 |
| 12 | Weekly pack | GET /api/pack?week= | metric_snapshot + views | scoreboard, funnel vs last week, motion table target vs actual, backlog, slips, next 10, one plan suggestion, 1:1 agenda | week | none | S31 S44 S05 |
| 13 | Scorecard | GET /api/scorecard | v_segment_scorecard | per industry: people, contacted, connect %, reply %, meetings, deals; 7-day delta | none | none | S57 |
| 14 | Sources and health (admin) | GET /api/health/full, GET /api/admin/news/run | job_runs, egress_log, llm_calls, news_* , parity_checks, v_news_run_request | Run news now: a Button (admin) that queues one news edition, with its state line (queued, running, done, failed); one open request at a time, a 10-minute cooldown (D150, D151, D178). Jobs: last run, status, note. News today as a funnel: sources tried, answered, items pulled, dropped seen / duplicate / not relevant, stored, scored, scored ≥ 4, about our people, sent. Each step opens the per-source card "Sources, last 24 hours" (D225). Failed or slow sources with problem, since, fallback. Parity: last hourly check of dashboard vs sheet vs news tables (row counts, stage counts, last story time) with red on mismatch. Sheet tabs: last read, stale tabs with the missing header in red, and unknown headers as "new columns" (D41). Discord (R7, docs/17 §10): cards sent, held and failed today, presses, the last card edit, chat memory. | none | Run news now; re-run parity | S33 S72 |

## R4 view notes
- Weekly pack (view 12): the page is `/#pack`, under Team in the left rail. It reads `GET /api/pack`. A number that is missing shows "—", never 0.
- Today (view 1): the Ask card sits at the foot of Today. One typed question goes to `POST /api/ask`. The answer is plain text: up to 5 items with citations by name, then "more". Questions only, no action (D119, D131).
- Scorecard (view 13): the "Last week" column shows each industry's value from the snapshot 7 days back. No snapshot for that industry shows a dash, never 0 (D118).
- Inbox (view 9): an action card with one draft cell has an inline Edit. Save calls `POST /api/proposal/{id}/edit`, signed, with the undo window. The first saver wins; the other gets "Already done by X at HH:MM" (D129, D137).

## R5 view notes (dashboard v2)
- Every page: title, one-line subtitle, the legend, then numbers, then lists. A failed load says "Could not load {what}." with the reason and a Retry Button (see Shell). A page chunk that does not load shows the same line with a Retry that reloads the page; the rail and the top bar stay (T17).
- No "View as" anywhere (D207). Today's tasks are always the signed-in person's.
- Today: see view 1. Done → Undo in place, never a bottom bar (D204). Add email is live when `EMAIL_WRITE_ALLOWED` is true (D181); when it is false the Email field is disabled and shows "Coming soon" (D154).
- Q4 goals, Accounts, People: see views 2–4. Accounts and People keep their filters as toggle Buttons whose value lives in the hash, for example `#accounts?filter=p1` (D213).
- Signals, Suggestions, Events (Gmail model, D157, D216): the New tab lists what this person has not seen, 10 a page; after a New page is drawn the page marks those items seen and fetches the New tab again from the start. The Seen earlier tab holds the rest. The tab labels show the counts. No "N you saw are left out" line.
- Blockers and Money blocked: the Money blocked tile on Blockers opens the Money blocked page (D42).
- Commitments: owner-only Done (D155), see view 10.
- Inbox, Decisions: the same POSTs and `data-act` names as R2–R4 (D220); one count per kind (D221).
- Weekly pack: with no pack at all the page says "No weekly pack yet. The first comes on Monday at 14:00."; the API answers 200 with `empty: true` (D229).
- Sources and health: Run news now, see view 14.

## Legends (R5, D199, D230)
Each page shows its legend at the top. The legend lists every tag and colour that page uses. "—" means "Not known yet. Never shown as 0." "Coming soon" means "This part is off for now."

| Page | The legend lists |
| --- | --- |
| Today | the funnel stages (In the list, Contacted, Connected, Talking now, Met, Won) · task kinds (Meeting, Reconnect, Email, LinkedIn, Promise, Team) · Late, Due today, Due later, No date · card types (News, Event, Blocker, Waiting) · — · Coming soon |
| Q4 goals | On track, At risk, Blocked · Ring · Stage and the stages (In the list, Contacted, Connected, Talking now, Met, Qualified, Won, Parked, Lost) · Late, Due today, Due later · — |
| Accounts | Qualified, Engaged, Single-threaded · Stage and the stages · Touches · — |
| People | P1 · Stage and the stages · Touches · — |
| Suggestions | New · Coming soon |
| Signals | Hiring, Funding, Product launch, Research, Partnership, Opportunity, Event · Ours · Important · Check the match · New |
| Blockers | Late · Complete · — |
| Events | Registered · New · Coming soon |
| Inbox | Action card, Confirm, Question, Reminder · — · Coming soon |
| Commitments | Open, Done, Dropped · Late, Due today, Due later, No due date · — |
| Decisions | Proposed, Confirmed, Active · Can undo, Undone, Window closed, Undo failed · Waiting for the sheet, Held: sheet writes off, In the sheet, Sheet write failed, Cancelled · — |
| Weekly pack, Scorecard | Up, Down · — |
| Sources and health | OK, Failed, Old, Running, Queued, Skipped · — · Coming soon |
| Money blocked | Late · Stage and the stages · — (no value on file) |
| Search | Stage and the stages · Goal |

The words come from `web/src/words.ts`; the entries per page from `web/src/components/saley/views.ts`. A tag on a page that is not in its legend fails the browser check (D199).

## Today: task order, Done and Undo (R5 T3, D152, D153, D143)
- Order: due day, then importance, then PoC Priority (AJ: P1, P2, P3, blank), then company, then name. A task with no due day does not count as today: it ranks after every task dated today, before tomorrow's, and shows "No date" (D180). li_confirm and li_first are due today by the rule below, so they keep that day. Importance: meeting and status_check 1 · reconnect 2 · email step (add_email, email1 to email3) 3 · LinkedIn (li_confirm, li_first) 4 · commitment 5 · decision, review_write, inbox_cards 6.
- Paging: `GET /api/today?tasks_offset=` (0 to 1000, else 422). A page is 10 tasks. `my_tasks` has `offset`, `more` (total minus shown minus offset, never below 0) and `more_label` ("12 more", or null).
- Done value: the client sends the task name only (`payload.done`). The server picks the value from the cells the column already holds (view `v_column_vocab`). Meeting Status: the first of Completed, Done, Met, Held that the column holds, in the column's own spelling. First Contact: TRUE. First Contact Type: LI or LinkedIn, the form the column uses more. Dates: today as an ISO date. If the column holds no fitting value, the answer is 409 "This column has no done value yet." and nothing is written. Reconnect and the email steps write no step cell (D49).
- Undo in place: a Done answer carries `write_id` and `undo_until`. `/api/today` keeps a ticked task on the list while its tick is inside the window: `done` is true, `action` is null, and `undo_id`, `undo_ids` and `undo_until` are set. `UNDO_MINUTES` is 5 (D143).

### Due-date rule
| Task | Due day | Sheet columns read |
| --- | --- | --- |
| meeting | today | Meeting Date (AA) = today, Meeting Status (AB) open |
| status_check | Meeting Date | Meeting Date (AA) before today, Meeting Status (AB) booked (D51, D59) |
| reconnect | Reconnect Date | Reconnect Date (AC) (D14, D36) |
| add_email (research) | LI Connected Date + 4 days | LI Connected Date (O); Email id (G) blank; LI DM Sent/Date (Y, Z) blank |
| email1 / 2 / 3 | anchor + 2 / 7 / 3 days | anchor = last step date (the latest date in the row), else First Contact Date (M) (D51, D58) |
| li_confirm | today | First Contact Type (L) or Sid - LI Addition (N) shows a request; LI Connected Date (O) blank |
| li_first | today | First Contact (K), Type (L), Date (M) blank; Stage Listed |
| commitment | commitment due | DB (meeting notes, Discord), not the sheet |

The rules read these columns by role, never by letter. The letters are for the reader only.

## Shared rules
- Empty state text per view: "No items. Last computed HH:MM." Never a blank panel.
- Evidence link: `GET /api/evidence?fact=&signal=&rule=&row=` opens a drawer with the source (sheet row key, story URL, rule text, fact author and date).
- Numbers: tabular. Dates: IST, "Fri 17 Oct". Money: USD.
- Per-user seen applies to the dashboard too (D75), as it does to bot answers. Signals, Suggestions and Events show a New tab (what this person has not seen, 10 a page) and a Seen earlier tab, each with its count (D157, D216). No "left out" line on the dashboard; the bot footer keeps it. Seen is per person, never per channel.
- Owner of a person (which list a task lands on), decision D31 (replaces D25): "VR" / "Sid" text in `First Contact` names the owner; else a LinkedIn request recorded in `Sid - LI Addition` → Sid; else `First Contact Type` = LI → Sid; else Vaishnavi. A human can reassign from the person drawer.
- Goal links (D30): until the sheet has a Goal column, people link to goals by industry. The link source is shown as "industry rule".
- Parity: a `parity_check` job runs hourly. It compares view counts with the sheet copy and the news tables. The result shows on Sources and health. Claude Code runs the `dashboard-parity` skill before any dashboard release.
- Stale: a view older than 15 min shows an amber stamp.
- No caps on the dashboard. Caps are for the bot.

## Releases
- R1: shell, sign-in (dev mode), global and page search, last-updated bar, Today numbers only, views 2, 3, 4, 7, 13 read-only, view 14 with jobs and parity. Read from the sheet copy.
- R2: Today "my tasks" per person with Done writes and undo, Inbox, Commitments, Decisions, People drawer with "working on".
- R3: Signals All + Linked, Events, Suggestions with add-to-pipeline, "needs a look" and "Saley suggests" on Today. News health funnel.
- R4: Weekly pack, Scorecard deltas, Google sign-in for real users, @Saley answers in Discord and the Ask card on Today (same engine, citations by name; D119, D131).

- R5: dashboard v2 (Vite + React + TypeScript + Tailwind, shadcn/ui, the funnel chart), legends on every page, the Today fix list (funnel, 4 tiles, blockers line, tasks 10 a page with Undo in place, Add email, needs-a-look cards, one-line suggests), goal cards with a ring, account stage bars and touch timelines, the People drawer changes, New / Seen earlier tabs, signal categories by name, Events City and Venue, owner-only commitment Done, Run news now, coming-soon badges, no View as (D207).

## Appendix A — API endpoints (R1.1 to R5)
All `GET` endpoints are read-only and same-origin. The R2 to R5 `POST` routes below are the only writes. Every `/api` route needs a signed-in session in every mode (R4, D116); without one the answer is 401. Any other method gets 404 or 405. Every POST needs the header `X-Saley-Act: 1`, a same-origin request, and a body of at most 16 KB (413 above that). In dev only, the `Host` port must be the port the server listens on (D68). The `X-Saley-As` header (loopback and the mock provider only, D50, D116) acts as another team member.
Rules for every endpoint (R1.1 ticket C, D40):
- Each response has `sync_id`, `last_computed`, `last_computed_label` and `stale`, and only the fields its view shows.
- A parameter the endpoint does not declare gives 422. A wrong type or a value out of range gives 422.
- Lists take `limit` (1–1000, default 200) and `offset` (0–100000, default 0).
- `sync_id` (optional, ≥ 1): the snapshot the page read first. If the data changed since, the answer is 409 `{"detail": "Data changed. Reload the page.", "sync_id": <current>}`.
- `sync_id` is the sheet snapshot. It changes only when the sheet changes. Numbers that count days (days late, days left, reconnect due) can change at midnight IST with the same `sync_id`.
- `q` is at most 100 characters. Search text is data: wildcards and SQL text match as plain text.
- A cross-site request (`Sec-Fetch-Site: cross-site` or a foreign `Origin`) gets 403. There is no CORS header.
- Headers on every response: Content-Security-Policy (`default-src 'self'`), X-Frame-Options DENY, nosniff, no-referrer, Cross-Origin-Opener-Policy and Cross-Origin-Resource-Policy same-origin.
- Errors never carry a stack trace, SQL, a sheet id, a path or an env value.

### Every route and its auth need (R5)

Auth words. **Session** = a signed-in team member (any role). **Approver or admin** = the SQL function refuses other roles with 403 "Only approvers can do this.". **Header** = `X-Saley-Act: 1` and a same-origin request (D24). **Admin** = the admin check in the app.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/healthz` | None | Container health check: `{"status": "ok"}`. |
| GET | `/api/me` | Session | Who is signed in, the role, the team list and the feature flags (R5, D164). |
| GET | `/api/status` | Session | Top bar: sync state, write counts, news freshness. |
| GET | `/api/today` | Session | Today: funnel, tiles, blockers line, my tasks, needs a look, Saley suggests. |
| GET | `/api/goals` | Session | Q4 goals with blockers and weighted pipeline. |
| GET | `/api/accounts` | Session | Account list with coverage per industry. |
| GET | `/api/accounts/{account_id}` | Session | One account with its people and touches. |
| GET | `/api/people` | Session | People list with filter tabs and counts. |
| GET | `/api/people/{contact_id}` | Session | One person: links, notes, touches, stage history. |
| GET | `/api/blockers` | Session | Late deliverables and the money-blocked tile. |
| GET | `/api/money-blocked` | Session | Deals blocked by a late item, with value. |
| GET | `/api/scorecard` | Session | Per industry scores, with "last week" (R4). |
| GET | `/api/pack` | Session | The weekly pack of one Monday, or the newest (R4). |
| GET | `/api/search` | Session | Global search: people, companies, goals, deliverables, decisions. |
| GET | `/api/signals` | Session | News signals, All or Linked. |
| GET | `/api/events` | Session | Upcoming or past events, with people in the city. |
| GET | `/api/suggest/{kind}` | Session | Suggestions: warm, best, newco, res. |
| GET | `/api/news/funnel` | Session | The news funnel for one day. |
| GET | `/api/inbox` | Session | Cards, decisions to confirm, asks and reminders. |
| GET | `/api/commitments` | Session | Commitments by owner and state. |
| GET | `/api/decisions` | Session | Decision log with undo state. |
| GET | `/api/health/full` | Admin | Sources and health: jobs, writes, mail, news, parity; R7 `discord`. |
| POST | `/api/act` | Approver or admin; header | Task Done, one-click cards, cell writes, row add, find people. |
| POST | `/api/undo/{write_id}` | Approver or admin; header | Undo a signed write while its window is open. |
| POST | `/api/decision/{decision_id}/confirm` | Approver or admin; header | Confirm a proposed strategy decision. |
| POST | `/api/proposal/{proposal_id}/dismiss` | Approver or admin; header | Dismiss an action card. |
| POST | `/api/proposal/{proposal_id}/edit` | Approver or admin; header | Save the edited draft of a one-cell card (R4). |
| POST | `/api/commitment/{commitment_id}/done` | Approver or admin; header | Mark a commitment done, with undo. |
| POST | `/api/seen` | Approver or admin; header | Record the items this person saw (per person). |
| POST | `/api/ask` | Session; header | Ask one question. Same engine as @Saley (R4). |
| POST | `/api/admin/news/run` | Admin; header | Run news now: queue one news edition (R5, D150, D151, D178). |
| GET | `/api/admin/news/run` | Admin | The newest Run news now request and its state. |
| GET | `/` | Session (else 303 to `/signin`) | The dashboard app: the built page with a per-request CSP style nonce (D146, D224). 503 with one plain line when there is no build. |
| GET | `/static/app/{file}` | None | The built CSS, JS and font files (hashed names, cached for a year). They hold no data. `index.html` and `build-stamp.json` are not served here. |
| GET | `/static/signin.css` | None | The stylesheet of the sign-in and message pages. No other file under `/static/` is served (D224). |
| GET | `/signin` | None | Sign-in page: a Google button, or the mock button where allowed (R4). R7: `?next=` keeps a same-site deep link through the sign-in (a mock form field, or a short `/auth` cookie through Google); any other `next` is dropped (D261). |
| GET | `/<page>[/<id>]?card=<card_id>` | Session (else 303 to `/signin?next=`) | R7 deep link from a Discord card (D261). Pages: today, people, accounts, suggestions, signals, events, blockers, commitments, pack, sources, asks. 303 to `/#<view>?open=<id>&card=<card_id>`; the page opens the item and outlines it for 3 s. A bad page, id or card id is 404. |
| POST | `/auth/mock` | None; mock provider only | Sign in as the dev user or an allowlisted address. Dev and loopback only. |
| GET | `/auth/google` | None; Google provider only | Redirect to Google with state and PKCE. |
| GET | `/auth/callback` | None; Google provider only | Finish Google sign-in. Checks state, claims and the allowlist. |
| POST | `/auth/signout` | Session cookie | End the session and go back to `/signin`. |

### Detail by route (R1.1 to R5)

| Endpoint | Parameters | Returns | View |
| --- | --- | --- | --- |
| `/healthz` | — | `{"status": "ok"}` | Container health check |
| `/api/me` | — | email, role, `dev`, `features` (`find_people`, `email_write`, `speaker_match`, `apollo`, `mail_replies`, `dms`: true or false; an off feature shows "Coming soon", D164), team members. No `view_as` (D207) | Top bar, badges |
| `/api/status` | — | state (`ok`, `worker_down`, `sync_failing`, `never`), message, late total, `pending`, `held`, `failed`, `write_note`, `write_bad` (R2) | Updated bar |
| `/api/today` | `tasks_offset` (0 to 1000, R5 T3); `user` (team short name) works only with the mock provider and is ignored with Google sign-in (D207) | greeting, date, `funnel[]` (`key`, `label`, `n`, `link`; In the list → Won, D170), `tiles` (4: deals, backlog, p1, week; with `source` and `link`, D202), `blockers_line` (`count`, `label`, `link`), `my_tasks` (`person`, `items`, `total`, `offset`, `more`, `more_label`, `empty`; each task has its `action`: an endpoint and a body, or null for add_email with `input_role` "email" and `soon`; a ticked task inside its window has `done`, `undo_id`, `undo_ids`, `undo_until`), `needs_look` (`cards[]` with `kind` news, event, blocker or waiting, `title`, `why`, `link`, D158; `soon` "mail_replies" while mail is off; `left_out_seen`), `saley_suggests` (up to 3, each with a one-line `line`, D217) | 1 Today |
| `/api/goals` | `limit`, `offset` | goals with active conversations (each with a one-line `note`), blockers (sorted by days late), weighted pipeline or a note, `why`, `progress` (`won`, `need`, `pct` or null, D159, D171) | 2 Q4 goals |
| `/api/accounts` | `filter` (`all`, `p1`, `has_connection`, `goal:G<n>`, `industry:<text>`), `q`, `limit`, `offset` | companies with `stage_counts`, `people_label` ("13 people · 9 P1"), `tags` (Qualified, Engaged, Single-threaded) and the tag legend (D174); coverage per industry | 3 Accounts |
| `/api/accounts/{id}` | — | one company, its people (no Evidence column on the page), touches, `timeline[]` (12 months, newest first: `month`, `label`, `touches` of `day`, `type`, `person`; at most 200 touches, D175), stage history | Company drawer |
| `/api/people` | `filter` (`all`, `backlog`, `p1`, `p1_waiting`, `in_conversation`, `reconnect_due`, `contacted_this_week`, `contacted_last_week`, `stage:<Stage>`, `account:<id>`), `q`, `limit`, `offset` | people, counts per tab | 4 People |
| `/api/people/{id}` | — | one person: email (`mailto:`), LinkedIn, website and paper links (https only), notes, `working_on` with its `source`, `follow_up_by` (`name`, `source`, `rule` "D31", D176), `papers[]` (`text`, `href`; the text is a stored title or the host plus the last path part, cut at 40 characters, never invented) and `papers_label`, `timeline[]` as for an account, touches, stage history, deal | Person drawer |
| `/api/blockers` | `q`, `limit`, `offset` | late deliverables, packages not ready, money blocked tile | 7 Blockers |
| `/api/money-blocked` | `limit`, `offset` | deals blocked by a late item: deal, company, value, blocking item, owner; total = sum of known values; count with no value (D42) | Money blocked list |
| `/api/scorecard` | `limit`, `offset` | per industry: people, contacted, connect %, reply % (null until R2), meetings, deals. R4 adds `last_week` and `last_week_note`: the same industry from the snapshot 7 days back (D118). No snapshot gives `null`, shown as a dash | 13 Scorecard |
| `/api/pack` | `week` (a Monday; another day gives 422; a Monday with no pack gives 404; none = the newest pack) | `week_start`, `built_at`, `body`: team numbers with `last_week`, Scorecard rows, late items, packed reminders, tasks per person. The same stored body the test-channel post uses (R4, D117, D134). `prev_week`, `next_week`: the Mondays of the neighbour packs, or null; the week picker moves only to these. `empty`: true only when no pack exists at all; then the answer is 200 and `week_start`, `built_at`, `body` are null (R5 T17, D229) | Weekly pack (`/#pack`) |
| `/api/health/full` | — (admin only) | writes (enabled, message, open, failed), mail (state, message, last_ok), jobs, sheet tabs with state and new columns, parity, data quality, calls out, LLM use; R3 adds `news` (funnel steps with `href`, failed sources, per-source counts), `search` and `apollo` (always "off" in R3, D78); R7 adds `discord.rows` (label and value: cards sent, held and failed today, presses ok / already / refused, the middle press time, the last card sent and edited, chat lines kept, the last chat summary, DMs on or off; from `v_bot_health`) | 14 Sources and health |
| `/api/search` | `q` (1–100 characters) | results grouped as people, companies, goals, deliverables, decisions; `match` = [start, length] for bold text | Search |
| `/api/inbox` | `type` (`card`, `confirm`, `ask`, `reminder`), `limit`, `offset` | items (one per card, decision to confirm, ask or reminder), total, empty text (R2) | 9 Inbox |
| `/api/commitments` | `owner`, `state` (`open`, `done`, `dropped`, `all`; default `open`), `limit`, `offset` | commitments: owner (`owner_short`, `owner_name`), text, due, source, state, reminders sent (R2); R5 adds `undo_id` and `undo_until` when the signed-in person's own Done is inside its window (D220) | 10 Commitments |
| `/api/decisions` | `person`, `surface` (`dashboard`, `chat`, `card`), `undo` (`open`, `undone`, `undo_failed`, `closed`), `state` (`proposed`, `confirmed`, `active`), `limit`, `offset` | decisions with label, text, value, undo state, sync reason, `can_confirm`, `can_undo` (R2) | 11 Decisions |
| `/api/signals` | `tab` (`all` or `linked`; default `all`), `range` (`day`, `week`, `month`, `year`; default `week`), `day` (date; more than 5 years from today gives 422), `cat` (a topic name: Hiring, Funding, Product launch, Research, Partnership, Opportunity, Event, or Other; a letter gives 422, D156, D239), `src` (text, at most 120 characters), `tag` (`Opportunity`, `People`, `Risk`, `Market`, `Tender`, `Discover`, `Ours`), `entity` (text), `min_importance` (1–5), `linked_only`, `view` (`new` or `seen`: the New or Seen earlier tab, 10 a page; R5 T7, D157), `limit`, `offset` | `tab`, items (each with `why`, `published_label`, `seen`), `total`, `window` (`from`, `to`, `label`), `left_out_seen` (0 with `view`), `view`, `new_total`, `seen_total`, `new_empty` (R3, F-R3-07, D75; `include_seen` and `left_out_note` removed at the cut-over, D224) | Signals (All, Linked) |
| `/api/events` | `when` (`upcoming` or `past`; default `upcoming`), `view` (`new` or `seen`), `limit`, `offset` | items (name, `city` Bangalore, London or Other, `venue` the full Location text, dates and `date_label`, `days_left`, `days_text`, registered, people in city, connected, P1, `speaker_matches` and `speaker_soon` ("coming soon", S12 not built), `href` https only, `seen`), registered first then by start date (D160), `total`, `window`, `left_out_seen`, `view`, `new_total`, `seen_total`, `new_empty` (R3, R5 D216) | Events |
| `/api/suggest/{kind}` | path `kind` (`warm`, `best`, `newco`, `res`), `view` (`new` or `seen`), `limit`, `offset` | `kind`, ranked items (person, company, title, evidence, `seen`, search `hits` with `engine`, `url`, `title` and an Add `form` prefilled from sheet values and the hit), `total`, `empty`, `window`, `left_out_seen`, `view`, `new_total`, `seen_total`, `new_empty`, `capped` and `capped_note` (the rank reads at most 1000 rows, by `ref_id`) (R3) | Suggestions (warm, best, newco, res) |
| `/api/news/funnel` | `day` (date; default today IST) | `day`, `label`, `steps` (fetched, dropped seen/copy/gate, deduped, scored, scored 4 or more, linked, sent "—, no news digest is sent yet") (R3, D73) | Sources and health (news funnel) |
| `POST /api/act` | body: `action` (`write`, `email_write`, `task_done`, R3: `row_add` or `find_people`), `target_ids[]`, `payload` (`row_add`: `fields[]` of `role` and `value`, new-row roles only, docs/10) | `write_id`, `undo_until`, `label`, `already`, one item per cell (R2). `row_add` answers the same way and writes one decision and one sheet `append`; `find_people` (one company in `target_ids`, no payload) answers `request_id`, `already`, `label` and queues one search for the worker (R3, D76, D77) | Tasks, cards, Done; R3: Suggestions add form and "Find people" |
| `POST /api/undo/{write_id}` | — | `write_id`, `cancelled` (R2) | Undo |
| `POST /api/decision/{id}/confirm` | — | `decision_id`, `already` (R2) | 9 Inbox, 11 Decisions |
| `POST /api/proposal/{id}/dismiss` | — | `proposal_id`, `already` (R2) | 9 Inbox (card) |
| `POST /api/proposal/{id}/edit` | body: `text` (1–200 characters, not blank). Needs `X-Saley-Act` | `ok`, `proposal_id`, `already`, `write_id`, `undo_until`, `label`. Saves the edited draft of an action card, signed (surface dashboard), with an undo window. Calls `saley_proposal_resolve`; the first saver wins, a later save gets 409 "Already done by X at HH:MM" (R4 ticket 7, D129, D137) | Inbox (card inline editor, ticket 10) |
| `POST /api/commitment/{id}/done` | — | `commitment_id`, `already`, `write_id`, `undo_until` (R2) | 10 Commitments |
| `POST /api/seen` | body: `item_type` (`story`, `signal`, `event`, `suggest_warm`, `suggest_best`, `suggest_newco`, `suggest_res`), `ids[]` (1–200 positive integers), `query_id` (optional, at most 64 characters: letters, digits, `_ . : -`) | `ok`, `marked`. Writes `user_seen` for the signed-in person only, through `saley_mark_seen`. Idempotent: the first time a person saw an item stays (R3, D75, D97) | Signals, Events, Suggestions |
| `POST /api/ask` | body: `text` (1–300 characters, not blank). Needs `X-Saley-Act` | `text`, `known`, `shown`, `more`, `left_out`. Same engine as @Saley in Discord (`saley.bot.answer`): 5 items with citations by name, then `more`; marks the shown items seen for the signed-in person and logs the exchange, through `saley_mark_seen` and `saley_ask_log` only. Questions only, no action (R4 ticket 6, D131) | Today (Ask card, ticket 10) |
| `POST /api/admin/news/run` | — (admin; header) | `ok`, `request_id`, `label`. Calls `saley_request_news_run`: one open request at a time; 403 for a user who is not admin, 409 when one is open, 429 with `Retry-After` in the 10-minute cooldown (R5 T8, D150, D178) | Sources and health (Run news now) |
| `GET /api/admin/news/run` | — (admin) | `latest` (id, requested_by, requested_at, state `queued`, `running`, `done` or `failed`, started_at, finished_at, note; null before the first request), `open`, `label` | Sources and health (its state line) |

POST answers (R2):
- 200 `ok`. `already: true` means the same instruction was done before (D48). A write that failed in the sheet is never `already` (D67).
- 403: not an approver or admin, a locked column, email writes off, a missing or wrong header, or a cross-site request.
- 404: no such person row, write, decision, card or commitment.
- 409: busy (a lock wait, deadlock or serialization failure, M1), cooldown (another person wrote the same cell within `ACT_COOLDOWN_SECONDS`), superseded (a newer write sits on the cell), undo already done, undo window closed, card closed.
- 413: body over 16 KB. 422: bad value (including text that starts with `=`, `+`, `-` or `@`) or bad action.
- Errors carry fixed text only. Never a SQL text, a sheet id or a stack trace.

POST answers (R3):
- `/api/seen`: 403 for a user who is not an approver. 422 for a bad item type or id.
- `row_add`: 403 for a locked role or a role outside the new-row set. 422 for a bad value, a LinkedIn URL that is not a `linkedin.com/in/` link, or a repeated role. A duplicate name and company is refused (D76, D85) with 409: "Already added by <who>" when a live row add holds the person (D111), else "That person is already in the sheet or queued."
- `find_people`: 422 when `target_ids` has not exactly one company or a payload is sent. A repeat of the same request answers `already: true` (D77).
- Undo of a row add uses `POST /api/undo/{write_id}`. It removes the row only when the row is unchanged (D91).

POST answers (R5):
- Task Done: 409 "This column has no done value yet." when the column holds no fitting value; nothing is written (D153).
- `email_write` (Add email): 403 while `EMAIL_WRITE_ALLOWED` is false (D154); 409 "That person already has an email." when the cell is filled or a live email write for the row exists (D169); 422 for a value that is not an email.
- Commitment Done by anyone but the owner: 403 "This is <name>'s commitment. Only they can mark it done." and no write. Undo of a commitment Done by anyone but its signer: 403 (D155).
- `find_people` while `FIND_PEOPLE_ENABLED` is false: 403 "Find people is off for now." (D164).
- Run news now: see `POST /api/admin/news/run` above (403, 409, 429).

## Appendix B: DOM hooks for the gate

The browser checks (`scripts/browser_qa.py`, gate steps 8 and 9) read the page through these hooks only (plan R5 §3.4, D199, D214, D216, D220, D224). A change to a hook is a change to this list and to the checks in the same ticket. Since the cut-over (R5 T16b) every check runs on `/`: one run covers the R1.1 to R5 rows.

**Shell (every page)**
- Rail links `.rail a[data-v]`; the shown page `section.view.on#v-<name>` (also `money` and `search`) with `data-features`.
- Top bar `header`: `#me`, `#gsearch`, `#updated`, `#pending`, the Retry Button `[data-retry]`; the theme toggle `[data-theme-toggle][data-theme-now]` (D163); the phone menu Button `[data-menu]` (D198).
- `#toast` (always in the DOM); a failed load `[data-retry]` inside the page.
- Legend `[data-legend]` with entries `[data-tag]`; a tag on the page `[data-tag-ref]` (each one in the legend).
- Off features: the place `[data-feature="<name>"]` holds the badge `[data-soon="<name>"]` while `/api/me.features` says off (D164).
- Lists that page: `[data-pager]`, rows `[data-row]` inside `[data-pager-list]`.
- Every state-changing control: `[data-act]` with `data-endpoint` and `type="button"`; no `<form>` and no button without `type="button"` on a page.

**Today (`#v-today`)**
- Funnel `[data-funnel] a[data-tile=<stage>] .v` (stages listed, contacted, connected, talking, met, won); each bar opens People with its filter.
- Tiles `#tiles a.tile[data-tile] .v` with a `title` that names the source: `deals`, `backlog`, `p1`, `week` (D202). The blockers line `#tiles [data-blockers-line][data-count]` with `[data-blockers-open]`.
- Tasks `#tasks li.task` with `[data-act="done"]` that turns into `[data-act="undo"]` in place (D204); `[data-add-email]` on an add_email task; `[data-pager]`.
- Needs a look `#look a[data-kind]` (news, event, blocker, waiting), the mail-replies badge `#look [data-soon="mail_replies"]`.
- Saley suggests `#suggests li` (one link each).
- Ask card `#ask-card`, `#ask-input`, `#ask-send`, `#ask-state`, `#ask-answer`.

**Drawers**
- `.drawer.on` (`[data-drawer="person"]` or the company drawer), title `[data-slot="sheet-title"]`, external links `a[target=_blank][rel~=noopener]`, the Email field `#drawer-email` and Save `[data-act="email-save"]`.
- People rows `[data-row][data-person]` with `[data-open-person]`; Accounts rows `[data-row][data-account]` with `[data-open-account]`; `[data-follow-up]`, `[data-papers]`, `[data-working-source]`, `[data-stagebar]`, `[data-timeline]` (D214).

**Signals, Suggestions, Events**
- Seen tabs `[data-seen-tabs] [data-seen-tab="new"|"seen"][data-count]`, `[data-nothing-new]`, `[data-seen-failed]` (D216); the window line `[data-stamp]`.
- Signals: `[data-sig-tabs] [data-filter="all"|"linked"]`, `[data-cat-chips] [data-filter]`, `[data-range-chips] [data-filter]`, `#sig-tag`, `#sig-src`, `#sig-entity`, rows `#sig-list > li[data-story][data-cat]`.
- Suggestions: `[data-kind-tabs] [data-filter]`, rows `#sg-list > li[data-suggestion][data-kind]`, `[data-line]`, `[data-open-subject]`, Find people `[data-act="find"]` in `[data-feature="find_people"]`, its state `[data-field="search"]`, hits `li[data-hit]` with `[data-open="add"]`, the form `[data-form="add"]` and `[data-act="add"]` that turns into `[data-act="undo"]` in place.
- Events: `[data-when-tabs] [data-filter]`, `#ev-table th[data-col]`, `tr[data-row][data-event][data-registered]`, `td[data-city]`, `td[data-venue]`.

**Team pages**
- Inbox `#inbox-list .inbox-card[data-kind][data-id]` with `[data-act="primary"|"edit-save"|"dismiss"|"undo"]`.
- Commitments `#commit-table tbody tr` with `[data-act="commitment-done"]` and `[data-act="undo"]`.
- Decisions `#dec-table tbody tr[data-id]`, `#dec-person`, `#dec-surface`, `#dec-undo`, `#dec-state`, `[data-act="confirm"|"undo"]`.
- Weekly pack `#pack-week`, `[data-pack-empty]` (no pack yet, D229), `#pack-tiles .tile .v/.d`, `#pack-late`, `#pack-reminders`, `#pack-tasks`, `#pack-score`; Scorecard `#sc-table`.
- Sources and health (admin): `#news-run` with `[data-act="run-news"]`, `[data-news-state]`, `[data-news-line]`; `#news-funnel a[data-tile] .v`; `#news-sources` with `#news-pick` ("Showing: <step>") and `#src-table` (D225); `#jobs-table`, `#egress-table`, `#tabs-table`, `#parity-table`, `#dq-table`, `#conn-list`.

**Search**
- `#search-results`, groups `[data-group]`, hits `.hit` with the match in `<mark>`, `.empty-state` for no result.
