"""People drawer read model: Follow-up by (D31), papers, working_on source, timeline (R5 T6).

Why a module of its own: app.py is near the line ceiling. Link text comes only from stored
data (a stored title, or the host and the last path part). A link is shown, never fetched.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urlsplit

from sqlalchemy import Connection

from saley.core.clock import day_label, tz
from saley.web import queries, timeline
from saley.web import words as w

PAPER_TEXT_MAX = 40  # characters, "…" included
PAPERS_MAX = 10
LINK_BAD = re.compile(r"[\s<>\"'\\\x00-\x1f\x7f]")  # a backslash is a slash to a browser
LINK_SPLIT = re.compile(r"[\s,;]+")
OWNER_SOURCES = {
    "first_contact": w.OWNER_SRC_FIRST,
    "li_addition": w.OWNER_SRC_LI,
    "first_contact_type": w.OWNER_SRC_TYPE,
    "default": w.OWNER_SRC_DEFAULT,
}
# The R2 wording (R2-P01): tab and column, so the drawer and the list say the same.
SHEET_COLUMNS = {"about_poc": w.SRC_ABOUT, "paper_link": w.SRC_PAPER}
SOURCE_OTHER = {"meeting_note": w.SRC_NOTE, "mail": w.SRC_MAIL}


def safe_href(value: str | None) -> str | None:
    """Only a plain http(s) URL with a host and no user info. Else None (stays text)."""
    candidate = (value or "").strip()
    if not candidate or LINK_BAD.search(candidate):
        return None
    try:
        parts = urlsplit(candidate)
        host = parts.hostname
    except ValueError:
        return None
    # An ASCII host only: a look-alike (IDN) host shows one name and opens another.
    ok = parts.scheme in ("http", "https") and host and host.isascii() and parts.username is None
    return candidate if ok and parts.password is None else None


def cut(text: str, limit: int = PAPER_TEXT_MAX) -> str:
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def derived_text(href: str) -> str:
    """Host plus the last path part. Both come from the URL itself, so nothing is invented."""
    parts = urlsplit(href)
    host = (parts.hostname or "").removeprefix("www.")
    last = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1])
    # A decoded escape may hide a control or bidi mark (U+202E) that flips the shown text.
    last = "".join(c for c in last if unicodedata.category(c) not in ("Cc", "Cf"))
    return f"{host}/{last}" if last else host


def stored_titles(conn: Connection, urls: list[str]) -> dict[str, str]:
    """A title kept for the exact URL: a search hit, else a working_on fact with text."""
    if not urls:
        return {}
    found: dict[str, str] = {}
    for sql in (
        "select url, title as t from v_search_hit where url = any(:u) and title <> '' "
        "order by found_at, id",
        "select url, text as t from v_fact_working_on "
        "where url = any(:u) and coalesce(text, '') <> ''",
    ):
        for r in queries.rows(conn, sql, u=urls):
            if r["t"].strip():
                found[r["url"]] = r["t"]
    return found


def papers(conn: Connection, paper_url: str | None) -> list[dict[str, str]]:
    urls = [u for u in LINK_SPLIT.split((paper_url or "").strip()) if safe_href(u)]
    urls = list(dict.fromkeys(urls))[:PAPERS_MAX]
    titles = stored_titles(conn, urls)
    return [{"text": cut(titles.get(u) or derived_text(u)), "href": u} for u in urls]


def papers_label(n: int) -> str:
    return w.fill_count(w.PAPERS_ONE, w.PAPERS, n)


def follow_up_by(conn: Connection, item: dict[str, Any]) -> dict[str, Any]:
    """D31: the owner stored by the sheet sync, with the rule step that named them."""
    short = item.get("owner")
    name = None
    if short:
        found = queries.rows(conn, "select name from v_team where short = :s", s=short)
        name = found[0]["name"] if found else None  # not on the team: no raw sheet code
    source = OWNER_SOURCES.get(item.get("owner_source") or "")
    return {
        "name": name,
        "source": source if name else None,
        "rule": w.FOLLOW_UP_RULE,
    }


def working_on_source(raw: dict[str, Any] | None) -> str | None:
    """The sheet column name, or "note <date>" for a meeting note. None without a fact."""
    if not raw or not (raw.get("text") or raw.get("url")):
        return None
    kind = str(raw.get("source_type"))
    if kind in SHEET_COLUMNS:
        return SHEET_COLUMNS[kind]
    seen = raw.get("observed_at")
    if kind == "meeting_note" and isinstance(seen, datetime):
        return w.fill(w.NOTE_SOURCE, day=day_label(seen.astimezone(tz()).date()))
    return SOURCE_OTHER.get(kind, w.SRC_OTHER)


def decorate(conn: Connection, item: dict[str, Any], raw_working_on: dict[str, Any] | None) -> None:
    """Add follow_up_by, papers, papers_label and timeline; set working_on.source. In place."""
    item["follow_up_by"] = follow_up_by(conn, item)
    item["papers"] = papers(conn, item.get("paper_url"))
    item["papers_label"] = papers_label(len(item["papers"]))
    item["timeline"] = timeline.for_person(conn, item["id"])
    source = working_on_source(raw_working_on)
    if source:
        item["working_on"]["source"] = source
