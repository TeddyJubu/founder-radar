"""The provider seam: one protocol, one real implementation, one replay stub.

Rules from 05-pipeline §3.3 and 02-architecture §2, all of them tested:

* **Pin a dated model snapshot, never an alias.** An alias rolls the model
  under you and turns golden tests into flaky tests. The id comes from
  `Settings.llm_model`.
* **Cache by content hash of the EXTRACTED TEXT, not the raw HTML.** Raw HTML
  changes on every load (ad slots, CSRF tokens, cache-busting params), so a raw
  hash never matches. The key is
  `sha256(prompt_version | model_id | normalised_text)`.
* **`llm_cache` doubles as the cost ledger.** Every call records `tokens_in`,
  `tokens_out` and `cost_usd` (03-data-model §6, query 10).
* **`ReplayLLM` hard-fails on a miss.** A silent network call in CI is worse
  than a broken test.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from radar.extract.grounding import normalise_ws
from radar.extract.schema import llm_json_schema

log = logging.getLogger(__name__)

# Bump either constant to invalidate every cache entry cleanly.
#
# The subject-selection rule changed, so cached articles must be re-read under
# the new prompt rather than silently retaining an investor or parent record.
PROMPT_VERSION = "2026-08-12.1"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"  # dated snapshot, never an alias

MAX_PROMPT_WORDS = 1500  # startup articles put who/what/how-much up front
MAX_OUTPUT_TOKENS = 2048
# The OpenAI-compatible provider needs far more output headroom than the
# Anthropic one: gateways often route `kilo-auto/free`-style ids to a
# *reasoning* model, whose thinking trace can burn 2-6k tokens before the JSON
# answer appears (verified against api.kilo.ai, Aug 2026). 2048 was enough for
# Anthropic's strict server-side schema; the OpenAI provider gets this instead.
OPENAI_MAX_OUTPUT_TOKENS = 8192
NEAR_DUP_SLICE = (200, 1200)

# USD per million tokens, keyed by model id. Used for the cost ledger only —
# nothing in the pipeline branches on price.
PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-5-20250929": (3.00, 15.00),
}


# --------------------------------------------------------------------- errors


class LLMError(RuntimeError):
    """Base for everything this module raises."""


class ProviderDown(LLMError):
    """The provider is unreachable, rate-limited or erroring.

    This is the only failure the pipeline is allowed to swallow: it triggers
    the deterministic heuristic fallback so the run still completes.
    """


class CacheMiss(LLMError):
    """A replay client was asked for a response it does not have.

    Deliberately NOT a `ProviderDown`: this must surface as a test failure, not
    silently degrade to the heuristic extractor.
    """


class InvalidModelJSON(LLMError):
    """The model returned something that is not the agreed schema."""


# ------------------------------------------------------------------- prompts

SYSTEM_PROMPT = """\
You read one UK startup news article and return one JSON record. You do not \
decide anything else: you never score, rank, deduplicate, or judge whether a \
company is a good investment. Another system does that.

