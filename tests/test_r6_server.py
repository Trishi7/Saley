"""R6: prod settings, db-roles, heartbeat files, the ping round trip, the lock heartbeat, the
server CLI commands, and the gate's CI reader."""

from __future__ import annotations

import importlib
import sys
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from saley.cli import app
from saley.core import alive, egress
from saley.core.settings import Settings
from saley.qa import matrix

runner = CliRunner()
SECRET = "s" * 40
PROD = {
    "saley_env": "prod",
    "public_url": "https://saley.example",
    "google_oauth_redirect_url": "https://saley.example/auth/callback",
    "auth_provider": "google",
    "google_oauth_client_id": "id.apps.googleusercontent.com",
    "google_oauth_client_secret": "secret",
    "web_allowed_emails": "trishi=t@example.com",
    "web_session_secret": SECRET,
}


# ------------------------------------------------------------------ settings (R6-P01..P05)


def test_saley_env_takes_dev_ci_prod_only() -> None:
    assert Settings(_env_file=None, saley_env=" PROD ").saley_env == "prod"
    assert Settings(_env_file=None, saley_env="ci").saley_env == "ci"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, saley_env="production")


def test_web_start_problems_in_prod() -> None:
    assert Settings(_env_file=None, **PROD).web_start_problems() == []
    assert Settings(_env_file=None).web_start_problems() == []  # dev
    bad = {
        "auth_provider": "mock",
        "public_url": "http://saley.example",
        "google_oauth_redirect_url": "http://localhost:8090/auth/callback",
        "web_session_secret": "short",
        "google_oauth_client_secret": "",
    }
    for key, value in bad.items():
        problems = Settings(_env_file=None, **{**PROD, key: value}).web_start_problems()
        assert problems, key
        if key == "web_session_secret":
            assert all(value not in p for p in problems)  # never echoes the secret
    localhost = Settings(_env_file=None, **{**PROD, "public_url": "https://localhost"})
    assert any("PUBLIC_URL" in p for p in localhost.web_start_problems())


def test_saley_web_refuses_a_half_set_server(
    set_env: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    started: list[Any] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append(a))
    set_env(SALEY_ENV="prod", AUTH_PROVIDER="mock", SALEY_IN_CONTAINER="true")
    result = runner.invoke(app, ["web"])
    assert result.exit_code == 1 and "Refused" in result.output
    assert "AUTH_PROVIDER=mock" in result.output and not started


def _request(host: str) -> Any:
    return SimpleNamespace(client=SimpleNamespace(host=host))


def test_mock_signin_dev_loopback_or_ci_localhost_only() -> None:
    from saley.web.auth import mock_allowed

    mock = {"auth_provider": "mock"}
    dev = Settings(_env_file=None, saley_env="dev", **mock)
    assert mock_allowed(_request("127.0.0.1"), dev)
    assert not mock_allowed(_request("10.0.0.5"), dev)
    local = {"public_url": "https://localhost:8443", "saley_ci_runner": True}
    ci = Settings(_env_file=None, saley_env="ci", **local, **mock)
    assert ci.is_ci and mock_allowed(_request("172.18.0.7"), ci)  # caddy, behind the proxy
    for url in ("https://saley.membrane.social", "http://localhost:8443"):
        not_ci = Settings(_env_file=None, saley_env="ci", **{**local, "public_url": url}, **mock)
        assert not not_ci.is_ci and not mock_allowed(_request("172.18.0.7"), not_ci)
    # Review M1: SALEY_ENV=ci alone is not enough; only compose.ci.yml sets the runner marker.
    unmarked = Settings(_env_file=None, saley_env="ci", public_url="https://localhost:8443", **mock)
    assert not unmarked.is_ci and not mock_allowed(_request("127.0.0.1"), unmarked)
    short = Settings(_env_file=None, saley_env="ci", web_session_secret="x", **local, **mock)
    assert short.web_start_problems()  # ci checks the session secret too
    prod = Settings(_env_file=None, saley_env="prod", public_url="https://localhost", **mock)
    assert not mock_allowed(_request("127.0.0.1"), prod)


