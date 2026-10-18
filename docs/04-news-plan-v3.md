# Saley News Plan v3

Source: design page, exported 18 Oct 2026. Diagrams are Mermaid.

NFThing / membrane · Saley v2 · AI news · the plan

# News: pull, clean, score, send, watch

Stage 1, agreed 16 Oct 2026. New schedule, built on the 94 sources. Nothing from the old hourly cycle is kept. Decisions in section 10 are taken.

**In five lines.** Four pulls a day, not twenty-four. Each pull is cleaned in memory and only unique stories reach PostgreSQL. The scorer (stage 2) runs once per pull on those unique stories. Three digests and a small NOW lane carry the news to Discord (stage 3). Health tables feed your dashboard (stage 4).

## 1. The day

00040812162024 IST

E1 06:30E2 12:00E3 17:00E4 22:00
pull → clean → scorepull → clean → scorepull → clean → scorepull → clean → score

Digest 08:00Digest 13:00Digest 18:00
overnight + E1E2E3E4 → tomorrow 08:00

Watch lane: every hour 08:00–22:00, plus 02:00 and 04:00. Labs, Techmeme, Google broad, top-30 Alerts. NOW news only.

07:30 page watches · APIs10:00 arXiv · letters23:45 health

Saturday: E1 + one digest at 11:00. Sunday: watch lane only. Monday 08:00 covers the weekend.

Figure 1. Four pulls, three digests, one watch lane. Scoring happens four times a day, right after each pull.

### Why these times

| Edition | Pull at | What is in it | Goes out |
| --- | --- | --- | --- |
| E1 | 06:30 | The US day (ended 04:00 IST): TechCrunch, Verge, Bloomberg, labs, PR wires. Plus everything since E4 | Digest 08:00, before the team starts |
| E2 | 12:00 | India morning: ET, Inc42, Mint, BS, Medianama. UK morning: Sifted, UKTN, FT. Names, topics, voice, hires, competitors | Digest 13:00 |
| E3 | 17:00 | India afternoon, UK full day, EU, US early morning. All lanes | Digest 18:00, end of the India day |
| E4 | 22:00 | US morning, late India, PR wires | No digest. Scored now, shown at 08:00 as "late yesterday" |

Each edition pulls everything that changed since the previous one. A feed with 20 items of depth and 30 items a day is safe on a 5-hour gap. Two feeds are too shallow for that (Verge AI: 10 items; India Today: 20 items, very fast). They get one extra pull between editions. The registry computes this from `depth ÷ rate ÷ 2`; nobody sets it by hand.

## 2. One edition, step by step

Sources
94

Egress
proxy
allowlist
rate limit
one log

1 · Pull
conditional GET
time window first
~500 raw items

2 · Clean, in memory
a. canonical URL · in news\_seen? → already have it
b. same fingerprint in 36 h? → copy, add outlet
c. same entities + 60 % words? → copy, add outlet
d. flag by sheet name, with name\_kind
e. general feed, no AI word, no entity, no flag?
 → dropped index (kept 14 d, searchable)
~350 set aside · 1 MB of RAM

3 · Store
news\_stories: 1 row / story
news\_seen: 2 hashes / item
~45 unique stories

4 · Score
1 call, up to 60 stories
importance · impact · tag · why
all 45 scored

5 · Digest
8 lines, by tag
Discord

Dashboard
all 45 with scores
+ the dropped index

news\_seen (14 days)
url hash · fingerprint → story id
one query per batch answers "have I got this?"

Gate rules
AI-only sources skip the gate (TechCrunch AI, labs, arXiv, letters)
General feeds pass on an AI word, an AI entity, or a flag
Typical weekday numbers. A launch day is about 3× everything; the scorer then makes 3 calls instead of 1.
Nothing waits unscored for the next edition unless the per-edition cap of 6 calls is hit. Then the health table says so.
"Gemini releases new model" passes the gate twice: Gemini is an AI entity, model is an AI word.
Nothing is unfindable: stories 90 days, dropped index 14 days, live search for the rest (section 7).

Figure 2. Every pull goes through the egress proxy. The clean step runs before anything is written. PostgreSQL holds one row per story and a small index of what was set aside.

