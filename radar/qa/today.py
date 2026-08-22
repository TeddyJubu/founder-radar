"""Today QA — a boxed veto after scoring, before anything is shown.

The deterministic pipeline still chooses the shortlist. This module then asks
a Hermes *subagent* (an isolated one-shot `hermes chat -Q --query-file -`
pass with a dedicated prompt, not the Telegram front desk) whether each
selected company is the WRONG company to put on Today: already backed, IPO /
late-stage, parent or investor, wrong legal entity, or a city that cannot be
the winning vehicle's region.

It may only *remove* a card. It cannot add one, cannot change a score, and
cannot merge. A stored `reason` is what makes "why did this drop off Today?"
a sentence a human can check, the same way `config_hash` makes a score
change answerable.

If Hermes is down, a small deterministic pre-check still catches the obvious
holes (IPO copy, Oxford offered as Yorkshire). Anything it cannot prove stays
on the list — a quiet Hermes day must not empty Today. `--no-llm` skips the
subagent but still runs the pre-check.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from radar.store.db import now_iso

log = logging.getLogger(__name__)

PROMPT_VERSION = "today-qa-2026-08-22.1"
QA_LIMIT = 24
HERMES_TIMEOUT_S = 60
REVIEWABLE = ("shortlist", "watchlist")
TRACK_A = ("news", "grant", "spinout", "accelerator")
VENTURE_SIGNAL_KINDS = (
    "share_issue", "grant_award", "spinout", "press", "news", "competition_win",
)

REJECT_REASONS = (
    "already_backed",
    "late_stage",
    "ipo",
    "wrong_entity",
    "not_a_startup",
    "geography_mismatch",
    "parent_or_investor",
    "already_large",
)

VERDICT_RE = re.compile(
    r"^\s*VERDICT:\s*(PASS|REJECT)\s*$", re.IGNORECASE | re.MULTILINE)
REASON_RE = re.compile(
    r"^\s*REASON:\s*([a-z_]+)\s*$", re.IGNORECASE | re.MULTILINE)
SUMMARY_RE = re.compile(
    r"^\s*SUMMARY:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)

IPO_RE = re.compile(
    r"(?i)(?:"
    r"\bpre-?IPO\b"
    r"|\bfiles? for (?:an )?IPO\b"
    r"|\bIPO\s+(?:filing|process|plans?|debut|listing)\b"
    r"|\blisted on (?:the )?(?:AIM|LSE|NASDAQ|NYSE|London Stock Exchange)\b"
    r"|\binitial public offering\b"
    r"|\bpublicly listed\b"
    r")"
)
LATE_STAGE_RE = re.compile(
    r"\b(Series [B-Z]\b|growth round|late[- ]stage|pre-IPO)\b", re.I)
BACKED_RE = re.compile(
    r"\b(backed by|portfolio compan|parkwalk|already (venture[- ])?backed|"
    r"zinc[- ]backed)\b",
    re.I,
)
GOLDEN_CITIES = frozenset({"oxford", "cambridge", "london"})
REGIONAL_GEOS = frozenset({
    "yorkshire", "north_east", "north_england", "sunderland",
})

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROMPT_PATH = (
    _REPO_ROOT / "hermes" / "skills" / "founder-radar"
    / "references" / "today-check.md"
)

FALLBACK_PROMPT = (
    "You are the Founder Radar Today QA subagent. You do not score. You only "
    "decide whether this company is the WRONG company for Today's list. "
    "WRONG: already VC-backed or on a fund/TTO portfolio; IPO / listed / "
    "Series B+; not an operating startup; wrong legal entity; city clearly "
    "wrong for the winning vehicle. PASS if it looks like a genuine "
    "early-stage UK operating startup. If unsure and there is no positive "
    "evidence it is wrong, PASS.\n\n"
    "Return exactly:\n"
    "VERDICT: PASS\nSUMMARY: <one sentence>\n"
    "or\n"
    "VERDICT: REJECT\n"
    "REASON: already_backed|late_stage|ipo|wrong_entity|not_a_startup|"
    "geography_mismatch|parent_or_investor|already_large\n"
    "SUMMARY: <one sentence>\n"
)


# ----------------------------------------------------------------- errors


class TodayQaError(RuntimeError):
    """Base for Today QA failures. The pipeline swallows these."""


class HermesUnavailable(TodayQaError):
    """The Hermes binary is missing, timed out, or returned nothing usable."""


class InvalidVerdict(TodayQaError):
    """The subagent did not return a parseable PASS/REJECT."""


# ------------------------------------------------------------------- types


@dataclass(frozen=True)
class TodayCard:
    """The facts the subagent is allowed to see — a Today card, not a score."""

    company_id: str
    name: str
    city: str | None = None
    region: str | None = None
    stage: str | None = None
    one_liner: str | None = None
    incorporated_on: str | None = None
    route: str | None = None
    website: str | None = None
    source_url: str | None = None
    source_key: str | None = None
    fund_key: str | None = None
    vehicle_key: str | None = None
    geo_rule: str | None = None
    geo_values: tuple[str, ...] = ()
    headlines: tuple[str, ...] = ()
    on_vc_portfolio: bool = False
    sector: str | None = None
    explanation: str | None = None

    def blob(self) -> str:
        """Stable serialisation — the cache key and the prompt body."""
        payload = {
            "company_id": self.company_id,
            "name": self.name,
            "city": self.city,
            "region": self.region,
            "stage": self.stage,
            "one_liner": self.one_liner,
            "incorporated_on": self.incorporated_on,
            "route": self.route,
            "website": self.website,
            "source_url": self.source_url,
            "source_key": self.source_key,
            "fund_key": self.fund_key,
            "vehicle_key": self.vehicle_key,
            "geo_rule": self.geo_rule,
            "geo_values": list(self.geo_values),
            "headlines": list(self.headlines),
            "on_vc_portfolio": self.on_vc_portfolio,
            "sector": self.sector,
            "explanation": self.explanation,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)

    def snapshot_hash(self) -> str:
        return hashlib.sha256(
            f"{PROMPT_VERSION}|{self.blob()}".encode()
        ).hexdigest()


@dataclass(frozen=True)
class TodayCheckResult:
    verdict: str                          # pass | reject
    reason: str | None = None
    summary: str = ""
    checker: str = "hermes"
    raw_text: str | None = None


@dataclass
class TodayQaReport:
    checked: int = 0
    passed: int = 0
    rejected: int = 0
    skipped: int = 0
    cached: int = 0
    warnings: list[str] = field(default_factory=list)


@runtime_checkable
class TodayChecker(Protocol):
    """The mock seam. Tests inject one; production uses `HermesSubagent`."""

    name: str

    def review(self, card: TodayCard) -> TodayCheckResult: ...


# ----------------------------------------------------------------- prompt


def subagent_prompt() -> str:
    """The Today QA subagent brief. The skill file is the source of truth."""
    try:
        text = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return FALLBACK_PROMPT
    return text or FALLBACK_PROMPT


def build_user_prompt(card: TodayCard) -> str:
    return (
        f"{subagent_prompt()}\n\n"
        f"<today_card>\n{card.blob()}\n</today_card>"
    )


# ------------------------------------------------------------------ parse


def parse_verdict(text: str, *, checker: str = "hermes") -> TodayCheckResult:
    """Read `VERDICT: PASS|REJECT` from free text, or a small JSON object.

    Anything else raises `InvalidVerdict` so the caller can skip rather than
    invent a decision.
    """
    raw = (text or "").strip()
    if not raw:
        raise InvalidVerdict("empty response")

    stripped = raw
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped).strip()

    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise InvalidVerdict(f"not JSON: {exc}") from exc
        verdict = str(payload.get("verdict") or payload.get("VERDICT") or "").lower()
        if verdict not in {"pass", "reject"}:
            raise InvalidVerdict(f"JSON verdict {verdict!r} is not pass/reject")
        reason = payload.get("reason") or payload.get("REASON")
        if isinstance(reason, str):
            reason = reason.strip().lower() or None
        else:
            reason = None
        if verdict == "reject" and reason not in REJECT_REASONS:
            reason = reason if reason in REJECT_REASONS else "not_a_startup"
        summary = str(payload.get("summary") or payload.get("SUMMARY") or "").strip()
        return TodayCheckResult(
            verdict=verdict, reason=reason, summary=summary,
            checker=checker, raw_text=raw,
        )

    match = VERDICT_RE.search(raw)
    if match is None:
        raise InvalidVerdict("no VERDICT line")
    verdict = match.group(1).lower()
    reason = None
    if verdict == "reject":
        reason_match = REASON_RE.search(raw)
        candidate = (reason_match.group(1).lower() if reason_match else "")
        reason = candidate if candidate in REJECT_REASONS else "not_a_startup"
    summary_match = SUMMARY_RE.search(raw)
    summary = summary_match.group(1).strip() if summary_match else ""
    return TodayCheckResult(
        verdict=verdict, reason=reason, summary=summary,
        checker=checker, raw_text=raw,
    )


# ---------------------------------------------------------- rules fallback


def _joined_text(card: TodayCard) -> str:
    parts = [
        card.name, card.one_liner, card.stage, card.explanation,
        *card.headlines,
    ]
    return " ".join(p for p in parts if p)


def rules_precheck(card: TodayCard) -> TodayCheckResult | None:
    """Catch the obvious leftovers without a model.

    Returns a reject, or None when the card needs Hermes (or is fine).
    This is the fallback when Hermes is down *and* a first pass that saves
    a subagent call on copy that already names an IPO or a golden-triangle
    city routed to a northern vehicle.
    """
    blob = _joined_text(card)
    if IPO_RE.search(blob):
        return TodayCheckResult(
            verdict="reject", reason="ipo", checker="rules",
            summary="Copy names an IPO or listing — not an early-stage lead.",
        )
    if LATE_STAGE_RE.search(blob):
        return TodayCheckResult(
            verdict="reject", reason="late_stage", checker="rules",
            summary="Copy names a late-stage or Series B+ round.",
        )
    if card.on_vc_portfolio or BACKED_RE.search(blob):
        return TodayCheckResult(
            verdict="reject", reason="already_backed", checker="rules",
            summary="Already on a VC portfolio or described as backed.",
        )
    city = (card.city or "").strip().lower()
    geos = {g.strip().lower() for g in card.geo_values if g}
    if city in GOLDEN_CITIES and geos & REGIONAL_GEOS:
        return TodayCheckResult(
            verdict="reject", reason="geography_mismatch", checker="rules",
            summary=(
                f"{card.city} cannot satisfy a "
                f"{'/'.join(sorted(geos & REGIONAL_GEOS))} vehicle."
            ),
        )
    return None


# ----------------------------------------------------------- hermes runner


def _argv_for_log(argv: list[str]) -> list[str]:
    """Argv without a multi-kilobyte prompt — logs must stay readable."""
    return [part if len(part) < 64 else f"<{len(part)} chars>" for part in argv[1:]]


class HermesSubagent:
    """One-shot Hermes chat as the Today QA subagent.

    Isolated from the Telegram gateway: no chat history, no scoring skill,
    just the Today-check brief plus one card. Every failure becomes
    `HermesUnavailable` so the pipeline has one mode to swallow.

    Hermes treats `-q` as `--query` (the prompt), not quiet. Quiet is `-Q`.
    `--query-file -` is the documented way to pass a long JSON body on stdin
    without shell-quoting it.
    """

    name = "hermes"

    def __init__(
        self,
        *,
        binary: str | None = None,
        timeout: float = HERMES_TIMEOUT_S,
        runner: Any | None = None,
    ) -> None:
        self._binary = binary
        self.timeout = timeout
        self._runner = runner or subprocess.run

    def review(self, card: TodayCard) -> TodayCheckResult:
        text = self._run(build_user_prompt(card))
        return parse_verdict(text, checker=self.name)

    def _run(self, prompt: str) -> str:
        binary = self._binary or shutil.which("hermes")
        if not binary:
            raise HermesUnavailable("hermes binary not on PATH")
        # stdin=True means the prompt is the query body; otherwise it is argv.
        attempts: list[tuple[list[str], bool]] = [
            ([binary, "chat", "-Q", "--query-file", "-"], True),
            ([binary, "chat", "-Q", "-q", prompt], False),
            ([binary, "-z", prompt], False),
        ]
        last: str | None = None
        env = {**os.environ, "TERM": "dumb", "HERMES_NONINTERACTIVE": "1"}
        for argv, use_stdin in attempts:
            try:
                completed = self._runner(  # noqa: S603 - fixed argv, no shell
                    argv,
                    input=prompt if use_stdin else None,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    env=env,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "hermes today-qa %s failed: %s", _argv_for_log(argv), last,
                )
                continue
            text = (completed.stdout or "").strip()
            if not text:
                text = (completed.stderr or "").strip()
            if completed.returncode == 0 and text:
                return text
            last = f"exit {completed.returncode}: {text[:240]}"
            log.warning("hermes today-qa %s: %s", _argv_for_log(argv), last)
        raise HermesUnavailable(last or "hermes returned nothing")


def build_today_checker(*, checker: TodayChecker | None = None) -> TodayChecker | None:
    """Production default: Hermes when the binary exists, otherwise None."""
    if checker is not None:
        return checker
    if os.environ.get("TODAY_QA", "1") in {"0", "false", "no"}:
        return None
    if shutil.which("hermes"):
        return HermesSubagent()
    return None


# -------------------------------------------------------------- persistence


def _one(db: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    if hasattr(db, "one"):
        return db.one(sql, params)
    return db.execute(sql, params).fetchone()


def _execute(db: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    if hasattr(db, "execute"):
        return db.execute(sql, params)
    raise TypeError(f"not a database: {type(db)!r}")


def _query(db: Any, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
    if hasattr(db, "query"):
        return list(db.query(sql, params))
    return list(db.execute(sql, params).fetchall())


def record_check(
    db: Any,
    card: TodayCard,
    result: TodayCheckResult,
    *,
    checked_at: str | None = None,
) -> None:
    """Write one veto. Re-checking the same snapshot replaces the row."""
    _execute(
        db,
        """INSERT OR REPLACE INTO today_check
           (company_id, snapshot_hash, verdict, reason, summary, checker,
            prompt_version, raw_text, checked_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            card.company_id, card.snapshot_hash(), result.verdict, result.reason,
            result.summary, result.checker, PROMPT_VERSION, result.raw_text,
            checked_at or now_iso(),
        ),
    )


