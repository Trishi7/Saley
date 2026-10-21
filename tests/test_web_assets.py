"""The dashboard v2 build and how this API serves it (R5 T10: D145, D146, D147).

Why most tests use a small fixture dist: pytest must not need Node. S01 and S03 check the
serving code against a tmp dist that has the same shape as a Vite build. S02 reads the real
build; it skips only when there is none, and gate step 1 fails in that case (build stamp).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saley.qa import build_stamp
from saley.web import next_app
from saley.web import words as w
from saley.web.app import create_app
from tests.conftest import sign_in

ROOT = Path(__file__).resolve().parents[1]
WEB_SRC = ROOT / "web" / "src"
REAL_DIST = ROOT / "src" / "saley" / "web" / "dist"

FIXTURE_INDEX = """<!doctype html>
<html lang="en"><head>
<meta name="csp-nonce" content="__CSP_NONCE__" />
<title>Saley</title>
<script type="module" crossorigin src="/static/app/assets/index-abc123.js"></script>
<link rel="stylesheet" crossorigin href="/static/app/assets/index-abc123.css">
</head><body><div id="root"></div></body></html>
"""
META_NONCE = re.compile(r'<meta name="csp-nonce" content="([^"]*)"')
HEADER_NONCE = re.compile(r"style-src 'self' 'nonce-([A-Za-z0-9_-]+)'")

# Strings in the built JS that look like URLs but are never fetched. Keep this list tiny.
HARMLESS_URLS = (
    # XML namespace names: React and the SVG code compare them; no request is made.
    "http://www.w3.org/",
    # React's minified error text names its docs page; it is only shown in a message.
    "https://react.dev/errors/",
)
URL_RE = re.compile(rb"(?:https?:)?//[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+[^\s\"'`)<>]*")


@pytest.fixture
def fixture_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(FIXTURE_INDEX, encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_bytes(b"export {};\n")
    (dist / "assets" / "index-abc123.css").write_text("body{}\n", encoding="utf-8")
    monkeypatch.setattr(next_app, "DIST", dist)
    return dist


@pytest.fixture
def client(synced: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def test_index_served_from_dist(fixture_dist: Path, client: TestClient) -> None:
    """R5-S01. During the strangler (D147) the built index is served at /next; T16 moves it
    to /. The built files are under /static/app/ (D145). Like /, it needs a session."""
    away = client.get("/next", follow_redirects=False)
    assert away.status_code == 303 and away.headers["location"] == "/signin"

    sign_in(client)
    page = client.get("/next")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-store"
    assert '<div id="root"></div>' in page.text
    assert "__CSP_NONCE__" not in page.text

    asset = client.get("/static/app/assets/index-abc123.js")
    assert asset.status_code == 200 and asset.text == "export {};\n"
    assert "immutable" in asset.headers["cache-control"]
    assert client.get("/static/app/assets/none.js").status_code == 404
    # The stamp is for the gate on disk; index.html only through /next (T10 reviews).
    for hidden in ("build-stamp.json", "Build-Stamp.JSON", "index.html"):
        assert client.get(f"/static/app/{hidden}").status_code == 404, hidden
    # The legacy app stays at / until the cut-over (D147).
    legacy = client.get("/")
    assert legacy.status_code == 200 and "csp-nonce" not in legacy.text
    assert client.get("/static/app.css").status_code == 200


@pytest.fixture
def missing_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set before `client`, so the /static/app mount also sees the missing folder."""
    missing = tmp_path / "missing"
    monkeypatch.setattr(next_app, "DIST", missing)
    return missing


def test_next_without_a_build_says_so(missing_dist: Path, client: TestClient) -> None:
    """No dist (a laptop before the first build): 503 with a plain line, never a trace. The
    503 and the signed-out redirect carry the fixed CSP, with no nonce."""
    away = client.get("/next", follow_redirects=False)
    assert away.status_code == 303 and "nonce-" not in away.headers["content-security-policy"]
    sign_in(client)
    page = client.get("/next")
    assert page.status_code == 503
    assert page.text == w.NEXT_NOT_BUILT
    assert "nonce-" not in page.headers["content-security-policy"]
    assert client.get("/static/app/assets/x.js").status_code == 404


