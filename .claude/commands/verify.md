---
description: Standing release gate. Runs the 12 steps in order and writes docs/test-reports/<release>-<date>.md. GATE PASS is required before the release ping.
argument-hint: <release id, e.g. R1.1>
---

# /verify — the release gate

Run this after every build, in every release. A release is done only when the report says
`GATE: PASS`. Send `python .claude/hooks/notify.py --release <id> "<one line>"` only after
GATE: PASS. Never ping on a partial pass. Never skip a step.

If a step fails: fix it, add a regression test, and run `/verify` again from step 1.
If a fix needs a human decision: write it in `docs/QUESTIONS.md`, then ping with "blocked":
`echo '{"message":"blocked: <question>"}' | python .claude/hooks/notify.py`.

The agent that runs this command is `saley-tester` (`.claude/agents/saley-tester.md`).

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
   uv run python scripts/verify.py --release $ARGUMENTS
   ```
   It runs, in order, and stops nothing on the first failure (every step gets a result):
   1. Static: first `python scripts/tidy.py --check` (a stray untracked file fails the gate
      and the list is in the note; see `/tidy`), then `python -m py_compile` on every Python
      file, `ruff check`, `ruff format --check`.
   2. Tests on `saley_test`: `pytest`, 100 % pass, coverage report; no new source file under 80 %.
   3. STE lint: every UI label, empty state, tooltip and bot string ≤ 60 characters; no word
      from `docs/ste-banned-words.txt`.
   4. Sheet robustness tests: insert column, rename header, delete row, move row, value
      changed mid-sync (`tests/test_sales.py -k robust`).
   5. API contract and abuse suite (`tests/test_api_abuse.py`); results also in
      `docs/test-reports/<release>-api-abuse.md`.
   6. `saley sheet verify` (skill `dashboard-parity`): recompute from the sheet copy
      (read-only), compare with the API, table metric · sheet · API · OK/DIFF.
   7. Start `saley dev` on port `WEB_PORT` (default 8090, read from settings and `.env`).
      First a pre-check: if the port is in use, step 7 fails and the error names the program
      (and the Windows service) that holds it. Wait for the first sync. "Updated HH:MM" must be
      within SHEET_SYNC_SECONDS.
   8. Browser QA (skill `browser-qa`, read-only) on http://localhost:<WEB_PORT>: every docs/08 view,
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
   in one commented RETIRED block), docs/08 appendix (endpoint list), `docs/STATUS.md`. Record:
   ```
   uv run python scripts/verify.py --release $ARGUMENTS --record 11 PASS "docs: ..."
   ```
4. Step 12:
   ```
   uv run python scripts/verify.py --release $ARGUMENTS --finalize
   ```
   It writes `GATE: PASS` or `GATE: FAIL — step N` at the top of the report. The summary
   ends with NEW / CHANGED-default / RETIRED env variables (from `docs/STATUS.md`).
5. Only on GATE: PASS: `python .claude/hooks/notify.py --release $ARGUMENTS "<one line>"`.