def cached_check(db: Any, card: TodayCard) -> TodayCheckResult | None:
    try:
        row = _one(
            db,
            "SELECT verdict, reason, summary, checker, raw_text "
            "FROM today_check WHERE company_id = ? AND snapshot_hash = ?",
            (card.company_id, card.snapshot_hash()),
        )
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return TodayCheckResult(
        verdict=row["verdict"], reason=row["reason"], summary=row["summary"] or "",
        checker=row["checker"] or "hermes", raw_text=row["raw_text"],
    )


def latest_today_verdict(db: Any, company_id: str) -> str | None:
    """The newest check for this company, any snapshot. None if never checked."""
    try:
        row = _one(
            db,
            "SELECT verdict FROM today_check WHERE company_id = ? "
            "ORDER BY checked_at DESC, rowid DESC LIMIT 1",
            (company_id,),
        )
    except sqlite3.OperationalError:
        return None
    return row["verdict"] if row else None


def is_rejected(db: Any, company_id: str) -> bool:
    """True when the latest Today QA verdict is reject.

    A missing table or a missing row is not a reject — Today stays populated
    when QA has not run yet.
    """
    return latest_today_verdict(db, company_id) == "reject"


# ---------------------------------------------------------- card loading


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _vehicle_geo(cfg: Any, vehicle_key: str | None) -> tuple[str | None, tuple[str, ...]]:
    if not vehicle_key or cfg is None:
        return None, ()
    for fund in getattr(cfg, "funds", ()):
        for vehicle in fund.vehicles:
            if vehicle.vehicle_key == vehicle_key:
                return vehicle.geo_rule, tuple(vehicle.geo_values or ())
    return None, ()


