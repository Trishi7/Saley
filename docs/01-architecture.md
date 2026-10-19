# Saley Architecture

Source: design page, exported 18 Oct 2026. Diagrams are Mermaid.

## 01 · Big picture

```mermaid
flowchart LR
  subgraph PEOPLE["People"]
    V["Vaishnavi · Sid · Kushal"]
  end
  subgraph IN["Outside data"]
    SH["GTM sheet"]
    AP["Apollo<br/>(to do, Wed)"]
    NEWS["News feeds · APIs"]
    MAIL["Membrane cc mailbox"]
  end
  subgraph HZ["Hetzner server"]
    DASH["Dashboard"]
    BOT["Discord bot"]
    BRAIN["Intelligence worker"]
    DB[("PostgreSQL")]
  end
  LLM["LLM via Anthropic API"]
  V -->|"look · approve · ask"| DASH
  V <-->|"ping · chat"| BOT
  SH <-->|"sync 60 s"| BRAIN
  AP --> BRAIN
  NEWS --> BRAIN
  MAIL --> BRAIN
  BRAIN <--> DB
  DASH --> DB
  BOT <--> DB
  BRAIN <--> LLM
```

Data comes in from the left. The worker cleans it, thinks, and stores proposals. People see them on the dashboard or get a ping. A yes goes back to the sheet.

## 02 · Containers on the server

```mermaid
flowchart TB
  NET["Internet"] --> CADDY["caddy<br/>TLS · reverse proxy"]
  CADDY --> WEB["web-api<br/>dashboard + JSON API<br/>read-only DB role"]
  subgraph COMPOSE["docker compose"]
    CADDY
    WEB
    BOT["bot<br/>Discord gateway"]
    WORKER["worker<br/>jobs · rules · LLM calls"]
    NEWS["news<br/>editions · watch lane"]
    PROXY["egress<br/>allowlist · rate limit"]
    PG[("postgres<br/>existing install")]
  end
  WEB --> PG
  BOT --> PG
  WORKER --> PG
  NEWS --> PG
  WORKER --> PROXY
  NEWS --> PROXY
  BOT --> PROXY
  PROXY --> OUT["Sheets API · Apollo · feeds · OpenAlex · Anthropic API · IMAP"]
  PG --> BK["nightly pg_dump<br/>encrypted · off-server"]
```

| Container | Does | Talks to | RAM |
| --- | --- | --- | --- |
| caddy | HTTPS in. Routes to web-api. | web-api | 50 MB |
| web-api | Serves the dashboard. Reads views. One write: decide. | postgres (read role), worker (decide queue) | 300 MB |
| bot | Discord in and out. Pings. Answers tags. | postgres, worker queue | 200 MB |
| worker | Sync, rules, facts, proposals, sheet writes, metrics. | postgres, egress | 500 MB |
| news | Pulls 94 sources, cleans, stores stories. | postgres, egress | 300 MB |
| egress | Only door to the internet. Allowlist. Per-host rate limit. | internet | 50 MB |
| postgres | Already on the server. One new database `saley`. | all | shared |

## 03 · Web + API

```mermaid
flowchart LR
  U["Browser<br/>Google sign-in"] --> C["caddy"] --> A["web-api"]
  A -->|"GET /api/*"| V["SQL views<br/>v_today · v_goal_status<br/>v_account_coverage · v_contact_stage<br/>v_suggest_* · v_signal<br/>v_deliverable_late · v_event_targets"]
  V --> PG[("postgres<br/>role: saley_ro")]
  A -->|"POST /api/proposal/{id}/decide"| Q["decision row<br/>+ signature"]
  Q --> PG
  W["worker"] -->|"reads approved"| PG
```

* Fourteen views in the dashboard. Each view = one endpoint = one SQL view.
* Roles: viewer, approver. Approvers: Kushal, Sid, Vaishnavi. Trishi: admin.
* Every decision stores: who, when, surface = dashboard.

## 04 · Bot

