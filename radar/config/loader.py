"""Stage ① — read the Google Sheet, validate it, never let a typo stop the run.

Four responsibilities, in order (05-pipeline §①):

1. **Coerce**, generously, because humans type inconsistently. `yes`, `Y`, `1`
   and `✓` are all `True`; `£1.5m` and `1,500,000` are both `1500000.0`.
2. **Validate** each setting on its own, so one bad cell costs one value rather
   than the whole configuration.
3. **Fall back** to the last known-good snapshot for exactly the fields that
   failed.
4. **Report in the sheet.** Column D of `Settings`, column Q of `Fund Criteria`,
   column I of `Scoring Weights`. Aryan never reads a log.

The controlled vocabularies are read from the `Lists` tab at runtime
(03-data-model §4). The tuples in `radar.config.models` are used only as the
seed for an empty tab and as the last-resort fallback when `Lists` is missing —
never in preference to what the sheet says.

Nothing here talks to Google. The caller hands in already-read tab grids, which
is what makes the whole stage testable offline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Sequence

from annotated_types import Ge, Gt, Le, Lt
from pydantic import ValidationError

from radar.config.models import (
    FOUNDER_SIGNALS,
    GATE_ONLY_GEOS,
    GEOGRAPHIES,
    HARD_REJECT_KEYS,
    SECTORS,
    STAGES,
    TIERS,
    TRACTION_SIGNALS,
    VERDICTS,
    Config,
    Fund,
    Settings,
    SourceConfig,
    Vehicle,
    Weights,
    canon_enum,
    suggest_enum,
)

# The five tabs stage ① reads. One `values.batchGet`, not five reads.
CONFIG_TABS: tuple[str, ...] = (
    "Settings", "Fund Criteria", "Scoring Weights", "Lists", "Sources",
)

SETTINGS_STATUS_COL = "D"
FUND_CRITERIA_STATUS_COL = "Q"
WEIGHTS_STATUS_COL = "I"

OK = "✅"

TRUE_TOKENS = frozenset({"yes", "y", "true", "t", "1", "✓", "✔", "on", "x", "☑", "enabled"})
FALSE_TOKENS = frozenset({"no", "n", "false", "f", "0", "off", "", "—", "-", "n/a", "☐"})

_MONEY = re.compile(
    r"^[£$€]?\s*(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?P<mult>[kKmMbB])?$"
)
_PLAIN_WORDS = re.compile(r"^[A-Za-z][A-Za-z _\-/]*$")
_SEPARATED = re.compile(r"[,;]")
_MULTIPLIER = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}

# A cell holding a note rather than a value: "(agnostic)", "*(as above)*".
_NOTE_CELL = re.compile(r"^[*_(\[].*$")

# The default vocabulary of each list column, used only to seed an empty tab.
DEFAULT_LISTS: dict[str, tuple[str, ...]] = {
    "stage": STAGES,
    "sector": SECTORS,
    "geography": GEOGRAPHIES,
    "founder_signal": FOUNDER_SIGNALS,
    "traction_signal": TRACTION_SIGNALS,
    "tier": TIERS,
    "verdict": VERDICTS,
    "gate_geography": GATE_ONLY_GEOS,
}

ATTRIBUTE_ALIASES: dict[str, str] = {
    "stage": "stage",
    "sector": "sector",
    "geography": "geography",
    "geo": "geography",
    "founder_signal": "founder_signal",
    "founder": "founder_signal",
    "traction_signal": "traction_signal",
    "traction": "traction_signal",
}

ATTRIBUTE_VOCAB_KEY: dict[str, str] = {
    "stage": "stage",
    "sector": "sector",
    "geography": "geography",
    "founder_signal": "founder_signal",
    "traction_signal": "traction_signal",
}

UNKNOWN_POLICIES: tuple[str, ...] = ("neutral", "pessimistic", "assume")


# --------------------------------------------------------------- coercion


def coerce_bool(raw: Any) -> bool:
    """`yes` `y` `TRUE` `1` `✓` `on` → True. `no` `n` `FALSE` `0` blank → False."""
    if isinstance(raw, bool):
        return raw
    token = str(raw or "").strip().lower()
    if token in TRUE_TOKENS:
        return True
    if token in FALSE_TOKENS:
        return False
    raise ValueError(f"{raw!r} is not a yes/no value")


def coerce_number(raw: Any) -> float | int:
    """`£1.5m` `1,500,000` `1.5M` → 1500000.0. Anything else raises."""
    if isinstance(raw, bool):
        raise ValueError(f"{raw!r} is not a number")
    if isinstance(raw, (int, float)):
        return raw
    text = str(raw or "").strip()
    match = _MONEY.match(text)
    if not match:
        raise ValueError(f"{text!r} is not a number")
    number = float(match.group("num").replace(",", ""))
    mult = match.group("mult")
    if mult:
        number *= _MULTIPLIER[mult.lower()]
        return number
    if "," in text or "." in text or text[:1] in "£$€":
        return number
    return int(number)


def coerce_csv(raw: Any) -> list[str]:
    """`GB, IE` and `gb;ie` both become `["GB", "IE"]`. Case is preserved."""
    if isinstance(raw, (list, tuple)):
        return [str(v).strip() for v in raw if str(v).strip()]
    return [p.strip() for p in _SEPARATED.split(str(raw or "")) if p.strip()]


def coerce_weight(raw: Any) -> float | None:
    """Blank means *use the default*; an explicit `0` means *weight at zero*.

    Collapsing those two is how every attribute silently becomes worthless
    (05-pipeline §①). Blank returns None so the caller writes no entry at all,
    which is what makes `Weights.attribute_weight()` return its built-in 1.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValueError(f"{raw!r} is not a weight")
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    return float(coerce_number(text))


