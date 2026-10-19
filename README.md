# Saley v2

Sales lead and chief-of-staff system for Membrane's sales team. Dashboard first, Discord bot for pings and chat, worker for rules and approved writes, news module for signals. One PostgreSQL.

- Design: `docs/` (start at `docs/00-index.md`).
- Rules for Claude Code and humans: `CLAUDE.md`.
- Status: `docs/STATUS.md`. Plan for the current release: `docs/plans/`.

## What runs (release R1.1)
| Service | Command | Does |
| --- | --- | --- |
| dev | `uv run saley dev` | Web and worker in one process on http://localhost:8090 (`WEB_PORT`; laptop only, D43) |
| web | `uv run saley web` | Read-only dashboard on http://localhost:8090 (`WEB_PORT`; dev sign-in) |
| worker | `uv run saley worker` | Sheet sync every 60 s, parity check hourly, metrics 23:50 IST, heartbeat 5 min |
| bot | `uv run saley bot` | Answers `@Saley ping` in the TEST channel only |
| news | `uv run saley news` | Idle in R1 (logs "news idle" every 10 min) |

## Run on a laptop
1. Install Python 3.12, `uv`, and Docker (or use an existing PostgreSQL 16+).
2. Copy `.env.example` to `.env`. Fill the `<REQUIRED>` values. Keep the key file outside the repo.
3. Start PostgreSQL: `docker compose up -d postgres`.
   If a local PostgreSQL service already uses port 5432, stop it first or point `DATABASE_URL*` at it.
4. Create the roles and the databases once, as the Postgres superuser, with plain psql:
   ```
   psql -h 127.0.0.1 -U postgres -f deploy/sql/roles.sql
   ```
   - It creates the roles `saley_admin`, `saley` (read-write) and `saley_ro` (read-only) and the databases `saley` and `saley_test`.
   - Default passwords match `.env.example`. To use others, edit them in the file before the first run, or later run `ALTER ROLE saley PASSWORD '…';` and put the same value in `.env`.
   - `CREATE DATABASE` cannot run inside a transaction. The script sends each `CREATE DATABASE` as its own statement (psql autocommit), so do not wrap the file in `BEGIN … COMMIT` and do not run it with `psql -1`.
   - Safe to run again: roles that exist keep their passwords.
   Then set in `.env`: `DATABASE_URL` (saley), `DATABASE_URL_RO` (saley_ro), and optionally `DATABASE_URL_ADMIN` (saley_admin) so migrations do not run as the read-write role.
5. `uv sync`, then `uv run saley db-upgrade` (same as `uv run alembic upgrade head`).
6. `uv run saley health` — prints `db ok`, the Alembic head, `sheet=copy|live` and `test_mode`.
7. Share the GTM Playbook COPY with the service account email (Viewer). Then `uv run saley sync --once`.
8. `uv run saley web` and open http://localhost:8090 (`WEB_PORT`, default 8090; 8080 is often taken on laptops). In dev (`SALEY_ENV=dev`) you are signed in as `DEV_USER_EMAIL`; pick Vaishnavi, Sid or Kushal in the top bar.
9. Or run web and worker together: `uv run saley dev` (http://localhost:8090; it uses `WEB_PORT`, `--port` overrides it).
   It refuses to start when another worker ran in the last 2 × `SHEET_SYNC_SECONDS` (use `--force` only if that worker is gone).
   The top bar turns red when the worker stopped or the sheet sync fails.
10. Other services: `uv run saley worker` · `uv run saley bot` · `uv run saley news`.
   One job: `uv run saley worker --once sheet_sync|metrics_snapshot|parity_check|heartbeat`.

## Tests
- `uv run ruff check .` and `uv run pytest -q`.
- Tests use a separate PostgreSQL database: `TEST_DATABASE_URL`, else `DATABASE_URL` with `_test` added to the database name. `deploy/sql/roles.sql` creates `saley_test` (owner `saley`). Tests drop and rebuild its schema. They never read `.env`.
- Coverage: `uv run pytest -q --cov=saley`.

## Release gate (`/verify`)
Every release ends with `/verify <release>` (`.claude/commands/verify.md`). GATE: PASS is required before the release ping.
- Steps 1–9: `uv run python scripts/verify.py --release R1.1`. Steps 7–9 use `WEB_PORT` (default 8090).
  If the port is in use, step 7 fails and names the program that holds it.
  Static checks, tests with coverage, STE lint (`uv run python -m saley.qa.ste_lint`), sheet robustness,
  API abuse suite, `uv run saley sheet verify` (sheet copy vs API), `saley dev`, browser QA and click-path check.
- Steps 10–11 (reviewers, docs) are recorded with `--record 10 PASS "…"`; step 12 is `--finalize`.
- Browser QA uses Playwright with the installed Microsoft Edge (`channel="msedge"`). No browser download is needed.
  It opens only localhost. Screenshots go to `docs/test-reports/<release>-screens/`.
- Reports: `docs/test-reports/<release>-<date>.md`, `<release>-api-abuse.md`, `<release>-sheet-verify.md`.

## Repo hygiene (`/tidy`)
`.claude/commands/tidy.md` + `scripts/tidy.py`. Safe to run again.
- `uv run python scripts/tidy.py`: lists untracked files as keep / by-product / scratch, deletes caches only
  (`__pycache__`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.hypothesis`, `htmlcov`, `.coverage*`),
  and writes a dead-code report (ruff F401/F841, vulture) to `docs/test-reports/tidy-<date>.md`. It removes no code.
- `uv run python scripts/tidy.py --move --yes`: only after a human "yes". Moves the scratch files to `.scratch/`, then runs
  py_compile, ruff and pytest; a failure moves them back ("tidy: reverted").
- `uv run python scripts/tidy.py --check`: changes nothing; fails on any untracked file outside the keep patterns. `/verify` step 1 runs it.
- Never moved or deleted: tracked files, files imported by code or named in `pyproject.toml`, `alembic.ini`, `.claude/`, `docs/`, and key files.
- Temporary files go in `.scratch/` (ignored by git).

## Discord test loop
Only the TEST channel, only with `TEST_MODE=true`:
```
uv run saley discord send --test "@Saley ping"
uv run saley discord read --test --last 5
```
`--as <id>` tags a message with a test user id from `DISCORD_TEST_USER_IDS`. The bot needs the Message Content intent in the Discord developer portal.

## Server (later)
`docker compose up -d` runs postgres, web, bot, worker, news and caddy from one image. Set `SALEY_DOMAIN`, `SALEY_*_PASSWORD` and `GOOGLE_SERVICE_ACCOUNT_HOST_FILE` in the server environment. Until Google sign-in (R4) the web refuses every `/api` call outside dev mode.

## Claude Code setup (once per laptop)
1. `npx @vectorize-io/hindsight-coding-agents install claude-code --server daemon` — project memory across sessions.
2. Set `SALEY_DEV_WEBHOOK` in your shell profile for Discord pings from `.claude/hooks/notify.py`.
3. In a session: `/remote-control` to answer from your phone.
4. Agents, skills, commands and rules in `.claude/` come from affaan-m/ECC (14 picked) plus `saley-tester`.
