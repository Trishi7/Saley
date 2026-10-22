---
description: Standing release gate. Runs the 12 steps in order and writes docs/test-reports/<release>-final.md on GATE PASS (else <release>-<date>.md). GATE PASS is required before the release ping.
argument-hint: <release id, e.g. R1.1>
---

# /verify — the release gate

Run this after every build, in every release. A release is done only when the report says
`GATE: PASS`. Send `python .claude/hooks/notify.py --release <id> "<one line>"` only after
GATE: PASS. Never ping on a partial pass. Never skip a step.

If a step fails: fix it, add a regression test, and run `/verify` again from step 1.

Memory (D112). Never wait for the human to rerun the gate. When the run stops for low RAM:
1. Stop the stale processes you started: `saley dev`, `saley worker`, pytest, and msedge or
   chromium with a Playwright profile (`playwright_` in the command line). Never another program.
   The gate starts `python -m saley.cli dev` and its `-m saley.cli worker` child. A `saley.exe dev`
   (from a terminal) is the human's server: never stop it; if it holds the port, ask (Q-R3-9).
2. Run the gate again from step 1 with `--low-memory`: browser QA runs one browser process per
   width (1440 all groups; 1024 and 390 views only) and merges the results. Below `LOW_RAM_MB`
   (3072 MB) free, `scripts/verify.py` does this by itself; step 8 names the free RAM.
3. After three memory failures in a row: write `Q-<release>-mem` in `docs/QUESTIONS.md` with
   the free-RAM number, ping "blocked", and go on with the next stage that needs no browser.
Laptop rule (D142): at most one background agent or test run at a time. Never run the full
suite while a build agent is still working. pytest always with `-x -p no:cacheprovider`.
On this laptop always pass `--low-memory` to `scripts/verify.py` (one browser width per run).
After GATE: PASS: `/tidy`, docs, the release ping, STATUS "Current release" = next release, and
start it at stage 0 without asking.
If a fix needs a human decision: write it in `docs/QUESTIONS.md`, then ping with "blocked":
`echo '{"message":"blocked: <question>"}' | python .claude/hooks/notify.py`.

The agent that runs this command is `saley-tester` (`.claude/agents/saley-tester.md`).

## The build pipeline (docs/16 §1) (every release, same order, no skips)

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

## Skills and agents for each step

| When | Use |
|---|---|
| Before coding | agent `planner` — plan in docs/plans/<release>.md |
| While coding | agent `tdd-guide`; skills `python-patterns`, `fastapi-patterns`, `postgres-patterns`, `database-migrations` |
| UI changes | skills `make-interfaces-feel-better`, `frontend-design-direction` |
| Sheet robustness | skill `python-testing` for the fixtures |
| API hardening | skills `security-review`, `verification-loop`; agents `fastapi-reviewer`, `security-reviewer` |
| Gate step 3 STE lint | our rule in CLAUDE.md ("plain, short sentences, ASD-STE100") |
| Gate step 6 | our skill `dashboard-parity` |
| Gate step 8 | ECC skill `browser-qa`, read-only mode |
| Gate step 9 | ECC skill `click-path-audit` |
| Gate step 10 | agents `code-reviewer`, `python-reviewer`, `database-reviewer`, `security-reviewer`, `silent-failure-hunter` |
| Gate step 11 | agent `doc-updater`; skill `living-docs-governance` |
| Build errors at any point | agent `build-error-resolver` |
| Whole gate | our agent `saley-tester` runs `/verify` |

The report names the skill or agent used for each step.

## Procedure

`$ARGUMENTS` is the release id (for example `R1.1`). Shell: `export PATH="$HOME/.local/bin:$PATH"`.

