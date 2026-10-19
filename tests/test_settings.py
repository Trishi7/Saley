"""Settings load without .env, check required values per service, and follow D28."""

from __future__ import annotations

import pytest

from saley.core.settings import MissingSettings, Settings, is_missing


def test_defaults_load_without_env_file() -> None:
    s = Settings(_env_file=None)
    assert s.saley_env == "dev"
    assert s.test_mode is True
    assert s.gtm_sheet_is_copy is True
    assert s.sheet_sync_seconds == 60


def test_llm_defaults_are_anthropic_models_d28() -> None:
    s = Settings(_env_file=None)
    assert s.llm_small == "claude-haiku-4-5"
    assert s.llm_mid == "claude-sonnet-4-5"
    assert "api.anthropic.com" in s.allowlist
    assert not any("openrouter" in host for host in s.allowlist)


def test_empty_anthropic_key_does_not_fail_in_r1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    s = Settings(_env_file=None)
    s.require("web")
    s.require("news")
    assert s.missing_for("llm") == ["ANTHROPIC_API_KEY"]


def test_placeholder_counts_as_missing() -> None:
    assert is_missing("<REQUIRED: dev bot token>")
    assert is_missing("  ")
    assert not is_missing("abc")


def test_require_names_missing_values_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "<REQUIRED: dev bot token>")
    monkeypatch.setenv("DISCORD_TEST_CHANNEL_ID", "123")
    s = Settings(_env_file=None)
    with pytest.raises(MissingSettings) as err:
        s.require("discord")
    assert "DISCORD_BOT_TOKEN" in str(err.value)
    assert "123" not in str(err.value)


def test_blocklist_and_lists_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGRESS_ALLOWLIST", " A.com , ,b.org")
    monkeypatch.setenv("DISCORD_TEST_USER_IDS", "1,2")
    s = Settings(_env_file=None)
    assert s.allowlist == ["a.com", "b.org"]
    assert s.blocklist == ["linkedin.com"]
    assert s.test_user_ids == ["1", "2"]


def test_test_database_url_is_derived(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/saley")
    monkeypatch.setenv("TEST_DATABASE_URL", "")
    monkeypatch.setenv("DATABASE_URL_ADMIN", "")
    s = Settings(_env_file=None)
    assert s.effective_test_database_url.endswith("/saley_test")
    assert s.admin_database_url == s.database_url


def test_bad_number_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHEET_SYNC_SECONDS", "1")
    with pytest.raises(ValueError, match="sheet_sync_seconds"):
        Settings(_env_file=None)