```mermaid
flowchart LR
  D["Discord"] <--> B["bot"]
  B -->|"@Saley tag"| Q["ask queue"] --> W["worker"]
  W -->|"answer · reminder · proposal"| B
  W -->|"ping: meeting tomorrow · approval waiting · reminder due"| B
  B -->|"Approve ✓ / Reject ✗ buttons"| PG[("postgres<br/>decision + signature<br/>surface = discord")]
```

```mermaid
sequenceDiagram
  participant S as Sid
  participant V as Vaishnavi
  participant B as bot
  participant P as postgres
  S->>B: "today's news"
  B->>P: stories today, minus user_seen(Sid)
  B->>S: "Window 00:00–14:10 IST. Top 5. 12 more."
  B->>P: user_seen += (Sid, 5 story ids)
  V->>B: "today's news"
  B->>P: stories today, minus user_seen(Vaishnavi)
  B->>V: same top 5. Her list is empty, so nothing is hidden.
  V->>B: "anything else?"
  B->>P: minus user_seen(Vaishnavi)
  B->>V: "Next 5. I left out the 5 you saw at 14:12."
```

Seen is per person, not per channel. Every answer states its time window and how many items were left out as already seen.

* The bot does three things: ping, chat, approve. No schedule of its own.
* Per-user seen applies to every query answer: news, people lists, suggestions, reminders.
* Caps: 3 posts a day, 2 h gap, weekend silence. Meetings and reconnects are the exception.
* Instructions only from the allowlist. Everyone else gets answers.

## 05 · Worker: Intelligence

```mermaid
flowchart LR
  T["Triggers<br/>sync diff · new story · tag · clock"] --> R["Rules engine<br/>11 rules, plain code"]
  R -->|"needs words"| L["LLM job"]
  R -->|"no words"| P["Proposal queue"]
  L --> P
  P --> H{"Human<br/>approve · edit · reject"}
  H -->|yes| WR["Sheet writer<br/>7 cells · email_write · row_add"]
  H -->|no + reason| K["Learn log"]
  WR --> F["New facts"]
  K --> F
  F --> R
```

Rules decide. The LLM only reads and writes words. Humans approve. The loop learns from rejections.

| Job | When (IST) | Output |
| --- | --- | --- |
| sheet\_sync | every 60 s | entities, touches, stage\_log |
| rules\_tick | every 5 min | proposals, reminders |
| signal\_match | after each news edition | signals |
| working\_on\_refresh | 05:00, 1/7 of contacts | facts |
| mail\_read | every 10 min | replies, bounces, meeting notes → facts |
| apollo\_pull | weekly + on demand · to do, Wed 22 Oct | candidate companies and people |
| daily\_brief | 08:30 | Discord: top 5 + count |
| metrics\_snapshot | 23:50 | metric rows |
| weekly\_pack | Mon 14:00 | pack page + Discord summary |
| expire | 00:30 | expired facts and proposals |

## 06 · News module

```mermaid
flowchart LR
  S["94 sources<br/>feeds · pages · Google lanes · APIs"] --> E["4 editions a day<br/>06:30 · 12:00 · 17:00 · 22:00<br/>+ watch lane hourly"]
  E --> C["Clean in memory<br/>canonical URL → seen 14 d → fingerprint → entity match → gate"]
  C -->|pass| ST["news_stories<br/>one row per story"]
  C -->|fail| DR["news_dropped<br/>index 14 d"]
  ST --> SC["Scorer<br/>stage 2"]
  SC --> DG["Digests 08:00 · 13:00 · 18:00"]
  ST --> SG["signal_match<br/>→ Linked tab"]
  ST --> ALL["All tab<br/>day · week · month · year"]
```

Details are in Saley News Plan v3. Here it is one box that feeds the Intelligence worker.

## 07 · Apollo pipeline

```mermaid
flowchart LR
  G["Goal + motion<br/>ICP filters"] --> A["Apollo search<br/>direct API<br/>Trishi builds, Wed 22 Oct"]
  A --> ST["apollo_candidates<br/>raw rows + credits used"]
  ST --> M["Match to sheet<br/>company alias · name · domain"]
  M -->|known| U["Update facts<br/>email · phone · title"]
  M -->|new| P["Proposal<br/>row_add company / person"]
  P --> H{"Approve"}
  H -->|yes| SH["Sheet + DB"]
  CAP["Monthly credit cap"] -.-> A
```