def test_dev_user_email_ignored_outside_dev() -> None:
    from saley.web.auth import email_for

    prod = Settings(_env_file=None, **PROD)
    assert email_for(prod, prod.dev_user_short, "google") == "t@example.com"  # the allowlist
    other = Settings(_env_file=None, **{**PROD, "web_allowed_emails": "sid=s@example.com"})
    assert email_for(other, other.dev_user_short, "google") is None  # never DEV_USER_EMAIL


def test_web_trusts_forwarded_for_from_the_named_proxy_only(
    set_env: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """D254 (review H1): behind caddy every client had caddy's address, so one bucket of the
    sign-in limit for the whole team. uvicorn now trusts X-Forwarded-For from caddy only."""
    import uvicorn

    seen: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: seen.update(k))
    set_env(SALEY_ENV="dev", SALEY_IN_CONTAINER="true", FORWARDED_ALLOW_IPS="172.30.0.2")
    assert runner.invoke(app, ["web"]).exit_code == 0
    assert seen["proxy_headers"] is True and seen["forwarded_allow_ips"] == "172.30.0.2"
    assert Settings.model_fields["forwarded_allow_ips"].default == "127.0.0.1"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, forwarded_allow_ips="*")


def test_two_forwarded_clients_get_two_limit_buckets() -> None:
    """The limiter keys on the client address uvicorn sets from X-Forwarded-For."""
    from saley.web.auth import RateLimiter

    limiter = RateLimiter(limit=2, window=60, now=lambda: 0.0)
    assert limiter.allow("203.0.113.1") and limiter.allow("203.0.113.1")
    assert not limiter.allow("203.0.113.1")
    assert limiter.allow("198.51.100.7")  # another person behind the same caddy


def test_port_check_skipped_in_container(
    set_env: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    import saley.cli as cli

    checked: list[int] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_refuse_busy_port", checked.append)
    set_env(SALEY_ENV="dev", SALEY_IN_CONTAINER="true")
    assert runner.invoke(app, ["web"]).exit_code == 0 and checked == []
    set_env(SALEY_ENV="dev", SALEY_IN_CONTAINER="false")
    assert runner.invoke(app, ["web"]).exit_code == 0 and len(checked) == 1


def test_bot_no_gateway_refused_in_prod(set_env: Callable[..., None]) -> None:
    set_env(SALEY_ENV="prod")
    result = runner.invoke(app, ["bot", "--no-gateway"])
    assert result.exit_code == 1 and "never the server" in result.output


def test_prod_cookie_secure_and_api_needs_session(
    synced: Session, set_env: Callable[..., None]
) -> None:
    from saley.web import auth
    from saley.web.app import create_app

    set_env(SALEY_ENV="prod")
    assert auth._secure(_request("127.0.0.1"))  # behind caddy the proxy is often loopback
    with TestClient(create_app()) as client:
        for path in ("/api/me", "/api/today", "/api/people"):
            assert client.get(path).status_code == 401, path
        assert client.post("/auth/mock").status_code == 404


# ------------------------------------------------------------------ db-roles (R6-D05)

URLS = {
    "database_url_admin": "postgresql+psycopg://saley_admin:pa@postgres:5432/saley",
    "database_url": "postgresql+psycopg://saley:pw@postgres:5432/saley",
    "database_url_ro": "postgresql+psycopg://saley_ro:pr@postgres:5432/saley",
    "postgres_password": "root-pw",
}


class FakeConn:
    def __init__(self, log: list[str], roles: set[str], dbs: set[str]) -> None:
        self.log, self.roles, self.dbs = log, roles, dbs
        self.pgconn = SimpleNamespace(
            encrypt_password=lambda pw, user, algo: b"SCRAM-SHA-256$4096:" + user + b"$" + algo
        )

    def __enter__(self) -> FakeConn:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, query: Any, params: tuple | None = None) -> Any:
        text_ = query if isinstance(query, str) else query.as_string(None)
        self.log.append(text_ if params is None else f"{text_} {params}")
        found = None
        if "from pg_roles" in text_:
            found = (1,) if params and params[0] in self.roles else None
        if "from pg_database" in text_:
            found = (1,) if params and params[0] in self.dbs else None
        return SimpleNamespace(fetchone=lambda: found)


