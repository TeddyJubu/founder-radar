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

A small deterministic pre-check still catches the obvious holes (IPO copy,
Oxford offered as Yorkshire) whether or not Hermes is up.

Every card ends in exactly one of three recorded outcomes:

* `pass`       a checker looked at the card and found nothing wrong;
* `reject`     a checker (or the pre-check) found it is the wrong company;
* `incomplete` nothing could vouch for it — Hermes missing, timed out, or
               returned something unparseable.

An incomplete card is NOT a pass. It is never cached as one (the next run asks
again) and it is withheld from every surface — the live Today page, the Sheet
and the Telegram ping — until a check completes (`qa_state` / `is_withheld`,
the one rule all three read). A card QA never reached at all (no row) is also
withheld until its first completed pass.

The one deliberate exception is an explicit rules-only mode (`--no-llm`,
`--no-hermes`, `TODAY_QA=0`, or the operator override
`RADAR_ALLOW_RULES_ONLY_PUBLISH=1`): there the pre-check is the whole check, its
passes are recorded as checker `rules`, stay visible, and never satisfy a later
run that does have Hermes.
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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from radar.store.db import now_iso

log = logging.getLogger(__name__)

PROMPT_VERSION = "today-qa-2026-08-22.1"
QA_LIMIT = 24
HERMES_TIMEOUT_S = 60
# A Hermes call can burn 3 attempts x HERMES_TIMEOUT_S. When this many cards in a
# row fail, Hermes is down, not the cards: stop asking and leave the rest
# incomplete instead of hanging a 3600 s unit on a dead subagent.
MAX_CONSECUTIVE_FAILURES = 3
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
    "source_dead",
    "invalid_source",
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
    """Base for Today QA failures. `check_one` records the card as `incomplete`."""


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
    recommendation_reason: str | None = None
    recommendation_warning: str | None = None

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
            "recommendation_reason": self.recommendation_reason,
            "recommendation_warning": self.recommendation_warning,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)

    def snapshot_hash(self) -> str:
        return hashlib.sha256(
            f"{PROMPT_VERSION}|{self.blob()}".encode()
        ).hexdigest()


@dataclass(frozen=True)
class TodayCheckResult:
    verdict: str                          # pass | reject | incomplete
    reason: str | None = None
    summary: str = ""
    checker: str = "hermes"
    raw_text: str | None = None


@dataclass
class TodayQaReport:
    checked: int = 0                      # cards with a final verdict (pass + reject)
    passed: int = 0
    rejected: int = 0
    skipped: int = 0                      # cards `check_one` itself blew up on
    cached: int = 0
    cards: int = 0
    # Cards with NO final verdict. They are withheld from Today, the Sheet and
    # the ping until a later run completes the check.
    incomplete: int = 0
    # Candidates ranked below QA_LIMIT: withheld until a later completed check.
    uncovered: int = 0
    hermes_failures: int = 0
    # True only when Hermes was the checker AND no card was left incomplete —
    # "a checker was selected" is not the same as "the check happened".
    hermes_used: bool = False
    rules_only: bool = False              # a deliberate rules-only mode
    aborted: str | None = None            # why QA could not run at all
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


def resolve_hermes_binary() -> str | None:
    """Prefer `HERMES_BIN` (hermes.env / systemd); fall back to PATH."""
    env_bin = (os.environ.get("HERMES_BIN") or "").strip()
    if env_bin and os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
        return env_bin
    return shutil.which("hermes")


