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


#: Plain-language help for every setting: what it does, then an example change.
#: Shown behind the ⓘ next to each row in the Control room.
SETTING_HELP: dict[str, str] = {
    "max_company_age_months": "Companies older than this are rejected. Example: lower it to 24 to see only companies under two years old.",
    "max_total_funding_gbp": "Companies that have already raised more than this (in £, foreign amounts converted) are rejected. Example: £1,500,000 keeps only the earliest rounds.",
    "max_stage": "The latest funding stage allowed through. Example: set it to seed to drop Series A companies.",
    "shortlist_fit": "How well a company must fit a fund (0–100) to be shortlisted. Example: raise it to 80 for fewer, stronger shortlist picks.",
    "shortlist_edge": "How fresh a company must be (0–100: young, little press, not yet funded) to be shortlisted. Example: lower it to 45 to shortlist better-known companies too.",
    "min_coverage": "How complete a company's record must be (0–1) before it is scored. Example: 0.4 lets in companies with more unknowns.",
    "watchlist_fit": "Fund fit (0–100) needed for the watchlist, below the shortlist. Example: raise it to 55 for a shorter, better watchlist.",
    "min_qualifiers": "How many signals (a grant, press, an accelerator, a share issue) a Companies House-only company needs before it is scored. Example: 2 keeps out more bare registrations.",
    "weight_fit": "How much fund fit counts in the ranking (0–1). It and weight_edge should add up to 1. Example: 0.7 ranks by fit more than freshness.",
    "weight_edge": "How much freshness counts in the ranking (0–1). It and weight_fit should add up to 1. Example: 0.5 gives freshness equal weight.",
    "regions_enabled": "Which UK regions the Companies House sweep covers, comma-separated. Example: add london to include London companies.",
    "ch_backfill_days": "How many days back the very first Companies House sweep reads. Example: 30 for a quicker first run.",
    "ch_daily_window_days": "How many recent days each daily Companies House sweep re-reads. Example: 5 for a lighter daily sweep.",
    "max_enrichment_requests_per_run": "Most Companies House look-ups (officers, filings) per run. Example: 200 for a faster, lighter run.",
    "daily_digest_max": "Most companies listed in the written daily digest. Example: 5 for a shorter digest.",
    "max_source_share": "Most of one fund's Today list a single source may fill after its first three cards; extra cards wait behind the rest. Example: 0.3 for a more varied list.",
    "max_registry_share": "The same limit for companies found only on Companies House, kept stricter. Example: 0 puts every Companies House-only card behind all others.",
    "llm_model": "A label for the model that reads articles; the provider itself is set on the server. Change it only to force every article to be read again.",
    "llm_enabled": "Off means articles are read by simple rules only, with no AI. Example: FALSE to pause AI use entirely.",
}


def _shown(key: str, type_hint: str, value: Any) -> str:
    """Money settings read as £3,000,000, not 3000000.0."""
    if type_hint == "money" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"£{value:,.0f}"
    return _display(value)


def source_quality(db: Any) -> dict[str, dict[str, int]]:
    """Per source: companies Aryan decided, and how many he kept.

    Kept is `worth contacting` or `unsure`; `not for me` is a reject. A
    company found by several sources counts for each of them.
    """
    out: dict[str, dict[str, int]] = {}
    for row in db.query(
        """SELECT cs.source_key, COUNT(DISTINCT u.company_id) AS decided,
                  COUNT(DISTINCT CASE WHEN lower(u.value) != 'not for me'
                                      THEN u.company_id END) AS kept
             FROM user_field u
             JOIN company_source cs ON cs.company_id = u.company_id
            WHERE u.field = 'verdict' AND TRIM(COALESCE(u.value, '')) != ''
            GROUP BY cs.source_key"""):
        out[row["source_key"]] = {"decided": int(row["decided"]), "kept": int(row["kept"])}
    return out


