---
name: dashboard-parity
description: Verify that every number and list on the Saley dashboard matches the GTM sheet copy and the news tables. Use before any release that touches saley/web, saley/sales or saley/news, and when a view looks wrong.
---

# Dashboard parity

The dashboard must never show a number the sheet or the news tables do not support.

## Procedure
1. Start postgres and the web service. Run `uv run saley sync --once` so the DB is fresh.
2. Pull the sheet copy with gspread (read-only) into pandas: Outreach PoCs, Master Pipeline, Deliverables, Goals, Packages, Events.
3. Recompute independently, in the skill's own code, not with the app's SQL views:
   - people total, contacted (First Contact true), connected (Sid - LI Addition = Connected), emailed (any email sent or LI DM sent), meetings (Meeting Date or Meeting Status set), P1 count, stage counts per docs/06 stage rules
   - per company: people, contacted, connected, emailed
   - late deliverables (Tentative Deadline < today and Status not Completed)
   - packages with Ready? = No
   - events in the next 30 days
4. Call the API: /api/today, /api/accounts, /api/people?filter=…, /api/blockers, /api/scorecard, /api/goals. Compare every number. Tolerance 0.
5. News: compare /api/health/full news funnel with SQL on news_editions, news_stories, news_dropped, news_sent, signal for today. Compare the Signals All count for "today" with `select count(*) from news_stories where stored::date = today`.
6. Spot-check 5 random people: the dashboard row equals the sheet row for name, company, title, stage inputs.
7. Check every tile link opens the right filter and the list count equals the tile number.
8. Check "Updated" times are within 2 min of job_runs.
9. Write docs/test-reports/parity-<date>.md: table of metric · sheet or SQL value · dashboard value · PASS/FAIL. End with PASS or FAIL.

## Rules
- Read-only. Never write to the sheet or the DB.
- Use `GTM_SHEET_ID` only if `GTM_SHEET_IS_COPY=true`. Else stop and report.
- A FAIL blocks the release. Fix the view or the sync, then rerun.