def _hermes_subprocess_env() -> dict[str, str]:
    """Env for Hermes subagents: remap HERMES_HOME to the data dir Hermes expects."""
    # QA runs as radar against the operator-owned Hermes installation.
    # Keep startup read-only: lazy dependency updates require the owner.
    env = {**os.environ, "TERM": "dumb", "HERMES_NONINTERACTIVE": "1",
           "HERMES_DISABLE_LAZY_INSTALLS": "1"}
    owner_home = (os.environ.get("HERMES_HOME") or "").strip()
    if owner_home:
        hermes_dir = Path(owner_home) / ".hermes"
        env["HOME"] = owner_home
        if hermes_dir.is_dir():
            env["HERMES_HOME"] = str(hermes_dir)
        else:
            env.pop("HERMES_HOME", None)
    return env


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
        binary = self._binary or resolve_hermes_binary()
        if not binary:
            raise HermesUnavailable("hermes binary not on PATH")
        _refresh_hermes_acl()
        try:
            return self._query(binary, prompt)
        finally:
            # Hermes, running as radar under the operator's home, rewrites
            # files there (auth.json, caches) and chmods the tree. Healing only
            # *before* the next run left the gateway — which reads the same
            # files as the operator — crash-looping in between.
            _refresh_hermes_acl()

    def _query(self, binary: str, prompt: str) -> str:
        # stdin=True means the prompt is the query body; otherwise it is argv.
        attempts: list[tuple[list[str], bool]] = [
            ([binary, "chat", "-Q", "--query-file", "-"], True),
            ([binary, "chat", "-Q", "-q", prompt], False),
            ([binary, "-z", prompt], False),
        ]
        last: str | None = None
        env = _hermes_subprocess_env()
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


def _refresh_hermes_acl() -> None:
    try:
        from radar.qa.publish import _ensure_hermes_acl

        _ensure_hermes_acl()
    except Exception:  # noqa: BLE001 — ACL refresh is best-effort
        pass


def build_today_checker(*, checker: TodayChecker | None = None) -> TodayChecker | None:
    """Production default: Hermes when the binary exists, otherwise None."""
    if checker is not None:
        return checker
    if os.environ.get("TODAY_QA", "1") in {"0", "false", "no"}:
        return None
    binary = resolve_hermes_binary()
    if binary:
        return HermesSubagent(binary=binary)
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

    if _state_of(result.verdict, result.checker) == "pass":
        current = load_today_cards(db, _config_for(db, None),
                                   company_id=card.company_id, limit=1)
        if current and current[0].snapshot_hash() == card.snapshot_hash():
            from radar.score.snapshot import approve_current
            approve_current(db, card, checked_at or now_iso())


