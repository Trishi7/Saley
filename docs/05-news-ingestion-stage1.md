# Saley Ingestion Stage 1

Source: design page, exported 18 Oct 2026. Diagrams are Mermaid.

NFThing / membrane · Saley v2 · AI news · stage 1 of 4

# News ingestion: sources, schedule, fit

Stage 1 = get the news in, from reliable sources, with fallbacks. Stage 2 = pre-processing (dedupe, gate, flag). Stage 3 = scoring. Stage 4 = delivery. Feed rates measured 16 Oct 2026 from each feed's own items. Architecture: the 15 Oct Hetzner design.

**In one line.** 94 sources in one registry table. Each has its own clock, set from how often it publishes and when. One worker reads them through one egress proxy, stores raw items in PostgreSQL, and reports health every night. Nothing here calls a model.

## 1. Where ingestion sits in the architecture

EXISTING HETZNER SERVER · DOCKER COMPOSE

94 sources
63 RSS / Atom feeds
7 Google News lanes (3 editions)
38 page watches
Google Alerts RSS · OpenAlex · HF API
Bing News · hnrss (fallback)
Apollo MCP (when allowed)

Egress proxy
NEW container
host allowlist
per-host rate limit
one place to log

GET

Bot + worker
scheduler tick every 5 min
reads due sources from registry
max 4 fetches at once
one request per host at a time
conditional GET · honest UA
feed parse · page extract
writes raw items + health

PostgreSQL
news\_sources
news\_source\_runs
news\_items\_raw
news\_stories (stage 2)
news\_sent (stage 4)
NEW tables, same DB

Server files
page snapshots
14 days, evidence

Discord gateway (bot)
23:45 health line · source alerts

Stage 2
pre-processing, reads raw

LLM API
not called in stage 1

Off-server backup
registry + stories in the nightly dump

Inbound: only Caddy on 443 for the web workspace. Ingestion opens no port. Sources see the proxy's IP and one named User-Agent.

Figure 1. Stage 1 lives inside the existing "Bot + worker" container and the existing PostgreSQL. One new container (the egress proxy) and five new tables. The LLM API is not touched.

### Changes the attached design needs

| # | Change | Why | Size |
| --- | --- | --- | --- |
| C1 | **Add an egress (forward) proxy container.** Squid or tinyproxy in the same Compose file. The worker sets `HTTPS_PROXY` to it. The proxy holds the host allowlist and the per-host rate limit. | The design has a reverse proxy (Caddy) for inbound only. Fetching 94 sources needs one outbound choke point for limits, logs and an allowlist. On one host it does not hide the server IP; a separate €4 VM or a Hetzner floating IP does. Start on the same host; move later if a source blocks the IP. | Small |
| C2 | **Five news tables in PostgreSQL.** `news_sources` (the registry), `news_source_runs` (one row per fetch), `news_items_raw` (14 days), `news_stories` (stage 2), `news_sent` (stage 4). | The current bot keeps news in SQLite. The design says PostgreSQL only. v2 writes PostgreSQL from day one. v1 keeps SQLite until it is retired. | Medium |
| C3 | **A scheduler inside the worker.** Tick every 5 min. Query `news_sources WHERE next_due_at <= now()`. Fetch up to 4 at once, one per host. Write `next_due_at` from the source's own interval and active hours. | Today one clock runs everything hourly. 94 sources at 94 different rates need per-source clocks in data, not in code. | Medium |
| C4 | **Page-watch extractor.** For 38 sites with no feed: fetch one page, extract links in the news or blog region, diff against the last snapshot. Snapshot saved under Server files. | Competitors, Indian labs and government tender pages have no feeds. The design already has "uploads + source evidence"; snapshots are evidence. | Medium |
| C5 | **Secrets for three outside services.** A Google account for Alerts RSS (read-only feed URLs, no API). OpenAlex needs only a contact email. Apollo needs an API key and a credit cap, after the "no paid APIs" rule is changed in writing. | New sources, new keys. They go in `.env`, never in the repo. | Small |
| C6 | **Health line through the bot.** 23:45 IST, test channel: sources run / failed / silent, items fetched / new, backlog. Plus an alert when a tier-1 source is silent 24 h. | The design has "logs and health checks". This makes them visible to a human without SSH. | Small |
| C7 | **Backup scope.** Add the news tables to the encrypted nightly dump. Raw items can be excluded (14-day, re-fetchable). | The registry holds months of tuning. It must not be lost. | Small |

