# Saley v2 — Requirements

Oct 18, 2026 · @NFThing

## 1. Purpose and how to read

Saley v2 is the sales lead and chief of staff for Sid and Vaishnavi. This document lists every requirement, old and new, in one place.

- Each requirement has an id. Old rules keep their sheet ids (R01–R14, C01–C12). New ones use S-ids.
- Each row shows a state: **Keep** (as is), **Upgrade** (same goal, better rule), **Retire**, **New**.
- Priority: **M** = must for v2 launch, **S** = should, **L** = later.
- Surface: where it shows. **Dash** = dashboard. **Bot** = Discord. Most items show on both. The bot only speaks when a human must act.
- Schedules from the old sheet are not binding. Each rule now has a trigger.
- Sources: Sales Bot\_membrane sheet (14 rules, 12 conduct rules, global caps), GTM Playbook (live 16 Oct), Discord export 22 Sep–15 Oct, this planning thread 17–18 Oct, pages Saley GTM Brain, Saley Dashboard Mock, Saley Intelligence SD.

## 2. Part A · Existing rules R01–R14

All 14 rules stay covered. Eight are upgraded. One is retired. Triggers replace weekday slots.

| Id | Rule today | State | v2 rule | Trigger | Surface | Pri |
| --- | --- | --- | --- | --- | --- | --- |
| R01 | AI news, global + India, linked to pipeline, ≤5 items, no repeats | Upgrade | Two tabs. **All**: every story for day / week / month / year, sorted by score, filters by category, source, tag. **Linked**: stories joined to accounts and people, each with one action or "no action". Digest to Discord stays. Per-user seen: each answer states its time window, shows the top items that person has not seen, and says how many it left out as already seen. Seen is per person. What Sid saw does not hide anything from Vaishnavi. | News editions 06:30 / 12:00 / 17:00 / 22:00 + watch lane | Dash + Bot | M |
| R02 | Find relevant companies from news | Upgrade | Companies and people come from the Apollo pipeline (Part E) plus news, papers (OpenAlex), event speakers and co-authors. Saley also suggests new companies on its own, weekly batch. Check Master Pipeline first. Explain fit. Approval before a row. | Story or paper names a company not in Master Pipeline; weekly batch | Dash + approval | M |
| R03 | Event reminders + discovery | Keep | Remind for logged events. Find new events weekly. Ask before adding. Build a target list per event from people in that city. | Event ≤14 d; weekly scan | Dash + Bot | M |
| R04 | Weekly deliverables, task + due date | Upgrade | Blocker chain. Each late item shows the goals and deals it blocks. Rank by money blocked. Monday message keeps task + date. | P1 item due this week or late | Dash + Bot Monday | M |
| R05 | Prioritise first outreach, P1 then A–F | Upgrade | One score per person: goal fit, role, warmth, email known, paper, city. Score parts visible. Five per post. Name / company / designation only. | First Contact blank | Dash + Bot | M |
| R06 | Verified email after LI connect, >3 d, no DM | Upgrade | Find email and phone through the Apollo pipeline (Part E, to do Wed 22 Oct). Until then: company site, paper, web search only. Then company site, paper, web search. Label each result: verified / found / not found. No LinkedIn fetch. | Connected, no email, >3 d | Dash + verify job | M |
| R07 | DM follow-up after 7 d | Retire | Covered by R13. | — | — | — |
| R08 | Meeting prep −5 / −3 / 0 d | Upgrade | Meeting brief 24 h before: company card, person "working on", last 3 touches, package to show, 3 questions, 1 risk. Checklist stays. | Meeting Date set | Dash + Bot | M |
| R09 | Capture next steps after meeting, +3 d then every 3 d | Keep | Ask only for the empty fields: next steps, package, deal size. First ask in API Sales, then DM. | Meeting Status completed, notes empty | Bot + Dash | M |
| R10 | Deal progression, prob >50 %, Monday | Upgrade | Deal review card: champion, budget owner, pain, timeline, next meeting, risk. Saley asks only for the empty field. Offers research and news. | Status deal / demo / quote | Dash + Bot Monday | M |
| R11 | Find people for a new company, +1 d | Keep | Offer first, research on yes. Return name, designation, LinkedIn URL, paper. Support department asks. Approval before rows. | Company added, no PoCs | Bot + Dash | M |
| R12 | Sales package readiness, Thursday | Keep | Ask status and date for every package with Ready? = No. Packages link to the goals and motions they block. | Ready? = No | Dash + Bot | M |
| R13 | Outreach sequence for Connected people, 5 per post, rotation | Upgrade | One plan per account, not one timer per person. Inputs: motion, goal, warmth, people covered, last touches. Output: who first, what, when, for the next 7 days. Timers still run (2 d, 7 d, 3 d × 2 wk, then Unresponsive). | Stage timers + weekly plan | Dash + Bot daily 15:00 | M |
| R14 | Reconnect on date or 45-d fallback, after 14:00 | Upgrade | 45-day rule stays for everyone. People with a reason (paper, funding, hire, event in their city) rank first and get the reason in the draft. No reason gets a plain check-in draft. | Reconnect Date or fallback | Dash + Bot | M |

