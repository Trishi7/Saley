"""Browser checks of R5 T14: Q4 goals, Accounts, People and the drawers (matrix R5.yaml).

goals_cards (R5-G02), accounts_page (R5-A03), people_drawer (R5-P03). A file of its own:
`checks_r5.py` is near the 800-line ceiling. `checks_r5` imports these names, so the CLI finds
them with the other R5 checks (run on `/`). The rules live in `r5_rules.py` (pure, tested);
this file only reads the page. Read only: no check here presses a control that writes.
"""

from __future__ import annotations

from playwright.sync_api import Page

from qa_browser.core import QA_USER, SETTLE_MS, WIDE, Run, settle
from qa_browser.r5_rules import (
    account_row_problems,
    company_drawer_problems,
    expand_problems,
    goal_card_problems,
    legend_names_problems,
    page_search_problems,
    person_drawer_problems,
    seeded_person_problems,
    timeline_problems,
    view_url,
)

QA_PERSON = "QA Seed Research"  # saley.qa.seed: two papers and a working_on fact (R5 T14)
PAGE_SIZE = 10
GOAL_FACTS = """() => [...document.querySelectorAll('section.view.on [data-goal]')].map((card) => {
  const people = card.querySelector('[data-goal-people]');
  const blockers = card.querySelector('[data-goal-blockers]');
  const more = (box) => {
    const b = box && box.querySelector('[data-more]');
    return b ? Number(b.dataset.more) : null;
  };
  const ring = card.querySelector('[data-ring]');
  return {code: card.dataset.goal, ring: ring ? ring.innerText : '',
    people_box: !!(people && (people.querySelector('table') || people.querySelector('p'))),
    people_rows: people ? people.querySelectorAll('[data-person]').length : 0,
    people_more: more(people),
    blockers_box: !!(blockers && (blockers.querySelector('ul') || blockers.querySelector('p'))),
    blockers_rows: blockers ? blockers.querySelectorAll('[data-blocker]').length : 0,
    blockers_more: more(blockers)};
})"""
INPUT_FACTS = """() => [...document.querySelectorAll('section.view.on input')]
  .filter((n) => n.getClientRects().length)
  .map((n) => ({id: n.id, type: n.type, label: n.getAttribute('aria-label') || '',
    placeholder: n.placeholder || ''}))"""
ACCOUNT_FACTS = """() => [...document.querySelectorAll('section.view.on [data-row][data-account]')]
  .map((row) => {
    const segments = {};
    row.querySelectorAll('[data-stagebar] [data-stage]').forEach((s) => {
      segments[s.dataset.stage] = Number(s.dataset.n);
    });
    const label = row.querySelector('[data-people-label]');
    return {id: row.dataset.account, label: label ? label.innerText : '',
      bars: row.querySelectorAll('[data-stagebar]').length, segments};
  })"""
LEGEND_TEXT = """() => {
  const legend = document.querySelector('section.view.on [data-legend]');
  return legend ? legend.innerText : '';
}"""
DRAWER_FACTS = """() => {
  const drawer = document.querySelector('.drawer.on');
  if (!drawer) return null;
  const strips = drawer.querySelectorAll('[data-timeline]');
  return {text: drawer.innerText, strips: strips.length,
    months: [...drawer.querySelectorAll('[data-timeline] [data-month]')]
      .map((m) => m.dataset.month),
    empty: !!drawer.querySelector('[data-timeline][data-empty]')};
}"""
PERSON_FACTS = """() => {
  const drawer = document.querySelector('.drawer.on');
  if (!drawer) return null;
  const label = drawer.querySelector('[data-follow-up]');
  const tip = drawer.querySelector('[data-follow-up-tip]');
  const papers = drawer.querySelector('[data-papers]');
  const source = drawer.querySelector('[data-working-source]');
  return {label: label ? label.innerText : '', tip: tip ? tip.getAttribute('aria-label') : '',
    papers_text: papers ? papers.innerText : '', has_papers_button: !!(papers &&
      papers.tagName === 'BUTTON'), working_source: source ? source.innerText : ''};
}"""
TOOLTIP_TEXT = """() => [...document.querySelectorAll('[role=tooltip], [data-slot=tooltip-content]')]
  .map((n) => n.innerText).join(' ')"""
PAPER_LINKS = """() => [...document.querySelectorAll('[data-papers-list] a')].map((a) => ({
  text: a.innerText, href: a.getAttribute('href'), rel: a.getAttribute('rel') || '',
  target: a.getAttribute('target') || '', decoration: getComputedStyle(a).textDecorationLine}))"""


def api_json(page: Page, base: str, path: str) -> dict:
    """GET an API view as the QA user (the page's own view of it); {} when it fails."""
    response = page.request.get(f"{base}{path}", headers={"x-saley-as": QA_USER})
    return response.json() if response.ok else {}


def open_view(page: Page, base: str, entry: str, view: str) -> None:
    page.set_viewport_size({"width": WIDE, "height": 900})
    page.goto(view_url(base, entry, view))
    page.reload()  # a hash-only move keeps the old page state; start clean
    settle(page)


