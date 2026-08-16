"""Deterministic normalisation — names, domains, money, dates, postcodes.

Stage ④ of the pipeline is "no network, no AI" (05-pipeline §4). Everything in
this module is a pure function of its input, so the same mention always produces
the same key and a merge is reproducible from the audit trail alone.

Two invariants carried from 03-data-model:

* **Companies House numbers are strings.** `00445790` cast to an integer becomes
  `445790` — a different company, or none at all. Normalisation upper-cases and
  zero-pads to 8 characters; it never touches `int`.
* **`None` is never `0`.** "undisclosed" is unknown funding, not zero funding,
  and the distinction survives all the way into scoring.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

from radar.store.db import PLACEHOLDER_NAMES

# --------------------------------------------------------------------- names

# 05-pipeline §4.1. Deliberately NOT stripped: group, holdings, ventures,
# partners, labs, technologies — those are part of the trading name and
# stripping them causes false merges ("Acme Labs" vs "Acme Holdings").
LEGAL_SUFFIXES = {
    "ltd", "limited", "plc", "llp", "lp", "llc", "cic", "cio",
    "inc", "incorporated", "corp", "corporation", "co", "company",
    "gmbh", "ag", "sa", "sas", "sarl", "bv", "nv", "ab", "oy", "as", "aps",
    "srl", "spa", "pty",
}

# Tokens that carry no identifying information. Used by the rare-token guard:
# a fuzzy merge needs at least one *distinctive* token in common.
GENERIC = {
    "labs", "lab", "tech", "technologies", "technology", "systems", "solutions",
    "group", "holdings", "ventures", "partners", "digital", "ai", "robotics", "bio",
    "biotech", "sciences", "science", "research", "innovation", "innovations",
    "international", "global", "uk", "london", "services", "software", "data",
    "health", "medical", "energy", "capital", "studio", "works", "co", "and", "the",
}

_ZERO_WIDTH = re.compile("[\u200b-\u200d\u2060\ufeff]")  # zero-width chars

# 03-data-model §3: the placeholder_name table is seeded with the static list
# "plus anything matching ^(bluesky|company|newco)\d+$". Companies House hands
# out names like "BLUE SKY 4471 LIMITED" and "NEWCO 123 LTD" to formation
# agents, so hundreds of unrelated spinouts share one before they rename
# (04-sources §3.4 #4). `norm_key` strips spaces, so "blue sky 4471" and
# "newco 123" both land here. This is the one definition of placeholder in
# the system — `sources.companies_house` re-exports it rather than keeping
# its own regex.
_PLACEHOLDER_RE = re.compile(r"^(bluesky|company|newco)\d+$")


def norm_name(s: str | None) -> str:
    """Case-folded, accent-folded, punctuation-stripped, suffix-stripped name."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    s = _ZERO_WIDTH.sub("", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if s.startswith("the "):
        s = s[4:]
    toks = s.split()
    while toks and toks[-1] in LEGAL_SUFFIXES:
        toks.pop()
    return " ".join(toks)


def norm_key(s: str | None) -> str:
    """The blocking key: `norm_name` with the spaces taken out.

    Idempotent — `norm_key(norm_key(x)) == norm_key(x)` — so it is safe to call
    on a value that has already been normalised.
    """
    return norm_name(s).replace(" ", "")


def rare_tokens(name: str | None) -> set[str]:
    """Distinctive tokens of a name: not generic, longer than two characters."""
    return {t for t in norm_name(name).split() if t not in GENERIC and len(t) > 2}


def is_placeholder_name(value: str | None, db=None) -> bool:
    """True for names that must never act as a merge key.

    Two companies both called "Stealth" are not duplicates (03-data-model §6,
    query 9). Single-letter names and the empty string are included: neither
    carries enough signal to identify anything.
    """
    key = norm_key(value)
    if len(key) <= 1:
        return True
    if key in PLACEHOLDER_NAMES:
        return True
    if _PLACEHOLDER_RE.match(key):
        return True
    if db is not None:
        row = db.one("SELECT 1 FROM placeholder_name WHERE norm_key = ?", (key,))
        if row is not None:
            return True
    return False


# ------------------------------------------------------- Companies House no.

_CH_STRIP = re.compile(r"[^A-Z0-9]")
# Companies House prefixes seen on real numbers. SC/NI/OC/SO are the ones that
# break naive parsing; the rest are included because they exist and cost nothing.
_CH_PREFIX = re.compile(r"^([A-Z]{2})(\d+)$")


def norm_ch_number(value: str | int | None) -> str | None:
    """Upper-case, strip separators, zero-pad to 8 characters. Always TEXT.

    `445790` and `00445790` are the same company; `SC445790` is a different one.
    """
    if value is None:
        return None
    raw = _CH_STRIP.sub("", str(value).upper())
    if not raw:
        return None
    m = _CH_PREFIX.match(raw)
    if m:
        prefix, digits = m.groups()
        # ponytail: the spec says "zero-pad to 8 characters" without saying where
        # the padding goes for prefixed numbers. Companies House formats them as
        # two letters plus six digits, so we pad the numeric part to 6 — which
        # also happens to make the whole string 8 characters.
        return prefix + digits.zfill(8 - len(prefix))
    if raw.isdigit():
        return raw.zfill(8)
    return raw  # unrecognised shape: keep it verbatim rather than mangle it