def cached_check(db: Any, card: TodayCard) -> TodayCheckResult | None:
    try:
        row = _one(
            db,
            "SELECT verdict, reason, summary, checker, raw_text, checked_at "
            "FROM today_check WHERE company_id = ? AND snapshot_hash = ?",
            (card.company_id, card.snapshot_hash()),
        )
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    if _state_of(row["verdict"], row["checker"]) == "pass":
        latest = _one(db, "SELECT MAX(scored_at) AS stamp FROM score WHERE company_id = ?",
                      (card.company_id,))
        # A rescore creates a new generation even when the card text is unchanged.
        # Reusing its old pass would skip Hermes and pretend new QA completed.
        if latest and latest["stamp"] and latest["stamp"] > row["checked_at"]:
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
    when QA has not run yet. Surfaces should ask `is_withheld`, which also
    covers a check that did not complete.
    """
    return latest_today_verdict(db, company_id) == "reject"


def _state_of(verdict: str | None, checker: str | None) -> str:
    """The one place a stored row becomes a decision: pass | reject | incomplete.

    Anything that is not a clean `pass` or `reject` is incomplete — including
    the `pass` / `skip` rows older builds wrote when Hermes was down or timed
    out, which were never checks and must not be trusted now.
    """
    if verdict == "reject":
        return "reject"
    if verdict == "pass" and (checker or "") != "skip":
        return "pass"
    return "incomplete"


def qa_state(db: Any, company_id: str) -> str | None:
    """`pass` | `reject` | `incomplete` from the newest check, None if never checked."""
    try:
        row = _one(
            db,
            "SELECT verdict, checker, checked_at, snapshot_hash FROM today_check WHERE company_id = ? "
            "ORDER BY checked_at DESC, rowid DESC LIMIT 1",
            (company_id,),
        )
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    state = _state_of(row["verdict"], row["checker"])
    if state == "pass":
        cards = load_today_cards(db, _config_for(db, None), company_id=company_id, limit=1)
        if not cards or cards[0].snapshot_hash() != row["snapshot_hash"]:
            return "incomplete"
        changed = _one(db, "SELECT MAX(scored_at) AS stamp FROM score WHERE company_id = ?",
                       (company_id,))
        if changed and changed["stamp"] and changed["stamp"] > row["checked_at"]:
            return "incomplete"
        # Once a URL has been checked, its stale or unusable outcome cannot
        # be hidden behind a model approval. This is a pure database read.
        try:
            link = _one(db, "SELECT state, expires_at FROM source_link_check WHERE url = ?",
                        (cards[0].source_url.split("#", 1)[0],))
        except sqlite3.OperationalError:
            link = None
        if not link and row["checker"] == "hermes":
            return "incomplete"
        if link:
            expired = datetime.fromisoformat(link["expires_at"]) <= datetime.now(timezone.utc)
            if not expired and link["state"] in {"dead", "invalid"}:
                return "reject"
            if expired or link["state"] != "reachable":
                return "incomplete"
    return state


def historical_pass(db: Any, company_id: str, approved_hash: str | None) -> bool:
    """Proof for this frozen score, while retaining the latest rejection veto."""
    if not approved_hash or is_rejected(db, company_id):
        return False
    row = _one(db, "SELECT verdict, checker FROM today_check "
               "WHERE company_id = ? AND snapshot_hash = ?",
               (company_id, approved_hash))
    return bool(row and _state_of(row["verdict"], row["checker"]) == "pass")


def is_withheld(db: Any, company_id: str) -> bool:
    """True when Today QA keeps this company off every surface.

    Rejected, or checked without a completed verdict. The live Today page, the
    Sheet's Today tab, the digest and the ping's counts all ask this.
    """
    return qa_state(db, company_id) != "pass"


def withheld_company_ids(db: Any) -> dict[str, str]:
    """`{company_id: 'reject' | 'incomplete'}` for every company QA withholds."""
    rows = _query(db, "SELECT id FROM company WHERE merged_into IS NULL")
    return {row["id"]: qa_state(db, row["id"]) or "incomplete"
            for row in rows if is_withheld(db, row["id"])}


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
    company_id: str | None = None,
) -> list[TodayCard]:
    """The companies Today *would* consider, in Today order, capped.

    Shortlist, Track A watchlist, and registry watchlist rows that already
    have a venture signal (SH01 / grant / press / a non-CH source). Registry
    shells without a venture signal are skipped so we do not spend a Hermes
    call on a card `_today_block_reason` would already hide. Reviewed companies
    and exact-snapshot cached rejects do not consume the QA limit. Explicit
    company lookups keep those records available for approval and history.
    """
    config_hash = _active_config_hash(db)
    hash_sql = "AND s.config_hash = ?" if config_hash else ""
    hash_params: tuple[Any, ...] = (config_hash,) if config_hash else ()
    company_sql = "AND s.company_id = ?" if company_id is not None else ""
    company_params = (company_id,) if company_id is not None else ()
    # Queue QA shares Today review decisions. Explicit identity lookups bypass
    # these filters so Kept and historical/current approval checks still work.
    decision_sql = ""
    decision_params: tuple[Any, ...] = ()
    if company_id is None:
        day = date.today().isoformat()
        decision_sql = """AND NOT EXISTS (
            SELECT 1 FROM user_field u WHERE u.company_id = s.company_id
              AND u.field = 'verdict' AND TRIM(COALESCE(u.value, '')) != ''
              AND substr(u.updated_at, 1, 10) < ?)
            AND NOT EXISTS (
            SELECT 1 FROM daily_review dr WHERE dr.company_id = s.company_id
              AND dr.review_date = ?)"""
        decision_params = (day, day)
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
             {company_sql}
             {decision_sql}
        )
        SELECT c.id AS company_id, c.canonical_name, c.hq_city, c.hq_region,
               c.stage, c.one_liner, c.incorporated_on, c.discovery_route,
               c.website_url, c.on_vc_portfolio, c.sector, c.merged_into,
               c.country_iso2, c.hq_postcode, c.companies_house_no, c.total_funding_gbp,
               s.fund_key, s.vehicle_key, s.tier, s.priority, s.explanation
          FROM latest s
          JOIN company c ON c.id = s.company_id
         WHERE s.score_rank = 1 AND c.merged_into IS NULL
         ORDER BY CASE WHEN c.discovery_route IN ({track_sql}) THEN 0 ELSE 1 END,
                  s.priority DESC, c.canonical_name
        """,
        (*REVIEWABLE, *hash_params, *company_params, *decision_params, *TRACK_A),
    )
    from radar.selection import rank_today_rows, deterministic_block_reason, recommend_today_rows
    cfg = cfg or _config_for(db, None)
    rows = recommend_today_rows(db, rows, cfg, config_hash=config_hash)
    cards: list[TodayCard] = []
    eligible_rows = []
    for row in rows:
        if company_id is None and deterministic_block_reason(
            db, row, cfg or _config_for(db, None), today=date.today(), config_hash=config_hash,
        ):
            continue
        source_key, source_url = _http_source(db, row["company_id"])
        if not source_url:
            continue
        route = _row_get(row, "discovery_route")
        if _is_registry_route(route) and not has_registry_venture_signal(
            db, row["company_id"],
        ):
            continue
        geo_rule, geo_values = _vehicle_geo(cfg, _row_get(row, "vehicle_key"))
        card = TodayCard(
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
            recommendation_reason=_row_get(row, "recommendation_reason"),
            recommendation_warning=_row_get(row, "recommendation_warning"),
        )
        if company_id is None:
            cached = cached_check(db, card)
            if cached is not None and cached.verdict == "reject":
                continue
        cards.append(card)
        eligible_rows.append(row)
    if company_id is not None:
        return cards[:max(1, int(limit))]
    by_id = {card.company_id: card for card in cards}
    ranked = rank_today_rows(db, eligible_rows, config_hash=config_hash)
    return [by_id[row["company_id"]] for row in ranked[:max(1, int(limit))]]


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


