# 16 · Verification standard

How every release from R2 on is built, checked and handed over. The chain ticket is in
the chain ticket in the appendix of this file. The gate is `/verify` (.claude/commands/verify.md, scripts/verify.py).
Hygiene is `/tidy` (.claude/commands/tidy.md, scripts/tidy.py).

Rules: no feature without a matrix row. No release without GATE: PASS on its matrix plus all
earlier ones. Every release ends with `/verify`, then `/tidy`.

## 1 · The build pipeline (every release, same order, no skips)

| Stage | Who | Output | Done when |
|---|---|---|---|
| 0 Tidy-in | `scripts/tidy.py --check` | clean tree | no stray files; caches deleted |
| 1 Plan | agent `planner` | docs/plans/<release>.md | scope, matrix rows to add, decisions needed, needs from the human |
| 2 Build | agent `tdd-guide`; skills python-patterns, fastapi-patterns, postgres-patterns, database-migrations; UI: make-interfaces-feel-better, frontend-design-direction | code + tests + matrix rows | every feature has a tests/matrix/<release>.yaml row |
| 3 Review | agents code-reviewer, python-reviewer, fastapi-reviewer, database-reviewer, security-reviewer, silent-failure-hunter | review notes in the test report | zero open "must fix" |
| 4 Verify | agent `saley-tester` runs `scripts/verify.py --release <id>` (the 12 gate steps + the matrices up to this release) | docs/test-reports/<release>-final.md on GATE: PASS, else <release>-<date>.md (D236) | GATE: PASS |
| 5 Fix | agent `build-error-resolver` | regression test per fix | back to stage 3, then 4 from step 1 |
| 6 Tidy-out | `/tidy` (report-only refactor-cleaner) | tidy-<date>.md | "tidy: nothing broke" |
| 7 Docs | agent `doc-updater`; skill living-docs-governance | README, .env.example, docs/08 appendix, DECISIONS, STATUS, QUESTIONS | all current |
| 8 Ping | `python .claude/hooks/notify.py --release <id> "<line>"` | one Discord line | only after GATE: PASS and tidy clean |
| 9 Next | — | STATUS.md "current" = next release | start at stage 0 |

Report names (D236): a finalize with GATE: PASS writes `docs/test-reports/<release>-final.md`.
Every other run (steps 1–9, or a finalize that fails) writes `docs/test-reports/<release>-<date>.md`.
The dated reports are run records. At a tidy stop, after a human yes, they move to `.scratch/`
(as on 21 Oct). The final report stays.

## 2 · The gate (scripts/verify.py, 12 steps, the same for every release)

1. Static: `scripts/tidy.py --check`, py_compile all files, `ruff check`, `ruff format --check`.
   From R5 also the web app (D144, D145): `npm --prefix web run lint` (eslint),
   `npm --prefix web run typecheck` (tsc), `npm --prefix web test` (web unit tests), and the
   build stamp (a stale `dist` fails with "Run npm --prefix web run build").
2. `pytest -q` on saley_test: 100 % pass (a skipped test does not pass); no new source file
   under 80 % coverage; the matrices load with no schema error; every feature in
   docs/plans/<release>.md (`| F-<release>-NN |` rows) has a matrix row, else FAIL.
3. STE lint: labels ≤ 60 characters; no word from docs/ste-banned-words.txt.
4. Sheet robustness: insert column, rename header, delete row, move row, value changed mid-sync.
5. API contract + abuse suite: declared fields only, limits, bad input rejected, no secrets in
   any response or error.
6. `saley sheet verify` (dashboard-parity): sheet = DB = dashboard; any DIFF fails.
7. The gate's own `saley dev` (D113) on `GATE_PORT` (8091) against `saley_test`, worker slot
   `worker:gate`; `WEB_PORT`, the dev database and the dev worker are never touched. The
   gate holds the test-database lock while it serves. First sync within SHEET_SYNC_SECONDS; a busy port names the
   program that holds it.
8. Browser QA on http://localhost:<GATE_PORT>: every docs/08 view, every tile click, 5 fixed
   search terms, 3 fixed people, widths 1440 / 1024 / 390, zero console errors, screenshots to
   docs/test-reports/<release>-screens/, verdict SHIP. The browser touches localhost and the
   Discord test channel only. Low memory (D112): below 3072 MB free, or with `--low-memory`,
   one browser process per width (1440 runs every group; 1024 and 390 the views only), merged.