Nothing else in the design changes. The web workspace, HITL writes, the Sheets sync and the LLM API are untouched by stage 1.

## 2. The schedule model

Every source has five numbers in the registry. The scheduler reads them. A human edits them in the table. No deploy to change a clock.

| Field | Meaning | How it is set |
| --- | --- | --- |
| `rate_7d` | Items per day, 7-day average | Measured. Seed from the 16 Oct test. Recomputed nightly |
| `depth` | Items the feed holds | Measured on each fetch |
| `active_from`, `active_to` | IST hours in which 80 % of items appear | Measured. Seed from the test. Recomputed weekly |
| `poll_active_min` | Minutes between fetches inside active hours | By class (below), capped by never-miss |
| `poll_idle_min` | Minutes between fetches outside active hours and at weekends | By class |
| `never_miss_min` | `depth ÷ rate_7d ÷ 2` in minutes. Poll at least this often or items fall off the feed unread | Computed. Always wins over the class value |

| Class | Rule | Active poll | Idle poll | Examples |
| --- | --- | --- | --- | --- |
| HOT | ≥ 10 items/day, or a launch source where every post is NOW news | 30 min | 2–4 h | ET Tech, Techmeme, Bloomberg, Inc42, TechCrunch, Verge, OpenAI, DeepMind, Anthropic page |
| MED | 3–10 items/day | 60 min | 4 h | Guardian AI, Mint AI, Sifted, FT AI, The Information, Medianama, Tech.eu |
| LOW | < 3 items/day | 2–4 h | 12 h | MIT TR, Wired AI, Ars, ZDNet, Crunchbase News, UKTN, DSIT, ERC |
| DAILY | One batch a day, or a weekly newsletter | once, at the hour after the batch | — | arXiv (10:00), newsletters (10:00), PR Newswire (08:00, 20:00) |
| WATCH | Page watch, no feed | once a day (07:30–09:30 spread), competitors and labs; once a week (Monday 08:00) for grants, conferences, research blogs | — | Scale blog, Bhashini tenders, NIC tenders, Krutrim, NeurIPS |
| LANE | Google News query lanes | per lane (section 4) | — | names, broad, topics, hires, site:, competitors |
| ENRICH | API or MCP, not a feed | daily 07:30 (OpenAlex, HF orgs, Apollo job changes), weekly Monday (Apollo company signals) | — | OpenAlex, Hugging Face API, Apollo |

Spreading: each source gets an offset of `hash(source_id) mod poll_active_min`. 26 HOT sources on a 30-minute clock become one request every 70 seconds, not 26 at once. A missed slot runs at the next tick; nothing is skipped.

00040812162024 IST

Google broad + labsIndia desks (HOT/MED)US / UK desks (HOT/MED)LOW feedsDaily batchesPage watches

every 60 min, 24 h · overnight sweep at 02:00 for the US evening

08:00–21:00 every 30–60 min · else every 4 h

17:00–04:00 every 30–60 min · else every 4 h

every 2–4 h in active hours, 12 h idle

08:00 PR wires10:00 arXiv + newsletters20:00 PR wires

07:30–09:30 daily (38 pages, spread)

23:45 health
Weekend: HOT becomes idle for desks; labs, Techmeme and the Google broad lane stay hourly; page watches skip Sunday.

Figure 2. The day follows the publishers. India is polled hard by day, the US by night. Load is about 1,200 requests a day, most answered 304.

## 3. The registry: every source, its clock, its fallback