def test_db_roles_creates_or_updates_and_runs_the_grants() -> None:
    from saley.core.roles import GRANTS_SQL, apply

    log: list[str] = []
    seen: list[str] = []

    def connect(conninfo: str) -> FakeConn:
        seen.append(conninfo)
        return FakeConn(log, roles={"saley"}, dbs=set())

    lines = apply(Settings(_env_file=None, **URLS), connect)
    assert lines == [
        "role saley_admin created",
        "role saley password set",
        "role saley_ro created",
        "database saley created",
        "grants applied",
    ]
    assert seen[0].startswith("postgresql://postgres:root-pw@postgres:5432/postgres")
    assert seen[1].endswith("@postgres:5432/saley")
    joined = "\n".join(log)
    # Only the SCRAM verifier reaches Postgres, never the password (security review M2).
    assert (
        'CREATE ROLE "saley_admin" WITH LOGIN PASSWORD \'SCRAM-SHA-256$4096:saley_admin' in joined
    )
    assert 'ALTER ROLE "saley" WITH LOGIN PASSWORD \'SCRAM-SHA-256$4096:saley$' in joined
    assert "'pa'" not in joined and "'pw'" not in joined and "'pr'" not in joined
    assert 'GRANT "saley" TO "saley_admin"' in joined
    assert 'CREATE DATABASE "saley" OWNER "saley_admin"' in joined
    assert log[-1] == GRANTS_SQL.read_text(encoding="utf-8")
    roles_sql = (Path(__file__).resolve().parents[1] / "deploy" / "sql" / "roles.sql").read_text(
        encoding="utf-8"
    )
    assert "\\ir ../../src/saley/core/sql/grants.sql" in roles_sql  # one source for the grants


def test_db_roles_refuses_bad_urls_without_printing_them(set_env: Callable[..., None]) -> None:
    from saley.core.roles import RolesRefused, apply

    cases = {
        "database_url_admin": "",
        "database_url": "postgresql+psycopg://someone:pw@postgres:5432/saley",
        "database_url_ro": "postgresql+psycopg://saley_ro:pr@other:5432/saley",
        "postgres_password": "",
    }
    for key, value in cases.items():
        with pytest.raises(RolesRefused) as err:
            apply(Settings(_env_file=None, **{**URLS, key: value}), lambda c: pytest.fail(c))
        assert "pw" not in str(err.value) and "root-pw" not in str(err.value)
    set_env(DATABASE_URL_ADMIN="")
    result = runner.invoke(app, ["db-roles"])
    assert result.exit_code == 1 and "DATABASE_URL_ADMIN is empty" in result.output


# ------------------------------------------------------------------ heartbeat (R6-H01)


def test_alive_beat_and_age(tmp_path: Path) -> None:
    clock = [1000.0]
    alive.beat("worker", tmp_path, now=lambda: clock[0])
    clock[0] += 60
    assert alive.age("worker", tmp_path, now=lambda: clock[0]) == 60
    assert alive.is_alive("worker", 120, tmp_path, now=lambda: clock[0])
    clock[0] += 120
    assert not alive.is_alive("worker", 120, tmp_path, now=lambda: clock[0])
    assert alive.age("bot", tmp_path) is None
    (tmp_path / "news").write_text("garbage", encoding="ascii")
    assert not alive.is_alive("news", folder=tmp_path)


def test_saley_alive_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alive, "ALIVE_DIR", tmp_path)
    assert runner.invoke(app, ["alive", "worker"]).exit_code == 1
    alive.beat("worker", tmp_path)
    result = runner.invoke(app, ["alive", "worker"])
    assert result.exit_code == 0 and "alive" in result.output
    assert alive.main(["worker"]) == 0  # the compose health check form
    assert alive.main(["bot"]) == 1
    assert alive.main([]) == 2