9. Click-path audit on every state-changing control (none in R1; live from R2).
10. Reviewers on the full diff since the last release: no open "must fix".
11. Docs current (stage 7 list). .env.example: every variable uncommented with its default;
    human-only values as `<REQUIRED: …>`; one RETIRED block. The human or the tester records
    step 11; the finalize then checks two things itself (D236) and turns a recorded PASS into
    FAIL when one is missing: the docs/STATUS.md env log has a NEW, a CHANGED-default and a
    RETIRED row for the release, and, from R5 on, `docs/test-reports/<release>-impeccable.json`
    passes `scripts/impeccable_report.py --check` (every page, both themes, no open critical).
    A matrix row that runs `step 10` or `step 11` (R5-D01) is read again at the finalize, from
    the final step results (D236).
12. Finalize: `GATE: PASS`, or `GATE: FAIL — step <n> · first failing matrix row <id>` at the
    top. From R5 the report has the Impeccable score table (`impeccable_report.py --table`).
    The summary ends with NEW / CHANGED-default / RETIRED env variables.

After step 9 the gate evaluates every matrix row (§3). A failing row fails the gate.

Loader rule: `--release R3` runs the matrices R1.1 + R2 + R3. Without `--release` the gate uses
the line `Current release: <id>` in docs/STATUS.md.

## 3 · Test matrices (tests/matrix/<release>.yaml)

Schema per row: `id`, `feature`, `layer`, `what`, `tool`, `command`, `pass`.
Layers: `unit` (unit + integration), `sheet`, `api`, `browser`, `bot`, `guardrails`.
Tools (saley.qa.matrix):

| Tool | Command | Passes when |
| --- | --- | --- |
| pytest | `pytest <node id> …` (a test, a parametrised test, or a whole file) | every named test ran in gate step 2 and passed |
| gate | `step <n>` | that gate step passed |
| browser | `browser <check name>` | the named check of scripts/browser_qa.py passed in step 8 |
| shell | an argv | exit 0. Exit 3 with `NOT CONFIGURED: Q-<id>` = PENDING on that open question |

The release itself needs its own `tests/matrix/<release>.yaml` and a docs/plans/<release>.md
with at least one `| F-<release>-NN |` feature row; a missing one fails step 2.
A `browser` row reads the `named` checks that scripts/browser_qa.py prints (R1.1 names:
`views`, `tiles`, `search`, `drawers`, `click_path`).

PENDING is only for a live check whose human-only need is missing (a secret, a share, an id).
The question must be open in docs/QUESTIONS.md, else the row fails. A PENDING row shows as
PENDING in the report, is listed in the summary, and is named in the release ping. A PENDING row is
never a substitute for the offline test of the same feature: every feature also has a pytest
row that runs without the live system.

Rules:
- Write tests run only against the scratch tab `SALEY_TEST` in the sheet copy: create tab →
  write → read back → undo → delete tab. Never Outreach PoCs rows. Tear-down runs on failure too.
- Bot tests run in the test channel with `[TEST]`; each test reads back and deletes its own post.
- LLM tests use recorded fixtures in tests/fixtures/llm/; one live call per release, counted.
- Set-up and tear-down always run, also on failure.
- Never edit a matrix row to make it pass; a wrong row is a docs/QUESTIONS.md entry.
- Three fails on one step → docs/QUESTIONS.md + "blocked" ping; continue with other work.

## 4 · Hygiene (/tidy)

