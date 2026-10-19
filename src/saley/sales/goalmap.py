"""Goal → deliverable override file (decision D34): docs/goal-deliverable-map.yaml.

Why: the Dependencies text rule is a guess from words. The team can pin the links for a goal
in one small file instead. A broken file stops the sync with a clear error; it is never
silently ignored, because a wrong blocker list changes what the dashboard shows as late.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from saley.sales.parse import norm_name

MAP_FILE = Path(__file__).resolve().parents[3] / "docs" / "goal-deliverable-map.yaml"


class GoalMapError(ValueError):
    """The override file is not in the expected shape."""


def short_name(item: str) -> str:
    """'NDA - to be prepared by legal' → 'nda'. The part before ' - ', case folded."""
    return norm_name(item.split(" - ", 1)[0])


def load_goal_map(path: Path = MAP_FILE) -> dict[str, set[str]]:
    """Goal code → item names. An entry with " - " must match the full item name; an entry
    without it matches the part of an item before " - ". No file means no overrides."""
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    goals = data.get("goals") if isinstance(data, dict) else None
    if goals is None:
        return {}
    if not isinstance(goals, dict):
        raise GoalMapError(f"{path.name}: 'goals' must map a goal code to a list of items")
    out: dict[str, set[str]] = {}
    for code, items in goals.items():
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            raise GoalMapError(f"{path.name}: goal {code!r} must list item names as text")
        out[str(code).strip().upper()] = {norm_name(i) for i in items if i.strip()}
    return out
