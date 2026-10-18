# Saley v2 docs — index

| File | What | Use it for |
| --- | --- | --- |
| 01-architecture.md | Containers, parts, 37 tables with fields and relations, sizes, cost, security, build order | Where things live. Table and field names. |
| 02-requirements.md | Every requirement with an id. R01–R14 sheet rules, C01–C12 conduct, S01–S72 new. Decisions D1–D16. | What to build. Tickets cite these ids. |
| 03-intelligence-sd.md | Stores, 11 rules, 7 LLM jobs, jobs schedule, four flows, API to dashboard map, guardrails | How the worker thinks. |
| 04-news-plan-v3.md | Editions, clean-first pipeline, scorer contract, any-question order, news tables | News module behaviour. |
| 05-news-ingestion-stage1.md | 94 sources with classes, rates, fallbacks; architecture changes C1–C7 | Source registry for the news module. |
| 06-gtm-brain.md | What the GTM sheet holds, stage rules, data model, 15 metrics | Sheet columns, stage derivation, metrics. |
| 07-dashboard-mock.html | Clickable mock of the dashboard views | Layout and copy for the web package. |
| 08-dashboard-spec.md | View by view: endpoint, SQL view, fields, filters, actions, phase | Contract for saley/web |
| 09-bot-messages.md | Channels, caps, tone, every message type, per-user seen footer, @Saley grammar | Contract for saley/bot |
| 10-sheet-column-map.md | 37 Outreach PoCs columns with role and lock, other tabs, row key | Contract for saley/sales |
| 11-coexistence.md | v1 and v2 side by side | Channel and sheet rules while both run |
| 12-templates.md | Draft email, LI and reconnect templates with ids and placeholders | Drafts in approval cards. Sending OFF. |
| 13-apollo-pipeline.md | Apollo direct pull (Trishi, from Wed 22 Oct) | Part E |
| 14-runbook.md | Setup, backup, restore, secrets, deploy | Claude Code fills |
| 15-test-plan.md | Acceptance tests per ticket | saley-tester runs them |
| DECISIONS.md | Every human decision with id, date, by, affected docs | Append on every answer |
| STATUS.md | Done, open, env changes per ticket | Update at the end of every ticket. |
| QUESTIONS.md | Questions for the human, newest first | Write here, then stop and wait. |

Build order: P0 skeleton → I-1 → I-2 → I-3 → I-4 → I-5. News stage 1 (N-1) runs in parallel after P0. Apollo pipeline is separate (Trishi).

When two docs disagree, 02-requirements.md wins on behaviour and 01-architecture.md wins on names and tables. Write the conflict in QUESTIONS.md.
