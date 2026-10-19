# Questions for the human

(newest first)

## Q-R1.1-2 · 2026-10-19 · Port 8080 is taken on the laptop (resolved)
- The ticket says: gate step 8 runs on http://localhost:8080. On this laptop, port 8080 belongs to the
  Windows service "NI Application Web Server" (National Instruments). `saley dev` cannot start there.
- Claude Code did not stop the service. A trial gate run on port 8081 passed steps 1–9, also after the review fixes.
- Choose one:
  1. You stop the NI service while the gate runs (or set it to Manual). The gate stays on 8080.
  2. Saley uses another port on laptops, for example 8090: `DEV_PORT`, `.claude/commands/verify.md`,
     README and the ticket text change. No env variable changes.
- Until then, `scripts/verify.py --port 8080` fails step 7 with "port 8080 is in use by another program".
- **Answer (Trishi, 19 Oct, chat):** option 2. Use the existing `WEB_PORT`, default 8090; no new DEV_PORT. Update verify.md, README, scripts/verify.py and the gate step text to read the port from WEB_PORT. Add a pre-check in step 7: if the port is in use, name the process in the error. `WEB_PORT=8090` is set in `.env`.
- Done: D44. `WEB_PORT` default 8000 → 8090 (settings, `.env.example`, compose, Caddyfile, Dockerfile); `saley dev` and `scripts/verify.py` read it; step 7 names the program that holds a busy port.

## Q-R1.1-1 · 2026-10-19 · R1.1 decisions and doc impact (  resolved)
1. D42 (proposed): Money blocked as a tile on the Blockers page (Today keeps 6 tiles). A deal-size band (e.g. "USD 30–50k") counts its upper end; the list shows the band text. OK?
2. Doc impact of D39–D43 (the ticket itself asks for the docs/08 endpoint appendix):
| Doc | Section | What changes |
| --- | --- | --- |
| 01-architecture.md | §08 Sheet sync | One snapshot per sheet read with a `sync_id`; a broken tab is skipped, last good data kept (D40, D41). |
| 08-dashboard-spec.md | Shell, Today, view 7, view 14, drawers | Updated bar red states (D43); `source` on every tile; Money blocked tile and list (D42); "new columns" and stale tabs on health; email and links in the person drawer (D39). |
| 10-sheet-column-map.md | Rules | "A required header that maps to no column stops that tab's sync; other tabs go on" (D41). |
**Update the docs?** (yes / no)
- **Answer (Trishi, 19 Oct, chat):** 1. D42 yes: Money blocked tile on the Blockers page. A band counts its midpoint, not the upper end (USD 30–50k → 40k). Show "≈ USD 40k" and the band text in the list. Exact values count as given. 2. Docs: yes, update all three. Confirm D39–D43 as Trishi, 19 Oct, surface = chat.
- Done: D39–D43 confirmed in docs/DECISIONS.md. docs/01 §08, docs/08 (Shell, view 4 drawer, view 7, view 14) and docs/10 Rules updated. Deal value = band midpoint in sync, list label and `saley sheet verify`.

## Q-R1-6 · 2026-10-18 · Update the docs for D29–D35 and the role name? (resolved)
These answers change what some doc sections say. Impact:
| Doc | Section | What changes |
| --- | --- | --- |
| 01-architecture.md | §11 table, "Total" row | Read-write role is `saley`, not `saley_rw` (roles that exist on 5432; `.env.example`). |
| 06-gtm-brain.md | §6.1 Stage rules | Order Won / Lost → Qualified → Meeting → Parked → Engaged → Connected → Contacted → Listed (D29). Qualified = Status lead / demo / quote / deal or Closure Prob ≥ 50 %; Lost adds "dead", "closed lost", Closure Prob 0 % (D31). |
| 08-dashboard-spec.md | Shared rules, "Owner of a person" | Owner rule D31 replaces D25: Sid - LI Addition value → Sid; First Contact Type "LI" → Sid; else Vaishnavi. Goal links: "industry rule" (D30). |
| 08-dashboard-spec.md | view 7 Blockers | Goal links can be pinned in docs/goal-deliverable-map.yaml (D34). |
**Update the docs?** (yes / no)
- **Answer (Trishi, 18 Oct, chat):** yes, update all four sections. Also replace every "saley_rw" in docs and `.env.example` with "saley". Add one line to D29 and D31 naming the doc sections they changed. Do not change CLAUDE.md.

## Q-R1-5 · 2026-10-18 · Two small actions to finish R1 on port 5432 (resolved)
1. **Test database.** Tests need `saley_test`, and the `saley` role cannot create databases. Please run once, as the Postgres superuser:
   `psql -h 127.0.0.1 -U postgres -f deploy/sql/roles.sql`
   It creates `saley_test` (owner `saley`) and the grants. Roles that exist keep their passwords; nothing else changes.
2. **`.env` allowlist.** `.env` still has the old `EGRESS_ALLOWLIST`. Saley blocks the Google token refresh and the Discord test loop with it. Please copy the value from `.env.example`:
   `EGRESS_ALLOWLIST=sheets.googleapis.com,oauth2.googleapis.com,discord.com,api.anthropic.com,api.openalex.org,export.arxiv.org,news.google.com,imap.gmail.com`
