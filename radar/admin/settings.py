"""Settings and Sources edits from the Control room.

The Google Sheet stays the editable brain. An edit here is the same edit as
typing in the Sheet, made safely:

1. **Validate** with the exact rules stage ① uses (`parse_settings`), so a
   value the morning run would reject is refused here instead.
2. **Write the Sheet first**, when one is configured. If that write fails,
   nothing else changes and the caller is told so — otherwise tomorrow's run
   would read the old cell and silently undo the edit.
3. **Save the last-good snapshot**, which is what this surface, Today and a
   sheet-less install read.
4. **Rescore at once.** The snapshot's `config_hash` is the score generation
   Today reads; without a rescore Today would be empty until the next run
   (the hash-drift failure `founder-radar doctor` warns about). Rescoring is
   stage ⑥: deterministic, no network, no AI.

A rescore makes every Today card's Hermes check stale (`qa_state` treats a
newer score as unchecked), so the answer says so and Today stays withheld
until `founder-radar today-qa` or the morning run checks the cards again.
That is the same rule a `founder-radar rescore` has always followed.

Every applied change is a row in `admin_change`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from radar.config.loader import (
    parse_settings,
    parse_snapshot,
    save_snapshot,
    sheet_credentials_present,
)
from radar.config.models import STAGES, Config
from radar.store.db import now_iso

AUTO = object()          # "open the real Sheet if credentials are present"

SETTINGS_TAB = "Settings"
SOURCES_TAB = "Sources"


class SettingsInvalid(ValueError):
    def __init__(self, errors: Mapping[str, str]) -> None:
        super().__init__("invalid settings")
        self.errors = dict(errors)


class SheetUnavailable(RuntimeError):
    """The Sheet is configured but could not be written. Nothing changed."""


class UnknownSource(KeyError):
    pass


# ------------------------------------------------------------------- reads


def current_config(db: Any) -> tuple[Config, str]:
    """(config, 'snapshot' | 'defaults') — the same source Today reads."""
    from radar.config.defaults import default_config

    row = db.one(
        "SELECT config_json FROM config_snapshot WHERE is_last_good = 1 "
        "ORDER BY created_at DESC LIMIT 1"
    )
    if row is not None:
        cfg = parse_snapshot(row["config_json"])
        if cfg is not None:
            return cfg, "snapshot"
    return default_config(), "defaults"


def _display(value: Any) -> str:
    from radar.render.sheet import _txt

    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return _txt(value)


def _stage_of() -> dict[str, str]:
    from radar.admin.flow import STAGES as FLOW_STAGES

    out: dict[str, str] = {}
    for stage in FLOW_STAGES:
        for key in stage.settings:
            out.setdefault(key, stage.id)
    return out


def _choices(key: str) -> list[str] | None:
    if key == "max_stage":
        return list(STAGES)
    if key == "llm_enabled":
        return ["TRUE", "FALSE"]
    return None


def settings_view(db: Any) -> dict[str, Any]:
    from radar.config.models import Settings
    from radar.render.sheet import SETTING_SPECS

    cfg, source = current_config(db)
    defaults = Settings()
    stages = _stage_of()
    return {
        "config_hash": cfg.hash(),
        "config_source": source,
        "sheet_configured": sheet_credentials_present(),
        "settings": [
            {
                "key": key,
                "value": _display(getattr(cfg.settings, key, None)),
                "default": _display(getattr(defaults, key, None)),
                "type": type_hint,
                "description": description,
                "stage": stages.get(key),
                "choices": _choices(key),
            }
            for key, type_hint, description in SETTING_SPECS
        ],
        "sources": [
            {"key": s.key, "track": s.track, "enabled": s.enabled, "note": s.note}
            for s in cfg.sources
        ],
    }


def changes_view(db: Any, limit: int = 100) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT id, at, kind, key, old_value, new_value, note, sheet_sync "
        "FROM admin_change ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ writes


def _gateway(gateway: Any) -> Any:
    if gateway is not AUTO:
        return gateway
    if not sheet_credentials_present():
        return None
    from radar.render.sheet import open_gateway

    try:
        return open_gateway()
    except Exception as exc:  # noqa: BLE001 - configured but unreachable
        raise SheetUnavailable(f"{type(exc).__name__}: {exc}") from exc


def _quote(tab: str) -> str:
    return f"'{tab}'"


def _write_settings_cells(gw: Any, values: Mapping[str, str]) -> None:
    from radar.render.sheet import SETTING_SPECS, ValueRange

    rng = f"{_quote(SETTINGS_TAB)}!A1:A200"
    grid = gw.batch_get([rng]).get(rng, [])
    rows = {(_cell(r, 0)): i for i, r in enumerate(grid, start=1) if _cell(r, 0)}
    hints = {k: (t, d) for k, t, d in SETTING_SPECS}
    next_row = max(len(grid), 1) + 1
    data = []
    for key, raw in values.items():
        if key in rows:
            data.append(ValueRange(f"{_quote(SETTINGS_TAB)}!B{rows[key]}:B{rows[key]}", [[raw]]))
        else:
            type_hint, description = hints.get(key, ("", ""))
            data.append(ValueRange(f"{_quote(SETTINGS_TAB)}!A{next_row}:E{next_row}",
                                   [[key, raw, type_hint, "", description]]))
            next_row += 1
    gw.batch_set(data, "USER_ENTERED")


def _cell(row: Any, index: int) -> str:
    try:
        return str(row[index]).strip()
    except IndexError:
        return ""


def _source_slug(name: str) -> str:
    # The same slug `parse_sources` derives from the Sources tab's column A.
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def _write_source_cell(gw: Any, key: str, enabled: bool) -> bool:
    """Set Sources!C for `key`. False when the tab has no row for it."""
    from radar.render.sheet import ValueRange

    rng = f"{_quote(SOURCES_TAB)}!A1:A200"
    grid = gw.batch_get([rng]).get(rng, [])
    for i, row in enumerate(grid, start=1):
        if i > 1 and _source_slug(_cell(row, 0)) == key:
            gw.batch_set([ValueRange(f"{_quote(SOURCES_TAB)}!C{i}:C{i}",
                                     [["TRUE" if enabled else "FALSE"]])],
                         "USER_ENTERED")
            return True
    return False


@dataclass
class Applied:
    applied: dict[str, str]
    config_hash: str
    sheet_sync: str
    rescore: dict[str, Any]
    today_qa_needed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "applied": self.applied,
            "config_hash": self.config_hash,
            "sheet_sync": self.sheet_sync,
            "rescore": self.rescore,
            "today_qa_needed": self.today_qa_needed,
        }


def _commit(db: Any, new_cfg: Config, *, kind: str, rows: list[tuple[str, str, str]],
            note: str | None, sheet_sync: str) -> tuple[str, dict[str, Any], bool]:
    """Snapshot, rescore, audit. Returns (config_hash, rescore summary, qa needed)."""
    from radar.pipeline import rescore_all

    digest = save_snapshot(db, new_cfg, is_last_good=True)
    # Rescore with the config *as Today will parse it back*, so the score
    # generation and Today's active hash are the same string.
    stored = db.one("SELECT config_json FROM config_snapshot WHERE config_hash = ?",
                    (digest,))
    cfg = parse_snapshot(stored["config_json"]) if stored else None
    cfg = cfg or new_cfg
    summary = rescore_all(db, cfg)
    qa_needed = bool(db.scalar("SELECT COUNT(*) FROM today_check"))
    stamp = now_iso()
    clean_note = (note or "").strip() or None
    db.executemany(
        "INSERT INTO admin_change(at, kind, key, old_value, new_value, note, sheet_sync) "
        "VALUES (?,?,?,?,?,?,?)",
        [(stamp, kind, key, old, new, clean_note, sheet_sync) for key, old, new in rows],
    )
    return cfg.hash(), {k: summary.get(k) for k in ("scored", "shortlisted")
                        if k in summary} | {"config_hash": cfg.hash()}, qa_needed


def apply_settings(db: Any, changes: Mapping[str, Any], *, note: str | None = None,
                   gateway: Any = AUTO) -> Applied:
    """Validate, write the Sheet, snapshot, rescore. All or nothing."""
    from radar.render.sheet import SETTING_SPECS

    known = {k: t for k, t, _ in SETTING_SPECS}
    errors: dict[str, str] = {}
    raw: dict[str, str] = {}
    for key, value in (changes or {}).items():
        if key not in known:
            errors[key] = "is not a setting"
            continue
        text = "" if value is None else str(value).strip()
        if text == "":
            errors[key] = "cannot be blank here — type the value you want"
            continue
        raw[key] = text
    if not raw and not errors:
        raise SettingsInvalid({"": "no changes"})

    cfg, _ = current_config(db)
    grid = [["Key", "Value", "Type", "Status"]]
    grid += [[key, text, known[key]] for key, text in raw.items()]
    values, parse_errors, _cells = parse_settings(grid, last_good=cfg, lists=cfg.lists)
    # The Sheet's wording ends "— using last good value X"; here nothing is
    # applied, so keep only the problem.
    errors.update({k: re.sub(r"\s+—\s+using .*$", "", v.lstrip("❌ ")).strip()
                   for k, v in parse_errors.items()})
    if errors:
        raise SettingsInvalid(errors)

    merged = cfg.settings.model_dump() | values
    try:
        new_settings = type(cfg.settings)(**merged)
    except ValueError as exc:
        raise SettingsInvalid({"": str(exc)}) from exc
    if new_settings.weight_fit + new_settings.weight_edge <= 0:
        raise SettingsInvalid({"weight_fit": "weight_fit and weight_edge cannot both be 0"})

    rows = [(key, _display(getattr(cfg.settings, key, None)),
             _display(getattr(new_settings, key, None))) for key in raw]
    rows = [r for r in rows if r[1] != r[2]]
    if not rows:
        return Applied({}, cfg.hash(), "skipped", {}, False)

    gw = _gateway(gateway)
    sheet_sync = "skipped"
    if gw is not None:
        try:
            _write_settings_cells(gw, {key: raw[key] for key, _, _ in rows})
        except Exception as exc:  # noqa: BLE001 - reported; nothing else changed
            raise SheetUnavailable(f"{type(exc).__name__}: {exc}") from exc
        sheet_sync = "synced"

    new_cfg = cfg.model_copy(update={"settings": new_settings}, deep=True)
    digest, summary, qa_needed = _commit(db, new_cfg, kind="setting", rows=rows,
                                         note=note, sheet_sync=sheet_sync)
    return Applied({key: new for key, _, new in rows}, digest, sheet_sync,
                   summary, qa_needed)


def apply_source(db: Any, key: str, enabled: bool, *, note: str | None = None,
                 gateway: Any = AUTO) -> Applied:
    """Switch one source on or off: the Sources tab's `Enabled` column."""
    cfg, _ = current_config(db)
    sources = list(cfg.sources)
    index = next((i for i, s in enumerate(sources) if s.key == key), None)
    if index is None:
        raise UnknownSource(key)
    before = sources[index]
    if before.enabled == bool(enabled):
        return Applied({}, cfg.hash(), "skipped", {}, False)

    gw = _gateway(gateway)
    sheet_sync = "skipped"
    if gw is not None:
        try:
            found = _write_source_cell(gw, key, bool(enabled))
        except Exception as exc:  # noqa: BLE001
            raise SheetUnavailable(f"{type(exc).__name__}: {exc}") from exc
        # A source missing from the tab is a code default the sheet has not
        # listed yet; the snapshot carries the switch until it is.
        sheet_sync = "synced" if found else "not_in_sheet"

    sources[index] = before.model_copy(update={"enabled": bool(enabled)})
    new_cfg = cfg.model_copy(update={"sources": sources}, deep=True)
    word = "on" if enabled else "off"
    digest, summary, qa_needed = _commit(
        db, new_cfg, kind="source",
        rows=[(key, "on" if before.enabled else "off", word)],
        note=note, sheet_sync=sheet_sync)
    return Applied({key: word}, digest, sheet_sync, summary, qa_needed)


def rescore_now(db: Any, *, note: str | None = None) -> dict[str, Any]:
    """`founder-radar rescore --all` against the current snapshot, logged."""
    from radar.pipeline import rescore_all

    cfg, _ = current_config(db)
    summary = rescore_all(db, cfg)
    db.execute(
        "INSERT INTO admin_change(at, kind, key, old_value, new_value, note, sheet_sync) "
        "VALUES (?, 'rescore', NULL, NULL, ?, ?, 'n/a')",
        (now_iso(), cfg.hash(), (note or "").strip() or None),
    )
    return {
        "scored": summary.get("scored"),
        "shortlisted": summary.get("shortlisted"),
        "config_hash": cfg.hash(),
        "today_qa_needed": bool(db.scalar("SELECT COUNT(*) FROM today_check")),
    }


__all__ = [
    "AUTO", "Applied", "SettingsInvalid", "SheetUnavailable", "UnknownSource",
    "apply_settings", "apply_source", "changes_view", "current_config",
    "rescore_now", "settings_view",
]
