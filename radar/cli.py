"""THE interface. Telegram calls it, the sheet is rendered by it, humans use it.

Nothing is trapped inside the chat layer (07-interfaces §3).

Exit codes: 0 success · 1 partial (some sources failed) · 2 fatal.

Commands import their implementation lazily and inside the function body. That
keeps `founder-radar doctor` working while other phases are still being built,
and it keeps startup fast — a CLI that imports gspread and trafilatura up front
takes a second to print `--help`.
"""

from __future__ import annotations

import json as jsonlib
import os
import shutil
import sys
from pathlib import Path

import click

from radar import __version__
from radar.store.db import Db, default_db_path

EXIT_OK, EXIT_PARTIAL, EXIT_FATAL = 0, 1, 2


class NotBuilt(click.ClickException):
    def __init__(self, what: str) -> None:
        super().__init__(f"{what} is not implemented yet")


def load_env_file(path: Path | None = None) -> int:
    """Read `.env` into the environment. Returns how many names it set.

    The systemd unit loads this file with `EnvironmentFile=`, so the server was
    always fine — but the README's own quick start is `cp .env.example .env`
    then `founder-radar doctor`, and nothing in the process read it. Following
    the documented steps exactly, you filled in a Companies House key and were
    then told the key was missing.

    A real environment variable always wins, so `CH_API_KEY=... founder-radar`
    still overrides the file, and systemd's copy is untouched. Hand-parsed
    rather than adding python-dotenv: `KEY=value`, `#` comments, optional
    `export`, and surrounding quotes stripped is the whole format in use.
    """
    path = path or Path.cwd() / ".env"
    try:
        if not path.is_file():
            return 0
    except PermissionError:
        # Unreadable cwd/.env (e.g. /root/.env when invoked via sudo -u radar).
        return 0
    loaded = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.removeprefix("export ").partition("=")
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if not name or not value or name in os.environ:
            continue
        os.environ[name] = value
        loaded += 1
    return loaded


def _db(ctx: click.Context) -> Db:
    db = Db(ctx.obj["db_path"])
    if not db.tables():
        db.migrate()
    return db


def _emit(data, as_json: bool) -> None:
    if as_json:
        click.echo(jsonlib.dumps(data, indent=2, default=str))
    elif isinstance(data, str):
        click.echo(data)
    else:
        click.echo(jsonlib.dumps(data, indent=2, default=str))


@click.group()
@click.option("--db", "db_path", default=None, help="SQLite path (default: $RADAR_DB)")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
@click.version_option(__version__, prog_name="founder-radar")
@click.pass_context
def cli(ctx: click.Context, db_path: str | None, as_json: bool) -> None:
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db_path or str(default_db_path())
    ctx.obj["json"] = as_json


# ------------------------------------------------------------------- the run


@cli.command()
@click.option("--fund", "fund_key", default=None, help="Scope the run to one fund")
@click.option("--source", "source_key", default=None, help="Run one adapter in isolation")
@click.option("--since", type=click.DateTime(formats=["%Y-%m-%d"]), default=None,
              help="Only items published on or after YYYY-MM-DD")
@click.option("--dry-run", is_flag=True, help="Do everything, write nothing")
@click.option("--no-llm", is_flag=True, help="Heuristic extraction only. Zero AI cost.")
@click.pass_context
def run(ctx, fund_key, source_key, since, dry_run, no_llm):
    """The daily run: fetch → extract → resolve → enrich → score → render."""
    from radar.extract.llm import build_llm
    from radar.pipeline import run_pipeline

    try:
        result = run_pipeline(
            # `click.DateTime` hands back a datetime; the adapters compare it
            # against `published_at`, which is a date. Mixing the two raises.
            _db(ctx), fund_key=fund_key, source_key=source_key,
            since=since.date() if since else None,
            dry_run=dry_run, use_llm=not no_llm,
            # `--no-llm` is the explicit switch; otherwise the provider comes
            # from env (LLM_PROVIDER/LLM_API_KEY/LLM_MODEL/LLM_BASE_URL), and
            # no key at all means the documented heuristic-only mode.
            llm=None if no_llm else build_llm(),
        )
    except ValueError as exc:                 # unknown --fund, before any crawl
        raise click.BadParameter(str(exc), param_hint="--fund") from exc
    _emit(result.summary(), ctx.obj["json"])
    sys.exit(EXIT_PARTIAL if result.status == "partial" else EXIT_OK)


