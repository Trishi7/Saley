# Status

Releases, not tickets. Claude Code pings Discord when a release is built, tested by saley-tester, parity-checked and written up here.

| Release | Scope | State | Notes |
| --- | --- | --- | --- |
| R1 | Skeleton (P0) + sales entities + sheet sync from copy + stage rules + metrics + dashboard shell with Today numbers, Goals, Accounts, People, Blockers, Scorecard, Sources and health (jobs + parity) | done 2026-10-18 | See "R1 detail" below. Verified on the real setup: Postgres 127.0.0.1:5432 through `.env`, schema owned by `saley_admin`, sheet copy. |
| R1.1 | Dashboard fixes (A), sheet robustness (B), API hardening (C), standing release gate `/verify` (D) | done, GATE: PASS (19 Oct) | See "R1.1 detail". Release tag: none committed yet; the R1.1 diff is the working tree against commit 81e4ed3. |
| R2 | Facts, working_on, Today "my tasks" per person with Done writes and undo, Inbox, Commitments, Decisions, sheet writer with signature | open | |
| R3 | News stage 1, Signals All + Linked, Events, Suggestions with add-to-pipeline, Today "needs a look" and "Saley suggests", news health funnel | open | |
| R4 | Weekly pack, Scorecard deltas, Google sign-in for real users, @Saley answers with per-user seen | open | bot shapes need decision D27 first |

## Env changes log
(NEW / CHANGED-default / RETIRED per release)

| Release | Kind | Variables |
| --- | --- | --- |
| R1 | NEW | `ANTHROPIC_API_KEY` (D28; not used in R1, may stay empty), `DATABASE_URL_ADMIN` (empty = DATABASE_URL), `TEST_DATABASE_URL` (empty = DATABASE_URL + `_test`) |
| R1 | CHANGED-default | `LLM_SMALL` openai/gpt-4o-mini → `claude-haiku-4-5`; `LLM_MID` anthropic/claude-sonnet-4-5 → `claude-sonnet-4-5`; `EGRESS_ALLOWLIST` −openrouter.ai, +api.anthropic.com, +oauth2.googleapis.com, +discord.com |
| R1 | RETIRED | `OPENROUTER_API_KEY` |
| R1.1 | NEW | none |
| R1.1 | CHANGED-default | `WEB_PORT` 8000 → 8090 (D44; compose, Caddyfile and Dockerfile follow) |
| R1.1 | RETIRED | none |
| Tidy (pre-R2) | NEW | none |
| Tidy (pre-R2) | CHANGED-default | none |
| Tidy (pre-R2) | RETIRED | none |

## Tidy (pre-R2) detail

**Done**
- `/tidy`: `.claude/commands/tidy.md` + `scripts/tidy.py` (keep / by-product / scratch lists, cache-only deletes, moves to `.scratch/` only with `--move --yes`, py_compile + ruff + pytest after a move with automatic undo, report-only dead-code list, `--check`). 11 tests in `tests/test_tidy.py`.
- `/verify` step 1 runs `scripts/tidy.py --check` first; a stray file fails the gate with the list.
- `.claude/agents/refactor-cleaner.md` copied unchanged from everything-claude-code (used in report-only mode).
- `vulture==2.16` in the dev group. `.gitignore`: caches, build output, `.scratch/`, logs, screenshots, editor and OS files.
- `.scratch/` with a README line. CLAUDE.md work rule for temporary files.
- First run (19 Oct): scratch 0, protected 0, 16 caches deleted, `--check` clean. Report: `docs/test-reports/tidy-2026-10-19.md` (ruff F401/F841 clean; vulture: 3 unused stub parameters in tests/test_verify.py, which must match the real signatures).

**Open**
- `saley-v2.json` (a key file) is still at the repo root. It is ignored by git and tidy never touches it. Move it outside the repo by hand.
- `.scratch/README.md` is ignored too, because `.scratch/` is ignored (as the ticket asks); the folder does not travel with a clone.

## R1.1 detail