- Stage 0, `--check`: untracked files outside the keep patterns fail; caches are deleted by the
  full run (`__pycache__`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.hypothesis`,
  `htmlcov`, `.coverage*`).
- Stage 6, full run: sort untracked files into keep / by-product / scratch; move scratch to
  `.scratch/` after a yes (never delete); refactor-cleaner report-only (`ruff --select
  F401,F841`, `vulture src/saley tests --min-confidence 80`) to docs/test-reports/tidy-<date>.md;
  after a move: py_compile + ruff + pytest; any failure moves the files back ("tidy: reverted").
- Never touch tracked files, imported files, or files named in pyproject.toml, alembic.ini,
  `.claude/`, `docs/`.
- Temporary files go in `.scratch/` or docs/test-reports/<release>-screens/ only.

## 5 · Chain rules (summary; full text in the appendix of this file)

- Order R2 → R3 → R4. The next release starts only after GATE: PASS, tidy clean and the ping.
- A need from the human (secret, id, decision) never stops the chain: a QUESTIONS.md row (id,
  release, what, what was built instead), a default-off flag or a "not configured" stub, one
  "blocked" ping, then continue.
- Hard stops: a live sheet write, an email send, a post outside the test channel, spend above
  LLM_DAILY_CALL_CAP. Stop that step, QUESTIONS.md, "blocked" ping, continue elsewhere.
- Decisions made alone are "proposed" rows in DECISIONS.md with a reason, listed in the ping.
- Never commit. The human commits after each ping.

## Appendix · Chain ticket R2 → R3 → R4 (received 2026-10-19; verbatim)

Moved here from `docs/plans/chain.md` at the R5 T16 tidy stop (21 Oct). Text unchanged; headings one level down. "PART 6" below holds the R2, R3 and R4 scopes.


Chain mode with the full build standard: build R2, then R3, then R4, one after the other, without waiting for me. Work on main. Do not create a branch. Every rule in CLAUDE.md stays in force.

### PART 1 — The build pipeline (every release, same order, no skips)

| Stage | Who | Output | Done when |
|---|---|---|---|
| 0 Tidy-in | `scripts/tidy.py --check` | clean tree | no stray files; caches deleted |
| 1 Plan | agent `planner` | docs/plans/<release>.md | scope, matrix rows to add, decisions needed, needs from me |
| 2 Build | agent `tdd-guide`; skills python-patterns, fastapi-patterns, postgres-patterns, database-migrations; UI: make-interfaces-feel-better, frontend-design-direction | code + tests + matrix rows | every feature has a tests/matrix/<release>.yaml row |
| 3 Review | agents code-reviewer, python-reviewer, fastapi-reviewer, database-reviewer, security-reviewer, silent-failure-hunter | review notes in the test report | zero open "must fix" |
| 4 Verify | agent `saley-tester` runs `scripts/verify.py --release <id>` (the 12 gate steps + the matrices up to this release) | docs/test-reports/<release>-<date>.md | GATE: PASS |
| 5 Fix | agent `build-error-resolver` | regression test per fix | back to stage 3, then 4 from step 1 |
| 6 Tidy-out | `/tidy` (report-only refactor-cleaner) | tidy-<date>.md | "tidy: nothing broke" |
| 7 Docs | agent `doc-updater`; skill living-docs-governance | README, .env.example, docs/08 appendix, DECISIONS, STATUS, QUESTIONS | all current |
| 8 Ping | `python .claude/hooks/notify.py --release <id> "<line>"` | one Discord line | only after GATE: PASS and tidy clean |
| 9 Next | — | STATUS.md "current" = next release | start at stage 0 |

Write this table into docs/16-verification-standard.md §1 and into .claude/commands/verify.md if not already there.

### PART 2 — Verify gate (scripts/verify.py, 12 steps, unchanged across releases)
1. Static: py_compile all files, `ruff check`, `ruff format --check`, `scripts/tidy.py --check`.
2. `pytest -q` on saley_test: 100 % pass; no new file under 80 % coverage; every feature in the plan has a matrix row, else FAIL.
3. STE lint: labels ≤ 60 chars; no word from docs/ste-banned-words.txt.
4. Sheet robustness: insert column, rename header, delete row, move row, value changed mid-sync.
5. API contract + abuse suite: allowed fields only, limits, bad input rejected, no secrets in any response or error.
6. `saley sheet verify` (dashboard-parity): sheet = DB = dashboard; any DIFF fails.
7. `saley dev` up; first sync within SHEET_SYNC_SECONDS; port from WEB_PORT, named process on conflict.
8. browser-qa on http://localhost:${WEB_PORT}: every view in docs/08, every tile click, 5 fixed search terms, 3 fixed people, widths 1440/1024/390, zero console errors, screenshots to docs/test-reports/<release>-screens/, verdict SHIP. Browser touches localhost and saley-test-v2 only.
9. click-path-audit on every state-changing control (none in R1; live from R2).
10. Reviewers on the full diff since the last release tag: no open "must fix".
11. Docs current (stage 7 list). .env.example: every variable uncommented with default; human-only as <REQUIRED: …>; RETIRED block.
12. GATE: PASS or GATE: FAIL with the failing step and first failing matrix row id at the top. Summary ends with NEW / CHANGED-default / RETIRED env vars.

Loader rule: `--release R3` runs matrices R1.1 + R2 + R3. Default release = STATUS.md "current".

### PART 3 — Test matrices (tests/matrix/<release>.yaml; schema: id, layer, what, tool, command, pass rule)
Six layers: unit+integration, sheet, API, browser, bot, guardrails. Rules:
- Write tests run only against a scratch tab SALEY_TEST in the sheet copy: create tab → write → read back → undo → delete tab. Never Outreach PoCs rows.
- Bot tests run in saley-test-v2 with [TEST]; each test reads back and deletes its own post.
- LLM tests use recorded fixtures in tests/fixtures/llm/; one live call per release, counted.
- Set-up and tear-down always run, also on failure.
- Never edit a matrix row to make it pass; a wrong row is a QUESTIONS.md entry.
- Three fails on one step → QUESTIONS.md + "blocked" ping, continue with other work.

R2 rows (minimum): write round trip (cell + decision row + Name note, read back equal) · undo inside UNDO_MINUTES restores, after window refused · locked-column write → 403 + log · email column refused while EMAIL_WRITE_ALLOWED=false · two users on one row within 2 s → second gets "already done by X at HH:MM" · same instruction twice (chat + dashboard) = one write (D24) · web process has no write credential · my tasks per person (Sid LI, Vaishnavi research, Kushal approvals; D31) · Inbox card once, dismissed never returns · click-path-audit Done/Undo/Confirm/dismiss · parity after 10 random writes + 5 undos.

R3 rows (minimum): scoring determinism ×3 · dedupe 2 sources → 1 signal · validator rejects URL/name/number not in input facts · linkedin.com fetch blocked, 3 URL shapes · host outside EGRESS_ALLOWLIST blocked · seen per user (A sees, B still sees, A gets "left out 1 you saw") · add-to-pipeline row_add lands in SALEY_TEST with signature, visible after one sync · news funnel fetched ≥ deduped ≥ scored ≥ linked, each number clickable · calls ≤ LLM_DAILY_CALL_CAP, cache hit on repeat.

R4 rows (minimum): cron fires once Mon 14:00 IST (mocked clock), pack = Scorecard, post in test channel with [TEST] · "last week N" = last Monday snapshot · sign-in: allowlisted in, others refused, no /api without session · @Saley answers from DB only, per-user seen · caps: 20 queued → ≤ 3/day, ≥ 2 h apart, Saturday 0 except meeting/reconnect · approver instruction → write with signature; non-approver → polite refusal, no write · EMAIL_SEND_ENABLED=false → sink file, never SMTP · browser-qa: sign-in page, weekly pack page, @Saley card.

### PART 4 — Hygiene (/tidy, scripts/tidy.py)
- Stage 0 `--check`: untracked files outside keep patterns fail; caches (__pycache__, .pytest_cache, .ruff_cache, .mypy_cache, .hypothesis, htmlcov, .coverage*) deleted.
- Stage 6 full run: sort untracked into keep / by-product / scratch; move scratch to .scratch/ (never delete); refactor-cleaner report-only (`ruff --select F401,F841`, `vulture saley tests --min-confidence 80`) to docs/test-reports/tidy-<date>.md; then py_compile + ruff + pytest; any fail → move back, "tidy: reverted".
- Never touch tracked files, imported files, or files named in pyproject.toml, alembic.ini, .claude/, docs/.
- Temporary files go in .scratch/ or docs/test-reports/<release>-screens/ only.

### PART 5 — Chain rules
1. Order R2 → R3 → R4. Next release only after GATE: PASS, tidy clean, and the ping.
2. Needs from me (secret, id, decision): do not stop. QUESTIONS.md row (id, release, what, what you built instead). Build around it: flag default off, stub "not configured" in Sources & health, or DRAFT in the doc. Ping once `--release <id> "blocked: Q-<id> — <line>; continuing with <next>"`. Continue.
3. My answer arrives as `Input Q-<id>: <answer>` or `Input D<n>: <answer>`. Apply it, re-run only the matrix rows and gate steps it touches, mark resolved, ping `"Q-<id> applied, gate PASS"`. Finish the current stage first.
4. Decisions you make alone → "proposed" row in DECISIONS.md with reason; list them in the ping.
5. Hard stops: live sheet write, email send, post outside the test channel, spend above LLM_DAILY_CALL_CAP. Stop that step, QUESTIONS.md, "blocked" ping, continue elsewhere.
6. Sheet = copy (GTM_SHEET_IS_COPY=true). Discord = saley-test-v2 with [TEST]. EMAIL_SEND_ENABLED=false.
7. LLM calls logged per release; over cap → pause to next day, say so in STATUS.md.
8. After R4: stop. Ping `--release R4 "chain complete; open questions: <ids>"`.
9. Never commit. I commit after each ping.

Known needs: N1 IMAP login (R2) · N2 D13 date anchors (R2) · N3 Apollo cap (R3, stays off) · N4 bot shapes D27 (R4, draft them) · N5 Google OAuth + four emails (R4) · N6 Sid's hours D15 (R4, default 10:00–19:00 IST).

### PART 6 — Release scope

### R2 — writes with signature, my tasks, Inbox (docs/08 §Releases, docs/02 Part C/D, D23–D25, D31)
- Sheet writer: only the worker holds the write credential; allowed columns per docs/10; every write = `decision` row (who, when, surface, before, after) + Name-cell note; undo window UNDO_MINUTES; `POST /api/act`, `/api/decision/{id}/confirm`, `/api/undo/{write_id}`.
- Today "my tasks" per signed-in person (contacts due, reconnects due, meetings today, LI connections to confirm); Done → write at once with signature, toast with Undo.
- Inbox: Saley's suggestions as one-click cards, never twice (D24).
- Commitments from meeting notes and mail (IMAP read-only, Membrane cc mailbox; N1 missing → "mail: not configured", notes only). Politeness caps from docs/02 Part C.
- Decisions view: `decision` table, filters person / surface / undo state.
- People drawer: "working on" from facts; email/LinkedIn links.
- click-path-audit live on Done, Undo, Confirm, dismiss.

### R3 — intelligence and news (docs/03, 04, 05, docs/02 Part D)
- News stage 1: Google News RSS + OpenAlex + arXiv; fixed-schema LLM scoring with DATA blocks; dedupe; link to companies and people; cache LLM_CACHE_DAYS.
- Signals All (day/week/month/year, by score) and Linked; Events; Suggestions with add-to-pipeline → `row_add` with signature.
- Today: "needs a look" and "Saley suggests" after my tasks.
- Sources & health: news funnel, failed sources, per-source counts.
- Per-user seen on every list with "left out N you saw".
- SearXNG → DuckDuckGo for profile links only; LinkedIn never fetched; egress allowlist in-process (D32). Apollo off, hook shows "off".

### R4 — weekly pack, real sign-in, @Saley (docs/08, docs/09 DRAFT, docs/02 Part B)
- Weekly pack Mon 14:00 IST: dashboard page + test-channel post; Scorecard deltas ("last week 91").
- Google sign-in with WEB_ALLOWED_EMAILS; dev mode off when configured; N5 missing → mock provider + DEV_USER_EMAIL, Q-N5.
- @Saley answers from the DB with per-user seen; caps 3/day, 2 h gap, weekend silence except meetings and reconnects; allowlists for instruct/approve; approver instruction = write with signature (D24).
- Bot shapes: draft all in docs/09 as DRAFT with examples, behind BOT_SHAPES_DRAFT=true (test channel only), list in the ping (N4, D27).
- Sid's hours default 10:00–19:00 IST (N6).

### PART 7 — Start
1. Write docs/16-verification-standard.md, the four matrices, verify.py loader, tidy.py if missing, and the CLAUDE.md lines: "No feature without a matrix row. No release without GATE: PASS on its matrix plus all earlier ones. Every release ends with /verify then /tidy."
2. Run `scripts/verify.py --release R1.1` to prove the loader. Must PASS.
3. Save this whole message as docs/plans/chain.md.
4. Begin R2 at stage 0.
