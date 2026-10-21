# Product

<!-- impeccable:product-schema 1 -->

<!-- Written by hand per .claude/skills/impeccable/reference/init.md (D196, R5 T11). No
interview round was held: the R5 brief says "do not stall". Facts come from CLAUDE.md,
docs/08-dashboard-spec.md and docs/plans/R5.md (Appendix A). Lines marked (inferred) need a yes from
the human. -->

## Platform

web

## Users

- Sid and Vaishnavi, the Membrane sales team. They work from this dashboard instead of the GTM sheet. Job: "Sid and Vaishnavi understand the sales state in one glance" (the human, R5 brief), then act on today's tasks.
- Kushal views and decides (approver). Trishi is admin (Sources and health, Run news now).
- Situation: a laptop at work, first; a phone must stay readable (docs/08). Sid works 14:00 to 24:00 IST (D126).

## Product Purpose

Saley is the sales lead and chief-of-staff system for Membrane's sales team. The dashboard shows where the pipeline stands, what each person must do today, what needs a look now, and what Saley suggests. A click is the approval (D24): it writes to the sheet at once, signed, with an Undo for `UNDO_MINUTES` (5).

Success: a person opens Today and knows the sales state and the next action without reading a table.

## Positioning

Saley is the only writer of the GTM sheet (D23). Every number comes from the sheet or Saley's own tables and matches them (parity is tested). Rules in plain code decide; the LLM never picks a person or a stage.

## Operating Context

- 14 views: Today, Q4 goals, Accounts, People, Suggestions, Signals, Blockers, Events, Inbox, Commitments, Decisions, Weekly pack, Scorecard, Sources and health (admin).
- Daily work: tick tasks Done (Undo in place), add an email, open a person or company drawer, read new signals, mark commitments done (owner only).
- Discord carries the daily brief and pings; the dashboard is where people act.

## Capabilities and Constraints

- Words: ASD-STE100, short sentences, clear on first read. No rule ids, no week numbers, no table names on the page. Never the word "overdue".
- A number that is not known shows "—", never 0. Every number tile opens the list it counts.
- Off features show a "coming soon" badge (find people, speaker matches, email sending).
- Internal links are Buttons or clickable cards, never underlined URLs. Long lists: 10 per page with "N more".
- A legend at the top of each page explains every tag and colour.
- Dark and light themes. No CDN at runtime: fonts and scripts are bundled. Strict CSP (no inline script).
- Never invent a URL, email, name, number or date.

## Evidence on Hand

Real data only: the GTM sheet copy, news tables and meeting notes. There are no testimonials, customers or benchmarks to show, and none may be invented.

## Product Principles

1. Numbers first, as tiles or charts; lists second (R5 brief).
2. One glance: a page answers "where do we stand and what do I do now" before any detail.
3. Never a fake value: unknown is "—", and every value traces to its source.
4. One click acts; nothing is asked twice (D24).
5. Calm and polite: no alarm words, no nagging (inferred from the reminder rules in CLAUDE.md).

## Accessibility & Inclusion

Readable at 390 px, 1024 px and 1440 px. Keyboard reachable controls and visible focus (inferred: the gate's browser QA checks these widths; no other standard was named).
