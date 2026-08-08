"""The verbatim-quote hallucination check (05-pipeline §3.2, item 2).

After parsing, assert every evidence quote appears verbatim in the source text
(after whitespace normalisation). If it doesn't, the model invented it — drop
the fields that quote was supporting and log it. This costs ~30 output tokens
and catches a large fraction of extraction errors with no AI involved.

`test_no_hallucinations` requires a rate of exactly 0, which this module makes
true by construction: an ungrounded quote never survives into the record.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

from radar.extract.schema import QUOTE_FIELDS, Extraction

log = logging.getLogger(__name__)

# A quote shorter than this is not evidence — "AI" appears in almost any
# article about startups, so a two-character span would pass the check while
# proving nothing.
# ponytail: the PRD does not name a minimum quote length. 12 characters is the
# assumption; it is long enough to be a real span and short enough to admit
# "raised £2m".
MIN_QUOTE_CHARS = 12

_WS = re.compile(r"\s+")

# Models routinely re-type curly quotes as straight ones and en-dashes as
# hyphens. That is a transcription artefact, not an invention, so normalise it
# away on both sides before comparing. Case is NOT normalised: a quote is
# verbatim or it is not.
_PUNCT = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-",
    "…": "...", " ": " ", " ": " ", " ": " ", "​": "",
}
_PUNCT_TABLE = {ord(k): v for k, v in _PUNCT.items()}


def normalise_ws(text: str | None) -> str:
    """NFKC + punctuation folding + whitespace collapse. Nothing else."""
    if not text:
        return ""
    out = unicodedata.normalize("NFKC", text).translate(_PUNCT_TABLE)
    return _WS.sub(" ", out).strip()


def appears_verbatim(quote: str | None, source_text: str) -> bool:
    """True when `quote` really is a span of `source_text`."""
    needle = normalise_ws(quote)
    if len(needle) < MIN_QUOTE_CHARS:
        return False
    return needle in normalise_ws(source_text)


# Which fields each quote is evidence *for*. Losing a quote drops exactly the
# claims it was supporting — never the whole record.
QUOTE_BINDINGS: dict[str, tuple[str, ...]] = {
    "evidence_quote_company": ("company_name", "company_website", "one_line_description"),
    "evidence_quote_amount": (
        "amount_raised_gbp",
        "amount_original",
        "amount_currency",
        "grant_amount_gbp",
    ),
    "evidence_quote_stage": ("stage",),
    "evidence_quote_spinout": ("is_university_spinout", "university_name"),
}


@dataclass
class GroundingReport:
    extraction: Extraction
    dropped: list[str] = field(default_factory=list)
    unevidenced: list[str] = field(default_factory=list)
    quotes_checked: int = 0
    quotes_failed: int = 0

    @property
    def rate(self) -> float:
        """Hallucinated-quote rate over the quotes the model produced."""
        if not self.quotes_checked:
            return 0.0
        return self.quotes_failed / self.quotes_checked


def ground(extraction: Extraction, source_text: str, *, source_url: str | None = None) -> GroundingReport:
    """Drop every claim the model could not point at. Returns a new record."""
    data = extraction.model_dump()
    dropped: list[str] = []
    unevidenced: list[str] = []
    checked = failed = 0

    for quote_field in QUOTE_FIELDS:
        quote = data.get(quote_field)
        bound = QUOTE_BINDINGS[quote_field]
        has_claim = any(data.get(f) is not None for f in bound)

        if quote:
            checked += 1
            if appears_verbatim(quote, source_text):
                continue
            failed += 1
            log.warning(
                "ungrounded quote %s=%r dropped %s (%s)",
                quote_field, quote[:80], list(bound), source_url or "?",
            )
            data[quote_field] = None
            for f in bound:
                if data.get(f) is not None:
                    data[f] = None
                    dropped.append(f)
        elif has_claim:
            # A stated fact with no quote is not a hallucination, but it is not
            # evidence either. Keep it and flag the record for review.
            unevidenced.extend(f for f in bound if data.get(f) is not None)

    # Founders carry their own quote; an invented person is dropped whole.
    kept_founders = []
    for i, founder in enumerate(data.get("founders") or []):
        quote = founder.get("evidence_quote")
        if quote:
            checked += 1
            if appears_verbatim(quote, source_text):
                kept_founders.append(founder)
                continue
            failed += 1
            log.warning("ungrounded founder quote %r dropped %r", quote[:80], founder.get("name"))
            dropped.append(f"founders[{i}]")
            continue
        unevidenced.append(f"founders[{i}]")
        kept_founders.append(founder)
    data["founders"] = kept_founders

    data["dropped_fields"] = sorted(set(list(data.get("dropped_fields") or []) + dropped))
    if dropped or unevidenced:
        data["needs_review"] = True

    return GroundingReport(
        extraction=Extraction.model_validate(data),
        dropped=dropped,
        unevidenced=sorted(set(unevidenced)),
        quotes_checked=checked,
        quotes_failed=failed,
    )


def hallucination_rate(extraction: Extraction, source_text: str) -> float:
    """Fraction of the record's surviving quotes that are not verbatim.

    On a grounded record this is 0.0 by construction — which is exactly what
    `test_no_hallucinations` asserts.
    """
    quotes = extraction.quotes()
    if not quotes:
        return 0.0
    bad = sum(1 for q in quotes.values() if not appears_verbatim(q, source_text))
    return bad / len(quotes)
