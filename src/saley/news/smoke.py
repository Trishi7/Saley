"""`saley llm smoke --live`: one real LLM call per release, counted in llm_calls (R3-L02).

Why: fixtures prove the code; one live call proves the key, the model id and the egress door.
The call scores one fixture story through the real client with job `release_smoke`. If a
successful smoke for the same release and model is already logged, nothing is called again
(the budget is one per release). A missing key is PENDING on Q-R3-1 (exit 3), not a failure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import anthropic
from sqlalchemy import select

from saley.core import llm
from saley.core.clock import hhmm
from saley.core.db import session_scope
from saley.core.models import LlmCall
from saley.core.settings import get_settings, is_missing
from saley.news.scorer import INSTRUCTION, SYSTEM, ScoreBatch, ScoreInput

JOB = "release_smoke"
RELEASE = "R3"
QUESTION = "Q-R3-1"
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_PENDING = 3

# A made-up headline, so the call never depends on a live feed.
SMOKE_STORY = ScoreInput(
    1,
    "Voice AI startup opens a speech data tender for Indian languages",
    "A public tender asks vendors to supply annotated speech data for Hindi and Tamil.",
    ("Smoke Test Wire",),
)


@dataclass(frozen=True)
class SmokeResult:
    code: int
    message: str


def _system(release: str) -> str:
    """The release id is part of the prompt, so the logged input hash is per release + model."""
    return f"{SYSTEM}\n\nRelease smoke test for {release}."


def proven_at(release: str, model: str) -> datetime | None:
    """When a successful, uncached smoke call for this release and model was logged."""
    key = llm.input_hash(
        model,
        _system(release),
        llm.wrap_data([SMOKE_STORY.block()]) + INSTRUCTION,
        ScoreBatch,
    )
    with session_scope() as session:
        return session.scalar(
            select(LlmCall.at)
            .where(
                LlmCall.job == JOB,
                LlmCall.input_hash == key,
                LlmCall.ok.is_(True),
                LlmCall.cached.is_(False),
            )
            .order_by(LlmCall.at.desc())
            .limit(1)
        )


def run_smoke(
    release: str = RELEASE,
    *,
    client_factory: Callable[[], anthropic.Anthropic] = llm.make_anthropic_client,
) -> SmokeResult:
    """Exit codes: 0 proven, 1 failed, 3 NOT CONFIGURED (Q-R3-1)."""
    settings = get_settings()
    model = settings.llm_small
    if is_missing(settings.anthropic_api_key):
        return SmokeResult(EXIT_PENDING, f"NOT CONFIGURED: {QUESTION} (ANTHROPIC_API_KEY is empty)")
    earlier = proven_at(release, model)
    if earlier is not None:
        return SmokeResult(EXIT_OK, f"already proven {hhmm(earlier)} {earlier:%d %b %Y} ({model})")
    try:
        answer = llm.complete(
            model,
            _system(release),
            [SMOKE_STORY.block()],
            ScoreBatch,
            job=JOB,
            instruction=INSTRUCTION,
            client_factory=client_factory,
        )
    except llm.LLMNotConfigured:
        return SmokeResult(EXIT_PENDING, f"NOT CONFIGURED: {QUESTION} (ANTHROPIC_API_KEY is empty)")
    except llm.LLMCapReached as exc:
        return SmokeResult(EXIT_FAIL, f"cap reached: {exc}")
    except llm.LLMError as exc:
        return SmokeResult(EXIT_FAIL, f"smoke failed on model {model}: {exc}")
    ids = [item.id for item in answer.items]
    return SmokeResult(EXIT_OK, f"smoke ok: model {model}, scored ids {ids}")