@cli.command()
@click.option("--days", default=90, help="Companies House first-run sweep window")
@click.pass_context
def backfill(ctx, days):
    """First-run Companies House sweep."""
    from radar.pipeline import run_backfill

    _emit(run_backfill(_db(ctx), days=days), ctx.obj["json"])


@cli.command()
@click.option("--all", "all_", is_flag=True, help="Re-score every company, not just today's")
@click.pass_context
def rescore(ctx, all_):
    """Recompute scores after a weights change. No network, no AI."""
    from radar.pipeline import run_rescore

    _emit(run_rescore(_db(ctx), all_companies=all_), ctx.obj["json"])


# --------------------------------------------------------------------- views


@cli.command()
@click.option("--alert-if-stale", "stale_after", default=None, metavar="DURATION",
              help="Send one Telegram alert if no successful run finished within "
                   "DURATION (e.g. 26h). FR-9.3.")
@click.pass_context
def status(ctx, stale_after):
    """Last run, source health, this month's AI cost.

    With `--alert-if-stale` this is also the heartbeat the systemd timer runs
    (FR-9.3, 08-deployment §4). The check itself lives in
    `radar.notify.heartbeat` — one implementation, two entry points — so the
    threshold, the three alert conditions and the single Telegram message are
    identical however it is invoked. Exit 1 means an alert fired, which is
    information rather than a broken unit.
    """
    from radar.render.digest import render_status

    conn = _db(ctx)
    report = render_status(conn)

    if stale_after is None:
        _emit(report, ctx.obj["json"])
        return

    from radar.notify.heartbeat import check, parse_duration

    try:
        threshold = parse_duration(stale_after)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--alert-if-stale") from exc

    result = check(conn, stale_after=threshold)
    if ctx.obj["json"]:
        _emit({"status": report, "stale": result.stale, "alerts": result.alerts,
               "alert_sent": result.sent}, True)
    else:
        _emit(report, False)
        for line in result.alerts or ["✅ Founder Radar heartbeat: healthy."]:
            click.echo(line)
    sys.exit(EXIT_PARTIAL if result.alerts else EXIT_OK)


@cli.command()
@click.argument("name")
@click.pass_context
def show(ctx, name):
    """Full record, signals and score breakdown for one company."""
    from radar.render.digest import render_show

    _emit(render_show(_db(ctx), name), ctx.obj["json"])


@cli.command()
@click.argument("fund_key")
@click.option("--top", default=10)
@click.pass_context
def fund(ctx, fund_key, top):
    """Top current matches for one fund."""
    from radar.render.digest import render_fund

    _emit(render_fund(_db(ctx), fund_key, top=top), ctx.obj["json"])


@cli.command()
@click.option("--today", "period", flag_value="today", default=True)
@click.option("--week", "period", flag_value="week")
@click.option("--date", "on_date", default=None)
@click.option("--send", is_flag=True, help="Push to Telegram as well as printing")
@click.option("--force", is_flag=True,
              help="Send even if the publish gate would BLOCK "
                   "(also requires RADAR_ALLOW_FORCE_PUBLISH=1)")
