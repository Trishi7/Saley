-- Saley v2 roles and databases (docs/01 §11).
--   saley_admin : may own the schema and run migrations (set DATABASE_URL_ADMIN to use it)
--   saley       : worker, bot, news (read and write rows). DATABASE_URL in .env.example.
--   saley_ro    : web-api, SELECT only. DATABASE_URL_RO in .env.example.
--
-- Run once as a Postgres superuser, with plain psql:
--   psql -h 127.0.0.1 -U postgres -f deploy/sql/roles.sql
--
-- Passwords: the defaults below match .env.example and are for a laptop only. On a server,
-- the first step after this script is ALTER ROLE … PASSWORD for all three roles.
-- To use other passwords from the start, edit them here
-- BEFORE the first run, or change them later with
--   ALTER ROLE saley PASSWORD 'new value';
-- and put the same value in .env. Never commit real passwords.
--
-- Safe to run again: a role that exists keeps its password; grants are re-applied.

\set ON_ERROR_STOP on

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'saley_admin') THEN
    CREATE ROLE saley_admin LOGIN PASSWORD 'saley_admin';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'saley') THEN
    CREATE ROLE saley LOGIN PASSWORD 'saley';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'saley_ro') THEN
    CREATE ROLE saley_ro LOGIN PASSWORD 'saley_ro';
  END IF;
END
$$;

-- CREATE DATABASE cannot run inside a transaction or a DO block. psql sends each line
-- below as its own statement (autocommit), and \gexec runs it only when the database is new.
SELECT 'CREATE DATABASE saley OWNER saley_admin'
  WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'saley') \gexec
-- The test database. Tests drop and rebuild its schema, so it belongs to the role tests use.
SELECT 'CREATE DATABASE saley_test OWNER saley'
  WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'saley_test') \gexec

REVOKE ALL ON DATABASE saley FROM PUBLIC;
GRANT CONNECT ON DATABASE saley TO saley_admin, saley, saley_ro;
REVOKE ALL ON DATABASE saley_test FROM PUBLIC;
-- saley_ro connects only so a test can prove it cannot write.
GRANT CONNECT ON DATABASE saley_test TO saley, saley_ro;

\connect saley

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

-- R2 (migrations 0006, 0008): the web writes only through seven SECURITY DEFINER functions.
-- saley_ro gets EXECUTE on them and on nothing else. Nobody else (PUBLIC) may run them.
-- Owner: `saley`, the read-write role, is the better owner (least rights that still work).
-- A migration run by saley_admin cannot move the owner, because saley_admin is not a member
-- of saley. This script runs as superuser, so it does that step. Safe to run again.
-- After this step a migration DOWNGRADE (0006, 0008, 0009) must run as `saley`, the owner:
-- only the owner can drop or replace the functions. The migrations switch to the owner with
-- SET LOCAL ROLE when the migrating role is a member of it, else they stop with a clear text.
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
