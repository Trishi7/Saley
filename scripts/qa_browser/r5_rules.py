"""Pure rules of the R5 browser checks (R5 T12). No Playwright here, so pytest can test them.

Each function takes what the page showed (plain dicts and lists that a `page.evaluate` call
returns) and gives a list of problems. An empty list is a pass. `checks_r5.py` collects the
page facts and turns each problem into a finding.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PAGE_SIZE = 10  # brief: long lists show 10 per page with "N more"
MORE_TEXT = re.compile(r"^(\d+) more$")
# A scheme, "www.", or a bare host with a path ("example.com/papers"): all are raw URL text.
RAW_URL = re.compile(
    r"(?i)^(?:https?://|www\.)\S+$|://|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}/\S*"
)
ENTRY = "/"  # where the dashboard lives since the cut-over (T16b, D224); /next is gone


def view_url(base: str, entry: str, view: str) -> str:
    """http://localhost:8090 + / + #today (the R1-R4 form /#today)."""
    return f"{base}{entry}#{view}"


def legend_problems(view: str, info: Mapping[str, object]) -> list[str]:
    """R5-Q01: one legend, at the top, each entry with a swatch and words, and every tag on the
    page explained in it.

    `info`: legends (int), tags (legend keys), refs (tag keys used on the page), bare (legend
    entries without swatch or text), content (the page has content after the legend), above
    (the legend sits above the first content). A page with no content is a fail, not a pass.
    """
    problems: list[str] = []
    legends = int(info.get("legends", 0))  # type: ignore[call-overload]
    if legends != 1:
        return [f"{view}: {legends} legends, want 1"]
    tags = set(_strings(info.get("tags")))
    if not tags:
        problems.append(f"{view}: the legend explains nothing")
    missing = sorted(set(_strings(info.get("refs"))) - tags)
    if missing:
        problems.append(f"{view}: tags not in the legend: {missing}")
    bare = list(_strings(info.get("bare")))
    if bare:
        problems.append(f"{view}: legend entries without swatch or words: {bare}")
    if not info.get("content", False):
        problems.append(f"{view}: no page content after the legend")
    elif not info.get("above", False):
        problems.append(f"{view}: the legend is not at the top of the page")
    return problems


def link_problems(view: str, links: Iterable[Mapping[str, str]]) -> list[str]:
    """R5-Q02: no visible link is underlined, and no link shows a raw URL as its text."""
    problems = []
    for link in links:
        text = (link.get("text") or "").strip()
        label = text[:40] or link.get("href", "?")
        if "underline" in (link.get("decoration") or ""):
            problems.append(f"{view}: underlined link {label!r}")
        if text and RAW_URL.search(text):
            problems.append(f"{view}: a link shows a raw URL {label!r}")
    return problems


def hover_picks(links: Iterable[Mapping[str, str]]) -> list[int]:
    """The index of the first link of each kind (rail, tile, card, ...): one hover per kind."""
    seen: set[str] = set()
    picks = []
    for i, link in enumerate(links):
        kind = link.get("kind") or "a"
        if kind not in seen:
            seen.add(kind)
            picks.append(i)
    return picks


def hover_problems(view: str, hovered: Iterable[Mapping[str, str]]) -> list[str]:
    """R5-Q02 on hover: neither the link nor any child under it turns underlined."""
    return [
        f"{view}: {h.get('kind', 'a')} link underlined on hover"
        for h in hovered
        if "underline" in (h.get("decoration") or "")
    ]


def soon_problems(
    features: Mapping[str, bool], shown: Mapping[str, Mapping[str, list[str]]]
) -> list[str]:
    """R5-Q03 (D164): a badge for every off feature on each visited page where it appears,
    never a badge for an on feature.

    `shown[view]`: {"hosts": [features that live on this page, on or off (the section's
    data-features)], "soon": [feature of each visible badge], "gates": [feature of each
    [data-feature] place], "gated": [places that hold their own badge]}. A feature whose pages
    were not visited (an admin page for a team member) is not demanded.
    """
    problems = []
    off = {name for name, on in features.items() if not on}
    for view, facts in shown.items():
        badges = set(_strings(facts.get("soon")))
        for name in sorted(badges):
            if name not in features:
                problems.append(f"{view}: badge for an unknown feature {name!r}")
            elif features[name]:
                problems.append(f"{view}: 'coming soon' on {name!r}, which is on")
        for name in sorted(set(_strings(facts.get("hosts"))) & off - badges):
            problems.append(f"{view}: {name!r} is off but the page shows no badge")
        gated = set(_strings(facts.get("gated")))
        for name in _strings(facts.get("gates")):
            if name in off and name not in gated:
                problems.append(f"{view}: {name!r} is off but its place shows no badge")
    return problems


