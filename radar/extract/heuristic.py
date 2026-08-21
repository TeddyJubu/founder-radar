"""The no-AI fallback (05-pipeline §3.4).

When the provider is unavailable after retries — or `llm_enabled` is false, or
`--no-llm` was passed — this produces a *complete* record from regexes and page
metadata alone:

* Company name from `og:title` / JSON-LD / the first `X Ltd|Limited` match
* Amount from a currency regex
* Explicit headquarters country and a verbatim business description when the
  source says them plainly

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

_TARGET_RELATION = (
    r"(?:investment\s+in|invests?\s+in|backs?|funds?|finances?|"
    r"acquires?|acquired|buys?|bought|takes\s+over)"
)
TARGET_IN_TITLE = re.compile(
    r"(?i:" + _TARGET_RELATION + r")\s+"
    r"(?:(?:[A-Za-z][\w&'’\.\-]*\s+){0,2}"
    r"(?:startup|company|venture|scaleup|spinout|business|firm)\s+)?"
    r"(?P<name>[A-Z][\w&'’\.\-]*(?:\s+[A-Z0-9][\w&'’\.\-]*){0,3})",
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

# Late-stage / public-company language. If we leave stage unknown these pass
# `max_stage` and occupy Today — the client's "already VC-backed / about to
# IPO" complaint.
IPO_LANGUAGE = re.compile(
    r"\b(?:initial public offering|IPO|fil(?:e|es|ed) for (?:an )?IPO|"
    r"going public|"
    r"listed on (?:the )?(?:AIM|LSE|NASDAQ|NYSE|London Stock Exchange))\b",
    re.I,
)
# Headlines like "Spine files for IPO" have no funding verb, so NAME_IN_TITLE
# misses them. Capture the subject anyway so the reject record is named.
IPO_SUBJECT = re.compile(
    r"(?P<name>[A-Z][\w&'’.\-]*(?:\s+[A-Z0-9][\w&'’.\-]*){0,3})\s+"
    r"(?:files?|filed)\s+for\s+(?:an\s+)?IPO\b"
)
LATE_ROUND = re.compile(
    r"\b(series\s+(?!a\b)[a-z]\b|growth round|late[- ]stage(?: round)?|pre-?IPO)\b",
    re.I,
)
SERIES_A = re.compile(r"\bseries\s+a\b", re.I)
PRE_SEED = re.compile(r"\bpre-?seed\b", re.I)
SEED_ROUND = re.compile(r"\b(seed round|seed funding|seed investment)\b", re.I)

# Location is deliberately conservative. A place is treated as headquarters
# evidence only when the prose says "based"/"headquartered", or calls it a
# startup/company in that place. This avoids turning a customer, market, or
# investor location into the company's country.
_HQ_COUNTRY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "AE",
        re.compile(
            r"\b(?:Dubai|Abu Dhabi|UAE|United Arab Emirates)[-\s]"
            r"(?:based|headquartered)\b"
            r"|\b(?:based|headquartered)\s+in\s+(?:Dubai|Abu Dhabi|"
            r"(?:the\s+)?UAE|United Arab Emirates)\b"
            r"|\b(?:Dubai|Abu Dhabi)\s+(?:startup|company|business)\b",
            re.I,
        ),
    ),
    (
        "US",
        re.compile(
            r"\b(?:US|U\.S\.|USA|United States|America)[-\s]"
            r"(?:based|headquartered)\b"
            r"|\b(?:based|headquartered)\s+in\s+(?:the\s+)?"
            r"(?:US|U\.S\.|USA|United States|America)\b"
            r"|\b(?:New York|San Francisco|Boston|Austin|Los Angeles|"
            r"Chicago|Seattle|Miami|Denver|Palo Alto|Silicon Valley)[-\s]"
            r"(?:based|headquartered)\b",
            re.I,
        ),
    ),
    (
        "GB",
        re.compile(
            r"\b(?:UK|U\.K\.|United Kingdom|England|Scotland|Wales|"
            r"Northern Ireland)[-\s](?:based|headquartered)\b"
            r"|\b(?:based|headquartered)\s+in\s+(?:the\s+)?"
            r"(?:UK|U\.K\.|United Kingdom|England|Scotland|Wales|"
            r"Northern Ireland)\b"
            r"|\b(?:London|Manchester|Leeds|Bristol|Newcastle|Edinburgh|"
            r"Glasgow|Cambridge|Oxford)[-\s](?:based|headquartered)\b",
            re.I,
        ),
    ),
)

_DESCRIPTION_VERB = re.compile(
    r"\b(?:builds?|develops?|creates?|provides?|offers?|makes?|helps?|"
    r"designs?|produces?|operates?|speciali[sz]es?|manufactures?|"
    r"delivers?|connects?|enables?)\b",
    re.I,
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
    """Prefer the operating startup in a relation headline.

    An investor/acquirer often leads a headline ("X invests in Y"), while the
    product only wants Y. Relation-aware title parsing therefore comes before
    the generic subject-at-the-start and legal-entity fallbacks.
    """
    meta = pf.parse_meta(html) if html else {}
    candidates: list[str] = []

    og_title = meta.get("og:title") or title
    stripped = _PRELUDE.sub("", (og_title or "").strip())
    target = TARGET_IN_TITLE.search(stripped)
    if target:
        candidates.append(target.group("name"))
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

    ipo_subject = IPO_SUBJECT.search(stripped or "") or IPO_SUBJECT.search(text or "")
    if ipo_subject:
        candidates.append(ipo_subject.group("name"))

    for candidate in candidates:
        cleaned = candidate.strip(" ,.-–—")
        if cleaned and len(cleaned) > 1:
            return cleaned
    return None


def find_hq_country(title: str, text: str) -> str | None:
    """Return an ISO-3166-1 alpha-2 code only for explicit HQ wording."""
    haystack = "\n".join(part for part in (title or "", text or "") if part)
    matches = [
        (match.start(), country)
        for country, pattern in _HQ_COUNTRY_PATTERNS
        if (match := pattern.search(haystack)) is not None
    ]
    return min(matches)[1] if matches else None


def find_one_line_description(text: str, company_name: str) -> str | None:
    """Return a short, verbatim product sentence rather than inventing one."""
    if not text or not company_name:
        return None
    for match in _SENTENCE.finditer(text):
        sentence = re.sub(r"\s+", " ", match.group(0)).strip()
        if not sentence or len(sentence) > 200:
            continue
        if company_name.casefold() not in sentence.casefold():
            continue
        if _DESCRIPTION_VERB.search(sentence):
            return sentence
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

    haystack = f"{title}\n{text}"
    # IPO / listing copy is not a lead even when the headline has no funding
    # verb and we cannot parse a company name.
    ipo = IPO_LANGUAGE.search(haystack)
    if ipo:
        name = find_company_name(title, text, html, jsonld)
        if name:
            data["company_name"] = name
            data["evidence_quote_company"] = _sentence_containing(text, name)
        data["rejection_reason"] = "already_large_company"
        data["stage"] = "growth"
        data["evidence_quote_stage"] = (
            _sentence_containing(text, ipo.group(0)) or ipo.group(0)
        )
        return Extraction.model_validate(data)

    name = find_company_name(title, text, html, jsonld)
    if name:
        data["company_name"] = name
        data["evidence_quote_company"] = _sentence_containing(text, name)
    else:
        data["rejection_reason"] = "no_company_identified"
        return Extraction.model_validate(data)

    hq_country = find_hq_country(title, text)
    if hq_country:
        data["hq_country_iso2"] = hq_country

    description = find_one_line_description(text, name)
    if description:
        data["one_line_description"] = description

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

    stage, stage_quote, reject_large = _detect_stage(haystack, text)
    if reject_large:
        data["rejection_reason"] = "already_large_company"
    if stage:
        data["stage"] = stage
        if stage_quote:
            data["evidence_quote_stage"] = stage_quote

    return Extraction.model_validate(data)


def _detect_stage(haystack: str, text: str) -> tuple[str | None, str | None, bool]:
    """Return `(stage, quote, already_large)`.

    IPO / listing language is a reject, not a lead. Series B+ still produces
    a record so the freshness `max_stage` gate can fire with a reason.
    """
    ipo = IPO_LANGUAGE.search(haystack)
    if ipo:
        quote = _sentence_containing(text, ipo.group(0)) or ipo.group(0)
        return "growth", quote, True
    late = LATE_ROUND.search(haystack)
    if late:
        quote = _sentence_containing(text, late.group(0)) or late.group(0)
        return "series_b_plus", quote, False
    if SERIES_A.search(haystack):
        match = SERIES_A.search(haystack)
        quote = _sentence_containing(text, match.group(0)) if match else None
        return "series_a", quote, False
    if PRE_SEED.search(haystack):
        match = PRE_SEED.search(haystack)
        quote = _sentence_containing(text, match.group(0)) if match else None
        return "pre_seed", quote, False
    if SEED_ROUND.search(haystack):
        match = SEED_ROUND.search(haystack)
        quote = _sentence_containing(text, match.group(0)) if match else None
        return "seed", quote, False
    return None, None, False
