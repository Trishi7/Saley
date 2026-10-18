# Saley v2

Sales lead and chief-of-staff system for Membrane's sales team. Dashboard first, Discord bot for pings and chat, worker for rules and approved writes, news module for signals. One PostgreSQL.

- Design: `docs/` (start at `docs/00-index.md`).
- Rules for Claude Code and humans: `CLAUDE.md`.
- Status: `docs/STATUS.md`.

## Run on a laptop
1. Install Python 3.12, Docker, and `uv`.
2. Copy `.env.example` to `.env`. Fill the `<REQUIRED>` values.
3. `docker compose up -d postgres`
4. `uv sync` then `uv run alembic upgrade head`
5. `uv run saley web` · `uv run saley bot` · `uv run saley worker` · `uv run saley news`
6. `uv run pytest -q`

(Steps 4–6 exist after ticket P0.)

## Claude Code setup (once per laptop)
1. `npx @vectorize-io/hindsight-coding-agents install claude-code --server daemon` — project memory across sessions.
2. Set `SALEY_DEV_WEBHOOK` in your shell profile for Discord pings from `.claude/hooks/notify.py`.
3. In a session: `/remote-control` to answer from your phone.
4. Agents, skills, commands and rules in `.claude/` come from affaan-m/ECC (14 picked) plus `saley-tester`.
