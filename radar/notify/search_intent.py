"""Classify Telegram text as a dashboard search / today ping / ignore.

Stdlib only so the Hermes gateway plugin can import this without pulling in
the rest of Founder Radar. The plugin rewrites matching messages to `/run`
or `/today` *before* the LLM sees them; that is the guarantee that a search
lands on the Today page instead of a company dump in chat.
"""

from __future__ import annotations

import re
import unicodedata

FUND_KEYS = ("northstar", "dsw", "outward", "anticus")

# Hermes builtin: acknowledge Telegram's /start handshake with no reply.
# Bare "Start" (Aryan's actual search cue) is a different token.
_HERMES_START = re.compile(r"^/start(?:@\S+)?(?:\s|$)", re.I)

_FUND_RE = re.compile(
    r"\b(northstar|dsw|outward|anticus)\b",
    re.I,
)

_TODAY_EXACT = {
    "/today",
    "today",
    "what's new",
    "whats new",
    "what is new",
    "today's list",
    "todays list",
    "open today",
    "show today",
    "today page",
    "dashboard",
    "the dashboard",
}

_SEARCH_EXACT = {
    "start",
    "search",
    "search now",
    "search please",
    "please search",
    "run",
    "run now",
    "run it",
    "run a scan",
    "run scan",
    "run a search",
    "scan",
    "scan now",
    "do a scan",
    "do a search",
    "kick off a search",
    "kick off a scan",
    "kickoff",
    "go",
}

_SEARCH_PREFIX = re.compile(
    r"^(?:/"
    r"(?:run|search)"
    r"|search|run|scan|start"
    r")(?:\s|$)",
    re.I,
)

_SEARCH_PHRASES = (
    "search now",
    "run a scan",
    "run a search",
    "do a search",
    "do a scan",
    "scan now",
    "kick off a search",
    "kick off a scan",
)

_NOT_SEARCH = (
    "not for me",
    "worth contacting",
    "unsure",
    "reject",
    "keep this",
    "/why",
    "/fund",
    "/decide",
    "/week",
    "/sheet",
    "/help",
    "/status",
)


def compact(text: str) -> str:
    """Lowercased, NFC, punctuation-stripped single line."""
    raw = unicodedata.normalize("NFC", str(text or ""))
    raw = raw.replace("\u2019", "'").replace("\u2018", "'")
    raw = raw.strip()
    raw = re.sub(r"\s+", " ", raw)
    raw = raw.lower()
    raw = re.sub(r"[.!?]+$", "", raw).strip()
    return raw


def fund_from(text: str) -> str | None:
    match = _FUND_RE.search(str(text or ""))
    return match.group(1).lower() if match else None


def classify(text: str) -> str | None:
    """Return ``search``, ``today``, or ``None`` (leave the message to Hermes)."""
    raw = str(text or "").strip()
    if not raw:
        return None
    if _HERMES_START.match(raw):
        return None

    folded = compact(raw)
    if not folded:
        return None
    if any(token in folded for token in _NOT_SEARCH):
        return None

    first = folded.split()[0].split("@", 1)[0]
    if first == "/today":
        return "today"
    if first in {"/run", "/search"}:
        return "search"

    if folded in _TODAY_EXACT or folded.startswith("/today"):
        return "today"
    if folded in _SEARCH_EXACT:
        return "search"
    if _SEARCH_PREFIX.match(folded) and len(folded) <= 80:
        return "search"
    if len(folded) <= 80 and any(phrase in folded for phrase in _SEARCH_PHRASES):
        return "search"
    return None


def slash_rewrite(text: str) -> str | None:
    """Rewrite natural-language search/today into a slash command.

    Returns None when the message should pass through unchanged — including
    when it is already `/run`, `/search`, or `/today` (the plugin command
    handler owns those).
    """
    kind = classify(text)
    if kind is None:
        return None
    folded = compact(text)
    first = folded.split()[0].split("@", 1)[0] if folded else ""
    if first in {"/run", "/search", "/today"}:
        return None
    if kind == "today":
        return "/today"
    fund = fund_from(text)
    return f"/run {fund}" if fund else "/run"


__all__ = ["FUND_KEYS", "classify", "compact", "fund_from", "slash_rewrite"]