Open points from the sheet that still need a decision are in Part I.

## 3. Part B · Conduct rules C01–C12 and global caps

All conduct rules stay. The bot is a ping to Sid and Vaishnavi for what the dashboard cannot push, plus a chat partner for questions and instructions. The old weekday schedule does not bind it. The caps below apply to the bot only. The dashboard has no caps.

| Id | Rule | State | v2 note | Pri |
| --- | --- | --- | --- | --- |
| C01 | Human conversation, no keywords, tolerate typos, follow-ups in context | Keep | Same. | M |
| C02 | Proactive and on demand. Weekend silence for scheduled posts. Questions allowed on weekends. | Keep | Dashboard is always live. | M |
| C03 | Public-web research: people, LinkedIn URLs, papers, funding, HQ | Keep | Adds OpenAlex, arXiv, HF, GitHub, company pages. | M |
| C04 | Approval before any sheet change | Keep | Approval buttons live on the dashboard and in Discord. Same queue. | M |
| C05 | Clear, polite voice. Please, thank you. Bullets. No robotic commentary. | Keep | Politeness rule for reminders: one reminder, one on due day, then the weekly pack. Never "overdue". | M |
| C06 | Acknowledge long requests | Keep | Same. | M |
| C07 | Ad-hoc reminders on request | Keep | Reminder table. Shows on dashboard. Cancel and edit by tag. | M |
| C08 | Correct data meaning: Next Steps vs post-meeting notes, moved columns | Keep | Column map in config. Column Q = Connected with VR on LI, read only. | M |
| C09 | Resolve company identities, point out discrepancies | Keep | Alias table, human approved. Google ≠ DeepMind. Rosebud AI ≠ Rosebud wellness. | M |
| C10 | Sales-focused only | Keep | Same. | M |
| C11 | API Sales channel primary, DMs for owner items and repeats | Keep | Same. Dashboard is for Vaishnavi and Sid. | M |
| C12 | Filters on request (stage, country, fields) | Keep | Dashboard filters replace most of these asks. Bot still answers. Every bot answer applies per-user seen (table user\_seen): window stated, unseen items first, count of items left out stated. | M |

| Cap | Value | State |
| --- | --- | --- |
| Channel posts per day | 3, overflow to next day | Keep for bot |
| Contacts per outreach post | 5 | Keep for bot |
| Gap between scheduled posts | 2 h | Keep for bot |
| Weekend | No scheduled posts unless a deliverable is due Monday | Keep. Meeting and reconnect reminders: decide (Part I) |
| Late 3+ days | Also DM the owner | Upgrade: one DM only, then weekly pack |
| Default time | 14:00 IST | Keep for sheet-driven reminders |

## 4. Part C · New core requirements S01–S41

These make Saley plan, find people, know them, act, track and answer. All are New.

**Plan**

