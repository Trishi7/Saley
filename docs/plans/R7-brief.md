# R7 brief · The Discord bot v2 (the human, 2026-10-23)

This is the brief for chain stage 1. Claude Code's planner writes `docs/plans/R7.md` from it, in the R6 plan format (Scope, Features with matrix rows, Design, Decisions, Tickets, Env, Needs, Risks, Appendix A = this file verbatim). Where the plan and this brief differ, the brief wins. A CLAUDE.md hard rule wins over both.

## Goal
Ship the v2 bot in one day: every card type in `docs/09-bot-messages.md` §5, the press and sync behaviour of §6, deep links of §7, answers of §8, with the look of `docs/18-bot-mock.html` and the voice of `docs/12b-voice.md`. Built on the R4 bot, not next to it. Runs on the laptop (`uv run saley bot`) and on the server (the existing `bot` compose service) with the same code and the same `.env` keys.

## In
1. Card registry `src/saley/bot/cards/` — one pure renderer per type M1–M15, A1, A2; shared limits in `bot/components.py` (≤ 5 items, ≤ 4 buttons, select menu when more, closing line, colours, link text rule).
2. Worker job `cards_plan` that turns dashboard notifications into `bot_post` rows with `card_id`, `card_type`, `priority`, `payload`; `bot_plan` priority order and the "holding N" line.
3. Delivery: embeds, Components v2 (M5, M12), thread overflow, pin/unpin (M12), `discord_message_id` stored, edit in place.
4. Press path `bot/press.py` + SQL door `saley_card_press` (migration 0027) that maps every action to the existing doors; `bot_interaction` table; writers from `DISCORD_WRITE_IDS` checked in SQL.
5. Sync `bot/sync.py`: trigger + `NOTIFY saley_card` on decision insert and undo; bot LISTEN; card edited within 5 s; undo both ways.
6. Modals: M6 after-meeting (next step, date, meeting status), M8 move date, M14 answer, M5 add a note. Select menus: M3 Do it with > 1, M11 Take it, name collision.
7. Deep links `bot/links.py` and the dashboard side: `?card=<id>` scrolls to and outlines the card; sign-in keeps same-site `next`.
8. Chat memory `bot/memory.py`: capture, backfill 14 days once, nightly `chat_summary` + `chat_fact` (LLM_MID, DATA blocks, batched per channel-day), retention 90 days, `saley chat forget`.
9. Answer upgrade: intent step (LLM_SMALL, fixed JSON schema, 10 s timeout → grammar fallback), chat memory as a source with `[Name, Tue 12:40]` citations, private thread over 12 lines, personal data ephemeral.
10. Health M15 embed and the Discord block on Sources & health; `saley bot status`.
11. Live round trips: `saley discord roundtrip --test --case <card>` for every card type.
12. Docs: docs/09 (seeded), docs/12b (seeded), docs/17 (seeded), docs/14 runbook bot section, docs/11 cut-over steps, README, `.env.example`, STATUS, DECISIONS (D257–D262 below), QUESTIONS.

## Out
- Email drafts and sending (next week). The R4 action card stays for non-email proposals.
- The API Sales cut-over and stopping v1 `sales-bot` (after one week in the test channel).
- DMs on (`DM_ENABLED` stays false). Apollo "Find people" (disabled button only).
- Any new container. Any HTTP interactions endpoint.

## Decisions to record (proposed, approved in chat 23 Oct)
- D257 Writers: sheet writes from Discord only by `DISCORD_WRITE_IDS` (sid, vaishnavi), checked in SQL; replaces the writer part of D135 and the approver list for card presses. Kushal and Trishi get A2 + Ask buttons.
- D258 Email drafts and sending are out of R7; next release.
- D259 One card per dashboard notification; the card edits itself after a dashboard act (NOTIFY) within 5 s; undo both ways; reactions never act.
- D260 Caps: 4/person/day, 2 h gap, max 5 people and 5 stories per card with the rest in a thread, priority M5→M8→M7→M2→M3→M11→M4, "holding N for Monday".
- D261 Buttons: one row ≤ 4 (act, act, dismiss, link); select menu beyond; link text names the thing; deep links `PUBLIC_URL/<page>/<id>?card=<id>` with same-site `next`.
- D262 Chat memory: all messages in the bot's channels, 90 days, nightly summary and facts, personal data ephemeral; answers cite chat lines by name and time.

## Tools for this release
- **graphify** before planning: `/graphify .` then read `graphify-out/GRAPH_REPORT.md` to see which modules the bot touches (bot ↔ worker ↔ web doors). `graphify-out/` is a by-product: add it to `.gitignore` and the tidy patterns.
- **ponytail** at stage 3 and in review: `/ponytail-review` on each ticket's diff; the ladder (exists → in codebase → stdlib → platform → installed dep → one line → minimum + one test) decides before new code is written. discord.py already does components, modals, threads and pins; do not wrap it.
- No ECC agents in R7. Tickets and the ponytail loop: `docs/plans/R7-ponytail-map.md`.

## Needs from the human (nothing blocks the build)
- None for the laptop. The test channel, guild id and bot token are in `.env`.
- Server: after the gate, `git pull` + `bash deploy/deploy.sh`; the bot needs `DISCORD_WRITE_IDS` in `/srv/saley/.env`.

## Env
NEW (all with defaults): `DISCORD_WRITE_IDS=sid,vaishnavi`, `EVENT_USERS=sid,vaishnavi,kushal`, `BOT_LIST_MAX=5`, `ANSWER_THREAD_LINES=12`, `CHAT_RETENTION_DAYS=90`, `CHAT_BACKFILL_DAYS=14`, `CHAT_CHANNEL_IDS=` (empty = test + sales channels), `SUGGESTIONS_TIME=14:30`, `EVENTS_TIME=15:00`, `NEW_COMPANY_TIME=15:30`, `AFTER_MEETING_HOURS=3`, `BOT_INTENT_TIMEOUT_S=10`.
CHANGED-default: none. RETIRED: none (`DISCORD_APPROVER_IDS` and `DISCORD_INSTRUCT_IDS` stay for the R4 grammar; note D257 in the comment).
