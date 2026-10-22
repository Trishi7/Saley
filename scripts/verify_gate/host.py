"""Late lookup of the names that tests replace on `scripts/verify.py`.

Why: tests do `monkeypatch.setattr(verify, "run", ...)` and expect the gate code to see it. Code
that moved out of verify.py reads those names as `V.run`, `V.REPORTS`, ... and `V` forwards to
the verify module that `bind` was given. In production this behaves like a plain global.
"""

from __future__ import annotations

from types import ModuleType


class _Host:
    module: ModuleType | None = None

    def __getattr__(self, name: str) -> object:
        if _Host.module is None:
            raise AttributeError(f"verify module not bound: {name}")
        return getattr(_Host.module, name)


V = _Host()


def bind(module: ModuleType) -> None:
    """Called by verify.py when it loads (a test loads it again: the last one wins)."""
    _Host.module = module
