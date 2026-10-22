"""Typed settings for every service, read once from the environment and `.env`.

Why: CLAUDE.md allows config only through this module. `.env.example` is the contract and
lists every field below (tests/test_env_example.py checks it). Required values are checked
per service with `require()`, never at import, so one service never fails on another's keys.
"""

from __future__ import annotations

import ipaddress
import re
from datetime import time
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

REQUIRED_PREFIX = "<REQUIRED"
ENVS = ("dev", "ci", "prod")  # D242
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
MIN_SESSION_SECRET = 32
MIN_PROXY_PREFIX = {4: 16, 6: 48}  # D254: a proxy network, never 0.0.0.0/0

# Which settings each service needs before it may start. Checked by `Settings.require()`.
SERVICE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "web": ("database_url_ro",),
    "bot": ("database_url", "discord_bot_token", "discord_guild_id", "discord_test_channel_id"),
    "worker": ("database_url", "gtm_sheet_id", "google_service_account_file"),
    "sync": ("database_url", "gtm_sheet_id", "google_service_account_file"),
    "news": ("database_url",),
    "discord": ("discord_bot_token", "discord_test_channel_id"),
    "llm": ("anthropic_api_key",),
    # Silent failure 11: asked for only when the feature is on (see `worker.jobs.register_all`).
    "notes_drive": ("google_service_account_file",),
    "sheet_write": ("gtm_sheet_id", "google_service_account_file"),
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


_SHORT = re.compile(r"[a-z][a-z0-9_-]{0,29}")
_CLOCK = re.compile(r"(\d{2}):(\d{2})")
_DISCORD_ID = re.compile(r"[0-9]{17,20}")
MINUTES_PER_DAY = 24 * 60


def parse_clock(value: str, *, allow_24: bool = False) -> int:
    """'14:00' as minutes after midnight. '24:00' (end of day, 1440) only when `allow_24`."""
    match = _CLOCK.fullmatch(value.strip())
    if not match:
        raise ValueError(f"{value!r} is not HH:MM")
    hour, minute = int(match.group(1)), int(match.group(2))
    if minute > 59 or hour > 24 or (hour == 24 and (minute or not allow_24)):
        raise ValueError(f"{value!r} is not a valid time")
    return hour * 60 + minute


def parse_user_hours(value: str) -> dict[str, tuple[int, int]]:
    """`sid:14:00-24:00;vaishnavi:10:00-19:00` as {short: (start, end)} in minutes (D126).

    Strict: a bad entry stops the service at start, so a typo never widens a person's hours.
    """
    hours: dict[str, tuple[int, int]] = {}
    for entry in (e.strip() for e in value.split(";")):
        if not entry:
            continue
        short, sep, window = entry.partition(":")
        start, dash, end = window.partition("-")
        if not sep or not dash or not _SHORT.fullmatch(short):
            raise ValueError(f"USER_HOURS entry {entry!r} must look like sid:14:00-24:00")
        lo, hi = parse_clock(start), parse_clock(end, allow_24=True)
        if lo >= hi:
            raise ValueError(f"USER_HOURS entry for {short} must start before it ends")
        if short in hours:
            raise ValueError(f"USER_HOURS names {short} twice")
        hours[short] = (lo, hi)
    return hours


def parse_user_map(value: str) -> dict[str, str]:
    """`sid:123...,kushal:456...` as {short: discord id} (D135). The ids are never logged.

    Entries are split by `,` or `;` (the server line used commas and every entry was refused,
    R6). A Discord id has 17 to 20 digits. An error names the entry by its place and short
    name and gives the id's length, never the id.
    """
    mapping: dict[str, str] = {}
    entries = [e.strip() for e in re.split(r"[;,]", value)]
    for place, entry in enumerate(entries, start=1):
        if not entry:
            continue
        short, sep, ident = entry.partition(":")
        short, ident = short.strip(), ident.strip()
        if not sep or not _SHORT.fullmatch(short) or not _DISCORD_ID.fullmatch(ident):
            shown = f"{short or '?'}:<{len(ident)} characters>" if sep else f"{short[:20]!r}"
            raise ValueError(
                f"DISCORD_USER_MAP entry {place} ({shown}) must look like short:<discord id>, "
                "a short name in lower case and an id of 17 to 20 digits, "
                "entries split by ',' or ';'"
            )
        if short in mapping:
            raise ValueError(f"DISCORD_USER_MAP names {short} twice")
        mapping[short] = ident
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("DISCORD_USER_MAP gives one Discord id to two people")
    return mapping


