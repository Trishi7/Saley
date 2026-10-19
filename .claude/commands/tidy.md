---
description: Repo hygiene. Sorts untracked files into keep / by-product / scratch, deletes caches only, moves scratch files to .scratch/ after a human yes, and writes a dead-code report. Safe to run again.
argument-hint: (none) | --check
---

# /tidy — repo hygiene

Run it before a release chain starts, and whenever the tree looks messy. Shell:
`export PATH="$HOME/.local/bin:$PATH"`. Work on main. Never commit.

## Safety rules (the script enforces them too)
- Never delete or move a file that is tracked by git, imported by code, or named in a config
  (`pyproject.toml`, `alembic.ini`, `.claude/`, `docs/`).
- Never open, move or delete key material (`.env`, `*-write.json`, service-account files, keys).
  Such files are listed by name only.
- Deletes without asking: caches only (`__pycache__`, `.pytest_cache`, `.ruff_cache`,
  `.mypy_cache`, `.hypothesis`, `htmlcov`, `.coverage*`). Nothing else is deleted, ever.
- Everything else: move to `.scratch/` after the human says "yes". Moves only, no deletes.
- Tests run after every move. A failure moves the files back at once.

## Procedure
a–b, e. `uv run python scripts/tidy.py`
   - a. Reads `git status --porcelain` and prints three lists: **keep** (belongs in git),
     **by-product** (matches `.gitignore` now), **scratch** (one-off: `debug_*`, `tmp_*`,
     `*_output.*`, notebooks, ad-hoc scripts outside the code folders). A fourth list,
     **protected**, shows files that look one-off but are never moved, with the reason.
   - b. Deletes the caches above. Prints what it deleted.
   - e. Runs agent `refactor-cleaner` in **report-only** mode:
     `ruff check --select F401,F841` and `vulture src/saley tests --min-confidence 80`.
     Writes `docs/test-reports/tidy-<date>.md`. Removes nothing: removal is a normal ticket
     later, with its own `/verify`. (The agent file says "commit after each batch"; here
     nothing is removed and nothing is committed.)
   - Saves the scratch list to `.scratch/tidy-plan.json`.
c. Show the lists and the report to the human. **Stop. Wait for "yes".**
d. On "yes": `uv run python scripts/tidy.py --move --yes`
   - Moves each scratch file to `.scratch/<same path>` (checks the safety rules again first).
   - Runs `py_compile` on all files, `ruff check`, `pytest -q`.
   - Any failure: moves the files back and prints `tidy: reverted (<reason>)`.
     Else prints `tidy: nothing broke`. The moves are in `.scratch/tidy-moves.json`.
f. `uv run python scripts/tidy.py --check` changes nothing. It exits non-zero and lists every
   untracked file outside the keep patterns. `/verify` step 1 runs it first.

## Keep patterns
`src/`, `tests/`, `scripts/`, `migrations/`, `deploy/`, `docs/`, `.claude/`, `.github/`, and the
root files `pyproject.toml`, `uv.lock`, `alembic.ini`, `Dockerfile`, `docker-compose.yml`,
`.dockerignore`, `.python-version`, `.gitignore`, `.env.example`, `README.md`, `CLAUDE.md`.
A one-off name (`debug_*`, `tmp_*`, `*_output.*`, `*.ipynb`) is scratch even inside them.
Temporary files go in `.scratch/` or `docs/test-reports/<release>-screens/` (both ignored).
