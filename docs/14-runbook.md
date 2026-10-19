# 14 · Runbook (filled by Claude Code during I-1 and later)

- [x] Laptop setup and first run (R1) — see README "Run on a laptop".
- [x] Database: create, migrate, roles, test database (R1)
  - Roles: `psql -U postgres -v admin_pw=… -v rw_pw=… -v ro_pw=… -f deploy/sql/roles.sql` (safe to re-run).
  - Migrate: `uv run saley db-upgrade` (uses `DATABASE_URL_ADMIN`, else `DATABASE_URL`). Server: `docker compose --profile migrate run --rm migrate`.
  - After a migration that adds a table with personal data: revoke SELECT on it from `saley_ro` (see migration 0003).
  - Test database: `TEST_DATABASE_URL`, else `<DATABASE_URL>_test`. Tests drop and rebuild its schema; the name must end in `_test`.
  - Check: `uv run saley health` → `db ok`, `alembic head 0003 · current 0003 · at head`, `sheet=copy test_mode=true`.
- [ ] Backup: nightly pg_dump, encrypt, copy off-server; restore test monthly
- [ ] Secrets: where each lives, rotation steps
- [ ] Health: endpoints, 23:45 health line, what red means
- [ ] Deploy to Hetzner: compose up, Caddy, egress allowlist
- [ ] Rollback