class CoreSettings(BaseSettings):
    """Only what logging and the database commands need (R6): the environment, the log level and
    the database logins.

    Why: `saley db-roles` stopped a server deploy on a DISCORD_USER_MAP line it never uses,
    because every command loaded every setting. Logging, `db-roles`, `db-upgrade` and
    `db-owners` read this smaller set, so a value of another service cannot stop them. Same
    env names and defaults as Settings (tests/test_r6_server.py checks it).
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    saley_env: str = "dev"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://saley:saley@localhost:5432/saley"
    database_url_ro: str = "postgresql+psycopg://saley_ro:saley_ro@localhost:5432/saley"
    database_url_admin: str = ""
    postgres_password: str = ""

    @field_validator("saley_env")
    @classmethod
    def _check_env(cls, value: str) -> str:
        env = value.strip().lower()
        if env not in ENVS:
            raise ValueError(f"SALEY_ENV must be one of {', '.join(ENVS)}")
        return env

    @property
    def is_dev(self) -> bool:
        return self.saley_env == "dev"

    @property
    def admin_database_url(self) -> str:
        """Migrations run as saley_admin when set, else as the read-write role."""
        return (
            self.database_url_admin
            if not is_missing(self.database_url_admin)
            else self.database_url
        )


def get_core_settings() -> CoreSettings:
    """Read fresh each call (a few calls per process), so tests that change the env see it."""
    return CoreSettings()


class Settings(BaseSettings):
    """Every variable the code reads. Field name = env name in lower case."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- core ---
    # D242: dev (laptop), ci (the compose checks: prod rules plus mock sign-in on localhost),
    # prod (the server). Any other value stops the process.
    saley_env: str = "dev"
    # D242: the address people open. Prod needs https; ci needs https on localhost.
    public_url: str = "http://localhost:8090"
    # D243: the image sets true. A container has its own network, so the busy-port check is off.
    saley_in_container: bool = False
    # D242 (security review M1): ci also needs this marker, set only by deploy/compose.ci.yml.
    saley_ci_runner: bool = False
    # D244: the Postgres superuser password; only `saley db-roles` reads it (server).
    postgres_password: str = ""
    # Server only, read by docker-compose.yml and deploy/*.sh from the same env file. Listed
    # here so the .env.example contract (tests/test_env_example.py) covers them too.
    http_port: int = Field(default=80, ge=1, le=65535)
    https_port: int = Field(default=443, ge=1, le=65535)
    google_service_account_host_file: str = "/srv/saley/secrets/google-sa.json"
    backup_s3_url: str = ""  # Q-N8, D253: empty = local backups only
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
    # D130: the blocker note is its own post this many minutes after the weekly pack (14:00 + 120
    # = 16:00 IST). 0 sends it with the pack.
    blocker_note_delay_minutes: int = Field(default=120, ge=0, le=1440)
    # D132: caps per person per day (DMs and @mentions together) and the gap between two posts
    # that count for one person. A meeting in under 24 h skips the gap.
    bot_posts_per_person_per_day: int = Field(default=4, ge=1, le=50)
    bot_post_gap_minutes: int = Field(default=120, ge=0, le=1440)
    # D126: a delivery window per person in IST, `short:HH:MM-HH:MM;...`. "24:00" ends the day.
    user_hours: str = "sid:14:00-24:00"
    # D128: the daily brief is a DM at this IST time on weekdays. DM_ENABLED is the global switch
    # (off until the human says on); only DM_USERS get a DM, and empty means nobody.
    daily_brief_time: str = "14:00"
    dm_enabled: bool = False
    dm_users: str = "sid,vaishnavi,kushal,trishi"
    # D135: `short:id;...` fills users.discord_id when the bot starts. Empty = leave it alone.
    discord_user_map: str = ""

    # --- google sheets ---
    gtm_sheet_id: str = ""
    gtm_sheet_is_copy: bool = True
    google_service_account_file: str = ""
    sheet_sync_seconds: int = Field(default=60, ge=10)
    email_write_allowed: bool = False
    # D164: the Find people act is off until the human turns it on. One source for the badges.
    find_people_enabled: bool = False
    undo_minutes: int = Field(default=5, ge=1)  # D143: 5, was 10
    # R2 (D45): the dashboard commits a write at once; the worker pushes it to the sheet.
    # Off until the service account is an editor on the sheet copy (Q-R2-1): rows stay `held`.
    sheet_write_enabled: bool = False
    sheet_push_seconds: int = Field(default=2, ge=1)
    sheet_test_tab: str = "SALEY_TEST"
    act_cooldown_seconds: int = Field(default=2, ge=0)  # D48
    owner_default_linkedin: str = "sid"
    owner_default_research: str = "vaishnavi"
    # D92 (confirmed), Q-R3-8: names a meeting note may use for a team member, as
    # `short:alias[|alias2],short2:alias`. An alias maps to that person's short code. Exact only.
    team_name_aliases: str = (
        "sid:Siddarth,Siddharth,Sid;vaishnavi:Vaishnavi,VR;kushal:Kushal;trishi:Trishi"
    )

    # --- llm (decision D28: Anthropic API directly) ---
    anthropic_api_key: str = ""
    llm_small: str = "claude-haiku-5-5"  # D80 (proposed)
    llm_mid: str = "claude-sonnet-5-5"  # D80 (proposed)
    llm_daily_call_cap: int = Field(default=400, ge=0)
    llm_cache_days: int = Field(default=7, ge=0)

    # --- news (R3, D70-D72): runs only in the `saley news` process ---
    news_editions: str = "06:30,12:00,17:00,22:00"
    news_user_agent: str = (
        "SaleyNewsReader/3.0 (+https://membrane.social; contact news@membrane.social)"
    )
    news_items_per_fetch: int = Field(default=50, ge=1)
    news_raw_log: bool = False
    # D151 (amends D70): the dev worker (main slot, SALEY_ENV=dev) runs one edition at start when
    # the newest one is over 6 h old. Production news still runs only in `saley news`.
    news_run_at_start: bool = True
    scorer_backend: str = Field(default="llm", pattern="^(llm|rules)$")
    scorer_batch: int = Field(default=40, ge=1)
    scorer_edition_call_cap: int = Field(default=6, ge=0)
    news_stale_days: int = Field(default=3, ge=1)
    openalex_mailto: str = ""
    # D79: the one-line "what to do" for a linked story. At most this many LLM hints a day.
    action_hint_daily_cap: int = Field(default=40, ge=0)

    # --- profile-link search (R3, D77): SearXNG first when set, then DuckDuckGo ---
    searxng_url: str = ""
    search_daily_cap: int = Field(default=50, ge=0)
    search_min_gap_seconds: int = Field(default=5, ge=0)  # Q-R3-3
    search_cache_days: int = Field(default=30, ge=0)  # Q-R3-3: same query, no new request

    # --- egress ---
    egress_allowlist: str = (
        "sheets.googleapis.com,www.googleapis.com,oauth2.googleapis.com,discord.com,"
        "api.anthropic.com,api.openalex.org,export.arxiv.org,news.google.com,imap.gmail.com,"
        "html.duckduckgo.com"
    )
    egress_blocklist: str = "linkedin.com"
    egress_rate_per_host_per_min: int = Field(default=30, ge=1)
    http_timeout_seconds: float = Field(default=20, gt=0)

    # --- worker ---
    # D113: the worker slot (D46). `saley dev` refuses to start while a worker of the SAME slot
    # ran lately; the gate runs as `worker:gate`, the laptop and server as `worker:main`.
    worker_slot: str = Field(default="worker:main", pattern=r"^worker:[a-z0-9_-]{1,30}$")

    # --- web ---
    # 8090, not 8080: 8080 is often taken on laptops (Q-R1.1-2).
    web_port: int = Field(default=8090, ge=1, le=65535)
    # D113: the gate's own web, against the saley_test database; never WEB_PORT.
    gate_port: int = Field(default=8091, ge=1, le=65535)
    # D254 (security review H1): the proxy whose X-Forwarded-For uvicorn trusts. Server: caddy's
    # fixed address on saley_net (docker-compose.yml). Never "*".
    forwarded_allow_ips: str = "127.0.0.1"
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    # D115: `short=email,...`. The entry names the user, so Saley never guesses (D35).
    web_allowed_emails: str = ""
    # D114, D116: auto = google when the client id, the secret and the allowlist are all set,
    # else mock (dev + loopback only). A session lasts SESSION_HOURS.
    auth_provider: str = Field(default="auto", pattern="^(auto|google|mock)$")
    google_oauth_redirect_url: str = "http://localhost:8090/auth/callback"
    session_hours: int = Field(default=12, ge=1, le=720)
    web_session_secret: str = ""
    dev_user_email: str = "dev@membrane.local"
    dev_user_short: str = "trishi"  # D50: who the dev identity acts as

    # --- email sending (OFF until a decision; drafts only, D19) ---
    email_send_enabled: bool = False
    email_sink_path: str = "data/outbox"
    # D124: SMTP is built only when the flag is on. SMTP_HOST must be on EGRESS_ALLOWLIST.
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = Field(default=587, ge=1, le=65535)
    email_from: str = ""
    smtp_app_password: str = ""  # human-only; the login user is EMAIL_FROM

    # --- mail (Membrane cc mailbox, read-only) ---
    mail_imap_host: str = "imap.gmail.com"
    mail_user: str = ""
    mail_app_password: str = ""
    mail_poll_minutes: int = 10

    # --- meeting notes (R2, D52, D81): a shared Drive folder, and an optional local folder ---
    meeting_notes_drive_folder_id: str = ""  # the id after /folders/ in the Drive URL
    notes_sync_minutes: int = Field(default=15, ge=1)
    meeting_notes_dir: str = "data/notes"

    # --- apollo (pipeline to build; off until then) ---
    apollo_enabled: bool = False
    apollo_api_key: str = ""
    apollo_monthly_credit_cap: int = 0

    @field_validator("saley_env")
    @classmethod
    def _check_env(cls, value: str) -> str:
        env = value.strip().lower()
        if env not in ENVS:
            raise ValueError(f"SALEY_ENV must be one of {', '.join(ENVS)}")
        return env

    @field_validator("forwarded_allow_ips")
    @classmethod
    def _check_forwarded(cls, value: str) -> str:
        for part in split_csv(value):
            try:
                net = ipaddress.ip_network(part, strict=False)
            except ValueError as exc:
                raise ValueError("FORWARDED_ALLOW_IPS lists proxy addresses, never *") from exc
            if net.prefixlen < MIN_PROXY_PREFIX[net.version]:
                raise ValueError(f"FORWARDED_ALLOW_IPS {part} trusts too many addresses")
        return value

    @field_validator("user_hours")
    @classmethod
    def _check_user_hours(cls, value: str) -> str:
        parse_user_hours(value)
        return value

    @field_validator("discord_user_map")
    @classmethod
    def _check_user_map(cls, value: str) -> str:
        parse_user_map(value)
        return value

    @field_validator("daily_brief_time")
    @classmethod
    def _check_brief_time(cls, value: str) -> str:
        parse_clock(value)
        return value

    @field_validator("dm_users")
    @classmethod
    def _check_dm_users(cls, value: str) -> str:
        bad = [short for short in split_csv(value) if not _SHORT.fullmatch(short)]
        if bad:
            raise ValueError("DM_USERS must list short names such as sid,kushal")
        return value

    # ---------- derived values ----------
    @property
    def user_hours_map(self) -> dict[str, tuple[int, int]]:
        return parse_user_hours(self.user_hours)

    @property
    def brief_time(self) -> time:
        minutes = parse_clock(self.daily_brief_time)
        return time(minutes // 60, minutes % 60)

    @property
    def dm_user_shorts(self) -> list[str]:
        return split_csv(self.dm_users)

    @property
    def user_map(self) -> dict[str, str]:
        return parse_user_map(self.discord_user_map)

    @property
    def approver_ids(self) -> list[str]:
        return split_csv(self.discord_approver_ids)

    @property
    def instruct_ids(self) -> list[str]:
        return split_csv(self.discord_instruct_ids)

    @property
    def is_dev(self) -> bool:
        return self.saley_env.strip().lower() == "dev"

    @property
    def public_host(self) -> str:
        return (urlsplit(self.public_url).hostname or "").lower()

    @property
    def is_ci(self) -> bool:
        """D242: the compose checks. Only on an https localhost address, so a server that is
        set to ci by mistake still allows no mock sign-in from the internet."""
        url = urlsplit(self.public_url)
        local = url.scheme == "https" and self.public_host in LOCAL_HOSTS
        return self.saley_env == "ci" and self.saley_ci_runner and local

    def web_start_problems(self) -> list[str]:
        """D242: what stops `saley web` on the server (prod), and the secret check in ci."""
        if self.saley_env == "ci" and len(self.web_session_secret) < MIN_SESSION_SECRET:
            return [f"WEB_SESSION_SECRET needs {MIN_SESSION_SECRET}+ random characters"]
        if self.saley_env != "prod":
            return []
        problems = []
        if self.auth_provider == "mock":
            problems.append("AUTH_PROVIDER=mock: the server signs in with Google only")
        google = ("google_oauth_client_id", "google_oauth_client_secret", "web_allowed_emails")
        unset = [name.upper() for name in google if is_missing(getattr(self, name))]
        if unset:
            problems.append(f"Google sign-in needs {', '.join(unset)}")
        if urlsplit(self.public_url).scheme != "https" or self.public_host in LOCAL_HOSTS:
            problems.append("PUBLIC_URL must be the https address of the server")
        callback = self.public_url.rstrip("/") + "/auth/callback"
        if self.google_oauth_redirect_url != callback:
            problems.append(f"GOOGLE_OAUTH_REDIRECT_URL must be {callback}")
        if len(self.web_session_secret) < MIN_SESSION_SECRET or is_missing(self.web_session_secret):
            problems.append(f"WEB_SESSION_SECRET needs {MIN_SESSION_SECRET}+ random characters")
        return problems

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
    def edition_slots(self) -> list[str]:
        return split_csv(self.news_editions)

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


WORKER_PROCESS = "worker"
_PROCESS: dict[str, str] = {"name": "unknown"}


def set_process(name: str) -> None:
    """Name this process. Only the worker entry sets "worker" (D23, D46).

    Why: the sheet write credential may be built only in the worker. The name is a plain
    module value, not an env variable, so a config mistake can never turn it on in the web.
    """
    _PROCESS["name"] = name


def current_process() -> str:
    return _PROCESS["name"]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """One Settings object per process. Tests clear the cache to load other values."""
    return Settings()
