"""Deep links from a Discord card into the dashboard (docs/09 §7, D261).

Why: a card's link button is `PUBLIC_URL/<page>/<id>?card=<card_id>`. The dashboard is one page
with hash routes, so this route turns the path into `/#<view>?open=<id>&card=<card_id>`; the
page opens the item and outlines it for 3 s (web/src/lib/card-focus.ts). Without a session it
sends the person to sign in first, with this path as `next`.

`safe_next` is the one check for `next`: it takes only this deep-link form on our own site. A
URL, a `//host` path, a backslash or any other text is refused, so the sign-in can never send
anyone to another site (an open redirect).
"""

from __future__ import annotations

import re
from typing import Annotated
from urllib.parse import quote, urlencode

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from saley.bot.links import CARD, ITEM, PAGES

NEXT = re.compile(
    rf"/(?:{'|'.join(PAGES)})(?:/[A-Za-z0-9-]{{1,40}})?(?:\?card=[a-z0-9:-]{{1,100}})?"
)
Card = Annotated[str | None, Query(max_length=100)]


def safe_next(raw: str | None) -> str | None:
    """`raw` when it is a deep link of this site (D261), else None."""
    return raw if raw and NEXT.fullmatch(raw) else None


def _open(request: Request, page: str, item: str | None, card: str | None) -> RedirectResponse:
    from saley.web.auth import session_user  # late: auth imports this module

    if (
        page not in PAGES
        or (item and not ITEM.fullmatch(item))
        or (card and not CARD.fullmatch(card))
    ):
        raise HTTPException(status_code=404, detail="Not Found")
    path = f"/{page}" + (f"/{item}" if item else "") + (f"?card={card}" if card else "")
    if session_user(request) is None:
        return RedirectResponse(f"/signin?next={quote(path, safe='')}", status_code=303)
    params = {k: v for k, v in (("open", item), ("card", card)) if v}
    query = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"/#{PAGES[page]}{query}", status_code=303)


def _routes(router: APIRouter, page: str) -> None:
    def plain(request: Request, card: Card = None) -> RedirectResponse:
        return _open(request, page, None, card)

    def with_item(request: Request, item: str, card: Card = None) -> RedirectResponse:
        return _open(request, page, item, card)

    router.add_api_route(f"/{page}", plain, methods=["GET"], include_in_schema=False)
    router.add_api_route(f"/{page}/{{item}}", with_item, methods=["GET"], include_in_schema=False)


def install(app: FastAPI) -> None:
    """One GET per page of the fixed set, with and without an id. Never a catch-all."""
    router = APIRouter()
    for page in PAGES:
        _routes(router, page)
    app.include_router(router)
