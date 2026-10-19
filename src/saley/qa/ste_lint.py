"""STE lint: gate step 3 (ASD-STE100 style for every UI and bot string).

Why: one check over the string catalogues, so plain short text stays plain and short as the
dashboard grows. Rules: each string is at most 60 characters (a `{name}` placeholder counts
as 4), and no word or phrase from docs/ste-banned-words.txt appears.
Sources: saley/web/words.py, static/words.js, the text and labels in static/index.html, and
the UPPER_CASE string constants of saley/bot.

Run: `uv run python -m saley.qa.ste_lint` (exit 1 on any finding).
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MAX_CHARS = 60
PLACEHOLDER_CHARS = 4
PLACEHOLDER = re.compile(r"\{\w*\}")
JS_STRING = re.compile(r'^\s*(\w+):\s*"((?:[^"\\]|\\.)*)"', re.MULTILINE)
HTML_ATTRS = ("placeholder", "title", "aria-label", "alt")
WEB = ROOT / "src" / "saley" / "web"


@dataclass(frozen=True)
class Finding:
    source: str
    text: str
    problem: str

    def __str__(self) -> str:
        return f"{self.source}: {self.problem}: {self.text!r}"


def visible_length(text: str) -> int:
    return len(PLACEHOLDER.sub("x" * PLACEHOLDER_CHARS, text))


def banned_words(path: Path = ROOT / "docs" / "ste-banned-words.txt") -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [w.strip().lower() for w in lines if w.strip() and not w.startswith("#")]


def check(source: str, text: str, banned: list[str]) -> list[Finding]:
    found = []
    if visible_length(text) > MAX_CHARS:
        found.append(Finding(source, text, f"over {MAX_CHARS} characters"))
    lower = text.lower()
    for word in banned:
        if re.search(rf"\b{re.escape(word)}\b", lower):
            found.append(Finding(source, text, f"banned word '{word}'"))
    return found


def python_strings(path: Path) -> list[tuple[str, str]]:
    """UPPER_CASE = "text" constants of a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.isupper():
                out.append((f"{path.name}:{target.id}", node.value.value))
    return out


def js_strings(path: Path) -> list[tuple[str, str]]:
    body = path.read_text(encoding="utf-8")
    return [
        (f"{path.name}:{key}", value.replace('\\"', '"')) for key, value in JS_STRING.findall(body)
    ]


class _TextNodes(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.skip = 0
        self.items: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.skip += 1
        self.items += [v for k, v in attrs if k in HTML_ATTRS and v]

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text and not self.skip:
            self.items.append(text)


def html_strings(path: Path) -> list[tuple[str, str]]:
    parser = _TextNodes()
    parser.feed(path.read_text(encoding="utf-8"))
    return [(path.name, t) for t in parser.items]


def collect() -> list[tuple[str, str]]:
    strings = python_strings(WEB / "words.py")
    strings += js_strings(WEB / "static" / "words.js")
    strings += html_strings(WEB / "static" / "index.html")
    for path in sorted((ROOT / "src" / "saley" / "bot").glob("*.py")):
        strings += python_strings(path)
    return [(s, t) for s, t in strings if not t.startswith(("http://", "https://"))]


def lint() -> tuple[int, list[Finding]]:
    banned = banned_words()
    strings = collect()
    return len(strings), [f for s, t in strings for f in check(s, t, banned)]


def main() -> int:
    count, findings = lint()
    for f in findings:
        print(f)
    print(f"STE lint: {count} strings, {len(findings)} findings")
    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