def coerce_value(raw: Any) -> Any:
    """The generous converter, exactly the table in 05-pipeline §①.

    Order matters: the yes/no tokens are checked first, which is why `"1"` is
    `True` and not `1`. Numeric *settings* never come through here — they are
    typed by the `Type` column and go through `coerce_number`.
    """
    if raw is None or isinstance(raw, (bool, int, float, list, tuple)):
        if isinstance(raw, (list, tuple)):
            return [str(v).strip() for v in raw]
        return raw
    text = str(raw).strip()
    lowered = text.lower()
    if lowered in TRUE_TOKENS:
        return True
    if lowered in FALSE_TOKENS:
        return False
    # Numbers first: `1,500,000` uses a comma that the CSV branch would
    # otherwise split into three tokens (09-test-plan §2.6).
    try:
        return coerce_number(text)
    except ValueError:
        pass
    if _SEPARATED.search(text):
        # 09-test-plan §2.6: `("gb;ie", ["GB","IE"])`. CSV cells that come
        # through the generous converter are country/region codes and
        # vocabularies, all conventionally upper-case — `coerce_csv` (the
        # typed path) still preserves case for `regions_enabled` etc.
        return [p.strip().upper() for p in _SEPARATED.split(text) if p.strip()]
    if _PLAIN_WORDS.match(text):
        # "Pre Seed" / "pre-seed" / "PRE_SEED" → pre_seed. Anything with digits
        # or punctuation (a pinned model id, a URL) is left exactly as typed.
        return re.sub(r"[\s\-/]+", "_", lowered).strip("_")
    return text


# ------------------------------------------------------------- constraints


def _bounds(name: str) -> tuple[float | None, float | None]:
    info = Settings.model_fields.get(name)
    if info is None:
        return None, None
    low = high = None
    for meta in info.metadata:
        if isinstance(meta, Ge):
            low = meta.ge
        elif isinstance(meta, Gt):
            low = meta.gt
        elif isinstance(meta, Le):
            high = meta.le
        elif isinstance(meta, Lt):
            high = meta.lt
    return low, high


