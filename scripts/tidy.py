"""Repo hygiene: `/tidy` (.claude/commands/tidy.md).

Why: one-off files pile up between releases and end up in a commit. This sorts every untracked
file, deletes only caches that tools make again, and moves one-off files to .scratch/ only
after a human "yes". It never deletes anything else, and it undoes a move that breaks a check.

Run:
  uv run python scripts/tidy.py              steps a, b, e: lists, cache clean, dead-code report
  uv run python scripts/tidy.py --move --yes steps c, d: move the scratch list, check, undo on fail
  uv run python scripts/tidy.py --check      gate (/verify step 1): fail on a stray file
  uv run python scripts/tidy.py --commit-note  the "safe to commit" note: tracked files that now
                                             match a by-product pattern, for `git rm` (D255)
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IST = timezone(timedelta(hours=5, minutes=30))
SCRATCH = ".scratch"
PLAN = f"{SCRATCH}/tidy-plan.json"
MOVES = f"{SCRATCH}/tidy-moves.json"

# Untracked files that belong in git. Anything else untracked is a stray file.
KEEP = (
    "src/*",
    "tests/*",
    "scripts/*",
    "migrations/*",
    "deploy/*",
    "docs/*",
    # The dashboard v2 sources (R5, D144): src, package.json, package-lock.json, .nvmrc,
    # .npmrc, components.json, configs. node_modules and the dist build are gitignored.
    "web/*",
    ".claude/*",
    ".github/*",
    "pyproject.toml",
    "uv.lock",
    "alembic.ini",
    "Dockerfile",
    "docker-compose.yml",
    ".dockerignore",
    ".python-version",
    ".gitignore",
    ".env.example",
    "README.md",
    "CLAUDE.md",
)
# One-off files by name, wherever they are.
SCRATCH_NAMES = ("debug_*", "tmp_*", "*_output.*", "*.ipynb")
# Ad-hoc scripts: a script outside the code folders.
SCRIPT_EXT = (".py", ".ps1", ".sh", ".bat", ".cmd")
CODE_DIRS = (
    *("src/", "tests/", "scripts/", "migrations/", ".claude/", ".github/", "deploy/"),
    "web/",
)
# The only things tidy deletes: caches the tools make again.
CACHE_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".hypothesis",
    "htmlcov",
}
CACHE_FILES = (".coverage", ".coverage.*")
SKIP_WALK = {".git", ".venv", "venv", SCRATCH, "node_modules"}
# Files whose text can name another file: such a named file is never moved.
CONFIG_FILES = ("pyproject.toml", "alembic.ini")
CONFIG_DIRS = (".claude", "docs")
TEXT_EXT = {".md", ".toml", ".ini", ".yaml", ".yml", ".json", ".txt", ".py", ".sh", ".cfg"}
# Key material: listed by name only, never opened, moved or deleted.
KEY_NAMES = (
    ".env",
    ".env.*",
    "*-write.json",
    "*service-account*.json",
    "*.pem",
    "*.key",
    "saley-v2.json",
)


@dataclass
class Lists:
    keep: list[str] = field(default_factory=list)
    byproduct: list[str] = field(default_factory=list)
    scratch: list[str] = field(default_factory=list)
    protected: dict[str, str] = field(default_factory=dict)  # scratch-like but never moved


def git(root: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed git command
        ["git", *args],  # noqa: S607 - git from PATH, like the developer's shell
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
    ).stdout


def matches(path: str, patterns: tuple[str, ...]) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(name, p) for p in patterns)


def untracked(root: Path) -> list[str]:
    out = git(root, "status", "--porcelain", "--untracked-files=all")
    return sorted(line[3:].strip('"') for line in out.splitlines() if line.startswith("?? "))


def ignored(root: Path) -> list[str]:
    out = git(root, "status", "--porcelain", "--ignored", "--untracked-files=normal")
    return sorted(line[3:].strip('"') for line in out.splitlines() if line.startswith("!! "))


def tracked_byproducts(root: Path) -> list[str]:
    """D255: tracked files that match a .gitignore pattern now (gate run records, for example).

    A commit made before the tidy "yes" would keep them in git for ever, so the "safe to commit"
    note lists them for `git rm`. Never removed here: the human runs `git rm`.
    """
    out = git(root, "ls-files", "-ci", "--exclude-standard")
    return sorted(line.strip() for line in out.splitlines() if line.strip())


def tracked(root: Path) -> set[str]:
    return set(git(root, "ls-files").splitlines())


def is_keep(path: str) -> bool:
    return matches(path, KEEP) and not matches(path, SCRATCH_NAMES)


def config_text(root: Path) -> str:
    """All text that may name a file: pyproject.toml, alembic.ini, .claude/ and docs/."""
    parts = [(root / f).read_text("utf-8", "replace") for f in CONFIG_FILES if (root / f).exists()]
    for d in CONFIG_DIRS:
        for p in (root / d).rglob("*") if (root / d).exists() else ():
            if p.is_file() and p.suffix in TEXT_EXT and "-screens" not in p.parent.name:
                parts.append(p.read_text("utf-8", "replace"))
    return "\n".join(parts)


def importers(root: Path) -> str:
    """Every import line of the code, to find a module that code imports."""
    lines = []
    for p in root.rglob("*.py"):
        if SKIP_WALK & set(p.relative_to(root).parts):
            continue
        text = p.read_text("utf-8", "replace")
        lines += [ln for ln in text.splitlines() if re.match(r"\s*(from|import)\s", ln)]
    return "\n".join(lines)


def protection(path: str, known: set[str], configs: str, imports: str) -> str | None:
    """Why a file must never be moved or deleted, or None when it may be moved."""
    name = path.rsplit("/", 1)[-1]
    if path in known:
        return "tracked by git"
    if matches(path, KEY_NAMES):
        return "key material"
    stem = name.rsplit(".", 1)[0]
    if name.endswith(".py") and re.search(rf"\b{re.escape(stem)}\b", imports):
        return "imported by code"
    if path in configs or name in configs:
        return "named in a config"
    return None


def classify(root: Path) -> Lists:
    """Step a: keep (belongs in git), by-product (ignored now), scratch (one-off)."""
    lists = Lists(byproduct=ignored(root))
    known, configs, imports = tracked(root), config_text(root), importers(root)
    for path in untracked(root):
        one_off = matches(path, SCRATCH_NAMES) or (
            path.endswith(SCRIPT_EXT) and not path.startswith(CODE_DIRS)
        )
        if not one_off and is_keep(path):
            lists.keep.append(path)
            continue
        why = protection(path, known, configs, imports)
        if why:
            lists.protected[path] = why
        else:
            lists.scratch.append(path)
    return lists


def stray(root: Path) -> list[str]:
    """Step f: untracked files outside the keep patterns."""
    return [p for p in untracked(root) if not is_keep(p)]


def clean_caches(root: Path) -> list[str]:
    """Step b: delete regenerable caches. Nothing else is deleted, ever."""
    known = tracked(root)
    removed: list[str] = []
    stack = [root]
    while stack:
        d = stack.pop()
        for p in sorted(d.iterdir()):
            rel = p.relative_to(root).as_posix()
            if p.is_dir() and not p.is_symlink():
                if p.name in SKIP_WALK:
                    continue
                if p.name in CACHE_DIRS:
                    if not any(k == rel or k.startswith(rel + "/") for k in known):
                        shutil.rmtree(p)
                        removed.append(rel + "/")
                    continue
                stack.append(p)
            elif p.is_file() and matches(p.name, CACHE_FILES) and rel not in known:
                p.unlink()
                removed.append(rel)
    return sorted(removed)


def run(cmd: list[str], root: Path, timeout: int = 1800) -> tuple[int, str]:
    try:
        proc = subprocess.run(  # noqa: S603 - fixed tool commands
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"{cmd[0]}: {exc}"
    return proc.returncode, proc.stdout + proc.stderr


def checks(root: Path) -> tuple[bool, str]:
    """Step d: py_compile on every file, ruff check, pytest -q."""
    files = [
        str(p)
        for d in ("src", "tests", "scripts", "migrations")
        for p in (root / d).rglob("*.py")
        if (root / d).exists()
    ]
    for name, cmd in (
        ("py_compile", [sys.executable, "-m", "py_compile", *files]),
        ("ruff check", [sys.executable, "-m", "ruff", "check", "."]),
        ("pytest -q", [sys.executable, "-m", "pytest", "-q"]),
    ):
        code, out = run(cmd, root)
        if code:
            return False, f"{name} failed: {out.strip().splitlines()[-1] if out.strip() else code}"
    return True, "ok"


def move(
    root: Path, paths: list[str], check: Callable[[Path], tuple[bool, str]] = checks
) -> tuple[bool, str, list[tuple[str, str]]]:
    """Steps c and d: move to .scratch/ (no deletes), check, move back on a failure."""
    known, configs, imports = tracked(root), config_text(root), importers(root)
    moves: list[tuple[str, str]] = []
    for path in paths:
        why = protection(path, known, configs, imports)
        src = root / path
        if why or not src.is_file():
            print(f"skip {path}: {why or 'not a file any more'}")
            continue
        dest = root / SCRATCH / path
        n = 1
        while dest.exists():
            dest = root / SCRATCH / f"{path}.{n}"
            n += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, dest)
        moves.append((path, dest.relative_to(root).as_posix()))
    (root / MOVES).write_text(json.dumps(moves, indent=1), encoding="utf-8")
    if not moves:
        return True, "nothing to move", moves
    ok, reason = check(root)
    if not ok:
        for path, dest in reversed(moves):
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(root / dest, root / path)
        return False, reason, moves
    return True, reason, moves


def dead_code(root: Path) -> tuple[str, str]:
    """Step e: the refactor-cleaner report, report only. Nothing is removed."""
    _, ruff = run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            "F401,F841",
            "--output-format",
            "concise",
            "--no-fix",
            ".",
        ],
        root,
    )
    targets = [d for d in ("src/saley", "tests") if (root / d).exists()]
    _, vult = run([sys.executable, "-m", "vulture", *targets, "--min-confidence", "80"], root)
    return ruff.strip(), vult.strip()


def write_report(root: Path, lists: Lists, removed: list[str], ruff: str, vult: str) -> Path:
    today = datetime.now(IST)
    path = root / "docs" / "test-reports" / f"tidy-{today:%Y-%m-%d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)

    def block(text: str) -> str:
        return f"```\n{text or 'no findings'}\n```"

    body = [
        f"# /tidy · {today:%d %b %Y %H:%M} IST",
        "",
        "Report only (agent refactor-cleaner). Nothing was removed. Removal is a later ticket "
        "with its own /verify.",
        "",
        f"Untracked: keep {len(lists.keep)} · by-product {len(lists.byproduct)} · "
        f"scratch {len(lists.scratch)} · protected {len(lists.protected)}. "
        f"Caches deleted: {len(removed)}.",
        "",
        "## ruff check --select F401,F841 (unused imports, unused variables)",
        block(ruff),
        "",
        "## vulture src/saley tests --min-confidence 80",
        block(vult),
        "",
    ]
    path.write_text("\n".join(body), encoding="utf-8", newline="\n")
    return path


def show(title: str, items: list[str] | dict[str, str]) -> None:
    print(f"\n{title} ({len(items)})")
    if isinstance(items, dict):
        items = [f"{k}  [{v}]" for k, v in items.items()]
    for item in items or ["(none)"]:
        print(f"  {item}")


def summarize_keep(keep: list[str]) -> list[str]:
    groups: dict[str, int] = {}
    for p in keep:
        top = p.split("/", 1)[0] + ("/" if "/" in p else "")
        groups[top] = groups.get(top, 0) + 1
    return [f"{k}  {n} file{'s' if n != 1 else ''}" for k, n in sorted(groups.items())]


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Gate: fail on a stray file.")
    parser.add_argument("--move", action="store_true", help="Move the scratch list to .scratch/.")
    parser.add_argument("--yes", action="store_true", help="The human said yes to the move.")
    parser.add_argument(
        "--commit-note", action="store_true", help="List tracked by-products for git rm (D255)."
    )
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if args.check:
        bad = stray(root)
        if bad:
            print(f"tidy --check: {len(bad)} stray file(s) outside the keep patterns:")
            for p in bad:
                print(f"  {p}")
            return 1
        print("tidy --check: no stray file")
        return 0
    if args.commit_note:
        old = tracked_byproducts(root)
        if not old:
            print("safe to commit: no tracked by-product")
            return 0
        print(f"safe to commit after `git rm` of {len(old)} tracked by-product(s) (D255):")
        for p in old:
            print(f"  git rm {p}")
        return 0
    if args.move:
        if not args.yes:
            parser.error("--move needs --yes: a human approves every move")
        plan = root / PLAN
        if not plan.exists():
            parser.error("no plan: run scripts/tidy.py first")
        ok, reason, moves = move(root, json.loads(plan.read_text(encoding="utf-8")))
        for path, dest in moves:
            print(f"moved {path} -> {dest}")
        print("tidy: nothing broke" if ok else f"tidy: reverted ({reason})")
        return 0 if ok else 1
    lists = classify(root)
    show("keep (belongs in git)", summarize_keep(lists.keep))
    show("by-product (ignored by .gitignore)", lists.byproduct)
    show("tracked by-products (git rm before the commit, D255)", tracked_byproducts(root))
    show("scratch (one-off; moves to .scratch/ after a yes)", lists.scratch)
    show("protected (looks one-off, never moved)", lists.protected)
    removed = clean_caches(root)
    show("caches deleted", removed)
    (root / SCRATCH).mkdir(exist_ok=True)
    (root / PLAN).write_text(json.dumps(lists.scratch, indent=1), encoding="utf-8")
    ruff, vult = dead_code(root)
    report = write_report(root, lists, removed, ruff, vult)
    print(f"\nreport: docs/test-reports/{report.name}")
    if lists.scratch:
        print(f"Waiting for a yes to move {len(lists.scratch)} file(s): tidy.py --move --yes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
