"""Build stamp of the dashboard v2 (D145): is `src/saley/web/dist/` built from the sources in
`web/`?

Why: dist is gitignored, so a reviewer cannot see it in a diff. The build (web/scripts/stamp.mjs)
writes `dist/build-stamp.json` with a sha256 over its inputs. Gate step 1 computes the same hash
here, without Node, and fails when the two differ: the gate then tests the code it reads.

The hash rule is the same on both sides: INPUTS plus every file under web/src, sorted by their
path relative to web/ (with "/"), each fed as path, NUL, content with CRLF turned into LF, NUL.
So CRLF and LF checkouts of one commit give one stamp.

Run: `uv run python -m saley.qa.build_stamp` (exit 1 when missing or stale).
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "web"
DIST = ROOT / "src" / "saley" / "web" / "dist"
STAMP_NAME = "build-stamp.json"
# components.json, .npmrc, .nvmrc and eslint.config.js do not change the built output, so they
# are not inputs. The stamp is written only by `npm run build`, after `vite build`.
INPUTS = (
    "index.html",
    "package.json",
    "package-lock.json",
    "public/theme-init.js",
    "tsconfig.json",
    "vite.config.ts",
)
REBUILD = "Run npm --prefix web run build"


@dataclass(frozen=True)
class StampCheck:
    ok: bool
    note: str


def source_files(web: Path = WEB) -> list[str]:
    """The build inputs as sorted paths relative to web/."""
    names = [name for name in INPUTS if (web / name).is_file()]
    src = web / "src"
    if src.is_dir():
        names += [p.relative_to(web).as_posix() for p in src.rglob("*") if p.is_file()]
    return sorted(names)


def source_hash(web: Path = WEB) -> str:
    digest = hashlib.sha256()
    for name in source_files(web):
        body = (web / name).read_bytes().replace(b"\r\n", b"\n")
        digest.update(name.encode("utf-8") + b"\0" + body + b"\0")
    return digest.hexdigest()


def check(web: Path = WEB, dist: Path = DIST) -> StampCheck:
    """PASS when dist holds an index.html and a stamp equal to the hash of the sources."""
    stamp_file = dist / STAMP_NAME
    if not (dist / "index.html").is_file() or not stamp_file.is_file():
        return StampCheck(False, f"no built dashboard in {dist.name}/. {REBUILD}.")
    try:
        built = json.loads(stamp_file.read_text(encoding="utf-8")).get("sha256")
    except (OSError, ValueError, AttributeError):
        return StampCheck(False, f"the build stamp cannot be read. {REBUILD}.")
    now = source_hash(web)
    if built != now:
        return StampCheck(False, f"the build is older than web/ sources. {REBUILD}.")
    return StampCheck(True, f"build stamp {now[:12]} matches web/")


def main() -> int:
    result = check()
    print(result.note)
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