Status: KEEP in `.env` today and working · ADD tested 16 Oct, works · SERVER TEST blocked from my side, test from the server first · ROUTE no feed, reached by Google `site:` or page watch · DROP dead URL.

### 3.1 News desks: global

| Source | Status | Kind | Rate/day | Active IST | Class | Active poll | Idle poll | Cat. | Fallback |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TechCrunch AI | KEEP | RSS | 15 | 17–04 | HOT | 30 min | 4 h | B E F J | Google site: |
| The Verge AI | KEEP | RSS | 36 | 23–05 | HOT | 30 min | 4 h | F | Google site: |
| Ars Technica AI | KEEP | RSS | 3 | 18–03 | LOW | 2 h | 12 h | F I | Google site: |
| MIT Technology Review AI | KEEP | RSS | 1.2 | 13–22 | LOW | 4 h | 12 h | F G I | Google site: |
| VentureBeat AI | SERVER TEST | RSS | ~15 | 18–04 | HOT | 60 min | 4 h | B F | Google site:venturebeat.com hourly until the feed answers |
| Wired AI | ADD | RSS | 4 | 15–03 | MED | 2 h | 6 h | F I | Google site: |
| The Guardian AI | ADD | RSS | 9 | 09–01 | MED | 60 min | 4 h | F I | Google site: |
| FT AI | ADD | RSS (headlines) | 15 | 20–10 | HOT | 60 min | 4 h | F J | Google site: |
| The Information | ADD | Atom (headlines) | 16 | 23–11 | HOT | 60 min | 4 h | E F | Techmeme |
| Bloomberg Technology | ADD | RSS | 29 | 09–14 | HOT | 30 min | 4 h | F J | Google site: |
| Reuters technology | ROUTE | Google site: | — | all | LANE | 60 min | — | F I J | Bing site: |
| Axios | ADD | RSS (all topics, AI gate) | 13 | 14–03 | MED | 60 min | 4 h | F I | — |
| ZDNet AI | ADD | Atom | 3 | 17–01 | LOW | 2 h | 12 h | F | — |
| Techmeme | ADD | RSS | 31 | 02–10 | HOT | 30 min | 2 h | F E (launch detector) | hnrss |
| The Decoder | ADD | RSS | 9 | 20–01 | MED | 60 min | 4 h | F G | — |
| AI Business | ADD | RSS | 1.5 | 17–03 | LOW | 4 h | 12 h | B E | — |
| The New Stack AI | ADD | RSS | 3 | 16–01 | LOW | 2 h | 12 h | G H | — |
| MarkTechPost | ADD | RSS | 5 | 01–15 | MED | 2 h | 6 h | G | — |
| IEEE Spectrum AI | ADD | RSS | 0.6 | 15–22 | LOW | 12 h | 12 h | G I | — |
| PR Newswire AI | ADD | RSS | ~20 | 18–03 | DAILY ×2 | 08:00, 20:00 | — | A B E J | Business Wire (feed empty today; retest) |

### 3.2 News desks: India

