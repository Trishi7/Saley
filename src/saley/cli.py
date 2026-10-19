"""`saley` command line: one entry for every service and the dev checks (CLAUDE.md).

Why: the same command runs a service on a laptop (`uv run saley web`) and in its container.
Each command checks only the settings its service needs.
"""

from __future__ import annotations

import io
import sys
from datetime import timedelta
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text

from saley.core.logging import configure_logging, get_logger
from saley.core.settings import MissingSettings, get_settings

# Tracebacks never print local variables: they can hold tokens.
app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
    help="Saley v2 services and checks.",
)
discord_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="Test loop on the Discord TEST channel.",
)
app.add_typer(discord_app, name="discord")
sheet_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="Read-only checks against the GTM sheet copy.",
)
app.add_typer(sheet_app, name="sheet")
log = get_logger("saley.cli")

ROOT = Path(__file__).resolve().parents[2]


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url or get_settings().admin_database_url)
    cfg.attributes["configure_logger"] = False
    return cfg


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


@app.callback()
def main() -> None:
    # Windows consoles use a narrow code page; never crash on an emoji in a message.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")
    configure_logging()


@app.command()
def web() -> None:
    """Serve the dashboard and the JSON API."""
    import uvicorn

    settings = get_settings()
    try:
        settings.require("web")
    except MissingSettings as exc:
        _fail(str(exc))
    uvicorn.run(
        "saley.web.app:create_app",
        factory=True,
        # Laptop (dev): this machine only. Container: all interfaces; caddy is the only door.
        host="127.0.0.1" if settings.is_dev else "0.0.0.0",  # noqa: S104
        port=settings.web_port,
        log_level=settings.log_level.lower(),
        proxy_headers=True,
    )


@app.command()
def bot() -> None:
    """Run the Discord bot (test channel, ping only in R1)."""
    from saley.bot.gateway import run

    try:
        run()
    except MissingSettings as exc:
        _fail(str(exc))


@app.command()
def worker(once: Annotated[str | None, typer.Option(help="Run one job and exit.")] = None) -> None:
    """Run the scheduler, or one job with --once JOB."""
    from saley.worker.jobs import register_all
    from saley.worker.registry import REGISTRY, run_job
    from saley.worker.scheduler import run_forever

    try:
        get_settings().require("worker")
    except MissingSettings as exc:
        _fail(str(exc))
    if once:
        register_all()
        if once not in REGISTRY:
            _fail(f"Unknown job {once!r}. Jobs: {', '.join(sorted(REGISTRY))}")
        result = run_job(once)
        typer.echo(f"{once}: {result}")
        raise typer.Exit(code=0 if result == "ok" else 1)
    run_forever()


@app.command()
def dev(
    port: Annotated[
        int | None, typer.Option(help="Web port on 127.0.0.1. Default: WEB_PORT.")
    ] = None,
    force: Annotated[bool, typer.Option(help="Start even if another worker ran lately.")] = False,
) -> None:
    """Run web and worker together on this laptop (D43). Ctrl+C stops both."""
    import uvicorn

    from saley.worker.scheduler import recent_worker, start_background

    settings = get_settings()
    try:
        settings.require("web")
        settings.require("worker")
    except MissingSettings as exc:
        _fail(str(exc))
    if not settings.is_dev:
        _fail("saley dev runs only with SALEY_ENV=dev.")
    window = timedelta(seconds=2 * settings.sheet_sync_seconds)
    if not force and recent_worker(window):
        _fail("Another worker ran in the last minutes. Stop it, or use --force.")
    scheduler = start_background(reap=not force)
    try:
        uvicorn.run(
            "saley.web.app:create_app",
            factory=True,
            host="127.0.0.1",
            port=port or settings.web_port,
            log_level=settings.log_level.lower(),
        )
    finally:
        scheduler.shutdown(wait=False)


@app.command()
def news() -> None:
    """Run the news service (idle in R1)."""
    from saley.news.service import run_forever

    run_forever()


def _run_once(job: str) -> None:
    """Run one job like the scheduler would, then print its job_runs note."""
    from saley.core.db import rw_engine
    from saley.worker.jobs import register_all
    from saley.worker.registry import run_job

    register_all()
    result = run_job(job)
    with rw_engine().connect() as conn:
        note = conn.execute(
            text("select note from job_runs where job = :j order by id desc limit 1"), {"j": job}
        ).scalar()
    typer.echo(f"{job}: {result} · {note or ''}")
    if result != "ok":
        raise typer.Exit(code=1)