* Serves R02, R06, R11, S07–S09 and Part E of the requirements.
* Nothing lands in the sheet without a yes. Credits are counted per month.
* Apollo MCP is not used. It costs too much today. Until Wednesday the flow is marked To do, and R02 / R06 use news, papers, event pages and web search only.

## 08 · Sheet sync

```mermaid
sequenceDiagram
  participant S as GTM sheet
  participant W as worker
  participant P as postgres
  participant H as Human
  loop every 60 s
    W->>S: read 8 tabs
    W->>W: diff by row key (Sr No + Name)
    W->>P: upsert entities, touches, stage_log
  end
  Note over W,P: a changed cell = a touch or a field update
  H->>P: approve proposal
  W->>P: read approved
  W->>S: write allowed cells only
  W->>P: mark done + audit row
```

* Sheet wins on identity, step and commercial columns. DB wins on derived fields.
* Allowed writes: First Contact, First Contact Type, First Contact Date, Sid - LI Addition, LI Connected Date, Meeting Date, Meeting Status. Plus Email via `email_write` and new rows via `row_add`. Plus Stage, Motion, Goal after D6.
* First migration is reviewed: diff report, human fixes, then cutover.
* One sheet read = one snapshot with a `sync_id` (`sheet_snapshot.id`). A new snapshot is made only when the sheet changed. Every API request reads one snapshot in one read-only transaction and returns its `sync_id`. A stale `?sync_id=` gets 409 "Data changed. Reload the page." (D40)
* Headers are found by name, never by letter. A required header that is missing or renamed stops that tab only: its last good data stays, `sheet_tab_status` marks it stale, and Sources and health shows a red line with the header. The other tabs sync. Outreach PoCs depends on Master Pipeline. Unknown headers are listed as "new columns". (D41)

## 09 · Egress proxy

```mermaid
flowchart LR
  W["worker · news · bot"] --> X["egress"]
  X -->|allow| OK["sheets.googleapis.com<br/>api.apollo.io (later)<br/>api.anthropic.com<br/>api.openalex.org<br/>export.arxiv.org<br/>feed hosts (94)<br/>imap.gmail.com"]
  X -->|block| NO["linkedin.com<br/>everything else"]
  X --> LOG["egress_log<br/>host · count · status"]
```

One door out. Per-host rate limits stop blacklisting. The log feeds the health view.

## 10 · LLM

```mermaid
flowchart LR
  J["Job"] --> C{"cache hit?<br/>input hash, 7 d"}
  C -->|yes| R["reuse"]
  C -->|no| K{"under daily cap?"}
  K -->|no| Q["queue to tomorrow<br/>rules still run"]
  K -->|yes| O["Anthropic API<br/>LLM_SMALL · LLM_MID"]
  O --> V["validator<br/>schema · no new names, emails, dates"]
  V --> S["store output + tokens"]
```

| Job | Calls / day | Model |
| --- | --- | --- |
| action\_hint, rank\_reason, working\_on | ≤ 210 | small |
| draft, answer, note\_extract, mail\_extract | ≤ 100 | mid |
| brief, pack, replan | ≤ 3 | mid |
| **Total** | ≈ 310 | ≈ 0.8 M in · 50 k out tokens |

## 11 · Database: what it stores

```mermaid
flowchart TB
  subgraph SALES["Sales (from the sheet)"]
    A1["strategy · goal · deliverable · package · event"]
    A2["account · contact · touch · stage_log · opportunity"]
  end
  subgraph BRAIN["Intelligence"]
    B1["fact · signal"]
    B2["proposal · decision · ask · reminder · commitment · user_seen"]
    B3["metric_snapshot · experiment · template"]
  end
  subgraph NEWS["News"]
    C1["news_sources · news_editions · news_source_runs"]
    C2["news_seen · news_stories · news_sent · news_dropped"]
  end
  subgraph OPS["Operations"]
    D1["sync_outbox · sync_audit · apollo_candidates"]
    D2["mail_messages · alias · dnc · egress_log · llm_calls · users"]
  end
  SALES --> BRAIN
  NEWS --> BRAIN
  OPS --> SALES
```

