# 09 · Bot messages

> Status: DRAFT. Message shapes are not yet agreed with Trishi. Build the bot only for: Claude Code test loop (`saley discord send/read --test`), `@Saley ping`, and the release ping. Everything else in this file waits for a decision.

The bot does three things: ping, chat, approve. It has no schedule of its own. The worker decides when a ping is due. The bot delivers it.

## Channels
| Channel | Use | Env |
| --- | --- | --- |
| saley-test-v2 | Everything while TEST_MODE=true. Messages carry the `[TEST]` prefix. | DISCORD_TEST_CHANNEL_ID |
| API Sales | Team-wide pings in prod. | DISCORD_SALES_CHANNEL_ID |
| DM to owner | Owner-only items and the one repeat. | user id from `users.discord_id` |
| saley-dev-pings | Claude Code hooks only. Not the bot. | webhook, outside the repo |

## Caps (bot only)
- 3 scheduled posts per channel per day. Overflow moves to the next day, ranked.
- 2 h gap between scheduled posts. Pings for a meeting in < 24 h skip the gap.
- Weekend: silent, except meeting briefs and reconnect-due pings (D10). Questions are always answered.
- Reminder budget per item: 1 reminder + 1 on the due day + weekly pack. Then silence. Never the word "overdue".
- Allowlist: `DISCORD_INSTRUCT_IDS` may instruct, `DISCORD_APPROVER_IDS` may approve. Others get answers only.

## Tone
- Short. Please and thank you. One ask per message. No robot words ("executing", "processing").
- Group by action, not by person. Max 5 names per list. Then "N more — say *more* to see them".
- Every answer ends with its time window and the seen line.

## Message types

| Type | Trigger | Shape | Buttons | Writes |
| --- | --- | --- | --- | --- |
| Daily brief | 08:30 IST weekdays | `Good morning. 3 things today:` then 3 lines (approval waiting, meeting, due reminder). Count of the rest. Link to dashboard. | Open dashboard | user_seen for items shown |
| Action card | Saley's own idea that is time-critical, or human asks | Title · why (1 line) · draft text in a code block when it is an email or note | ✅ Do it · ✏️ Edit · ❌ Skip | write + signature (surface=discord). A human instruction in chat writes at once, no card. |
| Meeting brief | 24 h before Meeting Date | Company card (3 lines) · person working on · last 3 touches · package · 3 questions · 1 risk | Open brief | — |
| Reminder due | reminder.fire_at | `@owner reminder: <text>` | Done · Snooze 1 d | reminder state |
| Commitment nudge | due − 1 d, due day | `@owner when you have a moment: <commitment>, due <date>.` | Done · Move date | commitment state |
| Ask | worker needs a fact | `@person quick one: <question>` | Reply in thread | answer → fact(source=teammate) |
| Blocker note | Monday 14:00, with the pack | `Late items that block goals:` ≤ 5 lines, owner, what it blocks | — | — |
| Weekly pack summary | Mon 14:00 | 12 lines: scoreboard RAG, funnel delta, top 3 actions, 1 plan suggestion, link | Open pack | — |
| Answer | @Saley <question> | Answer with citations `[row 412]`, `[fact 881]`, `[story 2201]`. Then window + seen line. | more · dismiss | user_seen |
| Health line | 23:45 IST to test channel | jobs ok/failed, calls vs cap, egress errors | — | — |

## Per-user seen (R01, C12)
- Query: `stories where window ∧ not in user_seen(user)`. Order: importance desc, published desc.
- Answer footer: `Window: Fri 17 Oct 00:00–14:10 IST · 5 shown · 12 more · 0 left out as seen by you.`
- Second ask: `… 5 shown · 7 more · 5 left out: you saw them at 14:12.`
- `more` shows the next 5 and records them. `all` shows everything ≥ importance 4.
- Seen is per user and per item type (story, contact, suggestion). Not per channel.

## @Saley grammar (I-2)
- Questions: "who at <company> works on evals", "news since Wednesday", "what is <person> working on", "which P1 have no email", "what is due this week".
- Instructions (allowlist): "remind me <when> to <text>", "cancel reminder <n>", "approve <id>", "reject <id> because <reason>", "note: <fact> about <person>" (becomes fact with author).
- Unknown: `I do not have that. I can search the web, or ask <person>. Which?`

## Templates (I-3) — see docs/12-templates.md. Email sending is OFF (`EMAIL_SEND_ENABLED=false`). Drafts are shown in the approval card and copied by a human.
