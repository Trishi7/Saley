"""scripts/tidy.py: sorting, cache-only deletes, safe moves with undo, the gate check."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tidy() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tidy_script", ROOT / "scripts" / "tidy.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["tidy_script"] = module
    spec.loader.exec_module(module)
    return module


def write(root: Path, rel: str, text: str = "x\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A small git repo: tracked code, an ignore file, and a mix of untracked files."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)  # noqa: S607
    write(root, ".gitignore", "__pycache__/\n.scratch/\n*.log\n.pytest_cache/\n.coverage\n")
    write(root, "src/saley/core.py", "import helper\n")
    write(root, "docs/notes.md", "Run named_tool.py to rebuild.\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)  # noqa: S607
    write(root, "src/saley/new_module.py")  # keep: new code
    write(root, "tests/test_new.py")  # keep
    write(root, "debug_dump.txt")  # scratch by name
    write(root, "tmp_query.sql")  # scratch by name
    write(root, "src/saley/run_output.json")  # scratch by name, even inside src
    write(root, "try_this.py")  # ad-hoc script at the root
    write(root, "helper.py")  # looks ad hoc, but code imports it
    write(root, "named_tool.py")  # looks ad hoc, but docs name it
    write(root, "notes.ipynb")  # notebook
    write(root, "server.log")  # by-product (ignored)
    write(root, "src/saley/__pycache__/core.cpython-312.pyc")  # cache
    write(root, ".pytest_cache/v/x")  # cache
    write(root, ".coverage")  # cache file
    return root


def test_classify_sorts_untracked_files(tidy: ModuleType, repo: Path) -> None:
    lists = tidy.classify(repo)
    assert lists.keep == ["src/saley/new_module.py", "tests/test_new.py"]
    assert lists.scratch == [
        "debug_dump.txt",
        "notes.ipynb",
        "src/saley/run_output.json",
        "tmp_query.sql",
        "try_this.py",
    ]
    assert lists.protected == {
        "helper.py": "imported by code",
        "named_tool.py": "named in a config",
    }
    assert "server.log" in lists.byproduct


def test_key_material_is_never_movable(tidy: ModuleType, repo: Path) -> None:
    write(repo, "tmp_service-account.json")
    lists = tidy.classify(repo)
    assert lists.protected["tmp_service-account.json"] == "key material"
    assert "tmp_service-account.json" not in lists.scratch


def test_clean_caches_deletes_caches_only(tidy: ModuleType, repo: Path) -> None:
    removed = tidy.clean_caches(repo)
    assert removed == [".coverage", ".pytest_cache/", "src/saley/__pycache__/"]
    assert not (repo / ".pytest_cache").exists()
    for kept in ("debug_dump.txt", "server.log", "src/saley/core.py", "try_this.py"):
        assert (repo / kept).exists()


def test_clean_caches_keeps_a_tracked_cache_dir(tidy: ModuleType, repo: Path) -> None:
    write(repo, "data/htmlcov/index.html")
    subprocess.run(["git", "add", "-f", "data/htmlcov/index.html"], cwd=repo, check=True)  # noqa: S607
    tidy.clean_caches(repo)
    assert (repo / "data/htmlcov/index.html").exists()


def test_move_keeps_files_when_checks_pass(tidy: ModuleType, repo: Path) -> None:
    ok, reason, moves = tidy.move(
        repo, ["debug_dump.txt", "helper.py", "src/saley/core.py"], check=lambda r: (True, "ok")
    )
    assert ok and reason == "ok"
    assert moves == [("debug_dump.txt", ".scratch/debug_dump.txt")]
    assert (repo / ".scratch/debug_dump.txt").exists() and not (repo / "debug_dump.txt").exists()
    assert (repo / "helper.py").exists() and (repo / "src/saley/core.py").exists()
    assert json.loads((repo / tidy.MOVES).read_text(encoding="utf-8")) == [list(moves[0])]


def test_move_reverts_when_a_check_fails(tidy: ModuleType, repo: Path) -> None:
    ok, reason, moves = tidy.move(
        repo, ["try_this.py", "src/saley/run_output.json"], check=lambda r: (False, "pytest failed")
    )
    assert not ok and reason == "pytest failed" and len(moves) == 2
    assert (repo / "try_this.py").exists() and (repo / "src/saley/run_output.json").exists()
    assert not (repo / ".scratch/try_this.py").exists()


def test_move_never_overwrites_in_scratch(tidy: ModuleType, repo: Path) -> None:
    write(repo, ".scratch/debug_dump.txt", "older\n")
    _, _, moves = tidy.move(repo, ["debug_dump.txt"], check=lambda r: (True, "ok"))
    assert moves == [("debug_dump.txt", ".scratch/debug_dump.txt.1")]
    assert (repo / ".scratch/debug_dump.txt").read_text(encoding="utf-8") == "older\n"


def test_check_lists_stray_files(
    tidy: ModuleType, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert tidy.main(["--check"], root=repo) == 1
    out = capsys.readouterr().out
    assert "debug_dump.txt" in out and "try_this.py" in out
    assert "new_module.py" not in out and "server.log" not in out


def test_check_passes_on_a_clean_tree(tidy: ModuleType, repo: Path) -> None:
    for rel in (
        "debug_dump.txt",
        "tmp_query.sql",
        "src/saley/run_output.json",
        "try_this.py",
        "helper.py",
        "named_tool.py",
        "notes.ipynb",
    ):
        (repo / rel).unlink()
    assert tidy.main(["--check"], root=repo) == 0


def test_move_needs_a_yes(tidy: ModuleType, repo: Path) -> None:
    with pytest.raises(SystemExit):
        tidy.main(["--move"], root=repo)
    assert (repo / "debug_dump.txt").exists()


def test_full_run_writes_plan_and_report(
    tidy: ModuleType, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tidy, "dead_code", lambda root: ("F401 x", ""))
    assert tidy.main([], root=repo) == 0
    plan = json.loads((repo / tidy.PLAN).read_text(encoding="utf-8"))
    assert "debug_dump.txt" in plan and "helper.py" not in plan
    report = next((repo / "docs" / "test-reports").glob("tidy-*.md")).read_text(encoding="utf-8")
    assert "F401 x" in report and "Nothing was removed" in report
    assert (repo / "debug_dump.txt").exists()  # no move without a yes