@click.option("--no-hermes", is_flag=True, help="Skip Hermes publish-check subagent")
@click.option("--no-heal", is_flag=True, help="Do not auto-rescore on hash drift")
@click.pass_context
def digest(ctx, period, on_date, send, force, no_hermes, no_heal):
    """The Telegram digest, printed (and optionally sent).

    `--send` runs the publish gate first (hash drift, poison, Hermes) and
    auto-heals by default. `--no-heal` refuses instead of rescoring.
    Use `founder-radar publish --send` for the full agent-native path
    (gate + Today QA + send). `--force` bypasses the gate only when
    `RADAR_ALLOW_FORCE_PUBLISH=1` is also set — last resort.
    """
    from radar.render.digest import render_digest

    db = _db(ctx)
    if send and force:
        allow = (os.environ.get("RADAR_ALLOW_FORCE_PUBLISH") or "").strip().lower()
        if allow not in ("1", "true", "yes"):
            click.echo(
                "digest --send --force refused: set RADAR_ALLOW_FORCE_PUBLISH=1 "
                "(ops escape hatch; not set in systemd)",
                err=True,
            )
            sys.exit(EXIT_FATAL)
        click.echo(
            "⚠️  digest --send --force: publish gate bypassed "
            "(RADAR_ALLOW_FORCE_PUBLISH=1)",
            err=True,
        )
    elif send:
        from radar.qa.publish import format_publish_report, pre_publish_check

        report = pre_publish_check(db, use_hermes=not no_hermes, heal=not no_heal)
        click.echo(format_publish_report(report))
        if not report.ok:
            click.echo("digest --send refused: publish gate BLOCK", err=True)
            sys.exit(EXIT_FATAL)

    text = render_digest(db, period=period, on_date=on_date)
    if send:
        from radar.notify.telegram import send_message

        send_message(text)
    _emit(text, ctx.obj["json"])



@cli.command("publish-check")
@click.option("--no-hermes", is_flag=True, help="Deterministic checks only")
@click.option("--no-heal", is_flag=True, help="Do not auto-rescore / repair")
@click.pass_context
def publish_check(ctx, no_hermes, no_heal):
    """Hermes + deterministic gate: is it safe to publish to Aryan?

    Exit 0 = PASS, exit 2 = BLOCK. Auto-heals config_hash drift when it can.
    """
    from radar.qa.publish import format_publish_report, pre_publish_check

    report = pre_publish_check(
        _db(ctx), use_hermes=not no_hermes, heal=not no_heal,
    )
    if ctx.obj["json"]:
        _emit(report.as_dict(), True)
    else:
        click.echo(format_publish_report(report))
    sys.exit(EXIT_OK if report.ok else EXIT_FATAL)


@cli.command("publish")
@click.option("--send", is_flag=True, help="Push the digest to Telegram on PASS")
@click.option("--no-hermes", is_flag=True, help="Skip Hermes subagents")
@click.option("--no-heal", is_flag=True, help="Do not auto-rescore / repair")
@click.option("--skip-today-qa", is_flag=True, help="Skip per-card Today QA")
@click.pass_context
def publish(ctx, send, no_hermes, no_heal, skip_today_qa):
    """Agent-native publish: gate → Today QA → digest (optional --send).

    This is the only path systemd and Hermes should use to message Aryan.
    """
    from radar.config.loader import load_runtime_config
    from radar.qa.publish import format_publish_report, pre_publish_check
    from radar.render.digest import render_digest

    db = _db(ctx)
    report = pre_publish_check(db, use_hermes=not no_hermes, heal=not no_heal)
    click.echo(format_publish_report(report))
    if not report.ok:
        click.echo("publish refused: gate BLOCK", err=True)
        sys.exit(EXIT_FATAL)

    if not skip_today_qa:
        from radar.qa.today import run_today_qa

        cfg, _, warnings = load_runtime_config(db)
        qa = run_today_qa(db, cfg, use_hermes=not no_hermes)
        click.echo(
            f"today QA: {qa.checked} checked, {qa.passed} pass, "
            f"{qa.rejected} rejected, {qa.cached} cached"
        )
        for line in [*warnings, *qa.warnings]:
            click.echo(line)

    # Re-check after QA/heal so a mid-flight hash flip cannot sneak through.
    report2 = pre_publish_check(db, use_hermes=False, heal=True)
    if not report2.ok:
        click.echo(format_publish_report(report2))
        click.echo("publish refused after Today QA: gate BLOCK", err=True)
        sys.exit(EXIT_FATAL)

    text = render_digest(db, period="today")
    if send:
        from radar.notify.telegram import send_message

        send_message(text)
        click.echo("digest sent")
    _emit(text, ctx.obj["json"])

@cli.command("today-qa")
@click.option("--no-hermes", is_flag=True,
              help="Rules only — skip the Hermes subagent")
