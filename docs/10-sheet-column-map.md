# 10 · Sheet column map

Source: live GTM Playbook headers (16 Oct 2026) and v1 role aliases (`gtm_sheet.py`). Locks are BY ROLE. Letters are today's layout only. The resolver matches headers to roles by alias list, case-insensitive.

## Outreach PoCs (37 columns today)

| Letter | Header | Role | Block | Existing row | New row |
| --- | --- | --- | --- | --- | --- |
| A | Sr No | sr_no | identity | read | write |
| B | Company/Uni | company | identity | read | write |
| C | Name | name | identity | read | write (+ signature note) |
| D | About PoC | about_poc | identity | read | write |
| E | Industry | industry | identity | read | write |
| F | Designation | designation | identity | read | write |
| G | Email id | email | identity | `email_write` only, if blank, behind EMAIL_WRITE_ALLOWED | write |
| H | Based (Sept 2026) | based | identity | read | write |
| I | Research Paper Link | paper_links | identity | read | write |
| J | LI Url | li_url | identity | read | write only if search returned linkedin.com/in |
| K | First Contact | first_contact | activation | **write** | write |
| L | First Contact Type | first_contact_type | activation | **write** | write |
| M | First Contact Date | first_contact_date | activation | **write** | write |
| N | Sid - LI Addition | sid_li_added | activation | **write** | write |
| O | LI Connected Date | li_connected_date | activation | **write** | write |
| P | Next Steps (dropdown) | outreach_step | step | read | — |
| Q | Connected with VR on LI | connected_with_vr | identity (new, 16 Oct) | read | — |
| R | Application Bucket | application_bucket | identity | read | — |
| S | 1st Email Sent | email_1_sent | step | read | — |
| T | 1st Email Date | email_1_date | step | read | — |
| U | 2nd Email Sent | email_2_sent | step | read | — |
| V | 2nd Email Date | email_2_date | step | read | — |
| W | 3rd Email Sent | email_3_sent | step | read | — |
| X | 3rd Email Date | email_3_date | step | read | — |
| Y | LI DM Sent | li_dm_sent | step | read | — |
| Z | LI DM Date | li_dm_date | step | read | — |
| AA | Meeting Date | meeting_date | activation | **write** | — |
| AB | Meeting Status | meeting_status | activation | **write** | — |
| AC | Reconnect Date | reconnect_date | always read-only | read | — |
| AD | Notes/Remarks | next_steps (notes) | commercial | read | — |
| AE | Package | package | commercial | read | — |
| AF | Status | prospect_status | commercial | read | — |
| AG | Closure Prob% | closure_prob | commercial | read | — |
| AH | Estd. Deal Size (USD) | deal_size | commercial | read | — |
| AI | Deal Status | deal_status | commercial | read | — |
| AJ | Priority | poc_priority | commercial | read | — |
| AK | X handle (or website) | x_handle | identity | read | write |

Planned columns after decision D6 (Saley may write, human can override): `Stage`, `Motion`, `Goal`. They are added by a human. Saley never adds a column.

Rules:
- A restricted role that maps to no column stops every write to the tab until it maps again.
- A required header that maps to no column stops that tab's sync; other tabs go on. The tab keeps its last good data and shows as stale on Sources and health (D41).
- Estd. Deal Size (USD) may be a band ("USD 30–50k"). The deal value is its midpoint (40k); an exact value counts as given (D42).
- Rule R13 never writes the step block. It asks people to update it.
- Meeting Status values: booked, completed, no-show, rescheduled, TBD. Normalise before compare.
- Dates arrive as real dates or text like `02.09.2026`. Parse both. Flag the text ones in data quality.

## Writes (R2)
- Saley writes a cell only from an approved instruction (D24). The worker pushes it to the sheet a few seconds later.
- Each write adds one line to the Name cell (column C), in this form (D47):
  `Saley · <column> = <value> · <user> · <surface> · <day time IST> · d<id>`
  Example: `Saley · Meeting Date = 3 Nov 2026 · Sid · dashboard · Mon 19 Oct 14:05 IST · d42`
- An undo adds: `Saley · undone d<id> · <user> · <surface> · <time> IST`.
- The Name cell keeps the newest 10 Saley lines. Human text stays.
- Formula rule: a value that starts with `=`, `+`, `-` or `@` is refused. Google would run it as a formula. The web, the SQL function and the worker all refuse it, including on undo (H1, D69).

## Other tabs (read-only)
| Tab | Key | Columns used |
| --- | --- | --- |
| Master Pipeline | Company | Industry, Geography, Approx. Funding, Outreach Line - Researchers |
| Sept2026_Sales Strategy | Sr No. | Strategy Name, Description, ICP Type, Target ICPs/month, Target Orgs/month, Collaterals, Are all ready?, What's pending?, Timeline, Success Metrics |
| Q4-OND2026-Goal Setting | row no. | Goal Setting, Estd. Value, Success Metric, Q4 Target, Accountable, Dependencies, Total Leads (Saley fills), Status (Saley fills), Due Date |
| Deliverables Checklist | Sr No | Action Item, Functional Dependency, Priority, Tentative Deadline, Timelines, Link, Status, Remarks, Reminder Freq |
| Sales Packages | Package | Name, Purpose, Use Case, Size, Audio, Image, JSONL, Pulse Doc, % Completion, Ready?, Status |
| AI Events & Summits | Sr No | Location, Event Name, Link, Date, Timings, Key People, Last day for registration, Registered?, Attended? |
| New AI Verticals_PoC List | Sr. No. | Company, Industry, PoC Name, Designation, Role in Deal, Evidence, LinkedIn url |
| Sales Funnel - March-June 2026 | — | baseline rates only |

Row key for sync: `sheet_row_key = tab + ":" + Sr No + ":" + normalised Name`. A changed Sr No with the same Name and Company is the same person.