def _http_source(db: Any, company_id: str) -> tuple[str | None, str | None]:
    rows = _query(
        db,
        "SELECT source_key, source_url FROM company_source WHERE company_id = ? "
        "ORDER BY last_seen DESC",
        (company_id,),
    )
    picked = None
    for row in rows:
        url = str(_row_get(row, "source_url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        key = _row_get(row, "source_key")
        if key != "companies_house":
            return key, url
        picked = picked or (key, url)
    return picked or (None, None)


def _headlines(db: Any, company_id: str, *, limit: int = 4) -> tuple[str, ...]:
    rows = _query(
        db,
        "SELECT headline FROM signal WHERE company_id = ? "
        "AND headline IS NOT NULL AND headline != '' "
        "ORDER BY COALESCE(occurred_on, first_seen) DESC LIMIT ?",
        (company_id, limit),
    )
    return tuple(str(row["headline"]) for row in rows if _row_get(row, "headline"))


def _is_registry_route(route: str | None) -> bool:
    return (route or "registry") in {"registry", ""}


def _active_config_hash(db: Any) -> str | None:
    """Same generation Today reads: last-good snapshot, canonicalized hash.

    Tests without a snapshot keep latest-per-company behaviour so a seeded
    `testhash` still reaches the checker.
    """
    try:
        row = _one(
            db,
            "SELECT config_json FROM config_snapshot WHERE is_last_good = 1 "
            "ORDER BY created_at DESC LIMIT 1",
        )
    except sqlite3.OperationalError:
        return None
    payload = _row_get(row, "config_json") if row else None
    if not payload:
        return None
    from radar.config.loader import parse_snapshot

    parsed = parse_snapshot(payload)
    return parsed.hash() if parsed is not None else None


def has_registry_venture_signal(db: Any, company_id: str) -> bool:
    """True when a Companies House card has a real venture signal, not just a Ltd.

    Shared with the Today prototype so a registry watchlist row that can occupy
    the queue is also the row this module asks Hermes about.
    """
    rows = _query(
        db,
        "SELECT source_key, source_url FROM company_source WHERE company_id = ?",
        (company_id,),
    )
    if any(
        _row_get(row, "source_key") != "companies_house"
        and str(_row_get(row, "source_url") or "").startswith(("http://", "https://"))
        for row in rows
    ):
        return True
    row = _one(
        db,
        "SELECT has_share_issue, is_university_spinout, news_mention_count "
        "FROM company WHERE id = ?",
        (company_id,),
    )
    if row is not None:
        if _row_get(row, "has_share_issue") or _row_get(row, "is_university_spinout"):
            return True
        if (_row_get(row, "news_mention_count") or 0) > 0:
            return True
    placeholders = ",".join("?" * len(VENTURE_SIGNAL_KINDS))
    found = _one(
        db,
        f"SELECT 1 AS ok FROM signal WHERE company_id = ? "
        f"AND kind IN ({placeholders}) LIMIT 1",
        (company_id, *VENTURE_SIGNAL_KINDS),
    )
    return found is not None


def load_today_cards(
    db: Any,
    cfg: Any = None,
    *,
    limit: int = QA_LIMIT,
) -> list[TodayCard]:
    """The companies Today *would* consider, in Today order, capped.

    Shortlist, Track A watchlist, and registry watchlist rows that already
    have a venture signal (SH01 / grant / press / a non-CH source). Registry
    shells without a venture signal are skipped so we do not spend a Hermes
    call on a card `_today_block_reason` would already hide.
    """
    config_hash = _active_config_hash(db)
    hash_sql = "AND s.config_hash = ?" if config_hash else ""
    hash_params: tuple[Any, ...] = (config_hash,) if config_hash else ()
    track_sql = ",".join("?" * len(TRACK_A))
    rows = _query(
        db,
        f"""
        WITH latest AS (
          SELECT s.*,
                 ROW_NUMBER() OVER (
                   PARTITION BY s.company_id
                   ORDER BY s.priority DESC, s.scored_at DESC, s.id DESC
                 ) AS score_rank
            FROM score s
           WHERE s.tier IN (?, ?)
             {hash_sql}
        )
        SELECT c.id AS company_id, c.canonical_name, c.hq_city, c.hq_region,
               c.stage, c.one_liner, c.incorporated_on, c.discovery_route,
               c.website_url, c.on_vc_portfolio, c.sector, c.merged_into,
               s.fund_key, s.vehicle_key, s.tier, s.priority, s.explanation
          FROM latest s
          JOIN company c ON c.id = s.company_id
         WHERE s.score_rank = 1 AND c.merged_into IS NULL
         ORDER BY CASE WHEN c.discovery_route IN ({track_sql}) THEN 0 ELSE 1 END,
                  s.priority DESC, c.canonical_name
        """,
        (*REVIEWABLE, *hash_params, *TRACK_A),
    )
    cards: list[TodayCard] = []
    for row in rows:
        source_key, source_url = _http_source(db, row["company_id"])
        if not source_url:
            continue
        route = _row_get(row, "discovery_route")
        if _is_registry_route(route) and not has_registry_venture_signal(
            db, row["company_id"],
        ):
            continue
        geo_rule, geo_values = _vehicle_geo(cfg, _row_get(row, "vehicle_key"))
        cards.append(TodayCard(
            company_id=row["company_id"],
            name=row["canonical_name"],
            city=_row_get(row, "hq_city"),
            region=_row_get(row, "hq_region"),
            stage=_row_get(row, "stage"),
            one_liner=_row_get(row, "one_liner"),
            incorporated_on=_row_get(row, "incorporated_on"),
            route=route,
            website=_row_get(row, "website_url"),
            source_url=source_url,
            source_key=source_key,
            fund_key=_row_get(row, "fund_key"),
            vehicle_key=_row_get(row, "vehicle_key"),
            geo_rule=geo_rule,
            geo_values=geo_values,
            headlines=_headlines(db, row["company_id"]),
            on_vc_portfolio=bool(_row_get(row, "on_vc_portfolio") or 0),
            sector=_row_get(row, "sector"),
            explanation=_row_get(row, "explanation"),
        ))
        if len(cards) >= max(1, int(limit)):
            break
    return cards


# ----------------------------------------------------------------- runner


def _config_for(db: Any, cfg: Any) -> Any:
    if cfg is not None:
        return cfg
    try:
        row = _one(
            db,
            "SELECT config_json FROM config_snapshot WHERE is_last_good = 1 "
            "ORDER BY created_at DESC LIMIT 1",
        )
    except sqlite3.OperationalError:
        row = None
    if row and _row_get(row, "config_json"):
        from radar.config.loader import parse_snapshot

        parsed = parse_snapshot(row["config_json"])
        if parsed is not None:
            return parsed
    from radar.config.defaults import default_config

    return default_config()


def check_one(
    db: Any,
    card: TodayCard,
    *,
    checker: TodayChecker | None,
) -> TodayCheckResult:
    """Rules first; Hermes on whatever rules cannot prove is wrong.

    Either reject wins. Hermes cannot override a rules reject — those are
    the leftover holes (IPO copy, Oxford-as-Yorkshire) that must not depend
    on a model being up.
    """
    cached = cached_check(db, card)
    if cached is not None:
        return cached

    rules = rules_precheck(card)
    if rules is not None and rules.verdict == "reject":
        record_check(db, card, rules)
        return rules

    if checker is None:
        passed = TodayCheckResult(
            verdict="pass", checker="skip",
            summary="Hermes unavailable; rules found no obvious veto.",
        )
        record_check(db, card, passed)
        return passed

    try:
        result = checker.review(card)
    except TodayQaError as exc:
        log.warning("today QA subagent failed for %s: %s", card.name, exc)
        if rules is not None:
            record_check(db, card, rules)
            return rules
        skipped = TodayCheckResult(
            verdict="pass", checker="skip",
            summary=f"subagent failed: {exc}",
        )
        record_check(db, card, skipped)
        return skipped

    if result.verdict not in {"pass", "reject"}:
        raise InvalidVerdict(result.verdict)
    record_check(db, card, result)
    return result


def run_today_qa(
    db: Any,
    cfg: Any = None,
    *,
    checker: TodayChecker | None = None,
    use_hermes: bool = True,
    limit: int = QA_LIMIT,
) -> TodayQaReport:
    """Check the companies selected for Today. Never raises into the run."""
    report = TodayQaReport()
    try:
        cfg = _config_for(db, cfg)
        cards = load_today_cards(db, cfg, limit=limit)
    except Exception as exc:  # noqa: BLE001 - one stage, not the run
        report.warnings.append(f"today QA skipped: {type(exc).__name__}: {exc}")
        log.warning("today QA could not load cards: %s", exc)
        return report

    active: TodayChecker | None = None
    if use_hermes:
        try:
            active = build_today_checker(checker=checker)
        except Exception as exc:  # noqa: BLE001
            report.warnings.append(f"hermes subagent unused: {type(exc).__name__}")
            log.warning("could not build today checker: %s", exc)
    elif checker is not None:
        active = checker

    if cards and use_hermes and active is None:
        report.warnings.append("today QA: Hermes not on PATH — rules only")

    for card in cards:
        before = cached_check(db, card)
        try:
            result = check_one(db, card, checker=active)
        except Exception as exc:  # noqa: BLE001 - one company, not the run
            report.skipped += 1
            report.warnings.append(f"{card.name}: {type(exc).__name__}: {exc}")
            log.warning("today QA skipped %s: %s", card.name, exc)
            continue
        report.checked += 1
        if before is not None:
            report.cached += 1
        if result.verdict == "reject":
            report.rejected += 1
            log.info(
                "today QA rejected %s (%s): %s",
                card.name, result.reason, result.summary,
            )
        else:
            report.passed += 1
    if report.rejected:
        report.warnings.append(
            f"today QA dropped {report.rejected} of {report.checked} selected companies"
        )
    return report


__all__ = [
    "HermesSubagent",
    "HermesUnavailable",
    "InvalidVerdict",
    "PROMPT_VERSION",
    "QA_LIMIT",
    "REJECT_REASONS",
    "TodayCard",
    "TodayCheckResult",
    "TodayChecker",
    "TodayQaError",
    "TodayQaReport",
    "build_today_checker",
    "build_user_prompt",
    "cached_check",
    "check_one",
    "has_registry_venture_signal",
    "is_rejected",
    "latest_today_verdict",
    "load_today_cards",
    "parse_verdict",
    "record_check",
    "rules_precheck",
    "run_today_qa",
    "subagent_prompt",
]