| Group | Tables | Holds | Written by |
| --- | --- | --- | --- |
| Sales | 10 | Mirror of the 8 sheet tabs plus derived stage and deal rows | sheet\_sync, approved writes |
| Intelligence | 10 | What Saley knows, proposes, asks, remembers, measures, and what each person has seen | worker, humans (decide) |
| News | 8 | Sources, pulls, stories, what was sent, what was dropped | news container |
| Operations | 9 | Sync queue and audit, Apollo raw, mail, aliases, do-not-contact, logs, users | worker, bot, web-api |
| **Total** | 37 | One database `saley`. Three roles: `saley` (worker, news, bot; read-write rows), `saley_ro` (web-api; read-only, views only for personal data), `saley_admin` (migrations, owns the schema). Created by `deploy/sql/roles.sql`. | |

## 12 · Schemas and relations

### 12.1 Sales

```mermaid
erDiagram
  strategy ||--o{ goal : serves
  strategy ||--o{ account : targets
  account ||--o{ contact : employs
  contact ||--o{ touch : receives
  contact ||--o{ stage_log : moves
  contact ||--o| opportunity : opens
  opportunity }o--|| goal : counts_toward
  opportunity }o--o| package : uses
  goal ||--o{ deliverable : depends_on
  event ||--o{ contact : expected_at
  strategy { int id PK  text name  text icp  int icp_per_month  int orgs_per_month  text model_abc  bool ready }
  goal { int id PK  text text  int value_min  int value_max  text metric  text target  date due  int strategy_id FK }
  account { int id PK  text name  text industry  text geo  text funding  text tier  text status  int motion_id FK  int goal_id FK  text apollo_org_id }
  contact { int id PK  int account_id FK  text sheet_row_key  text name  text title  text email  text email_status  text phone  text city  text li_url  text paper_url  text priority  text role_in_deal  bool connected_with_vr  text stage  int motion_id FK  int goal_id FK }
  touch { int id PK  int contact_id FK  text type  date date  text template_id  text outcome  text source }
  stage_log { int id PK  int contact_id FK  text stage  timestamptz from_at  text reason }
  opportunity { int id PK  int contact_id FK  int goal_id FK  int package_id FK  text status  int prob  int size_usd  text deal_status  text champion  text budget_owner  text risk }
  deliverable { int id PK  text item  text dependency  text priority  date due  text status  date done  text link }
  package { int id PK  text name  text purpose  text use_case  text size  int pct  bool ready }
  event { int id PK  text name  text city  date start  date end  text url  bool registered  bool attended }
```

`contact.stage` is derived on every sync. `touch.type` ∈ li\_req, li\_accept, email1, email2, email3, li\_dm, call, meeting, reply, bounce.

### 12.2 Intelligence

```mermaid
erDiagram
  contact ||--o{ fact : about
  account ||--o{ fact : about
  news_stories ||--o{ signal : becomes
  contact ||--o{ signal : links
  account ||--o{ signal : links
  contact ||--o{ proposal : about
  proposal ||--o| decision : gets
  users ||--o{ decision : signs
  users ||--o{ ask : receives
  ask ||--o| fact : answered_as
  users ||--o{ commitment : owns
  strategy ||--o{ metric_snapshot : measures
  goal ||--o{ metric_snapshot : measures
  fact { bigint id PK  text entity_type  int entity_id  text kind  jsonb value  text source_type  text source_ref  text author  timestamptz observed_at  int confidence  timestamptz expires_at  bigint superseded_by }
  signal { bigint id PK  bigint story_id FK  text entity_type  int entity_id  text match_kind  text tag  int importance  int impact  text action_hint  text state }
  proposal { bigint id PK  text type  text entity_type  int entity_id  jsonb payload  jsonb evidence  int rank  timestamptz due_at  text state }
  decision { bigint id PK  bigint proposal_id FK  text kind  text choice  text reason  int user_id FK  timestamptz at  text surface }
  ask { bigint id PK  int to_user FK  text question  int entity_id  timestamptz asked_at  timestamptz answered_at  bigint answer_fact_id FK  text msg_id }
  reminder { bigint id PK  int for_user FK  text text  timestamptz fire_at  int entity_id  text state }
  commitment { bigint id PK  int owner FK  text text  date due  text source_ref  text state  int reminders_sent }
  metric_snapshot { date day  text scope_type  int scope_id  text metric  numeric value }
  users ||--o{ user_seen : has
  users { int id PK  text email  text name  text role  text discord_id }
  user_seen { int user_id FK  text item_type  bigint item_id  timestamptz seen_at  text query_id }
```

