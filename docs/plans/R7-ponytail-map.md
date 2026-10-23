# R7 · Tickets and the ponytail discipline

No ECC agents in this release. Ponytail is the one review discipline. The repo's own gate stays: `/verify` (12 steps), `/tidy`, the matrix `tests/matrix/R7.yaml`.

## The loop per ticket
1. `/ponytail-help` once at the start of the day (reads the ladder into context).
2. Before any new function: the ladder. *Does this need to exist → is it in the codebase → stdlib → platform (discord.py, SQLAlchemy, psycopg, APScheduler) → installed dep → one line → the minimum plus one test.*
3. Write the test first (one per rule in the matrix row). Then the minimum code.
4. `/ponytail-review` on the ticket diff. Fix what it names. Re-run the ticket's tests.
5. py_compile, ruff, the matrix rows of the ticket.
6. Short entry in `docs/plans/R7.md` §5 "done" list. Next ticket. One test run at a time (D142). Never wait for the human (D112).

End of day: `/ponytail-debt src/saley/bot/` (list, fix only gate blockers), `/ponytail-audit` on the new tables and the press path, `/tidy`, `/verify`, ping `--release R7`.

## Tickets
| # | Ticket | Files | Ponytail "already exists" answer |
| --- | --- | --- | --- |
| T0 | `/graphify .`; write `docs/plans/R7.md` from `R7-brief.md`; fill `tests/matrix/R7.yaml` | `docs/plans/R7.md`, `tests/matrix/R7.yaml` | the R6 plan format |
| T1 | Migrations 0027 (`bot_post` columns, `bot_interaction`, `saley_card_press`, NOTIFY trigger) and 0028 (`chat_*`, `discord_event_link`, views, owner model D256) | `migrations/versions/`, `core/models.py`, `core/sql/grants.sql` | the existing doors; `saley_card_press` only maps |
| T2 | `bot/components.py` limits; `bot/cards/` renderers M1–M15, A1, A2 from the mock | `src/saley/bot/components.py`, `src/saley/bot/cards/*.py`, `tests/test_bot_cards.py` | `discord.Embed`, `discord.ui.*`; renderers are dict builders, no classes |
| T3 | Worker `cards_plan`; caps priority and the held line | `worker/jobs.py`, `bot/caps.py`, `bot/queue.py` | `caps.plan` already exists; add the sort key |
| T4 | Delivery: embeds, Components v2, thread overflow, pin, edit, message ids | `bot/deliver.py` | REST JSON through the existing egress client |
| T5 | Press and sync: `bot/press.py`, `bot/sync.py`, gateway handlers, modals, selects, ephemeral | `bot/press.py`, `bot/sync.py`, `bot/gateway.py` | `discord.ui.Modal/Select`, psycopg `conn.notifies()` |
| T6 | Deep links: `bot/links.py`; web `?card=` scroll + outline; sign-in `next` same-site | `bot/links.py`, `web/auth.py`, `web/src/` | `urllib.parse`; the `next` check already exists in auth |
| T7 | Chat memory: capture, backfill 14 d once, nightly summary + facts (LLM_MID, DATA blocks, batched), retention, `saley chat forget` | `bot/memory.py`, `worker/jobs.py`, `cli.py` | `channel.history()`; the job lock and `llm_cache` |
| T8 | Answer upgrade: intent step (LLM_SMALL, fixed schema, 10 s → grammar), chat source, thread at 12 lines, 30 fixtures | `bot/answer.py`, `bot/intent.py`, `tests/fixtures/answers_r7.yaml` | the R4 parser stays the fallback; `intel.guard` validates |
| T9 | Health M15 embed; Sources & health Discord block; `saley bot status` | `bot/health.py`, `web/notes_health.py`, `web/src/` | the health line builder |
| T10 | Round trips per card type; matrix rows live | `bot/roundtrip.py`, `tests/matrix/R7.yaml` | `roundtrip.py` cases |
| T11 | Docs, `.env.example`, README, STATUS, DECISIONS D257–D262, QUESTIONS; `/tidy`; `/verify`; ping | docs, `.env.example`, README | — |

## graphify
- `/graphify .` at T0 and before T11. In `graphify-out/GRAPH_REPORT.md` the `bot` package may have edges to `core`, `intel.guard` and the SQL doors only. An edge from `bot` to `sales`, `news` or `anthropic` is a defect.
- `graphify-out/` is a by-product: add it to `.gitignore` and the tidy patterns.
