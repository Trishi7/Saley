# Saley v2

Sales lead and chief-of-staff system for Membrane's sales team. Dashboard first, Discord bot for pings and chat, worker for rules and approved writes, news module for signals. One PostgreSQL.

- Design: `docs/` (start at `docs/00-index.md`).
- Rules for Claude Code and humans: `CLAUDE.md`.
- Status: `docs/STATUS.md`. Plan for the current release: `docs/plans/`.

## What runs (release R4)
| Service | Command | Does |
| --- | --- | --- |
| dev | `uv run saley dev` | Web in this process and the worker as a child process (`saley worker`) on http://localhost:8090 (`WEB_PORT`; laptop only, D43, D46). Ctrl+C stops both |
| web | `uv run saley web` | Read-only dashboard on http://localhost:8090 (`WEB_PORT`); every `/api` call needs a sign-in (`/signin`, R4) |
| worker | `uv run saley worker` | Sheet sync every 60 s, sheet push every 2 s (quiet when empty), parity check hourly, metrics 23:50 IST, heartbeat 5 min, R2 jobs: facts, proposals, commitments scan, mail poll, reminders, R3 job: profile-link search requests (`search_requests`, every 15 s, quiet); R4 jobs: `weekly_pack` (Mon 14:00 IST) with `weekly_pack_catchup` (hourly), `bot_plan` (every 5 min), `daily_brief` (`DAILY_BRIEF_TIME`, weekdays; DMs only when on), `health_line` (23:45 IST weekdays) |
| bot | `uv run saley bot` | Answers `@Saley` questions and sends the bot queue with the caps (R4). In dev, the TEST channel only (D125) |
| news | `uv run saley news` | R3 editions at 06:30, 12:00, 17:00, 22:00 IST; OpenAlex 07:30; arXiv 10:00; cleanup 03:00. Runs only here (D70) |

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
   - A downgrade that drops the `saley_*` functions (migrations 0006, 0008, 0009) must run as the role that owns them. After `roles.sql` that role is `saley`. The migration switches to it with `SET LOCAL ROLE` when the migrating role is a member of it. Otherwise it stops with a clear message: run the downgrade as `saley`.