| Source | Status | Kind | Rate/day | Active IST | Class | Active poll | Idle poll | Cat. | Fallback |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Inc42 | KEEP | RSS | 14 | 11–02 | HOT | 30 min | 4 h | A B D J | Google site: |
| ET Tech | KEEP | RSS (50 deep) | 26 | 07–20 | HOT | 30 min | 4 h | A B D I J | Google site: |
| YourStory | KEEP | RSS | 12 | 10–22 | MED | 60 min | 4 h | B D J | Google site: |
| Mint AI | ADD | RSS (no item dates) | ~35 | 08–22 | HOT | 30 min | 4 h | A B D K | Mint technology feed |
| Business Standard tech | ADD | RSS | 9 | 08–21 | MED | 60 min | 4 h | A D I | Google site: |
| CNBC-TV18 technology | ADD | RSS (200 deep) | ~40 | 08–22 | HOT | 60 min | 4 h | D J | — |
| Hindustan Times technology | ADD | RSS | ~15 | 08–22 | MED | 60 min | 4 h | D | — |
| The Hindu technology | ADD | RSS | ~10 | 08–21 | MED | 60 min | 4 h | D I | — |
| Hindu BusinessLine info-tech | ADD | RSS (no dates) | ~20 | 08–21 | MED | 60 min | 4 h | B D I | — |
| Indian Express AI | ADD | RSS (200 deep) | 2 | 07–17 | LOW | 4 h | 12 h | D I | — |
| Medianama | ADD | RSS | 10 | 11–17 | MED | 60 min | 4 h | I | — |
| India Today tech | ADD | RSS (20 deep, fast) | high | 08–22 | HOT | 30 min | 4 h | D | Low value; drop after 2 weeks if it adds nothing unique |
| Moneycontrol tech | DROP | RSS frozen 2024 | — | — | WATCH | page watch daily | — | D J | Google site: |
| Analytics India Magazine | SERVER TEST | RSS blocked here | ~10 | 10–20 | MED | 60 min | 4 h | B D G | Google site: hourly until it answers |
| Entrackr | ROUTE | Google site: | — | — | LANE | 2 h | — | J | — |
| Afaqs | ADD | RSS | ~10 | 10–19 | MED | 2 h | 12 h | K | — |
| Campaign India | ROUTE | page watch | — | — | WATCH | daily | — | K | Google site: |
| Exchange4media | SERVER TEST | 403 here | — | — | WATCH | daily | — | K | Google site: |

### 3.3 UK / EU

| Source | Status | Kind | Rate/day | Active IST | Class | Active poll | Idle poll | Cat. | Fallback |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Sifted | KEEP | RSS | 10 | 10–17 | MED | 60 min | 4 h | E J | Google site: |
| UKTN | KEEP | RSS | 1.4 | 12–18 | LOW | 4 h | 12 h | B J | — |
| Tech.eu | KEEP | RSS | 18 | 13–19 | HOT | 60 min | 4 h | E J | — |
| TechRound | ADD | RSS | 10 | 13–18 | MED | 60 min | 4 h | B J | — |
| EU-Startups | ADD | RSS | 11 | 11–18 | MED | 60 min | 4 h | J | — |
| Crunchbase News | KEEP | RSS | 2 | 16–18 | LOW | 2 h | 12 h | E J | — |

### 3.4 Government, tenders, grants, regulation

| Source | Status | Kind | Class | Poll | Cat. | Fallback / note |
| --- | --- | --- | --- | --- | --- | --- |
| PIB (MeitY, English) | SERVER TEST | RSS | MED | 2 h, 09–20 | C I | The ID tested was Hindi. Confirm the English MeitY feed ID |
| IndiaAI.gov.in + /missions | SERVER TEST | page watch | WATCH | daily | C D | Certificate error here. Google site:indiaai.gov.in as fallback |
| MeitY "what's new" | ROUTE | page watch | WATCH | daily | C I | — |
| Bhashini notifications & tenders | ROUTE | page watch | WATCH | daily | C D | A real tender page. High value |
| NIC tenders | ROUTE | page watch, keyword filter | WATCH | daily | C | Filter: AI, data, speech, annotation, evaluation |
| GeM (gem.gov.in) | ROUTE | Google site: | LANE | daily | C | Where government AI data buys appear |
| DST, SERB, ANRF | ROUTE | page watch | WATCH | weekly Mon | C | ANRF blocked here; server test |
| Startup India, NASSCOM | ROUTE | Google site: | LANE | weekly | C J | NASSCOM feed URL returns 0 items |
| gov.uk DSIT | ADD | Atom | LOW | 4 h, 14–20 | C I | — |
| gov.uk AI Security Institute | ADD | Atom | LOW | 4 h | C I | Covers AISI blog, grants, fast grants, alignment project. Plus /grants page watch weekly |
| EU digital-strategy (AI Act, enforcement, AI Pact) | ADD | RSS | LOW | 2 h, 13–22 | I | — |
| EU Funding & Tenders portal | ROUTE | Google site: | LANE | weekly | C | Portal API later |
| ERC | ADD | RSS | LOW | daily | C | — |
| UKRI opportunities | ROUTE | page watch | WATCH | weekly Mon | C | Feed URL exists but is empty |
| Wellcome grant funding | ROUTE | page watch | WATCH | monthly | C | Low fit |
| IAPP, ICO | ROUTE | Google site: | LANE | weekly | I | — |
| grants.gov | DROP | — | — | — | — | US federal grants, out of scope now |