def _fmt(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _error(raw: Any, problem: str, fallback: Any) -> str:
    return f'{"❌"} "{raw}" {problem} — using last good value {_fmt(fallback)}'


# ------------------------------------------------------------------ result


@dataclass
class StatusCell:
    tab: str
    column: str
    row: int
    text: str
    is_error: bool


@dataclass
class LoadResult:
    """The validated config plus everything the sheet needs to say about it.

    Unpacks as `cfg, errors = load_config(...)` to match 09-test-plan §2.6,
    while still carrying the status cells the renderer writes back.
    """

    config: Config
    errors: dict[str, str] = field(default_factory=dict)
    warnings: dict[str, str] = field(default_factory=dict)
    status_cells: list[StatusCell] = field(default_factory=list)
    used_last_good: bool = False
    seeded: list[str] = field(default_factory=list)

    def __iter__(self) -> Iterator[Any]:
        yield self.config
        yield self.errors


# ------------------------------------------------------------- persistence


def save_snapshot(db: Any, cfg: Config, *, is_last_good: bool = True) -> str:
    """Store the validated config under its hash. Reproducibility, and the
    fallback a typo lands on tomorrow (03-data-model §3)."""
    from radar.store.db import now_iso

    digest = cfg.hash()
    payload = json.dumps(cfg.model_dump(mode="json", exclude={"errors"}),
                         sort_keys=True, separators=(",", ":"))
    db.execute(
        """INSERT INTO config_snapshot(config_hash, config_json, is_last_good, created_at)
           VALUES (?,?,?,?)
           ON CONFLICT(config_hash) DO UPDATE SET
             config_json = excluded.config_json,
             is_last_good = excluded.is_last_good""",
        (digest, payload, 1 if is_last_good else 0, now_iso()),
    )
    if is_last_good:
        db.execute("UPDATE config_snapshot SET is_last_good = 0 WHERE config_hash != ?",
                   (digest,))
    return digest


def load_last_good(db: Any) -> Config | None:
    if db is None:
        return None
    row = db.one(
        "SELECT config_json FROM config_snapshot WHERE is_last_good = 1 "
        "ORDER BY created_at DESC LIMIT 1"
    )
    if row is None:
        return None
    try:
        return Config.model_validate(json.loads(row["config_json"]))
    except (ValidationError, ValueError):
        return None


# ------------------------------------------------------------- tab parsers


def _cell(row: Sequence[Any], index: int) -> str:
    if index < len(row) and row[index] is not None:
        return str(row[index]).strip()
    return ""


def _is_note(text: str) -> bool:
    return bool(text) and bool(_NOTE_CELL.match(text))


def parse_lists(grid: Sequence[Sequence[Any]]) -> dict[str, list[str]]:
    """The Lists tab: one column per vocabulary, header in row 1.

    Also carries the SIC → sector map and the ONS region map, as paired
    columns (`sic_code`/`sic_sector`, `region_ons`/`region_value`).
    """
    if not grid:
        return {name: list(values) for name, values in DEFAULT_LISTS.items()}

    header = [str(c).strip() for c in grid[0]]
    out: dict[str, list[str]] = {name: [] for name in header if name}
    for row in grid[1:]:
        for index, name in enumerate(header):
            if not name:
                continue
            value = _cell(row, index)
            if value:
                out[name].append(value)

    for name, values in DEFAULT_LISTS.items():
        if not out.get(name):
            # ponytail: 03-data-model §4 says vocabularies are read at runtime
            # and must not be hard-coded. They still need a value on the very
            # first run, before the Lists tab exists — this is the seed, and it
            # never wins over a populated tab.
            out[name] = list(values)
    return out


def vocab(lists: Mapping[str, Sequence[str]], name: str) -> tuple[str, ...]:
    return tuple(lists.get(name) or DEFAULT_LISTS.get(name, ()))


def parse_settings(
    grid: Sequence[Sequence[Any]],
    *,
    last_good: Config | None,
    lists: Mapping[str, Sequence[str]],
) -> tuple[dict[str, Any], dict[str, str], list[StatusCell]]:
    """Settings tab → a dict of overrides, plus per-key errors and status cells.

    Each key is validated on its own. One typo costs one value, and the message
    names the fallback that was used instead.
    """
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    cells: list[StatusCell] = []
    good = last_good.settings if last_good else Settings()

    for offset, row in enumerate(grid[1:], start=2):
        key = _cell(row, 0)
        if not key or key.startswith("#"):
            continue
        raw = _cell(row, 1)
        type_hint = _cell(row, 2).lower()

        if raw == "":
            # Blank means "use the default" — never zero (05-pipeline §①).
            cells.append(StatusCell("Settings", SETTINGS_STATUS_COL, offset, OK, False))
            continue

        fallback = getattr(good, key, None)
        if fallback is None and key in Settings.model_fields:
            fallback = Settings.model_fields[key].get_default(call_default_factory=True)

        try:
            coerced = _coerce_setting(key, raw, type_hint, lists)
        except ValueError as exc:
            errors[key] = _error(raw, str(exc), fallback)
            cells.append(StatusCell("Settings", SETTINGS_STATUS_COL, offset,
                                    errors[key], True))
            if fallback is not None:
                values[key] = fallback
            continue

        try:
            Settings(**{key: coerced}) if key in Settings.model_fields else None
        except ValidationError:
            low, high = _bounds(key)
            span = f"{_fmt(low)}–{_fmt(high)}" if low is not None and high is not None else "range"
            errors[key] = _error(raw, f"is out of {span}", fallback)
            cells.append(StatusCell("Settings", SETTINGS_STATUS_COL, offset,
                                    errors[key], True))
            if fallback is not None:
                values[key] = fallback
            continue

        values[key] = coerced
        cells.append(StatusCell("Settings", SETTINGS_STATUS_COL, offset, OK, False))

    return values, errors, cells


def _coerce_setting(key: str, raw: str, type_hint: str,
                    lists: Mapping[str, Sequence[str]]) -> Any:
    """Type the cell from the `Type` column first, the model annotation second."""
    info = Settings.model_fields.get(key)
    annotation = info.annotation if info else None

    if type_hint.startswith("bool") or annotation is bool:
        return coerce_bool(raw)

    if type_hint.startswith("csv") or annotation == list[str]:
        return coerce_csv(raw)

    if type_hint.startswith("enum") or key == "max_stage":
        allowed = vocab(lists, "stage")
        canon = canon_enum(raw, allowed)
        if canon is None:
            hint = suggest_enum(raw, allowed)
            raise ValueError("is not a valid stage" + (f" — did you mean '{hint}'?" if hint else ""))
        return canon

    if type_hint.startswith("string") or (annotation is str and key != "max_stage"):
        return raw

    if type_hint.startswith(("int", "money", "0", "float")) or annotation in (int, float):
        number = coerce_number(raw)          # raises "… is not a number"
        if annotation is int and float(number) != int(number):
            raise ValueError("is not a whole number")
        return int(number) if annotation is int else float(number)

    return coerce_value(raw)


def parse_fund_criteria(
    grid: Sequence[Sequence[Any]],
    *,
    lists: Mapping[str, Sequence[str]],
) -> tuple[list[Fund], dict[str, str], list[StatusCell]]:
    """One row per vehicle. Eleven of them by default (07-interfaces tab 4)."""
    funds: dict[str, Fund] = {}
    warnings: dict[str, str] = {}
    cells: list[StatusCell] = []
    stages = vocab(lists, "stage")
    sectors = vocab(lists, "sector")
    geos = tuple(vocab(lists, "geography")) + tuple(vocab(lists, "gate_geography"))

    for offset, row in enumerate(grid[1:], start=2):
        fund_key = _cell(row, 0).strip().lower()
        vehicle_key = _cell(row, 1).strip().lower()
        if not fund_key or not vehicle_key:
            continue

        notes: list[str] = []
        raw_rejects = _cell(row, 12)
        for part in re.split(r"\s*·\s*|\s*;\s*", raw_rejects):
            part = part.strip().strip("`")
            if not part or ":" not in part:
                continue
            name = part.split(":", 1)[0].strip()
            if name not in HARD_REJECT_KEYS:
                # Never an error. Warn and ignore (07-interfaces tab 4).
                notes.append(f"⚠️ unknown hard-reject key '{name}' ignored")

        stage_min = _enum_or_note(_cell(row, 5), stages, "stage", notes)
        stage_max = _enum_or_note(_cell(row, 6), stages, "stage", notes)
        geo_values = _enum_list(_cell(row, 10), geos, "geo value", notes)
        sectors_plus = _enum_list(_cell(row, 13), sectors, "sector", notes)
        sectors_minus = _enum_list(_cell(row, 14), sectors, "sector", notes)

        try:
            active = coerce_bool(_cell(row, 4))
        except ValueError:
            notes.append(f"⚠️ '{_cell(row, 4)}' is not TRUE/FALSE — treated as TRUE")
            active = True

        geo_rule = (_cell(row, 9) or "HARD").upper()
        if geo_rule not in {"HARD", "SOFT"}:
            notes.append(f"⚠️ geo rule '{geo_rule}' unknown — treated as HARD")
            geo_rule = "HARD"

        vehicle = Vehicle(
            fund_key=fund_key,
            vehicle_key=vehicle_key,
            fund_name=_cell(row, 2) or fund_key,
            vehicle_name=_cell(row, 3) or vehicle_key,
            active=active,
            stage_min=stage_min,
            stage_max=stage_max,
            cheque_min=_money_or_none(_cell(row, 7), notes, "cheque min"),
            cheque_max=_money_or_none(_cell(row, 8), notes, "cheque max"),
            geo_rule=geo_rule,
            geo_values=geo_values,
            max_age_years=_int_or_none(_cell(row, 11), notes, "max age"),
            hard_rejects=raw_rejects,
            sectors_plus=sectors_plus,
            sectors_minus=sectors_minus,
            one_liner=_cell(row, 15),
            warnings=notes,
        )

        fund = funds.get(fund_key)
        if fund is None:
            fund = funds[fund_key] = Fund(key=fund_key, name=vehicle.fund_name)
        fund.vehicles.append(vehicle)

        text = " · ".join(notes) if notes else OK
        cells.append(StatusCell("Fund Criteria", FUND_CRITERIA_STATUS_COL, offset,
                                text, bool(notes)))
        if notes:
            warnings[f"{fund_key}.{vehicle_key}"] = text

    return list(funds.values()), warnings, cells


def _enum_or_note(raw: str, allowed: Sequence[str], label: str,
                  notes: list[str]) -> str | None:
    if not raw or _is_note(raw):
        return None
    canon = canon_enum(raw, tuple(allowed))
    if canon is None:
        hint = suggest_enum(raw, tuple(allowed))
        notes.append(f"⚠️ '{raw}' is not a {label}" + (f" — did you mean '{hint}'?" if hint else ""))
        return None
    return canon


def _enum_list(raw: str, allowed: Sequence[str], label: str,
               notes: list[str]) -> list[str]:
    out: list[str] = []
    for part in coerce_csv(raw):
        if _is_note(part):
            continue                      # "*(agnostic)*" is a comment, not a value
        canon = canon_enum(part, tuple(allowed))
        if canon is None:
            hint = suggest_enum(part, tuple(allowed))
            notes.append(f"⚠️ '{part}' is not a {label}"
                         + (f" — did you mean '{hint}'?" if hint else ""))
            continue
        out.append(canon)
    return out


def _money_or_none(raw: str, notes: list[str], label: str) -> float | None:
    if not raw or _is_note(raw):
        return None
    try:
        return float(coerce_number(raw))
    except ValueError:
        notes.append(f"⚠️ {label} '{raw}' is not a number — ignored")
        return None


def _int_or_none(raw: str, notes: list[str], label: str) -> int | None:
    value = _money_or_none(raw, notes, label)
    return int(value) if value is not None else None


def parse_weights(
    grid: Sequence[Sequence[Any]],
    *,
    fund_keys: Mapping[str, str],
    lists: Mapping[str, Sequence[str]],
    last_good: Config | None,
) -> tuple[Weights, dict[str, str], list[StatusCell]]:
    """Two blocks, two tables, two functions.

    Block 1 (0–4) feeds `matrix_value()`. Block 2, under the
    `ATTRIBUTE IMPORTANCE` banner (0–10), feeds `attribute_weight()`. A blank
    importance cell means 1, not 0 — so it is simply not written, and the
    model's own default supplies the 1.
    """
    from radar.render.formatting import IMPORTANCE_BANNER

    weights = Weights()
    warnings: dict[str, str] = {}
    cells: list[StatusCell] = []
    good = last_good.weights if last_good else None

    if not grid:
        return weights, warnings, cells

    header = [str(c).strip() for c in grid[0]]
    matrix_funds = _fund_columns(header, fund_keys, start=2)

    banner_at = None
    for index, row in enumerate(grid):
        if _cell(row, 0).strip().upper() == IMPORTANCE_BANNER:
            banner_at = index
            break

    attribute: str | None = None
    end = banner_at if banner_at is not None else len(grid)
    for offset in range(1, end):
        row = grid[offset]
        raw_attr = _cell(row, 0)
        if raw_attr:
            attribute = ATTRIBUTE_ALIASES.get(
                re.sub(r"[\s\-/]+", "_", raw_attr.strip().lower()))
        category = _cell(row, 1)
        if attribute is None or not category:
            continue

        allowed = vocab(lists, ATTRIBUTE_VOCAB_KEY.get(attribute, attribute))
        value = canon_enum(category, allowed)
        notes: list[str] = []
        if value is None:
            hint = suggest_enum(category, allowed)
            notes.append(f"⚠️ '{category}' is not a known {attribute} value"
                         + (f" — did you mean '{hint}'?" if hint else ""))
        else:
            for index, key in matrix_funds.items():
                raw = _cell(row, index)
                try:
                    parsed = coerce_weight(raw)
                except ValueError:
                    parsed = None
                    notes.append(f"⚠️ '{raw}' is not a number — ignored")
                if parsed is None:
                    continue
                if not 0 <= parsed <= 4:
                    fallback = (good.matrix_value(attribute, value, key) if good else None)
                    notes.append(_error(raw, "is outside 0–4",
                                        fallback if fallback is not None else 0))
                    parsed = float(fallback) if fallback is not None else 0.0
                weights.matrix.setdefault(attribute, {}).setdefault(value, {})[key] = int(parsed)

        policy_raw = _cell(row, 6)
        if policy_raw:
            policy = canon_enum(policy_raw, UNKNOWN_POLICIES)
            if policy is None:
                notes.append(f"⚠️ unknown policy '{policy_raw}' — using neutral")
            else:
                weights.unknown_policy[attribute] = policy

        text = " · ".join(notes) if notes else OK
        cells.append(StatusCell("Scoring Weights", WEIGHTS_STATUS_COL, offset + 1,
                                text, bool(notes)))
        if notes:
            warnings[f"matrix.{attribute}.{category}"] = text

    if banner_at is None:
        return weights, warnings, cells

    importance_funds: dict[int, str] = {}
    for offset in range(banner_at + 1, len(grid)):
        row = grid[offset]
        first = _cell(row, 0)
        if not first:
            continue
        if first.lower() == "attribute":
            importance_funds = _fund_columns(
                [str(c).strip() for c in row], fund_keys, start=1)
            continue
        if not importance_funds:
            importance_funds = _fund_columns(list(header), fund_keys, start=1)
        attribute = ATTRIBUTE_ALIASES.get(re.sub(r"[\s\-/]+", "_", first.strip().lower()))
        if attribute is None:
            continue
        notes = []
        for index, key in importance_funds.items():
            raw = _cell(row, index)
            try:
                parsed = coerce_weight(raw)
            except ValueError:
                notes.append(f"⚠️ '{raw}' is not a number — ignored")
                continue
            if parsed is None:
                continue                  # blank means 1, supplied by the model
            if not 0 <= parsed <= 10:
                fallback = good.attribute_weight(attribute, key) if good else 1
                notes.append(_error(raw, "is outside 0–10", fallback))
                parsed = float(fallback)
            weights.importance.setdefault(attribute, {})[key] = int(parsed)
        text = " · ".join(notes) if notes else OK
        cells.append(StatusCell("Scoring Weights", WEIGHTS_STATUS_COL, offset + 1,
                                text, bool(notes)))
        if notes:
            warnings[f"importance.{attribute}"] = text

    return weights, warnings, cells


def _fund_columns(header: Sequence[str], fund_keys: Mapping[str, str],
                  *, start: int) -> dict[int, str]:
    """Map the `DSW | Northstar | Outward | Anticus` header cells to fund keys.

    The keys come from the Fund Criteria tab, so adding a fifth fund there is
    all it takes to add a fifth column here.
    """
    out: dict[int, str] = {}
    for index in range(start, len(header)):
        name = header[index].strip()
        if not name or name.lower() in {"unknown policy", "notes", "status", "category"}:
            continue
        key = fund_keys.get(name.strip().lower())
        if key is None:
            key = fund_keys.get(re.sub(r"[\s\-]+", "_", name.strip().lower()))
        if key is None and re.fullmatch(r"[a-z_]+", name.lower()):
            key = name.lower()
        if key:
            out[index] = key
    return out


def parse_sources(grid: Sequence[Sequence[Any]]) -> list[SourceConfig]:
    """`Enabled` is user-editable so Aryan can switch a noisy source off."""
    out: list[SourceConfig] = []
    for row in grid[1:]:
        name = _cell(row, 0)
        if not name:
            continue
        # ponytail: 07-interfaces tab 8 shows a display name in column A. The
        # adapter key is its snake_case form, which round-trips for every key
        # in 04-sources.
        key = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
        track = (_cell(row, 1) or "A").upper()
        try:
            enabled = coerce_bool(_cell(row, 2))
        except ValueError:
            enabled = True
        out.append(SourceConfig(key=key, track=track if track in {"A", "B"} else "A",
                                enabled=enabled, note=_cell(row, 7)))
    return out


# ------------------------------------------------------------------- entry


def load_config(raw: Mapping[str, Sequence[Sequence[Any]]], db: Any = None) -> LoadResult:
    """Read, coerce, validate, fall back, and report — in that order.

    Returns a `LoadResult`, which unpacks as `cfg, errors` for the test-plan
    signature. A bad cell never raises: the offending field falls back to the
    last known-good value and the reason is written into the sheet.
    """
    last_good = load_last_good(db) if db is not None else None

    lists = parse_lists(raw.get("Lists") or [])
    funds, fund_warnings, criteria_cells = parse_fund_criteria(
        raw.get("Fund Criteria") or [], lists=lists)

    fund_keys: dict[str, str] = {}
    for fund in funds:
        fund_keys[fund.key] = fund.key
        fund_keys[fund.name.strip().lower()] = fund.key
        fund_keys[fund.name.split()[0].strip().lower()] = fund.key

    weights, weight_warnings, weight_cells = parse_weights(
        raw.get("Scoring Weights") or [], fund_keys=fund_keys, lists=lists,
        last_good=last_good)

    overrides, errors, settings_cells = parse_settings(
        raw.get("Settings") or [], last_good=last_good, lists=lists)

    sources = parse_sources(raw.get("Sources") or [])

    used_last_good = False
    try:
        settings = Settings(**overrides)
    except ValidationError as exc:
        # Every individual field already validated, so this can only be a
        # cross-field rule. Fall back wholesale, exactly as 05-pipeline ①.
        if last_good is None:
            raise
        settings = last_good.settings
        used_last_good = True
        errors["settings"] = f"❌ {exc.error_count()} settings could not be validated together"

    cfg = Config(
        settings=settings,
        funds=funds or (last_good.funds if last_good else []),
        weights=weights if (weights.matrix or weights.importance)
        else (last_good.weights if last_good else weights),
        sources=sources or (last_good.sources if last_good else []),
        lists=dict(lists),
        errors=sorted(errors.values()),
    )

    if db is not None and not errors:
        save_snapshot(db, cfg, is_last_good=True)

    warnings = {**fund_warnings, **weight_warnings}
    return LoadResult(
        config=cfg,
        errors=errors,
        warnings=warnings,
        status_cells=settings_cells + criteria_cells + weight_cells,
        used_last_good=used_last_good,
    )