@click.pass_context
def today_qa(ctx, no_hermes):
    """Re-run the Hermes Today QA subagent on Today's list.

    Veto only: a reject hides the card from Today. Scores are not rewritten.
    """
    from radar.config.loader import load_runtime_config
    from radar.qa.today import run_today_qa

    db = _db(ctx)
    cfg, _, warnings = load_runtime_config(db)
    report = run_today_qa(db, cfg, use_hermes=not no_hermes)
    payload = {
        "checked": report.checked,
        "passed": report.passed,
        "rejected": report.rejected,
        "cached": report.cached,
        "skipped": report.skipped,
        "warnings": [*warnings, *report.warnings],
    }
    if ctx.obj["json"]:
        _emit(payload, True)
        return
    click.echo(
        f"today QA: {report.checked} checked, {report.passed} pass, "
        f"{report.rejected} rejected, {report.cached} cached"
    )
    for line in payload["warnings"]:
        click.echo(line)


@cli.command("why-today")
@click.pass_context
def why_today(ctx):
    """Explain an empty or thin Today list (funnel + Fund Criteria health).

    Paste this into Telegram when the client asks why there is nothing to
    review. Counts only — no company names.
    """
    from radar.render.today_diagnose import (
        diagnose_today,
        format_today_diagnosis,
    )

    report = diagnose_today(_db(ctx))
    if ctx.obj["json"]:
        _emit(report, True)
        return
    click.echo(format_today_diagnosis(report))
    if report.get("poisoned_fund_criteria") or (
        report.get("scored_for_active_hash") == 0
        and (report.get("scores_on_other_hashes") or 0) > 0
    ):
        sys.exit(EXIT_PARTIAL)


# ---------------------------------------------------------------- the sheet


@cli.command("sync-sheet")
@click.pass_context
def sync_sheet(ctx):
    """Re-render the Google Sheet without fetching anything."""
    from radar.render.sheet import sync_sheet as _sync

    _emit(_sync(_db(ctx)), ctx.obj["json"])


@cli.command("repair-fund-criteria")
@click.option(
    "--force-sheet", is_flag=True,
    help="Overwrite the Fund Criteria tab even when last-good looks healthy",
)
@click.pass_context
def repair_fund_criteria(ctx, force_sheet):
    """Reseed Fund Criteria from code defaults and refresh last-good.

    Use after a column-shifted sheet wrote vehicle_key='yes' into scores.
    Without --force-sheet, the Google tab is only rewritten when the loaded
    config is poisoned (or last-good was healed from defaults).
    """
    from radar.config.defaults import default_config
    from radar.config.loader import (
        funds_are_poisoned,
        load_last_good,
        load_runtime_config,
        save_snapshot,
    )
    from radar.render.sheet import (
        FUND_CRITERIA,
        ValueRange,
        a1,
        col_letter,
        fund_criteria_seed_grid,
        open_gateway,
    )

    db = _db(ctx)
    warn_list: list[str] = []
    cfg, _gateway, warnings = load_runtime_config(db)
    warn_list.extend(warnings)
    poisoned = funds_are_poisoned(cfg.funds)
    last = load_last_good(db)
    last_poisoned = last is not None and funds_are_poisoned(last.funds)

    needs_repair = poisoned or last_poisoned or force_sheet
    if needs_repair:
        cfg = default_config()
        save_snapshot(db, cfg, is_last_good=True)

    sheet_written = False
    if needs_repair:
        try:
            gw = open_gateway()
        except Exception as exc:  # noqa: BLE001 — sheet is optional for DB heal
            warn_list.append(f"sheet not rewritten: {exc}")
        else:
            grid = fund_criteria_seed_grid(cfg)
            # Blank the used range first so leftover shifted columns disappear,
            # then write the canonical seed.
            blank = [[""] * 17 for _ in range(200)]
            gw.batch_set(
                [ValueRange(a1(FUND_CRITERIA, "A", 1, "Q", 200), blank)],
                value_input_option="USER_ENTERED",
            )
            width = col_letter(max(len(r) for r in grid) - 1)
            gw.batch_set(
                [ValueRange(a1(FUND_CRITERIA, "A", 1, width, len(grid)), grid)],
                value_input_option="USER_ENTERED",
            )
            sheet_written = True

    payload = {
        "repaired": needs_repair,
        "sheet_written": sheet_written,
        "funds": [f.name for f in cfg.funds],
        "vehicle_keys": [
            v.vehicle_key for f in cfg.funds for v in f.vehicles
        ],
        "warnings": warn_list,
    }
    _emit(payload, ctx.obj["json"])
    if not payload["repaired"]:
        click.echo("Fund Criteria already healthy — pass --force-sheet to reseed")

