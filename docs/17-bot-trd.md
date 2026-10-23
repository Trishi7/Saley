# 17 · Bot TRD (R7)

> Status: AGREED (23 Oct 2026). PRD: `docs/09-bot-messages.md`. Voice: `docs/12b-voice.md`. Mock: `docs/18-bot-mock.html`. Plan: `docs/plans/R7.md`. This document says how the v2 bot is built on the R4 bot that is already in `src/saley/bot/`.

## 1. What exists (R4–R6) and what R7 changes
| Module | Today | R7 |
| --- | --- | --- |
| `bot/gateway.py` | discord.py client; ping, questions, card button clicks | + component handlers for every card type, modals, select menus, threads, ephemeral, LISTEN loop for card sync |
| `bot/deliver.py` | REST send of `ready` bot_post rows with `[TEST]` tag and mention rules | + embeds and Components v2 payloads, thread overflow, pin/unpin, message edit, `discord_message_id` stored |
| `bot/card.py` | Action card (Do it / Edit / Skip) → `saley_proposal_resolve` | becomes one of many cards; the generic press path moves to `bot/press.py` |
| `bot/caps.py`, `bot/queue.py` | Caps as a pure function; `bot_plan` job | + priority order M5→M8→M7→M2→M3→M11→M4, held count, "holding N for Monday" |
| `bot/answer.py` | Fixed parser, five lookups, citations, seen footer | + intent step (LLM_SMALL, fixed schema) in front of the grammar, chat memory as a source, thread for long answers |
| `bot/instruct.py` | Grammar for remind/note/approve/reject | unchanged; writer check uses `DISCORD_WRITE_IDS` |
| `bot/brief.py`, `intel/pack.py` | Daily brief text, weekly pack | re-rendered as M1 embed and M12 Components v2 |
| `bot/health.py` | Health line | M15 embed, + cards sent/held |
| `core/models.py` `BotPost` | queue row | + columns (§4) |

New modules: `bot/cards/` (one renderer per card type, pure functions row → payload), `bot/press.py` (press → door → edit), `bot/sync.py` (LISTEN `saley_card` → edit), `bot/memory.py` (chat_message capture, backfill, summary job), `bot/links.py` (deep links), `bot/components.py` (embed and Components v2 builders, limits).

## 2. Architecture
```
 dashboard (web) ──┐                         ┌── Discord gateway (websocket)
                   │  SQL doors              │
 worker ───────────┼─► PostgreSQL ◄──────────┼── bot container (discord.py)
  plans cards      │  bot_post, decision,    │   delivers, hears presses,
  builds text/LLM  │  bot_interaction,       │   calls doors, edits cards,
  NOTIFY saley_card│  chat_message/summary   │   LISTEN saley_card
                   └─────────────────────────┘
```
- The **worker** owns every decision: when a card is due, what it says, which people it mentions. It writes a `bot_post` row with the rendered payload (JSON) and a `rendered_hash`.
- The **bot** owns delivery and presses. It never builds text from the LLM and never reads the sheet. It holds `DISCORD_BOT_TOKEN`, the `saley` role `DATABASE_URL`, and `PUBLIC_URL`. No LLM key, no Google key (compose `environment:` for the bot service lists only these).
- The **doors** are the SECURITY DEFINER functions that already exist (`saley_act`, `saley_undo`, `saley_dismiss`, `saley_mark_seen`, `saley_proposal_resolve`, `saley_commitment_done`, `saley_li_requested`, `saley_ask_log`). R7 adds `saley_card_press(card_id, user_id, action, payload jsonb, surface)` that maps a press to the right door and returns `ok | already | refused | error` with who and when. The dashboard keeps calling the doors directly.
- **Sync**: a trigger on `decision` insert and on undo runs `pg_notify('saley_card', card_id)`. The bot's LISTEN loop edits the card. Reconnect of the listener re-reads cards changed in the last 10 minutes.