### 3.5 Labs, Indian AI companies, competitors, voice

| Source | Status | Kind | Class | Poll | Cat. | Fallback / note |
| --- | --- | --- | --- | --- | --- | --- |
| OpenAI news | ADD | RSS | HOT (launch) | 60 min, 24 h | F B | Google by name |
| Google DeepMind blog | ADD | RSS (fix date parse) | HOT (launch) | 60 min, 24 h | F G | Google by name |
| Google AI blog | ADD | RSS | LOW | 4 h | F | — |
| Anthropic news | ROUTE | page watch | HOT (launch) | 60 min, 06–24 | F | Google by name |
| Meta AI blog | ROUTE | page watch | HOT (launch) | 60 min, 06–24 | F | Google by name |
| Mistral news | ADD | RSS (`/news/rss`) | LOW | 2 h | F | — |
| xAI news | SERVER TEST | page watch (403 here) | LOW | 4 h | F | Google by name |
| DeepSeek, Qwen | ROUTE | Qwen blog feed (stale) + HF org API | DAILY | 07:30 | F | Google by name |
| Hugging Face blog | ADD | RSS | LOW | 4 h | G H | — |
| Hugging Face org API (Sarvam, AI4Bharat, Qwen, DeepSeek, Mistral, Meta, Google, OpenAI + watched orgs) | ADD | API, free | ENRICH | daily 07:30 | D F G H | New models and datasets by org |
| NVIDIA blog, AWS ML blog | ADD | RSS | LOW | 4 h | F H | — |
| Sarvam AI blog | ADD | RSS (`/rss.xml`) | LOW | 2 h | D | — |
| Tech Mahindra | ADD | RSS, filter AI / Indus | LOW | daily | D | — |
| Krutrim | SERVER TEST | page watch | WATCH | daily | D | Google by name |
| BharatGen, Soket, Gnani, CoRover | ROUTE | page watch | WATCH | daily | D H | Google by name |
| Scale, Surge, Mercor, Toloka, Appen, Labelbox, Prolific, Invisible, Karya, Sama, Mostly AI | ROUTE | page watch (11 pages) | WATCH | daily 07:30–09:30 | E | Google by name, 2 h. Apollo signals weekly |
| iMerit | ADD | RSS (`imerit.ai/feed/`) | LOW | daily | E | — |
| Handshake AI, DesiCrew, Synthesis AI | SERVER TEST | page watch | WATCH | daily | E | Blocked or reset from here |
| Gretel | DROP | 404 (acquired) | — | — | E | Google by name |
| ElevenLabs blog | ADD | RSS | LOW | 2 h | H | — |
| Skit, Uniphore, Observe.ai | ROUTE | page watch | WATCH | daily | H | Google by name |
| ICASSP 2026 | ADD | RSS (`/feed/`) | LOW | daily | L H | — |
| Interspeech 2026, NeurIPS, ICLR | ROUTE | page watch | WATCH | weekly Mon | L | Dates, CFP, accepted papers, speakers |
| ISCA archive, ACL Anthology, Papers with Code, Google Scholar | DROP | no feed / blocks bots | — | — | G | arXiv + OpenAlex cover them |

### 3.6 Research, community, trackers

