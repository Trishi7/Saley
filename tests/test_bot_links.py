"""R7-U01: deep links (docs/09 §7, D261).

A card link is PUBLIC_URL/<page>/<id>?card=<card_id> from a fixed page set. Signed out, the
link goes to sign-in with itself as `next`; after sign-in the browser lands on the page with
the card id; a foreign `next` is refused (no open redirect).
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saley.bot import links
from saley.core.settings import get_settings
from saley.web.app import create_app
from saley.web.deeplink import safe_next


def test_a_link_is_public_url_page_id_and_card() -> None:
    base = get_settings().public_url.rstrip("/")
    assert (
        links.url("people", 412, "m5-c412-20261024") == f"{base}/people/412?card=m5-c412-20261024"
    )
    assert links.url("pack", "2026-W43") == f"{base}/pack/2026-W43"
    assert links.url("today", card="m1-sid-20261023") == f"{base}/today?card=m1-sid-20261023"


@pytest.mark.parametrize(
    ("page", "item", "card"),
    [
        ("admin", None, None),
        ("people", "41 2", None),
        ("people", "../x", None),
        ("today", None, "M1 x"),
    ],
)
def test_a_link_outside_the_form_is_refused(page: str, item: str | None, card: str | None) -> None:
    with pytest.raises(ValueError):
        links.url(page, item, card)


@pytest.mark.parametrize(
    "raw",
    [
        "https://evil.example/people/1",
        "//evil.example/people/1",
        "/\\evil.example",
        "/people/1?card=x&next=https://evil.example",
        "/api/me",
        "/people/1#x",
        "javascript:alert(1)",
        "",
    ],
)
def test_safe_next_refuses_anything_but_our_deep_links(raw: str) -> None:
    assert safe_next(raw) is None


def test_safe_next_keeps_a_deep_link() -> None:
    assert safe_next("/people/412?card=m5-c412-20261024") == "/people/412?card=m5-c412-20261024"
    assert safe_next("/today") == "/today"


def test_signed_out_link_goes_to_sign_in_then_lands_on_the_card(db: Session) -> None:
    path = "/people/412?card=m5-c412-20261024"
    with TestClient(create_app()) as client:
        first = client.get(path, follow_redirects=False)
        assert first.status_code == 303
        assert first.headers["location"] == f"/signin?next={quote(path, safe='')}"
        page = client.get(first.headers["location"])
        assert 'name="next" value="/people/412?card=m5-c412-20261024"' in page.text
        signed = client.post("/auth/mock", data={"next": path}, follow_redirects=False)
        assert signed.status_code == 303 and signed.headers["location"] == path
        landed = client.get(path, follow_redirects=False)
        assert landed.headers["location"] == "/#people?open=412&card=m5-c412-20261024"


def test_a_foreign_next_is_refused_at_sign_in(db: Session) -> None:
    with TestClient(create_app()) as client:
        page = client.get("/signin", params={"next": "https://evil.example/"})
        assert "evil" not in page.text
        signed = client.post(
            "/auth/mock", data={"next": "https://evil.example/"}, follow_redirects=False
        )
        assert signed.headers["location"] == "/"


def test_a_bad_link_is_a_404(db: Session) -> None:
    with TestClient(create_app()) as client:
        assert client.get("/people/41;2", follow_redirects=False).status_code == 404
        assert client.get("/today?card=BAD CARD", follow_redirects=False).status_code == 404
