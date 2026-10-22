"""Server commands for deploy.sh, restore.sh and smoke.py (R6): `saley db-roles`, `saley last-run`.

Why a second module: cli.py is near the 800-line ceiling. These commands run inside the app
image on the server, so they need no tool on the host besides Docker. They print plain lines and
exit non-zero on a problem; they never print a password.

Also the CI helpers (D249): `--raw-file` lets the sync and the sheet verify read a saved sheet
(JSON, tab name -> rows) instead of Google. CI has no sheet credentials. Both refuse in prod, so
the server always reads the real sheet copy.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from sqlalchemy import text
from sqlalchemy.engine import make_url

from saley.core.settings import get_settings


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def load_raw(path: Path) -> dict[str, list[list[Any]]]:
    """A saved sheet: {tab title: rows}. Refused on the server (D249)."""
    if get_settings().saley_env == "prod":
        _fail("Refused: --raw-file is for CI and tests; the server reads the sheet copy.")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail(f"Refused: cannot read {path.name} ({exc.__class__.__name__}).")
    if not isinstance(raw, dict) or not all(isinstance(v, list) for v in raw.values()):
        _fail(f"Refused: {path.name} is not a saved sheet ({{tab: rows}}).")
    return raw


def sync_from_file(path: Path) -> Any:
    """One `sheet_sync` run, under its lock and with its job_runs row, from a saved sheet."""
    from saley.core.db import session_scope
    from saley.sales.sheet import build_tabs
    from saley.sales.sync import run_sync
    from saley.worker.jobs import register_all
    from saley.worker.registry import REGISTRY, run_job

    data = build_tabs(load_raw(path))

    def from_file() -> str:
        with session_scope() as session:
            return f"saved sheet · {run_sync(session, data).note()}"

    register_all()
    REGISTRY["sheet_sync"] = replace(REGISTRY["sheet_sync"], func=from_file, missing=())
    return run_job("sheet_sync")


def use_database(name: str) -> None:
    """Point this process at another database of the same server (restore.sh, D248).

    Only the database name changes; the roles and passwords stay those of .env. Called before
    any engine exists, so every engine of this process opens the scratch database.
    """
    settings = get_settings()
    for field in ("database_url", "database_url_ro"):
        url = make_url(getattr(settings, field)).set(database=name)
        setattr(settings, field, url.render_as_string(hide_password=False))


def register(app: typer.Typer) -> None:
    @app.command("db-roles")
    def db_roles() -> None:
        """Server: make the three roles, the database and the grants match .env (D244)."""
        from saley.core.roles import RolesRefused, apply

        try:
            lines = apply(get_settings())
        except RolesRefused as exc:
            _fail(f"Refused: {exc}")
        for line in lines:
            typer.echo(line)
        typer.echo("roles ok")

    @app.command("db-owners")
    def db_owners() -> None:
        """Owners and grants against docs/01 §11 (CI row R6-G03, D256). Exit 1 names each break."""
        import psycopg

        from saley.qa.db_owners import counts, problems

        url = make_url(get_settings().admin_database_url).set(drivername="postgresql")
        with psycopg.connect(url.render_as_string(hide_password=False)) as conn:
            found, n = problems(conn), counts(conn)
        for line in found:
            typer.echo(f"FAIL {line}")
        if found:
            raise typer.Exit(code=1)
        typer.echo(
            f"db owners ok: {n.get('r', 0)} tables, {n.get('v', 0)} views, "
            f"{n.get('S', 0)} sequences (docs/01 section 11)"
        )

    @app.command("last-run")
    def last_run(
        job: Annotated[str, typer.Argument(help="A job name, for example sheet_sync.")],
        within: Annotated[int, typer.Option(help="Minutes. The ok run must be this new.")] = 15,
    ) -> None:
        """Smoke: exit 0 when JOB finished ok in the last WITHIN minutes (deploy.sh)."""
        from saley.core.db import rw_engine

        with rw_engine().connect() as conn:
            row = conn.execute(
                text(
                    "select max(finished_at) as at, "
                    "max(finished_at) > now() - make_interval(mins => :m) as fresh "
                    "from job_runs where job = :j and status = 'ok'"
                ),
                {"j": job, "m": within},
            ).one()
        if not row.fresh:
            _fail(f"{job}: no ok run in the last {within} min (last ok: {row.at or 'never'})")
        typer.echo(f"{job}: ok at {row.at:%d %b %H:%M}")
