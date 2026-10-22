"""Step 9, the click-path audit: which non-GET requests the browser run may have made.

Why: the allowed POSTs and the R5 press rules are pure functions of the browser result, so they
are kept apart from the server code and can be read as one rule set.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from verify_gate.base import (
    ALLOWED_POST,
    ASK_POST_PATH,
    NEWS_RUN_POST_PATH,
    R2_STEP9_KINDS,
    R5_APP_NAMES,
    R5_GROUP_MARKS,
    R5_STEP9_KINDS,
    SEEN_POST_PATH,
    Result,
)


def stray_requests(non_get: list[str]) -> list[str]:
    """Non-GET requests that are not exactly one of the allowed POSTs (method, path, no query)."""
    stray = []
    for entry in non_get:
        method, _, url = entry.partition(" ")
        parts = urlsplit(url)
        if method != "POST" or parts.query or not ALLOWED_POST.match(parts.path):
            stray.append(entry)
    return stray


def stray_seen(seen_posts: list[str]) -> list[str]:
    """Seen marks that are not exactly `POST /api/seen` (no query, no extra path)."""
    stray = []
    for entry in seen_posts:
        method, _, url = entry.partition(" ")
        parts = urlsplit(url)
        if method != "POST" or parts.query or parts.path != SEEN_POST_PATH:
            stray.append(entry)
    return stray


def stray_ask(ask_posts: list[str]) -> list[str]:
    """Question posts that are not exactly `POST /api/ask` (no query, no extra path)."""
    stray = []
    for entry in ask_posts:
        method, _, url = entry.partition(" ")
        parts = urlsplit(url)
        if method != "POST" or parts.query or parts.path != ASK_POST_PATH:
            stray.append(entry)
    return stray


def ran_r5_app(qa: dict) -> bool:
    """True when the browser run was against the R5 app: a run with `app` "/" (or the
    strangler's "next"; merged: `apps`), or the R5 named checks in the result. A skipped check
    cannot hide the R5 run."""
    apps = qa.get("apps") if isinstance(qa.get("apps"), list) else [qa.get("app")]
    named = qa.get("named") if isinstance(qa.get("named"), dict) else {}
    return bool(R5_APP_NAMES & set(apps)) or any(mark in named for mark in R5_GROUP_MARKS)


def waived_presses(qa: dict) -> dict[str, str]:
    """Presses a check could not make by design (kind -> reason), e.g. Run news now while a
    request is open. Step 9 does not demand them and names them in its note."""
    raw = qa.get("waived")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if k in R5_STEP9_KINDS and str(v).strip()}


def r5_click_problems(qa: dict, non_get: list[str]) -> list[str]:
    """R5-K02: when the R5 app ran, the R5 presses all happened and one of them was the admin
    POST /api/admin/news/run, unless that press was waived with a reason. Before R5 nothing."""
    if not ran_r5_app(qa):
        return []
    kinds = qa.get("act_kinds")
    if not isinstance(kinds, list):
        return ["browser QA gave no list of pressed controls"]
    waived = waived_presses(qa)
    problems = []
    pressed = {str(k) for k in kinds}
    missing = sorted(R5_STEP9_KINDS - pressed - set(waived))
    if missing:
        problems.append(f"R5 controls never pressed: {missing}")
    older = sorted(R2_STEP9_KINDS - pressed)
    if older:
        problems.append(f"R2 controls never pressed: {older}")
    paths = [urlsplit(entry.partition(" ")[2]).path for entry in non_get]
    if "run-news" not in waived and NEWS_RUN_POST_PATH not in paths:
        problems.append(f"no press reached POST {NEWS_RUN_POST_PATH}")
    return problems


def click_path_result(qa: dict, clicks: list[str], ship: bool) -> Result:
    """Step 9: allowed POSTs only, each from a [data-act] control; no form, no submit button.

    The seen marks of the lists (R3) are a second, separate source with one fixed path. They do
    not count as control presses, and a seen path inside `non_get` is a stray request.
    """
    non_get = qa.get("non_get")
    if not isinstance(non_get, list):
        return Result(9, "FAIL", "browser QA gave no list of non-GET requests")
    seen_posts = qa.get("seen_posts", [])
    if not isinstance(seen_posts, list):
        return Result(9, "FAIL", "browser QA gave no valid list of seen marks")
    ask_posts = qa.get("ask_posts", [])
    if not isinstance(ask_posts, list):
        return Result(9, "FAIL", "browser QA gave no valid list of Ask posts")
    stray = stray_requests(non_get)
    uncounted = qa.get("act_posts") != len(non_get)
    problems = [*clicks[:5]]
    bad_seen = stray_seen(seen_posts)
    if bad_seen:
        problems.append(f"seen marks that are not POST {SEEN_POST_PATH}: {bad_seen[:3]}")
    bad_ask = stray_ask(ask_posts)
    if bad_ask:
        problems.append(f"Ask posts that are not POST {ASK_POST_PATH}: {bad_ask[:3]}")
    if stray:
        problems.append(f"requests that are not an allowed POST: {stray[:3]}")
    if uncounted:
        problems.append(
            f"{len(non_get)} non-GET requests but {qa.get('act_posts')} control presses"
        )
    problems += r5_click_problems(qa, non_get)
    if ship and not problems:
        seen_note = f"; {len(seen_posts)} seen marks after list render" if seen_posts else ""
        seen_note += f"; {len(ask_posts)} Ask questions" if ask_posts else ""
        sign_ins = qa.get("sign_ins")
        seen_note += f"; {sign_ins} sign-in" if type(sign_ins) is int else ""  # not a JSON true
        if ran_r5_app(qa):
            waived = waived_presses(qa)
            pressed = sorted(R5_STEP9_KINDS - set(waived))
            seen_note += f"; R5 presses: {', '.join(pressed)}"
            seen_note += "" if "run-news" in waived else " and Run news now"
            seen_note += "".join(f"; {k} not pressed: {why}" for k, why in sorted(waived.items()))
        return Result(
            9,
            "PASS",
            f"{len(non_get)} POSTs, all allowed, each from a [data-act] control{seen_note}; "
            "no form, no submit button",
        )
    return Result(9, "FAIL", " / ".join(problems) or "browser QA did not ship")