| Id | Requirement | Data needed | Pri |
| --- | --- | --- | --- |
| S01 | Read strategy, goals, deliverables, packages. Build the goal tree: goal → motion → accounts → people. | GTM tabs | M |
| S02 | Weekly targets per motion from the monthly targets. | Strategy tab | M |
| S03 | Goal scoreboard: active conversations, need, blockers, days left, red / amber / green. | Goals, PoCs, deliverables | M |
| S04 | Weighted pipeline per goal = deal size × probability. | Deal fields filled | S |
| S05 | Re-plan proposal when actuals < 70 % of target for 2 weeks. | Metric snapshots | S |
| S06 | Monthly learn loop: meetings per 100 contacts per motion, template reply rates, move effort. | Touches, replies | L |

**Acquire people and companies**

| Id | Requirement | Data needed | Pri |
| --- | --- | --- | --- |
| S07 | Best chance to connect: not contacted, company has a connection, email known, decision role. Scored, with reasons. | PoCs | M |
| S08 | Best person per company: budget / research / data / user roles. Add Role in Deal to PoCs. | PoC titles | M |
| S09 | New companies: Master Pipeline rows with no PoCs, ranked by goal fit, funding, news. Plus companies Saley finds on its own (R02 doors). | Master Pipeline, news, papers | M |
| S10 | Researchers: Academia with papers, ranked by topic, recency, city near a teammate or event. | PoCs, OpenAlex | M |
| S11 | Event target lists: people in the event city, connected first. | Events, PoC city | S |
| S12 | Speaker-list match for logged events. | Event URLs | S |
| S13 | Lookalike companies from news and public APIs. | News, HF, GitHub | L |

**Know the people** (PoC detail flow is separate, see Part E)

| Id | Requirement | Data needed | Pri |
| --- | --- | --- | --- |
| S14 | "Working on" per PoC: About PoC → paper → OpenAlex / arXiv → news → teammate answer. Show source and date. | Several | M |
| S15 | Account coverage and warmth per company. | PoCs | M |
| S16 | Hiring signals from careers pages of pipeline companies. | Page watch | S |
| S17 | Job moves of PoCs from news. | News | L |

**Strategise**

| Id | Requirement | Data needed | Pri |
| --- | --- | --- | --- |
| S18 | Tag each PoC to a motion and a goal. Saley proposes by industry. Human confirms in batch. | PoC industry | M |
| S19 | Operating model per segment: A volume, B account-based, C pilot-led. Metrics per model. | Decision D1 | S |
| S20 | Package match: meeting done → propose the package. Package gaps → tell the plan. | Packages, meetings | S |
| S21 | Competitor moves → risk note in the weekly pack. | News | L |

**Execute and remind**

| Id | Requirement | Data needed | Pri |
| --- | --- | --- | --- |
| S22 | Next 10 actions, ranked, with evidence and Approve / Edit / Reject. | All | M |
| S23 | Backlog rule: connected + email + no email 1 > 3 d. | PoCs | M |
| S24 | P1 14-day rule: LI not accepted → email. | PoCs | M |
| S25 | Park after 3 touches, no reply, 30 d. Reconnect +60 d. | Touches | M |
| S26 | Sequence timing 0 / 4 / 7 / 12 d. Template id per touch. Timers learn from replies later. | Touches, templates | S |
| S27 | Draft emails and LI notes from facts only. Validator rejects unknown names, emails, dates. | Facts | S |
| S28 | Reminders from @Saley ("remind me Friday"). Cancel and edit by tag. | Discord | M |

**Track and report**

| Id | Requirement | Data needed | Pri |
| --- | --- | --- | --- |
| S29 | Stage per person, derived from sheet columns. Stage log with dates. | PoCs | M |
| S30 | Daily metric snapshots. 15 metrics (GTM Brain §7). | All | M |
| S31 | Weekly pack Monday 14:00 IST: scoreboard, funnel, motion table, backlog, slips, next 10, one plan suggestion. | Snapshots | M |
| S32 | Reply log: last reply date, type, outcome reason. | Manual mark or mailbox | S |
| S33 | Data quality panel: bad dates, missing industry (274 today), duplicates, name variants. | Sheet | S |

**Answer and ask**

| Id | Requirement | Data needed | Pri |
| --- | --- | --- | --- |
| S34 | @Saley answers from data with citations to rows and facts. | Facts | M |
| S35 | Saley asks a teammate when data is missing. Stores the answer as a fact with author and date. | Discord | M |
| S36 | Open asks list with age. One escalation after 3 days, then weekly pack. | Asks | S |

