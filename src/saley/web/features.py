"""Feature flags for the badges and the server gates (D164, R5 T6).

One source: `/api/me.features` and the act gates read the same settings, so a "coming soon"
badge and a refusal can never disagree. An off feature shows a badge; the server answers 403.
"""

from __future__ import annotations

from fastapi import HTTPException

from saley.core.settings import Settings, get_settings
from saley.intel import mail
from saley.web import newsfresh
from saley.web import words as w


def flags(settings: Settings | None = None) -> dict[str, bool]:
    s = settings or get_settings()
    return {
        "find_people": s.find_people_enabled,
        "email_write": s.email_write_allowed,
        "speaker_match": False,  # S12 is not built
        "apollo": s.apollo_enabled
        and s.apollo_monthly_credit_cap > 0
        and newsfresh.APOLLO_PIPELINE_BUILT,
        "mail_replies": mail.configured(s),
        "dms": s.dm_enabled,
    }


def require_find_people() -> None:
    """The Find people act: 403 with a words line while FIND_PEOPLE_ENABLED is false."""
    if not get_settings().find_people_enabled:
        raise HTTPException(status_code=403, detail=w.ERR_FIND_OFF)
