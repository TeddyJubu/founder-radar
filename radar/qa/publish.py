"""Pre-publish gate — Hermes + deterministic checks before anything goes out.

Founder Radar is the tool. Hermes is the operator. Nothing is published
(Telegram digest) until this gate passes.

The dumb failure this exists to kill: a morning run writes shortlist scores
under hash A, a volatile Sources note flips last-good to hash B, Today and
the digest then ship against an empty generation. The gate must catch that
*before* `digest --send`, auto-heal when it can, and refuse when it cannot.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PROMPT_VERSION = "publish-check-2026-08-28.1"
HERMES_TIMEOUT_S = 90

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROMPT_PATH = (
    _REPO_ROOT / "hermes" / "skills" / "founder-radar"
    / "references" / "publish-check.md"
)

FALLBACK_PROMPT = (
    "You are the Founder Radar publish gate. Founder Radar is a tool; you "
    "decide whether this morning's results are safe to publish to Aryan "
    "(Telegram digest / Today). You do not invent companies or scores.\n\n"
    "BLOCK if any of: active config_hash has 0 scores while older hashes "
    "have scores (hash drift); Fund Criteria poisoned; last run shortlisted "
    "> 0 but active shortlist is 0.\n"
    "PASS if the generation is healthy. Empty shortlist on a quiet day is "
    "PASS (zero-day digest is correct).\n\n"
    "Return exactly:\n"
    "VERDICT: PASS|BLOCK\n"
    "SUMMARY: <one sentence>\n"
    "ACTIONS: none|rescore|repair-fund-criteria|doctor\n"
)


@dataclass
class PublishIssue:
    code: str
    detail: str
    healable: bool = False


@dataclass
class PublishReport:
    ok: bool = False
    hermes_verdict: str | None = None  # pass | block | skip
    hermes_summary: str = ""
    hermes_actions: str = "none"
    issues: list[PublishIssue] = field(default_factory=list)
    heals: list[str] = field(default_factory=list)
    diagnosis: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "hermes_verdict": self.hermes_verdict,
            "hermes_summary": self.hermes_summary,
            "hermes_actions": self.hermes_actions,
            "issues": [
                {"code": i.code, "detail": i.detail, "healable": i.healable}
                for i in self.issues
            ],
            "heals": list(self.heals),
            "diagnosis": self.diagnosis,
            "warnings": list(self.warnings),
        }


def _subagent_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return FALLBACK_PROMPT


def _parse_publish_verdict(text: str) -> tuple[str, str, str]:
    verdict_m = re.search(
        r"^\s*VERDICT:\s*(PASS|BLOCK)\s*$", text, re.I | re.M,
    )
    summary_m = re.search(
        r"^\s*SUMMARY:\s*(.+?)\s*$", text, re.I | re.M,
    )
    actions_m = re.search(
        r"^\s*ACTIONS:\s*(.+?)\s*$", text, re.I | re.M,
    )
    if not verdict_m:
        return "block", f"unparseable Hermes reply: {text[:160]}", "none"
    verdict = verdict_m.group(1).lower()
    summary = (summary_m.group(1).strip() if summary_m else "")
    actions = (actions_m.group(1).strip().lower() if actions_m else "none")
    return verdict, summary, actions



def _ensure_hermes_acl() -> None:
    """Re-assert radar→Hermes ACLs; chmod under ~/.hermes can clear the mask."""
    script = _REPO_ROOT / "deploy" / "hermes-acl.sh"
    if not script.is_file():
        return
    try:
        subprocess.run(
            [str(script)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("hermes-acl refresh failed: %s", exc)


def _run_hermes_publish_check(payload: dict[str, Any]) -> tuple[str, str, str]:
    """Return (verdict, summary, actions). verdict in {pass, block, skip}."""
    from radar.qa.today import HermesUnavailable, resolve_hermes_binary

    binary = resolve_hermes_binary()
    if not binary:
        raise HermesUnavailable("hermes binary not on PATH")
    _ensure_hermes_acl()

    brief = _subagent_prompt()
    body = (
        f"{brief}\n\n---\nPUBLISH SNAPSHOT (JSON, counts only):\n"
        f"{json.dumps(payload, sort_keys=True, indent=2)}\n"
    )
    env = {**os.environ, "TERM": "dumb", "HERMES_NONINTERACTIVE": "1"}
    owner_home = (os.environ.get("HERMES_HOME") or "").strip()
    if owner_home:
        hermes_dir = Path(owner_home) / ".hermes"
        env["HOME"] = owner_home
        if hermes_dir.is_dir():
            env["HERMES_HOME"] = str(hermes_dir)
        else:
            env.pop("HERMES_HOME", None)

    attempts: list[tuple[list[str], bool]] = [
        ([binary, "chat", "-Q", "--query-file", "-"], True),
        ([binary, "chat", "-Q", "-q", body], False),
        ([binary, "-z", body], False),
    ]
    last = "hermes returned nothing"
    try:
        for argv, use_stdin in attempts:
            try:
                completed = subprocess.run(  # noqa: S603
                    argv,
                    input=body if use_stdin else None,
                    capture_output=True,
                    text=True,
                    timeout=HERMES_TIMEOUT_S,
                    env=env,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                continue
            text = (completed.stdout or "").strip() or (completed.stderr or "").strip()
            if completed.returncode == 0 and text:
                return _parse_publish_verdict(text)
            last = f"exit {completed.returncode}: {text[:240]}"
        raise HermesUnavailable(last)
    finally:
        # Hermes often chmod 700 ~/.hermes which clears the ACL mask.
        _ensure_hermes_acl()


def _collect_issues(db: Any) -> tuple[list[PublishIssue], dict[str, Any]]:
    from radar.render.today_diagnose import diagnose_today

    diagnosis = diagnose_today(db)
    issues: list[PublishIssue] = []

    if diagnosis.get("poisoned_fund_criteria"):
        issues.append(PublishIssue(
            "poisoned_fund_criteria",
            "Fund Criteria last-good is poisoned (vehicle_key looks boolean)",
            healable=True,
        ))

    scored = int(diagnosis.get("scored_for_active_hash") or 0)
    other = int(diagnosis.get("scores_on_other_hashes") or 0)
    if scored == 0 and other > 0:
        issues.append(PublishIssue(
            "config_hash_drift",
            f"active config has 0 scores; {other} scores sit on older hashes",
            healable=True,
        ))

    last = diagnosis.get("last_run") or {}
    last_sl = int(last.get("shortlisted") or 0)
    tiers = diagnosis.get("tiers") or {}
    active_sl = int(tiers.get("shortlist") or 0)
    if last_sl > 0 and active_sl == 0 and scored == 0:
        issues.append(PublishIssue(
            "shortlist_vanished",
            f"last run shortlisted {last_sl} but active generation has 0 scores",
            healable=True,
        ))

    return issues, diagnosis


def _diagnosis_view(diagnosis: dict[str, Any]) -> dict[str, Any]:
    return {
        "config_hash": diagnosis.get("config_hash"),
        "scored_for_active_hash": diagnosis.get("scored_for_active_hash"),
        "scores_on_other_hashes": diagnosis.get("scores_on_other_hashes"),
        "tiers": diagnosis.get("tiers"),
        "poisoned_fund_criteria": diagnosis.get("poisoned_fund_criteria"),
        "last_run": diagnosis.get("last_run"),
        "likely_causes": diagnosis.get("likely_causes"),
        "reviewable": diagnosis.get("reviewable"),
    }


def _heal(db: Any, issues: list[PublishIssue], report: PublishReport) -> None:
    codes = {i.code for i in issues}

    if "poisoned_fund_criteria" in codes:
        try:
            from radar.config.defaults import default_config
            from radar.config.loader import canonicalize_config, save_snapshot

            cfg = canonicalize_config(default_config())
            digest = save_snapshot(db, cfg, is_last_good=True)
            report.heals.append(f"reseeded Fund Criteria → last-good {digest}")
        except Exception as exc:  # noqa: BLE001
            report.warnings.append(
                f"repair-fund-criteria failed: {type(exc).__name__}: {exc}"
            )

    if codes & {"config_hash_drift", "shortlist_vanished", "poisoned_fund_criteria"}:
        try:
            from radar.pipeline import run_rescore

            result = run_rescore(db, all_companies=True)
            report.heals.append(
                f"rescore --all → shortlisted {result.get('shortlisted', '?')} "
                f"on {result.get('config_hash', '?')}"
            )
        except Exception as exc:  # noqa: BLE001
            report.warnings.append(
                f"auto-rescore failed: {type(exc).__name__}: {exc}"
            )


def pre_publish_check(
    db: Any,
    *,
    use_hermes: bool = True,
    heal: bool = True,
) -> PublishReport:
    """Run deterministic checks, optional auto-heal, then Hermes publish check."""
    from radar.qa.today import HermesUnavailable

    report = PublishReport()
    issues, diagnosis = _collect_issues(db)
    report.issues = issues
    report.diagnosis = _diagnosis_view(diagnosis)

    if heal and any(i.healable for i in issues):
        _heal(db, issues, report)
        issues, diagnosis = _collect_issues(db)
        report.issues = issues
        report.diagnosis = _diagnosis_view(diagnosis)

    blocking = [
        i for i in issues
        if i.code in {
            "poisoned_fund_criteria",
            "config_hash_drift",
            "shortlist_vanished",
        }
    ]

    hermes_payload = {
        "prompt_version": PROMPT_VERSION,
        "blocking_issues": [
            {"code": i.code, "detail": i.detail} for i in blocking
        ],
        "heals": report.heals,
        "diagnosis": report.diagnosis,
    }

    if use_hermes:
        try:
            verdict, summary, actions = _run_hermes_publish_check(hermes_payload)
            report.hermes_verdict = verdict
            report.hermes_summary = summary
            report.hermes_actions = actions
        except HermesUnavailable as exc:
            report.hermes_verdict = "skip"
            report.warnings.append(f"Hermes publish-check skipped: {exc}")
            report.ok = not blocking
            if not report.ok:
                report.warnings.append(
                    "refusing publish: deterministic gate failed and Hermes unavailable"
                )
            return report
    else:
        report.hermes_verdict = "skip"
        report.warnings.append("Hermes publish-check disabled (--no-hermes)")

    if report.hermes_verdict == "block":
        report.ok = False
        return report

    report.ok = not blocking
    if report.hermes_verdict == "pass" and blocking:
        report.ok = False
        report.warnings.append(
            "Hermes PASS overridden: deterministic blocking issues remain"
        )
    return report


def format_publish_report(report: PublishReport) -> str:
    lines = ["📡 Founder Radar — publish gate", ""]
    lines.append(f"Result        {'PASS' if report.ok else 'BLOCK'}")
    if report.hermes_verdict:
        lines.append(
            f"Hermes        {report.hermes_verdict.upper()}"
            + (f" — {report.hermes_summary}" if report.hermes_summary else "")
        )
    if report.hermes_actions and report.hermes_actions != "none":
        lines.append(f"Actions       {report.hermes_actions}")
    d = report.diagnosis
    tiers = d.get("tiers") or {}
    lines.append(
        f"Active scores shortlist {tiers.get('shortlist', 0)} · "
        f"watchlist {tiers.get('watchlist', 0)} · reject {tiers.get('reject', 0)}"
    )
    if d.get("config_hash"):
        lines.append(f"config_hash   {str(d['config_hash'])[:12]}…")
    lines.append(
        f"scored rows   {d.get('scored_for_active_hash', 0)} on active · "
        f"{d.get('scores_on_other_hashes', 0)} on older"
    )
    if report.heals:
        lines.append("")
        lines.append("Auto-healed:")
        for heal in report.heals:
            lines.append(f"• {heal}")
    if report.issues:
        lines.append("")
        lines.append("Issues:")
        for issue in report.issues:
            tag = "healable" if issue.healable else "hard"
            lines.append(f"• [{tag}] {issue.code}: {issue.detail}")
    if report.warnings:
        lines.append("")
        lines.append("Warnings:")
        for warning in report.warnings:
            lines.append(f"• {warning}")
    return "\n".join(lines)


__all__ = [
    "PROMPT_VERSION",
    "PublishIssue",
    "PublishReport",
    "format_publish_report",
    "pre_publish_check",
]