# ------------------------------------------------------------------ ping round trip (R6-S02)

BOT = "900000000000000001"


def _discord(pong: bool, calls: list[str]) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/users/@me"):
            return httpx.Response(200, json={"id": BOT})
        if request.method == "POST":
            return httpx.Response(200, json={"id": "111"})
        if request.method == "DELETE":
            return httpx.Response(204)
        found = [{"id": "222", "author": {"id": BOT}, "content": "[TEST] pong"}] if pong else []
        return httpx.Response(200, json=found)

    return egress.make_client(inner=httpx.MockTransport(handle))


@pytest.fixture
def discord_env(set_env: Callable[..., None]) -> None:
    set_env(
        DISCORD_BOT_TOKEN="t",
        DISCORD_TEST_CHANNEL_ID="100000000000000002",
        DISCORD_SALES_CHANNEL_ID="",
        TEST_MODE="true",
        EGRESS_ALLOWLIST="discord.com",
    )
    egress.reset_policy(egress.build_policy(recorder=None))


def test_ping_roundtrip_finds_pong_and_deletes_both(discord_env: None) -> None:
    from saley.bot.roundtrip import ping

    calls: list[str] = []
    result = ping(_discord(True, calls), sleep=lambda s: None)
    assert result.ok and result.case == "ping"
    assert calls.count("DELETE /api/v10/channels/100000000000000002/messages/111") == 1
    assert calls.count("DELETE /api/v10/channels/100000000000000002/messages/222") == 1


def test_ping_roundtrip_without_pong_fails_and_still_deletes(discord_env: None) -> None:
    from saley.bot.roundtrip import ping

    calls: list[str] = []
    slept: list[float] = []
    result = ping(_discord(False, calls), wait=4, sleep=slept.append)
    assert not result.matched and not result.ok and result.deleted
    assert sum(slept) == 4
    assert any(c.endswith("/messages/111") and c.startswith("DELETE") for c in calls)


# ------------------------------------------------------------------ lock heartbeat (R6-R02)


def test_lock_kept_renews_while_the_job_runs(engine: Engine, db: Session) -> None:
    from saley.worker import locks

    holder = locks.new_holder("worker:main")
    assert locks.acquire(engine, "r6_long", holder, expiry=timedelta(seconds=2))
    with locks.kept(engine, "r6_long", holder, every=timedelta(seconds=0.5)) as lost:
        time.sleep(3)  # longer than the 2 s expiry: only the renewals keep it
        assert not lost.is_set()
        assert not locks.acquire(engine, "r6_long", locks.new_holder("worker:gate"))
    locks.release(engine, "r6_long", holder)


def test_lock_kept_flags_a_lost_lock(engine: Engine, db: Session) -> None:
    from saley.worker import locks

    holder = locks.new_holder("worker:main")
    assert locks.acquire(engine, "r6_lost", holder)
    locks.release(engine, "r6_lost", holder)  # someone removed it
    with locks.kept(engine, "r6_lost", holder, every=timedelta(seconds=0.2)) as lost:
        assert lost.wait(3)


# ------------------------------------------------------------------ server CLI (R6-S01, B01)


def test_last_run_needs_a_fresh_ok_run(db: Session) -> None:
    assert runner.invoke(app, ["last-run", "sheet_sync"]).exit_code == 1
    db.execute(
        text(
            "insert into job_runs(job, status, started_at, finished_at, note) "
            "values ('sheet_sync', 'ok', now() - interval '1 hour', now() - interval '1 hour', 'x')"
        )
    )
    db.commit()
    result = runner.invoke(app, ["last-run", "sheet_sync", "--within", "15"])
    assert result.exit_code == 1 and "no ok run in the last 15 min" in result.output
    db.execute(
        text(
            "insert into job_runs(job, status, started_at, finished_at, note) "
            "values ('sheet_sync', 'ok', now(), now(), 'x')"
        )
    )
    db.commit()
    assert runner.invoke(app, ["last-run", "sheet_sync"]).exit_code == 0


