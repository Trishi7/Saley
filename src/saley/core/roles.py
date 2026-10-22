"""`saley db-roles`: the three database roles, the database and the grants on the server (D244).

Why: the server keeps every password in /srv/saley/.env, inside the three database URLs. This
command reads them there and makes Postgres match: it creates a missing role, sets the password
of an existing one, creates the database when it is missing, and runs the grants. It runs on
every deploy, so a new password in `.env` reaches Postgres with the next deploy (runbook:
"rotate a secret"). It connects as the `postgres` superuser with POSTGRES_PASSWORD, to the host
of DATABASE_URL.

The owner model (docs/01 §11, D256) is in `sql/grants.sql`: saley_admin owns the tables and
views and is a member of `saley`, which owns the saley_* functions. No migration switches role.

The grants are in `sql/grants.sql`, the same file deploy/sql/roles.sql includes on a laptop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import sql
from sqlalchemy.engine import URL, make_url

from saley.core.settings import CoreSettings, Settings, is_missing

GRANTS_SQL = Path(__file__).with_name("sql") / "grants.sql"
SUPERUSER = "postgres"
ADMIN, RW, RO = "saley_admin", "saley", "saley_ro"
# (role, settings field). The URL of each field must log in as its role.
ROLE_URLS = ((ADMIN, "database_url_admin"), (RW, "database_url"), (RO, "database_url_ro"))


class RolesRefused(RuntimeError):
    """A missing or wrong value in .env. The text names the variable, never its value."""


@dataclass(frozen=True)
class Login:
    role: str
    password: str


def logins(settings: CoreSettings | Settings) -> tuple[list[Login], URL]:
    """The three logins from the .env URLs, and the URL of the database they share."""
    found: list[Login] = []
    target: URL | None = None
    for role, field in ROLE_URLS:
        raw = getattr(settings, field)
        if is_missing(raw):
            raise RolesRefused(f"{field.upper()} is empty. The server needs all three URLs.")
        url = make_url(raw)
        if url.username != role:
            raise RolesRefused(f"{field.upper()} must log in as {role}.")
        if not url.password:
            raise RolesRefused(f"{field.upper()} has no password.")
        if target is not None and (url.host, url.port, url.database) != (
            target.host,
            target.port,
            target.database,
        ):
            raise RolesRefused(f"{field.upper()} names another server or database.")
        target = target or url
        found.append(Login(role, str(url.password)))
    if target is None:  # pragma: no cover - ROLE_URLS is not empty
        raise RolesRefused("no database URL")
    return found, target


def superuser_conninfo(settings: CoreSettings | Settings, target: URL, database: str) -> str:
    if is_missing(settings.postgres_password):
        raise RolesRefused("POSTGRES_PASSWORD is empty.")
    url = target.set(
        drivername="postgresql",
        username=SUPERUSER,
        password=settings.postgres_password,
        database=database,
    )
    return url.render_as_string(hide_password=False)


def role_statements(login: Login, exists: bool, hashed: str) -> sql.Composed:
    """`hashed` is the SCRAM verifier, never the password: a failed statement lands in the
    Postgres log with its text (security review M2)."""
    verb = sql.SQL("ALTER ROLE") if exists else sql.SQL("CREATE ROLE")
    return sql.SQL("{} {} WITH LOGIN PASSWORD {}").format(
        verb, sql.Identifier(login.role), sql.Literal(hashed)
    )


def scram(conn: Any, login: Login) -> str:
    """The scram-sha-256 verifier of the password, made on this side (libpq)."""
    raw = conn.pgconn.encrypt_password(
        login.password.encode(), login.role.encode(), b"scram-sha-256"
    )
    return raw.decode("ascii")


Connect = Callable[[str], Any]  # psycopg.connect with autocommit; tests pass a fake


def _connect(conninfo: str) -> Any:
    import psycopg

    return psycopg.connect(conninfo, autocommit=True)


def apply(settings: CoreSettings | Settings, connect: Connect = _connect) -> list[str]:
    """Make Postgres match .env. Returns plain lines for the deploy log (no password)."""
    found, target = logins(settings)
    database = target.database or "saley"
    done = []
    with connect(superuser_conninfo(settings, target, "postgres")) as conn:
        for login in found:
            exists = (
                conn.execute("select 1 from pg_roles where rolname = %s", (login.role,)).fetchone()
                is not None
            )
            conn.execute(role_statements(login, exists, scram(conn, login)))
            done.append(f"role {login.role} {'password set' if exists else 'created'}")
        if conn.execute("select 1 from pg_database where datname = %s", (database,)).fetchone():
            done.append(f"database {database} exists")
        else:
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(database), sql.Identifier(ADMIN)
                )
            )
            done.append(f"database {database} created")
        conn.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database))
        )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database),
                sql.SQL(", ").join(sql.Identifier(r) for r, _ in ROLE_URLS),
            )
        )
    with connect(superuser_conninfo(settings, target, database)) as conn:
        # No parameters: psycopg sends the file with the simple protocol, many statements.
        conn.execute(GRANTS_SQL.read_text(encoding="utf-8"))
        done.append("grants applied")
    return done