def demanded(
    features: Mapping[str, bool], shown: Mapping[str, Mapping[str, list[str]]]
) -> list[str]:
    """Off features that some visited page hosts (what the check really verified)."""
    hosts = {name for facts in shown.values() for name in _strings(facts.get("hosts"))}
    return sorted(name for name, on in features.items() if not on and name in hosts)


def pager_problems(view: str, pager: Mapping[str, object]) -> list[str]:
    """R5-Q07: at most 10 rows, the rows match `data-shown`, and an "N more" count is right."""
    total = _int(pager.get("total"))
    offset = _int(pager.get("offset"))
    shown = _int(pager.get("shown"))
    rows = _int(pager.get("rows"))
    more_text = str(pager.get("more") or "").strip()
    problems = []
    if rows > PAGE_SIZE:
        problems.append(f"{view}: {rows} rows on one page, want at most {PAGE_SIZE}")
    if rows != shown:
        problems.append(f"{view}: {rows} rows but the pager says {shown}")
    more = max(0, total - offset - shown)
    if more > 0:
        match = MORE_TEXT.match(more_text)
        if not match:
            problems.append(f"{view}: no 'N more' line ({more_text!r}), {more} not shown")
        elif int(match.group(1)) != more:
            problems.append(f"{view}: says {match.group(1)} more, want {more}")
    return problems


def pagers_verdict(verified: Iterable[str], allow_none: bool) -> list[str]:
    """R5-Q07 must not pass on no evidence: no pager on any page is a fail, unless the run was
    told so (`--allow-no-pager`, T12 QA only, before T13 adds the first pager)."""
    if list(verified) or allow_none:
        return []
    return ["no pager verified on any page"]


def theme_problems(steps: Mapping[str, object]) -> list[str]:
    """R5-Q04 (D163): the toggle flips `.dark`, saves the choice, keeps it after a reload, and
    the two themes paint a different page background."""
    problems = []
    before, after = steps.get("before_dark"), steps.get("after_dark")
    if before == after:
        problems.append("the toggle did not change the theme")
    want = "dark" if after else "light"
    if steps.get("saved") != want:
        problems.append(f"the choice was not saved (saved {steps.get('saved')!r}, want {want!r})")
    if steps.get("reload_dark") != after:
        problems.append("the theme was lost after a reload")
    if steps.get("before_bg") == steps.get("after_bg"):
        problems.append("both themes paint the same background")
    return problems


DASH = "—"
LOOK_KINDS = frozenset({"news", "event", "blocker", "waiting"})  # D158
OPEN_BLOCKERS = "Open blockers"
TIMER_TEXT = re.compile(r"\b\d{1,2}:\d{2}\b")  # "4:59": an Undo countdown (brief: none)
SEARCH_WORD = re.compile(r"(?i)search")


def shown_count(n: object) -> str:
    """How the page writes a count: the number, or "—" when it is not known (never 0)."""
    return DASH if n is None or n == "" else str(n)


def funnel_problems(api_steps: Iterable[Mapping[str, object]], bars: list[Mapping]) -> list[str]:
    """R5-T02: one `a[data-tile]` bar per API step, in order, each with the API count and a
    link to its list. `bars`: [{key, value, href}] as the page shows them."""
    steps = list(api_steps)
    if not steps:
        return ["/api/today has no funnel"]
    problems = []
    keys = [str(s.get("key")) for s in steps]
    shown = [str(b.get("key")) for b in bars]
    if shown != keys:
        problems.append(f"funnel bars {shown}, want {keys}")
    for step in steps:
        bar = next((b for b in bars if b.get("key") == step.get("key")), None)
        if bar is None:
            continue
        want = shown_count(step.get("n"))
        if str(bar.get("value", "")).strip() != want:
            problems.append(f"bar {step.get('key')}: shows {bar.get('value')!r}, API {want!r}")
        if not str(bar.get("href", "")).startswith("#"):
            problems.append(f"bar {step.get('key')}: no link to its list")
    return problems