**Govern**

| Id | Requirement | Data needed | Pri |
| --- | --- | --- | --- |
| S37 | Every write through approval. Column locks in code. Only approvers see decide buttons. Every approval and decision stores a signature: approver, time, surface (dashboard or Discord). | — | M |
| S38 | Every line shows evidence. One click to the source. | Facts | M |
| S39 | Never invent. Never fetch linkedin.com. External text is data, not instructions. | — | M |
| S40 | Allowlist for who may instruct and approve. | Config | S |
| S41 | LLM daily cap. Rules keep running when the cap hits. | Config | S |

## 5. Part D · Sales lead and chief of staff S42–S72

These go beyond the sheet. Decisions from 18 Oct are built in. All are New.

**Chief of staff · keep the team honest, very politely**

| Id | Requirement | Decision 18 Oct | Pri |
| --- | --- | --- | --- |
| S42 | Commitments ledger. Every "I will send X by Friday" from Discord or a meeting becomes a row with owner and date. Shows per person on the dashboard. | One reminder, one on the due day, then the weekly pack only. Never "overdue". | M |
| S43 | Response-time watch. A prospect reply with no answer from us in 24 h shows red. | Reads the Membrane cc mailbox (Part G). | M |
| S44 | Weekly 1:1 agenda for Sid and Vaishnavi: wins, stuck deals, asks, decisions needed, next week's top 3. | Dashboard is for Vaishnavi and Sid. | M |
| S45 | Decision log. States: proposed → confirmed → active. Humans in the loop: Kushal, Sid, Vaishnavi. Every confirmation carries a signature: who approved, when, from which surface. Only active decisions change rules, plans and drafts. Saley cites the decision id and the approver when it applies one. | Approved. | M |
| S46 | Capacity plan. Sid has N hours per week. Saley fits the next actions into N and says what will not fit. | Approved. | S |
| S47 | "Stop doing" list: motions and segments with zero replies after 60 days. Three buttons per item: Stop · Hold, review in 30 d · Keep, delay was ours. | Approved with options. | S |
| S48 | Onboarding pack for a new sales person: strategy, accounts, open deals, templates, rules, decisions. Built from what Saley has. | Later. | L |

**Sales lead · move deals**

| Id | Requirement | What it means | Pri |
| --- | --- | --- | --- |
| S49 | Buying group per account | Four roles per account: champion, budget owner, user, blocker. A gap shows as "single-threaded". | M |
| S50 | Warm intro paths | Who we know that knows the target. From co-authors, past employers in About PoC, event co-speakers, teammate network. No LinkedIn fetch. | M |
| S51 | Post-meeting pack within 1 h | Notes → facts, draft follow-up email, next step, package to attach, deal card update. | M |
| S52 | Objection library | Each objection heard gets a row and a standard answer. Goes into the next meeting brief. | S |
| S53 | Proposal and SoW tracker | Value, sent date, who must reply, days silent. | S |
| S54 | Forecast | Commit / likely / possible per goal from stage × probability. Reviewed Monday. | S |
| S55 | Win / loss review | One short form per closed deal. Reasons feed the learn loop. | L |
| S56 | Pricing guide | Price band per package and size. Warning when a quote is off band. | L |

**Strategist · choose where to play**

| Id | Requirement | What it means | Pri |
| --- | --- | --- | --- |
| S57 | Segment scorecard | One table per industry: people, contacted, connect %, reply %, meetings, deals. Daily. Replaces the Mar–Jun funnel tab. | M |
| S58 | ICP fit score per company | One number from industry, funding, hiring, research output, language work, geography. Column in Master Pipeline. | S |
| S59 | Quarter plan proposal | Draft of next quarter's motions and targets from the scorecard. Humans edit. | S |
| S60 | Experiment tracker | A vs B on templates, subject lines, send times. Sample size and result. | S |
| S61 | Market map | Competitors, their clients, their pricing from public sources. Monthly. | L |
| S62 | Tender and grant calendar | Government and lab calls with deadlines. Linked to Goal 3. | L |

**Researcher · know more than the prospect expects**

