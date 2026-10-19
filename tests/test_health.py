"""`saley health` prints db ok, the alembic head, sheet=copy|live and test_mode."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from saley.cli import app
from saley.core.settings import get_settings

runner = CliRunner()


def test_health_ok_on_test_db(test_db_url: str) -> None:
    result = runner.invoke(app, ["health"])
    assert result.exit_code == 0, result.output
    assert "db ok" in result.output
    assert "alembic head 0005 · current 0005 · at head" in result.output
    assert "sheet=copy test_mode=true" in result.output


def test_health_reports_db_failure(test_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from saley.core.db import dispose_engines

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nobody:x@127.0.0.1:1/none")
    get_settings.cache_clear()
    dispose_engines()
    try:
        result = runner.invoke(app, ["health"])
    finally:
        monkeypatch.setenv("DATABASE_URL", test_db_url)
        get_settings.cache_clear()
        dispose_engines()
    assert result.exit_code == 1
    assert "db FAIL" in result.output
    assert "nobody" not in result.output
