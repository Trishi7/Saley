# 08 · Dashboard spec

Base: docs/07-dashboard-mock.html. This file is the contract for `saley/web`. One view = one endpoint = one SQL view. Every line carries an evidence link. Every list shows "last computed".

## Who it is for
Sid and Vaishnavi use this dashboard instead of the sheet. Saley is the only writer of the sheet. Kushal views and decides. Trishi is admin. Every word on the page must make sense to Sid and Vaishnavi at a glance: no rule ids, no week numbers, no table names. Say "last week 91", not "W41: 91".

## Shell
- Left rail: Run the plan (Today, Q4 goals, Accounts, People, Suggestions) · Watch (Signals, Blockers, Events) · Team (Inbox, Commitments, Decisions, Weekly pack, Scorecard) · Sources and health (admin).
- Top bar on every page: signed-in person, global search ("Search everything: a company, a person, a city, a news story"), and "Updated HH:MM IST · sheet HH:MM · news HH:MM".
- The Updated bar turns red (D43): "Worker not running since HH:MM" when no sync started in 3 × SHEET_SYNC_SECONDS; "Sheet sync failing since HH:MM" when syncs run but none succeeds; "Old data: tab … not read" when a tab is stale (D41). On a laptop, `saley dev` runs web and worker in one process on `WEB_PORT` (default 8090).
- Every page has its own "Search in this page" box. Global search opens a results page: people, companies, news, decisions, with links.
- Every number tile is a link. It opens the tab with the filter set. Every tile carries a `source` (the sheet tab or table it counts). A number that is not known shows "—", never 0; a tile whose sheet tab was never read says so.
- Subtitle under every page title: one plain sentence for Sid and Vaishnavi (see mock).
- Auth: Google sign-in. `WEB_ALLOWED_EMAILS`. Roles from `users.role`: viewer, approver, admin. Approver sees decide buttons. Admin sees Sources and health.
- Dev: `SALEY_ENV=dev` skips sign-in and uses `DEV_USER_EMAIL`.
- Laptop first. Phone readable. No horizontal scroll except tables.
- Stack: FastAPI serves `/api/*` JSON and `/` static. Static = plain HTML + one JS file + one CSS file. No build step. Fetch JSON, render tables.
## Writes and the approval model (decision D24)
- An instruction from an approver IS the approval. A button click on the dashboard or a chat instruction writes at once. Nothing waits for a second yes. Nothing is asked twice across chat and dashboard.
- Saley's own ideas (rules, suggestions) show once as a card with one primary button. The click writes. They never pile up in a queue.
- Every write stores a signature (user, time, surface) in `decision` and an `undo_until` = now + UNDO_MINUTES (default 10). An Undo button shows until then. Undo reverts the cell and logs it.
- Endpoints: `POST /api/act` with `{action, target_ids[], payload?}` for task ticks, one-click cards, add-to-pipeline, mark done, fill industry. `POST /api/decision/{id}/confirm` for strategy decisions. `POST /api/undo/{write_id}`. All require role approver (Kushal, Sid, Vaishnavi) or admin.
- The Inbox holds only: Saley's open questions to the team, the team's questions to Saley, reminders, and strategy decisions that need a confirm. Never a write the human already asked for.

## Views