def _truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes"}


def rules_only_mode(*, use_hermes: bool = True) -> bool:
    """Is rules-only QA a choice someone made, rather than an outage?

    `--no-llm` / `--no-hermes` (`use_hermes=False`), `TODAY_QA=0`, and the
    operator override `RADAR_ALLOW_RULES_ONLY_PUBLISH=1` all say "the
    pre-check is enough". A missing or failing Hermes with none of those set
    is an outage, and its cards are `incomplete`, not passed.
    """
    if not use_hermes:
        return True
    if os.environ.get("TODAY_QA", "1") in {"0", "false", "no"}:
        return True
    return _truthy("RADAR_ALLOW_RULES_ONLY_PUBLISH")


def _reusable(cached: TodayCheckResult | None, *, rules_only: bool) -> bool:
    """May a stored row stand in for a fresh check?

    A reject is deterministic evidence and always stands. A pass stands only if
    a real checker gave it. An `incomplete` row is a failure record, never a
    result; a `pass` by checker `skip` is the legacy false pass; and a
    rules-only pass does not stand in for a model check, so it is honoured only
    while rules-only mode is still what was asked for.
    """
    if cached is None:
        return False
    if cached.verdict == "reject":
        return True
    if cached.verdict != "pass":
        return False
    if cached.checker == "skip":
        return False
    if cached.checker == "rules":
        return rules_only
    return True