# ------------------------------------------------------------------- domains

# 05-pipeline §4.1. Never company identity: a spinout page hosted at
# eng.ox.ac.uk/spinouts/acme gives you the company *name*, not its domain.
DOMAIN_DENYLIST = {
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "crunchbase.com", "github.com", "medium.com", "notion.site", "wixsite.com",
    "webflow.io", "github.io", "wordpress.com", "blogspot.com", "substack.com",
    "youtube.com", "angel.co", "wellfound.com", "sites.google.com",
    "companieshouse.gov.uk", "find-and-update.company-information.service.gov.uk",
}

_TLD_EXTRACT = None


def _extractor():
    """tldextract with the bundled suffix snapshot — never a network fetch."""
    global _TLD_EXTRACT
    if _TLD_EXTRACT is None:
        import tldextract

        _TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), fallback_to_snapshot=True)
    return _TLD_EXTRACT


def host_of(value: str | None) -> str | None:
    """The bare hostname from a URL, a bare domain or an email-ish string."""
    if not value:
        return None
    s = str(value).strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", s)
    s = s.split("@")[-1]
    s = s.split("/")[0].split("?")[0].split("#")[0]
    s = s.split(":")[0]
    s = s.strip().rstrip(".")
    if not s or " " in s:
        return None
    try:  # consistent IDN handling: compare punycode, always
        s = s.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        pass
    return s or None


def is_denylisted_domain(value: str | None) -> bool:
    """True for social, aggregator, host-your-page and academic domains."""
    host = host_of(value)
    if not host:
        return False
    if host == "ac.uk" or host.endswith(".ac.uk"):
        return True
    for bad in DOMAIN_DENYLIST:
        if host == bad or host.endswith("." + bad):
            return True
    return False


def norm_domain(value: str | None) -> str | None:
    """The registrable domain, lowercase, no `www.`, or None if it is not identity.

    Returns None for denylisted hosts so callers cannot accidentally treat
    `linkedin.com` as two companies' shared identity.
    """
    host = host_of(value)
    if not host or is_denylisted_domain(host):
        return None
    parts = _extractor()(host)
    if not parts.domain or not parts.suffix:
        return None
    registrable = f"{parts.domain}.{parts.suffix}".lower()
    return None if is_denylisted_domain(registrable) else registrable


# --------------------------------------------------------------------- money

_MULTIPLIER = {
    "k": 1_000, "thousand": 1_000,
    "m": 1_000_000, "mm": 1_000_000, "mn": 1_000_000, "million": 1_000_000,
    "bn": 1_000_000_000, "b": 1_000_000_000, "billion": 1_000_000_000,
}

_CURRENCY_SYMBOL = {"£": "GBP", "$": "USD", "€": "EUR", "¥": "JPY"}
_CURRENCY_CODE = {"GBP", "USD", "EUR", "JPY", "CHF", "CAD", "AUD", "SEK", "NOK", "DKK"}

# "undisclosed" is not zero. Every one of these means "we do not know".
UNKNOWN_MONEY = {
    "", "-", "--", "n/a", "na", "none", "null", "unknown", "undisclosed",
    "not disclosed", "unspecified", "confidential", "tbc", "tbd", "?",
}

_AMOUNT_RE = re.compile(
    r"(?P<num>\d[\d,\s]*(?:\.\d+)?)\s*(?P<mult>k|m|mm|mn|bn|b|thousand|million|billion)?\b",
    re.I,
)


def parse_money(value: str | int | float | None) -> tuple[float | None, str | None]:
    """`(amount, currency)`. Amount is None when the input states nothing.

    Currency is None when the input carried no symbol or code — the caller
    decides what the default is rather than this function guessing.
    """
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, None
    if isinstance(value, (int, float)):
        return float(value), None

    s = str(value).strip()
    if s.lower() in UNKNOWN_MONEY:
        return None, None

    currency = None
    for sym, iso in _CURRENCY_SYMBOL.items():
        if sym in s:
            currency = iso
            break
    if currency is None:
        code = re.search(r"\b(" + "|".join(sorted(_CURRENCY_CODE)) + r")\b", s, re.I)
        if code:
            currency = code.group(1).upper()

    m = _AMOUNT_RE.search(s)
    if not m:
        return None, currency
    num = m.group("num").replace(",", "").replace(" ", "")
    try:
        amount = float(num)
    except ValueError:
        return None, currency
    mult = (m.group("mult") or "").lower()
    if mult:
        amount *= _MULTIPLIER[mult]
    return amount, currency


