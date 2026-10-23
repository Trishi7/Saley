# Tools for R7: graphify and ponytail

Both run on the laptop only. Nothing from them goes to the server image. Neither takes an API key.

## graphify (Graphify-Labs/graphify)
What it does: builds a code graph of the repo with tree-sitter (no LLM for code) and writes `graphify-out/graph.json`, `graph.html` and `GRAPH_REPORT.md`. Claude Code reads the report to see which modules touch which.

Install (PowerShell, repo root):
```
uv tool install graphifyy
graphify install --project
```
`graphify install --project` writes `.claude/skills/graphify/SKILL.md`. Restart Claude Code once. Then in Claude Code: `/graphify .`

Repo rules:
- `graphify-out/` goes in `.gitignore` and in the tidy by-product patterns (`scripts/tidy.py`).
- Run it at T0 and before T11. The `bot` package may have edges to `core`, `intel.guard` and the SQL doors only.

## ponytail (DietrichGebert/ponytail)
What it does: a skill that forces the smallest code that works. Its ladder: *need to exist → already in the codebase → stdlib → platform → installed dep → one line → the minimum plus one test.* Commands: `/ponytail-review` (diff review), `/ponytail-audit` (security and failure paths), `/ponytail-debt` (list over-built code), `/ponytail-gain` (what to delete), `/ponytail-help`.

Install, option A (plugin, in Claude Code):
```
/plugin marketplace add DietrichGebert/ponytail
/plugin install ponytail@ponytail
```
Option B (no plugin): copy `docs/tools/ponytail-SKILL.md` to `.claude/skills/ponytail/SKILL.md`. The commands then come from the skill text; use `/ponytail` plus the word (review, audit, debt).

Repo rules:
- `/ponytail-review` runs on every ticket diff before the ticket closes. No ECC agents in R7.
- `/ponytail-debt src/saley/bot/` once at the end of the day. Fix only gate blockers.
- The skill text is the reviewer. CLAUDE.md hard rules win over it (for example: no `.env` reads, DATA blocks to the LLM, PostgreSQL only).