| Id | Requirement | What it means | Pri |
| --- | --- | --- | --- |
| S63 | Company card | Funding, size, products, latest news, hiring, research output, who we know. Weekly refresh for active accounts. | M |
| S64 | Person card | Role, "working on", papers, talks, posts, last touches, mutual links. Same refresh. Detail collection follows Part E. | M |
| S65 | Pulse demo proposal | For a Tier 1 account: which pulse to run on their model or a competitor's. Links to motion 3. | S |
| S66 | Co-author graph | Who publishes with whom. Finds the lab lead behind a PhD student. | S |
| S67 | Talk and podcast watch | A PoC speaks somewhere → reconnect reason. | L |

**Operator · keep the machine clean**

| Id | Requirement | What it means | Pri |
| --- | --- | --- | --- |
| S68 | Do-not-contact list | Bounced, opted out, asked to stop. Saley blocks proposals for them. | M |
| S69 | Duplicate and alias control | One alias table, human approved. | M |
| S70 | Consent note per contact | Source of the email, date, basis. Needed for UK and EU outreach. | S |
| S71 | Template library | Versions and reply rates. | S |
| S72 | Health view | Jobs, caps, skipped rows, open proposals by age. | L |

## 6. Part E · PoC detail flow (separate flow, to be defined)

How Saley finds companies and people, and their details, is a separate pipeline. Trishi will build it. It pulls directly from Apollo. Work starts Wed 22 Oct. Apollo MCP is not used: it costs too much today. This section holds the place and the boundaries.

- Scope: email, phone, role, "working on", papers, public profile link, mutual links.
- Consumers: R06, R11, S07, S08, S10, S14, S50, S64.
- Fixed rules that the flow must respect: no linkedin.com fetch, no connection requests, no invented data, every field with source and date, paid finders only under a cap, approval before any sheet write.
- Tools reviewed 18 Oct: OpenOutreach and 50k-lead-generation-system. Neither is used. Apollo direct pull replaces them.
- Open: Apollo search filters per goal, credit cap per month, what "verified" means, how Apollo rows merge with sheet rows.

* [ ] Trishi: write the Apollo pipeline flow and link it here.

## 7. Part F · Dashboard requirements

The dashboard is the main surface. The bot reminds and answers when tagged. Base: Saley Dashboard Mock, 17 Oct.

| View | Must show | Requirements served | Pri |
| --- | --- | --- | --- |
| Today | Six tiles, goal scoreboard, funnel, next 10 actions with Approve / Edit / Reject, open commitments, open asks | S03, S22, S42, S36 | M |
| Q4 goals | One card per goal: active conversations, need, blockers, weighted pipeline, forecast buckets | S03, S04, S54, R04 | M |
| Accounts | One row per company: people, contacted, connected, emailed, coverage, warmth, buying group gaps, account plan, "Saley says". Filters: P1, has connection, goal, industry | S15, S49, R13, R10 | M |
| People | Stage, "working on" with source, email status, next step, last touches. Filters: backlog, P1 waiting, in conversation, reconnect due | S29, S14, R06, R13, R14 | M |
| Suggestions | Four lists: best chance, best person per company, new companies, researchers. Score parts shown. Approve to add rows | S07–S10, R02, R05, R11 | M |
| Signals | Tab All: every story by day / week / month / year, sorted by score, filters. Tab Linked: stories joined to accounts and people with one action each | R01 | M |
| Blockers | Late deliverables and packages with what they block, ranked by money blocked | R04, R12 | M |
| Events | Each event: days left, people in city, connected, P1, target list, speaker match | R03, S11, S12 | M |
| Asks and approvals | Approval queue, Saley's asks with age, team asks to Saley, reminders | S35, S36, S28, C04 | M |
| Commitments | Ledger per person, due dates, state. Polite by design: no red words | S42 | M |
| Decisions | Log with states proposed / confirmed / active, approver signature (name, time, surface), where applied | S45 | M |
| Weekly pack | Monday 14:00 page: scoreboard, funnel, motion table, backlog, slips, next 10, one plan suggestion, 1:1 agenda | S31, S44, S05 | M |
| Scorecard | Segment table per industry, daily | S57 | M |
| Sources and health | What Saley reads, last run per job, caps used, skipped rows, data quality | S33, S72 | S |