`user_seen` records what each person was shown, by item type (story, contact, suggestion). Queries subtract it per user. `decision` is the signature table. Every approval, edit, reject and strategy decision has one row: who, when, where. `decision.kind` ∈ proposal, strategy\_decision, column\_write.

### 12.3 News

```mermaid
erDiagram
  news_sources ||--o{ news_source_runs : runs
  news_editions ||--o{ news_source_runs : contains
  news_sources ||--o{ news_stories : found_by
  news_stories ||--o{ news_sent : posted_as
  news_stories ||--o{ signal : linked
  news_sources { int id PK  text name  text url  text kind  text class  text category  int rate_per_day  text hours  bool active  text fallback }
  news_editions { int id PK  timestamptz slot  int pulled  int stored  int dropped  int seconds }
  news_source_runs { bigint id PK  int source_id FK  int edition_id FK  int status  int items  int new  int ms }
  news_seen { text url_hash PK  timestamptz first_seen }
  news_stories { bigint id PK  text story_key  text headline  text best_url  text outlets  int outlet_count  int source_id FK  text category  text tag  int importance  int impact  text why  timestamptz published  timestamptz stored }
  news_sent { bigint id PK  bigint story_id FK  text channel  text kind  timestamptz at }
  news_dropped { bigint id PK  text url_hash  text headline  text reason  timestamptz at }
```

### 12.4 Operations

```mermaid
erDiagram
  proposal ||--o| sync_outbox : queues
  sync_outbox ||--o{ sync_audit : logs
  apollo_candidates }o--o| contact : matched
  mail_messages }o--o| contact : from_or_to
  alias }o--|| account : names
  dnc }o--|| contact : blocks
  sync_outbox { bigint id PK  bigint proposal_id FK  text tab  text row_key  text col  text value  text state  int tries }
  sync_audit { bigint id PK  bigint outbox_id FK  text before  text after  timestamptz at  text by }
  apollo_candidates { bigint id PK  text apollo_id  text kind  jsonb raw  int credits  int matched_id  text state  timestamptz at }
  mail_messages { bigint id PK  text msg_id  text from_addr  text to_addr  text subject  timestamptz at  text kind  int contact_id FK  bigint fact_id }
  alias { int id PK  int account_id FK  text alias  text approved_by  timestamptz at }
  dnc { int id PK  int contact_id FK  text reason  timestamptz at  text by }
  egress_log { date day  text host  int calls  int errors }
  llm_calls { bigint id PK  text job  text model  int tokens_in  int tokens_out  bool cached  timestamptz at }
```

## 13 · Sizes and retention

**~1 k**contacts today

**~300**accounts

**~2 k**stories per month

**~50 k**facts per year

**< 2 GB**database after 1 year

| Table group | Retention | Size after 1 year |
| --- | --- | --- |
| Sales | Forever. History in stage\_log and touch. | ~20 MB |
| fact | 1 year. Superseded rows kept. | ~100 MB |
| signal, proposal, decision | 1 year | ~50 MB |
| user\_seen | 90 days | ~5 MB |
| metric\_snapshot | Forever. ~60 rows a day. | ~5 MB |
| news\_stories | 90 days. Sent stories 1 year. | ~300 MB |
| news\_seen, news\_dropped | 14 days | ~20 MB |
| news\_raw\_debug | 48 h, off in prod | 0 |
| mail\_messages | Headers 1 year. Bodies not stored. Facts extracted. | ~10 MB |
| apollo\_candidates | Raw JSON 90 days. Matched ids forever. | ~50 MB |
| llm\_calls, egress\_log | 90 days | ~30 MB |