def test_csp_script_self_style_nonce_per_request(fixture_dist: Path, client: TestClient) -> None:
    """R5-S03 (D146): script-src 'self'; style-src 'self' plus a new nonce per request; never
    unsafe-inline or unsafe-eval; the page's meta nonce equals the header nonce."""
    sign_in(client)
    nonces = []
    for _ in range(2):
        page = client.get("/next")
        csp = page.headers["content-security-policy"]
        assert "script-src 'self';" in csp
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
        header = HEADER_NONCE.search(csp)
        meta = META_NONCE.search(page.text)
        assert header and meta and header.group(1) == meta.group(1)
        nonces.append(header.group(1))
    assert nonces[0] != nonces[1]
    assert len(nonces[0]) >= 16

    # Every other page keeps the fixed policy: no nonce, style-src 'self' only.
    for path in ("/", "/signin", "/healthz"):
        csp = client.get(path).headers["content-security-policy"]
        assert "style-src 'self';" in csp and "nonce-" not in csp
    with pytest.raises(ValueError):
        next_app.csp_with_style_nonce("bad'; script-src *")

    # The built index (fixture and, when present, the real build) has no inline script.
    indexes = [fixture_dist / "index.html"]
    if (REAL_DIST / "index.html").is_file():
        indexes.append(REAL_DIST / "index.html")
    for index in indexes:
        # Without the placeholder the nonce never reaches the page (T10 security review).
        assert next_app.NONCE_PLACEHOLDER in index.read_text(encoding="utf-8"), index
        for tag in re.findall(r"<script\b[^>]*>", index.read_text(encoding="utf-8")):
            assert " src=" in tag, f"inline script in {index}: {tag}"


def test_built_assets_have_no_external_urls() -> None:
    """R5-S02: the built JS, CSS, HTML and fonts hold no external URL (no CDN at runtime,
    egress rule, D148). The gate fails on a missing build in step 1 (build stamp)."""
    if not (REAL_DIST / "index.html").is_file():
        pytest.skip("no build in src/saley/web/dist; run npm --prefix web run build")
    files = [p for p in REAL_DIST.rglob("*") if p.is_file() and p.name != "build-stamp.json"]
    assert any(p.suffix == ".js" for p in files) and any(p.suffix == ".woff2" for p in files)
    found = []
    for path in files:
        for match in URL_RE.findall(path.read_bytes()):
            url = match.decode("latin1")
            if not url.startswith(HARMLESS_URLS):
                found.append(f"{path.name}: {url[:80]}")
    assert found == []


def test_build_stamp_matches_only_its_sources(tmp_path: Path) -> None:
    """D145: the stamp changes with any source; CRLF and LF checkouts give one stamp."""
    web, dist = tmp_path / "web", tmp_path / "dist"
    (web / "src").mkdir(parents=True)
    (web / "index.html").write_bytes(b"<p>a</p>\n")
    (web / "src" / "main.tsx").write_bytes(b"one\ntwo\n")
    lf = build_stamp.source_hash(web)
    (web / "src" / "main.tsx").write_bytes(b"one\r\ntwo\r\n")
    assert build_stamp.source_hash(web) == lf

    assert not build_stamp.check(web, dist).ok  # no build at all
    dist.mkdir()
    (dist / "index.html").write_text("x", encoding="utf-8")
    (dist / "build-stamp.json").write_text(f'{{"sha256": "{lf}"}}', encoding="utf-8")
    assert build_stamp.check(web, dist).ok
    (web / "src" / "main.tsx").write_bytes(b"one\ntwo\nthree\n")
    stale = build_stamp.check(web, dist)
    assert not stale.ok and build_stamp.REBUILD in stale.note
    (dist / "build-stamp.json").write_text("not json", encoding="utf-8")
    assert not build_stamp.check(web, dist).ok


W_USE = re.compile(r"\bW\.(\w+)")


def test_every_word_key_is_used_and_exists() -> None:
    """plan §3.3: every W.key in the app exists in words.ts, and no key is unused."""
    words = (WEB_SRC / "words.ts").read_text(encoding="utf-8")
    keys = {k for k, _ in re.findall(r'^\s*(\w+):\s*"((?:[^"\\]|\\.)*)"', words, re.MULTILINE)}
    used: set[str] = set()
    for path in WEB_SRC.rglob("*"):
        if path.suffix in (".ts", ".tsx") and path.name != "words.ts":
            used |= set(W_USE.findall(path.read_text(encoding="utf-8")))
    assert used - keys == set(), "W keys used but missing in words.ts"
    assert keys - used == set(), "words.ts keys that no code uses"


def test_next_during_a_rebuild_says_not_built(
    fixture_dist: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vite empties dist while it builds: an unreadable index gives the 503 line, not a 500."""
    sign_in(client)

    def gone(*_args: object, **_kw: object) -> str:
        raise FileNotFoundError("index.html")

    monkeypatch.setattr(Path, "read_text", gone)
    page = client.get("/next")
    assert page.status_code == 503 and page.text == w.NEXT_NOT_BUILT