def blockers_problems(api_line: Mapping[str, object] | None, facts: Mapping) -> list[str]:
    """R5-T02: one blockers line inside the tile area, with the API count and label and an
    "Open blockers" Button that opens the Blockers page."""
    if api_line is None:
        return ["/api/today has no blockers_line"]
    problems = []
    if facts.get("lines") != 1:
        return [f"{facts.get('lines')} blockers lines, want 1"]
    if not facts.get("inside_tiles"):
        problems.append("the blockers line is not inside the tile area")
    want = shown_count(api_line.get("count"))
    if facts.get("count") != want:
        problems.append(f"blockers count {facts.get('count')!r}, API {want!r}")
    if str(api_line.get("label", "")) not in str(facts.get("text", "")):
        problems.append(f"blockers label missing: {api_line.get('label')!r}")
    if str(facts.get("button", "")).strip() != OPEN_BLOCKERS:
        problems.append(f"blockers Button reads {facts.get('button')!r}")
    if facts.get("button_slot") != "button":
        problems.append("Open blockers is not a Button")
    if not str(facts.get("href", "")).startswith("#blockers"):
        problems.append(f"Open blockers goes to {facts.get('href')!r}")
    return problems


def page_search_problems(view: str, inputs: Iterable[Mapping[str, str]]) -> list[str]:
    """R5-T03: no search input inside the page (the global `#gsearch` lives in the top bar)."""
    return [
        f"{view}: a page search input {i.get('id') or i.get('label') or '?'}"
        for i in inputs
        if i.get("type") == "search"
        or SEARCH_WORD.search(" ".join(str(i.get(k) or "") for k in ("label", "placeholder")))
    ]


def undo_row_problems(facts: Mapping[str, object]) -> list[str]:
    """R5-T09: after Done the same button is Undo in the row; no timer text, no bottom bar,
    no toast."""
    problems = []
    if not facts.get("same_button"):
        problems.append("Done did not become Undo in place (another element)")
    if facts.get("undo") != 1:
        problems.append(f"{facts.get('undo')} Undo buttons in the row, want 1")
    if TIMER_TEXT.search(str(facts.get("text", ""))):
        problems.append(f"timer text in the row: {facts.get('text')!r}")
    if facts.get("bars"):
        problems.append("an Undo bar outside the row")
    if facts.get("toasts"):
        problems.append(f"{facts.get('toasts')} toasts after Done")
    if not facts.get("focused"):
        problems.append("focus left the button after Done")
    return problems


def done_back_problems(facts: Mapping[str, object]) -> list[str]:
    """R5-T09 after Undo: the SAME button (not a remount) is Done again and keeps the focus."""
    problems = []
    if not facts.get("same_button"):
        problems.append("Undo did not turn back into Done in place (another element)")
    if facts.get("undo"):
        problems.append("Undo stays after Undo")
    if not facts.get("focused"):
        problems.append("focus left the button after Undo")
    return problems


def look_card_problems(api_cards: list[Mapping], cards: list[Mapping]) -> list[str]:
    """R5-T13: one card per API card; each is ONE clickable link with its type badge and no
    inner link or button ("Open")."""
    problems = []
    if len(cards) != len(api_cards):
        problems.append(f"{len(cards)} cards, API {len(api_cards)}")
    for i, card in enumerate(cards):
        kind = str(card.get("kind"))
        if card.get("tag") != "A" or not str(card.get("href", "")).startswith("#"):
            problems.append(f"card {i}: not one link")
        if kind not in LOOK_KINDS:
            problems.append(f"card {i}: kind {kind!r}")
        if card.get("badge") != kind or not str(card.get("badge_text", "")).strip():
            problems.append(f"card {i}: no {kind} badge")
        if card.get("inner"):
            problems.append(f"card {i}: {card.get('inner')} inner links or buttons")
        if i < len(api_cards) and api_cards[i].get("kind") != kind:
            problems.append(f"card {i}: kind {kind}, API {api_cards[i].get('kind')}")
    return problems