**Done**
- Gate: `.claude/commands/verify.md` (12 steps, skills and agents per step), `scripts/verify.py` (steps 1–9, `--record`, `--finalize`, per-release source manifest for the new-file coverage rule), `scripts/browser_qa.py` (Playwright on the installed Edge, localhost only, 3 widths, tiles, 5 search terms, 3 drawers, no non-GET request), `saley sheet verify` (independent recount in `saley.qa.sheet_verify`), STE lint (`saley.qa.ste_lint`). CLAUDE.md work rule added. `saley-tester` and the `dashboard-parity` skill now follow the gate.
- A (dashboard): `saley dev` (web + worker, port `WEB_PORT` = 8090, D43, D44); red top bar "Worker not running since HH:MM" / "Sheet sync failing since HH:MM"; tiles show "—" for unknown, never 0, and carry `source`; Reply % shows "—" with "No source until R2"; Money blocked tile on Blockers and a Money blocked list (total = sum of the list; a band counts its midpoint, shown "≈ USD 40k (USD 30–50k)", D42); person drawer shows the real email (`mailto:`) and https links in a new tab, else "None on file" (D39); text over 160 characters shows as bullets (max 6, "Show all"); goal cards: blockers one per line, sorted by days late, red > 14, amber 1–14, grey "Due in N days", max 4 and "+N more", short names with the full name on hover, active conversations with stage pills, "why" line under the pill; grouped search with bold match and a "0 results" hint; every visible string in `web/words.py` and `static/words.js` (ASD-STE100, lint-checked).
- B (sheet robustness): headers by name; unknown headers listed as "new columns"; a missing required header stops that tab only and keeps its last good data (D41); one snapshot `sync_id` per sheet read; APIs read one snapshot per request; stale `sync_id` → 409 (D40).
- C (API hardening): response models drop extra fields; unknown parameters → 422; `limit`/`offset` on every list; same-origin only, no CORS; security headers; abuse suite `tests/test_api_abuse.py` (69 checks) with a report per release. Endpoint list: docs/08 Appendix A.

- Review fixes (gate step 10; five reviewers, no CRITICAL):
  - The web role `saley_ro` reads only `v_*` views and the two snapshot tables (migration 0005; `deploy/sql/roles.sql` matches).
  - Broken URL cells or `Origin` headers no longer cause a 500. Same-site calls are refused. Links with user info are refused.
  - The top bar turns red ("Old data: tab … not read") when a tab is stale, for every user.
  - Tiles show "—" when their sheet tab was never read.
  - The page sends `sync_id`, so the 409 "reload" banner works (D40).
  - 500 answers keep the security headers.
  - The gate fails on skipped tests, on an unreadable sheet, on a missing metric and on code changed after steps 1–9.
  - Parity says PARTIAL when it skipped tabs. Findings of a stale tab stay open.
  - An exact tab title wins over a look-alike. `saley dev --force` does not reap a live worker.
  - Logs and the Sources page hide bearer tokens, API keys and token query values.

**Open**
- Not fixed (LOW or later releases):
  - A late-item name with a comma splits in the Today "late" tile detail.
  - Compose role passwords have defaults (server only, R4).
  - `testclient` is in the loopback set.
  - No "all rows gone" guard for tabs under 10 rows.
  - Job locks are not renewed for runs over 15 min.
  - The abuse suite does not send raw traversal paths.
- After answers to Q-R1.1-1 and Q-R1.1-2 (both resolved):
  - D39–D43 confirmed (Trishi, 19 Oct, chat). docs/01 §08, docs/08 and docs/10 updated.
  - D42: deal value = band midpoint (`parse.deal_value`), in sync, the list label and `saley sheet verify` (new check "money values = sheet midpoints"). The midpoint also feeds the goal weighted pipeline.
  - D44: `WEB_PORT` default 8090 for `saley web`, `saley dev` and the gate. Step 7 names the program that holds a busy port.
- Optional, for a human: re-run `deploy/sql/roles.sql` once as postgres, so the default privileges of the `saley` role for `saley_ro` are revoked too. Migration 0005 already fixed the grants that exist.

## R1 detail