## 14 · Cost estimate

```mermaid
pie showData title Monthly run cost, USD (mid case ≈ 128)
  "Apollo credits (plan set Wed)" : 60
  "LLM via Anthropic API" : 45
  "Backup storage" : 5
  "Domain + TLS" : 2
  "Hetzner server (already paid)" : 0
  "X, OpenAlex, arXiv, HF, GitHub (free)" : 0
  "Buffer for watch lane + Apollo spikes" : 16
```

| Item | Low / month | Mid / month | High / month | Note |
| --- | --- | --- | --- | --- |
| Hetzner server | $0 | $0 | $0 | Existing. Adds ~1.5 GB RAM, ~2 GB disk. |
| LLM (Anthropic API) | $15 | $45 | $90 | ≈ 310 calls/day. Small model for 2/3 of calls. |
| Apollo credits | $0 (not used yet) | $60 | $120 | Direct API plan. Decided Wed 22 Oct. Apollo MCP not used: too costly. Cap in D9. |
| Backup storage | $3 | $5 | $8 | Object storage, 20–50 GB encrypted. |
| Domain + TLS | $1 | $2 | $2 | Caddy gets certificates free. |
| Free APIs | $0 | $0 | $0 | OpenAlex, arXiv, HF, GitHub, X public, Google News RSS. |
| Jev scorer (stage 2, optional) | $0 | $1 | $3 | $0.042 per M tokens. |
| **Total** | **≈ $19 today** | **≈ $113–128** | **≈ $223** | Apollo is the only line that can jump. Today it is $0. |

One-time: about 6–7 weeks of build (section 16). No new hardware.

## 15 · Security in one picture

```mermaid
flowchart LR
  subgraph EDGE["Edge"]
    C["caddy TLS"] --> G["Google sign-in<br/>allowlisted emails"]
  end
  subgraph APP["App"]
    G --> RO["web-api · read-only role"]
    RO -.->|"only write: decide"| SIG["decision + signature"]
  end
  subgraph CORE["Core"]
    SIG --> WK["worker · rw role<br/>writes only approved rows<br/>column locks in code"]
    WK --> VAL["validator<br/>no invented names · emails · dates"]
    WK --> DATA["LLM input = DATA blocks<br/>never instructions"]
  end
  subgraph OUT["Out"]
    WK --> EG["egress allowlist<br/>linkedin.com blocked"]
    PG[("postgres<br/>private port")] --> BK["encrypted backup<br/>off-server"]
  end
  ENV[".env on server only<br/>never in prompts or logs"] -.-> WK
```

## 16 · Build order

```mermaid
flowchart LR
  I1["I-1 · 1 week<br/>DB · sync · stage rules<br/>metrics · 5 read views"] --> I2["I-2 · 1 week<br/>facts · working on<br/>@Saley answers · mail read"]
  I2 --> I3["I-3 · 2 weeks<br/>rules · proposals<br/>approvals + signatures<br/>sheet writer"]
  I3 --> I4["I-4 · 1–2 weeks<br/>signals All / Linked<br/>events · suggestions<br/>briefs · deal cards"]
  I4 --> I5["I-5 · 1 week<br/>weekly pack Mon 14:00<br/>1:1 agenda · re-plan"]
  N1["News stage 1 · 2 weeks<br/>same DB, parallel"] -.-> I4
  T1["Apollo pipeline · Trishi<br/>starts Wed, ~3 weeks"] -.-> I4
```

* After I-1: read-only views from real data. After I-3: approvals end to end. After I-5: the full dashboard. About 6–7 weeks in total.
* No Apollo until the direct pipeline lands. Suggestions run on sheet, news and papers until then.

Companion pages: Saley v2 — Requirements (doc) · Saley Intelligence SD · Saley Dashboard Mock · Saley GTM Brain · Saley News Plan v3. Base: Hetzner design of 15 Oct 2026. Rules: CLAUDE.md in Trishi7/sales-bot.