def control_problems(
    where: str, controls: Iterable[Mapping[str, str]], allowed: Callable[[str], bool]
) -> list[str]:
    """Gate step 9 on the new app: each `[data-act]` control is a plain button aimed at an
    allowed POST path (`allowed`: the step 9 list plus /api/ask)."""
    problems = []
    for c in controls:
        kind = c.get("act") or "?"
        if c.get("type") != "button":
            problems.append(f"{where}: {kind} is not a button")
        if not allowed(str(c.get("endpoint") or "")):
            problems.append(f"{where}: {kind} -> {c.get('endpoint')!r}")
    return problems


# ---------------------------------------------------------------- D207: top bar and load errors

LOAD_FAILED_STATUS = "Could not load the status line."  # words.js / words.ts loadFailed
NO_SERVER = "The server did not answer."  # words.js / words.ts noServer
RAW_FETCH_ERRORS = ("Failed to fetch", "NetworkError", "Load failed", "TypeError")
ABORT_NOISE = "console: Failed to load resource: net::ERR_FAILED"  # Chromium, one per abort
VIEW_AS_WORDS = ("View as", "Viewing as")


def with_query(url: str, key: str, value: str) -> str:
    """`url` with `key=value` in its query, set once (an old value of `key` is replaced)."""
    parts = urlsplit(url)
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != key]
    return urlunsplit(parts._replace(query=urlencode([*pairs, (key, value)])))


def load_error_problems(facts: Mapping[str, object]) -> list[str]:
    """R5-V01: a failed /api/status names the data and the reason and offers a Retry button;
    the raw browser text ("Failed to fetch") never shows.

    `facts`: text (the status line), retry_tag (the tag of the visible `[data-retry]` in the
    top bar, "" when none)."""
    text = str(facts.get("text") or "")
    problems = []
    if LOAD_FAILED_STATUS not in text:
        problems.append(f"the status line does not name the data: {text!r}")
    if NO_SERVER not in text:
        problems.append(f"no reason for a network error: {text!r}")
    raw = [w for w in RAW_FETCH_ERRORS if w in text]
    if raw:
        problems.append(f"raw browser text {raw} in {text!r}")
    if str(facts.get("retry_tag") or "").lower() != "button":
        problems.append(f"no Retry button (got {facts.get('retry_tag')!r})")
    return problems


def recovered_problems(facts: Mapping[str, object]) -> list[str]:
    """R5-V01: after Retry the Updated line is back: text, no error words, no Retry button."""
    text = str(facts.get("text") or "").strip()
    problems = []
    if not text or "Could not load" in text:
        problems.append(f"the Updated line did not come back: {text!r}")
    if facts.get("retry_visible"):
        problems.append("the Retry button stays after a good load")
    return problems


def drop_abort_noise(findings: list[str], aborts: int) -> list[str]:
    """The findings with at most `aborts` Chromium console lines of the requests the check
    aborted on purpose removed. Any other finding stays."""
    kept, left = [], aborts
    for finding in findings:
        if left and finding == ABORT_NOISE:
            left -= 1
            continue
        kept.append(finding)
    return kept


def view_as_problems(facts: Mapping[str, object]) -> list[str]:
    """R5-V02 (D207): no "View as" / "Viewing as", no person tabs or pressable person Buttons, and
    Today asks /api/today with no `user=` (the signed-in person's own list).

    `facts`: hooks ({selector: count}), words (the visible words of the top bar),
    today_urls (the /api/today URLs the page asked for)."""
    problems = []
    hooks = facts.get("hooks") if isinstance(facts.get("hooks"), Mapping) else {}
    for selector, count in hooks.items():  # type: ignore[union-attr]
        if _int(count):
            problems.append(f"{selector} is on the page ({count})")
    words = str(facts.get("words") or "")
    problems += [f"the top bar says {w!r}" for w in VIEW_AS_WORDS if w in words]
    urls = _strings(facts.get("today_urls"))
    if not urls:
        problems.append("the page did not load /api/today")
    for url in urls:
        if "user" in dict(parse_qsl(urlsplit(url).query)):
            problems.append(f"Today asked for another person: {url}")
    return problems


# ---------------------------------------------------------------- T14: Q4 goals, Accounts, People

