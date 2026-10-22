"""The laptop's dev database gets the server's owner model (Q-R6-4, D256).

Why: before R6 the laptop migrated as `saley`, so `saley` owns its tables and views; on the
server saley_admin owns them (docs/01 §11). `saley dev` calls `transfer_dev()` at start: it
gives every table, view and free sequence that `saley` owns to saley_admin, and grants the
row rights back to saley. After the first run there is nothing to move, so it is a one-time
step that costs one catalog query later. It logs one line either way.

It needs DATABASE_URL_ADMIN and saley_admin as a member of saley. That membership comes from
`deploy/sql/roles.sql` (grants.sql), run once as the postgres superuser; without it the line
says so and nothing changes. It never touches a `_test` database (tests own theirs).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from psycopg import sql
from sqlalchemy.engine import make_url

from saley.core.settings import Settings, is_missing

ADMIN, RW = "saley_admin", "saley"
KIND_WORD = {"r": "TABLE", "p": "TABLE", "v": "VIEW", "m": "MATERIALIZED VIEW", "S": "SEQUENCE"}
OWNED_BY_RW = """
select c.relname, c.relkind from pg_class c
where c.relnamespace = 'public'::regnamespace and c.relowner = 'saley'::regrole
  and (c.relkind in ('r', 'p', 'v', 'm')
       or (c.relkind = 'S' and not exists (
         select 1 from pg_depend d where d.objid = c.oid and d.deptype in ('a', 'i'))))
order by c.relkind, c.relname
"""
NEEDS_ROLES = "run once: psql -U postgres -f deploy/sql/roles.sql (saley_admin joins saley)"

Connect = Callable[[str], Any]


def _connect(conninfo: str) -> Any:
    import psycopg

    return psycopg.connect(conninfo, autocommit=True)


def transfer_dev(settings: Settings, connect: Connect = _connect) -> str:
    """One line for the log: what moved, or why nothing did."""
    if not settings.is_dev:
        return "skipped: not SALEY_ENV=dev"
    if is_missing(settings.database_url_admin):
        return "skipped: DATABASE_URL_ADMIN is empty (the owner model needs saley_admin)"
    url = make_url(settings.database_url_admin)
    if (url.database or "").endswith("_test"):
        return "skipped: a _test database"
    conninfo = url.set(drivername="postgresql").render_as_string(hide_password=False)
    with connect(conninfo) as conn:
        owned = conn.execute(OWNED_BY_RW).fetchall()
        if not owned:
            return "ok: saley_admin owns every table and view"
        member = conn.execute(f"select pg_has_role(current_user, '{RW}', 'member')").fetchone()
        if not member or not member[0]:
            return f"pending: {len(owned)} objects still owned by {RW}; {NEEDS_ROLES}"
        for name, kind in owned:
            conn.execute(
                sql.SQL("ALTER {} {} OWNER TO {}").format(
                    sql.SQL(KIND_WORD[kind]), sql.Identifier(name), sql.Identifier(ADMIN)
                )
            )
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO saley")
        conn.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO saley")
    return f"moved: {len(owned)} objects from {RW} to {ADMIN}"
