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
| 09-bot-messages.md | v2 (R7): roles and writers, channels, caps, the card catalogue M1–M15 A1 A2, card behaviour, deep links, answers | Contract for saley/bot |
| 10-sheet-column-map.md | 37 Outreach PoCs columns with role and lock, other tabs, row key | Contract for saley/sales |
| 11-coexistence.md | v1 and v2 side by side | Channel and sheet rules while both run |
| 12-templates.md | Draft email, LI and reconnect templates with ids and placeholders | Drafts in approval cards. Sending OFF. |
| 12b-voice.md | Saley's voice in Discord: character card, rules, words, examples | Every bot message and answer (R7) |
| 13-apollo-pipeline.md | Apollo direct pull (Trishi, from Wed 22 Oct) | Part E |
| 14-runbook.md | Setup, backup, restore, secrets, deploy | Claude Code fills |
| 15-test-plan.md | Acceptance tests per ticket | saley-tester runs them |
| 17-bot-trd.md | How the v2 bot is built on the R4 bot: architecture, data model, flows, error table, security, tests, rollout | saley/bot, the R7 build |
| 18-bot-mock.html | The mock of every bot message, Discord look | Open in a browser |
| 16-verification-standard.md | Build pipeline, the 12-step gate, test matrices, /tidy, chain rules; appendix: the chain ticket R2–R4 (verbatim) | every release: /verify, then /tidy |
| DECISIONS.md | Every human decision with id, date, by, affected docs | Append on every answer |
| STATUS.md | Done, open, env changes per ticket | Update at the end of every ticket. |
| QUESTIONS.md | Questions for the human, newest first | Write here, then stop and wait. |
| goal-deliverable-map.yaml | Goal → deliverable links for goals whose Dependencies text is not enough (D34) | Read by the blockers code; ships in the image |
| ste-banned-words.txt | Words the STE lint refuses | Read by `saley.qa.ste_lint` (gate step 3) |

## Plans (one file per release)

| File | What |
| --- | --- |
| plans/R1.md | R1 plan |
| plans/R1.1.md | R1.1 plan (hardening, the gate) |
| plans/R2.md | R2 plan |
| plans/R3.md | R3 plan |
| plans/R4.md | R4 plan |
| plans/R5.md | R5 plan (Dashboard v2); Appendix A: the human's brief, verbatim |
| plans/R6.md | R6 plan (Deploy to the server); Appendix A: the human's brief, verbatim |
| plans/R7.md | R7 plan (the Discord bot v2); Appendix A: the human's brief, verbatim |
| plans/R7-brief.md, plans/R7-ponytail-map.md | The R7 brief and the tickets with the ponytail loop |

Tool notes for R7 (laptop only): `tools/SETUP-graphify-ponytail.md`, with reference copies `tools/ponytail-SKILL.md`, `tools/graphify-README.md`, `tools/v1-voice-book.md`.

## Test reports (the PASS report per release, plus gate inputs)

| File | What |
| --- | --- |
| test-reports/R1-final.md, R1.1-final.md, R2-final.md, R3-final.md, R4-final.md | The GATE: PASS report of each release |
| test-reports/R5-files.txt | Source manifest of the last release: gate step 2 reads it as the coverage baseline (R4-files.txt is the older one) |
| test-reports/R5-impeccable.json | R5 Impeccable audit scores |
| test-reports/R6-impeccable.json | R6 Impeccable scores (R5 scores carried; Signals re-scored for the Opportunity topic) |
| test-reports/R7-impeccable.json | R7 Impeccable scores (R6 scores carried; Sources & health has the Discord block, People and Accounts open a drawer from a card link) |
| test-reports/<release>-gate.json, -matrix.json, -compose.json, -api-abuse.md, -sheet-verify.md, tidy-*.md | Gate run records: by-products, ignored by git (D255); `tidy.py --commit-note` lists any that are still tracked |
| test-reports/<release>-<date>.md | A gate run that is not a PASS (steps 1-9, or a failed finalize). A PASS writes <release>-final.md (D236). Run records move to .scratch/ at a tidy stop |

Older run records, dev logs and screenshots live in `.scratch/test-reports/` (ignored).

## Outside docs/

| File | What |
| --- | --- |
| ../README.md | Set-up and run steps |
| ../CLAUDE.md | Rules for every Claude Code session |
| ../web/PRODUCT.md | Product truth for the dashboard design work (Impeccable, D196) |

Build order: P0 skeleton → I-1 → I-2 → I-3 → I-4 → I-5. News stage 1 (N-1) runs in parallel after P0. Apollo pipeline is separate (Trishi).

When two docs disagree, 02-requirements.md wins on behaviour and 01-architecture.md wins on names and tables. Write the conflict in QUESTIONS.md.
