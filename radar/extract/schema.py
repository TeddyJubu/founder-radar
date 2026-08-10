"""The one Pydantic model the reader is allowed to produce (05-pipeline §3.2).

Two design choices from the PRD are load-bearing here and must not be undone:

1. **The "is this even about one startup?" decision is a schema field, not a
   second call.** One request, one price, and the gate becomes a plain
   assertion in the golden tests.
2. **`evidence_quote_*` gives a free, deterministic hallucination check.** The
   quotes cost ~30 output tokens and let `grounding.py` drop any field the
   model could not point at in the source text.

Everything the model is allowed to say lives in `LLM_FIELDS`. The remaining
fields are *bookkeeping* written by our own code after validation
(`extraction_method`, `needs_review`, `dropped_fields`, ...) and are stripped
out of the JSON schema the provider sees, so the model can never set them.
"""

from __future__ import annotations

import copy
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from radar.config.models import FOUNDER_SIGNALS, SECTORS, STAGES, canon_enum

# Bumping this invalidates every cache entry cleanly (05-pipeline §3.3).
EXTRACTOR_VERSION = "extract-1"

REJECTION_REASONS = (
    "roundup",
    "market_report",
    "opinion",
    "not_a_startup",
    "already_large_company",
    "no_company_identified",
    "paywalled",
)

RejectionReason = Literal[
    "roundup",
    "market_report",
    "opinion",
    "not_a_startup",
    "already_large_company",
    "no_company_identified",
    "paywalled",
]

ExtractionMethod = Literal["llm", "heuristic", "prefilter"]

# Below this the record goes to the Needs Review tab (05-pipeline §3.4 sets the
# heuristic floor at 0.3; anything under 0.5 from the model is thin content).
# ponytail: the PRD names 0.3 for heuristic records but never states the
# review threshold for model records. 0.5 is the assumption.
LOW_CONFIDENCE = 0.5