6. `uv run saley health` — prints `db ok`, the Alembic head, `sheet=copy|live` and `test_mode`.
7. Share the GTM Playbook COPY with the service account email (Viewer; Editor once `SHEET_WRITE_ENABLED=true`, D57). Then `uv run saley sync --once`.
8. `uv run saley web` and open http://localhost:8090/signin (`WEB_PORT`, default 8090; 8080 is often taken on laptops). On the laptop, sign in with the mock button (see R4 run notes). The "View as" select works only with the mock provider (D116).
9. Or run web and worker together: `uv run saley dev` (http://localhost:8090; it uses `WEB_PORT`, `--port` overrides it).
   It refuses to start when another worker ran in the last 2 × `SHEET_SYNC_SECONDS` (use `--force` only if that worker is gone).
   The top bar turns red when the worker stopped or the sheet sync fails.
10. Other services: `uv run saley worker` · `uv run saley bot` · `uv run saley news`.
   One job: `uv run saley worker --once sheet_sync|metrics_snapshot|parity_check|heartbeat`.

## R2 run notes
- Writes are off by default: `SHEET_WRITE_ENABLED=false`. Pending rows wait as `held`, and the top bar says "sheet writes off". Turn it on only after the service account is Editor on the COPY (D57). The writer refuses to build unless `GTM_SHEET_IS_COPY=true` (D53).
- Live round trip in the scratch tab only: `uv run saley sheet roundtrip --test-tab`. It creates `SHEET_TEST_TAB` (default SALEY_TEST), writes, reads back, undoes and deletes the tab. Exit 0 is PASS, 1 is FAIL, 3 is not configured.
- Decisions: `uv run saley decisions import` loads `docs/DECISIONS.md` into the decision table. `--file PATH` reads another file. Safe to run again. Superseded rows are skipped, and a decision confirmed on the dashboard stays confirmed.
- `uv run saley dev` starts the web and a worker child process (D46). The web never holds the write credential. Ctrl+C stops both. On Windows the whole process tree stops.
- Meeting notes: put `.md`, `.txt` or `.pdf` files in `MEETING_NOTES_DIR` (default `data/notes`). PDF reading uses pypdf. Notes by mail wait for the cc mailbox (Q-R2-3). Until then, Sources and health shows the mail source as not configured.
- Dev identity: `DEV_USER_SHORT` (default `trishi`) is who the dashboard acts as. The "View as" select sends `X-Saley-As` from this machine only (D50).
- The gate's `saley dev` runs with `SHEET_WRITE_ENABLED=false`, so browser QA never writes to the sheet.

## R3 run notes
- News sources: `uv run saley news seed` loads `src/saley/news/registry.yaml` into `news_sources`. Safe to run again.
- News service: `uv run saley news` runs the editions on schedule. Only this process fetches news (D70). `saley dev` and the gate never fetch.
- One bounded live fetch: `uv run saley news fetch --live --once --no-score`. It makes one request per source kind, with `--limit` items each (1–50, default 3), and scores nothing. Exit 0 is PASS. Without all three flags it refuses to run (R3-N03).
- Scorer: `SCORER_BACKEND=llm` by default. With no key, or when the cap is reached, the `rules` scorer runs and the row says so. One live LLM call per release: `uv run saley llm smoke --live` (exit 0 proven, 1 failed, 3 not configured, Q-R3-1).
- Profile-link search: on a Suggestions card, "Find people" is a human click (D77). The worker runs it with `search_requests`. SearXNG runs first when `SEARXNG_URL` is set, else DuckDuckGo html. Limits: `SEARCH_MIN_GAP_SECONDS` (5), `SEARCH_DAILY_CAP` (50 a day), `SEARCH_CACHE_DAYS` (30). Only the result URL and title are stored. Nothing is fetched.
- Add to pipeline: the Add form on a Suggestion writes one `row_add` (D76). Undo inside `UNDO_MINUTES` removes the row only if it is unchanged (D91). Test the scratch tab only: `uv run saley sheet roundtrip --test-tab --row-add`. It adds a signed row, undoes it and deletes the tab.
- Meeting notes: `uv run saley notes sync --live --once` reads the Drive folder once. Exit 0 OK, 1 failure, 3 not configured (Q-R2-6). "Next steps" lines of the form `[Name] action` become team commitments. Names come from `TEAM_NAME_ALIASES` (D92, D108).
- Apollo stays off in R3 (D78). Health shows "off".

## R4 run notes
- Database: the dev database must be at the Alembic head. Run `uv run alembic upgrade head` (or `uv run saley db-upgrade`). Migrations 0017 to 0022 are R4. Migration 0016 is R3.
- Sign-in (D114 to D116): `AUTH_PROVIDER` is `auto` (default), `google` or `mock`.
  - `auto` uses Google when `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` and `WEB_ALLOWED_EMAILS` are all set. Otherwise it uses mock.
  - `mock` signs in as `DEV_USER_EMAIL`. It works only with `SALEY_ENV=dev`, from this machine. Anywhere else it answers 404.
  - Google needs the two OAuth values, the redirect URL `GOOGLE_OAUTH_REDIRECT_URL` (default `http://localhost:8090/auth/callback`) registered in Google, and `WEB_ALLOWED_EMAILS` as `short=email,...`. The Google values are open (Q-N5). Until they are set, the laptop uses mock.
  - `SESSION_HOURS` (default 12) sets how long a sign-in lasts.
- Weekly pack (D117, D130, D134): the worker builds the pack on Monday at 14:00 IST and posts it through the bot queue. The blocker note follows `BLOCKER_NOTE_DELAY_MINUTES` later (default 120). The page is `/#pack`. A pack that was due while the worker was down is built later by `weekly_pack_catchup`.
- DMs (D126, D128, D132, D135): the daily brief is a DM at `DAILY_BRIEF_TIME` (default 14:00 IST, Monday to Friday).
  - `DM_ENABLED` is the global switch. It is `false` by default. While it is off, no DM is sent and the health line says "DMs off".
  - `DM_USERS` lists who gets a DM (default `sid,vaishnavi,kushal,trishi`). Empty means nobody.
  - Caps: 4 posts per person per day (`BOT_POSTS_PER_PERSON_PER_DAY`), DMs and @mentions together, and a 2 h gap (`BOT_POST_GAP_MINUTES`).
  - `USER_HOURS` sets each person's delivery window (default `sid:14:00-24:00`). `DISCORD_USER_MAP` fills `users.discord_id` when the bot starts. The ids are never logged.
- Mail sink (D124): `EMAIL_SEND_ENABLED=false` by default. Drafts are written as `.eml` files to `EMAIL_SINK_PATH` (default `data/outbox`). No SMTP call is made. A human sends the mail. The SMTP settings count only when the flag is true.
- Test-channel round trip: `uv run saley discord roundtrip --test --case pack` or `--case answer`. It builds the text with the bot's own code, in a transaction that is rolled back. It posts once to the TEST channel, reads the post back by id and deletes it. Exit 0 is PASS, 1 is FAIL.
- Gate: `/verify R4` runs its own web on `GATE_PORT` (default 8091) against `saley_test`, with worker slot `worker:gate` (D113). Your `saley dev` on `WEB_PORT` may keep running.
- Laptop memory (D142, D112): one background agent or test run at a time. Never run the full suite while an agent is still working. Run tests one file at a time: `uv run pytest -q -x -p no:cacheprovider tests/<file>.py`. Browser QA runs one width at a time: `uv run python scripts/verify.py --low-memory`. It also switches by itself when free RAM is below `LOW_RAM_MB` (3072 MB).

## R5 run notes (dashboard v2)
The new dashboard lives in `web/` (Vite, React, TypeScript, Tailwind, vendored shadcn/ui and bklit). Node runs at build time only; the server needs no Node (D144).
1. Install Node 24 LTS (`web/.nvmrc`). Then install the exact lock, with install scripts off:
   ```
   npm --prefix web ci --ignore-scripts
   ```
   No package needs its install script (esbuild, rollup, Tailwind oxide and lightningcss ship platform packages, D191).
2. Build: `npm --prefix web run build`. The output goes to `src/saley/web/dist/` (gitignored) with `build-stamp.json` (D145).
3. Preview: start `uv run saley dev`, sign in, and open http://localhost:8090/next. `/` stays the legacy page until the cut-over (D147).
   Without a build, `/next` answers "The new dashboard is not built yet. Use the main page.".
4. Dev loop: run `npm --prefix web run build -- --watch` in a second terminal beside `uv run saley dev`, and reload the page after a save. There is no Vite dev server, so the CSP and the Host-port check stay as in production. Stop the watch before you run pytest (D142).
5. Checks: `npm --prefix web run lint` (ESLint; all visible text comes from `web/src/words.ts`) and `npm --prefix web run typecheck`. Gate step 1 runs both and fails when the build stamp is older than `web/` ("Run npm --prefix web run build").
6. Docker builds `web/` in a Node stage (`node:24-bookworm-slim`, pinned by digest); the run image only copies `dist/`.

## Tests
- `uv run ruff check .` and `uv run pytest -q`. On the laptop, run one file at a time: `uv run pytest -q -x -p no:cacheprovider tests/<file>.py` (D142).
- Tests use a separate PostgreSQL database: `TEST_DATABASE_URL`, else `DATABASE_URL` with `_test` added to the database name. `deploy/sql/roles.sql` creates `saley_test` (owner `saley`). Tests drop and rebuild its schema. They never read `.env`.
- Coverage: `uv run pytest -q --cov=saley`.

## Release gate (`/verify`)
Every release ends with `/verify <release>` (`.claude/commands/verify.md`). GATE: PASS is required before the release ping.
- Steps 1–9: `uv run python scripts/verify.py --release R1.1`. Steps 7–9 run the gate's own server on `GATE_PORT` (default 8091) against `saley_test`, worker slot `worker:gate` (D113). Your own `saley dev` on `WEB_PORT` may keep running.
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
`docker compose up -d` runs postgres, web, bot, worker, news and caddy from one image. Set `SALEY_DOMAIN`, `SALEY_*_PASSWORD` and `GOOGLE_SERVICE_ACCOUNT_HOST_FILE` in the server environment. The web refuses every `/api` call without a session, in every mode (D116).

## Claude Code setup (once per laptop)
1. `npx @vectorize-io/hindsight-coding-agents install claude-code --server daemon` — project memory across sessions.
2. Set `SALEY_DEV_WEBHOOK` in your shell profile for Discord pings from `.claude/hooks/notify.py`.
3. In a session: `/remote-control` to answer from your phone.
4. Agents, skills, commands and rules in `.claude/` come from affaan-m/ECC (14 picked) plus `saley-tester`.