1. Automated steps 1–9:
   ```
   uv run python scripts/verify.py --release $ARGUMENTS --low-memory
   ```
   It runs, in order, and stops nothing on the first failure (every step gets a result):
   1. Static: first `python scripts/tidy.py --check` (a stray untracked file fails the gate
      and the list is in the note; see `/tidy`), then `python -m py_compile` on every Python
      file, `ruff check`, `ruff format --check`. From R5 also the web app: eslint
      (`npm --prefix web run lint`), tsc (`npm --prefix web run typecheck`), the web unit
      tests (`npm --prefix web test`) and the build stamp ("Run npm --prefix web run build").
   2. Tests on `saley_test`: `pytest`, 100 % pass, coverage report; no new source file under 80 %.
      The matrices load with no error and every plan feature (`| F-<release>-NN |`) has a row.
      After step 9 every matrix row of this release and the earlier ones is evaluated
      (docs/16 §3); `--release` defaults to `Current release:` in docs/STATUS.md.
   3. STE lint: every UI label, empty state, tooltip and bot string ≤ 60 characters; no word
      from `docs/ste-banned-words.txt`.
   4. Sheet robustness tests: insert column, rename header, delete row, move row, value
      changed mid-sync (`tests/test_sales.py -k robust`).
   5. API contract and abuse suite (`tests/test_api_abuse.py`); results also in
      `docs/test-reports/<release>-api-abuse.md`.
   6. `saley sheet verify` (skill `dashboard-parity`): recompute from the sheet copy
      (read-only), compare with the API, table metric · sheet · API · OK/DIFF.
   7. Start the gate's own `saley dev` (D113) on `GATE_PORT` (default 8091) against the
      `saley_test` database, worker slot `worker:gate`. It never touches `WEB_PORT`, the dev
      database or the dev worker, so a human's `saley dev` may keep running. The gate rebuilds
      `saley_test` at head and holds the test-database lock until its server stops.
      First a pre-check: if `GATE_PORT` is in use, step 7 fails and the error names the program
      (and the Windows service) that holds it. Wait for the first sync. "Updated HH:MM" must be
      within SHEET_SYNC_SECONDS.
   8. Browser QA (skill `browser-qa`, read-only) on http://localhost:<GATE_PORT>: every docs/08 view,
      every tile click, the 5 terms in `tests/fixtures/search-terms.txt`, 3 People drawers,
      widths 1440 / 1024 / 390, zero console errors, screenshots in
      `docs/test-reports/<release>-screens/`. Verdict SHIP required.
      Browser use: localhost and the Discord test channel only. Never Google Sheets, Gmail
      or LinkedIn.
   9. Click-path audit (skill `click-path-audit`) on every button that changes state.
      R1 has none: the script proves the page sends no non-GET request.
2. Step 10: run the five reviewer agents on the full diff since the last release tag in
   `docs/STATUS.md`. Fix every "must fix" (CRITICAL or HIGH), then record:
   ```
   uv run python scripts/verify.py --release $ARGUMENTS --record 10 PASS "reviewers: ... no open must-fix"
   ```
   If you changed code, run `/verify` again from step 1.
3. Step 11: agent `doc-updater` with skill `living-docs-governance`: README, `.env.example`
   (every variable uncommented with its default; human-only as `<REQUIRED: …>`; retired ones
   in one commented RETIRED block), docs/08 appendix (endpoint list), `docs/STATUS.md` (with a
   NEW, a CHANGED-default and a RETIRED row for the release in the env log). From R5 on,
   `uv run python scripts/impeccable_report.py --check --release $ARGUMENTS` must pass. Record:
   ```
   uv run python scripts/verify.py --release $ARGUMENTS --record 11 PASS "docs: ..."
   ```
4. Step 12:
   ```
   uv run python scripts/verify.py --release $ARGUMENTS --finalize
   ```
   It writes `GATE: PASS` or `GATE: FAIL — step N` at the top of the report. On PASS the
   report is `docs/test-reports/<release>-final.md`; else `<release>-<date>.md` (D236).
   The finalize checks step 11 itself (D236): no env rows for the release in `docs/STATUS.md`,
   or (from R5) an incomplete Impeccable scores file, turns step 11 into FAIL. Matrix rows that
   run `step 10` or `step 11` (R5-D01) are read again from these final results. From R5 the
   report has the Impeccable score table. The summary ends with NEW / CHANGED-default /
   RETIRED env variables (from `docs/STATUS.md`).
5. Only on GATE: PASS: `python .claude/hooks/notify.py --release $ARGUMENTS "<one line>"`.