def goals_cards(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-G02: one card per goal from /api/goals: a ring (`[data-ring]`, the API percent or
    "—"), a compact people table, a blockers list, and "+N more" Buttons that open the rest
    in place. No page search input."""
    open_view(page, base, entry, "goals")
    goals = api_json(page, base, "/api/goals").get("items") or []
    cards = page.evaluate(GOAL_FACTS)
    problems = goal_card_problems(goals, cards)
    run.expect(not problems, f"goals_cards: {problems[:5]}")
    problems = page_search_problems("goals", page.evaluate(INPUT_FACTS))
    run.expect(not problems, f"goals_cards: {problems}")
    expanded = expand_first_more(page, run)
    run.notes["goals_cards"] = [f"{len(cards)} cards equal /api/goals", expanded]


def expand_first_more(page: Page, run: Run) -> str:
    """Press the first "+N more" and count the rows of its list before and after."""
    button = page.locator("section.view.on [data-more]").first
    if not button.count():
        return "no list longer than its first rows; '+N more' not pressed"
    hidden = int(button.get_attribute("data-more") or 0)
    box = button.locator("xpath=ancestor::*[@data-goal-people or @data-goal-blockers][1]")
    rows = "[data-person], [data-blocker]"
    before = box.locator(rows).count()
    tag = button.evaluate("(b) => b.tagName")
    button.click()
    page.wait_for_timeout(SETTLE_MS // 2)
    after = box.locator(rows).count()
    problems = expand_problems(before, after, hidden, tag)
    run.expect(not problems, f"goals_cards: {problems}")
    return f"'+{hidden} more' opened {after - before} rows"


def accounts_page(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-A03: the legend names every account tag; each row shows `people_label` and a stage
    bar equal to `stage_counts`; the company drawer has no Evidence and a timeline strip of at
    most 12 month rows."""
    open_view(page, base, entry, "accounts")
    data = api_json(page, base, f"/api/accounts?filter=all&limit={PAGE_SIZE}&offset=0")
    problems = legend_names_problems(data.get("tags_legend") or [], page.evaluate(LEGEND_TEXT))
    run.expect(not problems, f"accounts_page: {problems}")
    rows = page.evaluate(ACCOUNT_FACTS)
    problems = account_row_problems(data.get("items") or [], rows)
    run.expect(not problems, f"accounts_page: {problems[:5]}")
    opener = page.locator("section.view.on [data-row][data-account] [data-open-account]").first
    if not run.expect(opener.count() == 1, "accounts_page: no company to open"):
        return
    account_id = opener.locator("xpath=ancestor::tr[1]").get_attribute("data-account")
    opener.click()
    page.wait_for_timeout(SETTLE_MS)
    page.wait_for_selector(".drawer.on [data-people-label]")
    facts = page.evaluate(DRAWER_FACTS)
    if not run.expect(facts is not None, "accounts_page: the company drawer did not open"):
        return
    detail = api_json(page, base, f"/api/accounts/{account_id}").get("item") or {}
    problems = company_drawer_problems(facts)
    problems += timeline_problems("company drawer", detail.get("timeline") or [], facts)
    run.expect(not problems, f"accounts_page: {problems}")
    page.keyboard.press("Escape")
    page.wait_for_timeout(SETTLE_MS)
    run.notes["accounts_page"] = [
        f"{len(rows)} rows equal /api/accounts",
        f"drawer of account {account_id}: {len(facts['months'])} month rows",
    ]


def people_drawer(page: Page, base: str, run: Run, entry: str) -> None:
    """R5-P03: open the seeded "QA Seed Research" drawer (never another row: a broken seed
    must fail, not compare [] with []): "Follow-up by" with the tooltip "from the follow-up rule", the
    papers Button text equals `papers_label` and its popover lists `papers[]` as plain links,
    a `[data-timeline]` strip, and the working_on source when the API has one."""
    open_view(page, base, entry, "people?filter=all")
    page.locator("#ppl-search").fill(QA_PERSON)
    page.wait_for_timeout(SETTLE_MS)
    settle(page)
    row = page.locator("section.view.on [data-row][data-person]", has_text=QA_PERSON)
    rows = row.count()
    if not run.expect(rows == 1, f"people_drawer: {rows} '{QA_PERSON}' rows, want 1"):
        return
    person_id = row.first.get_attribute("data-person")
    row.first.locator("[data-open-person]").click()
    page.wait_for_selector(".drawer.on [data-follow-up]")
    item = api_json(page, base, f"/api/people/{person_id}").get("item") or {}
    facts = page.evaluate(PERSON_FACTS) or {}
    page.locator(".drawer.on [data-follow-up-tip]").focus()
    page.wait_for_timeout(SETTLE_MS // 2)
    facts["tooltip"] = page.evaluate(TOOLTIP_TEXT)
    facts["links"] = open_papers(page) if facts.get("has_papers_button") else []
    problems = seeded_person_problems(item, facts) + person_drawer_problems(item, facts)
    problems += timeline_problems(
        "person drawer", item.get("timeline") or [], page.evaluate(DRAWER_FACTS) or {}
    )
    run.expect(not problems, f"people_drawer: {problems[:5]}")
    page.keyboard.press("Escape")
    page.wait_for_timeout(SETTLE_MS)
    if page.locator(".drawer.on").count():
        page.keyboard.press("Escape")
        page.wait_for_timeout(SETTLE_MS)
    run.expect(page.locator(".drawer.on").count() == 0, "people_drawer: Escape did not close")
    run.notes["people_drawer"] = [
        f"person {person_id}: {item.get('papers_label')}, source "
        f"{(item.get('working_on') or {}).get('source') or '—'}"
    ]


def open_papers(page: Page) -> list[dict]:
    """Open the papers popover and read its links (the popover closes with Escape after)."""
    page.locator(".drawer.on [data-papers]").click()
    page.wait_for_selector("[data-papers-list]")
    links = page.evaluate(PAPER_LINKS)
    page.keyboard.press("Escape")
    page.wait_for_timeout(SETTLE_MS // 2)
    return links
