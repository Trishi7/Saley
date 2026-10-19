---
name: dashboard-parity
description: Gate step 6 of /verify. Verify that every number and list on the Saley dashboard matches the GTM sheet copy and the news tables, with `saley sheet verify`. Use in every release gate, and when a view looks wrong.
---

# Dashboard parity (gate step 6)

The dashboard must never show a number the sheet or the news tables do not support.
This skill is step 6 of `/verify` (`.claude/commands/verify.md`). `scripts/verify.py` runs it.

## Procedure
1. Postgres up. `uv run saley sync --once` so the DB is fresh.
2. Run `uv run saley sheet verify --out docs/test-reports/<release>-sheet-verify.md`.
   It reads the sheet copy through the service account with the read-only scope, one batch call.
3. It recomputes independently, in its own code (`src/saley/qa/sheet_verify.py`), never with the
   app's SQL views or `saley.sales` code:
   - people total, contacted, connected, emailed, meetings, P1, stage counts per docs/06 §6.1
     (D29, D31), Won and Lost
   - the six Today numbers (deals closed of N, live conversations, backlog, P1 waiting,
     contacted this week, late items that block goals)
   - per company: people, contacted, connected, emailed
   - late deliverables (deadline before today, no done date, status not completed / done / closed)
   - packages with Ready? = No
   - money blocked: the total equals the sum of the money-blocked list
4. It calls the API in process (the same FastAPI app the browser uses): /api/today,
   /api/people, /api/accounts, /api/blockers, /api/money-blocked, /api/scorecard, /api/goals.
   Tolerance 0.
5. It spot-checks 5 people (fixed seed): name, company, title, stage.
6. It checks that every tile's list count equals the tile number.
7. News: R1 has no news tables; the health funnel must show "not started" for every step.
8. It writes a table: metric · sheet · API · OK/DIFF, and ends with PASS or FAIL.
   Any DIFF fails step 6.

## Rules
- Read-only. Never write to the sheet or the DB.
- Use `GTM_SHEET_ID` only if `GTM_SHEET_IS_COPY=true`. Else stop and report.
- A FAIL blocks the release. Fix the view or the sync, add a regression test, run `/verify`
  again from step 1.
