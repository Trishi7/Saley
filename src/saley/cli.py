"""`saley` command line: one entry for every service and the dev checks (CLAUDE.md).

Why: the same command runs a service on a laptop (`uv run saley web`) and in its container.
Each command checks only the settings its service needs.
"""

from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text

from saley import cli_server
from saley.core.logging import configure_logging, get_logger
from saley.core.settings import MissingSettings, get_core_settings, get_settings

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
    help="Checks against the GTM sheet copy: read-only, plus the scratch-tab round trip.",
)
app.add_typer(sheet_app, name="sheet")
decisions_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="The decision table (docs/DECISIONS.md).",
)
app.add_typer(decisions_app, name="decisions")
news_app = typer.Typer(
    invoke_without_command=True,
    pretty_exceptions_show_locals=False,
    help="The news service (no subcommand), the registry seed and one bounded live fetch.",
)
app.add_typer(news_app, name="news")
llm_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="LLM checks: the one live call per release.",
)
app.add_typer(llm_app, name="llm")
notes_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="Meeting notes from the Drive folder (read only, D81).",
)
app.add_typer(notes_app, name="notes")
cli_server.register(app)  # R6: db-roles, last-run (deploy.sh, smoke.py)
log = get_logger("saley.cli")

ROOT = Path(__file__).resolve().parents[2]
CHILD_CHECK_SECONDS = 2.0  # a worker that dies at start is reported, not hidden
CHILD_STOP_SECONDS = 20
CHILD_WATCH_SECONDS = 1.0  # how often `saley dev` looks at its worker child


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    # R6: the database values only, so `saley db-upgrade` never stops on another service's value.
    cfg.set_main_option("sqlalchemy.url", url or get_core_settings().admin_database_url)
    cfg.attributes["configure_logger"] = False
    return cfg


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


PORT_PROBE_SECONDS = 0.5
PORT_HOSTS = ("127.0.0.1", "::1")  # a browser at localhost may reach either


def port_in_use(port: int) -> bool:
    """True when a program already answers on this port on this machine.

    Why (Q-N5): uvicorn sets SO_REUSEADDR, and on Windows that lets a second server bind the
    same port with no error. Two servers then split the requests, so one sign-in can start in
    one process and end in another. A connect test sees the first server whatever its bind.
    """
    for host in PORT_HOSTS:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(PORT_PROBE_SECONDS)
                if probe.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue  # no IPv6 on this machine
    return False


def _refuse_busy_port(port: int) -> None:
    if port_in_use(port):
        _fail(
            f"Port {port} is in use: another program answers there. Stop it first. "
            "On Windows two servers can share one port and split the requests."
        )


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
    problems = settings.web_start_problems()  # D242: the server refuses a half-set sign-in
    if problems:
        _fail("Refused: " + "; ".join(problems) + ".")
    if not settings.saley_in_container:
        _refuse_busy_port(settings.web_port)  # Q-N5: never two servers on one laptop port
    uvicorn.run(
        "saley.web.app:create_app",
        factory=True,
        # Laptop (dev): this machine only. Container: all interfaces; caddy is the only door.
        host="127.0.0.1" if settings.is_dev else "0.0.0.0",  # noqa: S104
        port=settings.web_port,
        log_level=settings.log_level.lower(),
        proxy_headers=True,
        # D254: only caddy's X-Forwarded-For counts, so the sign-in limit is per real client.
        forwarded_allow_ips=settings.forwarded_allow_ips,
    )


@app.command()
def bot(
    no_gateway: Annotated[
        bool, typer.Option("--no-gateway", help="CI only: no Discord connection (D247).")
    ] = False,
) -> None:
    """Run the Discord bot (test channel, ping only in R1)."""
    from saley.bot.gateway import run, run_offline

    if no_gateway:
        if get_settings().saley_env == "prod":
            _fail("Refused: --no-gateway is for the CI compose checks, never the server.")
        run_offline()
        return
    try:
        run()
    except MissingSettings as exc:
        _fail(str(exc))