GOAL_PEOPLE_SHOWN = 4  # web/src/views/goals/goal-rules.ts PEOPLE_SHOWN
GOAL_BLOCKERS_SHOWN = 3  # BLOCKERS_SHOWN
TIMELINE_MONTHS = 12  # saley.web.timeline.MONTHS
FOLLOW_UP_LABEL = "Follow-up by"
FOLLOW_UP_RULE = "from the follow-up rule"  # Q-R5-16
EVIDENCE = re.compile(r"(?i)\bevidence\b")


def ring_text(progress: Mapping[str, object] | None) -> str:
    """What the ring centre shows: "<pct>%" (the real value, also above 100), else "—"."""
    pct = (progress or {}).get("pct")
    return DASH if pct is None else f"{pct}%"


def goal_card_problems(api_goals: list[Mapping], cards: list[Mapping]) -> list[str]:
    """R5-G02: one card per API goal, in order; each has a ring with the API value, a people
    table (or its empty line), a blockers list (or its empty line), and a "+N more" Button
    with the right count where a list is longer than its first rows.

    `cards`: [{code, ring, people_table, people_rows, people_more, blockers, blockers_rows,
    blockers_more}] (`*_more`: the `data-more` count, None when there is no Button)."""
    if not api_goals:
        return ["/api/goals has no goal"]
    problems = []
    codes = [str(g.get("code")) for g in api_goals]
    shown = [str(c.get("code")) for c in cards]
    if shown != codes:
        problems.append(f"goal cards {shown}, want {codes}")
    for goal in api_goals:
        card = next((c for c in cards if c.get("code") == goal.get("code")), None)
        if card is None:
            continue
        code = goal.get("code")
        want = ring_text(goal.get("progress"))
        if want not in str(card.get("ring") or ""):
            problems.append(f"{code}: ring shows {card.get('ring')!r}, want {want!r}")
        active = len(goal.get("active") or [])
        blockers = len(goal.get("blockers") or [])
        problems += _list_problems(code, "people", active, GOAL_PEOPLE_SHOWN, card)
        problems += _list_problems(code, "blockers", blockers, GOAL_BLOCKERS_SHOWN, card)
    return problems


def _list_problems(code: object, name: str, n: int, first: int, card: Mapping) -> list[str]:
    """One list of a goal card: present, the first rows shown, "+N more" with the rest."""
    problems = []
    if not card.get(f"{name}_box"):
        problems.append(f"{code}: no {name} list")
    rows = _int(card.get(f"{name}_rows"))
    if rows != min(n, first):
        problems.append(f"{code}: {rows} {name} rows, want {min(n, first)} of {n}")
    hidden = max(0, n - first)
    more = card.get(f"{name}_more")
    if hidden and _int(more) != hidden:
        problems.append(f"{code}: '+N more' for {name} says {more!r}, want {hidden}")
    if not hidden and more is not None:
        problems.append(f"{code}: a '+N more' Button for {name} with nothing hidden")
    return problems


def expand_problems(before: int, after: int, hidden: int, tag: str) -> list[str]:
    """R5-G02: "+N more" is a Button that opens N more rows in place."""
    problems = []
    if tag.lower() != "button":
        problems.append(f"'+N more' is a {tag!r}, not a button")
    if after - before != hidden:
        problems.append(f"'+N more' opened {after - before} rows, want {hidden}")
    return problems


def account_row_problems(api_items: list[Mapping], rows: list[Mapping]) -> list[str]:
    """R5-A03: each API account has a row with its `people_label` and one stage bar whose
    segments equal `stage_counts` (a stage with 0 people has no segment).

    `rows`: [{id, label, bars, segments: {stage: n}}] as the page shows them."""
    if not api_items:
        return ["/api/accounts has no account"]
    problems = []
    by_id = {str(r.get("id")): r for r in rows}
    for item in api_items:
        row = by_id.get(str(item.get("id")))
        name = item.get("name")
        if row is None:
            problems.append(f"{name}: no row")
            continue
        label = str(row.get("label") or "").strip()
        if label != str(item.get("people_label")):
            problems.append(f"{name}: shows {label!r}, API {item.get('people_label')!r}")
        if _int(row.get("bars")) != 1:
            problems.append(f"{name}: {row.get('bars')} stage bars, want 1")
        want = {str(k): _int(v) for k, v in _pairs(item.get("stage_counts")) if _int(v) > 0}
        got = {str(k): _int(v) for k, v in _pairs(row.get("segments"))}
        if got != want:
            problems.append(f"{name}: stage bar {got}, API {want}")
    return problems