| Source | Status | Kind | Class | Poll | Cat. | Note |
| --- | --- | --- | --- | --- | --- | --- |
| arXiv cs.CL, cs.AI, cs.LG, cs.CY, eess.AS, cs.SD | ADD | RSS, keyword filter before store | DAILY | 10:00 | G H | Batch lands 09:30 IST. Filter keeps ~30 of ~500 |
| OpenAlex per researcher | ADD | API, free, contact email | ENRICH | daily 07:30 | A G | Replaces arXiv author search as primary; arXiv author stays as fallback |
| Semantic Scholar per researcher | ADD | API, free | ENRICH | weekly | A G | Second copy of OpenAlex |
| Alignment Forum | ADD | RSS | LOW | daily | G | — |
| LessWrong (karma ≥ 30) | ADD | RSS | LOW | daily | G | Karma filter on |
| EleutherAI, LAION, Cohere research | ROUTE | page watch | WATCH | weekly Mon | G | — |
| Import AI, Last Week in AI, Ben's Bites, The Rundown, Simon Willison | ADD | RSS | DAILY | 10:00 | F G | Weekly or daily letters |
| Product Hunt | ADD | Atom, AI gate | DAILY | 10:00 | B | Leads only; impact capped at 1 in stage 3 |
| Hacker News (hnrss, AI, ≥ 50 points) | ADD | RSS | DAILY + fallback | 10:00 | F | — |
| Reddit r/MachineLearning, r/LocalLLaMA | SERVER TEST | RSS, 429 here | DAILY | 10:00 | F G | 10 s gap, one request each. Drop if 429 persists |
| Artificial Analysis | ROUTE | page watch | WATCH | weekly | F | Leaderboard moves |
| LMArena, Perplexity Discover | DROP | 403 / app | — | — | F | Techmeme carries leaderboard news |

### 3.7 Google News lanes, Alerts, and enrichment

| Lane | Query shape | Editions | Poll | Requests/h | Cat. |
| --- | --- | --- | --- | --- | --- |
| Names | 15 names per query: pipeline companies, PoC companies, watch list | IN | 60 min, 06–23 | ~10 | A E |
| PoC persons | `"First Last" AND (company OR role)`, 5 per query | IN + GB or US by PoC location | 2 h | ~8 | A |
| Broad AI | `"artificial intelligence" OR AI OR LLM when:1h` | IN, GB, US | 60 min, 24 h | 3 | all |
| Topics | 38 topics in 8 OR-queries | IN | 2 h | 4 | B D G H I |
| Voice | `"voice agent" OR "speech model" OR TTS OR ASR OR duplex` | IN, US | 60 min | 2 | H |
| Hires | `(joins OR appointed OR named OR promoted) AND ("head of AI" OR evals OR "head of data" OR "chief scientist")` | IN, GB, US | 2 h | 1.5 | A B |
| Competitors | one query per watch name | IN, US | 2 h | ~5 | E |
| site: routes | Reuters, Entrackr, VentureBeat, AIM, AISI, GeM, Startup India, NASSCOM, IAPP, ICO, EU portal | IN or GB | 2 h to weekly | ~2 | mixed |
| Big pull | warm people, researchers, 24 h window | IN | daily 06:30 | ~15 once | A G |
| Google Alerts RSS | one alert per PoC, company, researcher (about 150) | — | Alerts update hourly; poll 60 min, spread | ~150 cheap GETs, mostly 304 | A E |
| Talkwalker Alerts RSS | same names, second copy | — | 2 h | ~75 | A E |
| Bluesky / Mastodon / Substack / GitHub per PoC | from a new `poc_feeds` table, filled by hand | — | 2 h | small | A |
| Apollo (MCP) | job changes for PoCs daily; company and competitor signals weekly | — | 07:30 / Mon 07:30 | API credits, capped | A E |

Total: 63 feeds, 38 page watches, 7 Google lanes, about 225 alert feeds, 3 APIs. About 1,200 requests a day plus the alert polls (304s). About 1,500–2,000 raw items a day before stage 2.

## 4. Fallback chain

1 · Direct feedRSS / Atom / page watchconditional GET
fail ×3 orsilent 24 h
2 · Google site:same outlet, IN editionadded automatically
429
3 · Google GB, then USother edge cachesIN lane pauses 10 min
all 429
4 · Bing News RSSsame queryolder items, still fine