@app.command()
def alive(
    service: Annotated[str, typer.Argument(help="worker, news or bot.")],
    max_age: Annotated[
        int, typer.Option(help="Seconds a beat stays fresh.")
    ] = 120,  # alive.MAX_AGE_SECONDS
) -> None:
    """D245: the compose health check. Exit 0 when the service wrote its heartbeat lately."""
    from saley.core import alive as heartbeat

    seconds = heartbeat.age(service)
    if seconds is None or not heartbeat.is_alive(service, max_age):
        typer.echo(
            f"{service}: no fresh heartbeat ({'none' if seconds is None else f'{seconds:.0f} s'})"
        )
        raise typer.Exit(code=1)
    typer.echo(f"{service}: alive ({seconds:.0f} s)")


@app.command()
def worker(
    once: Annotated[str | None, typer.Option(help="Run one job and exit.")] = None,
    reap: Annotated[
        bool, typer.Option("--reap/--no-reap", help="Close job rows an old worker left running.")
    ] = True,
) -> None:
    """Run the scheduler, or one job with --once JOB."""
    from saley.core.settings import WORKER_PROCESS, set_process
    from saley.worker.jobs import register_all
    from saley.worker.registry import REGISTRY, run_job
    from saley.worker.scheduler import run_forever

    set_process(WORKER_PROCESS)  # D23, D46: only this entry may build the sheet write client
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
    run_forever(reap=reap)


@app.command()
def dev(
    port: Annotated[
        int | None, typer.Option(help="Web port on 127.0.0.1. Default: WEB_PORT.")
    ] = None,
    force: Annotated[bool, typer.Option(help="Start even if another worker ran lately.")] = False,
) -> None:
    """Run web and worker together on this laptop (D43, D46). Ctrl+C stops both.

    The web runs in this process. The worker runs as a child process (`saley worker`), so this
    process never holds the sheet write credential (D23, D46).
    """
    import uvicorn

    from saley.worker.scheduler import recent_worker

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
    _refuse_busy_port(port or settings.web_port)  # Q-N5: before the worker child starts
    _dev_owners(settings)
    child = start_worker_child(reap=not force)
    died = threading.Event()
    stop = threading.Event()
    try:
        time.sleep(CHILD_CHECK_SECONDS)
        if child.poll() is not None:
            _fail(f"The worker stopped at start (exit {child.returncode}). Read the lines above.")
        server = uvicorn.Server(
            uvicorn.Config(
                "saley.web.app:create_app",
                factory=True,
                host="127.0.0.1",
                port=port or settings.web_port,
                log_level=settings.log_level.lower(),
            )
        )
        threading.Thread(
            target=watch_child, args=(child, server, died, stop), name="worker-watch", daemon=True
        ).start()
        server.run()
    finally:
        stop.set()  # the watcher ends; a normal stop is not "the worker died"
        stop_child(child)
    if died.is_set():
        raise typer.Exit(code=1)


def _dev_owners(settings: Any) -> None:
    """Q-R6-4 (D256): the dev database gets the server's owner model once. Never stops dev."""
    from saley.core.ownership import transfer_dev

    try:
        log.info("dev_db_owners", result=transfer_dev(settings))
    except Exception as exc:  # a laptop without the admin login still runs dev
        log.warning("dev_db_owners", result=f"failed: {exc.__class__.__name__}")


def watch_child(
    child: subprocess.Popen[bytes],
    server: Any,
    died: threading.Event,
    stop: threading.Event,
    interval: float | None = None,
) -> None:
    """Stop the web when the worker child dies, loudly (H3).

    A web that runs on without its worker looks fine but never syncs and never pushes a write.
    The exit code of `saley dev` is 1, so a script that started it sees the failure.
    """
    while not stop.is_set():
        code = child.poll()
        if code is not None:
            log.error("worker_child_exited", exit_code=code)
            typer.echo(f"The worker stopped (exit {code}). Stopping saley dev.", err=True)
            died.set()
            server.should_exit = True
            return
        stop.wait(CHILD_WATCH_SECONDS if interval is None else interval)


def start_worker_child(*, reap: bool) -> subprocess.Popen[bytes]:
    """`python -m saley.cli worker` with the same environment and the same console."""
    argv = [sys.executable, "-m", "saley.cli", "worker"]
    if not reap:
        argv.append("--no-reap")
    return subprocess.Popen(argv, cwd=ROOT)  # noqa: S603 - fixed argv, no user input


def _is_windows() -> bool:
    return os.name == "nt"


