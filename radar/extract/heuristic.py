"""The no-AI fallback (05-pipeline §3.4).

When the provider is unavailable after retries — or `llm_enabled` is false, or
`--no-llm` was passed — this produces a *complete* record from regexes and page
metadata alone:

* Company name from `og:title` / JSON-LD / the first `X Ltd|Limited` match
* Amount from a currency regex
* Everything else `None`

Records are marked `extraction_method = "heuristic"`, `confidence = 0.3`,
`needs_review = True`, and land on the `Needs Review` tab. **The run completes
and exits 0.** The pipeline never stops because a provider did.
"""

from __future__ import annotations

import re
from typing import Any

from radar.extract import prefilter as pf
from radar.extract.schema import Extraction

HEURISTIC_CONFIDENCE = 0.3

# --- money ------------------------------------------------------------------

_MULTIPLIERS = {
    "k": 1_000, "thousand": 1_000,
    "m": 1_000_000, "mn": 1_000_000, "million": 1_000_000,
    "bn": 1_000_000_000, "b": 1_000_000_000, "billion": 1_000_000_000,
}
_SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR"}

MONEY = re.compile(
    r"(?P<sym>[£$€])\s?(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<mult>bn|billion|mn|m|million|k|thousand)?\b",
    re.I,
)

GRANT_CONTEXT = re.compile(r"\b(grant|innovate uk|ukri|award(ed)?|non-?dilutive)\b", re.I)

# --- names ------------------------------------------------------------------

_FUNDING_VERB = (
    r"raises?|raised|secures?|secured|closes?|closed|lands?|nets?|banks?|wins?|won"
    r"|receives?|scoops?|announces?|launches?|bags?|attracts?|completes?|picks up"
)

# "Newcastle's Palisade Health raises ..." / "Leeds-based Loamweave secures ..."
_PRELUDE = re.compile(
    r"^(?:(?:[A-Z][\w&'’.\-]*(?:\s+[A-Z][\w&'’.\-]*)*['’]s)\s+"
    r"|(?:[A-Za-z][\w\s\-]{0,24}-based)\s+"
    r"|(?:Exclusive|Breaking|Update)\s*[:\-–]\s*)",
)

NAME_IN_TITLE = re.compile(
    r"^(?P<name>[A-Z][\w&'’.\-]*(?:\s+[A-Z0-9][\w&'’.\-]*){0,3})\s+(?:" + _FUNDING_VERB + r")\b"
)

LEGAL_ENTITY = re.compile(
    r"\b(?P<name>[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+){0,2})\s+(?:Ltd|Limited)\b"
)

FOUNDED_YEAR = re.compile(
    r"\b(?:founded|established|incorporated|spun out|set up)\s+(?:in\s+)?(?P<year>19\d\d|20[0-3]\d)\b",
    re.I,
)

SPINOUT = re.compile(r"\bspin-?out|spin-?off|spun out\b", re.I)
UNIVERSITY = re.compile(
    r"\b(University of [A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+)?|[A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+)?\s+University)\b"
)

_SENTENCE = re.compile(r"[^.!?\n]*[.!?]?")


def _sentence_containing(text: str, needle: str) -> str | None:
    """A verbatim sentence from `text` containing `needle`, or None.

    The quote must come out of the *text*, never out of the title, or the
    grounding check would drop the very field this fallback just found.
    """
    if not needle:
        return None
    idx = text.find(needle)
    if idx < 0:
        return None
    start = max(text.rfind(".", 0, idx), text.rfind("\n", 0, idx)) + 1
    end = len(text)
    for stop in (".", "\n"):
        pos = text.find(stop, idx + len(needle))
        if pos != -1:
            end = min(end, pos + (1 if stop == "." else 0))
    quote = text[start:end].strip()
    return quote or None


