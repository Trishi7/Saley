"""`verify.py --ci` (R6, D241): the compose checks come from GitHub Actions.

Why: the laptop has no Docker (Q-R6-docker). The workflow `.github/workflows/compose.yml`
proves the server stack on a fresh runner, one step per matrix row (the step name starts with
the row id). This module reads the step results of that workflow for HEAD over the public
GitHub API (no token; the repository is public) and saves them as
docs/test-reports/<release>-compose.json. Matrix rows of tool `ci` read that file.

The result belongs to HEAD, so the tree must be HEAD: a changed file outside docs/ refuses the
result ("commit and push first"). Gate tooling, not Saley code: it is not under src/, so the
app's egress rule (saley.core.egress) does not apply; it calls api.github.com only.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from verify_gate.base import ROOT
from verify_gate.host import V

WORKFLOW = "compose.yml"
GIT = shutil.which("git") or "git"
API = "https://api.github.com"
TIMEOUT = 20
ROW_ID = re.compile(r"^(R[\d.]+-[A-Z]+\d+)\b")
# Paths a commit can change without changing what the compose run proved.
NOT_DEPLOY = ("docs/",)

GetJson = Callable[[str], Any]


class CiRefused(RuntimeError):
    pass


def compose_path(release: str) -> Path:
    return V.REPORTS / f"{release}-compose.json"


def _git(*args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed git arguments
        [GIT, *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.rstrip("\n")  # never lstrip: porcelain lines start with a status column


def repo_slug(remote: str) -> str:
    """owner/name from an https or ssh GitHub remote."""
    found = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", remote)
    if not found:
        raise CiRefused(f"origin is not a GitHub remote: {remote}")
    return found.group(1)


def dirty_deploy_paths(porcelain: str) -> list[str]:
    """Changed paths outside docs/. A rename `R a -> b` counts both paths."""
    paths = []
    for line in porcelain.splitlines():
        if len(line) > 3:
            paths += [p.strip().strip('"') for p in line[3:].split(" -> ")]
    return [p for p in paths if not p.startswith(NOT_DEPLOY)]


def get_json(url: str) -> Any:
    request = urllib.request.Request(  # noqa: S310 - fixed https host
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "saley-gate"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:  # noqa: S310
        return json.loads(answer.read())


def step_results(slug: str, sha: str, get: GetJson = get_json) -> dict[str, Any]:
    """{run: {...}, steps: {row id: conclusion}} of the newest compose run for this commit."""
    runs = get(f"{API}/repos/{slug}/actions/workflows/{WORKFLOW}/runs?head_sha={sha}&per_page=5")
    found = runs.get("workflow_runs") or []
    if not found:
        raise CiRefused(f"no {WORKFLOW} run for {sha[:12]}: push the commit and wait for it")
    run = found[0]
    if run.get("status") != "completed":
        raise CiRefused(f"the {WORKFLOW} run for {sha[:12]} is {run.get('status')}: wait for it")
    jobs_url = str(run.get("jobs_url", ""))
    if not jobs_url.startswith(f"{API}/"):
        raise CiRefused(f"the run has no GitHub jobs URL ({jobs_url[:60]!r})")
    jobs = get(jobs_url)
    steps: dict[str, str] = {}
    for job in jobs.get("jobs", []):
        for step in job.get("steps", []):
            match = ROW_ID.match(str(step.get("name", "")))
            if match:
                # Two steps with one id (a retry, a second job): the worst result counts.
                got = str(step.get("conclusion") or step.get("status"))
                if steps.get(match.group(1), "success") == "success":
                    steps[match.group(1)] = got
    return {
        "sha": sha,
        "run": {k: run.get(k) for k in ("id", "html_url", "conclusion", "updated_at")},
        "steps": steps,
    }


def collect(release: str, get: GetJson = get_json) -> tuple[bool, str]:
    """Save the compose results for HEAD. (ok, note) for the console; never raises."""
    path = compose_path(release)
    path.unlink(missing_ok=True)
    try:
        dirty = dirty_deploy_paths(_git("status", "--porcelain"))
        if dirty:
            raise CiRefused(
                f"the tree differs from HEAD ({', '.join(dirty[:3])}): commit and push first"
            )
        data = step_results(
            repo_slug(_git("remote", "get-url", "origin")), _git("rev-parse", "HEAD"), get
        )
    except (CiRefused, OSError, ValueError, subprocess.CalledProcessError) as exc:
        return False, f"compose results not read: {exc}"
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    good = sum(1 for c in data["steps"].values() if c == "success")
    return True, f"compose run {data['run']['id']}: {good} of {len(data['steps'])} row steps passed"


def load(release: str) -> dict[str, str] | None:
    path = compose_path(release)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("steps", {})
    except (ValueError, AttributeError):
        return None  # the ci rows then read "no CI result"
