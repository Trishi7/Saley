# R1 tester report 2026-10-18

Env: private Postgres 17 on 127.0.0.1:5433, sheet=copy, test_mode=true. docker CLI not on PATH (compose ps not run; Postgres reached directly).

| Test | Command | Expected | Got | Result |
|---|---|---|---|---|
| Lint | uv run ruff check . | clean | All checks passed | PASS |
| Unit tests | uv run pytest -q (TEST_DATABASE_URL set) | green | 203 passed | PASS |
| Migrations | alembic downgrade base; upgrade head | no error, at head | 0001-0003 applied; health "at head" | PASS |
| Health | saley health | db ok, sheet=copy | db ok, 0003 at head, sheet=copy test_mode=true | PASS |
| RO role | insert as saley_ro | refused | ReadOnlySqlTransaction | PASS |
| Sync twice | saley sync --once x2 | 2nd: 0 new/0 changed/0 stage moves | 1st 1160 new, 464 touches, 724 stage moves; 2nd all 0 | PASS |
| Stage rules + env + egress + web tests | pytest tests/test_sales.py test_egress.py test_web.py test_env_example.py | pass | 140 passed | PASS |
| Metrics | saley metrics --once x2 | rows; rerun replaces | 119 rows both runs, table total 119 | PASS |
| Parity | worker --once parity_check | all rows ok | 14 checks all match; /api/health/full lists 14 ok | PASS |
| API Today | GET /api/today | 6 tiles, last_computed | 6 tiles, last_computed present | PASS |
| Tile vs list | tiles live/backlog/p1 vs /api/people?filter= | same counts | 11/41/39 equal list totals; reconnect_due 4 | PASS |
| Late tile vs Blockers | tile late=8; /api/blockers late=13 | consistent | tile counts only goal-blocking (8 of 13 have blocks_goals); matches label | PASS |
| Goals/Accounts/People/Blockers/Scorecard/Health/Search/drawers | curl /api/goals, accounts(+id, filter=p1), people(+id), blockers, scorecard, health/full, search?q=a | 200 with evidence ids, last_computed | all 200; evidence.row present; last_computed present | PASS |
| No write endpoint | curl -X POST/PUT/DELETE/PATCH /api/today, /api/act | 404/405 | 405 and 404 | PASS |
| Non-loopback | curl -H "X-Forwarded-For: 8.8.8.8" /api/me | 401 | 401 | PASS |
| Prod mode | SALEY_ENV=prod saley web; /api/me, /api/today | 401 | 401, 401 (healthz 200) | PASS |
| Egress linkedin | egress.request(client,"GET","https://www.linkedin.com/in/x") | EgressBlocked | EgressBlocked (also sub.linkedin.com) | PASS |
| Discord P0/R1 loop | saley discord send --test "P0 ok"; saley bot | pong in TEST channel | Refused: "DISCORD_TEST_CHANNEL_ID equals the live sales channel"; bot also refuses to start | FAIL (env, safety guard works) |
| Per-user seen, caps, LLM, rules, --as | n/a in R1 | n/a | N/A | N/A |
| Dashboard parity skill | existing docs/test-reports/parity-2026-10-18.md | PASS | not re-run by tester | N/A |

Fix for the FAIL: set DISCORD_TEST_CHANNEL_ID in .env to the real TEST channel id (human action; tester does not read .env). Then rerun: `uv run saley bot` and `uv run saley discord send --test "@Saley ping"` / `read --test --last 3`.

Services started: web (stopped), bot (refused at start). All stopped.

## Result: FAIL (1 failure: Discord ping not testable, env misconfig)

## Addendum (main session, 2026-10-18 18:23 IST): re-run of the failed Discord test

- Cause of the FAIL: in `.env`, `DISCORD_SALES_CHANNEL_ID` equals `DISCORD_TEST_CHANNEL_ID` (checked without printing either value). docs/11 says the sales channel id stays empty while v1 runs, so the guard refused on purpose. Question Q-R1-4.
- Re-run with `DISCORD_SALES_CHANNEL_ID=""` set in the shell only (`.env` not changed):

| Test | Command | Expected | Got | Result |
| --- | --- | --- | --- | --- |
| bot login | `uv run saley bot` | logs guild and test channel | guild=NFThing test_channel=sleyv2 test_mode=True | PASS |
| ping | `saley discord send --test "@Saley ping"` + `read --test --last 3` | `[TEST] pong` in the test channel | `[TEST] pong` | PASS |
| send/read | `saley discord send --test "R1 ok"` + read | message read back | `[TEST] R1 ok` | PASS |

Result with the documented setting: **PASS**. The FAIL above stays on record: it holds until `.env` is fixed.


## Re-run 2 (real setup)

Real Postgres via .env, no overrides. WEB_PORT=8765, PYTHONIOENCODING=utf-8.

| Test | Command | Expected | Got | Result |
|---|---|---|---|---|
| Lint | `uv run ruff check .` | clean | All checks passed | PASS |
| Tests | `uv run pytest -q` | green, no TEST_DATABASE_URL | 232 passed | PASS |
| Health | `uv run saley health` | sheet=copy, at head | db ok, head 0003 = current, sheet=copy test_mode=true | PASS |
| Sync x2 | `uv run saley sync --once` | run 2: 0 new/0 changed/0 stage moves | both runs: 1050 rows, 0 new, 0 changed, 0 stage moves | PASS |
| Parity | `uv run saley worker --once parity_check` | all match | 13 checks, all match | PASS |
| Metrics | `uv run saley metrics --once` | snapshot ok | 119 metric rows (2 skipped by design) | PASS |
| Today | GET /api/today | 6 tiles | 6 tiles; live 12, backlog 41, p1 39, week 50 equal list totals; late 8 = 8 of 13 late blockers that block goals; deals "0 of 3" links to goals | PASS |
| Goals/Accounts/Scorecard/Blockers | GET /api/goals, accounts, scorecard, blockers | 200, last_computed present | all 200 with last_computed; accounts filters all/p1/has_connection/goal:G1/industry:X ok | PASS |
| People filters | GET /api/people?filter=all,backlog,p1_waiting,in_conversation,reconnect_due,stage:Qualified | 200, totals | 724 (page 200), 41, 39, 12, 4, 4 | PASS |
| Gamez | GET /api/people?filter=all&q=gamez | David Gamez Qualified | Dr. David Gamez, Qualified | PASS |
| Search | GET /api/search?q=gamez | person hit | 1 person, Qualified | PASS |
| Drawers | GET /api/accounts/368, /api/people/126 | 200 with detail | 200 both | PASS |
| Health full | GET /api/health/full | jobs, parity, egress, news | all keys present | PASS |
| Read-only | POST/PUT/DELETE /api/today; POST /api/act | 404/405 | 405 x3, 404 | PASS |
| Non-loopback | X-Forwarded-For 8.8.8.8 | 401 | 401 | PASS |
| Prod env | `SALEY_ENV=prod` web | 401 | 401 | PASS |
| LinkedIn egress | `egress.request(make_client(), GET, linkedin.com)` | EgressBlocked | EgressBlocked (blocklist) | PASS |
| Bot ping | `discord send --test "@Saley ping"`, `read --test --last 3` | "[TEST] pong" | "[TEST] pong" | PASS |
| --as refused | `discord send --test --as 123 x` | refused | "Refused: --as must be one of DISCORD_TEST_USER_IDS" | PASS |

Notes: `/api/evidence` and `accounts?filter=search` are not in R1 (404 / 422); not counted as failures. Per-user seen, caps, LLM, rules: N/A in R1. All started services stopped.

Result: PASS