# -------------------------------------------------------------------- upkeep


@cli.command()
@click.option("--list", "list_", is_flag=True, help="Every source with its robots verdict")
@click.option("--test", "test_key", default=None, help="Run one adapter against the live site")
@click.option("--sniff", "sniff_url", default=None, help="Find a CMS JSON endpoint on a site")
@click.pass_context
def sources(ctx, list_, test_key, sniff_url):
    """Inspect, test and discover sources."""
    from radar.sources import cli_sources

    _emit(cli_sources(_db(ctx), list_=list_, test_key=test_key, sniff_url=sniff_url),
          ctx.obj["json"])


@cli.command()
@click.pass_context
def tune(ctx):
    """Threshold sweep against Aryan's own verdicts."""
    from radar.score.tune import sweep

    _emit(sweep(_db(ctx)), ctx.obj["json"])


@cli.command()
@click.pass_context
def review(ctx):
    """Work the fuzzy-match review queue."""
    from radar.resolve.review import review_queue

    _emit(review_queue(_db(ctx)), ctx.obj["json"])


@cli.command()
@click.argument("name")
@click.pass_context
def forget(ctx, name):
    """GDPR erasure: delete the person and suppress re-ingestion."""
    from radar.privacy import forget_person

    _emit(forget_person(_db(ctx), name), ctx.obj["json"])


# ------------------------------------------------------------------ database


@cli.group()
def db():
    """Database maintenance."""


@db.command("migrate")
@click.pass_context
def db_migrate(ctx):
    """Create or update every table."""
    conn = Db(ctx.obj["db_path"])
    conn.migrate()
    click.echo(f"migrated {ctx.obj['db_path']} — {len(conn.tables())} tables")


@db.command("backup")
@click.option("--to", "dest", default=None)
@click.option("--retain-days", default=14, show_default=True,
              help="Delete older snapshots once this one has succeeded. 0 keeps everything.")
@click.pass_context
def db_backup(ctx, dest, retain_days):
    """Snapshot the database with SQLite's own backup API, then prune.

    FR-9.4 is "backed up daily, with 14 days retained" — both halves. The
    retention window matches `deploy/backup.sh` and is applied the same way, by
    modification time and only over our own `radar-*.db` filenames, and only
    *after* the new snapshot is safely on disk: never delete history to make
    room for nothing.
    """
    import sqlite3
    import time
    from datetime import date

    src = Path(ctx.obj["db_path"])
    dest_path = Path(dest) if dest else src.parent / "backups" / f"radar-{date.today()}.db"
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    conn = Db(src)
    out = sqlite3.connect(dest_path)
    try:
        conn.conn.backup(out)
    finally:
        out.close()
        conn.close()

    pruned = 0
    if retain_days > 0:
        cutoff = time.time() - retain_days * 86_400
        for old in dest_path.parent.glob("radar-*.db"):
            if old != dest_path and old.is_file() and old.stat().st_mtime < cutoff:
                old.unlink()
                pruned += 1
    click.echo(f"backed up to {dest_path}"
               + (f" ({pruned} older than {retain_days}d pruned)" if pruned else ""))


@db.command("restore")
@click.argument("src")
@click.pass_context
def db_restore(ctx, src):
    """Replace the live database with a backup."""
    shutil.copy2(src, ctx.obj["db_path"])
    click.echo(f"restored {ctx.obj['db_path']} from {src}")


# -------------------------------------------------------------------- doctor