- Optional: `.env` has no `DATABASE_URL_ADMIN`, so migrations run as `saley` and `saley` owns the tables. That works. To keep DDL out of the worker role, set `DATABASE_URL_ADMIN` to the `saley_admin` login before the first migration.
- **Answer (Trishi, 18 Oct, chat):** `roles.sql` ran as postgres on 5432; `saley_test` exists. `EGRESS_ALLOWLIST` in `.env` matches `.env.example`. `DATABASE_URL_ADMIN` is set to the `saley_admin` login; migrations run with it from now on.

## Q-R1-4 · 2026-10-18 · DISCORD_SALES_CHANNEL_ID equals the test channel id in .env (resolved)
- Asked: set `DISCORD_SALES_CHANNEL_ID=` (empty) in `.env`; the guard refused the bot because both ids were equal.
- **Answer (Trishi, 18 Oct, chat):** `DISCORD_SALES_CHANNEL_ID` is now empty in `.env`. Re-run the test loop.

## Q-R1-3 · 2026-10-18 · Guesses made in R1 (resolved)
**Answers (Trishi, 18 Oct, chat).** Recorded as D29–D35 (confirmed) in docs/DECISIONS.md.
1. D29 stage order: Won / Lost first, then Qualified, then Meeting, then Parked, then Engaged, Connected, Contacted, Listed. A Reconnect Date parks a person only below Meeting.
2. D30: link goals by industry until the Goal column exists. The link source is shown as "industry rule".
3. D31: First Contact is a checkbox. Owner: a value in "Sid - LI Addition" → Sid; else First Contact Type "LI" → Sid; else Vaishnavi. "VR" / "Sid" text in First Contact is accepted too.
4. Won = "won", "signed", "closed won". Lost = "lost", "dead", "closed lost", or Closure Prob "0%".
5. Qualified = Status "lead", "demo", "quote" or "deal", or Closure Prob ≥ 50 %.
6. D33: N = 3, the goals whose text starts with "Close".
7. D34: Dependencies text now, plus the override file docs/goal-deliverable-map.yaml.
8. D35: R4 sign-in users are the Membrane Google accounts of Vaishnavi, Sid, Kushal and Trishi; addresses later.
9. D32: in-process allowlist only; the egress container stays a compose profile stub until Hetzner.
10. Laptop-only until R4; /api is refused outside dev mode.
11. With the new order David Gamez is Qualified. Stage derivation and parity are re-run.
12. Duplicates are removed in the sheet copy. The data_quality flag stays.

<details><summary>Original questions</summary>

1. D29 stage precedence: a Reconnect Date parks a person even after a meeting. OK?
2. D30 goal links by industry until the Goal column exists. OK, or should Goal stay empty?
3. D31 First Contact values: is the cell a checkbox, or "VR" / "Sid"? The code accepts both.
4. Deal Status words for Won and Lost: the code looks for "won" / "signed" and "lost".
5. Status for Qualified: the code looks for "lead" or "demo" in the Status cell.
6. D33 "Deals closed of N": N = goals that start with "Close". Is N = 3 right?
7. D34 deliverable → goal links come from the Dependencies text. Is a manual map better?
8. D35 user emails: which Google accounts sign in (R4)? Not needed for R1.
9. Egress container (D32): forward proxy as a second wall, or in-process only?
10. Server exposure before Google sign-in (R4): the web refuses every /api call outside dev mode. Is laptop-only OK until R4?
11. D29 effect on real data: David Gamez has a completed meeting, Status "Lead" AND a Reconnect Date, so he shows as Parked. Keep Parked first, or put Qualified above Parked?
12. Master Pipeline lists Udio, Krisp and Sarvam AI twice. Please remove the duplicates in the sheet.

</details>

## Q-R1-2 · 2026-10-18 · Local database login fails (resolved)
- Asked: the `.env` logins failed on the Postgres 17 service at 127.0.0.1:5432; Claude Code used a private cluster on port 5433.
- **Answer (Trishi, 18 Oct, chat):** roles `saley`, `saley_ro`, `saley_admin` and database `saley` exist on 127.0.0.1:5432 with the `.env` passwords. Use `.env`. Drop the 5433 cluster. Fix `deploy/sql/roles.sql` so it runs with plain `psql -f` (default passwords, a comment on how to change them); CREATE DATABASE is its own statement outside a transaction, and the README says so.
- Done: the 5433 cluster is removed; `roles.sql` is rewritten (rw role is `saley`, as in `.env.example`). Follow-up: Q-R1-5.

## Q-R1-1 · 2026-10-18 · The service account and the sheet copy (resolved)
- Asked: share the sheet copy with the service account, move the key file out of the repo, and confirm the account.
- **Answer (Trishi, 18 Oct, chat):** the key file is outside the repo and `.env` points at the new path. The sheet copy is shared with sales-bot@sales-bot-write.iam.gserviceaccount.com as Viewer. v2 uses this account for now, reads only until a release needs writes; a v2-only account can replace it at Hetzner. Never read or print the file.
