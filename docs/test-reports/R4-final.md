GATE: PASS

# /verify R4 · 21 Oct 2026 00:44 IST

| Step | Check | Skill or agent | Result | Note |
| --- | --- | --- | --- | --- |
| 1 | Static: tidy --check, py_compile, ruff check, ruff format --check | verification-loop | PASS | no stray file; 166 files compile; ruff check and format clean |
| 2 | Tests, coverage, new files >= 80 %, a matrix row per feature | pytest, tdd-guide | PASS | 1690 passed, coverage 97 %, every new file >= 80 %, 102 matrix rows (R1.1, R2, R3, R4) |
| 3 | STE lint: <= 60 characters, no banned words | saley.qa.ste_lint | PASS | STE lint: 854 strings, 0 findings |
| 4 | Sheet robustness tests | python-testing | PASS | 12 robustness tests passed |
| 5 | API contract and abuse suite | security-review | PASS | 150 of 150 passed; R4-api-abuse.md |
| 6 | saley sheet verify | dashboard-parity | PASS | 62 checks OK, 0 DIFF; R4-sheet-verify.md |
| 7 | saley dev: first sync within SHEET_SYNC_SECONDS | saley dev | PASS | Updated 00:37 IST · sheet 00:37 · news — (age 7 s, limit 60 s) · port 8091, worker:gate |
| 8 | Browser QA, read-only | browser-qa | PASS | verdict SHIP, 1028 checks, 62 screenshots in docs/test-reports/R4-screens; low memory (3816 MB free): one width per run |
| 9 | Click-path audit: no non-GET request | click-path-audit | PASS | 9 POSTs, all allowed, each from a [data-act] control; 15 seen marks after list render; 2 Ask questions; no form, no submit button |
| 10 | Reviewers on the release diff | code, python, database, security, silent-failure | PASS | reviewers: per ticket (python, silent-failure, code-reviewer, database, fastapi, security) plus a security/silent-failure/database pass on the late gate fixes; no open must-fix; MEDIUM/LOW items listed in STATUS for R5 |
| 11 | Docs: README, .env.example, docs/08 appendix, STATUS | doc-updater | PASS | docs (doc-updater, ticket 11): README R4 run notes, .env.example matches settings (test_env_example green), docs/08 Appendix A 37 routes + R4 view notes, STATUS R4 env NEW 17 / CHANGED 0 / RETIRED BOT_SHAPES_DRAFT, DECISIONS D114-D142, QUESTIONS Q-R4-2/Q-R4-4/Q-N5 open |
| 12 | Finalize | scripts/verify.py | DONE | GATE: PASS |

## Test matrix

102 of 102 rows passed (R1.1, R2, R3, R4).