Rules:
1. Report only what the article actually says. Never infer a founder, an \
amount, a sector or a city that is not stated.
2. Every claim you make must be backed by a verbatim quote copied character \
for character from the article. If you cannot quote it, leave the field null \
and do not invent a quote. A quote that is not in the article is the worst \
possible failure.
3. Subject identity: company_name must be the operating startup that is the \
relevant subject — the company receiving the investment, being spun out, or \
whose product or milestone is being reported. Never use the parent company, \
investor, fund, university or acquirer named around it. Set company_role to \
startup for the operating company and to parent, investor, acquirer, \
university or other when company_name is one of those surrounding entities. \
If the article clearly names only a parent, investor or other non-startup, set \
is_about_single_company to false with rejection_reason not_a_startup.
4. Set is_about_single_company to false for round-ups, listicles, market \
reports, opinion pieces, accelerator cohort announcements, and anything \
covering three or more companies with no single subject. Give the matching \
rejection_reason.
5. Money: put equity funding in amount_raised_gbp only when the article states \
it in pounds. If the amount is in another currency, put the number in \
amount_original with the ISO code in amount_currency and leave \
amount_raised_gbp null. Put grants and non-dilutive awards in \
grant_amount_gbp, never in amount_raised_gbp.
6. sector and stage must be one of the listed values, or null. Never guess a \
close-enough value. pre_seed / seed / series_a for those rounds. series_b_plus \
or growth for Series B and later, a growth round, or a company approaching an \
IPO. If the article is about a public company, a stock-market listing, or an \
IPO, set is_about_single_company false with rejection_reason \
already_large_company — that is not an early-stage lead.
7. extraction_confidence is your honest confidence in the whole record: 0.85+ \
for a clear single-company funding story with named founders, 0.3-0.5 for a \
thin regional note with few facts.
8. one_line_description describes the COMPANY, not the article. Say what it \
makes or does, the way the company would say it. Never restate the news event, \
and never reuse the headline. If the article only reports that money changed \
hands and never says what the company does, leave it null.

