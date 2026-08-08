"""Stage ③ — EXTRACT. The only place an AI model is used, boxed on all six sides.

    from radar.extract import extract, ExtractContext
    record = extract(raw_item, ExtractContext(llm=client, db=db))

The box, restated because every side of it is tested:

1. **Free gates first.** `prefilter` runs before any paid call.
2. **One schema.** The model fills `Extraction` or it fills nothing.
3. **Evidence or nothing.** `grounding` drops any field whose quote is not
   verbatim in the source.
4. **Cached by content hash of the extracted text**, never the raw HTML.
5. **A deterministic fallback.** Provider down, `--no-llm`, or
   `llm_enabled = false` all land in `heuristic`, flagged `needs_review`.
6. **Invalid JSON is quarantined, never crashed on.** One retry with the
   validation error appended, then the row goes to the `quarantine` table.

The AI reads prose into a record. It never decides a gate, a score, a
duplicate, or what lands in the sheet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from pydantic import ValidationError

from radar.extract import prefilter as _prefilter
from radar.extract.grounding import (
    GroundingReport,
    ground,
    hallucination_rate,
    normalise_ws,
)
from radar.extract.heuristic import heuristic_extract
from radar.extract.llm import (
    DEFAULT_MODEL,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    CacheMiss,
    InvalidModelJSON,
    LlmCache,
    LLMClient,
    LLMError,
    LLMResponse,
    ProviderDown,
    build_user_prompt,
    cache_key,
    near_dup_key,
    quarantine,
)
from radar.extract.prefilter import PrefilterResult, prefilter, should_extract
from radar.extract.schema import (
    EXTRACTOR_VERSION,
    LOW_CONFIDENCE,
    Extraction,
    Founder,
    blank,
    llm_json_schema,
)

log = logging.getLogger(__name__)

__all__ = [
    "extract",
    "extract_html",
    "extract_all",
    "ExtractContext",
    "Extraction",
    "Founder",
    "GroundingReport",
    "PrefilterResult",
    "prefilter",
    "should_extract",
    "ground",
    "hallucination_rate",
    "heuristic_extract",
    "cache_key",
    "near_dup_key",
    "llm_json_schema",
    "normalise_ws",
    "LLMClient",
    "LLMResponse",
    "LLMError",
    "ProviderDown",
    "CacheMiss",
    "InvalidModelJSON",
    "PROMPT_VERSION",
    "EXTRACTOR_VERSION",
    "DEFAULT_MODEL",
    "LOW_CONFIDENCE",
]


@dataclass
class ExtractContext:
    """Everything the reader is allowed to reach for. No globals."""

    llm: LLMClient | None = None
    db: Any | None = None
    use_llm: bool = True
    model_id: str = DEFAULT_MODEL
    source_key: str = "unknown"
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: Any, *, use_llm: bool = True, **kw: Any) -> "ExtractContext":
        """`llm_enabled` and `llm_model` come from the Settings tab; `--no-llm`
        is the CLI half of the same switch."""
        return cls(
            use_llm=bool(getattr(settings, "llm_enabled", True)) and use_llm,
            model_id=str(getattr(settings, "llm_model", DEFAULT_MODEL)),
            **kw,
        )

    @property
    def cache(self) -> LlmCache:
        return LlmCache(self.db)


def extract(item: Any, ctx: ExtractContext | None = None) -> Extraction:
    """`RawItem` → `Extraction`. Never raises for a content reason."""
    ctx = ctx or ExtractContext()
    html = getattr(item, "body_text", None) or ""
    url = getattr(item, "source_url", None) or ""
    title = getattr(item, "title", None) or _prefilter.parse_title(html)
    source_key = getattr(item, "source_key", None) or ctx.source_key
    return extract_html(url=url, title=title, html=html, ctx=ctx, source_key=source_key)


def extract_html(
    *,
    url: str,
    title: str,
    html: str,
    ctx: ExtractContext | None = None,
    source_key: str = "unknown",
) -> Extraction:
    ctx = ctx or ExtractContext()

    # ---- ① the free cascade ------------------------------------------------
    pre = prefilter(url, title, html)
    if not pre.ok:
        return blank(
            prefilter_reason=pre.reason,
            rejection_reason=_prefilter.REASON_TO_REJECTION.get(pre.reason),
            extraction_method="prefilter",
        )

    # ---- ② no AI wanted, or none available --------------------------------
    if not ctx.use_llm or ctx.llm is None:
        return _finish_heuristic(pre, title=title, html=html, url=url)

    key = cache_key(pre.text, ctx.model_id)
    user = build_user_prompt(title, pre.text, url=url, jsonld=pre.jsonld)

    # ---- ③ the cache, which is also the cost ledger ------------------------
    cached = ctx.cache.get(key)
    if cached is not None:
        record = _validate(cached.payload)
        if record is not None:
            return _finish_llm(record, pre, url=url, cache_hit=True)
        log.warning("cached payload for %s no longer validates — recalling", key[:12])

    # ---- ④ the one paid call, with one retry -------------------------------
    try:
        response = ctx.llm.complete(key=key, system=SYSTEM_PROMPT, user=user)
    except CacheMiss:
        raise  # a replay miss is never a content failure — surface it loudly
    except Exception as exc:
        log.warning("provider unavailable (%s) — heuristic fallback for %s", exc, url)
        return _finish_heuristic(pre, title=title, html=html, url=url)

    ctx.cache.put(key, response)
    record = _validate(response.payload)

    if record is None:
        retry_user = build_user_prompt(
            title,
            pre.text,
            url=url,
            jsonld=pre.jsonld,
            validation_error=_validation_error(response.payload),
        )
        try:
            response = ctx.llm.complete(key=key, system=SYSTEM_PROMPT, user=retry_user)
        except CacheMiss:
            raise
        except Exception as exc:
            log.warning("provider unavailable on retry (%s) for %s", exc, url)
            return _finish_heuristic(pre, title=title, html=html, url=url)
        ctx.cache.put(key, response)
        record = _validate(response.payload)

    if record is None:
        # Quarantine rather than silently drop, then keep the run moving with a
        # heuristic record so the pipeline never stops (05-pipeline §3.3).
        quarantine(
            ctx.db,
            source_key=source_key,
            source_url=url,
            raw=response.payload,
            error=_validation_error(response.payload),
        )
        fallback = _finish_heuristic(pre, title=title, html=html, url=url)
        return fallback.model_copy(update={"quarantined": True})

    return _finish_llm(record, pre, url=url, cache_hit=False)


# ------------------------------------------------------------------ helpers


def _validate(payload: Any) -> Extraction | None:
    if not isinstance(payload, dict):
        return None
    try:
        return Extraction.model_validate(payload)
    except ValidationError:
        return None


def _validation_error(payload: Any) -> str:
    if not isinstance(payload, dict):
        return f"expected a JSON object, got {type(payload).__name__}"
    try:
        Extraction.model_validate(payload)
    except ValidationError as exc:
        return str(exc)
    return "unknown validation error"


def _finish_llm(
    record: Extraction, pre: PrefilterResult, *, url: str, cache_hit: bool
) -> Extraction:
    report = ground(record, pre.text, source_url=url)
    out = report.extraction
    needs_review = (
        out.needs_review
        or out.extraction_confidence < LOW_CONFIDENCE
        # A non-GBP amount is flagged, not silently converted.
        or out.amount_currency not in (None, "GBP")
    )
    return out.model_copy(
        update={
            "extraction_method": "llm",
            "needs_review": needs_review,
            "cache_hit": cache_hit,
            "prefilter_reason": pre.reason,
        }
    )


def _finish_heuristic(pre: PrefilterResult, *, title: str, html: str, url: str) -> Extraction:
    record = heuristic_extract(title=title, text=pre.text, html=html, url=url, jsonld=pre.jsonld)
    out = ground(record, pre.text, source_url=url).extraction
    return out.model_copy(
        update={
            "extraction_method": "heuristic",
            "needs_review": True,
            "prefilter_reason": pre.reason,
        }
    )


def extract_all(items: Iterable[Any], ctx: ExtractContext | None = None) -> list[Extraction]:
    """Convenience for the pipeline: one bad item never stops the batch."""
    ctx = ctx or ExtractContext()
    out: list[Extraction] = []
    for item in items:
        try:
            out.append(extract(item, ctx))
        except CacheMiss:
            raise
        except Exception as exc:  # pragma: no cover - defence in depth
            log.warning("extraction failed for %r: %s", getattr(item, "source_url", item), exc)
            out.append(
                blank(
                    prefilter_reason="error",
                    extraction_method="heuristic",
                    needs_review=True,
                )
            )
    return out
