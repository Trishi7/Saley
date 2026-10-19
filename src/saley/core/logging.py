"""structlog setup shared by every service.

Why: logs must never carry secrets (CLAUDE.md, docs/02 Part H "Secrets"). A redaction
processor blanks any key that looks like a credential before the line is rendered.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from saley.core.settings import get_settings

_SECRET_KEY = re.compile(
    r"(token|secret|passw|api_key|apikey|authorization|cookie|credential|bearer|dsn|session)",
    re.IGNORECASE,
)
_URL_PASSWORD = re.compile(r"://([^:/@\s]+):[^@\s]+@")
# Token shapes that error texts echo back: query keys, bearer headers, Google API keys.
_TOKEN_TEXT = (
    (
        re.compile(
            r"([?&](?:key|token|access_token|api_key|apikey|sig|signature)=)[^&\s\"']+", re.I
        ),
        r"\1***",
    ),
    (re.compile(r"(\bBearer\s+)[A-Za-z0-9._~+/=-]+", re.I), r"\1***"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "***"),
    (re.compile(r"\bsk-ant-[0-9A-Za-z_-]{8,}"), "***"),
)
REDACTED = "[redacted]"


def redact(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> dict[str, Any]:
    """Blank values whose key names look secret. Returns a new dict, never mutates the input."""
    return {k: (REDACTED if _SECRET_KEY.search(k) else _scrub(v)) for k, v in event_dict.items()}


def scrub_text(value: str | None) -> str | None:
    """Text safe to show: URL passwords, token query values, bearer tokens and keys become ***."""
    if value is None:
        return None
    out = _URL_PASSWORD.sub(r"://\1:***@", value)
    for pattern, repl in _TOKEN_TEXT:
        out = pattern.sub(repl, out)
    return out


def _scrub(value: Any) -> Any:
    """Secrets inside text (URLs, tokens) become ***, also in tracebacks, lists and dicts."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {
            k: (REDACTED if _SECRET_KEY.search(str(k)) else _scrub(v)) for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(_scrub(v) for v in value)
    return value


def _stderr_logger(*_args: Any) -> structlog.PrintLogger:
    """Look up sys.stderr at call time, so a swapped stream (tests, reloads) never goes stale."""
    return structlog.PrintLogger(file=sys.stderr)


def configure_logging(level: str | None = None, *, json: bool | None = None) -> None:
    """Configure structlog once per process. JSON outside dev, readable console in dev."""
    settings = get_settings()
    level_name = (level or settings.log_level).upper()
    use_json = (not settings.is_dev) if json is None else json
    logging.basicConfig(stream=sys.stderr, level=level_name, format="%(message)s")
    # egress logs every call itself; httpx's own INFO line would repeat it.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    renderer: Any = (
        structlog.processors.JSONRenderer() if use_json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            redact,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level_name)),
        logger_factory=_stderr_logger,
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.typing.FilteringBoundLogger:
    return structlog.get_logger(name)