def settings_view(db: Any) -> dict[str, Any]:
    from radar.config.models import Settings
    from radar.render.sheet import SETTING_SPECS

    from radar.config.defaults import DEFAULT_SOURCES

    cfg, source = current_config(db)
    defaults = Settings()
    stages = _stage_of()
    builtin = {s.key for s in DEFAULT_SOURCES}
    quality = source_quality(db)
    return {
        "config_hash": cfg.hash(),
        "config_source": source,
        "sheet_configured": sheet_credentials_present(),
        "settings": [
            {
                "key": key,
                "value": _shown(key, type_hint, getattr(cfg.settings, key, None)),
                "default": _shown(key, type_hint, getattr(defaults, key, None)),
                "type": type_hint,
                "description": description,
                "help": SETTING_HELP.get(key, ""),
                "stage": stages.get(key),
                "choices": _choices(key),
            }
            for key, type_hint, description in SETTING_SPECS
        ],
        "sources": [
            {"key": s.key, "track": s.track, "enabled": s.enabled, "note": s.note,
             "url": s.url, "funds": list(s.funds), "removable": s.key not in builtin,
             **quality.get(s.key, {"decided": 0, "kept": 0})}
            for s in cfg.sources
        ],
        "fund_keys": list(FUND_KEYS),
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


class SourceInvalid(ValueError):
    pass


FUND_KEYS = ("dsw", "northstar", "outward", "anticus")


def _clean_funds(funds: Any) -> list[str]:
    raw = funds if isinstance(funds, (list, tuple)) else re.split(r"[,\s]+", str(funds or ""))
    out = []
    for f in raw:
        f = str(f).strip().lower()
        if not f:
            continue
        if f not in FUND_KEYS:
            raise SourceInvalid(f"unknown fund {f!r} — use {', '.join(FUND_KEYS)}")
        if f not in out:
            out.append(f)
    return out


def _write_source_row(gw: Any, key: str, cells: Mapping[str, str] | None) -> bool:
    """Write columns of `key`'s Sources row; `cells=None` blanks the row.

    A missing row is appended when `cells` names column A. Returns False only
    when there is no row to change and none was added.
    """
    from radar.render.sheet import ValueRange

    tab = _quote(SOURCES_TAB)
    rng = f"{tab}!A1:A200"
    grid = gw.batch_get([rng]).get(rng, [])
    row_no = next((i for i, row in enumerate(grid, start=1)
                   if i > 1 and _source_slug(_cell(row, 0)) == key), None)
    if row_no is None:
        if not cells or "A" not in cells:
            return False
        row_no = max(len(grid) + 1, 2)
    if cells is None:
        gw.batch_set([ValueRange(f"{tab}!A{row_no}:J{row_no}", [[""] * 10])], "USER_ENTERED")
        return True
    gw.batch_set([ValueRange(f"{tab}!{col}{row_no}:{col}{row_no}", [[value]])
                  for col, value in cells.items()], "USER_ENTERED")
    return True


def _sheet_write(gateway: Any, fn) -> str:
    gw = _gateway(gateway)
    if gw is None:
        return "skipped"
    try:
        return "synced" if fn(gw) else "not_in_sheet"
    except Exception as exc:  # noqa: BLE001
        raise SheetUnavailable(f"{type(exc).__name__}: {exc}") from exc


def add_feed_source(db: Any, name: str, url: str, *, funds: Any = None,
                    note: str | None = None, gateway: Any = AUTO,
                    check: Any = None) -> Applied:
    """Add an RSS/Atom feed as a source (`feed_<name>`), on, feeding `funds`.

    The feed is fetched and parsed once first (`check`), so a URL that is not
    a feed is refused here rather than failing every morning.
    """
    from radar.sources.feed import PREFIX

    slug = _source_slug(name or "")
    if not slug:
        raise SourceInvalid("give the source a name")
    key = slug if slug.startswith(PREFIX) else PREFIX + slug
    url = (url or "").strip()
    if not re.match(r"^https://[^\s/]+\.[^\s]+$", url):
        raise SourceInvalid("the feed URL must start with https://")
    fund_list = _clean_funds(funds)
    cfg, _ = current_config(db)
    if any(s.key == key for s in cfg.sources):
        raise SourceInvalid(f"a source called {key} already exists")
    (check or _check_feed)(key, url)

    from radar.config.models import SourceConfig

    sheet_sync = _sheet_write(gateway, lambda gw: _write_source_row(gw, key, {
        "A": key, "B": "A", "C": "TRUE", "H": (note or "").strip(), "I": url,
        "J": ", ".join(fund_list)}))
    source = SourceConfig(key=key, track="A", enabled=True, note=(note or "").strip(),
                          url=url, funds=fund_list)
    new_cfg = cfg.model_copy(update={"sources": [*cfg.sources, source]}, deep=True)
    digest, summary, qa_needed = _commit(
        db, new_cfg, kind="source", rows=[(key, "", f"added {url}")],
        note=note, sheet_sync=sheet_sync)
    return Applied({key: "added"}, digest, sheet_sync, summary, qa_needed)


def _check_feed(key: str, url: str) -> None:
    from radar.fetch.http import HttpClient
    from radar.sources._common import rss_feed

    with HttpClient(timeout=10, max_retries=0) as http:
        resp = http.get(url)
    if not resp.ok:
        raise SourceInvalid(f"the feed answered HTTP {resp.status}")
    try:
        entries, _ = rss_feed(resp.text, key)
    except Exception as exc:  # noqa: BLE001
        raise SourceInvalid(f"that URL is not a readable RSS/Atom feed ({exc})") from exc
    if not entries:
        raise SourceInvalid("the feed has no entries")


def remove_source(db: Any, key: str, *, note: str | None = None,
                  gateway: Any = AUTO) -> Applied:
    """Remove a source the Control room added, or a built-in no longer shipped.

    Built-in sources in `DEFAULT_SOURCES` come back on the next load, so they
    are switched off instead (`apply_source`), never removed.
    """
    from radar.config.defaults import DEFAULT_SOURCES

    cfg, _ = current_config(db)
    if not any(s.key == key for s in cfg.sources):
        raise UnknownSource(key)
    if any(s.key == key for s in DEFAULT_SOURCES):
        raise SourceInvalid(f"{key} is built in — switch it off instead")
    sheet_sync = _sheet_write(gateway, lambda gw: _write_source_row(gw, key, None))
    new_cfg = cfg.model_copy(update={"sources": [s for s in cfg.sources if s.key != key]},
                             deep=True)
    digest, summary, qa_needed = _commit(
        db, new_cfg, kind="source", rows=[(key, "present", "removed")],
        note=note, sheet_sync=sheet_sync)
    return Applied({key: "removed"}, digest, sheet_sync, summary, qa_needed)


def set_source_funds(db: Any, key: str, funds: Any, *, note: str | None = None,
                     gateway: Any = AUTO) -> Applied:
    """Which funds a source feeds (the Sources tab's Funds column). Empty = all."""
    cfg, _ = current_config(db)
    sources = list(cfg.sources)
    index = next((i for i, s in enumerate(sources) if s.key == key), None)
    if index is None:
        raise UnknownSource(key)
    fund_list = _clean_funds(funds)
    before = sources[index]
    if list(before.funds) == fund_list:
        return Applied({}, cfg.hash(), "skipped", {}, False)
    sheet_sync = _sheet_write(gateway, lambda gw: _write_source_row(
        gw, key, {"J": ", ".join(fund_list)}))
    sources[index] = before.model_copy(update={"funds": fund_list})
    new_cfg = cfg.model_copy(update={"sources": sources}, deep=True)
    digest, summary, qa_needed = _commit(
        db, new_cfg, kind="source", rows=[(f"{key}.funds", ", ".join(before.funds) or "all",
                                           ", ".join(fund_list) or "all")],
        note=note, sheet_sync=sheet_sync)
    return Applied({key: ", ".join(fund_list) or "all"}, digest, sheet_sync, summary, qa_needed)


FUND_CRITERIA_TAB = "Fund Criteria"
#: Vehicle fields the Control room may edit → Fund Criteria column.
#: Geography and sector rules stay in the Sheet: a HARD geography typo there
#: is held (never loosened) by the loader's own validation.
VEHICLE_FIELDS: dict[str, str] = {
    "active": "E", "stage_min": "F", "stage_max": "G", "cheque_min": "H",
    "cheque_max": "I", "max_age_years": "L", "one_liner": "P",
}


class FundInvalid(ValueError):
    def __init__(self, errors: Mapping[str, str]) -> None:
        super().__init__("invalid fund criteria")
        self.errors = dict(errors)


class UnknownVehicle(KeyError):
    pass


def _vehicle_value(field: str, raw: Any) -> tuple[Any, str]:
    """(model value, sheet text) for one edited vehicle field, or ValueError."""
    from radar.config.loader import coerce_bool, coerce_number
    from radar.config.models import canon_enum

    text = "" if raw is None else str(raw).strip()
    if field == "active":
        value = raw if isinstance(raw, bool) else coerce_bool(text)
        return value, "TRUE" if value else "FALSE"
    if field in ("stage_min", "stage_max"):
        if not text:
            return None, ""
        canon = canon_enum(text, STAGES)
        if canon is None:
            raise ValueError(f"is not a stage ({', '.join(STAGES)})")
        return canon, canon
    if field in ("cheque_min", "cheque_max"):
        if not text:
            return None, ""
        value = float(coerce_number(text))
        if value < 0:
            raise ValueError("cannot be negative")
        return value, f"{value:.0f}"
    if field == "max_age_years":
        if not text:
            return None, ""
        value = coerce_number(text)
        if float(value) != int(value) or not 0 < int(value) <= 30:
            raise ValueError("must be a whole number of years, 1–30")
        return int(value), str(int(value))
    return text, text            # one_liner


def funds_view(db: Any) -> list[dict[str, Any]]:
    cfg, _ = current_config(db)
    return [
        {"fund_key": v.fund_key, "vehicle_key": v.vehicle_key, "fund_name": v.fund_name,
         "vehicle_name": v.vehicle_name, "active": v.active, "stage_min": v.stage_min,
         "stage_max": v.stage_max, "cheque_min": v.cheque_min, "cheque_max": v.cheque_max,
         "max_age_years": v.max_age_years, "geo_rule": v.geo_rule,
         "geo_values": list(v.geo_values), "one_liner": v.one_liner}
        for v in cfg.all_vehicles()
    ]


def _write_vehicle_cells(gw: Any, fund_key: str, vehicle_key: str,
                         cells: Mapping[str, str]) -> bool:
    from radar.render.sheet import ValueRange

    tab = _quote(FUND_CRITERIA_TAB)
    rng = f"{tab}!A1:B200"
    grid = gw.batch_get([rng]).get(rng, [])
    row_no = next((i for i, row in enumerate(grid, start=1) if i > 1
                   and _cell(row, 0).lower() == fund_key and _cell(row, 1).lower() == vehicle_key),
                  None)
    if row_no is None:
        return False
    gw.batch_set([ValueRange(f"{tab}!{col}{row_no}:{col}{row_no}", [[value]])
                  for col, value in cells.items()], "USER_ENTERED")
    return True


def apply_vehicle(db: Any, fund_key: str, vehicle_key: str, changes: Mapping[str, Any], *,
                  note: str | None = None, gateway: Any = AUTO) -> Applied:
    """Edit one Fund Criteria row: Sheet first, then snapshot and rescore."""
    cfg, _ = current_config(db)
    found = next(((fi, vi) for fi, f in enumerate(cfg.funds) for vi, v in enumerate(f.vehicles)
                  if v.fund_key == fund_key and v.vehicle_key == vehicle_key), None)
    if found is None:
        raise UnknownVehicle(f"{fund_key}/{vehicle_key}")
    fi, vi = found
    vehicle = cfg.funds[fi].vehicles[vi]
    errors: dict[str, str] = {}
    updates: dict[str, Any] = {}
    cells: dict[str, str] = {}
    for field, raw in (changes or {}).items():
        if field not in VEHICLE_FIELDS:
            errors[field] = "cannot be edited here — change it in the Fund Criteria tab"
            continue
        try:
            value, text = _vehicle_value(field, raw)
        except ValueError as exc:
            errors[field] = str(exc)
            continue
        if getattr(vehicle, field) != value:
            updates[field] = value
            cells[VEHICLE_FIELDS[field]] = text
    merged = vehicle.model_copy(update=updates)
    if (merged.cheque_min is not None and merged.cheque_max is not None
            and merged.cheque_min > merged.cheque_max):
        errors["cheque_min"] = "is above cheque_max"
    if (merged.stage_min and merged.stage_max
            and STAGES.index(merged.stage_min) > STAGES.index(merged.stage_max)):
        errors["stage_min"] = "is later than stage_max"
    if errors:
        raise FundInvalid(errors)
    if not updates:
        return Applied({}, cfg.hash(), "skipped", {}, False)

    sheet_sync = _sheet_write(gateway, lambda gw: _write_vehicle_cells(
        gw, fund_key, vehicle_key, cells))
    funds = [f.model_copy(deep=True) for f in cfg.funds]
    funds[fi].vehicles[vi] = merged
    new_cfg = cfg.model_copy(update={"funds": funds}, deep=True)
    label = f"{fund_key}/{vehicle_key}"
    digest, summary, qa_needed = _commit(
        db, new_cfg, kind="fund",
        rows=[(f"{label}.{k}", _display(getattr(vehicle, k)), _display(v)) for k, v in updates.items()],
        note=note, sheet_sync=sheet_sync)
    return Applied({f"{label}.{k}": _display(v) for k, v in updates.items()},
                   digest, sheet_sync, summary, qa_needed)


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
    "AUTO", "Applied", "SettingsInvalid", "SheetUnavailable", "SourceInvalid",
    "UnknownSource", "add_feed_source", "apply_settings", "apply_source",
    "FundInvalid", "UnknownVehicle", "apply_vehicle", "funds_view",
    "changes_view", "current_config", "remove_source", "rescore_now",
    "set_source_funds", "settings_view",
]
