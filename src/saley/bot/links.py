"""Deep links from a card into the dashboard (docs/09 §7, D261).

Why: a link button opens the page of the thing the card is about and scrolls to it:
`PUBLIC_URL/<page>/<id>?card=<card_id>`. The page set is fixed, so a card can never link
outside the dashboard, and the id is checked, so no row text ends up in a URL. The web side
(`web/deeplink.py`) signs the person in first, keeps this path as `next`, then opens the page.
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlencode

from saley.core.settings import get_settings

# Path name -> the dashboard view it opens (web/src/App.tsx PAGES; hash routes).
PAGES = {
    "today": "today",
    "people": "people",
    "accounts": "accounts",
    "suggestions": "suggest",
    "signals": "signals",
    "events": "events",
    "blockers": "blockers",
    "commitments": "commit",
    "pack": "pack",
    "sources": "sources",
    "asks": "asks",
}
ITEM = re.compile(r"[A-Za-z0-9-]{1,40}")
CARD = re.compile(r"[a-z0-9:-]{1,100}")


def url(page: str, item: object | None = None, card: str | None = None) -> str:
    """The deep link. Raises ValueError for a page or id outside the fixed form."""
    if page not in PAGES:
        raise ValueError(f"unknown page {page!r}")
    path = f"/{page}"
    if item is not None:
        if not ITEM.fullmatch(str(item)):
            raise ValueError("a link id is letters, digits and dashes only")
        path += f"/{quote(str(item), safe='')}"
    if card is not None and not CARD.fullmatch(card):
        raise ValueError("bad card id")
    query = f"?{urlencode({'card': card})}" if card else ""
    return get_settings().public_url.rstrip("/") + path + query
