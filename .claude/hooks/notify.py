"""Ping a private Discord channel when Claude Code needs the human or finishes a turn.

Why: the human is not always at the laptop. Claude Code fires the Notification hook when it
waits for permission or input, and the Stop hook when it finishes. This script posts a short
line to a Discord webhook. The webhook URL is read from the environment variable
SALEY_DEV_WEBHOOK (set it in your shell profile or VS Code terminal env, never commit it).
No URL -> the script exits quietly. Never raises: a failed ping must not break Claude Code.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request


def main() -> int:
    url = os.environ.get("SALEY_DEV_WEBHOOK", "").strip()
    if not url:
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    if "--release" in sys.argv:
        i = sys.argv.index("--release")
        rel = sys.argv[i + 1] if len(sys.argv) > i + 1 else "?"
        line = " ".join(sys.argv[i + 2:]) or "built, tested, verified"
        text = f"🚀 Saley release {rel} ready in `{os.path.basename(os.getcwd())}`: {line}. Review and commit."
        body = json.dumps({"content": text[:1900]}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            pass
        return 0
    stop = "--stop" in sys.argv
    cwd = os.path.basename(os.getcwd())
    if stop:
        text = f"✅ Claude Code finished a turn in `{cwd}`. Check VS Code or Remote Control."
    else:
        msg = payload.get("message") or payload.get("title") or "needs your input"
        text = f"🔔 Claude Code in `{cwd}`: {msg}"
    body = json.dumps({"content": text[:1900]}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