REQUIRED_ENV = ("COMPANIES_HOUSE_API_KEY",)
OPTIONAL_ENV = ("LLM_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                "GOOGLE_SA_JSON", "SHEET_ID", "RADAR_USER_AGENT")


@cli.command()
@click.pass_context
def doctor(ctx):
    """Run this first when anything looks wrong.

    Checks keys, database, disk and sheet access, and prints a pass/fail table.
    Never raises — a diagnostic that crashes is useless.
    """
    checks: list[tuple[str, bool, str]] = []

    for name in REQUIRED_ENV:
        checks.append((f"env {name}", bool(os.environ.get(name)), "required"))
    for name in OPTIONAL_ENV:
        present = bool(os.environ.get(name))
        # Bot token without chat id is a misconfiguration: the morning
        # digest prints locally and the client sees "no result".
        if name == "TELEGRAM_CHAT_ID" and os.environ.get("TELEGRAM_BOT_TOKEN"):
            checks.append((
                f"env {name}", present,
                "required when TELEGRAM_BOT_TOKEN is set — else digest never arrives",
            ))
        else:
            checks.append((f"env {name}", present, "optional — degraded if missing"))

    db_path = Path(ctx.obj["db_path"])
    conn: Db | None = None
    try:
        conn = Db(db_path)
        tables = conn.tables()
        checks.append(("database", bool(tables), f"{len(tables)} tables at {db_path}"))
        version = conn.get_meta("schema_version", "?")
        checks.append(("schema version", version is not None, str(version)))
    except Exception as exc:                    # noqa: BLE001 - diagnostics must not crash
        checks.append(("database", False, str(exc)))

    if conn is not None:
        try:
            from radar.render.today_diagnose import diagnose_today

            report = diagnose_today(conn)
            poisoned = not report["poisoned_fund_criteria"]
            checks.append((
                "Fund Criteria last-good",
                poisoned,
                (
                    "ok"
                    if poisoned
                    else "POISONED vehicle_key — repair-fund-criteria then rescore --all"
                ),
            ))
            hash_ok = not (
                report["scored_for_active_hash"] == 0
                and report["scores_on_other_hashes"] > 0
            )
            checks.append((
                "Today score generation",
                hash_ok,
                (
                    f"shortlist {report['tiers']['shortlist']} · "
                    f"watchlist {report['tiers']['watchlist']}"
                    if hash_ok
                    else "active config_hash has 0 scores — run rescore --all"
                ),
            ))
        except Exception as exc:  # noqa: BLE001 — doctor must not crash
            checks.append(("Today diagnosis", False, str(exc)))

    from radar.qa.today import resolve_hermes_binary

    hermes = resolve_hermes_binary()
    checks.append((
        "hermes binary", bool(hermes),
        hermes or "optional — Today QA falls back to rules only",
    ))

    try:
        usage = shutil.disk_usage(db_path.parent if db_path.parent.exists() else Path("."))
        free_mb = usage.free // (1024 * 1024)
        checks.append(("disk space", free_mb > 500, f"{free_mb} MB free"))
    except OSError as exc:
        checks.append(("disk space", False, str(exc)))

    sa = os.environ.get("GOOGLE_SA_JSON")
    if sa:
        checks.append(("google service account", Path(sa).is_file(), sa))

    if ctx.obj["json"]:
        _emit([{"check": c, "ok": ok, "detail": d} for c, ok, d in checks], True)
    else:
        width = max(len(c) for c, _, _ in checks)
        for name, ok, detail in checks:
            click.echo(f"{'✅' if ok else '❌'}  {name.ljust(width)}  {detail}")

    required_ok = all(
        ok for name, ok, detail in checks
        if detail == "required"
        or detail.startswith("required when")
    )
    # Poisoned Fund Criteria is also fatal for a useful Today list.
    fund_ok = all(
        ok for name, ok, _ in checks if name == "Fund Criteria last-good"
    )
    sys.exit(EXIT_OK if required_ok and fund_ok else EXIT_PARTIAL)


def main() -> None:
    load_env_file()
    cli(obj={})


if __name__ == "__main__":
    main()
