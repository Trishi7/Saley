#!/usr/bin/env python3
"""Read-only smoke checks after a deploy (R6). deploy.sh runs it; you can run it any time:

    sudo python3 /srv/saley/app/deploy/smoke.py

Checks, in order (each prints OK or FAIL and why):
  healthz   GET <PUBLIC_URL>/healthz answers 200 {"status": "ok"}
  signin    GET <PUBLIC_URL>/signin answers 200
  api       GET <PUBLIC_URL>/api/me without a session answers 401
  sheet     the worker finished one sheet sync in the last 15 minutes (waits up to 3 min)
  bot       the running bot answers "@Saley ping" with "pong" in the TEST channel (D250)

It changes no data. The bot check posts one ping to the TEST channel and deletes the ping and
the pong again. Python standard library only: it runs on the server's own python3, outside the
containers; the sheet and bot checks run their command inside the containers.

Options: --skip sheet,bot (CI has no sheet and no Discord, D249, D247). For an https://localhost
address (CI) it accepts Caddy's local certificate; for any other host the certificate must be
valid.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

APP_DIR = Path(os.environ.get("SALEY_APP_DIR", "/srv/saley/app"))
ENV_FILE = Path(os.environ.get("SALEY_ENV_FILE", "/srv/saley/.env"))
COMPOSE_FILES = os.environ.get("SALEY_COMPOSE_FILES", "docker-compose.yml").split()
CHECKS = ("healthz", "signin", "api", "sheet", "bot")
LOCAL = ("localhost", "127.0.0.1")
HTTP_TIMEOUT = 15
SHEET_WAIT_SECONDS = 180
SHEET_POLL_SECONDS = 10
SHEET_WITHIN_MINUTES = 15


@dataclass(frozen=True)
class Result:
    name: str
    ok: bool
    detail: str


def env_value(name: str, path: Path = ENV_FILE) -> str:
    """The last KEY=value of the env file, quotes removed. Used for PUBLIC_URL only."""
    value = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            value = line.split("=", 1)[1].strip().strip("'\"")
    return value


def compose_argv(*args: str) -> list[str]:
    files = [part for f in COMPOSE_FILES for part in ("-f", str(APP_DIR / f))]
    return [
        "docker",
        "compose",
        "--project-directory",
        str(APP_DIR),
        *files,
        "--env-file",
        str(ENV_FILE),
        "--profile",
        "prod",
        *args,
    ]


def http_get(url: str, insecure: bool) -> tuple[int, bytes]:
    """Status and body. A 4xx or 5xx is an answer here, not an exception."""
    context = ssl.create_default_context()
    if insecure:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("http or https only")
    request = urllib.request.Request(url, headers={"User-Agent": "saley-smoke"})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT, context=context) as answer:  # noqa: S310
            return answer.status, answer.read(4096)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(4096)


Get = Callable[[str], tuple[int, bytes]]
Run = Callable[[list[str]], tuple[int, str]]


def run_command(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def check_http(base: str, get: Get) -> list[Result]:
    out = []
    try:
        status, body = get(f"{base}/healthz")
        ok = status == 200 and json.loads(body or b"{}").get("status") == "ok"
        out.append(Result("healthz", ok, f"HTTP {status}"))
    except (OSError, ValueError) as exc:
        out.append(Result("healthz", False, f"no answer ({exc.__class__.__name__})"))
    for name, path, want in (("signin", "/signin", 200), ("api", "/api/me", 401)):
        try:
            status, _ = get(f"{base}{path}")
            out.append(Result(name, status == want, f"HTTP {status}, want {want}"))
        except OSError as exc:
            out.append(Result(name, False, f"no answer ({exc.__class__.__name__})"))
    return out


def check_sheet(run: Run, sleep: Callable[[float], None] = time.sleep) -> Result:
    argv = compose_argv(
        "exec", "-T", "worker", "saley", "last-run", "sheet_sync",
        "--within", str(SHEET_WITHIN_MINUTES),
    )  # fmt: skip
    waited = 0
    while True:
        code, text = run(argv)
        last = (text.splitlines() or [""])[-1]
        if code == 0:
            return Result("sheet", True, last)
        if waited >= SHEET_WAIT_SECONDS:
            return Result("sheet", False, last)
        sleep(SHEET_POLL_SECONDS)
        waited += SHEET_POLL_SECONDS


def check_bot(run: Run) -> Result:
    argv = compose_argv(
        "exec", "-T", "bot", "saley", "discord", "roundtrip", "--test", "--case", "ping"
    )  # fmt: skip
    code, text = run(argv)
    return Result("bot", code == 0, (text.splitlines() or [""])[-1])


def smoke(base: str, skip: set[str], get: Get, run: Run) -> list[Result]:
    results = [r for r in check_http(base, get) if r.name not in skip]
    if "sheet" not in skip:
        results.append(check_sheet(run))
    if "bot" not in skip:
        results.append(check_bot(run))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Saley smoke checks (read-only).")
    parser.add_argument("--skip", default="", help="comma list of: " + ", ".join(CHECKS))
    args = parser.parse_args(argv)
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    unknown = skip - set(CHECKS)
    if unknown:
        parser.error(f"unknown check(s): {', '.join(sorted(unknown))}")
    base = env_value("PUBLIC_URL").rstrip("/")
    if not base:
        print(f"FAIL: PUBLIC_URL is empty in {ENV_FILE}")
        return 1
    local = urlsplit(base).hostname in LOCAL
    results = smoke(base, skip, lambda url: http_get(url, local), run_command)
    for r in results:
        print(f"{'OK  ' if r.ok else 'FAIL'} {r.name}: {r.detail}")
    for name in sorted(skip):
        print(f"SKIP {name}")
    failed = [r.name for r in results if not r.ok]
    print("smoke ok" if not failed else f"smoke FAIL: {', '.join(failed)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
