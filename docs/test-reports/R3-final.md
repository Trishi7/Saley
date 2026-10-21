GATE: PASS

# /verify R3 · 20 Oct 2026 14:37 IST

| Step | Check | Skill or agent | Result | Note |
| --- | --- | --- | --- | --- |
| 1 | Static: tidy --check, py_compile, ruff check, ruff format --check | verification-loop | PASS | no stray file; 133 files compile; ruff check and format clean |
| 2 | Tests, coverage, new files >= 80 %, a matrix row per feature | pytest, tdd-guide | PASS | 1282 passed, coverage 97 %, every new file >= 80 %, 71 matrix rows (R1.1, R2, R3) |
| 3 | STE lint: <= 60 characters, no banned words | saley.qa.ste_lint | PASS | STE lint: 677 strings, 0 findings |
| 4 | Sheet robustness tests | python-testing | PASS | 12 robustness tests passed |
| 5 | API contract and abuse suite | security-review | PASS | 147 of 147 passed; R3-api-abuse.md |
| 6 | saley sheet verify | dashboard-parity | PASS | 51 checks OK, 0 DIFF; R3-sheet-verify.md |
| 7 | saley dev: first sync within SHEET_SYNC_SECONDS | saley dev | PASS | Updated 14:33 IST · sheet 14:33 · news — (age 4 s, limit 60 s) |
| 8 | Browser QA, read-only | browser-qa | PASS | verdict SHIP, 1410 checks, 60 screenshots in docs/test-reports/R3-screens; low memory (4213 MB free): one width per run |
| 9 | Click-path audit: no non-GET request | click-path-audit | PASS | 9 POSTs, all allowed, each from a [data-act] control; 15 seen marks after list render; no form, no submit button |
| 10 | Reviewers on the release diff | code, python, database, security, silent-failure | PASS | reviewers: security, database, silent-failure, python, fastapi, code on the R3 diff; database + python re-review of 0016 (D111) and the gate scripts (D112); 0 CRITICAL/HIGH open; D102 alias fix with regression test; mediums and lows listed in STATUS R3 detail |
| 11 | Docs: README, .env.example, docs/08 appendix, STATUS | doc-updater | PASS | docs: README R3 run notes; .env.example checked (LLM comment fixed); docs/08 Appendix A R3 endpoints + duplicate text; docs/16 low-memory line; STATUS R3 detail + env rows; DECISIONS D111-D113; QUESTIONS Q-R3-9; CLAUDE.md + verify.md rules (D112, D113) |
| 12 | Finalize | scripts/verify.py | DONE | GATE: PASS |

## Test matrix

71 of 71 rows passed (R1.1, R2, R3).

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
| R2-W02 | R2 | sheet | live round trip in SALEY_TEST - create tab, write, read back, undo, delete tab (always) | PASS | exit 0 2026-10-20T09:06:15.289054Z [info     ] egress_call                    host=sheets.googleapis.com method=GET status=200 |
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
| R2-N05 | R2 | sheet | live read-only Drive sync of the notes folder through egress (never a write) | PASS | exit 0 2026-10-20T09:06:19.891168Z [info     ] team_aliases_loaded            aliases=7 users=4 |
| R3-N01 | R3 | unit | scoring is deterministic - the same story scores the same 3 times | PASS | passed |
| R3-N02 | R3 | unit | the same story from 2 sources becomes 1 signal | PASS | passed |
| R3-G01 | R3 | guardrails | the validator rejects a URL, name or number not in the input facts | PASS | passed |
| R3-G02 | R3 | guardrails | linkedin.com fetch is blocked in 3 URL shapes | PASS | passed |
| R3-G03 | R3 | guardrails | a host outside EGRESS_ALLOWLIST is blocked | PASS | passed |
| R3-U01 | R3 | api | seen per user - A sees, B still sees, A gets "left out 1 you saw" | PASS | passed |
| R3-S01 | R3 | sheet | add-to-pipeline row_add lands with a signature (fake sheet) and shows after one sync | PASS | passed |
| R3-S02 | R3 | sheet | live row_add in SALEY_TEST with signature, then the tab is deleted | PASS | exit 0 2026-10-20T09:07:30.314529Z [info     ] egress_call                    host=sheets.googleapis.com method=GET status=200 |
| R3-A01 | R3 | api | news funnel fetched >= deduped >= scored >= linked, each number links to its list | PASS | passed |
| R3-L01 | R3 | guardrails | LLM calls stay <= LLM_DAILY_CALL_CAP and a repeat is a cache hit | PASS | passed |
| R3-A02 | R3 | api | Signals All by range and score, Linked, Events, Suggestions endpoints | PASS | passed |
| R3-B01 | R3 | browser | Signals, Events, Suggestions pages and Today "needs a look" / "Saley suggests" | PASS | passed |
| R3-G04 | R3 | guardrails | Apollo stays off and Sources and health says "off" | PASS | passed |
| R3-L02 | R3 | guardrails | one live LLM call scores one fixture story, logged in llm_calls, under the cap | PASS | exit 0 already proven 15:05 19 Oct 2026 (claude-haiku-5-5) |
| R3-G05 | R3 | guardrails | SearXNG then DuckDuckGo; only result URLs stored; a linkedin.com result is never fetched | PASS | passed |
| R3-N03 | R3 | unit | live fetch, 1 request per source kind, 3 items max, no scoring, through egress | PASS | exit 0 2026-10-20T09:07:44.196560Z [info     ] egress_call                    host=api.openalex.org method=GET ms=420 status=20 |
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

## Summary

GATE: PASS

Env changes:
- NEW: `NEWS_EDITIONS` (06:30,12:00,17:00,22:00), `NEWS_USER_AGENT` (SaleyNewsReader/3.0 (+https://membrane.social; contact news@membrane.social)), `NEWS_ITEMS_PER_FETCH` (50), `NEWS_RAW_LOG` (false), `NEWS_STALE_DAYS` (3), `SCORER_BACKEND` (llm; `rules` or `llm`), `SCORER_BATCH` (40), `SCORER_EDITION_CALL_CAP` (6), `OPENALEX_MAILTO` (empty; the human's email goes in `.env` only, D83), `ACTION_HINT_DAILY_CAP` (40, D79), `SEARXNG_URL` (empty = DuckDuckGo only, Q-R3-2), `SEARCH_DAILY_CAP` (50), `SEARCH_MIN_GAP_SECONDS` (5, D82), `SEARCH_CACHE_DAYS` (30, D82), `TEAM_NAME_ALIASES` (sid:Siddarth,Siddharth,Sid;vaishnavi:Vaishnavi,VR;kushal:Kushal;trishi:Trishi, D108), `MEETING_NOTES_DRIVE_FOLDER_ID` (empty, D81; not in the R2 row), `NOTES_SYNC_MINUTES` (15, D81; not in the R2 row)
- CHANGED-default: `LLM_SMALL` claude-haiku-4-5 → `claude-haiku-5-5`; `LLM_MID` claude-sonnet-4-5 → `claude-sonnet-5-5` (D80, Q-R3-1); `EGRESS_ALLOWLIST` +html.duckduckgo.com (Q-R3-3), +www.googleapis.com (Drive notes, D81; not in the R2 row, see R3 detail)
- RETIRED: none
