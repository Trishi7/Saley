# Decisions

Every answer from the human that changes behaviour, data or architecture goes here. Claude Code appends. Humans confirm. Format: one row per decision.

| Id | Date | By | Decision | Affects docs | Docs updated |
| --- | --- | --- | --- | --- | --- |
| D1–D16 | 2026-10-18 | Trishi | See docs/02-requirements.md Part I | 02 | yes |
| D17 | 2026-10-18 | Trishi | PostgreSQL only. No SQLite anywhere, tests included. | 01, CLAUDE.md | yes |
| D18 | 2026-10-18 | Trishi | Local tests use a copy of the GTM Playbook. | CLAUDE.md, 11 | yes |
| D19 | 2026-10-18 | Trishi | Email sending stays off. Code exists. Drafts only. | 09, 12, CLAUDE.md | yes |
| D20 | 2026-10-18 | Trishi | Apollo MCP not used. Apollo pipeline starts Wed 22 Oct. | 02 Part E, 01 | yes |
| D21 | 2026-10-18 | Trishi | Weekly pack Mon 14:00 IST. | 02, 09 | yes |
| D22 | 2026-10-18 | Trishi | Per-user seen on every bot answer. | 02, 09, 01 | yes |
| D23 | 2026-10-18 | Trishi | Dashboard is the work surface for Sid and Vaishnavi. Saley is the only writer of the sheet. | 08, 11, CLAUDE.md | yes |
| D24 | 2026-10-18 | Trishi | An instruction from an approver is the approval. One-click cards for Saley's ideas. No second yes, no double ask across chat and dashboard. Undo window 10 min (proposed value). | 08, 09, 02 C04, CLAUDE.md | yes |
| D25 | 2026-10-18 | proposed | Owner rule for tasks: First Contact value when set; new people: Sid = LinkedIn, labs, calls; Vaishnavi = research, lists, emails. | 08 | awaiting confirm |
| D26 | 2026-10-18 | Trishi | Build in releases R1–R4. Claude Code pings on Discord only when a release is built, tested and verified, or when blocked. | CLAUDE.md, STATUS | yes |
| D27 | 2026-10-18 | Trishi | Bot message shapes are open. Build only the test loop, @Saley ping and the release ping until decided. | 09 | yes |
