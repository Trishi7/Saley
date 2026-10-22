"""Memory under the limits for 10 minutes of load (R6-M01, D251).

    uv run python scripts/ci/load_memory.py https://localhost:8443 --minutes 10

Eight threads sign in once (mock, SALEY_ENV=ci) and then GET the dashboard and its API routes
through caddy, again and again, for the whole time. Every 10 seconds `docker stats` gives the
memory of each container. Pass when, for every service of the stack:
- the highest memory stays under 90 % of its limit;
- the container was never killed for memory (OOMKilled false) and never restarted;
- under 1 % of the requests failed (a 5xx or no answer).
A table of the peaks is printed. https://localhost only.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

LOCAL = ("localhost", "127.0.0.1")
DOCKER = shutil.which("docker") or "docker"
THREADS = 8
SAMPLE_SECONDS = 10
MAX_SHARE = 0.90
MAX_FAILED_SHARE = 0.01
PROJECT = "saley"  # docker-compose.yml `name:`
SERVICES = ("postgres", "web", "worker", "bot", "news", "caddy")
PATHS = (
    "/",
    "/healthz",
    "/api/me",
    "/api/today",
    "/api/goals",
    "/api/accounts",
    "/api/people",
    "/api/signals",
    "/api/events",
    "/api/commitments",
    "/api/decisions",
    "/api/blockers",
    "/api/status",
    "/api/news/funnel",
    "/api/pack",
    "/api/scorecard",
)
UNITS = {
    "b": 1,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
}
SIZE = re.compile(r"([\d.]+)\s*([kmg]?i?b)", re.I)


def size_bytes(text: str) -> float:
    match = SIZE.fullmatch(text.strip())
    if not match:
        raise ValueError(f"not a size: {text!r}")
    return float(match.group(1)) * UNITS[match.group(2).lower()]


@dataclass
class Tally:
    ok: int = 0
    failed: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, good: bool) -> None:
        with self.lock:
            if good:
                self.ok += 1
            else:
                self.failed += 1


def worker(base: str, stop: threading.Event, tally: Tally) -> None:
    with httpx.Client(verify=False, timeout=20, follow_redirects=False) as client:  # noqa: S501 - localhost only
        client.post(f"{base}/auth/mock")
        i = 0
        while not stop.is_set():
            path = PATHS[i % len(PATHS)]
            i += 1
            try:
                status = client.get(f"{base}{path}").status_code
            except httpx.HTTPError:
                tally.add(False)
                continue
            tally.add(status < 500)


def container_ids() -> dict[str, str]:
    out = subprocess.run(  # noqa: S603
        [DOCKER, "ps", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}"],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    ids = {}
    for cid in out:
        service = subprocess.run(  # noqa: S603
            [DOCKER, "inspect", "-f", '{{index .Config.Labels "com.docker.compose.service"}}', cid],
            capture_output=True, text=True, check=True,
        ).stdout.strip()  # fmt: skip
        ids[service] = cid
    return ids


def sample(ids: dict[str, str]) -> dict[str, tuple[float, float]]:
    """service -> (used bytes, limit bytes) from one `docker stats --no-stream`."""
    out = subprocess.run(  # noqa: S603
        [DOCKER, "stats", "--no-stream", "--format", "{{.ID}}\t{{.MemUsage}}", *ids.values()],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    by_id = {cid[:12]: name for name, cid in ids.items()}
    got = {}
    for line in out.splitlines():
        cid, usage = line.split("\t", 1)
        used, limit = (part.strip() for part in usage.split("/"))
        got[by_id.get(cid[:12], cid)] = (size_bytes(used), size_bytes(limit))
    return got


def state(cid: str) -> dict:
    out = subprocess.run(  # noqa: S603
        [DOCKER, "inspect", "-f", "{{json .State}}|{{.RestartCount}}", cid],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    raw, restarts = out.rsplit("|", 1)
    data = json.loads(raw)
    data["RestartCount"] = int(restarts)
    return data


def judge(
    peaks: dict[str, tuple[float, float]], states: dict[str, dict], tally: Tally
) -> list[str]:
    problems = []
    for service in SERVICES:
        if service not in peaks:
            problems.append(f"{service}: no memory sample (not running)")
            continue
        used, limit = peaks[service]
        if used >= MAX_SHARE * limit:
            problems.append(f"{service}: peak {used / 2**20:.0f} MiB of {limit / 2**20:.0f} MiB")
        st = states.get(service, {})
        if st.get("OOMKilled"):
            problems.append(f"{service}: killed for memory")
        if st.get("RestartCount", 0):
            problems.append(f"{service}: restarted {st['RestartCount']} times")
    total = tally.ok + tally.failed
    if total == 0:
        problems.append("no request was made")
    elif tally.failed / total >= MAX_FAILED_SHARE:
        problems.append(f"{tally.failed} of {total} requests failed")
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    parser.add_argument("--minutes", type=float, default=10)
    args = parser.parse_args(argv)
    base = args.base.rstrip("/")
    if urlparse(base).hostname not in LOCAL:
        print("Refused: the load runs against https://localhost only", file=sys.stderr)
        return 2
    ids = container_ids()
    stop, tally = threading.Event(), Tally()
    threads = [threading.Thread(target=worker, args=(base, stop, tally)) for _ in range(THREADS)]
    for t in threads:
        t.start()
    peaks: dict[str, tuple[float, float]] = {}
    end = time.monotonic() + args.minutes * 60
    try:
        while time.monotonic() < end:
            for service, (used, limit) in sample(ids).items():
                if used >= peaks.get(service, (0.0, limit))[0]:
                    peaks[service] = (used, limit)
            time.sleep(SAMPLE_SECONDS)
    finally:
        stop.set()
        for t in threads:
            t.join()
    states = {service: state(cid) for service, cid in ids.items()}
    print(f"{'service':<10} {'peak MiB':>9} {'limit MiB':>9} {'share':>6}")
    for service in SERVICES:
        used, limit = peaks.get(service, (0.0, 0.0))
        share = used / limit if limit else 0
        print(f"{service:<10} {used / 2**20:>9.0f} {limit / 2**20:>9.0f} {share:>6.0%}")
    print(f"requests: {tally.ok} ok, {tally.failed} failed, {args.minutes:g} min")
    problems = judge(peaks, states, tally)
    for line in problems:
        print(f"FINDING {line}")
    print("memory PASS" if not problems else "memory FAIL")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