Cross-cutting:

- Every line carries an evidence link (S38). Every list shows "last computed".
- Google sign-in, allowlisted emails. Roles: viewer, approver. Approvers see decide buttons.
- Read-only database role for all views. One write endpoint: decide.
- Works on a laptop screen first. Phone width readable.
- Same data feeds the Discord brief. One source of truth.

## 8. Part G · Sources and integrations

| Source | State | Used by | Note |
| --- | --- | --- | --- |
| GTM Playbook, 8 tabs | Live | All | Sync every 60 s. Two-way inside the 7 allowed cells, email\_write, row\_add. |
| News tables (stories, sent, dropped) | Live | R01, R02, S14, S21 | News Plan v3, stage 1. |
| Discord channels and @Saley tags | Live | C01–C12, S28, S34, S35, S42 | Ping + chat. Allowlist for instructions. |
| Drive meeting notes | Live, deferred by Vaishnavi 7 Oct | S51, R09 | Turn on when she agrees. |
| Web search (SearXNG, DuckDuckGo, Google News RSS) | Live | C03, R02, R11 | Never linkedin.com. |
| Apollo pipeline, direct pull | To build (Trishi, Part E) | R02, R06, R11, S07–S09, Part E | Primary source for new companies, people, emails, phones. Credit cap per month. |
| Apollo MCP | Not used | Same as above | Not used. Too costly today. |
| Membrane cc mailbox | Decided 18 Oct | S32, S43, S51, R08, R09 | One Membrane address cc'd on all sales emails and calendar invites. Read-only. Replaces separate mailbox, Calendar and recorder access. Meeting recorder notes sent to the same address. |
| OpenAlex, arXiv | Add, free | S10, S14, S50, S66 | By author name + affiliation check. |
| Hugging Face, GitHub public | Add, free | S13, S63 | Org activity. |
| Company team and careers pages | Add, free | S16, S63 | Page watch. |
| Event speaker pages | Add, free | S12 | Match to PoCs. |
| X public posts | Decided 18 Oct: yes if free | S14, S67 | 143 handles in sheet. Free tier or public pages only. Rate limits. |
| Google account for Alerts | Decide | R01 | 225 name alerts. |
| OpenOutreach, 50k-lead-generation-system | Not used | — | Reviewed 18 Oct. |

## 9. Part H · Non-functional and guardrails

| Area | Requirement |
| --- | --- |
| Writes | Only the worker writes. Only from a proposal in state approved or edited. Column locks from CLAUDE.md enforced in code. |
| Truth | No invented name, email, URL, number or date. A validator checks drafts against the input fields. |
| Provenance | Every fact has source, time, confidence, expiry. Expired facts leave the views. |
| Prompt safety | Web, news, docs and notes enter the LLM as DATA blocks. Output parsed to a fixed schema. |
| LinkedIn | Egress proxy blocks linkedin.com. Profile URLs stored, never fetched. No connection requests. |
| Identity | Entity resolver needs two matches. One match = confidence 1 + "verify" flag. |
| Politeness | Reminder budget: 1 + 1 on due day + weekly pack. Tone rules from C05. |
| Cost | LLM daily call cap. Cache by input hash 7 days. Rules run without the LLM. About 300 calls per day, USD 0.5–2. |
| Availability | Jobs idempotent. Locks expire 15 min. Docker restart. Health line 23:45. |
| Backup | Nightly encrypted pg\_dump off-server. |
| Secrets | .env read by the worker only. Never in prompts or logs. |
| Access | Google sign-in, allowlisted emails, roles viewer and approver. |
| Retention | Facts 1 yr, stories 90 d, sent 1 yr, proposals 1 yr, raw debug 48 h. |
| Data residency | Hetzner. Prospect data stays in the one PostgreSQL. |

## 10. Part I · Open decisions

