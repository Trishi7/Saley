"""The Docker image: Node builds the dashboard at build time only (R5 T10, D144, D145).

Why read the Dockerfile and not build it: a docker build needs network and a lot of memory
(D142). The rules that matter are visible in the file: a digest-pinned Node stage that installs
from the lock with scripts off, and a run stage without Node that copies the built dist.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIGEST_FROM = re.compile(r"^FROM\s+node:24-[\w.-]+@sha256:[0-9a-f]{64}\s+AS\s+web$", re.I)


def stages() -> list[list[str]]:
    """The Dockerfile as stages: lists of instruction lines (comments dropped)."""
    out: list[list[str]] = []
    for raw in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.upper().startswith("FROM "):
            out.append([])
        out[-1].append(line)
    return out


def test_node_build_stage_no_node_at_runtime() -> None:
    """R5-S05."""
    all_stages = stages()
    web, run = all_stages[0], all_stages[-1]

    assert DIGEST_FROM.match(web[0]), web[0]
    runs = [line for line in web if line.upper().startswith("RUN ")]
    assert any("npm ci --ignore-scripts" in line for line in runs)
    assert any(line.endswith("npm run build") for line in runs)
    assert not any("npm install" in line for line in runs)  # the lock decides, nothing else

    assert "node" not in run[0].lower(), run[0]
    body = "\n".join(run).lower()
    assert "npm" not in body and "apt-get install" not in body
    assert any(
        line.startswith("COPY --from=web") and line.endswith("./src/saley/web/dist") for line in run
    )
    # Nothing copies node_modules or a laptop build into the image.
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "web/node_modules" in ignore and "src/saley/web/dist" in ignore
