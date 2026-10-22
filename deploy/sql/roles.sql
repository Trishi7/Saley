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

-- The grants live in the package, so the server command `saley db-roles` runs the same text.
\ir ../../src/saley/core/sql/grants.sql