@app.command()
def sync(
    once: Annotated[bool, typer.Option("--once", help="One sync, then exit.")] = False,
) -> None:
    """Sync the sheet copy into the database."""
    try:
        get_settings().require("sync")
    except MissingSettings as exc:
        _fail(str(exc))
    if not once:
        _fail("Use `saley sync --once`. The worker runs the sync on its schedule.")
    _run_once("sheet_sync")


@app.command()
def metrics(
    once: Annotated[bool, typer.Option("--once", help="One snapshot, then exit.")] = False,
) -> None:
    """Write today's metric snapshot."""
    if not once:
        _fail("Use `saley metrics --once`. The worker runs it at 23:50 IST.")
    _run_once("metrics_snapshot")


@app.command("db-upgrade")
def db_upgrade() -> None:
    """Run Alembic migrations up to head."""
    command.upgrade(alembic_config(), "head")
    typer.echo("database at head")


@app.command()
def health() -> None:
    """Print db ok, alembic head, sheet=copy|live, test_mode."""
    from saley.core.db import rw_engine

    settings = get_settings()
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    try:
        with rw_engine().connect() as conn:
            conn.execute(text("select 1"))
            current = MigrationContext.configure(conn).get_current_revision()
    except Exception as exc:  # health must say why, not crash
        typer.echo(f"db FAIL ({type(exc).__name__})")
        typer.echo(f"alembic head {head}")
        typer.echo(f"sheet={settings.sheet_mode} test_mode={str(settings.test_mode).lower()}")
        raise typer.Exit(code=1) from exc
    typer.echo("db ok")
    state = "at head" if current == head else "BEHIND, run `saley db-upgrade`"
    typer.echo(f"alembic head {head} · current {current} · {state}")
    typer.echo(f"sheet={settings.sheet_mode} test_mode={str(settings.test_mode).lower()}")
    if current != head:
        raise typer.Exit(code=1)


@sheet_app.command("verify")
def sheet_verify(
    out: Annotated[Path | None, typer.Option(help="Write the markdown report here.")] = None,
) -> None:
    """Gate step 6: recount the sheet copy and compare every number with the API."""
    from fastapi.testclient import TestClient

    from saley.core.clock import today_ist
    from saley.core.egress import EgressBlocked
    from saley.qa.sheet_verify import verify
    from saley.sales.sheet import SheetNotCopy, fetch_raw
    from saley.web.app import create_app

    try:
        raw = fetch_raw()
    except (SheetNotCopy, MissingSettings, EgressBlocked) as exc:
        _fail(f"Refused: {exc}")
    with TestClient(create_app()) as client:

        def get(path: str) -> dict:
            response = client.get(path)
            response.raise_for_status()
            return response.json()

        report = verify(raw, today_ist(), get)
    body = report.markdown(f"Sheet verify · {today_ist():%d %b %Y}")
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8", newline="\n")
    for metric, sheet, api, ok in report.rows:
        typer.echo(f"{metric} · {sheet} · {api} · {'OK' if ok else 'DIFF'}")
    typer.echo("PASS" if report.ok else "FAIL")
    if not report.ok:
        raise typer.Exit(code=1)


@discord_app.command("send")
def discord_send(
    message: Annotated[str, typer.Argument(help="Text to post. '@Saley' becomes a mention.")],
    test: Annotated[
        bool, typer.Option("--test", help="Required: post to the TEST channel.")
    ] = False,
    as_user: Annotated[
        str | None, typer.Option("--as", help="Test user id from DISCORD_TEST_USER_IDS.")
    ] = None,
) -> None:
    """Post to the TEST channel."""
    from saley.bot.cli_discord import DiscordRefused, send
    from saley.core.egress import EgressBlocked

    if not test:
        _fail("Refused: add --test. Only the TEST channel is allowed.")
    try:
        message_id = send(message, as_user=as_user)
    except (DiscordRefused, MissingSettings, EgressBlocked) as exc:
        _fail(f"Refused: {exc}")
    typer.echo(f"sent {message_id}")


@discord_app.command("read")
def discord_read(
    test: Annotated[bool, typer.Option("--test", help="Required: read the TEST channel.")] = False,
    last: Annotated[int, typer.Option("--last", min=1, max=100)] = 20,
) -> None:
    """Print the last N messages of the TEST channel, oldest first."""
    from saley.bot.cli_discord import DiscordRefused, read
    from saley.core.egress import EgressBlocked

    if not test:
        _fail("Refused: add --test. Only the TEST channel is allowed.")
    try:
        messages = read(last)
    except (DiscordRefused, MissingSettings, EgressBlocked) as exc:
        _fail(f"Refused: {exc}")
    for m in messages:
        typer.echo(f"{m.at} {m.author}: {m.content}")


if __name__ == "__main__":  # pragma: no cover
    app()