## 3. Your question: store first, or clean first?

Clean first. Store only what passes. Here is why, and what it costs.

|  | Store everything, then delete | Clean in memory, store unique (chosen) |
| --- | --- | --- |
| Rows written per day | About 2,000, then 1,800 deleted | About 180 stories + 4,000 tiny hash rows |
| Duplicate rows in the table | Yes, until the delete job runs | Never |
| What the scorer reads | Must filter out copies and junk every time. This is today's bug | Reads `news_stories WHERE scored = false`. Nothing else |
| What can go wrong | The delete job fails or lags. Copies get scored and sent | A good item is dropped by a wrong rule. The counters show it. Debug mode keeps raw items for 48 h to find it |
| Evidence of what was dropped | The rows, until deleted | Counters per source per edition: seen, new, dropped-seen, dropped-gate, dropped-copy. Plus the debug log when on |

Memory across pulls comes from one small table, `news_seen`: the URL hash and the title fingerprint of every item we have looked at in the last 14 days, pointing to the story it belongs to. 60 bytes a row. That is how the second outlet's copy of a story is recognised an hour later and added to the first row as an outlet, not stored again.

### The questions, answered

| Question | Answer |
| --- | --- |
| Can we filter by time at pull instead of the URL check? | We do both. Conditional GET and Google's `when:` cut volume at the source. The URL check is still needed: some feeds have no item dates (Mint, Hindu BusinessLine), some items appear hours after their stated time, and a second outlet's copy is new in time but not in content. Time alone would miss or double those. The check is one indexed query per batch. |
| Does the AI-word gate miss "Gemini releases a new model"? | No. The gate is skipped for AI-only sources. On general feeds it passes any item with an AI word, an AI entity (Gemini, GPT, Claude, Llama, Mistral, DeepSeek, Qwen, the labs, the sheet names) or a flag. That headline passes on "Gemini" and on "model". An item that still fails is not deleted: it goes to the dropped index for 14 days, searchable. |
| What is a fingerprint? | The title, lower-cased, punctuation and small words removed, the first 12 real words sorted, then hashed. "Google launches Gemini 4 Aragon with 2M context" and "Gemini 4 Aragon launched by Google: 2M context window" give the same words, so the same fingerprint, so one story with two outlets. "Gemini 4 fails safety test" gives different words, so a new story. |
| How does the next batch know a story is already stored? | It asks `news_seen` with all the batch's URL hashes and fingerprints in one query. A URL match means "have it". A fingerprint match means "copy of story X": the outlet is added to X. 14 days of memory covers every realistic repeat. |
| Will cleaning in memory use too much RAM? | No. One edition is about 500 items of about 2 KB each: 1 MB. The worker handles one source at a time, so it holds a few hundred KB. A staging table would add about 2,000 writes and 1,800 deletes a day for no gain. In debug mode a raw log is kept for 48 h. |
| How do PoC and competitor stories come in? | Six routes: the Google names lane, per-PoC Google queries, Google Alerts and Talkwalker per name, PR wires, page watches of competitor blogs, Apollo job changes when allowed. In the clean step every item is matched against the sheet names with its kind (person, company, researcher, watch). A match sets the flag and skips the gate. The name list is rebuilt from the sheet at 06:00 every day. |
| Where is the proxy? | Every pull goes worker → egress proxy → source. It holds the host allowlist, the per-host rate limit and one log. Caddy stays for inbound traffic to the dashboard. |

### Cleanup that still runs, nightly at 03:00

