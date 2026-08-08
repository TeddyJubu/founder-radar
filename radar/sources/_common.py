"""Parsing helpers shared by the adapters. Private to `radar.sources`.

Ten adapters, four access patterns (WordPress JSON, RSS, HTML, plain API). The
per-site knowledge lives in the adapter file; everything mechanical — date
coercion, HTML stripping, snapshot-diff bookkeeping, the structural checks that
turn a silent 200 into a loud `LayoutChanged` — lives here so it is written
once and tested once.

Nothing in this module knows about the pipeline. Adapters return `RawItem` and
nothing else (02-architecture §4).
"""

from __future__ import annotations

import html as html_module
import json
import re
import unicodedata
from datetime import date, datetime
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import urljoin, urlsplit

from radar.fetch.layout import LayoutChanged, guard_nonempty, selector_fingerprint

__all__ = [
    "LayoutChanged",
    "absolute_url",
    "clean_text",
    "guard_nonempty",
    "html_doc",
    "norm_key",
    "norm_name",
    "parse_date",
    "rss_entries",
    "selector_fingerprint",
    "slug_of",
    "snapshot_diff",
    "strip_html",
    "text_of",
    "wp_posts",
]

_WS = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"(?is)<(script|style|noscript)\b.*?</\1>")

# 05-pipeline §4.1. Deliberately NOT stripped: group, holdings, ventures,
# partners, labs, technologies — those are part of the trading name.
LEGAL_SUFFIXES = {
    "ltd", "limited", "plc", "llp", "lp", "llc", "cic", "cio", "inc",
    "incorporated", "corp", "corporation", "co", "company", "gmbh", "ag", "sa",
    "sas", "sarl", "bv", "nv", "ab", "oy", "as", "aps", "srl", "spa", "pty",
}

_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\ufeff]")


# ------------------------------------------------------------------- strings


def strip_html(value: str | None) -> str:
    """Tags out, entities decoded, whitespace collapsed.

    `content.rendered` from WordPress is HTML; the pipeline's reader wants text.
    Script and style bodies are removed first — without that, an inline
    analytics blob becomes part of `body_text` and every keyword pre-filter in
    stage ③ starts matching on Google Tag Manager.
    """
    if not value:
        return ""
    text = _SCRIPT.sub(" ", value)
    text = _TAG.sub(" ", text)
    text = html_module.unescape(text)
    return _WS.sub(" ", text).strip()


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return _WS.sub(" ", html_module.unescape(value)).strip()


def norm_name(value: str | None) -> str:
    """05-pipeline §4.1 normalisation, verbatim.

    # ponytail: duplicated from the spec because `radar.resolve` is Phase 4 and
    # does not exist yet. When it lands, this should become a re-export — the
    # VC denylist match must use the *same* key as entity resolution or the two
    # will disagree about what "already seen" means.
    """
    if not value:
        return ""
    s = unicodedata.normalize("NFKD", value)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    s = _ZERO_WIDTH.sub("", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = _WS.sub(" ", s).strip()
    if s.startswith("the "):
        s = s[4:]
    tokens = s.split()
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def norm_key(value: str | None) -> str:
    return norm_name(value).replace(" ", "")


def slug_of(url: str) -> str:
    """Last non-empty path segment — the stable id for a portfolio card."""
    path = urlsplit(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else ""


def absolute_url(base: str, href: str | None) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("mailto:", "tel:", "javascript:", "#")):
        return None
    return urljoin(base, href)


# --------------------------------------------------------------------- dates

_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"], start=1)
}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})