def _parse_money(match: re.Match) -> tuple[float, str]:
    raw = match.group("num").replace(",", "")
    value = float(raw)
    mult = (match.group("mult") or "").lower()
    if mult:
        value *= _MULTIPLIERS[mult]
    elif value < 1000 and "." in raw:
        # "£1.2" with no suffix in a funding headline is almost certainly £1.2m,
        # but guessing is worse than being honest — leave the number as printed.
        pass
    return value, _SYMBOLS[match.group("sym")]


def find_amount(title: str, text: str) -> tuple[float, str, str | None] | None:
    """(value, ISO currency, verbatim quote) for the first amount stated."""
    for haystack, quotable in ((title, False), (text, True)):
        match = MONEY.search(haystack or "")
        if match:
            value, currency = _parse_money(match)
            quote = _sentence_containing(text, match.group(0)) if quotable else None
            if quote is None:
                quote = _sentence_containing(text, match.group(0))
            return value, currency, quote
    return None


def find_company_name(title: str, text: str, html: str, jsonld: dict | None = None) -> str | None:
    """og:title / JSON-LD `about` / the first `X Ltd|Limited` match."""
    meta = pf.parse_meta(html) if html else {}
    candidates: list[str] = []

    og_title = meta.get("og:title") or title
    stripped = _PRELUDE.sub("", (og_title or "").strip())
    match = NAME_IN_TITLE.match(stripped)
    if match:
        candidates.append(match.group("name"))

    if jsonld:
        for key in ("organization", "about"):
            value = jsonld.get(key)
            if isinstance(value, str):
                candidates.append(value)

    match = LEGAL_ENTITY.search(text or "")
    if match:
        candidates.append(match.group("name"))

    for candidate in candidates:
        cleaned = candidate.strip(" ,.-–—")
        if cleaned and len(cleaned) > 1:
            return cleaned
    return None


def heuristic_extract(
    *,
    title: str,
    text: str,
    html: str = "",
    url: str | None = None,
    jsonld: dict | None = None,
) -> Extraction:
    """A complete record, deterministically, with no AI and no network."""
    title = title or ""
    text = text or ""
    jsonld = jsonld or pf.parse_jsonld(html) if html else (jsonld or {})

    data: dict[str, Any] = {
        "is_about_single_company": True,
        "extraction_confidence": HEURISTIC_CONFIDENCE,
        "extraction_method": "heuristic",
        "needs_review": True,
    }

    # The gate, without a model: the same free signals the prefilter uses.
    if pf.ROUNDUP.search(title) or len(set(pf.ORG.findall(text))) > pf.MAX_ORGS:
        data["rejection_reason"] = "roundup"
        return Extraction.model_validate(data)

    name = find_company_name(title, text, html, jsonld)
    if name:
        data["company_name"] = name
        data["evidence_quote_company"] = _sentence_containing(text, name)
    else:
        data["rejection_reason"] = "no_company_identified"
        return Extraction.model_validate(data)

    found = find_amount(title, text)
    if found:
        value, currency, quote = found
        is_grant = bool(GRANT_CONTEXT.search(title)) or bool(GRANT_CONTEXT.search(text[:600]))
        if currency == "GBP" and is_grant:
            data["grant_amount_gbp"] = value
        elif currency == "GBP":
            data["amount_raised_gbp"] = value
        else:
            # Never silently wrong: report the printed number and its currency,
            # leave the GBP field null for a human to resolve.
            data["amount_original"] = value
            data["amount_currency"] = currency
        data["evidence_quote_amount"] = quote

    match = FOUNDED_YEAR.search(text)
    if match:
        year = int(match.group("year"))
        if 1990 <= year <= 2030:
            data["founded_year"] = year

    if SPINOUT.search(text):
        uni = UNIVERSITY.search(text)
        data["is_university_spinout"] = True
        data["university_name"] = uni.group(1) if uni else None
        data["evidence_quote_spinout"] = _sentence_containing(
            text, uni.group(1) if uni else SPINOUT.search(text).group(0)
        )

    return Extraction.model_validate(data)
