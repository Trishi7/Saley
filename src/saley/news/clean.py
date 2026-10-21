"""Clean step of an edition, in plain code (docs/04 §2): URL hash, fingerprint, AI gate, flag.

Why: no model runs here. A canonical URL hash says "we have it". A fingerprint of the first 12
real title words says "same story from another outlet" (outlets merge). The gate keeps general
feeds from filling the day with noise; what it drops is kept 14 days, never deleted at once.
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

FINGERPRINT_WORDS = 12
MAX_TITLE = 300
MAX_SUMMARY = 1200
TRACKING_PARAMS = frozenset({"fbclid", "gclid", "ref", "ref_src", "cmpid"})
# Only Google adds these. On another site `source` or `output` can pick the page (B4).
GOOGLE_PARAMS = frozenset({"oc", "hl", "gl", "ceid", "output", "source"})
SMALL_WORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "with",
        "from",
        "by",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "has",
        "have",
        "had",
        "will",
        "would",
        "can",
        "could",
        "may",
        "might",
        "into",
        "over",
        "under",
        "after",
        "before",
        "about",
        "says",
        "say",
        "said",
        "new",
        "via",
        "vs",
        "not",
        "no",
    ]
)
# Words and entities that make a general-feed item an AI item (docs/04 §6, D71).
AI_WORDS = frozenset(
    [
        "ai",
        "artificial",
        "intelligence",
        "llm",
        "llms",
        "gpt",
        "chatgpt",
        "genai",
        "generative",
        "model",
        "models",
        "machine",
        "learning",
        "neural",
        "deep",
        "ml",
        "nlp",
        "chatbot",
        "chatbots",
        "agent",
        "agents",
        "agentic",
        "copilot",
        "transformer",
        "transformers",
        "inference",
        "dataset",
        "datasets",
        "annotation",
        "labeling",
        "speech",
        "voice",
        "asr",
        "tts",
        "multimodal",
        "diffusion",
        "embedding",
        "embeddings",
        "robotics",
    ]
)
AI_ENTITIES = frozenset(
    [
        "openai",
        "anthropic",
        "claude",
        "gemini",
        "deepmind",
        "mistral",
        "deepseek",
        "qwen",
        "llama",
        "nvidia",
        "sarvam",
        "krutrim",
        "bhashini",
        "perplexity",
        "xai",
        "grok",
        "cohere",
        "huggingface",
        "midjourney",
        "sora",
    ]
)
_TAG_RE = re.compile(r"<[^>]+>")
_WORD_RE = re.compile(r"[a-z0-9]+")
_SPACE_RE = re.compile(r"\s+")
_OUTLET_SUFFIX = re.compile(r"\s+[-–—|]\s+[^-–—|]{2,60}$")


def strip_html(text: str) -> str:
    """Plain text: tags removed, entities decoded, spaces folded. Feeds carry markup."""
    return _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", text or ""))).strip()


def clean_title(title: str, outlet: str = "") -> str:
    """Strip markup, and the ' - Outlet' tail Google News adds to every headline."""
    text = strip_html(title)
    if outlet:
        suffix = f" - {outlet}"
        if text.lower().endswith(suffix.lower()):
            text = text[: -len(suffix)]
    return text[:MAX_TITLE].strip()


def _is_google(host: str) -> bool:
    return host == "google.com" or host.endswith(".google.com")


def canonical_url(url: str) -> str:
    """One shape per link: lower-case host, no www, no fragment, no tracking, sorted query.

    A link that cannot be parsed (a bad port) keeps its raw text, so one odd item never stops
    an edition.
    """
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return url.strip()
    host = (parts.hostname or "").lower().removeprefix("www.")
    if ":" in host:  # an IPv6 address needs its brackets back
        host = f"[{host}]"
    if port and port not in (80, 443):
        host = f"{host}:{port}"
    drop = TRACKING_PARAMS | GOOGLE_PARAMS if _is_google(host) else TRACKING_PARAMS
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in drop
    )
    path = parts.path.rstrip("/") or ""
    return urlunsplit(("https", host, path, urlencode(query), ""))


def url_hash(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode(), usedforsecurity=False).hexdigest()


def _stem(word: str) -> str:
    for tail in ("ing", "ed", "es", "s"):
        if word.endswith(tail) and len(word) - len(tail) >= 4:
            return word[: -len(tail)]
    return word


def title_words(title: str) -> list[str]:
    """Words of any script, NFKC and case-folded. A combining mark (a Hindi vowel sign) stays
    inside its word: `str.isalnum` alone would cut "है" in two."""
    words: list[str] = []
    current: list[str] = []
    for char in unicodedata.normalize("NFKC", title).casefold():
        if char.isalnum() or unicodedata.category(char).startswith("M"):
            current.append(char)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words


def fingerprint(title: str) -> str:
    """The first 12 real words of the title, stemmed, sorted, hashed (docs/04 §6).

    The same headline from two outlets, or with the words in a different order, gives the same
    value. "Gemini 4 fails safety test" gives a different one. A title with no real word gives
    "": the store then skips the merge and uses the URL hash, so unrelated stories never join.
    """
    words = [_stem(w) for w in title_words(title) if w not in SMALL_WORDS]
    if not words:
        return ""
    key = " ".join(sorted(words[:FINGERPRINT_WORDS]))
    return hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()


def has_ai_signal(text: str) -> bool:
    words = set(_WORD_RE.findall(text.lower()))
    return bool(words & AI_WORDS or words & AI_ENTITIES)


@dataclass(frozen=True)
class NameIndex:
    """Names from the sheet: companies and people. A match sets the flag and skips the gate."""

    companies: tuple[str, ...] = ()
    people: tuple[str, ...] = ()
    _patterns: tuple[tuple[str, str, re.Pattern[str]], ...] = field(
        init=False, repr=False, compare=False, default=()
    )

    def __post_init__(self) -> None:
        patterns = []
        for kind, names in (("company", self.companies), ("person", self.people)):
            for name in sorted(set(names)):
                clean = name.strip()
                if len(clean) < 3:
                    continue
                # Short or one-word names are common words: they must match case-exactly.
                strict = len(clean) < 5 or (" " not in clean and clean.islower())
                flags = 0 if strict else re.IGNORECASE
                pattern = re.compile(rf"(?<!\w){re.escape(clean)}(?!\w)", flags)
                patterns.append((kind, clean, pattern))
        object.__setattr__(self, "_patterns", tuple(patterns))

    def match(self, text: str) -> list[tuple[str, str]]:
        """(kind, name) for every sheet name found in the text, in a stable order."""
        return [(kind, name) for kind, name, pattern in self._patterns if pattern.search(text)]


@dataclass(frozen=True)
class Verdict:
    """What the clean step decided for one item."""

    passes: bool
    reason: str  # "ai_only" | "ai_word" | "flag" | "no_ai_signal"
    flag: bool = False
    name_kind: str | None = None
    names: tuple[str, ...] = ()


def judge(title: str, summary: str, *, ai_only: bool, names: NameIndex | None = None) -> Verdict:
    """Flag by sheet name first (it skips the gate), then the gate (docs/04 §2 d, e)."""
    text = f"{title} {summary}"
    found = names.match(text) if names else []
    if found:
        kinds = {kind for kind, _ in found}
        kind = "person" if "person" in kinds and "company" not in kinds else "company"
        return Verdict(True, "flag", True, kind, tuple(n for _, n in found))
    if ai_only:
        return Verdict(True, "ai_only")
    if has_ai_signal(text):
        return Verdict(True, "ai_word")
    return Verdict(False, "no_ai_signal")
