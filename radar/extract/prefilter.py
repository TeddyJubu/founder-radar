"""The free cascade that runs before any paid call (05-pipeline §3.1).

Every article that reaches the model is a chance to pollute the database, so
this is about **quality** at least as much as cost. Cheapest checks first:
a URL regex costs nothing, boilerplate removal costs a few milliseconds, and
only what survives is ever paid for.

Nothing in this module calls the network or an AI provider.
"""

from __future__ import annotations

import html as _html
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterator

MIN_TEXT_CHARS = 400
SIGNAL_WINDOW = 6000
MAX_ORGS = 6

# --- the four gates, verbatim from 05-pipeline §3.1 -------------------------

INDEX_URL = re.compile(r"/(tag|category|author|page|archive|topics?)/", re.I)

# NOTE the first alternative is anchored to a listicle noun. A bare ^\d+\s
# would drop "3 years after founding, Acme raises £2m" — a real article.
ROUNDUP = re.compile(
    r"(^\d+\s+(best|top|things|startups?|companies|founders|reasons))"
    r"|(\b(top|best|biggest)\s+\d+)|(round-?up)|(weekly|monthly)\s+(digest|wrap)"
    r"|(newsletter)|(deals? of the (week|month))|(funding round-?up)"
    r"|(\d+\s+(startups?|companies|founders)\s+to\s+watch)",
    re.I,
)

SIGNAL = re.compile(
    r"\b(raise[sd]?|raising|secure[sd]|closes?|closed|pre-?seed|seed round"
    r"|series\s+[a-c]|spin-?out|spin-?off|founded|launch(es|ed)?|grant"
    r"|Innovate UK|incorporat)",
    re.I,
)

ORG = re.compile(
    r"\b[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+){0,2}"
    r"\s+(?:Ltd|Limited|Inc|AI|Labs|Technologies)\b"
)


@dataclass(frozen=True)
class PrefilterResult:
    """Iterable so `ok, reason = prefilter(...)` keeps working."""

    ok: bool
    reason: str
    text: str = ""
    jsonld: dict = field(default_factory=dict)
    orgs: tuple[str, ...] = ()

    def __iter__(self) -> Iterator[Any]:
        yield self.ok
        yield self.reason


# --- boilerplate removal ----------------------------------------------------

_DROP_TAGS = re.compile(
    r"<(script|style|noscript|template|svg|nav|header|footer|aside|form|figure)\b"
    r"[^>]*>.*?</\1\s*>",
    re.I | re.S,
)
_BLOCK_END = re.compile(
    r"</(p|div|li|h[1-6]|tr|blockquote|section|article|ul|ol)\s*>|<br\s*/?>", re.I
)
_TAG = re.compile(r"<[^>]+>")
_BLANKS = re.compile(r"[ \t\r\f\v]+")
_NEWLINES = re.compile(r"\n{3,}")


def _builtin_extract(html: str) -> str:
    """Dependency-free fallback for `trafilatura.extract`.

    Good enough for news markup: drop the obvious non-content elements, turn
    block ends into newlines, strip the rest, unescape entities.
    """
    body = re.search(r"<body\b[^>]*>(.*?)</body\s*>", html, re.I | re.S)
    text = body.group(1) if body else html
    text = _DROP_TAGS.sub(" ", text)
    text = _BLOCK_END.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = _html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = _BLANKS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _NEWLINES.sub("\n\n", text).strip()


def extract_text(html: str) -> str:
    """Article text with the furniture removed.

    Uses trafilatura when installed (02-architecture §9) and falls back to the
    built-in extractor otherwise, so the offline test suite has no hard
    dependency on a native package.

    ponytail: the two extractors do not produce byte-identical text, and the
    llm cache is keyed on that text — installing trafilatura after the
    fixtures were recorded invalidates them. `tests/fixtures/rekey_llm_cache.py`
    rebuilds the keys without calling a provider.
    """
    if not html:
        return ""
    if "<" not in html:  # already plain text
        return unicodedata.normalize("NFKC", html).strip()
    try:
        import trafilatura  # type: ignore

        got = trafilatura.extract(html, include_comments=False, include_tables=False)
        if got:
            return unicodedata.normalize("NFKC", got).strip()
    except Exception:
        pass
    return _builtin_extract(html)


_JSONLD = re.compile(
    r'<script[^>]+type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script\s*>',
    re.I | re.S,
)