Return the JSON record and nothing else."""


def truncate_words(text: str, limit: int = MAX_PROMPT_WORDS) -> str:
    """First `limit` whitespace-separated tokens, original spacing preserved."""
    tokens = list(re.finditer(r"\S+", text or ""))
    if len(tokens) <= limit:
        return text or ""
    return text[: tokens[limit - 1].end()]


def prompt_text(title: str, text: str) -> str:
    """Exactly what the model is shown, and exactly what the cache is keyed on."""
    return f"{(title or '').strip()}\n\n{truncate_words(text or '')}".strip()


def build_user_prompt(
    title: str,
    text: str,
    *,
    url: str | None = None,
    jsonld: dict | None = None,
    validation_error: str | None = None,
) -> str:
    parts = ["<article>"]
    if url:
        parts.append(f"<url>{url}</url>")
    if jsonld:
        hints = {k: v for k, v in jsonld.items() if isinstance(v, str)}
        if hints:
            parts.append("<page_metadata>" + json.dumps(hints, sort_keys=True) + "</page_metadata>")
    parts.append(prompt_text(title, text))
    parts.append("</article>")
    if validation_error:
        parts.append(
            "\nYour previous answer failed schema validation with this error. "
            "Return the corrected record.\n<validation_error>"
            f"{validation_error}</validation_error>"
        )
    return "\n".join(parts)


# --------------------------------------------------------------------- keys


def cache_key(
    text: str,
    model_id: str = DEFAULT_MODEL,
    prompt_version: str = PROMPT_VERSION,
) -> str:
    """`sha256(prompt_version | model_id | normalise_ws(text))`.

    `text` is the *extracted* article text, never the raw HTML.
    """
    blob = f"{prompt_version}|{model_id}|{normalise_ws(text)}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def near_dup_key(text: str) -> str:
    """Hash of `text[200:1200]` after whitespace normalisation.

    Kills ~80% of syndicated copies that exact hashing misses, because the
    syndicating site rewrites the headline and the first paragraph but not the
    middle of the body (05-pipeline §3.3).
    """
    body = normalise_ws(text)[NEAR_DUP_SLICE[0]: NEAR_DUP_SLICE[1]]
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def estimate_cost(model_id: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = PRICES_USD_PER_MTOK.get(model_id, (0.0, 0.0))
    return round((tokens_in * price_in + tokens_out * price_out) / 1_000_000, 8)


# ------------------------------------------------------------------ protocol


@dataclass(frozen=True)
class LLMResponse:
    """One provider call, plus what it cost."""

    payload: dict
    model_id: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    raw_text: str | None = None


@runtime_checkable
class LLMClient(Protocol):
    """The mock seam. Everything downstream depends on this, not on a vendor."""

    model_id: str

    def complete(self, *, key: str, system: str, user: str) -> LLMResponse: ...


# -------------------------------------------------------------- real provider


class AnthropicLLM:
    """The one place a provider SDK is imported.

    `temperature=0`, strict JSON schema enforcement on, dated snapshot id.
    Every provider exception becomes `ProviderDown` so the caller has exactly
    one failure mode to handle.
    """

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        max_tokens: int = MAX_OUTPUT_TOKENS,
        client: Any | None = None,
    ) -> None:
        if model_id.count("-") < 3 or not re.search(r"\d{8}$", model_id):
            # Fail loud rather than let an alias roll the golden tests.
            raise ValueError(
                f"{model_id!r} is not a dated snapshot id — pin one "
                "(e.g. claude-haiku-4-5-20251001), never an alias"
            )
        self.model_id = model_id
        self.max_tokens = max_tokens
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = client

    def _connect(self) -> Any:
        if self._client is None:
            try:
                import anthropic  # type: ignore
            except ImportError as exc:  # pragma: no cover - env dependent
                raise ProviderDown(f"anthropic SDK not installed: {exc}") from exc
            kwargs = {"api_key": self._api_key} if self._api_key else {}
            self._client = anthropic.Anthropic(**kwargs)
        return self._client

    def complete(self, *, key: str, system: str, user: str) -> LLMResponse:
        client = self._connect()
        try:
            message = client.messages.create(
                model=self.model_id,
                max_tokens=self.max_tokens,
                temperature=0,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "format": {"type": "json_schema", "schema": llm_json_schema()}
                },
            )
        except Exception as exc:  # network, 429, 5xx, timeout — all the same to us
            raise ProviderDown(f"{type(exc).__name__}: {exc}") from exc

        text = "".join(
            block.text for block in message.content if getattr(block, "type", None) == "text"
        )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InvalidModelJSON(f"not JSON: {exc}") from exc

        usage = getattr(message, "usage", None)
        tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
        return LLMResponse(
            payload=payload,
            model_id=self.model_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=estimate_cost(self.model_id, tokens_in, tokens_out),
            raw_text=text,
        )


class OpenAILLM:
    """OpenAI-format chat completions — OpenAI itself, or any compatible gateway.

    The second provider for the seam, for the case where the only AI credential
    on the box is an OpenAI-shaped key (08-deployment §3 reserves `LLM_PROVIDER`
    and `LLM_API_KEY` for exactly this). Same contract as `AnthropicLLM`: every
    provider exception becomes `ProviderDown`, the caller still has exactly one
    failure mode to handle, and `estimate_cost` prices the ledger (an unknown
    model id — e.g. a gateway's free tier — records £0.00 honestly).

    Two robustness details the Anthropic client does not need:

    * `response_format` json_object is sent because the prompt is already a
      strict "return only JSON" contract, but a gateway that rejects the field
      must degrade to heuristic, not crash — so the whole call is one try.
    * Reasoning models sometimes wrap the JSON in markdown fences, so fences
      are stripped before `json.loads`; and a `content: null` (the reasoning
      ate the whole token budget) is a `ProviderDown`, i.e. heuristic, not a
      quarantine.
    """

    def __init__(
        self,
        model_id: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = OPENAI_MAX_OUTPUT_TOKENS,
        client: Any | None = None,
    ) -> None:
        self.model_id = model_id
        self.max_tokens = max_tokens
        self._api_key = api_key or os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self._base_url = (base_url or os.environ.get("LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self._client = client

    def _connect(self) -> Any:
        if self._client is None:
            try:
                import httpx  # type: ignore
            except ImportError as exc:  # pragma: no cover - env dependent
                raise ProviderDown(f"httpx not installed: {exc}") from exc
            self._client = httpx.Client(timeout=90)
        return self._client

    def complete(self, *, key: str, system: str, user: str) -> LLMResponse:
        client = self._connect()
        # The Anthropic client gets the schema enforced server-side via
        # `output_config`; an OpenAI-format gateway usually only honours
        # `json_object`, so the schema must be *told* to the model. Without it,
        # the free tier answers close-enough shapes (founders as strings, extra
        # keys) that fail `Extraction` validation and get quarantined.
        schema_hint = (
            "\n\nYour JSON output MUST match this exact schema — the same key "
            "names, the same types, no extra keys, no omitted required keys. "
            "Arrays keep the exact element shape shown (e.g. founders is an "
            "array of objects with name and role, never strings).\n"
            + json.dumps(llm_json_schema(), sort_keys=True)
        )
        content: str | None = None
        data: dict = {}
        for attempt in range(2):
            # A reasoning model (e.g. the free tier of a gateway) can spend the
            # whole token budget thinking and answer `content: null`. That is a
            # bad draw, not a dead provider, so one retry happens here — before
            # the pipeline's heuristic fallback — rather than degrading a whole
            # article over a coin flip.
            try:
                response = client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": self.model_id,
                        "max_tokens": self.max_tokens,
                        "temperature": 0,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": system + schema_hint},
                            {"role": "user", "content": user},
                        ],
                    },
                )
                response.raise_for_status()
                data = response.json()
            except Exception as exc:  # network, 4xx, 5xx, timeout — all the same
                raise ProviderDown(f"{type(exc).__name__}: {exc}") from exc

            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise ProviderDown(f"no content in completion: {exc}") from exc
            if content and str(content).strip():
                break
        if not content or not str(content).strip():
            raise ProviderDown("empty completion content on both attempts")
        content = str(content).strip()
        # A reasoning model may fence the JSON; the Anthropic client never sees
        # fences because output_config enforces the schema server-side.
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content).strip()
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise InvalidModelJSON(f"not JSON: {exc}") from exc

        usage = data.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens", 0) or 0)
        tokens_out = int(usage.get("completion_tokens", 0) or 0)
        return LLMResponse(
            payload=payload,
            model_id=self.model_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=estimate_cost(self.model_id, tokens_in, tokens_out),
            raw_text=content,
        )


# ------------------------------------------------------------------- factory


def build_llm(
    *,
    provider: str | None = None,
    api_key: str | None = None,
    model_id: str | None = None,
    base_url: str | None = None,
) -> LLMClient | None:
    """One client from env, or `None` for the documented heuristic-only mode.

    `LLM_PROVIDER` is `anthropic` (default) or `openai` — any OpenAI-format
    chat-completions gateway. The generic `LLM_API_KEY` (the name 08-deployment
    §3 and the README promise) is read first, then the provider-specific key.
    No key → `None` → heuristic extraction at £0 AI cost, which is a supported
    mode, not an error (README: "with no LLM_API_KEY the system falls back to
    a deterministic heuristic extractor").
    """
    provider = (provider or os.environ.get("LLM_PROVIDER") or "anthropic").lower()
    model_id = model_id or os.environ.get("LLM_MODEL") or DEFAULT_MODEL
    if provider in ("openai", "openai_compatible", "openai-compatible"):
        key = api_key or os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            return None
        return OpenAILLM(model_id, api_key=key, base_url=base_url)
    key = api_key or os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    return AnthropicLLM(model_id, api_key=key)


# ------------------------------------------------------------------- replay


class ReplayLLM:
    """Serves `tests/fixtures/llm_cache` and nothing else.

    A miss is a test failure, not a network call. `REFRESH_LLM=1` is the only
    path allowed to reach a provider; it records the response and returns it,
    so `REFRESH_LLM=1 pytest` re-records cleanly.
    """

    def __init__(
        self,
        cache_dir: str | Path,
        *,
        model_id: str = DEFAULT_MODEL,
        refresh: bool | None = None,
        provider_factory: Callable[[], LLMClient] | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.model_id = model_id
        self.refresh = os.environ.get("REFRESH_LLM") == "1" if refresh is None else refresh
        self._provider_factory = provider_factory or (lambda: AnthropicLLM(model_id))
        self.calls: list[str] = []

    def path_for(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def complete(self, *, key: str, system: str, user: str) -> LLMResponse:
        self.calls.append(key)
        path = self.path_for(key)
        if path.is_file():
            record = json.loads(path.read_text())
            return LLMResponse(
                payload=record["payload"],
                model_id=record.get("model_id", self.model_id),
                tokens_in=int(record.get("tokens_in", 0)),
                tokens_out=int(record.get("tokens_out", 0)),
                cost_usd=float(record.get("cost_usd", 0.0)),
            )

        if not self.refresh:
            raise CacheMiss(
                f"LLM cache miss for {key}. Re-record with REFRESH_LLM=1 pytest, "
                f"or rebuild keys offline with tests/fixtures/rekey_llm_cache.py "
                f"(looked in {self.cache_dir})."
            )

        response = self._provider_factory().complete(key=key, system=system, user=user)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "prompt_version": PROMPT_VERSION,
                    "model_id": response.model_id,
                    "tokens_in": response.tokens_in,
                    "tokens_out": response.tokens_out,
                    "cost_usd": response.cost_usd,
                    "payload": response.payload,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        return response


class StubLLM:
    """A canned client for unit tests that do not need a recorded fixture."""

    def __init__(self, payload: dict | list[dict] | Exception, model_id: str = DEFAULT_MODEL):
        self.model_id = model_id
        self._payloads = payload if isinstance(payload, list) else [payload]
        self.calls: list[dict] = []

    def complete(self, *, key: str, system: str, user: str) -> LLMResponse:
        self.calls.append({"key": key, "system": system, "user": user})
        item = self._payloads[min(len(self.calls) - 1, len(self._payloads) - 1)]
        if isinstance(item, Exception):
            raise item
        return LLMResponse(
            payload=item, model_id=self.model_id, tokens_in=900, tokens_out=180,
            cost_usd=estimate_cost(self.model_id, 900, 180),
        )


# ------------------------------------------------------ cache + cost ledger


@dataclass
class CacheEntry:
    payload: dict
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


class LlmCache:
    """The `llm_cache` table. Also the cost ledger — same rows, two readers."""

    def __init__(self, db: Any | None) -> None:
        self.db = db

    def get(self, key: str) -> CacheEntry | None:
        if self.db is None:
            return None
        row = self.db.one(
            "SELECT response_json, tokens_in, tokens_out, cost_usd FROM llm_cache WHERE key = ?",
            (key,),
        )
        if row is None:
            return None
        try:
            payload = json.loads(row["response_json"])
        except (TypeError, json.JSONDecodeError):
            return None
        return CacheEntry(
            payload=payload,
            tokens_in=row["tokens_in"] or 0,
            tokens_out=row["tokens_out"] or 0,
            cost_usd=row["cost_usd"] or 0.0,
        )

    def put(self, key: str, response: LLMResponse) -> None:
        if self.db is None:
            return
        from radar.store.db import now_iso

        self.db.execute(
            """INSERT OR REPLACE INTO llm_cache
               (key, response_json, tokens_in, tokens_out, cost_usd, created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                key,
                json.dumps(response.payload, sort_keys=True),
                response.tokens_in,
                response.tokens_out,
                response.cost_usd,
                now_iso(),
            ),
        )

    def monthly_spend(self) -> list[dict]:
        """03-data-model §6 query 10, so the ledger has exactly one owner."""
        if self.db is None:
            return []
        rows = self.db.query(
            "SELECT strftime('%Y-%m', created_at) AS month, COUNT(*) AS calls, "
            "ROUND(SUM(cost_usd), 4) AS cost_usd FROM llm_cache "
            "GROUP BY month ORDER BY month DESC"
        )
        return [dict(r) for r in rows]


def quarantine(
    db: Any | None,
    *,
    source_key: str,
    source_url: str | None,
    raw: Any,
    error: str,
) -> None:
    """Keep the bad record for inspection rather than silently dropping it."""
    if db is None:
        return
    from radar.store.db import now_iso

    db.execute(
        "INSERT INTO quarantine (source_key, source_url, raw_json, error, created_at) "
        "VALUES (?,?,?,?,?)",
        (source_key, source_url, json.dumps(raw, default=str), error[:2000], now_iso()),
    )