def legend_names_problems(api_legend: list[Mapping], legend_text: str) -> list[str]:
    """R5-A03: the page legend names every tag of `tags_legend` (Qualified, Engaged, ...)."""
    if not api_legend:
        return ["/api/accounts has no tags_legend"]
    return [
        f"the legend does not name {t.get('tag')!r}"
        for t in api_legend
        if str(t.get("tag")) not in legend_text
    ]


def timeline_problems(where: str, api_months: list[Mapping], facts: Mapping) -> list[str]:
    """A `[data-timeline]` strip: one row per month (at most 12), the API months in order when
    the API has touches; an empty line when it has none."""
    if _int(facts.get("strips")) != 1:
        return [f"{where}: {facts.get('strips')} [data-timeline] strips, want 1"]
    problems = []
    months = _strings(facts.get("months"))
    if len(months) > TIMELINE_MONTHS:
        problems.append(f"{where}: {len(months)} month rows, want at most {TIMELINE_MONTHS}")
    touched = any(m.get("touches") for m in api_months)
    want = [str(m.get("month")) for m in api_months]
    if touched and months != want:
        problems.append(f"{where}: month rows {months[:3]}, API {want[:3]}")
    if not touched and not facts.get("empty"):
        problems.append(f"{where}: no touches but no empty line")
    return problems


def company_drawer_problems(facts: Mapping) -> list[str]:
    """R5-A03: the company drawer has no Evidence column or row."""
    text = str(facts.get("text") or "")
    return ["the company drawer shows 'Evidence'"] if EVIDENCE.search(text) else []


def person_drawer_problems(item: Mapping, facts: Mapping) -> list[str]:
    """R5-P03: "Follow-up by" with the tooltip "from the follow-up rule" (Q-R5-16); the papers Button reads
    `papers_label` and its popover lists `papers[]` as plain links (no underline, rel
    noopener, a new tab); the working_on source shows when the API has one.

    `facts`: label, tip (the trigger's name), tooltip (the open tooltip's text), papers_text,
    links [{text, href, rel, target, decoration}], working_source."""
    problems = []
    if FOLLOW_UP_LABEL not in str(facts.get("label") or ""):
        problems.append(f"no '{FOLLOW_UP_LABEL}' label ({facts.get('label')!r})")
    for key in ("tip", "tooltip"):
        if FOLLOW_UP_RULE not in str(facts.get(key) or ""):
            problems.append(f"the Follow-up by {key} does not say {FOLLOW_UP_RULE}")
    papers_text = str(facts.get("papers_text") or "").strip()
    if papers_text != str(item.get("papers_label")):
        problems.append(f"papers reads {papers_text!r}, API {item.get('papers_label')!r}")
    links = [lk for lk in facts.get("links") or [] if isinstance(lk, Mapping)]  # type: ignore[attr-defined]
    shown = [(str(lk.get("text") or "").strip(), lk.get("href")) for lk in links]
    papers = [p for p in item.get("papers") or [] if isinstance(p, Mapping)]  # type: ignore[attr-defined]
    want = [(str(p.get("text")), p.get("href")) for p in papers]
    if shown != want:
        problems.append(f"popover links {shown}, API {want}")
    for link in links:
        href = link.get("href")
        if "noopener" not in str(link.get("rel") or ""):
            problems.append(f"paper link {href!r} has no rel noopener")
        if link.get("target") != "_blank":
            problems.append(f"paper link {href!r} does not open a new tab")
        if "underline" in str(link.get("decoration") or ""):
            problems.append(f"paper link {href!r} is underlined")
    working = item.get("working_on")
    source = working.get("source") if isinstance(working, Mapping) else None
    if source and str(source) not in str(facts.get("working_source") or ""):
        problems.append(f"working_on source {source!r} not shown")
    return problems


def seeded_person_problems(item: Mapping, facts: Mapping) -> list[str]:
    """R5-P03: the seeded person (saley.qa.seed) has papers in the API and a papers Button on
    the page, so the popover is really tested; a broken seed is a fail."""
    problems = []
    if not item.get("papers"):
        problems.append("the seeded person has no papers in /api/people")
    if not facts.get("has_papers_button"):
        problems.append("no papers Button in the drawer")
    return problems


