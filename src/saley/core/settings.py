"""Typed settings for every service, read once from the environment and `.env`.

Why: CLAUDE.md allows config only through this module. `.env.example` is the contract and
lists every field below (tests/test_env_example.py checks it). Required values are checked
per service with `require()`, never at import, so one service never fails on another's keys.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

REQUIRED_PREFIX = "<REQUIRED"

# Which settings each service needs before it may start. Checked by `Settings.require()`.
SERVICE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "web": ("database_url_ro",),
    "bot": ("database_url", "discord_bot_token", "discord_guild_id", "discord_test_channel_id"),
    "worker": ("database_url", "gtm_sheet_id", "google_service_account_file"),
    "sync": ("database_url", "gtm_sheet_id", "google_service_account_file"),
    "news": ("database_url",),
    "discord": ("discord_bot_token", "discord_test_channel_id"),
    "llm": ("anthropic_api_key",),
}


class MissingSettings(RuntimeError):
    """A service was started without the values it needs. Names only, never values."""


def is_missing(value: object) -> bool:
    """True for empty values and for unfilled `<REQUIRED: …>` placeholders from .env.example."""
    if value is None:
        return True
    text = str(value).strip()
    return not text or text.startswith(REQUIRED_PREFIX)


def split_csv(value: str) -> list[str]:
    """Comma-separated env values become a clean list. Empty items are dropped."""
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    """Every variable the code reads. Field name = env name in lower case."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- core ---
    saley_env: str = "dev"
    saley_tz: str = "Asia/Kolkata"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://saley:saley@localhost:5432/saley"
    database_url_ro: str = "postgresql+psycopg://saley_ro:saley_ro@localhost:5432/saley"
    database_url_admin: str = ""
    test_database_url: str = ""

    # --- discord ---
    discord_bot_token: str = ""
    discord_guild_id: str = ""
    discord_test_channel_id: str = ""
    discord_sales_channel_id: str = ""
    discord_approver_ids: str = ""
    discord_instruct_ids: str = ""
    test_mode: bool = True
    discord_test_user_ids: str = ""

    # --- google sheets ---
    gtm_sheet_id: str = ""
    gtm_sheet_is_copy: bool = True
    google_service_account_file: str = ""
    sheet_sync_seconds: int = Field(default=60, ge=10)
    email_write_allowed: bool = False
    undo_minutes: int = Field(default=10, ge=1)
    owner_default_linkedin: str = "sid"
    owner_default_research: str = "vaishnavi"

    # --- llm (decision D28: Anthropic API directly) ---
    anthropic_api_key: str = ""
    llm_small: str = "claude-haiku-4-5"
    llm_mid: str = "claude-sonnet-4-5"
    llm_daily_call_cap: int = Field(default=400, ge=0)
    llm_cache_days: int = Field(default=7, ge=0)

    # --- egress ---
    egress_allowlist: str = (
        "sheets.googleapis.com,oauth2.googleapis.com,discord.com,api.anthropic.com,"
        "api.openalex.org,export.arxiv.org,news.google.com,imap.gmail.com"
    )
    egress_blocklist: str = "linkedin.com"
    egress_rate_per_host_per_min: int = Field(default=30, ge=1)
    http_timeout_seconds: float = Field(default=20, gt=0)

    # --- web ---
    # 8090, not 8080: 8080 is often taken on laptops (Q-R1.1-2).
    web_port: int = Field(default=8090, ge=1, le=65535)
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    web_allowed_emails: str = ""
    web_session_secret: str = ""
    dev_user_email: str = "dev@membrane.local"

    # --- email sending (OFF until a decision; drafts only, D19) ---
    email_send_enabled: bool = False
    email_sink_path: str = "data/outbox"

    # --- mail (Membrane cc mailbox, read-only) ---
    mail_imap_host: str = "imap.gmail.com"
    mail_user: str = ""
    mail_app_password: str = ""
    mail_poll_minutes: int = 10

    # --- apollo (pipeline to build; off until then) ---
    apollo_enabled: bool = False
    apollo_api_key: str = ""
    apollo_monthly_credit_cap: int = 0

    # ---------- derived values ----------
    @property
    def is_dev(self) -> bool:
        return self.saley_env.strip().lower() == "dev"

    @property
    def admin_database_url(self) -> str:
        """Migrations run as saley_admin when set, else as the read-write role."""
        return (
            self.database_url_admin
            if not is_missing(self.database_url_admin)
            else self.database_url
        )

    @property
    def effective_test_database_url(self) -> str:
        """Tests use TEST_DATABASE_URL, else DATABASE_URL with the database name + `_test`."""
        if not is_missing(self.test_database_url):
            return self.test_database_url
        url = make_url(self.database_url)
        return url.set(database=f"{url.database or 'saley'}_test").render_as_string(
            hide_password=False
        )

    @property
    def allowlist(self) -> list[str]:
        return [h.lower() for h in split_csv(self.egress_allowlist)]

    @property
    def blocklist(self) -> list[str]:
        return [h.lower() for h in split_csv(self.egress_blocklist)]

    @property
    def test_user_ids(self) -> list[str]:
        return split_csv(self.discord_test_user_ids)

    @property
    def sheet_mode(self) -> str:
        return "copy" if self.gtm_sheet_is_copy else "live"

    def missing_for(self, service: str) -> list[str]:
        """Names (upper case) of the required settings that are empty for this service."""
        names = SERVICE_REQUIREMENTS.get(service, ())
        return [name.upper() for name in names if is_missing(getattr(self, name))]

    def require(self, service: str) -> None:
        """Stop a service early, with the variable names it lacks, before it does any work."""
        missing = self.missing_for(service)
        if missing:
            raise MissingSettings(f"{service} needs these settings in .env: {', '.join(missing)}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """One Settings object per process. Tests clear the cache to load other values."""
    return Settings()