| Row | Release | Layer | What | Result | Note |
| --- | --- | --- | --- | --- | --- |
| R1.1-U01 | R1.1 | unit | saley dev starts web and scheduler on WEB_PORT and refuses beside a live worker | PASS | passed |
| R1.1-A01 | R1.1 | api | Updated bar states ok / worker down / sync failing / stale tab | PASS | passed |
| R1.1-A02 | R1.1 | api | tile number equals its list; unknown is "—", never 0 | PASS | passed |
| R1.1-A03 | R1.1 | api | money total = sum of list; band counts its midpoint | PASS | passed |
| R1.1-A04 | R1.1 | api | person drawer shows real email and https links only | PASS | passed |
| R1.1-A05 | R1.1 | api | goal card blockers sorted by days late with a why line | PASS | passed |
| R1.1-A06 | R1.1 | api | global search groups results by type | PASS | passed |
| R1.1-B01 | R1.1 | browser | every view, tile clicks, 5 search terms, 3 drawers, 3 widths, zero console errors | PASS | step 8 PASS |
| R1.1-B02 | R1.1 | browser | the page sends no request other than GET | PASS | step 9 PASS |
| R1.1-G01 | R1.1 | guardrails | STE lint clean on the repo; long text and banned words are flagged | PASS | passed |
| R1.1-S01 | R1.1 | sheet | insert column, rename header, delete row, move row, value changed mid-sync | PASS | passed |
| R1.1-S02 | R1.1 | sheet | sheet copy = DB = dashboard (saley sheet verify) | PASS | step 6 PASS |
| R1.1-A07 | R1.1 | api | one sync_id per request; a stale sync_id gets 409 | PASS | passed |
| R1.1-A08 | R1.1 | api | API contract and abuse suite (fields, limits, bad input, no secrets) | PASS | step 5 PASS |
| R1.1-G02 | R1.1 | guardrails | no secrets in any response or error; cross-site refused | PASS | passed |
| R1.1-G03 | R1.1 | guardrails | web role reads only views; every endpoint works as that role | PASS | passed |
| R1.1-G04 | R1.1 | guardrails | gate bookkeeping, tidy rules, matrix loader | PASS | passed |
| R1.1-G05 | R1.1 | guardrails | linkedin.com is blocked even when allowlisted | PASS | passed |
| R1.1-C01 | R1.1 | guardrails | CI run green on a fresh database (roles.sql, migrations, pytest) | PASS | passed |
| R2-W01 | R2 | sheet | write round trip on a fake sheet - cell + decision row + Name note, read back equal | PASS | passed |
| R2-W02 | R2 | sheet | live round trip in SALEY_TEST - create tab, write, read back, undo, delete tab (always) | PASS | exit 0 2026-10-20T19:10:41.402811Z [info     ] egress_call                    host=sheets.googleapis.com method=GET status=200 |
| R2-W03 | R2 | unit | undo inside UNDO_MINUTES restores the old value; after the window it is refused | PASS | passed |
| R2-W04 | R2 | guardrails | a write to a locked column is refused with 403 and logged | PASS | passed |
| R2-W05 | R2 | guardrails | the Email column is refused while EMAIL_WRITE_ALLOWED=false | PASS | passed |
| R2-W06 | R2 | api | two users on one row within 2 s - the second gets "already done by X at HH:MM" | PASS | passed |
| R2-W07 | R2 | api | the same instruction twice (chat + dashboard) is one write (D24) | PASS | passed |
| R2-G01 | R2 | guardrails | the web process has no write credential and no write scope | PASS | passed |
| R2-T01 | R2 | api | my tasks per person - Sid LinkedIn, Vaishnavi research, Kushal approvals (D31) | PASS | passed |
| R2-T02 | R2 | api | Done writes at once with a signature and returns an undo id | PASS | passed |
| R2-I01 | R2 | api | an Inbox card shows once; a dismissed card never returns | PASS | passed |
| R2-C01 | R2 | unit | commitments from meeting notes; mail shows "not configured" without an IMAP login | PASS | passed |
| R2-C02 | R2 | guardrails | reminder politeness - one reminder, one on the due day, then the weekly pack; never "overdue" | PASS | passed |
| R2-D01 | R2 | api | Decisions view lists decision rows with filters person / surface / undo state | PASS | passed |
| R2-P01 | R2 | api | People drawer "working on" comes from facts with source and date | PASS | passed |
| R2-B01 | R2 | browser | click-path Done -> toast -> Undo restores | PASS | passed |
| R2-B02 | R2 | browser | click-path Confirm on a decision and dismiss on an Inbox card | PASS | passed |
| R2-B03 | R2 | browser | click-path audit of every state-changing control (gate step 9) | PASS | step 9 PASS |
| R2-S01 | R2 | sheet | parity holds after 10 random writes and 5 undos | PASS | passed |
| R2-G02 | R2 | guardrails | the web role has no table grants; it may only EXECUTE the saley_* act functions | PASS | passed |
| R2-N01 | R2 | unit | Drive notes folder read (fake Drive, subfolders, Docs/.md/.txt/.pdf/.docx) -> notes in the DB, one parser | PASS | passed |
| R2-N02 | R2 | unit | a changed Drive file (new modifiedTime) is parsed again exactly once; an unchanged file is not | PASS | passed |
| R2-N03 | R2 | unit | a removed Drive file is marked gone; its commitments stay with "source removed" | PASS | passed |
| R2-N04 | R2 | guardrails | a 403 on the folder gives the health line "notes - folder not shared" with the folder id, no crash | PASS | passed |
| R2-N05 | R2 | sheet | live read-only Drive sync of the notes folder through egress (never a write) | PASS | exit 0 2026-10-20T19:11:17.675146Z [info     ] team_aliases_loaded            aliases=7 users=4 |
| R3-N01 | R3 | unit | scoring is deterministic - the same story scores the same 3 times | PASS | passed |
| R3-N02 | R3 | unit | the same story from 2 sources becomes 1 signal | PASS | passed |
| R3-G01 | R3 | guardrails | the validator rejects a URL, name or number not in the input facts | PASS | passed |
| R3-G02 | R3 | guardrails | linkedin.com fetch is blocked in 3 URL shapes | PASS | passed |
| R3-G03 | R3 | guardrails | a host outside EGRESS_ALLOWLIST is blocked | PASS | passed |
| R3-U01 | R3 | api | seen per user - A sees, B still sees, A gets "left out 1 you saw" | PASS | passed |
| R3-S01 | R3 | sheet | add-to-pipeline row_add lands with a signature (fake sheet) and shows after one sync | PASS | passed |
| R3-S02 | R3 | sheet | live row_add in SALEY_TEST with signature, then the tab is deleted | PASS | exit 0 2026-10-20T19:12:28.211485Z [info     ] egress_call                    host=sheets.googleapis.com method=GET status=200 |
| R3-A01 | R3 | api | news funnel fetched >= deduped >= scored >= linked, each number links to its list | PASS | passed |
| R3-L01 | R3 | guardrails | LLM calls stay <= LLM_DAILY_CALL_CAP and a repeat is a cache hit | PASS | passed |
| R3-A02 | R3 | api | Signals All by range and score, Linked, Events, Suggestions endpoints | PASS | passed |
| R3-B01 | R3 | browser | Signals, Events, Suggestions pages and Today "needs a look" / "Saley suggests" | PASS | passed |
| R3-G04 | R3 | guardrails | Apollo stays off and Sources and health says "off" | PASS | passed |
| R3-L02 | R3 | guardrails | one live LLM call scores one fixture story, logged in llm_calls, under the cap | PASS | exit 0 HTTP Request: POST https://api.anthropic.com/v1/messages "HTTP/1.1 200 OK" |
| R3-G05 | R3 | guardrails | SearXNG then DuckDuckGo; only result URLs stored; a linkedin.com result is never fetched | PASS | passed |
| R3-N03 | R3 | unit | live fetch, 1 request per source kind, 3 items max, no scoring, through egress | PASS | exit 0 2026-10-20T19:12:44.101948Z [info     ] egress_call                    host=api.openalex.org method=GET ms=937 status=20 |
| R3-C01 | R3 | unit | meeting-note Next steps lines "[Name] action" become that team member's commitments; non-team owners skipped and counted; alias only from TEAM_NAME_ALIASES, never a prefix guess (D92) | PASS | passed |
| R3-C02 | R3 | unit | a name that only starts with a team short code does not match without an alias (D92, Q-R3-8) | PASS | passed |
| R3-C03 | R3 | unit | in Drive and folder notes, a line by a person outside the team becomes a fact on that person ("said they will ...") with the note file + line as source, only when exactly one contact matches; never a commitment, never a new contact (D108) | PASS | passed |
| R3-C04 | R3 | unit | an alias two people list in TEAM_NAME_ALIASES maps to nobody, in any order of the setting, with one team_alias_ignored warning (D102) | PASS | passed |
| R3-S03 | R3 | api | a second add of the same person and company is a 409 "Already added by <who>"; the claim key (company, name) refuses the add even when the in-function check is passed, and rolls back the decision (D111) | PASS | passed |
| R3-S04 | R3 | api | undo, a cancelled push and a push that failed before the write free the claim; a failed add with a saved plan keeps it until the undo (D111, D86) | PASS | passed |
| R3-S05 | R3 | api | the web answer names who added the person; a person only in the sheet keeps the old text (D111) | PASS | passed |
| R3-S06 | R3 | unit | the row-add advisory lock is on the name, not the company, so two adds of one person in two companies cannot both pass the duplicate check (D111) | PASS | passed |
| R3-N04 | R3 | unit | news_seen has an index on story_id, so the nightly story cleanup does not scan news_seen per story (D111) | PASS | passed |
| R3-Q01 | R3 | unit | low memory runs browser QA as one process per width (1440 all groups, 1024 and 390 views only); the merged result fails if any width fails or crashes (D112) | PASS | passed |
| R3-Q02 | R3 | unit | step 6 first removes `qa:` seed rows that a stopped run left behind, names the count, and fails if the teardown fails (D112) | PASS | passed |
| R4-P01 | R4 | unit | the weekly-pack cron fires once Mon 14:00 IST (mocked clock); pack = Scorecard | PASS | passed |
| R4-P02 | R4 | unit | "last week N" is the last Monday snapshot | PASS | passed |
| R4-P03 | R4 | bot | the pack posts in the test channel with [TEST]; read back, then delete | PASS | exit 0 2026-10-20T19:12:49.164801Z [info     ] egress_call                    host=discord.com method=DELETE ms=406 status=204 |
| R4-A01 | R4 | guardrails | sign-in - allowlisted in, others refused, no /api without a session | PASS | passed |
| R4-Q01 | R4 | bot | @Saley answers from the DB only, with per-user seen | PASS | passed |
| R4-Q02 | R4 | bot | live @Saley answer in the test channel; read back, then delete | PASS | exit 0 2026-10-20T19:12:53.536651Z [info     ] egress_call                    host=discord.com method=DELETE ms=437 status=204 |
| R4-C01 | R4 | guardrails | caps per person (D132) - 20 queued for one person give <= 4 a day for that person (DMs and @mentions together), >= 2 h apart; a channel post with no mention counts for nobody; a meeting in < 24 h skips the gap; Saturday 0 except meeting / reconnect | PASS | passed |
| R4-C02 | R4 | guardrails | approver instruction writes with a signature; a non-approver gets a polite refusal, no write | PASS | passed |
| R4-C03 | R4 | guardrails | EMAIL_SEND_ENABLED=false sends to the sink file, never SMTP | PASS | passed |
| R4-B01 | R4 | browser | sign-in page | PASS | passed |
| R4-B02 | R4 | browser | weekly pack page | PASS | passed |
| R4-B03 | R4 | browser | @Saley card on the dashboard | PASS | passed |
| R4-H01 | R4 | unit | Sid's hours 14:00-24:00 IST bound his posts (D126); a post outside waits for the next start | PASS | passed |
| R4-S01 | R4 | bot | the docs/09 shapes are live in test mode; BOT_SHAPES_DRAFT is retired; on the laptop every shape goes to the test channel only (D127) | PASS | passed |
| R4-G00 | R4 | unit | gate runs while a dev server is up on WEB_PORT - steps 7-9 use GATE_PORT, saley_test and worker slot worker:gate; the dev listener is never stopped (D113) | PASS | passed |
| R4-G01 | R4 | unit | a worker reaps and counts only its own slot; lock names stay global, so two slots never run one job at once (D113) | PASS | passed |
| R4-A02 | R4 | api | /api/pack and /api/scorecard return allowed fields only; a bad week gives 422; no session gives 401 | PASS | passed |
| R4-P04 | R4 | unit | Scorecard shows "last week N" per industry from the snapshot 7 days back; no snapshot shows "—" | PASS | passed |
| R4-A03 | R4 | guardrails | with Google configured, dev mode is off - mock sign-in 404, X-Saley-As refused, no loopback bypass | PASS | passed |
| R4-A04 | R4 | guardrails | the callback refuses a bad state, a wrong aud or iss, an unverified email; the cookie is HttpOnly and SameSite=Lax; expiry or removal from the allowlist ends the session | PASS | passed |
| R4-Q03 | R4 | guardrails | an unknown question gets the docs/09 unknown line; no answer holds a value that is not in the DB rows; every answer has citations and the window + seen footer | PASS | passed |
| R4-C04 | R4 | guardrails | per item, 1 reminder + 1 on the due day, then the weekly pack only; no bot, pack or word-file text contains "overdue" | PASS | passed |
| R4-C05 | R4 | guardrails | the same instruction in chat and on the dashboard gives one write; the second answer says "already done" (D24) | PASS | passed |
| R4-S02 | R4 | guardrails | test and live use one render path; only the [TEST] tag and the channel differ; with SALEY_ENV=dev a channel other than the test channel is refused | PASS | passed |
| R4-D01 | R4 | guardrails | DM_ENABLED=false - no daily brief DM is sent and the health line says "DMs off" (D128) | PASS | passed |
| R4-D02 | R4 | guardrails | DM_USERS leaves a person out - that person gets no DM; empty DM_USERS = nobody (D128) | PASS | passed |
| R4-D03 | R4 | unit | the daily brief goes at 14:00 IST to two users in DM_USERS (mocked clock), not before (D128) | PASS | passed |
| R4-E01 | R4 | bot | Edit in the Discord modal writes the edited text with a signature, surface=discord (D129) | PASS | passed |
| R4-E02 | R4 | api | the same card edited on the dashboard after Discord - the second saver gets "already done by X at HH:MM", one write only (D129) | PASS | passed |
| R4-P05 | R4 | unit | on a mocked Monday the pack posts at 14:00 IST and the blocker note at 16:00 (BLOCKER_NOTE_DELAY_MINUTES), at most 5 lines with owner and what it blocks (D130) | PASS | passed |
| R4-Q04 | R4 | guardrails | answer citations are by name ([sheet - person], [note date, company], [news - title]); no citation is a digits-only reference (D131) | PASS | passed |

## Summary

GATE: PASS

Env changes:
- NEW: `GATE_PORT` (8091, D113: the gate's own web against saley_test), `WORKER_SLOT` (worker:main; the gate uses worker:gate, D113), `AUTH_PROVIDER` (auto), `GOOGLE_OAUTH_REDIRECT_URL` (http://localhost:8090/auth/callback), `SESSION_HOURS` (12, 1 to 720), `BLOCKER_NOTE_DELAY_MINUTES` (120, 0 to 1440; D130), `BOT_POSTS_PER_PERSON_PER_DAY` (4; D132), `BOT_POST_GAP_MINUTES` (120; D132), `USER_HOURS` (sid:14:00-24:00; D126), `DAILY_BRIEF_TIME` (14:00; D128), `DM_ENABLED` (false; D128), `DM_USERS` (sid,vaishnavi,kushal,trishi; D128), `DISCORD_USER_MAP` (empty; D135), `SMTP_HOST` (smtp.gmail.com), `SMTP_PORT` (587), `EMAIL_FROM` (empty), `SMTP_APP_PASSWORD` (<REQUIRED>, used only with the send flag; D124)
- CHANGED-default: none
- RETIRED: `BOT_SHAPES_DRAFT` (never read by code; D127)
