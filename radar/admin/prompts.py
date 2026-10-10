"""The three AI prompts, editable from the Control room without a redeploy.

The defaults stay where they always were — `radar/extract/llm.py` for article
extraction and the Hermes skill references for Today QA and the publish
check. An edit is a row in `prompt_override`; at most one row per key is
active, and every earlier row is kept as history so a bad edit is one click
from undone.

Two rules keep an edit honest:

* **An edit changes the prompt version.** The version is part of the LLM
  cache key (extraction) and of the Today QA `snapshot_hash`, so the next run
  re-reads articles and re-checks Today cards under the new text instead of
  silently replaying answers the old prompt gave. Defaults keep their
  original version string exactly, so recorded fixtures still replay.
* **Resolving a prompt never fails a run.** A missing table or a locked
  database means the default prompt, not a stopped morning.

None of this lets AI near a score: these prompts read prose (③) and veto
cards after scoring (⑥½). Stage ⑥ has no prompt to edit.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ registry


def _extract_default() -> str:
    from radar.extract.llm import SYSTEM_PROMPT

    return SYSTEM_PROMPT


def _extract_version() -> str:
    from radar.extract.llm import PROMPT_VERSION

    return PROMPT_VERSION


def _today_default() -> str:
    from radar.qa.today import subagent_prompt

    return subagent_prompt()


def _today_version() -> str:
    from radar.qa.today import PROMPT_VERSION

    return PROMPT_VERSION


def _publish_default() -> str:
    from radar.qa.publish import _subagent_prompt

    return _subagent_prompt()


def _publish_version() -> str:
    from radar.qa.publish import PROMPT_VERSION

    return PROMPT_VERSION


@dataclass(frozen=True)
class PromptSpec:
    key: str
    label: str
    stage: str
    description: str
    effect: str
    default_text: Callable[[], str]
    base_version: Callable[[], str]


SPECS: dict[str, PromptSpec] = {
    spec.key: spec
    for spec in (
        PromptSpec(
            key="extract.system",
            label="Article extraction",
            stage="extract",
            description=(
                "System prompt for stage ③: turns one news article into one "
                "structured company record with verbatim quotes. It never "
                "scores or ranks."
            ),
            effect=(
                "New articles are read with this prompt from the next run. "
                "Articles already read keep their stored record until they "
                "are fetched again; cached answers from the old prompt are "
                "not reused."
            ),
            default_text=_extract_default,
            base_version=_extract_version,
        ),
        PromptSpec(
            key="today_qa.brief",
            label="Today QA check",
            stage="today_qa",
            description=(
                "Brief for the Hermes subagent at stage ⑥½: decides only "
                "whether a scored company is the WRONG company for Today "
                "(already backed, late stage, not a startup…). Veto only."
            ),
            effect=(
                "Every Today card needs a fresh check under the new prompt. "
                "Run `founder-radar today-qa` (or wait for the morning run); "
                "until then cards show as not yet checked."
            ),
            default_text=_today_default,
            base_version=_today_version,
        ),
        PromptSpec(
            key="publish.brief",
            label="Publish check",
            stage="render",
            description=(
                "Brief for the Hermes publish gate before stage ⑦: decides "
                "whether this morning's results are safe to send (hash drift, "
                "poisoned fund criteria, vanished shortlist)."
            ),
            effect="Used from the next publish check onwards.",
            default_text=_publish_default,
            base_version=_publish_version,
        ),
    )
}


class UnknownPrompt(KeyError):
    """The key is not one of `SPECS`."""


def spec(key: str) -> PromptSpec:
    try:
        return SPECS[key]
    except KeyError:
        raise UnknownPrompt(key) from None


def override_version(base: str, body: str) -> str:
    """`<default version>+<10 hex>` — distinct per text, stable across processes."""
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:10]
    return f"{base}+{digest}"


# ------------------------------------------------------------------ storage


def _conn(handle: Any) -> sqlite3.Connection:
    """`radar.store.db.Db` or a bare `sqlite3.Connection` — both are callers."""
    return getattr(handle, "conn", handle)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _tx(conn: sqlite3.Connection) -> Iterator[None]:
    """One explicit transaction, whatever the connection's isolation level."""
    if conn.in_transaction:
        # A caller already holds one (the web server's implicit DML
        # transaction); finish it rather than nesting.
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _active_row(conn: sqlite3.Connection, key: str) -> sqlite3.Row | tuple | None:
    return conn.execute(
        "SELECT id, body, created_at FROM prompt_override "
        "WHERE prompt_key = ? AND active = 1 ORDER BY id DESC LIMIT 1",
        (key,),
    ).fetchone()