| Id | Decision | Options | Decided | State |
| --- | --- | --- | --- | --- |
| D1 | Operating model per segment | A volume · B account · C pilot | B labs and sovereign, C academia, A voice startups | Decided 18 Oct, Trishi |
| D2 | Stage field | Derived only · New column Saley fills · Human fills | Derived now, column after migration | Decided 18 Oct, Trishi |
| D3 | Motion and goal tags on PoCs | Saley proposes, human confirms · Manual | Saley proposes, batch approval | Decided 18 Oct, Trishi |
| D4 | Sequence timing | Fixed 0 / 4 / 7 / 12 · Per motion · Manual | Fixed now | Decided 18 Oct, Trishi |
| D5 | Reply log source | Membrane cc mailbox, read-only | cc mailbox from launch | Decided 18 Oct, Trishi |
| D6 | Columns Saley may write | None · Stage, Motion, Goal · Also Tier, Account status | Stage, Motion, Goal | Decided 18 Oct, Trishi |
| D7 | Weekly pack day | Mon 14:00 IST | Mon 14:00 IST | Decided 18 Oct, Trishi |
| D8 | Plan change authority | Proposes only · Edits targets within ±20 % | Proposes only | Decided 18 Oct, Trishi |
| D9 | Paid finders | Apollo pipeline + Apollo MCP fallback, monthly credit cap | Apollo. Cap value to set | Decided 18 Oct, Trishi. Cap open |
| D10 | Weekend reminders | Silent · Meetings and reconnects allowed | Meetings allowed, rest silent | Decided 18 Oct, Trishi |
| D11 | R04 scope | Current week only · Also older overdue | Both, overdue on top | Decided 18 Oct, Trishi |
| D12 | "Verified email" standard | Finder says verified · Finder + domain check · Reply received | Apollo verified + domain check | Decided 18 Oct, Trishi |
| D13 | R13 date anchors and booked-but-not-completed meeting | To define with Vaishnavi | — | Open |
| D14 | R14 fallback precedence | Explicit → meeting +45 → latest date +45 | As listed | Decided 18 Oct, Trishi |
| D15 | Capacity N hours per week for Sid | Number from Sid | — | Open |
| D16 | Humans in the loop for decisions | — | Kushal, Sid, Vaishnavi. Trishi for product decisions. | Decided 18 Oct, Trishi |

## 11. Part J · Release cut and build order

v2 launch covers every M item: 14 sheet rules (R07 retired), 12 conduct rules, 22 core (S01–S41) and 13 sales-lead (S42–S72). S and L items follow after launch.

| Step | Scope | Requirement ids | Visible result | Time |
| --- | --- | --- | --- | --- |
| I-1 | Entities, sheet sync, stage rules, metric snapshots, alias table | S01–S03, S15, S29, S30, S57, S69, C08, C09 | Accounts, People, Goals, Blockers, Scorecard views, read-only | 1 week |
| I-2 | Facts, "working on" refresh, @Saley answers, asks, reminders | S14, S34, S35, S28, S63, S64, C01–C03, C07 | "Working on" column. Answers with citations. | 1 week |
| I-3 | Rules, proposal queue, approvals, sheet writer, commitments, decisions | S07–S10, S18, S22–S25, S37–S39, S42, S45, S68, R05, R06, R09, R11, R12, R13, R14, C04, C05 | Today view with next 10. Approvals end to end. | 2 weeks |
| I-4 | Signals, events, suggestions, meeting brief, deal card, buying group, intro paths | R01 tabs, R02, R03, R08, R10, S11, S49, S50, S51 | Signals All + Linked, Events, Suggestions, briefs | 1–2 weeks |
| I-5 | Weekly pack, 1:1 agenda, re-plan, response watch, learn log | S31, S44, S43, S05, R04 chain | Monday pack. Plan change proposals. | 1 week |
| Later | All S and L items | S04, S06, S12, S13, S16, S17, S19–S21, S26, S27, S32, S33, S36, S40, S41, S46–S48, S52–S56, S58–S62, S65–S67, S70–S72 | — | After launch |

I-1 starts now. It shares only the database with news stage 1.

- [ ] Trishi: D13 with Vaishnavi, D15 with Sid, Apollo cap.
- [ ] Trishi: PoC detail flow (Part E).
- [ ] Claude: ticket I-1 and the Claude Code prompt after the confirmations.