def stop_child(child: subprocess.Popen[bytes]) -> None:
    """Stop the worker child and everything it started. Safe to call twice.

    On Windows `terminate()` ends only that one process, so a grandchild would stay as an
    orphan worker: the whole tree goes with `taskkill /T /F`. Elsewhere: ask, wait, then kill.
    """
    if child.poll() is not None:
        return
    if _is_windows():
        subprocess.run(  # noqa: S603 - fixed argv, our own child pid
            ["taskkill", "/PID", str(child.pid), "/T", "/F"],  # noqa: S607
            capture_output=True,
            check=False,
        )
    else:
        child.terminate()
    try:
        child.wait(timeout=CHILD_STOP_SECONDS)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


@news_app.callback()
def news(ctx: typer.Context) -> None:
    """Run the news service: editions on the schedule, only in this process (D70)."""
    if ctx.invoked_subcommand is not None:
        return
    from saley.news.service import run_forever

    try:
        get_settings().require("news")
    except MissingSettings as exc:
        _fail(str(exc))
    run_forever()


@news_app.command("seed")
def news_seed() -> None:
    """Load src/saley/news/registry.yaml into news_sources. Safe to run again."""
    from saley.core.db import session_scope
    from saley.news.sources import seed_registry

    with session_scope() as session:
        added, updated = seed_registry(session)
    typer.echo(f"news sources: {added} added, {updated} updated")


@news_app.command("fetch")
def news_fetch(
    live: Annotated[
        bool, typer.Option("--live", help="Required: this calls public feeds.")
    ] = False,
    once: Annotated[
        bool, typer.Option("--once", help="Required: one request per source kind.")
    ] = False,
    limit: Annotated[int, typer.Option("--limit", min=1, max=50)] = 3,
    no_score: Annotated[
        bool, typer.Option("--no-score", help="Required: store nothing scored.")
    ] = False,
) -> None:
    """R3-N03: one request per source kind through egress, `limit` items each, nothing scored."""
    from saley.core.db import session_scope
    from saley.news.editions import probe_once
    from saley.news.sources import seed_registry

    if not (live and once and no_score):
        _fail("Refused: add --live --once --no-score. This command is one bounded fetch.")
    try:
        get_settings().require("news")
    except MissingSettings as exc:
        _fail(str(exc))
    with session_scope() as session:
        added, _ = seed_registry(session)
    if added:
        typer.echo(f"seeded {added} news sources")
    reports = probe_once(limit)
    for report in reports:
        typer.echo(report.line())
    failed = [r for r in reports if r.status == "error"]
    if not reports or failed:
        _fail(f"FAIL: {len(failed)} of {len(reports)} source kinds failed")
    typer.echo(f"PASS: {len(reports)} source kinds answered")


@llm_app.command("smoke")
def llm_smoke(
    live: Annotated[
        bool, typer.Option("--live", help="Required: this makes one real call.")
    ] = False,
    release: Annotated[str, typer.Option(help="Release id the call is counted against.")] = "R3",
) -> None:
    """R3-L02: one live LLM call per release. Exit 0 proven, 1 failed, 3 NOT CONFIGURED: Q-R3-1."""
    from saley.news.smoke import run_smoke

    if not live:
        _fail("Refused: add --live. This command makes one real LLM call (once per release).")
    result = run_smoke(release)
    typer.echo(result.message)
    if result.code:
        raise typer.Exit(code=result.code)


def _run_once(job: str) -> None:
    """Run one job like the scheduler would, then print its job_runs note."""
    from saley.worker.jobs import register_all
    from saley.worker.registry import run_job

    register_all()
    result = run_job(job)
    # The note of THIS run. The newest job_runs row may belong to a worker that ran meanwhile.
    typer.echo(f"{job}: {result} · {getattr(result, 'note', None) or ''}")
    if result != "ok":
        raise typer.Exit(code=1)


@app.command()
def sync(
    once: Annotated[bool, typer.Option("--once", help="One sync, then exit.")] = False,
    raw_file: Annotated[
        Path | None, typer.Option(help="CI and tests: a saved sheet (JSON), never Google (D249).")
    ] = None,
) -> None:
    """Sync the sheet copy into the database."""
    if raw_file is not None:
        result = cli_server.sync_from_file(raw_file)
        typer.echo(f"sheet_sync: {result} · {getattr(result, 'note', None) or ''}")
        raise typer.Exit(code=0 if result == "ok" else 1)
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
    from saley.bot.words import DMS_OFF
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
    if not settings.dm_enabled:
        typer.echo(DMS_OFF)  # D128, D133: no DM is sent while the switch is off
    if current != head:
        raise typer.Exit(code=1)