def check_one(
    db: Any,
    card: TodayCard,
    *,
    checker: TodayChecker | None,
    rules_only: bool = False,
    unavailable: str = "Hermes unavailable",
    source_verifier: Any = None,
) -> TodayCheckResult:
    """Rules first; a checker on whatever rules cannot prove is wrong.

    Either reject wins. Hermes cannot override a rules reject — those are
    the leftover holes (IPO copy, Oxford-as-Yorkshire) that must not depend
    on a model being up.

    A check that does not finish is recorded `incomplete`, never `pass`: not
    when there is no checker, not when it times out, not when it answers
    something unparseable. Incomplete rows are not reused as cache hits.
    """
    cached = cached_check(db, card)
    rules = rules_precheck(card)
    if rules is not None and rules.verdict == "reject":
        record_check(db, card, rules)
        return rules

    # A completed veto remains a veto even if the evidence site is temporarily
    # unavailable. Only approvals need refreshed reachability proof.
    if cached is not None and cached.verdict == "reject":
        return cached

    # Network validation belongs here, after scoring and before approval.
    # Injected offline checkers may also inject a verifier; the real Hermes
    # path always supplies one. An earlier model pass cannot bypass this GET.
    if source_verifier is not None:
        outcome = source_verifier(db, card.source_url)
        if outcome.state != "reachable":
            definitive = outcome.state in {"dead", "invalid"}
            result = TodayCheckResult(
                verdict="reject" if definitive else "incomplete",
                reason=("source_dead" if outcome.state == "dead" else "invalid_source")
                       if definitive else None,
                checker="source",
                summary=f"Source link: {outcome.reason}. Company held back until usable evidence is checked.",
            )
            record_check(db, card, result)
            return result

    if _reusable(cached, rules_only=rules_only):
        record_check(db, card, cached)
        return cached  # type: ignore[return-value]

    if checker is None:
        if rules_only:
            result = TodayCheckResult(
                verdict="pass", checker="rules",
                summary="Rules-only mode: no obvious veto; no model check was run.",
            )
        else:
            result = TodayCheckResult(
                verdict="incomplete", checker="skip",
                summary=f"{unavailable}; card not checked.",
            )
        record_check(db, card, result)
        return result

    name = getattr(checker, "name", "hermes")
    try:
        result = checker.review(card)
    except TodayQaError as exc:
        log.warning("today QA subagent failed for %s: %s", card.name, exc)
        result = TodayCheckResult(
            verdict="incomplete", checker=name, summary=f"check failed: {exc}"[:240],
        )
        record_check(db, card, result)
        return result

    if result.verdict not in {"pass", "reject"}:
        result = TodayCheckResult(
            verdict="incomplete", checker=name,
            summary=f"unusable verdict {result.verdict!r}",
        )
    record_check(db, card, result)
    return result


def _record_incomplete_quietly(db: Any, card: TodayCard, exc: Exception) -> None:
    """Best effort: leave a withheld marker even when the check itself blew up."""
    try:
        record_check(db, card, TodayCheckResult(
            verdict="incomplete", checker="skip",
            summary=f"{type(exc).__name__}: {exc}"[:240],
        ))
    except Exception:  # noqa: BLE001 - the run must not die on its own bookkeeping
        log.warning("could not record incomplete marker for %s", card.name)


