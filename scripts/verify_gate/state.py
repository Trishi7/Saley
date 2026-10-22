"""Gate state on disk: result files, paths, the code fingerprint and the current release.

Why: steps 1-9 count only for the code they ran on, so the fingerprint and the files it guards
live together. Paths read `V.REPORTS` and `V.GATE_TMP`, so a test can point them at a temp dir.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict
from pathlib import Path

from verify_gate.base import (
    CODE_DIRS,
    CODE_SUFFIXES,
    FINGERPRINT_SKIP_DIRS,
    KEY_FILE_NAMES,
    ROOT,
    Result,
)
from verify_gate.host import V


def state_path(release: str) -> Path:
    return V.REPORTS / f"{release}-gate.json"


def junit_path(release: str) -> Path:
    return V.GATE_TMP / f"{release}-junit.xml"


def browser_path(release: str) -> Path:
    return V.GATE_TMP / f"{release}-browser.json"


def matrix_path(release: str) -> Path:
    return V.REPORTS / f"{release}-matrix.json"


def load_matrix(release: str) -> list[dict] | None:
    path = matrix_path(release)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def current_release(status_file: Path | None = None) -> str | None:
    """STATUS.md line 'Current release: R2' (the chain's default)."""
    text = (status_file or V.STATUS_FILE).read_text(encoding="utf-8")
    found = re.search(r"^Current release: (R[\d.]+)", text, re.MULTILINE)
    return found.group(1) if found else None


def load(release: str) -> dict[int, Result]:
    path = state_path(release)
    if not path.exists():
        return {}
    return {int(k): Result(**v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def save(release: str, results: dict[int, Result]) -> None:
    data = {str(k): asdict(v) for k, v in sorted(results.items())}
    state_path(release).write_text(json.dumps(data, indent=2), encoding="utf-8")


def code_files(top: Path) -> list[Path]:
    """Code files under `top`, sorted. node_modules and dist are pruned, not walked: web/
    holds tens of thousands of installed files. Key files are never opened."""
    found: list[Path] = []
    for here, dirs, names in os.walk(top):
        dirs[:] = [d for d in dirs if d not in FINGERPRINT_SKIP_DIRS]
        for name in names:
            p = Path(here) / name
            if p.suffix in CODE_SUFFIXES and not any(p.match(k) for k in KEY_FILE_NAMES):
                found.append(p)
    return sorted(found)


def code_fingerprint() -> str:
    """Hash of every code file. Steps 1-9 count only for the code they ran on."""
    digest = hashlib.sha256()
    for d in CODE_DIRS:
        for p in code_files(ROOT / d):
            digest.update(str(p.relative_to(ROOT)).replace("\\", "/").encode())
            digest.update(p.read_bytes())
    for name in ("pyproject.toml", "uv.lock", "alembic.ini"):
        digest.update((ROOT / name).read_bytes())
    return digest.hexdigest()


def fingerprint_path(release: str) -> Path:
    return V.REPORTS / f"{release}-gate.tree"