## 3. Processes and where they run
Laptop: `uv run saley bot` next to `saley dev` (web + worker). Server: the `bot` compose service, already present (`command: ["saley","bot"]`, heartbeat health check). No new container. The worker jobs run in the existing `worker` service. Same `.env` keys on both, different values (`PUBLIC_URL`, channel ids, `TEST_MODE`).

## 4. Data model (migration 0027, 0028)
`bot_post` + columns: `card_type text` (M1..M15, A1, A2), `priority int`, `held_reason text`, `discord_message_id bigint`, `thread_id bigint`, `card_id text unique`, `payload jsonb`, `rendered_hash text`, `pinned bool`, `edited_at timestamptz`.

New tables:
- `bot_interaction(id, card_id, user_id, action, surface, result, detail jsonb, latency_ms, at)` — every press, modal and select.
- `chat_message(id, channel_id, message_id unique, thread_id, author_short, author_discord_id, text, reply_to, at)` — captured gateway messages; `text` only, no attachments.
- `chat_summary(channel_id, day, summary text, facts int, model, at)`; `chat_fact(id, channel_id, message_id, company_id, contact_id, text, author_short, at)`.
- `discord_event_link(event_id, discord_event_id, at)`.
Retention job deletes `chat_message` older than `CHAT_RETENTION_DAYS`. Owner model per D256; saley_ro gets views `v_bot_card`, `v_chat_recent`.

## 5. Flows
**Ping.** worker job (`cards_plan`, every 5 min) reads notifications → for each due item builds the card payload with `bot/cards/<type>.py` → inserts `bot_post(queued)` with `card_id` → `bot_plan` applies caps and priority → bot `deliver` sends (embed or Components v2), stores `discord_message_id`, posts overflow to a thread, pins M12 → state `sent`.

**Press.** gateway interaction → `press.handle` → writer check (`DISCORD_WRITE_IDS`) for write actions → `saley_card_press` → result → `bot_interaction` row → edit message (footer, buttons) → ephemeral reply only on `already`, `refused`, `error`.

**Dashboard acts first.** web calls door → trigger `NOTIFY saley_card` → `sync.loop` → edit card footer "on the dashboard" → undo on the dashboard → NOTIFY again → buttons back.

**Answer.** mention → `memory.capture` (every message is captured anyway) → worker `answer` call through the existing `ask` path (bot posts a `bot_exchange` row and waits ≤ 10 s on NOTIFY `saley_answer`, so the LLM key stays in the worker) → intent step (LLM_SMALL, schema `{kind, company, person, window, topic}`; timeout 10 s → grammar fallback) → R4 grammar → SQL on views + `chat_fact` → `intel.guard` validator → ≤ 12 lines in channel, else private thread → `saley_ask_log`, `saley_mark_seen`.

**Backfill.** first start with `CHAT_BACKFILL_DAYS` = 14: reads history of each configured channel once (`chat_backfill_done` key in `job_runs`), 100 messages per page, sleeps on 429.

## 6. Error table
| Case | What happens | Who sees what |
| --- | --- | --- |
| Gateway drops | discord.py reconnects; `ready` rows wait; LISTEN reconnects and re-reads 10 min of changes | nothing |
| 429 | honour `retry_after`; row stays `ready`; 3 sends fail → `failed` with the idempotency key kept | health line counts it |
| Message deleted by a human | edit returns 404 → row `gone`, no repost | nothing |
| Press on a done item | `already` → ephemeral "Already done by X at HH:MM", card edited | presser |
| Non-writer presses a write button | `refused` → ephemeral A2 with Ask buttons; `bot_interaction` logged | presser |
| Modal field invalid (date) | ephemeral "I need a date like 28 Oct"; nothing written | presser |
| LLM intent timeout 10 s | grammar fallback; answer still from rows | asker, same answer shape |
| Validator rejects an answer line | line removed; if nothing is left → A2 "I do not have that" | asker |
| Sheet push later fails | card footer gets "sheet update pending"; worker retries; health line shows it | channel |
| Postgres down | gateway stays up; presses get ephemeral "I cannot reach our data right now, I will tell you when it is back"; cards wait | presser |
| Cap reached | cards `deferred`; last card says "holding N" | owner |
| Backfill forbidden in a channel | logged once, skipped | health line |
| DM blocked by the user | fall back to an ephemeral in the channel on their next mention; `dm_blocked` on users | nothing public |
| Thread archived | unarchive, then post | nothing |
| Meeting moved | M5 re-rendered and the old card edited to "moved to <date>"; new card 24 h before the new date | channel |