5 · Cross-checkTechmeme, hnrssGDELT later
Independent of the chain: Google Alerts and Talkwalker (a different system), PR wires, lab pages, OpenAlex, Apollo.
Rule: every category A–L has at least two routes that do not share a failure. Ten sources down out of 94 does not stop the day.
A source that moves to route 2 stays listed. The health line says "VentureBeat: on Google route since 12 Oct".

Figure 3. Five routes, in order. The switch is automatic. The switch back is automatic when the feed answers again.

## 5. Limits, politeness, health

* One request per host at a time. 2 s gap per host. Google: 1.5 s. Reddit: 10 s.
* Conditional GET on every feed (`ETag`, `If-Modified-Since`). A 304 costs nothing and is not a fetch failure.
* One User-Agent: `SaleyNewsReader/3.0 (+https://membrane.social; contact news@membrane.social)`. No browser spoofing. Respect `Retry-After` and `robots.txt`.
* Back-off per source: retry once after 60 s; then sleep 1 h, 4 h, 24 h. Health, not removal.
* Per-fetch cap: 50 new items stored. First run of a deep feed (OpenAI, HF) takes its newest 50 only.
* Worker: max 4 fetches in flight. Timeout 15 s. Body cap 1.5 MB.
* Egress proxy allowlist: the 94 hosts, Google News, Bing, arXiv, OpenAlex, Semantic Scholar, Hugging Face, Apollo. Anything else is refused and logged.
* Storage: raw items 14 days; runs 30 days; registry forever. Nightly VACUUM at 03:00.
* Health line, 23:45 IST, test channel: sources due / run / 304 / new items / errors; silent list; failing list; route changes; backlog for stage 2. An immediate alert when a tier-1 source has 3 failures in a row.
* Weekly, Monday 08:00: the bot proposes new `rate_7d`, `active hours` and class per source. A human confirms in the web workspace. Nothing changes on its own.

## 6. Tables (PostgreSQL)

| Table | Columns |
| --- | --- |
| `news_sources` | id, name, kind (rss / atom / page / google / alert / api / mcp), url, lane, tier (1–4), area, categories[], trusted, active, class, poll\_active\_min, poll\_idle\_min, active\_from, active\_to, weekend\_min, depth, rate\_7d, never\_miss\_min, next\_due\_at, last\_ok\_at, last\_status, last\_error, fail\_streak, route (direct / google\_site / bing), etag, last\_modified, extractor (for pages), notes |
| `news_source_runs` | id, source\_id, started\_at, finished\_at, status (ok / 304 / error / skipped), http\_status, items\_seen, items\_new, bytes, error, route\_used |
| `news_items_raw` | id, source\_id, run\_id, url, url\_canonical, title, summary, author, published\_at, fetched\_at, lang, raw\_hash, stage2\_status (pending / stored / dropped), dropped\_reason |
| `news_page_snapshots` | source\_id, fetched\_at, file\_path (Server files), links\_hash |
| `poc_feeds` | poc\_row\_key, kind (bluesky / mastodon / substack / github / blog / alert / talkwalker), url, added\_by, added\_at, active |

Stage 2 adds `news_stories` (one row per story, with outlets[]) and the dedupe, gate and flag fields. Stage 3 adds importance, impact, tag, why. Stage 4 adds `news_sent`.

## 7. Open before build

1. Google Alerts: a Google account the bot can read. Who owns it?
2. Apollo: the "no paid APIs" line in CLAUDE.md must change, with a monthly credit cap. Yes or no, and the cap.
3. Egress proxy on the same host now, separate VM later. Agreed?
4. PostgreSQL for v2 from day one. v1 stays on SQLite until retired. Agreed?
5. Server tests for the 10 blocked sources. I give one script; you paste the output.

Prepared 16 Oct 2026. Measurements from the feeds' own items on that day. Rates will move; the registry recomputes them weekly.
