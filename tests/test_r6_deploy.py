"""R6: the server files (compose, Dockerfile, Caddyfile, the deploy scripts, smoke.py, the CI
compose workflow).

Why read and stub, not run: the laptop has no Docker (Q-R6-docker, D241). The rules that matter
are visible in the files, and the scripts' control flow (order, rollback, idempotent re-run)
runs here with stub `docker`, `git` and `python3` commands that log what they were asked.
The real stack runs in `.github/workflows/compose.yml`; the gate reads it with --ci.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
LIMITS = {"postgres": "512m", "web": "384m", "worker": "512m", "bot": "256m", "news": "384m"}
LIMITS["caddy"] = "64m"


def compose() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def services() -> dict[str, Any]:
    return compose()["services"]


# ------------------------------------------------------------------ compose (R6-C01)


def test_compose_prod_profile_limits_health_logs_and_ports() -> None:
    """R6-C01: six services with their memory limits, restart, health checks, json-file logs
    20 MB x 3, a named postgres volume, and only caddy publishes ports."""
    svc = services()
    assert set(LIMITS) <= set(svc)
    for name, limit in LIMITS.items():
        s = svc[name]
        assert s["mem_limit"] == limit and s["memswap_limit"] == limit, name
        assert s["restart"] == "unless-stopped", name
        assert s["healthcheck"]["test"], name
        assert s["logging"] == {
            "driver": "json-file",
            "options": {"max-size": "20m", "max-file": "3"},
        }
        if name != "postgres":
            assert s["profiles"] == ["prod"], name
        published = s.get("ports", [])
        assert (published != []) == (name == "caddy"), f"{name} publishes {published}"
    assert svc["caddy"]["ports"] == ["${HTTP_PORT:-80}:80", "${HTTPS_PORT:-443}:${HTTPS_PORT:-443}"]
    assert "pgdata:/var/lib/postgresql/data" in svc["postgres"]["volumes"]
    # D254: caddy has a fixed address and the web trusts X-Forwarded-For from it alone.
    assert svc["caddy"]["networks"]["saley_net"]["ipv4_address"] == "172.30.0.2"
    assert svc["web"]["environment"]["FORWARDED_ALLOW_IPS"] == "172.30.0.2"
    # Review H2: a missing key file fails the start, never becomes an empty folder.
    key = svc["worker"]["volumes"][0]
    assert key["read_only"] is True and key["bind"]["create_host_path"] is False
    assert "pgdata" in compose()["volumes"]
    for name in ("worker", "bot", "news"):
        assert svc[name]["healthcheck"]["test"] == ["CMD", "python", "-m", "saley.core.alive", name]


def test_web_waits_for_postgres_and_migrations_and_holds_no_rw_secret() -> None:
    svc = services()
    web = svc["web"]
    assert web["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert web["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert svc["migrate"]["command"] == ["saley", "db-upgrade"]
    assert svc["migrate"]["restart"] == "no"
    env = web["environment"]
    for secret in ("DATABASE_URL", "DATABASE_URL_ADMIN", "POSTGRES_PASSWORD", "DISCORD_BOT_TOKEN",
                   "ANTHROPIC_API_KEY", "GOOGLE_SERVICE_ACCOUNT_FILE"):  # fmt: skip
        assert env[secret] == "", secret
    assert "/healthz" in " ".join(web["healthcheck"]["test"])
    for name in ("bot", "news"):  # review M3: keys they never use
        for key in (
            "MAIL_APP_PASSWORD",
            "SMTP_APP_PASSWORD",
            "APOLLO_API_KEY",
            "WEB_SESSION_SECRET",
        ):
            assert svc[name]["environment"][key] == "", (name, key)
    for name in ("web", "worker", "bot", "news"):
        assert svc[name]["environment"]["SALEY_ENV"] == "prod"
        assert svc[name]["environment"]["DATABASE_URL_ADMIN"] == "", name
    # The image is built once (migrate) and never pulled from a registry.
    assert [n for n, s in svc.items() if "build" in s] == ["migrate"]
    assert all(
        s["pull_policy"] == "never" for n, s in svc.items() if s.get("image") == "saley:current"
    )


def test_ci_overlay_is_ci_only() -> None:
    overlay = yaml.safe_load((DEPLOY / "compose.ci.yml").read_text(encoding="utf-8"))["services"]
    assert {s["environment"]["SALEY_ENV"] for s in overlay.values()} == {"ci"}
    assert {s["environment"]["SALEY_CI_RUNNER"] for s in overlay.values()} == {"true"}
    assert overlay["bot"]["command"] == ["saley", "bot", "--no-gateway"]
    assert "compose.ci.yml" not in (DEPLOY / "deploy.sh").read_text(encoding="utf-8")


# ------------------------------------------------------------------ Dockerfile, Caddyfile


def test_dockerfile_runtime_non_root_no_dev_deps_in_container_flag() -> None:
    """R6-C03."""
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.12-slim" in text and "USER saley" in text
    assert text.count("--no-dev") == 2
    assert "SALEY_IN_CONTAINER=true" in text
    run_stage = text.rsplit("FROM ", 1)[1]
    assert "npm" not in run_stage


def test_caddyfile_proxies_public_url_with_hsts_and_leaves_app_headers() -> None:
    """R6-C04."""
    text = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "email {$ACME_EMAIL}" in body
    assert "{$PUBLIC_URL} {" in body and "reverse_proxy web:8090" in body
    assert 'header Strict-Transport-Security "max-age=31536000"' in body
    for app_header in ("Content-Security-Policy", "X-Frame-Options", "Referrer-Policy"):
        assert app_header not in body
    caddy = services()["caddy"]["environment"]
    assert caddy["ACME_EMAIL"].startswith("${OPENALEX_MAILTO:?")
    assert caddy["PUBLIC_URL"].startswith("${PUBLIC_URL:?")


# ------------------------------------------------------------------ scripts with stubs

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(
    BASH is None or "system32" in BASH.lower(), reason="needs a real bash (never WSL)"
)

STUB_DOCKER = r"""#!/usr/bin/env bash
echo "docker $*" >>"$STUB_LOG"
case "$1 $2" in
"image inspect")
	case "$*" in
	*saley:current*) [ -n "${STUB_PREV_ID:-}" ] && echo "$STUB_PREV_ID" && exit 0; exit 1 ;;
	*) exit 0 ;;
	esac ;;
