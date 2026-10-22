"""Owners and grants of the database against docs/01 §11 (R6, Q-R6-4, D256).

Why: the third R6 compose run failed in a migration. On the laptop one role (`saley`) had made
and owned everything, so the role rules were never tested; on the server saley_admin migrates.
This check runs after the migrations (CI row R6-G03, and `saley db-owners` on any database) and
names every object that breaks the model:

- saley_admin owns every table, view, materialized view and sequence of `public`;
- saley_admin is a member of saley (migrations replace saley's functions without `set role`);
- every SECURITY DEFINER saley_* function belongs to saley, and PUBLIC may not run it;
- saley may read and write the rows of every table;
- saley_ro reads every v_* view, reads no table except the documented ones (0005: the sheet
  snapshot tables, no personal data), and may not write any table.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

ADMIN, RW, RO = "saley_admin", "saley", "saley_ro"
RO_TABLES = frozenset({"sheet_snapshot", "sheet_tab_status"})  # 0005, deploy/sql roles

Rows = Sequence[Sequence[Any]]


def _rows(conn: Any, sql: str) -> Rows:
    return conn.execute(sql).fetchall()


def problems(conn: Any) -> list[str]:
    """Every break of the model, one plain line each. Empty means the database is right."""
    found: list[str] = []
    for name, kind, owner in _rows(
        conn,
        "select c.relname, c.relkind, c.relowner::regrole::text from pg_class c "
        "where c.relnamespace = 'public'::regnamespace and c.relkind in ('r','p','v','m','S') "
        "order by 1",
    ):
        if owner != ADMIN:
            found.append(f"{name} ({kind}) is owned by {owner}, want {ADMIN}")
    member = _rows(conn, "select pg_has_role('saley_admin', 'saley', 'member')")
    if not member or not member[0][0]:
        found.append(f"{ADMIN} is not a member of {RW}")
    for name, owner, public_x in _rows(
        conn,
        "select p.oid::regprocedure::text, p.proowner::regrole::text, "
        "  p.proacl is null or exists (select 1 from aclexplode(p.proacl) a "
        "    where a.grantee = 0 and a.privilege_type = 'EXECUTE') "
        "from pg_proc p where p.pronamespace = 'public'::regnamespace and p.prosecdef "
        "and p.proname like 'saley\\_%' order by 1",
    ):
        if owner != RW:
            found.append(f"function {name} is owned by {owner}, want {RW}")
        if public_x:
            found.append(f"function {name} may be run by PUBLIC")
    for (name,) in _rows(
        conn,
        "select c.relname from pg_class c where c.relnamespace = 'public'::regnamespace "
        "and c.relkind in ('r', 'p') "
        "and not has_table_privilege('saley', c.oid, 'SELECT,INSERT,UPDATE,DELETE') "
        "order by 1",
    ):
        found.append(f"{RW} cannot read and write the rows of {name}")
    for name, can_read, can_write in _rows(
        conn,
        "select c.relname, has_table_privilege('saley_ro', c.oid, 'SELECT'), "
        "has_table_privilege('saley_ro', c.oid, 'INSERT') "
        "or has_table_privilege('saley_ro', c.oid, 'UPDATE') "
        "or has_table_privilege('saley_ro', c.oid, 'DELETE') "
        "from pg_class c where c.relnamespace = 'public'::regnamespace "
        "and c.relkind in ('r', 'p') order by 1",
    ):
        if can_read and name not in RO_TABLES:
            found.append(f"{RO} may read the table {name} (views only, docs/01 §11)")
        if can_write:
            found.append(f"{RO} may write the table {name}")
    for (name,) in _rows(
        conn,
        "select c.relname from pg_class c where c.relnamespace = 'public'::regnamespace "
        "and c.relkind = 'v' and c.relname like 'v\\_%' "
        "and not has_table_privilege('saley_ro', c.oid, 'SELECT') order by 1",
    ):
        found.append(f"{RO} cannot read the view {name}")
    return found


def counts(conn: Any) -> dict[str, int]:
    rows = _rows(
        conn,
        "select c.relkind, count(*) from pg_class c "
        "where c.relnamespace = 'public'::regnamespace group by 1",
    )
    return {str(kind): int(n) for kind, n in rows}