# ---------------------------------------------------------------- T15: Signals, Suggestions, Events

CATEGORY_NAMES = (
    "Hiring",
    "Funding",
    "Product launch",
    "Research",
    "Partnership",
    "Opportunity",  # D239
    "Event",
)
ALL_CHIP = "All"
SOURCE_LETTER = re.compile(r"^[A-L]$")  # the registry source groups (D156): never a chip
CITY_NAMES = frozenset({"Bangalore", "London", "Other"})  # D160
NOTHING_NEW = "Nothing new since your last visit"  # words.ts nothingNew, words.py NOTHING_NEW
LEFT_OUT_WORDS = re.compile(r"(?i)\bleft out\b")
SEEN_BODY_KEYS = frozenset({"item_type", "ids"})  # the legacy body of POST /api/seen


def seen_tab_problems(where: str, api: Mapping, facts: Mapping) -> list[str]:
    """R5-N03: the New and Seen earlier tabs show the counts of the page's own last answer, the
    list shows the answer's items (at most 10), no "left out" text, and an empty New tab says
    "Nothing new since your last visit" (and only then).

    `api`: the answer (new_total, seen_total, items). `facts`: tabs {new, seen: data-count},
    pressed ("new"/"seen"), rows (int), text (the view text), nothing_new (bool).
    """
    if not api:
        return [f"{where}: the page asked no list (no answer to compare)"]
    raw_tabs = facts.get("tabs")
    tabs: Mapping = raw_tabs if isinstance(raw_tabs, Mapping) else {}
    problems = []
    if set(tabs) != {"new", "seen"}:
        problems.append(f"{where}: tabs {sorted(tabs)}, want new and seen")
    for tab, key in (("new", "new_total"), ("seen", "seen_total")):
        shown, want = str(tabs.get(tab, "")), str(api.get(key, ""))
        if shown != want:
            problems.append(f"{where}: the {tab} tab says {shown!r}, the API {key} is {want}")
    rows, items = _int(facts.get("rows")), len(api.get("items") or [])
    if rows > PAGE_SIZE:
        problems.append(f"{where}: {rows} rows on one page, want at most {PAGE_SIZE}")
    if rows != items:
        problems.append(f"{where}: {rows} rows shown, the answer has {items}")
    text = str(facts.get("text") or "")
    if LEFT_OUT_WORDS.search(text):
        problems.append(f"{where}: a 'left out' line is on the page")
    empty_new = facts.get("pressed") == "new" and items == 0
    nothing = bool(facts.get("nothing_new"))
    if empty_new and (not nothing or NOTHING_NEW not in text):
        problems.append(f"{where}: the empty New tab does not say '{NOTHING_NEW}'")
    if nothing and not empty_new:
        problems.append(f"{where}: '{NOTHING_NEW}' shows with items or on Seen earlier")
    return problems


def seen_post_problems(
    where: str, item_type: str, items: Iterable[Mapping], posts: Iterable[Mapping]
) -> list[str]:
    """R5-N03: after a New page is drawn, ONE POST /api/seen marks exactly its unseen items,
    with the legacy body {item_type, ids}. No unseen item: no mark at all."""
    unseen = {_int(i.get("id")) for i in items if not i.get("seen")}
    mine = [p for p in posts if p.get("item_type") == item_type]
    if not unseen:
        return [f"{where}: {len(mine)} seen marks for a page with nothing unseen"] if mine else []
    if len(mine) != 1:
        return [f"{where}: {len(mine)} seen marks of {item_type}, want 1"]
    sent = {_int(i) for i in mine[0].get("ids") or []}
    problems = []
    if sent != unseen:
        problems.append(f"{where}: the mark sent {sorted(sent)[:5]}, shown {sorted(unseen)[:5]}")
    extra = sorted(set(mine[0]) - SEEN_BODY_KEYS)
    if extra:
        problems.append(f"{where}: the mark body has extra keys {extra}")
    return problems


def category_chip_problems(chips: Iterable[str]) -> list[str]:
    """R5-N05: the chips are exactly All + the seven names (D156, D239), never a source letter."""
    found = [str(c).strip() for c in chips]
    problems = []
    if found != [ALL_CHIP, *CATEGORY_NAMES]:
        problems.append(f"category chips {found}, want {[ALL_CHIP, *CATEGORY_NAMES]}")
    letters = [c for c in found if SOURCE_LETTER.match(c)]
    if letters:
        problems.append(f"category chips with a source letter: {letters}")
    return problems