"network inspect")
	case "$*" in *Subnet*) echo "172.30.0.0/24" ;; esac
	exit 0 ;;
esac
if [ "$1" = inspect ]; then echo healthy; exit 0; fi
if [ "$1" = compose ]; then
	if [ -n "${STUB_FAIL:-}" ] && [[ "$*" == *"$STUB_FAIL"* ]]; then exit 1; fi
	case "$*" in
	*" ps -q "*) echo "cid-${@: -1}" ;;
	*pg_dump*) echo "-- dump" ;;
	esac
fi
exit 0
"""
STUB_GIT = """#!/usr/bin/env bash
echo "git $*" >>"$STUB_LOG"
[ "$1" = rev-parse ] && echo abc123def456
exit 0
"""
STUB_PYTHON = """#!/usr/bin/env bash
echo "python3 $*" >>"$STUB_LOG"
exit "${STUB_SMOKE:-0}"
"""


def posix(path: Path) -> str:
    text = path.as_posix()
    if re.match(r"^[A-Za-z]:/", text):
        return f"/{text[0].lower()}{text[2:]}"
    return text


@pytest.fixture
def server(tmp_path: Path) -> dict[str, Any]:
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (("docker", STUB_DOCKER), ("git", STUB_GIT), ("python3", STUB_PYTHON)):
        (stubs / name).write_text(body, encoding="utf-8", newline="\n")
        (stubs / name).chmod(0o755)
    app = tmp_path / "app"
    app.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+psycopg://saley:x@postgres:5432/saley\nBACKUP_S3_URL=\n",
        encoding="utf-8",
    )
    env = {
        "SALEY_APP_DIR": app.as_posix(),
        "SALEY_ENV_FILE": env_file.as_posix(),
        "SALEY_BACKUP_DIR": (tmp_path / "backups").as_posix(),
        "SALEY_STATE_DIR": (tmp_path / "state").as_posix(),
        "SALEY_HEALTH_WAIT": "5",
        "STUB_LOG": (tmp_path / "calls.log").as_posix(),
        "GOOGLE_SERVICE_ACCOUNT_HOST_FILE": (tmp_path / "google-sa.json").as_posix(),
        "SALEY_SKIP_MODE_CHECK": "1",  # NTFS has no Unix modes; the server checks 600
    }
    (tmp_path / "google-sa.json").write_text("{}", encoding="utf-8")
    return {"tmp": tmp_path, "stubs": posix(stubs), "env": env, "env_file": env_file}


def run_script(
    server: dict[str, Any], script: str, *args: str, **extra: str
) -> tuple[int, str, list[str]]:
    env = {**os.environ, **server["env"], **extra}
    command = f'export PATH="{server["stubs"]}:$PATH"; exec "{posix(DEPLOY / script)}" "$@"'
    proc = subprocess.run(  # noqa: S603 - our own script with stub tools
        [BASH, "-c", command, script, *args],
        env=env, capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    log = Path(server["env"]["STUB_LOG"])
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return proc.returncode, proc.stdout + proc.stderr, calls


def step_order(calls: list[str]) -> list[str]:
    marks = {
        "build migrate": "build",
        "up -d --wait postgres": "postgres",  # review HIGH: healthy before db-roles
        "run --rm --no-deps migrate saley db-roles": "roles",
        "pg_dump": "backup",
        "run --rm --no-deps migrate": "migrate",
        "up -d --remove-orphans": "up",
        "python3 ": "smoke",
    }
    out = []
    for call in calls:
        for needle, mark in marks.items():
            if needle in call and not (mark == "migrate" and "db-roles" in call):
                out.append(mark)
                break
    return out


@needs_bash
def test_deploy_runs_the_steps_in_order_and_says_deploy_ok(server: dict[str, Any]) -> None:
    """R6-D02: pull -> build -> roles -> backup -> migrate -> roles -> up -> smoke -> ok."""
    code, out, calls = run_script(server, "deploy.sh", STUB_PREV_ID="sha256:old")
    assert code == 0, out
    assert "deploy ok abc123def456" in out
    assert calls[0] == "git pull --ff-only"
    assert step_order(calls) == [
        "build",
        "postgres",
        "roles",
        "backup",
        "migrate",
        "roles",
        "up",
        "smoke",
    ]
    assert "docker tag saley:current saley:abc123def456" in calls
    backups = list((server["tmp"] / "backups").glob("saley-*-pre-abc123def456.sql.gz"))
    assert len(backups) == 1
    assert (server["tmp"] / "state" / "current").read_text().strip() == "abc123def456"


@needs_bash
def test_deploy_rolls_back_to_the_previous_image_and_says_so(server: dict[str, Any]) -> None:
    code, out, calls = run_script(
        server, "deploy.sh", "--no-pull", STUB_PREV_ID="sha256:old", STUB_SMOKE="1"
    )
    assert code != 0
    assert "docker tag sha256:old saley:current" in calls
    assert "ROLLED BACK" in out and "deploy of abc123def456 failed: smoke" in out
    assert not any(c.startswith("git pull") for c in calls)


@needs_bash
def test_a_failed_migration_rolls_back_before_compose_up(server: dict[str, Any]) -> None:
    code, out, calls = run_script(
        server, "deploy.sh", "--no-pull", STUB_PREV_ID="sha256:old", STUB_FAIL="db-roles"
    )
    assert code != 0 and "failed: db-roles" in out
    order = step_order(calls)
    assert "migrate" not in order and "backup" not in order  # stopped at the roles step
    assert "docker tag sha256:old saley:current" in calls


@needs_bash
def test_deploy_stops_early_without_the_key_file(server: dict[str, Any]) -> None:
    """Review H2: a plain message before anything changes, never a folder made by Docker."""
    (server["tmp"] / "google-sa.json").unlink()
    code, out, calls = run_script(server, "deploy.sh", "--no-pull")
    assert code != 0 and "no service account key" in out
    assert not any(c.startswith("docker compose") for c in calls)


@needs_bash
def test_first_deploy_failure_has_nothing_to_roll_back(server: dict[str, Any]) -> None:
    code, out, _ = run_script(server, "deploy.sh", "--no-pull", STUB_SMOKE="1")
    assert code != 0 and "nothing to roll back to" in out


@needs_bash
def test_deploy_with_an_image_skips_the_build(server: dict[str, Any]) -> None:
    code, out, calls = run_script(
        server, "deploy.sh", "--no-pull", "--image", "saley:broken", "--skip", "bot"
    )
    assert code == 0, out
    assert "docker tag saley:broken saley:current" in calls
    assert "build" not in step_order(calls)
    assert any(c.startswith("python3 ") and c.endswith("smoke.py --skip bot") for c in calls)


@needs_bash
def test_backup_writes_gzip_keeps_14_days_and_refuses_an_s3_url(server: dict[str, Any]) -> None:
    """R6-B01."""
    backups = server["tmp"] / "backups"
    backups.mkdir()
    old = backups / "saley-20200101-000000.sql.gz"
    old.write_bytes(b"")
    os.utime(old, (0, 0))
    code, out, calls = run_script(server, "backup.sh")
    assert code == 0, out
    assert any("pg_dump -U postgres --no-password -d saley" in c for c in calls)
    made = [p for p in backups.glob("saley-*.sql.gz") if p != old]
    assert len(made) == 1 and "backup ok" in out
    assert not old.exists()  # older than 14 days
    with server["env_file"].open("a", encoding="utf-8") as f:
        f.write("BACKUP_S3_URL=s3://bucket/x\n")
    code, out, _ = run_script(server, "backup.sh")
    assert code != 0 and "off-server copy is not built" in out


@needs_bash
def test_restore_uses_a_scratch_database_and_runs_sheet_verify(server: dict[str, Any]) -> None:
    backups = server["tmp"] / "backups"
    backups.mkdir()
    code, out, _ = run_script(server, "backup.sh")
    assert code == 0, out
    code, out, calls = run_script(server, "restore.sh", "--raw-file", "/tmp/sheet.json")
    assert code == 0, out
    joined = "\n".join(calls)
    assert "CREATE DATABASE saley_restore_check OWNER saley_admin" in joined
    assert "-d saley_restore_check" in joined and " -d saley " not in joined
    verify = [c for c in calls if "sheet verify" in c]
    assert verify and "--database saley_restore_check" in verify[0]
    assert "-e SALEY_ENV=dev -e AUTH_PROVIDER=mock" in verify[0]
    assert "restore ok: sheet verify PASS" in out
    assert joined.rstrip().endswith("DROP DATABASE IF EXISTS saley_restore_check WITH (FORCE)")


@needs_bash
def test_install_dry_run_prints_every_step() -> None:
    """R6-D01: ufw 22/80/443 + deny, fail2ban, folders, the network, systemd, the cron."""
    proc = subprocess.run(  # noqa: S603
        [BASH, posix(DEPLOY / "install.sh"), "--dry-run"],
        capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    out = proc.stdout
    assert proc.returncode == 0, proc.stderr
    for want in (
        "ufw default deny incoming",
        "ufw allow 22/tcp",
        "ufw allow 80/tcp",
        "ufw allow 443/tcp",
        "ufw --force enable",
        "fail2ban",
        "install -d -m 700 /srv/saley/secrets",
        "install -d -m 700 /srv/saley/backups",
        "docker network create --subnet 172.30.0.0/24 saley_net",
        "would run: install -d -m 700 /srv/saley\n",
        "--profile prod up -d",
        "systemctl enable saley.service",
        "0 21 * * * root /bin/bash /srv/saley/app/deploy/backup.sh",
    ):
        assert want in out, want


@needs_bash
def test_every_script_parses() -> None:
    for script in sorted(DEPLOY.glob("*.sh")):
        proc = subprocess.run(  # noqa: S603
            [BASH, "-n", posix(script)], capture_output=True, text=True, check=False
        )
        assert proc.returncode == 0, f"{script.name}: {proc.stderr}"


# ------------------------------------------------------------------ smoke.py (R6-S01)


def smoke_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("deploy_smoke", DEPLOY / "smoke.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def test_smoke_checks_health_signin_api_sheet_and_bot() -> None:
    smoke = smoke_module()
    answers = {"/healthz": (200, b'{"status": "ok"}'), "/signin": (200, b""), "/api/me": (401, b"")}
    get = lambda url: answers["/" + url.split("/", 3)[3]]  # noqa: E731
    commands: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        commands.append(argv)
        return 0, "ok line"

    results = smoke.smoke("https://saley.example", set(), get, run)
    assert [(r.name, r.ok) for r in results] == [
        ("healthz", True), ("signin", True), ("api", True), ("sheet", True), ("bot", True)
    ]  # fmt: skip
    assert commands[0][-5:] == ["saley", "last-run", "sheet_sync", "--within", "15"]
    assert commands[1][-7:] == ["bot", "saley", "discord", "roundtrip", "--test", "--case", "ping"]
    bad = {**answers, "/api/me": (200, b"{}")}
    results = smoke.smoke(
        "https://x", {"sheet", "bot"}, lambda u: bad["/" + u.split("/", 3)[3]], run
    )
    assert [r.name for r in results if not r.ok] == ["api"]


def test_smoke_sheet_waits_then_fails() -> None:
    smoke = smoke_module()
    slept: list[float] = []
    result = smoke.check_sheet(lambda argv: (1, "sheet_sync: no ok run"), sleep=slept.append)
    assert not result.ok and sum(slept) == smoke.SHEET_WAIT_SECONDS


def test_smoke_main_refuses_unknown_checks_and_empty_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    smoke = smoke_module()
    with pytest.raises(SystemExit):
        smoke.main(["--skip", "nope"])
    empty = tmp_path / ".env"
    empty.write_text("PUBLIC_URL=\n", encoding="utf-8")
    monkeypatch.setattr(smoke, "ENV_FILE", empty)
    assert smoke.env_value("PUBLIC_URL", empty) == ""


# ------------------------------------------------------------------ the CI workflow (R6-G01)


def test_compose_workflow_has_one_step_per_ci_row() -> None:
    flow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "compose.yml").read_text(encoding="utf-8")
    )
    names = [s.get("name", "") for s in flow["jobs"]["compose"]["steps"]]
    rows = yaml.safe_load((ROOT / "tests" / "matrix" / "R6.yaml").read_text(encoding="utf-8"))[
        "rows"
    ]
    ci_rows = [r["id"] for r in rows if r["tool"] == "ci"]
    assert ci_rows, "R6 has ci rows"
    for row_id in ci_rows:
        assert sum(1 for n in names if n.startswith(f"{row_id} ")) == 1, row_id
    text = json.dumps(flow)
    assert "secrets." not in text  # CI needs no secret
    assert "compose.ci.yml" in text and "--skip" in text


@needs_bash
def test_a_set_s3_url_does_not_fail_a_deploy(server: dict[str, Any]) -> None:
    """Review M4: the pre-migration backup is local only; a config value never rolls back."""
    with server["env_file"].open("a", encoding="utf-8") as f:
        f.write("BACKUP_S3_URL=s3://bucket/x\n")
    code, out, _ = run_script(server, "deploy.sh", "--no-pull", STUB_PREV_ID="sha256:old")
    assert code == 0 and "deploy ok abc123def456" in out, out


def test_workflow_job_env_uses_only_allowed_contexts() -> None:
    """The first R6 push: `${{ runner.temp }}` in a job-level env made GitHub reject the whole
    compose workflow (a run with 0 jobs). Job env may use github, inputs, matrix, needs,
    secrets, strategy and vars only (actionlint rule)."""
    allowed = {"github", "inputs", "matrix", "needs", "secrets", "strategy", "vars"}
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        flow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, job in flow["jobs"].items():
            for key, value in (job.get("env") or {}).items():
                used = set(re.findall(r"\$\{\{\s*([a-z_]+)\.", str(value)))
                assert used <= allowed, f"{path.name} {name} env {key}: {used - allowed}"
