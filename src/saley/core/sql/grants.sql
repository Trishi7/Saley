-- Saley v2 grants inside the saley database (docs/01 §11). One source for two callers:
--   laptop: deploy/sql/roles.sql includes this file with \ir after it connects to saley;
--   server: `saley db-roles` runs it as the postgres superuser on every deploy (D244).
-- Plain SQL only (no psql commands), safe to run again.
-- The owner model (docs/01 §11, Q-R6-4, D256):
--   saley_admin runs the migrations and owns every table, view and sequence of public;
--   saley owns the SECURITY DEFINER saley_* functions (moved below) and gets row rights;
--   saley_admin is a member of saley, so a migration may replace those functions without
--   `set role` (no migration switches role any more).
GRANT saley TO saley_admin;

-- Objects a migration made as `saley` (a laptop before R6) go to saley_admin. Their ACLs move
-- with them; the row rights of saley are granted again below.
DO $$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT c.oid::regclass AS name, c.relkind FROM pg_class c
    WHERE c.relnamespace = 'public'::regnamespace AND c.relowner = 'saley'::regrole
      AND c.relkind IN ('r', 'p', 'v', 'm')
  LOOP
    EXECUTE format('ALTER %s %s OWNER TO saley_admin',
      CASE r.relkind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' ELSE 'TABLE' END,
      r.name);
  END LOOP;
  -- Sequences that belong to no column (a column's sequence moves with its table).
  FOR r IN
    SELECT c.oid::regclass AS name FROM pg_class c
    WHERE c.relnamespace = 'public'::regnamespace AND c.relowner = 'saley'::regrole
      AND c.relkind = 'S'
      AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype IN ('a', 'i'))
  LOOP
    EXECUTE format('ALTER SEQUENCE %s OWNER TO saley_admin', r.name);
  END LOOP;
END
$$;

-- Migrations run as saley_admin when DATABASE_URL_ADMIN is set, else as saley.
GRANT USAGE, CREATE ON SCHEMA public TO saley_admin, saley;
GRANT USAGE ON SCHEMA public TO saley_ro;
REVOKE CREATE ON SCHEMA public FROM saley_ro;

-- Objects that exist now.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO saley;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO saley;

-- Objects that migrations create later, whichever of the two roles runs them.
ALTER DEFAULT PRIVILEGES FOR ROLE saley_admin IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO saley;
ALTER DEFAULT PRIVILEGES FOR ROLE saley_admin IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO saley;
-- saley_ro gets no default grant: it reads only the v_* views and the two snapshot
-- tables, granted by name in the migrations (0005). Remove the old defaults if present.
ALTER DEFAULT PRIVILEGES FOR ROLE saley_admin IN SCHEMA public
  REVOKE SELECT ON TABLES FROM saley_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE saley IN SCHEMA public
  REVOKE SELECT ON TABLES FROM saley_ro;

-- The web reads only views (migration 0005). On a database that is already migrated,
-- this repeats the 0005 grants: no table, every v_* view, the two snapshot tables.
DO $$
DECLARE
  v text;
BEGIN
  REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM saley_ro;
  FOR v IN SELECT viewname FROM pg_views WHERE schemaname = 'public' AND viewname LIKE 'v\_%' LOOP
    EXECUTE format('GRANT SELECT ON %I TO saley_ro', v);
  END LOOP;
  IF to_regclass('public.sheet_snapshot') IS NOT NULL THEN
    GRANT SELECT ON sheet_snapshot, sheet_tab_status TO saley_ro;
  END IF;
END
$$;

-- Every SECURITY DEFINER saley_* function belongs to `saley`, the read-write role (least rights
-- that still work), and PUBLIC may not run it (Q-R6-4: also the trigger function of 0016).
-- saley_admin is a member of saley, so migrations replace or drop them without `set role`.
DO $$
DECLARE
  f text;
BEGIN
  FOR f IN
    SELECT p.oid::regprocedure::text FROM pg_proc p
    WHERE p.pronamespace = 'public'::regnamespace AND p.prosecdef AND p.proname LIKE 'saley\_%'
  LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO saley', f);
    EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f);
  END LOOP;
END
$$;

-- R2 (migrations 0006, 0008): the web writes only through these SECURITY DEFINER functions.
-- saley_ro gets EXECUTE on them (the migrations of the later ones grant theirs).
DO $$
DECLARE
  f text;
BEGIN
  FOR f IN
    SELECT p.oid::regprocedure::text FROM pg_proc p
    WHERE p.pronamespace = 'public'::regnamespace
      AND p.proname IN ('saley_act', 'saley_undo', 'saley_confirm', 'saley_dismiss',
                    'saley_commitment_done', 'saley_mark_seen', 'saley_request_search',
                    'saley_proposal_resolve', 'saley_undo_group',
                    'saley_request_news_run')
  LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO saley', f);
    EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f);
    EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO saley_ro', f);
  END LOOP;
END
$$;

-- Defence in depth only: what stops writes is that saley_ro has no write grants.
ALTER ROLE saley_ro SET default_transaction_read_only = on;