_DMY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?\s+(\d{4})\b")
_MDY = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def parse_date(value: Any) -> date | None:
    """Best-effort date from whatever a site actually publishes.

    Returns `None` rather than guessing. `published_at = None` is a legitimate
    value in `RawItem` — an invented date is not, because the freshness gates
    are the entire product.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        try:
            return date(int(value[0]), int(value[1]), int(value[2]))
        except (TypeError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None

    # ISO 8601 datetimes — "2026-08-04T06:00:00Z" is the exact format WordPress
    # returns for `date_gmt` and GOV.UK for `public_timestamp`. `fromisoformat`
    # accepts the `T` separator, timezone offsets and a trailing `Z` (3.11+),
    # which the regex below cannot do: its trailing `\b` fails when the next
    # character is `T` or a digit.
    try:
        parsed_dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed_dt.date()
    except ValueError:
        pass

    match = _ISO.search(text)
    if match:
        try:
            return date(int(match[1]), int(match[2]), int(match[3]))
        except ValueError:
            return None

    match = _DMY.search(text)
    if match:
        month = _MONTHS.get(match[2].lower()) or _MONTHS.get(match[2][:3].lower())
        if month:
            try:
                return date(int(match[3]), month, int(match[1]))
            except ValueError:
                return None

    match = _MDY.search(text)
    if match:
        month = _MONTHS.get(match[1].lower()) or _MONTHS.get(match[1][:3].lower())
        if month:
            try:
                return date(int(match[3]), month, int(match[2]))
            except ValueError:
                return None

    # UK order. A UK-only crawler reading UK sites: 03/04/2026 is 3 April.
    match = _SLASH.search(text)
    if match:
        try:
            return date(int(match[3]), int(match[2]), int(match[1]))
        except ValueError:
            return None
    return None


# ----------------------------------------------------------- WordPress JSON


WP_REQUIRED = ("id", "link", "title")


def wp_posts(payload: str | bytes, source_key: str) -> list[dict]:
    """Validate and normalise `/wp-json/wp/v2/posts`.

    Every failure mode here is a `LayoutChanged`, including the empty list. A
    WordPress posts endpoint returns the newest N posts regardless of date, so
    "200 OK, `[]`" never means "quiet week" — it means the route moved, the
    site migrated off WordPress, or a security plugin started filtering us.
    """
    body = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise LayoutChanged(source_key, f"response is not JSON: {exc}") from exc

    if isinstance(data, dict) and "code" in data:      # WP_Error shape
        raise LayoutChanged(source_key, f"wp error {data.get('code')!r}")
    if not isinstance(data, list):
        raise LayoutChanged(source_key, f"expected a JSON array, got {type(data).__name__}")

    guard_nonempty(source_key, data, detail="wp-json returned no posts", document=body)

    out: list[dict] = []
    for post in data:
        if not isinstance(post, dict):
            raise LayoutChanged(source_key, "post is not an object")
        missing = [k for k in WP_REQUIRED if k not in post]
        if missing:
            raise LayoutChanged(source_key, f"post is missing {missing}")
        title = post.get("title")
        rendered = title.get("rendered") if isinstance(title, dict) else title
        content = post.get("content")
        excerpt = post.get("excerpt")
        out.append({
            "id": str(post["id"]),
            "link": post["link"],
            "date": parse_date(post.get("date_gmt") or post.get("date")),
            "title": clean_text(strip_html(rendered)),
            "body": strip_html(content.get("rendered") if isinstance(content, dict) else content),
            "excerpt": strip_html(excerpt.get("rendered") if isinstance(excerpt, dict) else excerpt),
            "raw": post,
        })
    return out


def wp_fingerprint(posts: Sequence[dict]) -> str:
    """Structure hash for a JSON source: the key set of the first few posts."""
    paths: set[str] = set()
    for post in posts[:5]:
        raw = post.get("raw", post)
        if isinstance(raw, dict):
            paths.update(raw.keys())
    return selector_fingerprint(paths)


# ----------------------------------------------------------------------- RSS


def rss_entries(payload: str | bytes, source_key: str) -> list[dict]:
    """Parse a feed, preferring `content:encoded` over `description`.

    BusinessCloud and Tech.eu put the full article in `content:encoded`, which
    removes one fetch per article — that is the difference between 30 requests
    a day at this source and 1 (04-sources §4.2).
    """
    body = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
    try:
        import feedparser
    except ImportError as exc:                          # pragma: no cover
        raise LayoutChanged(source_key, f"feedparser unavailable: {exc}") from exc

    parsed = feedparser.parse(body)
    if getattr(parsed, "bozo", 0) and not parsed.entries:
        raise LayoutChanged(source_key, f"feed did not parse: {parsed.get('bozo_exception')}")

    guard_nonempty(source_key, parsed.entries, detail="feed has no entries", document=body)

    out: list[dict] = []
    for entry in parsed.entries:
        link = entry.get("link")
        title = clean_text(entry.get("title"))
        if not link or not title:
            raise LayoutChanged(source_key, "feed entry has no link or title")

        full = ""
        for block in entry.get("content", []) or []:
            value = block.get("value") if isinstance(block, dict) else None
            if value and len(value) > len(full):
                full = value
        summary = entry.get("summary") or entry.get("description") or ""
        body_html = full or summary

        out.append({
            "id": entry.get("id") or entry.get("guid") or link,
            "link": link,
            "title": title,
            "date": parse_date(entry.get("published_parsed") or entry.get("published")
                               or entry.get("updated")),
            "body": strip_html(body_html),
            "has_full_text": bool(full),
            "tags": [t.get("term", "") for t in entry.get("tags", []) or []],
        })
    return out


# ---------------------------------------------------------------------- HTML


def html_doc(payload: str | bytes, source_key: str):
    """A selectolax tree, or a loud failure. Never a silent empty document."""
    body = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
    try:
        from selectolax.parser import HTMLParser
    except ImportError as exc:                          # pragma: no cover
        raise LayoutChanged(source_key, f"selectolax unavailable: {exc}") from exc
    if not body or not body.strip():
        raise LayoutChanged(source_key, "empty response body")
    return HTMLParser(body)


def text_of(node, selector: str | None = None, *, default: str = "") -> str:
    """Text of `node`, or of its first match for `selector`."""
    if node is None:
        return default
    target = node if selector is None else node.css_first(selector)
    if target is None:
        return default
    return clean_text(target.text(separator=" ", strip=True))


def attr_of(node, selector: str | None, name: str) -> str | None:
    if node is None:
        return None
    target = node if selector is None else node.css_first(selector)
    if target is None:
        return None
    value = target.attributes.get(name)
    return value.strip() if value else None


def select_any(doc, selectors: Sequence[str]) -> tuple[str, list]:
    """First selector in `selectors` that matches anything.

    A tuple of (selector, nodes) rather than just nodes, because the selector
    that fired is half of the structure fingerprint — the day a site swaps
    `.portfolio-item` for `.w-dyn-item` we want that in the alert text, not a
    bare "0 items".
    """
    for selector in selectors:
        nodes = doc.css(selector)
        if nodes:
            return selector, list(nodes)
    return "", []


def node_fingerprint(nodes: Iterable[Any], *, limit: int = 5) -> str:
    """Hash the tag/class shape of the first few matched nodes."""
    paths: set[str] = set()
    for node in list(nodes)[:limit]:
        classes = (node.attributes.get("class") or "").split()
        paths.add(node.tag + "." + ".".join(sorted(classes)))
        for child in node.iter() if hasattr(node, "iter") else []:
            child_classes = (child.attributes.get("class") or "").split()
            paths.add(f"{node.tag}>{child.tag}." + ".".join(sorted(child_classes)))
    return selector_fingerprint(paths)


def meta_noindex(doc) -> bool:
    """`<meta name="robots" content="noindex">` — honoured, per 04-sources §5."""
    for node in doc.css('meta[name="robots"], meta[name="ROBOTS"]'):
        content = (node.attributes.get("content") or "").lower()
        if "noindex" in content:
            return True
    return False


def header_noindex(headers: Any) -> bool:
    """`X-Robots-Tag: noindex` — the header half of the same rule."""
    if not headers:
        return False
    value = ""
    for key in ("x-robots-tag", "X-Robots-Tag"):
        try:
            value = headers.get(key) or value
        except AttributeError:
            return False
    return "noindex" in str(value).lower()


# ------------------------------------------------------------- snapshot-diff


def _snapshot_key(source_key: str) -> str:
    return f"snapshot:{source_key}"


def load_snapshot(db: Any, source_key: str) -> set[str] | None:
    """Previously seen external ids, or None when this source has never run."""
    if db is None:
        return None
    raw = db.get_meta(_snapshot_key(source_key))
    if raw is None:
        return None
    try:
        return set(json.loads(raw))
    except (TypeError, ValueError):
        return None


def save_snapshot(db: Any, source_key: str, ids: Iterable[str]) -> None:
    if db is not None:
        db.set_meta(_snapshot_key(source_key), json.dumps(sorted(set(ids))))


def snapshot_diff(db: Any, source_key: str, ids: Sequence[str]) -> tuple[set[str], bool]:
    """New ids since the last run, plus whether this is the bootstrap run.

    An undated portfolio page has no `published_at`, so "what changed" is the
    only freshness signal it can offer (04-sources §4.3). On the first ever run
    there is no baseline: everything is returned and flagged `bootstrap`, so
    downstream can decline to treat a 2018 cohort as this week's news.
    """
    previous = load_snapshot(db, source_key)
    save_snapshot(db, source_key, ids)
    if previous is None:
        return set(ids), True
    return {i for i in ids if i not in previous}, False


# ------------------------------------------------------------------ plumbing


def unique_by_id(items: Iterable[Any]) -> list[Any]:
    """De-dup on `external_id`, keeping first sight. Paginated feeds overlap."""
    seen: set[str] = set()
    out = []
    for item in items:
        if item.external_id in seen:
            continue
        seen.add(item.external_id)
        out.append(item)
    return out


def after(items: Iterable[Any], since: date | None) -> Iterator[Any]:
    """Date filter that keeps undated items.

    Dropping `published_at is None` here would silently discard every snapshot
    -diff source, which is exactly the class of source we are trying to add.
    """
    for item in items:
        if since is None or item.published_at is None or item.published_at >= since:
            yield item