| # | View | Endpoint | SQL view | Fields | Filters | Actions | Reqs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Today | GET /api/today?user= | v_today_user | **Order: numbers → my tasks → needs a look → blocked → Saley suggests.** Numbers: 6 tiles (deals closed of 3, people in a live conversation, connected with no email, priority people waiting on LinkedIn, new people contacted this week vs last week, late items that block goals), each a link. My tasks: built per signed-in person from the rules due today (R05 pick 5, R06 find email, R08 brief, R09 next steps, R10 deal ask, R13 email/research/DM/call steps, R14 reconnect, commitments due, decisions to confirm), each with the action buttons and a Done tick; Done writes the sheet cell(s) and logs the signature. Needs a look: news ≥ 4 linked to our accounts, prospect replies with no answer in 24 h, events ≤ 3 days, tenders with deadlines. Blocked: late deliverables that block goals, asks waiting on others, data-quality fixes with a one-click yes. Saley suggests: new companies and people, warm intro paths, park lists, with one primary button each. | user (from sign-in) | tick, draft, open, one-click write, undo | S03 S22 S42 S36 R05 R06 R08 R09 R10 R13 R14 |
| 2 | Q4 goals | GET /api/goals | v_goal_status | per goal: text, value band, active conversations (list), need, blockers (deliverables), weighted USD, forecast commit/likely/possible, days left, RAG | none | none | S03 S04 S54 R04 |
| 3 | Accounts | GET /api/accounts?filter= | v_account_coverage | company, industry, goal, motion, people, contacted, connected, emailed, coverage %, warmth (stage), buying-group gaps, P1 count, account plan (next 7 d), "Saley says" | all · p1 · has_connection · goal:Gn · industry:X · search | open account drawer (people, touches, facts, signals) | S15 S49 R13 R10 |
| 4 | People | GET /api/people?filter=&q= | v_contact_stage | name, company, title, stage, working_on + source + date, email status, next step, last 3 touches, priority, city. Person drawer (D39): the real email as a `mailto:` link (only when the cell holds an address), LinkedIn, paper and website as https links in a new tab; empty = "None on file". | backlog · p1_waiting · in_conversation · reconnect_due · stage:X · search | open person drawer; "Act" opens the matching proposal | S29 S14 R06 R13 R14 |
| 5 | Suggestions | GET /api/suggest/{warm,best,newco,res} | v_suggest_* | per row: subject, reason, score and score parts, via, evidence | tab | Approve adds a proposal (row_add or email1) | S07–S10 R02 R05 R11 |
| 6 | Signals | GET /api/signals?tab=all&range=day|week|month|year&cat=&src=&tag= and tab=linked | v_signal_all, v_signal_linked | All: headline, outlets, source, category, tag, importance, impact, published · Linked: + entity, action hint, state | range, category, source, tag, entity | Linked: Add to action · Dismiss · Ask | R01 |
| 7 | Blockers | GET /api/blockers | v_deliverable_late | item, owner, due, late by, blocks (goals, deals, motions), status, money blocked. Goal links come from the goal's Dependencies text, or from docs/goal-deliverable-map.yaml for the goals listed there (D34). Money blocked (D42): a tile on this page (Today keeps 6 tiles) and a list at GET /api/money-blocked: deal · company · deal value · blocking deliverable (+N) · owner. A deal-size band counts its midpoint ("USD 30–50k" → "≈ USD 40k (USD 30–50k)"); an exact value counts as given; no value = "No value on file", never 0. Total = sum of the list. | none | Remind (creates a polite ask) | R04 R12 |
| 8 | Events | GET /api/events | v_event_targets | event, city, dates, days left, registered, people in city, connected, P1, speaker matches | upcoming · past | Build target list (proposal event_prep) | R03 S11 S12 |
| 9 | Inbox | GET /api/inbox | ask, reminder, decision(proposed) | Saley's questions to the team with age, team questions to Saley, reminders, strategy decisions to confirm. No writes the human already asked for. | type, age | Answer · Confirm · Snooze | S35 S36 S28 S45 |
| 10 | Commitments | GET /api/commitments | commitment | owner, text, due, source, state, reminders sent | owner, state | Mark done | S42 |
| 11 | Decisions | GET /api/decisions | decision | id, text, state (proposed/confirmed/active), approver, time, surface, applies to | state | Confirm (approver) | S45 |
| 12 | Weekly pack | GET /api/pack?week= | metric_snapshot + views | scoreboard, funnel vs last week, motion table target vs actual, backlog, slips, next 10, one plan suggestion, 1:1 agenda | week | none | S31 S44 S05 |
| 13 | Scorecard | GET /api/scorecard | v_segment_scorecard | per industry: people, contacted, connect %, reply %, meetings, deals; 7-day delta | none | none | S57 |
| 14 | Sources and health (admin) | GET /api/health/full | job_runs, egress_log, llm_calls, news_* , parity_checks | Jobs: last run, status, note. News today as a funnel: sources tried, answered, items pulled, dropped seen / duplicate / not relevant, stored, scored, scored ≥ 4, about our people, sent. Failed or slow sources with problem, since, fallback. Parity: last hourly check of dashboard vs sheet vs news tables (row counts, stage counts, last story time) with red on mismatch. Sheet tabs: last read, stale tabs with the missing header in red, and unknown headers as "new columns" (D41). | none | re-run parity | S33 S72 |