def test_raw_file_sync_and_verify_refused_in_prod(
    tmp_path: Path, set_env: Callable[..., None]
) -> None:
    raw = tmp_path / "sheet.json"
    raw.write_text("{}", encoding="utf-8")
    set_env(SALEY_ENV="prod")
    for argv in (["sync", "--raw-file", str(raw)], ["sheet", "verify", "--raw-file", str(raw)]):
        result = runner.invoke(app, argv)
        assert result.exit_code == 1 and "--raw-file is for CI" in result.output, argv
    set_env(SALEY_ENV="dev")
    raw.write_text("[1, 2]", encoding="utf-8")
    result = runner.invoke(app, ["sync", "--raw-file", str(raw)])
    assert result.exit_code == 1 and "not a saved sheet" in result.output


# ------------------------------------------------------------------ the gate's CI reader (R6-G01)


@pytest.fixture
def ci_mod() -> Any:
    scripts = str(Path(__file__).resolve().parents[1] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module("verify_gate.ci")


JOBS = "https://api.github.com/repos/Trishi7/Saley/actions/runs/7/jobs"


def test_ci_step_results_and_refusals(ci_mod: Any) -> None:
    sha = "a" * 40
    run = {
        "id": 7,
        "status": "completed",
        "conclusion": "success",
        "jobs_url": JOBS,
        "html_url": "u",
    }
    steps = [
        {"name": "Set up job", "conclusion": "success"},
        {"name": "R6-C02 compose up healthy from clean (first deploy)", "conclusion": "success"},
        {"name": "R6-D04 rollback works", "conclusion": "failure"},
    ]
    pages = {JOBS: {"jobs": [{"steps": steps}]}}

    def get(url: str) -> Any:
        return pages.get(url) or {"workflow_runs": [run]}

    data = ci_mod.step_results("Trishi7/Saley", sha, get)
    assert data["steps"] == {"R6-C02": "success", "R6-D04": "failure"}
    with pytest.raises(ci_mod.CiRefused, match="push the commit"):
        ci_mod.step_results("o/r", sha, lambda url: {"workflow_runs": []})
    with pytest.raises(ci_mod.CiRefused, match="in_progress"):
        ci_mod.step_results(
            "o/r", sha, lambda url: {"workflow_runs": [{**run, "status": "in_progress"}]}
        )
    with pytest.raises(ci_mod.CiRefused, match="jobs URL"):  # review M5: GitHub only
        ci_mod.step_results(
            "o/r", sha, lambda url: {"workflow_runs": [{**run, "jobs_url": "https://evil.test/x"}]}
        )
    assert ci_mod.repo_slug("https://github.com/Trishi7/Saley") == "Trishi7/Saley"
    assert ci_mod.repo_slug("git@github.com:Trishi7/Saley.git") == "Trishi7/Saley"
    porcelain = " M docs/STATUS.md\n?? deploy/x.sh\n M src/saley/cli.py\n"
    assert ci_mod.dirty_deploy_paths(porcelain) == ["deploy/x.sh", "src/saley/cli.py"]


def test_matrix_ci_rows() -> None:
    row = matrix.Row("R6", "R6-C02", "F-R6-02", "guardrails", "w", "ci", "ci R6-C02", "p")
    other = matrix.Row("R6", "R6-D04", "F-R6-06", "guardrails", "w", "ci", "ci R6-D04", "p")
    out = matrix.evaluate([row, other], {}, {}, {}, ci={"R6-C02": "success", "R6-D04": "failure"})
    assert [o.ok for o in out] == [True, False]
    none = matrix.evaluate([row], {}, {}, {}, ci=None)[0]
    assert not none.ok and "--ci" in none.note
    assert matrix.row_problem({**_raw(row), "command": "ci R6-X99"}) == (
        "a ci row runs `ci <its own row id>`"
    )
    assert matrix.row_problem(_raw(row)) is None


def _raw(row: matrix.Row) -> dict[str, str]:
    return {
        "id": row.id,
        "feature": row.feature,
        "layer": row.layer,
        "what": row.what,
        "tool": row.tool,
        "command": row.command,
        "pass": row.pass_rule,
    }