def norm_money(value: str | int | float | None, *, assume: str = "GBP") -> float | None:
    """Sterling amount, or None.

    Returns None — never a number — when the amount is in another currency.
    A silently wrong figure is worse than a missing one (09-test-plan §3, #21).
    """
    amount, currency = parse_money(value)
    if amount is None:
        return None
    if currency is not None and currency != assume:
        return None
    return amount


# --------------------------------------------------------------------- dates

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_YMD_SLASH = re.compile(r"^(\d{4})[/.](\d{1,2})[/.](\d{1,2})$")
_DMY_RE = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})$")
_TEXT_DMY = re.compile(r"^(\d{1,2})\s+([a-z]+)\.?\,?\s+(\d{4})$", re.I)
_TEXT_MDY = re.compile(r"^([a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\,?\s+(\d{4})$", re.I)
_MONTH_YEAR = re.compile(r"^([a-z]+)\.?\s+(\d{4})$", re.I)
_YEAR_ONLY = re.compile(r"^(\d{4})$")


def norm_date(value: str | date | datetime | None) -> str | None:
    """ISO `YYYY-MM-DD`, or None when the input is not a full date.

    ponytail: a bare year returns None rather than 1 January. The age gate is
    measured in months, so inventing a day would fabricate up to twelve months
    of precision on the single field the whole product is judged by. Callers
    that only have a year should record it as an observation, not as a date.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    s = str(value).strip()
    if not s or s.lower() in UNKNOWN_MONEY:
        return None

    m = _ISO_RE.match(s)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    m = _YMD_SLASH.match(s)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    m = _DMY_RE.match(s)
    if m:  # UK order: 03/04/2024 is 3 April, not 4 March
        d, mo, y = (int(x) for x in m.groups())
        if y < 100:
            y += 2000 if y < 70 else 1900
        return _safe_date(y, mo, d)

    m = _TEXT_DMY.match(s)
    if m:
        mo = _MONTHS.get(m.group(2)[:4].lower()) or _MONTHS.get(m.group(2)[:3].lower())
        if mo:
            return _safe_date(int(m.group(3)), mo, int(m.group(1)))

    m = _TEXT_MDY.match(s)
    if m:
        mo = _MONTHS.get(m.group(1)[:4].lower()) or _MONTHS.get(m.group(1)[:3].lower())
        if mo:
            return _safe_date(int(m.group(3)), mo, int(m.group(2)))

    if _MONTH_YEAR.match(s) or _YEAR_ONLY.match(s):
        return None  # not a date — see the docstring
    return None


def date_confidence(value: str | date | datetime | None) -> str | None:
    """`exact` for a full date, `inferred` for a month or year, else None."""
    if value is None:
        return None
    if norm_date(value) is not None:
        return "exact"
    s = str(value).strip()
    if _MONTH_YEAR.match(s) or _YEAR_ONLY.match(s):
        return "inferred"
    return None


def _safe_date(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


# ----------------------------------------------------------------- postcodes

_POSTCODE_RE = re.compile(r"^([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})$")


def norm_postcode(value: str | None) -> str | None:
    """`ne14st` → `NE1 4ST`. None when it is not a UK postcode."""
    if not value:
        return None
    s = re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()
    m = _POSTCODE_RE.match(s)
    if not m:
        return None
    return f"{m.group(1)} {m.group(2)}"


def outcode_of(value: str | None) -> str | None:
    """The outward code — `NE1 4ST` → `NE1`. This is the postcodes.io cache key.

    The single definition of "outcode" in the system: `score.derive` and
    `enrich.postcode` re-export this instead of keeping their own regexes.
    Returns None for anything that is not a UK postcode or outcode.
    """
    full = norm_postcode(value)
    if full:
        return full.split(" ")[0]
    if not value:
        return None
    s = re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()
    m = re.match(r"^[A-Z]{1,2}\d[A-Z\d]?$", s)
    return s if m else None


# ----------------------------------------------------------------- countries

_COUNTRY_ALIASES = {
    "uk": "GB", "u k": "GB", "gb": "GB", "gbr": "GB", "england": "GB",
    "scotland": "GB", "wales": "GB", "northern ireland": "GB",
    "united kingdom": "GB", "great britain": "GB",
    "usa": "US", "us": "US", "u s a": "US", "united states": "US",
    "united states of america": "US", "america": "US",
    "ireland": "IE", "eire": "IE", "irl": "IE",
    "germany": "DE", "deutschland": "DE", "france": "FR", "spain": "ES",
    "netherlands": "NL", "holland": "NL",
}


def norm_country(value: str | None) -> str | None:
    """ISO-3166 alpha-2, upper-case. None stays None — never defaulted to `GB`.

    03-data-model §2: defaulting the country would make the `min_uk_presence`
    gate a no-op for missing data, which is exactly the silent pass this system
    exists to avoid.
    """
    if value is None:
        return None
    s = re.sub(r"[^a-z ]", " ", str(value).strip().lower())
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return None
    if s in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[s]
    if len(s) == 2:
        return s.upper()
    return None
