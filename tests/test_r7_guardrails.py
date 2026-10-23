"""R7-G01: the bot holds no LLM or Google key, and the bot package reaches no sales, news or
anthropic module (docs/17 §3; docs/plans/R7-ponytail-map.md, the graphify rule).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import yaml

from saley.core.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "src" / "saley" / "bot"
FORBIDDEN = ("saley.sales", "saley.news", "anthropic", "saley.core.llm", "saley.worker")
# The secrets the bot service must not have (compose `environment:` wins over the env file).
SECRETS = (
    "ANTHROPIC_API_KEY",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "WEB_SESSION_SECRET",
    "MAIL_APP_PASSWORD",
    "SMTP_APP_PASSWORD",
    "APOLLO_API_KEY",
    "POSTGRES_PASSWORD",
    "DATABASE_URL_ADMIN",
    "DATABASE_URL_RO",
    "TEST_DATABASE_URL",
)
KEPT = ("DISCORD_BOT_TOKEN", "DATABASE_URL", "PUBLIC_URL")


def _bot_environment() -> dict[str, str]:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    return {k: str(v) for k, v in compose["services"]["bot"]["environment"].items()}


def test_the_bot_service_blanks_every_secret_and_keeps_its_three() -> None:
    env = _bot_environment()
    assert {name: env.get(name) for name in SECRETS} == dict.fromkeys(SECRETS, "")
    assert not set(KEPT) & set(env)  # these come from the env file, untouched


def test_saley_bot_settings_hold_no_llm_or_google_key() -> None:
    full = {name: "secret-value" for name in SECRETS} | {
        "DATABASE_URL_RO": "postgresql+psycopg://x"
    }
    settings = Settings(_env_file=None, **{k.lower(): v for k, v in full.items()})
    assert settings.anthropic_api_key == "secret-value"  # the env file would give it
    bot = Settings(
        _env_file=None,
        **{k.lower(): v for k, v in (full | _bot_environment()).items() if k != "SALEY_ENV"},
    )
    assert bot.anthropic_api_key == ""
    assert bot.google_service_account_file == "" and bot.google_oauth_client_secret == ""


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
    return found


def test_the_bot_package_imports_no_sales_news_or_anthropic_module() -> None:
    edges = {
        f"{path.relative_to(BOT)} -> {name}"
        for path in BOT.rglob("*.py")
        for name in _imports(path)
        if name.startswith(FORBIDDEN)
    }
    assert edges == set()


def test_the_running_bot_loads_none_of_them() -> None:
    code = (
        "import sys, saley.bot.gateway, saley.bot.press, saley.bot.sync, saley.bot.memory;"
        f"print(sorted(m for m in sys.modules if m.startswith({FORBIDDEN!r})))"
    )
    out = subprocess.run(  # noqa: S603 - our own interpreter and a fixed script
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert out.stdout.strip().splitlines()[-1] == "[]"