@dataclass(frozen=True)
class Effective:
    key: str
    text: str
    version: str
    source: str                 # default | override
    override_id: int | None = None
    updated_at: str | None = None


def effective(handle: Any, key: str) -> Effective:
    """The prompt a run would use right now. Never raises for a bad database."""
    s = spec(key)
    row = None
    if handle is not None:
        try:
            row = _active_row(_conn(handle), key)
        except sqlite3.Error as exc:
            log.debug("prompt override for %s unavailable: %s", key, exc)
            row = None
    if row is None:
        return Effective(key, s.default_text(), s.base_version(), "default")
    override_id, body, created_at = row[0], row[1], row[2]
    return Effective(key, body, override_version(s.base_version(), body),
                     "override", int(override_id), created_at)


def history(handle: Any, key: str, limit: int = 50) -> list[dict[str, Any]]:
    spec(key)
    try:
        rows = _conn(handle).execute(
            "SELECT id, body, note, created_at, active FROM prompt_override "
            "WHERE prompt_key = ? ORDER BY id DESC LIMIT ?",
            (key, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {"id": r[0], "created_at": r[3], "note": r[2] or "",
         "active": bool(r[4]), "length": len(r[1] or "")}
        for r in rows
    ]


def _log_change(conn: sqlite3.Connection, key: str, old: str, new: str,
                note: str | None) -> None:
    conn.execute(
        "INSERT INTO admin_change(at, kind, key, old_value, new_value, note, sheet_sync) "
        "VALUES (?, 'prompt', ?, ?, ?, ?, 'n/a')",
        (_now(), key, old, new, (note or "").strip() or None),
    )


def save_override(handle: Any, key: str, text: str, note: str | None = None) -> Effective:
    """Make `text` the active prompt for `key`.

    Text identical to the default is a reset, not an override: it would
    otherwise mint a new version and force a full re-check for no change.
    """
    s = spec(key)
    body = (text or "").replace("\r\n", "\n")
    if not body.strip():
        raise ValueError("prompt text is empty")
    if body.strip() == s.default_text().strip():
        return reset(handle, key, note)
    conn = _conn(handle)
    before = effective(conn, key)
    if before.source == "override" and before.text == body:
        return before
    new_version = override_version(s.base_version(), body)
    with _tx(conn):
        conn.execute(
            "UPDATE prompt_override SET active = 0 WHERE prompt_key = ? AND active = 1",
            (key,))
        conn.execute(
            "INSERT INTO prompt_override(prompt_key, body, note, created_at, active) "
            "VALUES (?, ?, ?, ?, 1)",
            (key, body, (note or "").strip() or None, _now()),
        )
        _log_change(conn, key, before.version, new_version, note)
    return effective(conn, key)


def reset(handle: Any, key: str, note: str | None = None) -> Effective:
    """Back to the shipped default. History rows are kept."""
    spec(key)
    conn = _conn(handle)
    before = effective(conn, key)
    if before.source == "default":
        return before
    with _tx(conn):
        conn.execute(
            "UPDATE prompt_override SET active = 0 WHERE prompt_key = ? AND active = 1",
            (key,))
        _log_change(conn, key, before.version, spec(key).base_version(), note or "reset")
    return effective(conn, key)


# ------------------------------------------------------------------- views


def list_view(handle: Any) -> list[dict[str, Any]]:
    out = []
    for key, s in SPECS.items():
        eff = effective(handle, key)
        out.append({
            "key": key, "label": s.label, "stage": s.stage,
            "description": s.description, "source": eff.source,
            "version": eff.version, "length": len(eff.text),
            "updated_at": eff.updated_at,
        })
    return out


def detail_view(handle: Any, key: str) -> dict[str, Any]:
    s = spec(key)
    eff = effective(handle, key)
    return {
        "key": key, "label": s.label, "stage": s.stage,
        "description": s.description, "effect": s.effect,
        "text": eff.text, "default_text": s.default_text(),
        "source": eff.source, "version": eff.version,
        "history": history(handle, key),
    }


SAMPLE_ARTICLE_TITLE = "Newcastle robotics startup Gridline raises £1.2m seed round"
SAMPLE_ARTICLE_TEXT = (
    "Gridline, a Newcastle-based startup building autonomous inspection robots "
    "for electricity substations, has raised £1.2m in a seed round led by "
    "Northern Angels. The company was founded in 2025 by Priya Shah and Tom "
    "Reid, who met at Newcastle University. Gridline will use the funding to "
    "hire six engineers and start pilots with two network operators."
)


def preview(handle: Any, key: str) -> dict[str, Any]:
    """What the model would actually receive — no network, no AI call."""
    eff = effective(handle, key)
    if key == "extract.system":
        from radar.extract.llm import build_user_prompt

        user = build_user_prompt(SAMPLE_ARTICLE_TITLE, SAMPLE_ARTICLE_TEXT,
                                 url="https://example.org/gridline-seed")
        return {
            "key": key,
            "sample": "A made-up seed-round article (Gridline, Newcastle).",
            "preview": f"=== SYSTEM ===\n{eff.text}\n\n=== USER ===\n{user}",
        }
    if key == "today_qa.brief":
        from dataclasses import replace

        from radar.qa.today import TodayCard, build_user_prompt

        card, sample = None, "A made-up card (no Today cards in the database)."
        try:
            from radar.qa.today import load_today_cards

            cards = load_today_cards(_DbView(_conn(handle)), limit=1)
            if cards:
                card = cards[0]
                sample = f"The first card Today would check: {card.name}."
        except Exception as exc:  # noqa: BLE001 - a preview, never an error page
            log.debug("today preview fell back to a sample card: %s", exc)
        if card is None:
            card = TodayCard(company_id="sample", name="Gridline Ltd",
                             city="Newcastle", region="north_east", stage="seed",
                             one_liner="Autonomous inspection robots for substations")
        card = replace(card, brief=eff.text, prompt_version=eff.version)
        return {"key": key, "sample": sample, "preview": build_user_prompt(card)}
    if key == "publish.brief":
        payload = {"prompt_version": eff.version, "blocking_issues": [],
                   "heals": [], "diagnosis": {"active_shortlist": 6}}
        body = (f"{eff.text}\n\n---\nPUBLISH SNAPSHOT (JSON, counts only):\n"
                f"{json.dumps(payload, sort_keys=True, indent=2)}\n")
        return {"key": key, "sample": "A healthy morning with six shortlisted.",
                "preview": body}
    raise UnknownPrompt(key)


class _DbView:
    """The read verbs of `radar.store.db.Db` over a bare connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def query(self, sql: str, params: Any = ()) -> list[Any]:
        return list(self.conn.execute(sql, params).fetchall())

    def one(self, sql: str, params: Any = ()) -> Any:
        return self.conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Any = ()) -> Any:
        row = self.one(sql, params)
        return row[0] if row is not None else None


__all__ = [
    "Effective", "PromptSpec", "SPECS", "UnknownPrompt", "detail_view",
    "effective", "history", "list_view", "override_version", "preview",
    "reset", "save_override", "spec",
]