def run_today_qa(
    db: Any,
    cfg: Any = None,
    *,
    checker: TodayChecker | None = None,
    use_hermes: bool = True,
    limit: int = QA_LIMIT,
    source_verifier: Any = None,
) -> TodayQaReport:
    """Check the companies selected for Today. Never raises into the run.

    Every card it considers leaves a row: pass, reject, or `incomplete`. What
    it could not do is on the report (`incomplete`, `aborted`, `warnings`) so
    the caller can be loud about it; `hermes_used` is true only when Hermes was
    the checker and finished every card.
    """
    report = TodayQaReport()
    try:
        cfg = _config_for(db, cfg)
        candidates = load_today_cards(db, cfg, limit=10_000)
    except Exception as exc:  # noqa: BLE001 - one stage, not the run
        report.aborted = f"{type(exc).__name__}: {exc}"
        report.warnings.append(f"today QA skipped: {report.aborted}")
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

    rules_only = active is None and rules_only_mode(use_hermes=use_hermes)
    is_hermes = bool(active is not None and getattr(active, "name", "") == "hermes")
    if source_verifier is None and (checker is None or isinstance(active, HermesSubagent)):
        from radar.qa.provenance import verify_source
        source_verifier = verify_source
    # Completed current approvals remain in the report, but never spend the
    # budget intended for unfinished checks. check_one still visits them so
    # provenance validation can veto an expired or newly dead source link.
    completed, unfinished = [], []
    for card in candidates:
        cached = cached_check(db, card)
        target = completed if (cached and cached.verdict == "pass"
                               and _reusable(cached, rules_only=rules_only)) else unfinished
        target.append(card)
    cards = completed + unfinished[:max(1, int(limit))]
    report.uncovered = max(0, len(unfinished) - max(1, int(limit)))
    report.cards = len(cards)
    report.rules_only = rules_only
    if cards and active is None:
        report.warnings.append(
            "today QA: rules only by request — no model check"
            if rules_only else
            "today QA: Hermes not available — cards left unchecked (incomplete)"
        )

    consecutive = 0
    abandoned = False
    for card in cards:
        live = None if abandoned else active
        hit = _reusable(cached_check(db, card), rules_only=rules_only)
        try:
            result = check_one(
                db, card, checker=live, rules_only=rules_only,
                unavailable=("Hermes abandoned after repeated failures"
                             if abandoned else "Hermes unavailable"),
                source_verifier=source_verifier,
            )
        except Exception as exc:  # noqa: BLE001 - one company, not the run
            report.skipped += 1
            report.incomplete += 1
            report.warnings.append(f"{card.name}: {type(exc).__name__}: {exc}")
            log.warning("today QA skipped %s: %s", card.name, exc)
            _record_incomplete_quietly(db, card, exc)
            continue

        if result.verdict == "incomplete":
            report.incomplete += 1
            if live is not None and result.checker != "source":
                report.hermes_failures += 1
                consecutive += 1
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    abandoned = True
                    report.warnings.append(
                        f"today QA: {consecutive} checks in a row failed — "
                        "abandoning Hermes; remaining cards left incomplete"
                    )
            continue

        report.checked += 1
        if hit:
            report.cached += 1
        elif live is not None and result.checker != "rules":
            consecutive = 0
        if result.verdict == "reject":
            report.rejected += 1
            log.info(
                "today QA rejected %s (%s): %s",
                card.name, result.reason, result.summary,
            )
        else:
            report.passed += 1

    report.incomplete += report.uncovered
    report.hermes_used = bool(is_hermes and report.incomplete == 0)
    if report.incomplete:
        report.warnings.append(
            f"today QA: {report.incomplete} of {report.cards} cards have no "
            "completed check — withheld from Today, the Sheet and the ping "
            "until one completes"
        )
    if report.uncovered:
        report.warnings.append(
            f"today QA: {report.uncovered} further candidate cards rank below "
            f"the QA limit ({limit}) and were not checked"
        )
    if report.rejected:
        report.warnings.append(
            f"today QA dropped {report.rejected} of {report.checked} selected companies"
        )
    return report


__all__ = [
    "HermesSubagent",
    "HermesUnavailable",
    "InvalidVerdict",
    "MAX_CONSECUTIVE_FAILURES",
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
    "is_withheld",
    "latest_today_verdict",
    "load_today_cards",
    "parse_verdict",
    "qa_state",
    "record_check",
    "resolve_hermes_binary",
    "rules_only_mode",
    "rules_precheck",
    "run_today_qa",
    "subagent_prompt",
    "withheld_company_ids",
]