* `news_seen` rows older than 14 days: deleted.
* `news_stories` with no score after 3 editions and no flag: deleted. (This only happens if the scorer was over budget three times.)
* `news_stories` older than 90 days: deleted, unless sent (kept 1 year for the dashboard's history).
* Debug raw log older than 48 h: deleted.
* VACUUM.

## 4. Not getting blocked

| Rule | Value |
| --- | --- |
| Requests per host | One at a time. 2 s apart. Google 1.5 s. Reddit 10 s |
| Per host, per day | Cap 100. Google per edition cap 60, then the lane waits for the next edition |
| Conditional GET | Always. A 304 is free for them and for us |
| User-Agent | One honest string with a contact address. No browser spoofing |
| Spread | Sources in an edition are spread over 20 minutes by a hash offset. No burst |
| Back-off | Error: retry once after 60 s. Then sleep 1 h, 4 h, 24 h. 429 pauses the host for the edition and honours `Retry-After` |
| Fallback | Feed down → Google `site:` query for that outlet. Google IN 429 → GB → US → Bing. Automatic, and back again when the feed answers |
| Egress | Everything through one proxy container with a host allowlist. One place to see every request |

Total: about 1,200 real requests a day plus about 900 alert polls that mostly return 304. Google sees about 45 requests per edition, 1.5 s apart, across three editions. That is a tenth of where 429 starts.

## 5. The scorer: stage 2

The scorer is not part of stage 1. Stage 2 decides the model, the rubric and the fields. Stage 1 only fixes the contract between them:

* The scorer reads `news_stories WHERE scored = false` for one edition. Nothing else.
* It runs once per edition, right after the clean step. The watch lane may call it for 1–5 candidates an hour, only when a code test passes.
* It writes back to the same row. The columns are reserved now: `importance`, `impact`, `tag`, `why`, `confidence`, `scored_at`, `scorer_backend`.
* The backend is a setting: `SCORER_BACKEND`. Stage 1 wires the existing scorer behind it so the pipeline runs end to end. Stage 2 can replace it (LLM, Jev, or both) without touching ingestion.
* Questions in Discord never trigger the scorer. They read stored scores.

## 6. Sending it out, so nothing is missed

| Lane | When | What goes in | Shape |
| --- | --- | --- | --- |
| Digest | 08:00, 13:00, 18:00 weekdays. 11:00 Saturday | Every story scored since the last digest with impact ≥ 1, or ours, or importance ≥ 4. Ranked: flagged, impact, importance, outlets, newest. Grouped by tag in the order Opportunity, People, Risk, Discover, Market | 8 lines. Line = headline, outlet, "+N outlets", [Tag]. A why-line under impact ≥ 2. Tail: "+N more on the dashboard" |
| NOW | Any hour 08:00–22:00 | Impact 3; or ours with importance ≥ 4; or industry 5 with 2+ outlets. Not already in a digest | One line + why + dashboard link. At most 1 per 2 h, 4 a day |
| Dashboard | Always | Every scored story | Filters by tag, ours, day, source, score |
| On demand | Any time, any day | "@Saley news" = last 24 h, top 5 by rank, repeats allowed. Any window, topic, person, company, tag | 5 per page. "There are N more. Want the rest?" |

Nothing is missed because every unique story is scored and every scored story is on the dashboard. The digest shows the top 8 of each third of the day. The NOW lane covers the few that cannot wait. The 24-hour question shows the top 5 again, including what was already posted.

### Any question, any window

A person can ask Saley for any kind of news. The answer is built in this order, and stops at the first step that has results.

1. `news_stories`, 90 days. Every scored story. Filtered by the window, topic, person, company or tag the person named. Ranked by the rank key. "This topic since 48 days" is answered here.
2. The dropped index, 14 days. Items a general feed carried that failed the gate. Shown as "not scored, found in the feed log".
3. A live search through the search backend (Google News, then Bing), for anything older than 90 days or not in the store. Shown as "from a live search", not scored.
4. Nothing anywhere → "I have nothing on that. Want me to add it to the watch list?"

* A question never triggers the scorer. Lists come from stored scores or from the live search as found.
* A question outside sales and AI news → Saley says what it covers and offers one live search.
* A posted story is never hidden from an answer. Repeats are allowed in answers.
* Default window when none is named: 24 hours. Top 5. "There are N more. Want the rest?"

## 7. The health dashboard

Your Saley dashboard will show many things. News health is one module. The module reads three tables and one view. No logs, no files.

RAW ITEMS TODAY1,980
UNIQUE STORIES184
DUPLICATES REMOVED1,120
SCORED / UNSCORED184 / 0
SENT IN DIGESTS24
NOW POSTS2 / 4

Funnel, today

raw 1,980
not seen before 860
passed gate 540
unique stories 184
scored 184
sent 26 (24 digest + 2 NOW)

Scores, today

importance
1: 40
2: 61
3: 55
4: 22
5: 6
impact
0: 118
1: 41
2: 19
3: 6
tags
Opportunity 9 · People 7 · Risk 5 · Discover 31 · Market 132
scorer calls5 of 24 allowed · 71k tokens · 0 carried
by category A–LA 12 · B 14 · C 2 · D 18 · E 6 · F 61 · G 29 · H 11 · I 9 · J 8 · K 3 · L 11

Sources (94) — sort by status

sourceitemsnewdroppedkeptlast okroutestatus
ET Tech1042618817:02directok
VentureBeat AI000012 Octgoogle site:fallback
Analytics India Mag0000—google site:silent 3 d
Google names lane6121401211917:04INok
Scale AI blog (page)110107:41pageok
… 89 more

Top stories today · alerts

5/3 Opportunity · MeitY Indic voice tender · NOW 11:20
4/3 People · J. Doe → Head of Evals, Acme · digest 13:00
5/0 Market · Gemini 4 Aragon · NOW 09:40 · 12 outlets
4/2 Risk · Scale AI Hinglish team · digest 18:00
⚠ AIM silent 3 days · Google IN 429 ×1 (E2, recovered on GB)
⚠ Reddit r/ML 429 — paused 24 h
Scorer: 0 carried · caps hit: none

Figure 3. The news health module as one screen. Six tiles, the funnel, the scores, the sources, the top stories, the alerts. Every number comes from a table or a view below.

### Metrics, and where each one comes from

| Metric | Source | Why it matters |
| --- | --- | --- |
| Raw items per source per edition | `news_source_runs.items_seen` | Shows which sources carry volume, and a feed that went quiet |
| New (not seen before) per source | `news_source_runs.items_new` | Volume after the URL check |
| Dropped: seen / gate / copy, per source | `news_source_runs.dropped_seen`, `dropped_gate`, `dropped_copy` | The duplicate count you asked for. Also shows a source that is all noise |
| Kept (unique stories) per source | `news_stories` grouped by `first_source_id` | Who actually gives us stories first |
| Outlet count per story | `news_stories.outlet_count` | How many copies one story had. Corroboration |
| Totals per day: raw, unique, duplicates, scored, unscored | view `v_news_day` over the two tables above | The six tiles |
| Importance and impact distribution, tag counts, category counts | `news_stories` grouped | Is the scorer calibrated? Too many 5s, too few Opportunities |
| Scorer calls, tokens, carried-over count, per edition | `news_editions` | Cost and backlog |
| Sent: digest lines, NOW posts, on-demand answers, per day | `news_sent` (story\_id, lane, sent\_at, message\_id) | What reached the team, and how much |
| Top stories with score and lane | join `news_stories` × `news_sent` | "Which were important and had to be looked at" |
| Important but not sent | `news_stories` with impact ≥ 2 and no `news_sent` row | The miss detector. Should be near zero |
| Source status: ok / fallback / failing / silent | `news_sources.last_status`, `route`, `fail_streak`, `last_ok_at` | Health per source |
| Caps hit: per host, Google per edition, scorer per edition, NOW per day | `news_editions.caps_hit[]` | Did a limit cost us anything today |
| Rate and active hours per source, 7-day | `news_sources.rate_7d`, `active_from`, `active_to` | Tunes the schedule. Proposed changes shown for approval |
| Engagement, later | Discord reactions and link clicks via the dashboard | Which tags people open. Feeds the scorer examples |

### Filters and search

| Control | Values | Backed by |
| --- | --- | --- |
| Tag | Opportunity, People, Risk, Discover, Market | `news_stories.tag` |
| Importance | 1–5, range | `news_stories.importance` |
| Impact | 0–3, range | `news_stories.impact` |
| Category | A–L | `news_stories.categories[]` |
| Day and hour | Date range, hour range (IST), edition E1–E4 | `news_stories.published_at`, `first_seen_at`, `edition_id` |
| Source | Any of the 94, or a lane | `news_stories.first_source_id`, `outlets[]` |
| Ours | Flagged only; by name kind: person, company, researcher, watch | `news_stories.flag`, `name_kind`, `names` |
| Lane | Digest, NOW, on demand, not sent | `news_sent.lane`, or no row |
| Status | Scored, unscored, carried, dropped (with reason), copy of another story | `news_stories.scored`, `news_dropped.reason`, `news_seen.story_id` |
| Search | Free text over title, why, names, outlets. Covers stories (90 days) and the dropped index (14 days) | PostgreSQL full-text index on both tables |

A search result opens the story's detail: title and best link; every outlet with its URL and time; first source and edition; importance, impact, tag, why; flag, names and name kind; where it was sent, when, with the Discord message link; or, for a dropped item, the reason and the feed it came from. A copy shows "same as story X" with a link.

### How it plugs into your main Saley dashboard

* News writes only tables. It does not render anything. Your dashboard reads them. The same pattern can hold for R13, R14, approvals and reminders: each module owns tables, the dashboard owns screens.
* One read-only PostgreSQL role for the dashboard, with access to the news tables and views only.
* Four views do the heavy lifting: `v_news_day` (tiles), `v_news_source_health` (source table), `v_news_scores_day` (charts), `v_news_top_today` (stories and lanes). The dashboard never joins raw tables.
* Every story row has a stable id and a Discord message id when sent. The dashboard can link from a tile to the exact message.
* Actions from the dashboard (approve "add to pipeline", snooze a source, change a poll time) go through the same HITL proposal path as Discord. The dashboard never writes to news tables directly.
* A JSON endpoint per view (`/api/news/day`, `/api/news/sources`, …) if the dashboard is a separate app. Same data, no second source of truth.

## 8. Tables, final

| Table | Holds | Kept |
| --- | --- | --- |
| `news_sources` | The 94 sources, their clock, route, health | Forever |
| `news_editions` | One row per edition: times, counts, scorer calls, tokens, carried, caps hit | 1 year |
| `news_source_runs` | One row per source per edition: counters, status, route used | 90 days |
| `news_seen` | URL hash and fingerprint of every item looked at, pointing to its story | 14 days |
| `news_stories` | One row per unique story: title, best URL, outlets[], first source, flag, name\_kind, edition, scored, importance, impact, tag, why | 90 days; sent ones 1 year |
| `news_sent` | Story, lane, time, Discord message id | 1 year |
| `news_dropped` | Title, URL, source, reason, seen\_at for items a general feed carried that failed the gate. Searchable | 14 days |
| `news_raw_debug` | Raw items, only when `NEWS_RAW_LOG=true` | 48 h |
| `poc_feeds` | Personal feeds per PoC (Bluesky, Substack, GitHub, alert) | Forever |

## 9. Stage 1 scope

Stage 1 builds: the source registry with the 94 sources and their clocks, the egress proxy, the four-edition scheduler, the pull with conditional GET and time windows, the clean step with `news_seen`, the gate rules, the dropped index, the flag with name kinds, the page-watch extractor, the health tables and views, the 23:45 health line, and the `SCORER_BACKEND` plug with the existing scorer behind it so the pipeline runs end to end.

Stage 2: the scorer (model, rubric, fields, overrides, rank key). Stage 3: digests, NOW lane, on-demand answers. Stage 4: the dashboard module. Until stage 3, the existing daily post reads `news_stories`.

## 10. Decisions, taken 16 Oct

1. Editions at 06:30 / 12:00 / 17:00 / 22:00. Digests at 08:00 / 13:00 / 18:00. Agreed.
2. Saturday one digest at 11:00. Sunday none. Agreed.
3. NOW cap 4 a day, 1 per 2 hours. Agreed.
4. Debug raw log off in production. Agreed.
5. Dashboard reads views through a read-only role, with a JSON endpoint per view for the main Saley dashboard. Agreed.
6. Clean in memory, store unique, keep a dropped index. Agreed.
7. Gate skipped for AI-only sources; general feeds pass on AI word, AI entity or flag. Agreed.

Still open for stage 1: the Google account for Alerts; the Apollo rule change and its cap. The scorer model is a stage 2 decision.

Prepared 16 Oct 2026. Numbers are from the 16 Oct feed measurements and the 14 Oct cycle rows. They will move; the registry recomputes rates weekly.