**Done**
- Skeleton: uv project (Python 3.12, pinned deps), `saley` CLI (web, bot, worker [--once JOB], news, sync --once, metrics --once, db-upgrade, health, discord send/read --test [--as]).
- Core: settings (per-service required values), structlog with secret redaction, IST clock, rw / ro / admin engines, egress (allowlist, hard linkedin.com block, per-host token bucket, retries for idempotent calls, redirects checked hop by hop, gspread and Anthropic clients guarded), LLM wrapper (DATA blocks, schema output, cache, daily cap; no callers).
- DB: migrations 0001 (ops), 0002 (sales, D17), 0003 (views, helper functions, grants). `deploy/sql/roles.sql` (saley_admin / saley / saley_ro; ro cannot read users, contact, llm_cache).
- Sales: column map by role (docs/10) with a write guard; sheet reader (read-only scope, copy only); sync with row keys, Sr No renumber rule, alias rules (C09), owner rule (D31), touches, stage rules (D29, D31: Won/Lost > Qualified > Meeting > Parked > …; Closure Prob), goal ↔ deliverable links with the override file `docs/goal-deliverable-map.yaml` (D34), data quality, structure and mass-removal guards.
- Intel: daily metric snapshot (13 of 15 metrics; reply rate null; agent metrics wait for R2), hourly parity check (fails the job on a mismatch).
- Worker: APScheduler, registry, DB locks (15 min, DB clock, renew), job_runs, heartbeat 5 min, stale-run cleanup at start. News: idle loop.
- Bot: `@Saley ping` → `[TEST] pong` in the TEST channel only (D27).
- Web: read-only dashboard (docs/07 layout and words): Today tiles, Q4 goals, Accounts, People, Blockers, Scorecard, Sources and health, global search, page search, company and person drawers, last-updated line, stale stamp. Dev sign-in only from this machine; 401 elsewhere until R4.
- Docker: one image, compose (postgres, migrate, web, bot, worker, news, caddy; egress stub), CI with Postgres.
- Checks: ruff clean, pyright 0 errors, 232 tests on `saley_test` (91 % coverage), reviewers (python, fastapi, database, security, silent-failure; python again for D29–D35) run and findings fixed, verification loop PASS, dashboard-parity PASS (docs/test-reports/parity-2026-10-18.md), Discord loop PASS (`[TEST] pong`), real sheet copy synced (724 people; Qualified 4, Engaged 8, Connected 41, Contacted 143, Listed 528; second run: 0 changes).

**Files**: `pyproject.toml`, `uv.lock`, `.python-version`, `alembic.ini`, `migrations/` (env.py, 0001–0003), `src/saley/` (core, sales, intel, news, bot, web, worker, cli.py), `tests/` (13 files), `scripts/dashboard_parity.py`, `deploy/sql/roles.sql`, `deploy/Caddyfile`, `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.github/workflows/ci.yml`, `.env.example`, `.gitignore`, `README.md`, `docs/plans/R1.md`, `docs/DECISIONS.md` (D28–D38; D29–D35 confirmed), `docs/QUESTIONS.md` (Q-R1-1…6 resolved), `docs/15-test-plan.md`, `docs/14-runbook.md`, `docs/goal-deliverable-map.yaml`, `src/saley/sales/goalmap.py`, `docs/01` (D28 wording, §11 roles), `docs/03` (D28 wording), `docs/06` §6.1, `docs/08` (owner rule, industry rule, view 7), `docs/test-reports/`.

**Open (for the human or a later release)**
- All R1 questions (Q-R1-1 … Q-R1-6) are resolved. D36–D38 stay "proposed" (not asked yet).
- `deploy/sql/roles.sql` now also grants `saley_ro` CONNECT on `saley_test` (a test proves the role cannot write). On this laptop the grant was applied by `saley`, the owner of `saley_test`.
- Not done in R1, by design: `/api/evidence` (R2), Today tasks, Inbox, Commitments, Decisions view (R2), Signals, Events, Suggestions, news funnel (R3), weekly pack, scorecard 7-day change, Google sign-in (R4).
- Hardening left for later: egress container (D32), `SecretStr` for secrets in settings, self-hosted fonts (CSP), web rate limiting (with sign-in, R4), retention jobs for job_runs / parity_checks / llm_calls / egress_log, `job_runs` growth (~1.5k rows a day).