class Founder(BaseModel):
    """A named human. Privacy rules in 02-architecture §8 apply at ingest:
    name, role and a public professional URL only — never DOB, address, email."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Full name exactly as printed in the article")
    role: Optional[str] = Field(None, description="e.g. 'co-founder and CEO'")
    profile_url: Optional[str] = Field(
        None, description="Public professional profile URL if the article links one"
    )
    signal: Optional[str] = Field(
        None,
        description=(
            "One of: " + ", ".join(FOUNDER_SIGNALS) + ". Null when the article does not say."
        ),
    )
    evidence_quote: Optional[str] = Field(
        None, description="Verbatim span from the article naming this person"
    )

    @model_validator(mode="before")
    @classmethod
    def _canon(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("signal") is not None:
            # Unknown must never silently become a valid value (03-data-model §4).
            data = {**data, "signal": canon_enum(data["signal"], FOUNDER_SIGNALS)}
        return data


class Extraction(BaseModel):
    """Article prose → one structured record. Nothing else."""

    model_config = ConfigDict(extra="forbid")

    # ---- the gate, decided by the model, part of the schema, therefore testable
    is_about_single_company: bool = Field(
        description=(
            "False for round-ups, listicles, market reports, opinion pieces, "
            "or anything covering 3+ companies with no single subject."
        )
    )
    rejection_reason: Optional[RejectionReason] = Field(
        None, description="Why this article is not a usable single-company signal."
    )

    # ---- the record
    company_name: Optional[str] = None
    company_website: Optional[str] = None
    one_line_description: Optional[str] = Field(
        None,
        max_length=200,
        description=(
            "What the company DOES — its product or service, in one plain "
            "sentence, as the company would describe itself. Never the news "
            "event: not 'raises £2m Seed', not 'has secured funding to "
            "expand'. Write 'Turns brewery waste into packaging foam', not "
            "'Newcastle startup raises £900k'. Null if the article never says "
            "what the company actually does."
        ),
    )
    sector: Optional[str] = Field(None, description="One of: " + ", ".join(SECTORS))
    stage: Optional[str] = Field(None, description="One of: " + ", ".join(STAGES))
    hq_city: Optional[str] = None
    hq_country_iso2: Optional[str] = Field(None, pattern=r"^[A-Z]{2}$")
    founded_year: Optional[int] = Field(None, ge=1990, le=2030)
    founders: list[Founder] = Field(default_factory=list)

    amount_raised_gbp: Optional[float] = Field(
        None, description="Equity raised, in GBP. Null unless the article states GBP."
    )
    amount_original: Optional[float] = Field(
        None, description="The number as printed, when the currency is not GBP."
    )
    amount_currency: Optional[str] = Field(
        None, pattern=r"^[A-Z]{3}$", description="ISO-4217 code of amount_original."
    )
    grant_amount_gbp: Optional[float] = Field(
        None, description="Grant / non-dilutive funding. NEVER put a grant in amount_raised_gbp."
    )

    is_university_spinout: Optional[bool] = None
    university_name: Optional[str] = None

    extraction_confidence: float = Field(ge=0.0, le=1.0)

    # ---- evidence: one verbatim span per claim (02-architecture §2)
    evidence_quote_company: Optional[str] = Field(
        None, description="Verbatim span from the article that names the company"
    )
    evidence_quote_amount: Optional[str] = Field(
        None, description="Verbatim span stating the amount"
    )
    evidence_quote_stage: Optional[str] = Field(
        None, description="Verbatim span stating the round or stage"
    )
    evidence_quote_spinout: Optional[str] = Field(
        None, description="Verbatim span tying the company to a university"
    )

    # ---- bookkeeping. Written by us, never by the model.
    extraction_method: ExtractionMethod = "llm"
    needs_review: bool = False
    dropped_fields: list[str] = Field(default_factory=list)
    prefilter_reason: Optional[str] = None
    quarantined: bool = False
    cache_hit: bool = False
    extractor_version: str = EXTRACTOR_VERSION

    # ------------------------------------------------------------- validation

    @model_validator(mode="before")
    @classmethod
    def _canon_enums(cls, data: Any) -> Any:
        """Case/separator-insensitive matching, `None` on no match.

        `canon_enum` returning None is the whole point: an unknown sector must
        not silently become `other`, and `None` is never `0`.
        """
        if not isinstance(data, dict):
            return data
        out = dict(data)
        if out.get("sector") is not None:
            out["sector"] = canon_enum(out["sector"], SECTORS)
        if out.get("stage") is not None:
            out["stage"] = canon_enum(out["stage"], STAGES)
        if isinstance(out.get("hq_country_iso2"), str):
            out["hq_country_iso2"] = out["hq_country_iso2"].strip().upper() or None
        if isinstance(out.get("amount_currency"), str):
            out["amount_currency"] = out["amount_currency"].strip().upper() or None
        if out.get("rejection_reason") is not None:
            out["rejection_reason"] = canon_enum(out["rejection_reason"], REJECTION_REASONS)
        return out

    @model_validator(mode="after")
    def _reject_implies_not_single(self) -> "Extraction":
        # ponytail: the PRD lists "paywalled" and "already_large_company" as
        # rejection reasons, neither of which is about *how many* companies the
        # piece covers. The consistent reading is therefore
        # `rejection_reason is not None` ⟺ `is_about_single_company is False`,
        # i.e. the boolean is "is this a usable single-company signal?".
        if self.rejection_reason is not None:
            self.is_about_single_company = False
        return self

    # ------------------------------------------------------------- helpers

    @property
    def is_usable(self) -> bool:
        """True when this record may become a company candidate."""
        return self.is_about_single_company and bool(self.company_name)

    def quotes(self) -> dict[str, str]:
        """Every non-empty evidence quote, keyed by field name."""
        out = {
            name: getattr(self, name)
            for name in QUOTE_FIELDS
            if getattr(self, name)
        }
        for i, f in enumerate(self.founders):
            if f.evidence_quote:
                out[f"founders[{i}].evidence_quote"] = f.evidence_quote
        return out


QUOTE_FIELDS: tuple[str, ...] = (
    "evidence_quote_company",
    "evidence_quote_amount",
    "evidence_quote_stage",
    "evidence_quote_spinout",
)

# Everything the model is allowed to emit. Anything outside this set is ours.
LLM_FIELDS: tuple[str, ...] = (
    "is_about_single_company",
    "rejection_reason",
    "company_name",
    "company_website",
    "one_line_description",
    "sector",
    "stage",
    "hq_city",
    "hq_country_iso2",
    "founded_year",
    "founders",
    "amount_raised_gbp",
    "amount_original",
    "amount_currency",
    "grant_amount_gbp",
    "is_university_spinout",
    "university_name",
    "extraction_confidence",
    *QUOTE_FIELDS,
)

META_FIELDS: tuple[str, ...] = tuple(
    n for n in Extraction.model_fields if n not in LLM_FIELDS
)


def _strictify(node: Any) -> Any:
    """Make a Pydantic-generated schema acceptable as a strict JSON schema.

    Strict mode wants `additionalProperties: false` and *every* property in
    `required` (optionality is expressed as a nullable union, not absence).
    """
    if isinstance(node, list):
        return [_strictify(n) for n in node]
    if not isinstance(node, dict):
        return node

    out = {k: _strictify(v) for k, v in node.items() if k not in {"title", "default"}}
    if out.get("type") == "object" and "properties" in out:
        out["additionalProperties"] = False
        out["required"] = list(out["properties"].keys())
    return out


def llm_json_schema() -> dict:
    """The schema handed to the provider — model-facing fields only."""
    schema = copy.deepcopy(Extraction.model_json_schema())
    for name in META_FIELDS:
        schema.get("properties", {}).pop(name, None)
    schema["required"] = [n for n in LLM_FIELDS if n in schema.get("properties", {})]
    return _strictify(schema)


def blank(**kw: Any) -> Extraction:
    """A complete, valid record with nothing in it. Used by the reject paths."""
    base: dict[str, Any] = {
        "is_about_single_company": False,
        "extraction_confidence": 0.0,
    }
    base.update(kw)
    return Extraction.model_validate(base)
