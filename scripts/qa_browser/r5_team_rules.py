"""Pure rules of the R5 T16a browser checks: the team pages (commitment owner toast, Run news
now, the click path of the new app). No browser here: `tests/test_qa_browser.py` tests them.

Why a new file: `r5_rules.py` is near the 800-line ceiling.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

# web/src/views/... `data-act` names of the controls the R5 click path must press (R5-K01).
# "commitment-refused" is the non-owner press of R5-C03: a press, but not a successful Done.
R5_CLICK_KINDS = frozenset({"done", "undo", "commitment-done", "run-news"})
OWNER_LINE = "This is {name}'s commitment. Only they can mark it done."  # words.py ERR_NOT_OWNER
NEWS_QUEUED = "News run queued."  # words.py NEWS_RUN_QUEUED
NEWS_OPEN_STATES = frozenset({"queued", "running"})
NEWS_CALM_STATUSES = frozenset({409, 429})  # busy, cooldown (admin.py _refuse)
COMMIT_DONE_PATH = re.compile(r"^/api/commitment/\d+/done$")
NEWS_RUN_PATH = "/api/admin/news/run"
OPEN_REQUEST_NOTE = "open request; button off as designed"  # the waived run-news press
REFUSAL_CONSOLE = "console: Failed to load resource: the server responded with a status of {s}"


def owner_line(name: str) -> str:
    """The toast a non-owner gets (D155), with the owner's name from users.name."""
    return OWNER_LINE.format(name=name)


def drop_refusal_noise(findings: list[str], status: int, url: str) -> list[str]:
    """The findings without the two lines one expected refusal makes: the watcher's
    "HTTP <status> <url>" and Chromium's console line for that status (one of each). Any other
    finding stays, so a second refusal or another error still fails the check."""
    http = f"HTTP {status} {url}"
    console = REFUSAL_CONSOLE.format(s=status)
    kept: list[str] = []
    dropped_http = dropped_console = False
    for finding in findings:
        if not dropped_http and finding == http:
            dropped_http = True
            continue
        if not dropped_console and finding.startswith(console):
            dropped_console = True
            continue
        kept.append(finding)
    return kept


def press_problems(where: str, facts: Mapping[str, object], statuses: Iterable[int]) -> list[str]:
    """One press: exactly one POST, to the path it names, with X-Saley-Act, and an answer the
    check expects. `facts`: sent (POSTs this press made), path, header, status, want_path
    (a regex or a plain path)."""
    problems = []
    sent = _int(facts.get("sent"))
    if sent != 1:
        problems.append(f"{where}: the press sent {sent} POSTs, want 1")
    path = str(facts.get("path") or "")
    want = facts.get("want_path")
    ok_path = bool(want.match(path)) if isinstance(want, re.Pattern) else path == str(want)
    if not ok_path:
        problems.append(f"{where}: posted to {path!r}")
    if facts.get("header") != "1":
        problems.append(f"{where}: the POST had no X-Saley-Act")
    status = _int(facts.get("status"))
    allowed = sorted(set(statuses))
    if status not in allowed:
        problems.append(f"{where}: answered {status}, want one of {allowed}")
    return problems


def owner_toast_problems(facts: Mapping[str, object], expected: str) -> list[str]:
    """R5-C03: the toast says the owner line, nothing is written, the row stays as it was.

    `facts`: toasts (visible toast lines), done_buttons and undo_buttons (in the row),
    row_state (`data-state`), row_lines (row error lines), api_state (the state the API gives
    after the press), decisions_before / decisions_after (commitment Done rows of the QA user).
    """
    problems = []
    toasts = [str(t).strip() for t in _list(facts.get("toasts"))]
    if not any(expected in t for t in toasts):  # a toast also holds its Close button
        problems.append(f"no toast {expected!r} (toasts: {toasts[:3]})")
    if _int(facts.get("done_buttons")) != 1:
        problems.append("the row lost its Mark done Button")
    if _int(facts.get("undo_buttons")) != 0:
        problems.append("the row shows Undo after a refused press")
    if facts.get("row_state") != "open":
        problems.append(f"the row state is {facts.get('row_state')!r}, want 'open'")
    if _int(facts.get("row_lines")):
        problems.append("the row shows a line: the refusal belongs in the toast only")
    if facts.get("api_state") != "open":
        problems.append(f"the API says {facts.get('api_state')!r} after the press, want 'open'")
    if facts.get("decisions_before") != facts.get("decisions_after"):
        problems.append("a commitment Done was written")
    return problems


def news_run_problems(facts: Mapping[str, object]) -> list[str]:
    """R5-N09: after one press, the page shows the queued state with the Button off (200), or
    the server's calm line with no state change (409 busy, 429 cooldown).

    `facts`: status, detail (the server's line on a refusal), line (`[data-news-line]`),
    line_bad (the line is red), state (`[data-news-state]`), disabled (the Button), api_open.
    """
    status = _int(facts.get("status"))
    line = str(facts.get("line") or "").strip()
    problems = []
    if status == 200:
        if line != NEWS_QUEUED:
            problems.append(f"the line reads {line!r}, want {NEWS_QUEUED!r}")
        if facts.get("state") not in NEWS_OPEN_STATES:
            problems.append(f"the state reads {facts.get('state')!r}, want queued or running")
        if not facts.get("disabled"):
            problems.append("the Button still works while the run is open")
        if not facts.get("api_open"):
            problems.append("the API has no open request after a queued answer")
    elif status in NEWS_CALM_STATUSES:
        detail = str(facts.get("detail") or "").strip()
        if not detail or line != detail:
            problems.append(f"the line reads {line!r}, want the server's {detail!r}")
    else:
        problems.append(f"the press answered {status}")
    if facts.get("line_bad"):
        problems.append("the answer line is red; it must be calm")
    return problems


def commit_undo_problems(facts: Mapping[str, object], after: str) -> list[str]:
    """The owner's Done turns into Undo in place (`after` "done"), and Undo brings Mark done
    back (`after` "undo"). `facts`: done_buttons, undo_buttons, row_state."""
    done, undo = _int(facts.get("done_buttons")), _int(facts.get("undo_buttons"))
    state = facts.get("row_state")
    if after == "done":
        if (done, undo) != (0, 1):
            return [f"after Done the row has {done} Done and {undo} Undo, want 0 and 1"]
        return [] if state == "done" else [f"after Done the row state is {state!r}"]
    if (done, undo) != (1, 0):
        return [f"after Undo the row has {done} Done and {undo} Undo, want 1 and 0"]
    return [] if state == "open" else [f"after Undo the row state is {state!r}"]


def open_request_problems(facts: Mapping[str, object]) -> list[str]:
    """Run news now before the press, when a request (anyone's) is already open: the Button
    must be off and the state must show queued or running (D150, D178)."""
    problems = []
    if not facts.get("disabled"):
        problems.append("the Button works while a request is open")
    if facts.get("state") not in NEWS_OPEN_STATES:
        problems.append(f"the state reads {facts.get('state')!r}, want queued or running")
    return problems


def missing_kinds(pressed: Iterable[str], waived: Iterable[str] = ()) -> list[str]:
    """The R5 click-path controls that were never pressed (R5-K01). A waived press (the
    Button was off by design, with a note) is not demanded."""
    return sorted(R5_CLICK_KINDS - set(pressed) - set(waived))


def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return -1


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, list | tuple) else []