## 7. Security
- Writers: `DISCORD_WRITE_IDS` checked in the bot and again inside `saley_card_press` (`users.role`); the SQL check is the one that counts.
- Interactions are verified by discord.py (gateway); no HTTP interaction endpoint is opened.
- No personal data (phone, personal email) in a channel message; ephemeral only.
- Egress allowlist unchanged: `discord.com`, `cdn.discordapp.com` (thumbnails are URLs, never downloaded).
- Deep links carry ids only; the dashboard checks the session and the role before it shows the card.
- Chat memory is team data; the retention job runs nightly; `saley chat forget <message_id>` removes one line on request.

## 8. Edge cases
- One story for 3 people → one card that mentions all three (caps count one each).
- Due at 23:55, hours end 24:00 → send; due 00:10 → wait for the window.
- Two people ask the same question → two answers, each with its own seen marks.
- Name collision ("Priya" twice) → select menu with company, no guess.
- Undo after the window → ephemeral "the undo window closed at HH:MM; I can make a new change if you tell me".
- Sid presses on Vaishnavi's card → allowed (both writers); footer names Sid.
- A card for an account that left the sheet → card edited to "no longer on the sheet", buttons removed.

## 9. Test layers (matrix `tests/matrix/R7.yaml`)
| Layer | Tool | What |
| --- | --- | --- |
| unit | pytest | each `bot/cards/*` renderer: fields from rows only, ≤ 5 items, ≤ 4 buttons, link text never "Open", closing line present, STE banned words absent, colours by type |
| unit | pytest | caps priority order, held count text, weekend rule, 00:10 window |
| sql | pytest | `saley_card_press` maps every action to its door; Discord and dashboard produce one `decision`; undo both ways; non-writer refused in SQL |
| integration | pytest + fake Discord (httpx MockTransport + fake interaction objects) | deliver → edit → sync; thread overflow; pin/unpin; modal validation; backfill paging; summary job |
| live | `saley discord roundtrip --test --case <card>` | each card type to the test channel: send, read back, press through a test interaction when possible, edit within 5 s, delete |
| answers | pytest, 30 fixtures in `tests/fixtures/answers_r7.yaml` | intent step schema, grammar fallback, citations by name, thread rule at 12 lines, guard rejects an invented value |
| guardrails | pytest | bot service env has no LLM or Google key; `.env.example` rules; banned words; no row ids in text |
| browser | browser-qa, one width | deep link with sign-in `next` lands on the card and outlines it |

## 10. Observability
Sources & health page gets a "Discord" block: gateway up since, cards sent/held/failed today, presses ok/already/refused, answer latency p50, LISTEN last event, chat memory rows and last summary. `saley bot status` prints the same.

## 11. Rollout
1. R7 gate PASS on the laptop with `TEST_MODE=true` → commit → `deploy.sh` on the server (bot service restarts; `TEST_MODE=true` stays).
2. One week in `saley-test-v2`. The team presses real buttons.
3. Then: `DISCORD_SALES_CHANNEL_ID` set, `TEST_MODE=false`, stop the v1 `sales-bot` in PM2 (docs/11). DMs stay off until Trishi says on.
