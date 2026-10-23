"""Text keys shared by the sheet sync and the bot (moved from `sales.parse` in R7).

Why here: the bot matches a person by the same name key as the sync (C09), and the bot package
may depend on `core` only (docs/plans/R7-ponytail-map.md, graphify rule). `sales.parse` imports
these, so both sides keep one definition.
"""

from __future__ import annotations

import re


def clean_text(value: object) -> str | None:
    """Trimmed text, or None for empty cells. Numbers keep a plain form (12.0 → '12')."""
    if value is None or isinstance(value, bool):
        return None if value is None else ("TRUE" if value else "FALSE")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def norm_name(value: object) -> str:
    """Name key for matching: trimmed, single spaces, case folded. Never fuzzy (C09)."""
    return (clean_text(value) or "").casefold()
