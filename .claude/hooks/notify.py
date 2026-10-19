"""Ping a private Discord channel when Claude Code needs the human or finishes a release.

Why: the human is not always at the laptop. Claude Code fires the Notification hook when it
waits for permission or input. The release ping is run by hand at the end of a release.
The webhook URL comes from the environment variable SALEY_DEV_WEBHOOK (user environment,
never .env, never the repo). No URL -> exit quietly. Errors go to stderr but never fail the hook.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

HEADERS = {"Content-Type": "application/json", "User-Agent": "saley-dev-hook/1.0"}


def post(url: str, text: str) -> None:
    body = json.dumps({"content": text[:1900]}).encode()
    req = urllib.request.Request(url, data=body, headers=HEADERS)
    try:
        urllib.request.urlopen(req, timeout=8).read()
    except Exception as exc:  # never break Claude Code
        print(f"notify.py: {exc}", file=sys.stderr)


def main() -> int:
    url = os.environ.get("SALEY_DEV_WEBHOOK", "").strip()
    if not url:
        print("notify.py: SALEY_DEV_WEBHOOK not set", file=sys.stderr)
        return 0
    cwd = os.path.basename(os.getcwd())
    if "--release" in sys.argv:
        i = sys.argv.index("--release")
        rel = sys.argv[i + 1] if len(sys.argv) > i + 1 else "?"
        line = " ".join(sys.argv[i + 2:]) or "built, tested, verified"
        post(url, f"🚀 Saley release {rel} ready in `{cwd}`: {line}. Review and commit.")
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    msg = payload.get("message") or payload.get("title") or "needs your input"
    post(url, f"🔔 Claude Code in `{cwd}`: {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
