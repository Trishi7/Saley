"""`.env.example` is the contract (CLAUDE.md hard rule). Never weaken this test."""

from __future__ import annotations

import re
from pathlib import Path

from saley.core.settings import REQUIRED_PREFIX, Settings

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / ".env.example"
LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def _parse() -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    retired: list[str] = []
    in_retired = False
    for raw in EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("# --- RETIRED"):
            in_retired = True
            continue
        if in_retired:
            assert line == "" or line.startswith("#"), f"RETIRED block must stay commented: {raw}"
            match = re.match(r"#\s*([A-Z][A-Z0-9_]*)\b", line)
            if match:
                retired.append(match.group(1))
            continue
        match = LINE.match(line)
        if match:
            assert match.group(1) not in values, f"duplicate key {match.group(1)}"
            values[match.group(1)] = match.group(2)
    return values, retired


def test_every_setting_is_listed_uncommented() -> None:
    values, _ = _parse()
    missing = [name.upper() for name in Settings.model_fields if name.upper() not in values]
    assert not missing, f"add to .env.example: {missing}"


def test_every_listed_key_is_read_by_the_code() -> None:
    values, _ = _parse()
    fields = {name.upper() for name in Settings.model_fields}
    unknown = sorted(set(values) - fields)
    assert not unknown, f"keys in .env.example the code does not read: {unknown}"


def test_example_value_is_the_code_default() -> None:
    values, _ = _parse()
    for name, field in Settings.model_fields.items():
        example = values[name.upper()]
        default = field.default
        if example.startswith(REQUIRED_PREFIX):
            assert default in ("", None), f"{name.upper()} is <REQUIRED> but has a code default"
            continue
        expected = str(default).lower() if isinstance(default, bool) else str(default)
        if isinstance(default, float) and default.is_integer():
            expected = str(int(default))
        assert example == expected, f"{name.upper()}: .env.example={example!r} code={expected!r}"


def test_retired_keys_are_not_read() -> None:
    _, retired = _parse()
    fields = {name.upper() for name in Settings.model_fields}
    assert "OPENROUTER_API_KEY" in retired
    assert not set(retired) & fields


def test_no_os_environ_outside_settings() -> None:
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"os\.environ|os\.getenv", text) and path.name != "settings.py":
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"config only through saley.core.settings: {offenders}"