@sheet_app.command("verify")
def sheet_verify(
    out: Annotated[Path | None, typer.Option(help="Write the markdown report here.")] = None,
    raw_file: Annotated[
        Path | None, typer.Option(help="CI and tests: a saved sheet (JSON), never Google (D249).")
    ] = None,
    database: Annotated[
        str | None, typer.Option(help="Another database on the same server (restore.sh).")
    ] = None,
) -> None:
    """Gate step 6: recount the sheet copy and compare every number with the API."""
    from fastapi.testclient import TestClient

    from saley.core.clock import today_ist
    from saley.core.egress import EgressBlocked
    from saley.qa.sheet_verify import verify
    from saley.sales.sheet import SheetNotCopy, fetch_raw
    from saley.web.app import create_app

    if database:
        cli_server.use_database(database)
    try:
        raw = cli_server.load_raw(raw_file) if raw_file else fetch_raw()
    except (SheetNotCopy, MissingSettings, EgressBlocked) as exc:
        _fail(f"Refused: {exc}")
    with TestClient(create_app()) as client:
        # R4: every /api route needs a session. The mock sign-in works with SALEY_ENV=dev only.
        if client.post("/auth/mock", follow_redirects=False).status_code != 303:
            _fail("Refused: sign-in needs AUTH_PROVIDER=mock with SALEY_ENV=dev or ci.")

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


@sheet_app.command("roundtrip")
def sheet_roundtrip(
    test_tab: Annotated[
        bool, typer.Option("--test-tab", help="Required: write only in the scratch tab.")
    ] = False,
    row_add: Annotated[
        bool, typer.Option("--row-add", help="R3-S02: add a person row, sign it, undo it.")
    ] = False,
    case: Annotated[
        str | None,
        typer.Option(
            "--case",
            help="R5 T9, live in SALEY_TEST: done-undo, add-email, commitment-nonowner, parity.",
        ),
    ] = None,
    n: Annotated[int, typer.Option("--n", min=2, max=60, help="Parity: Done/Undo steps.")] = 20,
    seed: Annotated[int, typer.Option("--seed", help="Parity: the random seed.")] = 20261021,
) -> None:
    """R2-W02: write, read back, undo and delete in the scratch tab of the sheet copy.

    With --row-add (R3-S02) the steps add a person row instead, through the worker's row code.
    With --case (R5 T9) the dashboard routes run, the worker pushes, and the sheet is read back.

    Exit 0 PASS, 1 FAIL, 3 NOT CONFIGURED: Q-R2-1 (a human must open the sheet to Saley).
    """
    from saley.core.settings import WORKER_PROCESS, set_process
    from saley.sales.roundtrip import execute
    from saley.sales.roundtrip_cases import CASES

    if not test_tab:
        _fail("Refused: add --test-tab. Only the scratch tab is allowed.")
    if case is not None and (case not in CASES or row_add):
        _fail(f"Refused: --case is one of {', '.join(CASES)}, and not with --row-add.")
    set_process(WORKER_PROCESS)  # D23, D46: the write client is built in the worker role only
    code = execute(typer.echo, row_add=row_add, case=case, n=n, seed=seed)
    if code:
        raise typer.Exit(code=code)


@decisions_app.command("import")
def decisions_import(
    file: Annotated[Path | None, typer.Option("--file", help="Default: docs/DECISIONS.md.")] = None,
) -> None:
    """Load the docs table into the decision table. Human rows are active, 'proposed' rows wait.

    Safe to run again: a row is added once, a text change is updated, and a decision already
    confirmed on the dashboard is not moved back to proposed.
    """
    from saley.core.db import session_scope
    from saley.intel.decisions import DECISIONS_FILE, import_decisions

    path = file or DECISIONS_FILE
    if not path.exists():
        _fail(f"File not found: {path}")
    with session_scope() as session:
        stats = import_decisions(session, path)
    typer.echo(f"decisions: {stats.note()}")


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