def category_pick_problems(
    want: str, urls: Iterable[str], answers: Iterable[Mapping], rows: Iterable[str]
) -> list[str]:
    """R5-N05: picking `want` asks /api/signals with cat=<want> (never a letter); the answer
    counts at least one story (the QA seed's Funding story, on New or Seen earlier); rows show
    on one of the two tabs, and every row shown is of that category."""
    cats = [dict(parse_qsl(urlsplit(u).query)).get("cat") for u in urls]
    problems = []
    if want not in cats:
        problems.append(f"no /api/signals request with cat={want}: {cats}")
    letters = [c for c in cats if c and SOURCE_LETTER.match(c)]
    if letters:
        problems.append(f"a request asked a source letter: cat={letters}")
    total = max((_int(a.get("new_total")) + _int(a.get("seen_total")) for a in answers), default=0)
    if total < 1:
        problems.append(f"cat={want} gave no story (the QA seed has one)")
    shown = list(rows)
    if not shown:
        problems.append(f"cat={want}: no row on New or Seen earlier")
    wrong = sorted({r for r in shown if r != want})
    if wrong:
        problems.append(f"cat={want}: rows of other categories {wrong}")
    return problems


def events_problems(facts: Mapping) -> list[str]:
    """R5-E02: City and Venue headers; registered rows first, each with the Registered tag (and
    only those); City is Bangalore, London or Other; the speaker-match "coming soon" badge."""
    problems = []
    headers = [h.strip() for h in _strings(facts.get("headers"))]
    for name in ("City", "Venue"):
        if name not in headers:
            problems.append(f"no {name} column: {headers}")
    raw = facts.get("rows")
    rows = [r for r in raw if isinstance(r, Mapping)] if isinstance(raw, list) else []
    if not rows:
        return [*problems, "no event row"]
    flags = [bool(r.get("registered")) for r in rows]
    if flags != sorted(flags, reverse=True):
        problems.append(f"registered events are not first: {flags}")
    if all(flags) or not any(flags):
        problems.append(f"the page needs a registered and an unregistered row: {flags}")
    wrong = [r.get("name") for r in rows if bool(r.get("badge")) != bool(r.get("registered"))]
    if wrong:
        problems.append(f"Registered tag wrong on: {wrong[:5]}")
    cities = sorted({str(r.get("city")) for r in rows} - CITY_NAMES)
    if cities:
        problems.append(f"City values outside {sorted(CITY_NAMES)}: {cities}")
    if _int(facts.get("soon")) < 1:
        problems.append('no [data-soon="speaker_match"] badge')
    return problems


def seeded_event_problems(rows: list[Mapping], seeded: list[tuple]) -> list[str]:
    """R5-E02: the QA events (saley.qa.seed.EVENTS: key, name, location, days, registered)
    show with the City the rule gives and the whole Location as Venue; the registered one
    comes before the others although it starts last. The first (registered) seeded event must
    be on the page; the others are checked when the page shows them."""
    names = [str(r.get("name")) for r in rows]
    by_name = {str(r.get("name")): r for r in rows}
    problems = []
    for at, (_key, name, location, _days, registered) in enumerate(seeded):
        row = by_name.get(name)
        if row is None:
            if at == 0:
                problems.append(f"the seeded event {name!r} is not on the page")
            continue
        city = (
            "Bangalore"
            if "Bengaluru" in location
            else "London"
            if "London" in location
            else "Other"
        )
        if row.get("city") != city or row.get("venue") != location:
            problems.append(f"{name}: city {row.get('city')!r}, venue {row.get('venue')!r}")
        if bool(row.get("registered")) != registered:
            problems.append(f"{name}: registered {row.get('registered')}, want {registered}")
    if seeded and seeded[0][1] in names:
        first = names.index(seeded[0][1])
        if any(n in names and names.index(n) < first for _k, n, *_ in seeded[1:]):
            problems.append(f"the registered seeded event {seeded[0][1]!r} is not first")
    return problems


def _pairs(value: object) -> list[tuple[object, object]]:
    return list(value.items()) if isinstance(value, Mapping) else []


def _strings(value: object) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list | tuple | set) else []


def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0