## Shared rules
- Empty state text per view: "No items. Last computed HH:MM." Never a blank panel.
- Evidence link: `GET /api/evidence?fact=&signal=&rule=&row=` opens a drawer with the source (sheet row key, story URL, rule text, fact author and date).
- Numbers: tabular. Dates: IST, "Fri 17 Oct". Money: USD.
- Per-user seen does not apply to the dashboard. It applies to bot answers only.
- Owner of a person (which list a task lands on), decision D31 (replaces D25): "VR" / "Sid" text in `First Contact` names the owner; else a LinkedIn request recorded in `Sid - LI Addition` → Sid; else `First Contact Type` = LI → Sid; else Vaishnavi. A human can reassign from the person drawer.
- Goal links (D30): until the sheet has a Goal column, people link to goals by industry. The link source is shown as "industry rule".
- Parity: a `parity_check` job runs hourly. It compares view counts with the sheet copy and the news tables. The result shows on Sources and health. Claude Code runs the `dashboard-parity` skill before any dashboard release.
- Stale: a view older than 15 min shows an amber stamp.
- No caps on the dashboard. Caps are for the bot.

## Releases
- R1: shell, sign-in (dev mode), global and page search, last-updated bar, Today numbers only, views 2, 3, 4, 7, 13 read-only, view 14 with jobs and parity. Read from the sheet copy.
- R2: Today "my tasks" per person with Done writes and undo, Inbox, Commitments, Decisions, People drawer with "working on".
- R3: Signals All + Linked, Events, Suggestions with add-to-pipeline, "needs a look" and "Saley suggests" on Today. News health funnel.
- R4: Weekly pack, Scorecard deltas, Google sign-in for real users.

## Appendix A — API endpoints (R1.1)
All endpoints are `GET`, read-only, and same-origin. Any other method gets 404 or 405.
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

| Endpoint | Parameters | Returns | View |
| --- | --- | --- | --- |
| `/api/me` | — | email, role, team members | Top bar |
| `/api/status` | — | state (`ok`, `worker_down`, `sync_failing`, `never`), message, late total | Updated bar |
| `/api/today` | `user` (team short name) | greeting, date, 6 tiles with `source` and `link` | 1 Today |
| `/api/goals` | `limit`, `offset` | goals with active conversations, blockers (sorted by days late), weighted pipeline or a note, `why` | 2 Q4 goals |
| `/api/accounts` | `filter` (`all`, `p1`, `has_connection`, `goal:G<n>`, `industry:<text>`), `q`, `limit`, `offset` | companies, coverage per industry | 3 Accounts |
| `/api/accounts/{id}` | — | one company, its people, touches, stage history | Account drawer |
| `/api/people` | `filter` (`all`, `backlog`, `p1`, `p1_waiting`, `in_conversation`, `reconnect_due`, `contacted_this_week`, `contacted_last_week`, `stage:<Stage>`, `account:<id>`), `q`, `limit`, `offset` | people, counts per tab | 4 People |
| `/api/people/{id}` | — | one person: email (`mailto:`), LinkedIn, website and paper links (https only), notes, touches, stage history, deal | Person drawer |
| `/api/blockers` | `q`, `limit`, `offset` | late deliverables, packages not ready, money blocked tile | 7 Blockers |
| `/api/money-blocked` | `limit`, `offset` | deals blocked by a late item: deal, company, value, blocking item, owner; total = sum of known values; count with no value (D42) | Money blocked list |
| `/api/scorecard` | `limit`, `offset` | per industry: people, contacted, connect %, reply % (null until R2), meetings, deals | 13 Scorecard |
| `/api/health/full` | — (admin only) | jobs, sheet tabs with state and new columns, parity, data quality, news funnel, calls out, LLM use | 14 Sources and health |
| `/api/search` | `q` (1–100 characters) | results grouped as people, companies, goals, deliverables, decisions; `match` = [start, length] for bold text | Search |
| `/healthz` | — | `{"status": "ok"}` | Container health check |