@discord_app.command("roundtrip")
def discord_roundtrip(
    test: Annotated[bool, typer.Option("--test", help="Required: use the TEST channel.")] = False,
    case: Annotated[str, typer.Option("--case", help="pack, answer or ping.")] = "pack",
) -> None:
    """R4-P03, R4-Q02: post one shape to the TEST channel, read it back, delete it."""
    import httpx
    from sqlalchemy.exc import SQLAlchemyError

    from saley.bot.cli_discord import DiscordRefused
    from saley.bot.roundtrip import run
    from saley.core.egress import EgressBlocked

    if not test:
        _fail("Refused: add --test. Only the TEST channel is allowed.")
    try:
        result = run(case)
    except (httpx.HTTPError, SQLAlchemyError) as exc:
        _fail(f"roundtrip {case} failed: {exc.__class__.__name__}")
    except (DiscordRefused, MissingSettings, EgressBlocked) as exc:
        _fail(f"Refused: {exc}")
    back, gone = ("yes" if flag else "NO" for flag in (result.matched, result.deleted))
    typer.echo(f"roundtrip {result.case}: {result.message_id} read back {back}, deleted {gone}")
    if not result.ok:
        raise typer.Exit(code=1)


NOT_CONFIGURED_EXIT = 3  # docs/16 §3: a human-only need is missing


@notes_app.command("sync")
def notes_sync_command(
    live: Annotated[bool, typer.Option("--live", help="Read the real Drive folder.")] = False,
    once: Annotated[bool, typer.Option("--once", help="One sync, then exit.")] = False,
) -> None:
    """R2-N05: one read-only sync of the Drive notes folder through the egress door.

    Exit 0 OK, 1 failure (also: folder not shared), 3 NOT CONFIGURED: Q-R2-6 (the Drive API is
    off in the Google project, an empty folder id, or googleapis.com off the allowlist).
    The flags only state the intent: this command never writes to Drive.
    """
    from saley.core.db import session_scope
    from saley.core.egress import EgressBlocked, EgressRateLimited
    from saley.intel import drive
    from saley.intel.notes_sync import sync_drive
    from saley.web import words

    def not_configured(reason: str) -> NoReturn:
        # Human-only values (.env): PENDING on the open question, never a plain pass (docs/16).
        typer.echo(f"NOT CONFIGURED: Q-R2-6 ({reason})")
        raise typer.Exit(code=NOT_CONFIGURED_EXIT)

    folder = get_settings().meeting_notes_drive_folder_id.strip()
    if not folder:
        not_configured("MEETING_NOTES_DRIVE_FOLDER_ID is empty")
    try:
        try:
            client = drive.build_client()
        except (OSError, ValueError) as exc:  # the key file is missing or damaged; never print it
            _fail(f"Refused: the service account file could not be read ({type(exc).__name__})")
        with session_scope() as session:
            stats = sync_drive(session, client, folder)
    except (
        EgressRateLimited
    ) as exc:  # before EgressBlocked: it is a subclass, and it is not a block
        _fail(f"Rate limited: wait a little and run it again ({exc})")
    except EgressBlocked as exc:
        not_configured(f"EGRESS_ALLOWLIST in .env: {exc}")
    except MissingSettings as exc:
        _fail(f"Refused: {exc}")
    if stats.state == "api_disabled":
        typer.echo("NOT CONFIGURED: Q-R2-6")
        typer.echo(f"Enable the Drive API: {stats.enable_url or 'no link in the Google answer'}")
        raise typer.Exit(code=NOT_CONFIGURED_EXIT)
    if stats.state == "folder_not_shared":
        # Q-R2-6 is resolved: a folder that is not shared is a real failure (exit 1) that names
        # who to share it with. A rate limit is a different state (`error`), also exit 1.
        account = getattr(client, "account", None)
        _fail(
            words.fill(words.NOTES_NOT_SHARED, sa=account, id=folder)
            if account
            else words.fill(words.NOTES_NOT_SHARED_NO_SA, id=folder)
        )
    if stats.state not in ("ok", "partial"):
        _fail(words.NOTES_FAILING)
    for label, value in (
        ("files listed", stats.listed),
        ("new", stats.new),
        ("changed", stats.changed),
        ("gone", stats.gone),
        ("parsed", stats.parsed),
        ("commitments", stats.commitments),
        ("outside facts", stats.outside_facts),
        ("outside, no contact", stats.outside_no_contact),
        ("unknown owner", stats.unknown_owner),
    ):
        typer.echo(f"{label}: {value}")


if __name__ == "__main__":  # pragma: no cover
    app()