def parse_jsonld(html: str) -> dict:
    """`<script type="application/ld+json">` is free, structured and deterministic.

    Used to pre-fill headline/date/author and to cross-check the model
    (05-pipeline §3.1). Malformed JSON is ignored, never fatal.
    """
    out: dict[str, Any] = {}
    for raw in _JSONLD.findall(html or ""):
        try:
            data = json.loads(raw.strip())
        except Exception:
            continue
        for node in _iter_nodes(data):
            for key in ("headline", "datePublished", "dateModified", "url", "description"):
                if key not in out and isinstance(node.get(key), str):
                    out[key] = node[key]
            author = node.get("author")
            if "author" not in out and author:
                if isinstance(author, dict) and isinstance(author.get("name"), str):
                    out["author"] = author["name"]
                elif isinstance(author, str):
                    out["author"] = author
            if node.get("@type") in {"Organization", "Corporation"} and "organization" not in out:
                if isinstance(node.get("name"), str):
                    out["organization"] = node["name"]
                if isinstance(node.get("url"), str):
                    out.setdefault("organization_url", node["url"])
    return out


def _iter_nodes(data: Any) -> Iterator[dict]:
    if isinstance(data, dict):
        yield data
        for value in data.values():
            yield from _iter_nodes(value)
    elif isinstance(data, list):
        for item in data:
            yield from _iter_nodes(item)


_META = re.compile(
    r'<meta[^>]+(?:property|name)\s*=\s*["\']([^"\']+)["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
    re.I,
)
_META_REV = re.compile(
    r'<meta[^>]+content\s*=\s*["\']([^"\']*)["\'][^>]*(?:property|name)\s*=\s*["\']([^"\']+)["\']',
    re.I,
)


def parse_meta(html: str) -> dict[str, str]:
    """og:/twitter:/name meta tags, lower-cased keys."""
    out: dict[str, str] = {}
    for key, value in _META.findall(html or ""):
        out.setdefault(key.strip().lower(), _html.unescape(value).strip())
    for value, key in _META_REV.findall(html or ""):
        out.setdefault(key.strip().lower(), _html.unescape(value).strip())
    return out


_TITLE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.I | re.S)


def parse_title(html: str) -> str:
    meta = parse_meta(html)
    for key in ("og:title", "twitter:title"):
        if meta.get(key):
            return meta[key]
    m = _TITLE.search(html or "")
    if m:
        return _html.unescape(_TAG.sub("", m.group(1))).strip()
    m = re.search(r"<h1\b[^>]*>(.*?)</h1\s*>", html or "", re.I | re.S)
    if m:
        return _html.unescape(_TAG.sub("", m.group(1))).strip()
    return ""


# --- the cascade ------------------------------------------------------------


def prefilter(url: str, title: str, html: str) -> PrefilterResult:
    """Cheapest checks first. Returns the extracted text so the caller does
    not pay for boilerplate removal twice."""
    url = url or ""
    title = title or ""

    if INDEX_URL.search(url):
        return PrefilterResult(False, "index_page")

    if ROUNDUP.search(title):
        return PrefilterResult(False, "roundup_title")

    text = extract_text(html)
    if not text or len(text) < MIN_TEXT_CHARS:
        return PrefilterResult(False, "too_short", text=text)

    if not SIGNAL.search(text[:SIGNAL_WINDOW]):
        return PrefilterResult(False, "no_signal_keyword", text=text)

    orgs = set(ORG.findall(text))
    if len(orgs) > MAX_ORGS:
        return PrefilterResult(False, "too_many_orgs", text=text, orgs=tuple(sorted(orgs)))

    return PrefilterResult(
        True, "ok", text=text, jsonld=parse_jsonld(html), orgs=tuple(sorted(orgs))
    )


def should_extract(url: str, title: str, html: str) -> tuple[bool, str]:
    """The PRD signature. Kept so the cascade reads the same in code and docs."""
    result = prefilter(url, title, html)
    return result.ok, result.reason


# A prefilter verdict only maps onto the schema's rejection vocabulary in the
# two round-up cases. `too_short` is not `paywalled` and must not pretend to be
# — the raw reason is carried on `Extraction.prefilter_reason` instead.
REASON_TO_REJECTION: dict[str, str | None] = {
    "index_page": None,
    "roundup_title": "roundup",
    "too_short": None,
    "no_signal_keyword": None,
    "too_many_orgs": "roundup",
    "ok": None,
}
