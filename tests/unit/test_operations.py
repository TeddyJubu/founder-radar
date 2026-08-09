"""09-test-plan §7 — operations (FR-9), plus the §8 CI guard script.

These are the cheap tests that catch a deployment which "looks fine" but
silently never runs.

What lives elsewhere, deliberately not duplicated here:

* the §8 greps themselves — `test_banned_fuzzy_scorers_appear_nowhere` and
  `test_sub_score_is_never_coerced_to_zero` in `test_schema_privacy.py`. What
  is added below is the shell form §8 specifies, and a test that it is real.

* `test_every_run_writes_a_run_log_row` (FR-9.2) — `test_pipeline.py`
* `test_every_telegram_command_maps_to_a_cli_command` (FR-9.6) — `test_pipeline.py`
* `test_telegram_allowlist_rejects_unknown_user` (FR-8.4) — `test_pipeline.py`
* `test_sh01_sets_has_share_issue` (FR-1.6) and `test_postcode_to_geography`
  (FR-1.3) — `test_enrich.py`
* `test_unknown_value_policies` (FR-4.6) — `test_scoring.py`
* `test_timer_is_enabled_and_scheduled` (FR-9.1) and
  `test_env_file_is_0600_and_never_logged` (FR-9.5) — `tests/integration/`,
  because both need the VPS.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from radar.store.db import Db

from tests.factories import C, store_company

REPO = Path(__file__).resolve().parents[2]


def _cli(args, **kw):
    from radar.cli import cli

    return CliRunner().invoke(cli, args, obj={}, **kw)


# --------------------------------------------------------- FR-9.3 heartbeat


@pytest.fixture
def telegram_outbox(monkeypatch):
    """Capture what the heartbeat would send. The suite blocks sockets, so the
    alternative to injecting here is not "a real message" but a crash."""
    import radar.notify.telegram as telegram

    sent: list[str] = []

    def _send(text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(telegram, "send_message", _send)
    return sent


def _db_with_last_run(path: Path, *, hours_ago: float, status: str = "ok") -> Db:
    db = Db(path)
    db.migrate()
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    db.execute(
        """INSERT INTO run(started_at, finished_at, mode, status, items_fetched,
                           items_extracted, companies_new, companies_merged,
                           gated_out, shortlisted, llm_calls, llm_cost_usd)
           VALUES (?,?,'daily',?,0,0,0,0,0,0,0,0)""",
        (stamp, stamp, status),
    )
    db.close()
    return db


def test_heartbeat_alerts_when_stale(tmp_path, telegram_outbox):
    """FR-9.3 — a run that stopped happening produces no error of its own,
    because nothing runs to produce one. A second clock is the only defence.

    Driven through `founder-radar status --alert-if-stale 26h`, which is the
    command 08-deployment §4 puts in the systemd unit — not the `python -m`
    entry point, so the thing under test is the thing that ships.
    """
    path = tmp_path / "radar.db"
    _db_with_last_run(path, hours_ago=27)

    result = _cli(["--db", str(path), "status", "--alert-if-stale", "26h"])

    assert len(telegram_outbox) == 1, telegram_outbox
    assert "Stale" in telegram_outbox[0]
    assert "27h" in telegram_outbox[0]
    assert result.exit_code == 1, "an alert must be visible to systemd too"


def test_heartbeat_is_quiet_when_the_run_is_recent(tmp_path, telegram_outbox):
    """The other half: an alert that fires every day is an alert that is muted
    by the second week."""
    path = tmp_path / "radar.db"
    _db_with_last_run(path, hours_ago=3)

    result = _cli(["--db", str(path), "status", "--alert-if-stale", "26h"])

    assert telegram_outbox == []
    assert result.exit_code == 0


def test_heartbeat_treats_a_partial_run_as_no_run(tmp_path, telegram_outbox):
    """`partial` means sources failed. Counting it as proof of life would mask
    a pipeline that fails every source every day."""
    path = tmp_path / "radar.db"
    _db_with_last_run(path, hours_ago=2, status="partial")

    _cli(["--db", str(path), "status", "--alert-if-stale", "26h"])
    assert len(telegram_outbox) == 1


def test_status_without_the_flag_sends_nothing(tmp_path, telegram_outbox):
    """`status` is also a human command. Reading it must never page anyone."""
    path = tmp_path / "radar.db"
    _db_with_last_run(path, hours_ago=99)

    result = _cli(["--db", str(path), "status"])
    assert telegram_outbox == []
    assert result.exit_code == 0


# ------------------------------------------------------------ FR-9.4 backups


def _age(path: Path, days: int) -> None:
    stamp = time.time() - days * 86_400
    os.utime(path, (stamp, stamp))


def test_backup_creates_and_prunes(tmp_path):
    """FR-9.4 — "backed up daily, with 14 days retained" is two promises, and
    the second one is the one that silently stops being true.

    Pruning happens only after the new snapshot is on disk, so a failed backup
    can never be the thing that deletes the last good one.
    """
    db_path = tmp_path / "data" / "radar.db"
    backups = tmp_path / "data" / "backups"
    Db(db_path).migrate()

    assert _cli(["--db", str(db_path), "db", "backup"]).exit_code == 0
    snapshots = list(backups.glob("radar-*.db"))
    assert len(snapshots) == 1
    assert snapshots[0].stat().st_size > 0

    # Age the existing snapshot past the window, under the name it would have
    # had when it was taken — a same-day re-run overwrites rather than accrues.
    old = backups / f"radar-{date.today() - timedelta(days=15)}.db"
    snapshots[0].rename(old)
    _age(old, days=15)

    assert _cli(["--db", str(db_path), "db", "backup"]).exit_code == 0

    kept = sorted(backups.glob("radar-*.db"))
    assert not [f for f in kept if time.time() - f.stat().st_mtime > 14 * 86_400], \
        "a snapshot older than the 14-day window survived"
    assert len(kept) == 1
    assert kept[0].name == f"radar-{date.today()}.db"


def test_backup_keeps_everything_inside_the_window(tmp_path):
    """Retention prunes by age, not by count. A week of history must survive."""
    db_path = tmp_path / "radar.db"
    backups = tmp_path / "backups"
    Db(db_path).migrate()
    backups.mkdir()

    for days in (1, 5, 13):
        recent = backups / f"radar-{date.today() - timedelta(days=days)}.db"
        recent.write_bytes(b"")
        _age(recent, days=days)

    assert _cli(["--db", str(db_path), "db", "backup"]).exit_code == 0
    assert len(list(backups.glob("radar-*.db"))) == 4


def test_backup_never_touches_files_that_are_not_ours(tmp_path):
    """`radar-*.db` only. A backup directory shared with anything else must
    come out the other side untouched."""
    db_path = tmp_path / "radar.db"
    backups = tmp_path / "backups"
    Db(db_path).migrate()
    backups.mkdir()
    stranger = backups / "important-notes.txt"
    stranger.write_text("do not delete")
    _age(stranger, days=400)

    _cli(["--db", str(db_path), "db", "backup"])
    assert stranger.exists()


# ------------------------------------------------- FR-4.7 / NFR-6 sheet edits


def _config_with(**settings):
    """A fresh `Config` with the Settings tab edited. Fresh, not mutated: two
    configs that share a `Settings` object would share a hash."""
    from radar.config.defaults import default_config

    cfg = default_config()
    return cfg.model_copy(
        update={"settings": cfg.settings.model_copy(update=settings)}, deep=True)


def test_sheet_edit_changes_scores_with_no_code_change():
    """FR-4.7 / NFR-6, committed to the client on 9 July: changing a fund's
    criteria is a sheet edit, not a deploy.

    `max_company_age_months` is the one the whole rebuild is about. A company
    two years old is a candidate at 36 months and a reject at 12, and the
    config hash moves with it so the two answers never collide in the score
    table.
    """
    from tests.factories import score_one

    company = C(age_months=24)

    generous = _config_with(max_company_age_months=36)
    strict = _config_with(max_company_age_months=12)

    before = score_one(company, "northstar", generous)
    after = score_one(company, "northstar", strict)

    assert before.tier != after.tier
    assert after.tier == "reject"
    assert after.reject_reason == "max_company_age_months"
    assert before.config_hash != after.config_hash

    # And nothing in `radar/` had to move for that to be true.
    assert generous.hash() == _config_with(max_company_age_months=36).hash()


def test_sheet_edit_to_a_weight_changes_the_ranking():
    """The other half of the 9 July promise: a *weight* edit re-ranks, with no
    code change either."""
    from tests.factories import score_one

    cfg = _config_with()
    climate = C(sector="climate_tech", geography="north_east", age_months=12)
    saas = C(sector="b2b_saas", geography="north_east", age_months=12)

    base_gap = score_one(climate, "northstar", cfg).fund_fit_pct \
        - score_one(saas, "northstar", cfg).fund_fit_pct

    tuned = cfg.model_copy(deep=True)
    tuned.weights.matrix["sector"]["b2b_saas"]["northstar"] = 4
    tuned.weights.matrix["sector"]["climate_tech"]["northstar"] = 0

    tuned_gap = score_one(climate, "northstar", tuned).fund_fit_pct \
        - score_one(saas, "northstar", tuned).fund_fit_pct

    assert base_gap != tuned_gap
    assert cfg.hash() != tuned.hash()


# --------------------------------------------------- NFR-5 adding a source


NEW_ADAPTER = '''
"""A whole new source. One file — this one — and one registry line."""

from datetime import date

from radar.sources.base import RawItem


class TynesideTechAdapter:
    key = "tyneside_tech"
    kind = "news"
    schedule = "daily"
    requires_browser = False
    endpoint = "https://example.test/tyneside/feed.json"

    def fetch(self, ctx):
        yield RawItem(
            source_key=self.key,
            source_url="https://example.test/tyneside/quayside-robotics",
            external_id="quayside-robotics",
            published_at=date(2026, 8, 1),
            title="Quayside Robotics raises \\u00a3900k pre-seed",
            body_text="<html><body><p>Quayside Robotics Ltd has raised "
                      "\\u00a3900k.</p></body></html>",
        )


ADAPTER = TynesideTechAdapter()
'''

# Everything a new source must NOT have to touch. If adding a source means
# editing any of these, the 9 July promise is broken.
SHARED_MODULES = (
    "radar/pipeline.py",
    "radar/resolve/match.py",
    "radar/resolve/merge.py",
    "radar/render/sheet.py",
    "radar/render/digest.py",
    "radar/score/criteria.py",
    "radar/score/fund_fit.py",
    "radar/score/gates.py",
    "radar/score/tiering.py",
    "radar/store/schema.sql",
)


def test_adding_a_source_touches_no_shared_code(db, config, tmp_path, monkeypatch):
    """NFR-5, the client's 9 July promise: "if we add more sources later,
    straightforward to extend".

    Asserted statically rather than against a fixture commit hash — a test that
    depends on a particular commit existing in the history is a test that dies
    at the first rebase. The check is the same one a reviewer would make:

    1. the shared modules name no source, so none of them can need editing;
    2. the registry really is one line per source, and `register()` is public;
    3. a brand-new adapter file, written here and never imported by anything
       in `radar/`, fetches, resolves and scores end to end.
    """
    from radar.sources import REGISTRY, SOURCE_MODULES
    from radar.sources.base import FetchContext, SourceAdapter

    # ---- 1. the shared modules are source-agnostic -----------------------
    known = set(SOURCE_MODULES) - {"companies_house"}   # Track B is a pipeline stage
    for relative in SHARED_MODULES:
        text = (REPO / relative).read_text()
        named = sorted(key for key in known if key in text)
        assert not named, f"{relative} names {named} — adding a source would touch it"

    # ---- 2. the registry is one line per source --------------------------
    for key, module_path in SOURCE_MODULES.items():
        assert module_path == f"radar.sources.{key}", \
            f"{key} does not follow the one-line convention"
        assert (REPO / "radar" / "sources" / f"{key}.py").is_file()
    assert hasattr(REGISTRY, "register"), "registering a source is not a public API"

    # ---- 3. a new file plus that one line is genuinely enough ------------
    module = tmp_path / "tyneside_tech.py"
    module.write_text(NEW_ADAPTER)
    monkeypatch.syspath_prepend(str(tmp_path))
    # The whole diff to `radar/`: this one call, which in a real change is one
    # line in `SOURCE_MODULES`. Undone in `finally` so it cannot leak into the
    # rest of the session — the registry is process-wide.
    REGISTRY.register("tyneside_tech", "tyneside_tech")
    try:
        adapter = REGISTRY["tyneside_tech"]
        assert isinstance(adapter, SourceAdapter), "the protocol is the whole contract"

        from radar.pipeline import resolve_item, score_company
        from radar.sources import fetch_all

        ctx = FetchContext(http=None, config=config, db=db, now=date(2026, 8, 8))
        result = fetch_all(ctx, [adapter], db=db, observed_on=date(2026, 8, 8))
        assert result.source("tyneside_tech").status == "ok"
        assert len(result.items) == 1

        company_id = resolve_item(db, result.items[0], config)
        assert company_id, "the new source produced no company"
        assert db.scalar("SELECT canonical_name FROM company WHERE id = ?",
                         (company_id,)) == "Quayside Robotics raises £900k pre-seed"
        score_company(db, company_id, config, today=date(2026, 8, 8))
        assert db.scalar("SELECT COUNT(*) FROM score WHERE company_id = ?",
                         (company_id,)) > 0
    finally:
        REGISTRY._modules.pop("tyneside_tech", None)
        REGISTRY._cache.pop("tyneside_tech", None)


# ------------------------------------------------- Phase 3 enrichment budget


class CountingCH:
    """A Companies House double that counts requests and answers everything."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    def get(self, url, **kw):                                     # noqa: ARG002
        self.requests.append(url)
        return _Resp(_payload_for(url))


class _Resp:
    def __init__(self, payload, status: int = 200) -> None:
        self._payload = payload
        self.status = status
        self.text = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400

    def json(self):
        return self._payload


def _payload_for(url: str):
    if "filing-history" in url:
        return {"items": []}
    if "persons-with-significant-control" in url:
        return {"items": []}
    if "/officers" in url:
        return {"items": [{
            "name": "LOVELACE, Ada",
            "officer_role": "director",
            "appointed_on": "2026-01-05",
            "links": {"officer": {"appointments": "/officers/abc123/appointments"}},
        }]}
    if "/appointments" in url:
        return {"total_results": 1, "items": []}
    return {}


def _queue(db, count: int) -> None:
    for index in range(count):
        store_company(db, C(
            canonical_name=f"Queued {index}",
            norm_key=f"queued{index}",
            companies_house_no=f"{10_000_000 + index}",
            discovery_route="registry",
            age_months=4,
        ))


def test_enrichment_respects_budget(db):
    """The Phase 3 done-criterion (10-build-plan): the budget counts requests,
    not companies, and running out is a clean stop rather than a crash.

    Companies House *bans* an application for repeated breaches rather than
    throttling it, so an over-eager first run is the most likely way to brick
    the key. Whatever the budget does not reach stays queued with
    `enriched_at IS NULL` for tomorrow — never dropped, never re-fetched twice.
    """
    from radar.enrich import RequestBudget, enrich_companies

    _queue(db, 40)
    http = CountingCH()

    result = enrich_companies(db, http, api_key="test-key",
                              budget=RequestBudget(limit=9))

    assert len(http.requests) <= 9, f"{len(http.requests)} requests against a budget of 9"
    assert result.budget_spent <= result.budget_limit == 9

    # Full enrichment is 4-8 calls per company: nine requests must NOT have
    # been read as nine companies.
    assert result.enriched < 9

    still_queued = db.scalar(
        "SELECT COUNT(*) FROM company WHERE enriched_at IS NULL "
        "AND companies_house_no IS NOT NULL AND merged_into IS NULL")
    assert still_queued > 0
    assert result.queued == still_queued


def test_enrichment_budget_of_zero_makes_no_requests(db):
    """The boundary that matters on the very first run after a key rotation."""
    from radar.enrich import RequestBudget, enrich_companies

    _queue(db, 5)
    http = CountingCH()
    result = enrich_companies(db, http, api_key="test-key", budget=RequestBudget(limit=0))

    assert http.requests == []
    assert result.enriched == 0
    assert result.queued == 5


def test_enrichment_resumes_where_the_budget_stopped(db):
    """A deferred company is deferred, not lost: the next run picks it up and
    does not re-spend pass-1 requests on the ones already checked."""
    from radar.enrich import RequestBudget, enrich_companies

    _queue(db, 12)
    first = CountingCH()
    enrich_companies(db, first, api_key="k", budget=RequestBudget(limit=6))
    after_first = db.scalar(
        "SELECT COUNT(*) FROM company WHERE enriched_at IS NOT NULL")

    second = CountingCH()
    enrich_companies(db, second, api_key="k", budget=RequestBudget(limit=200))
    after_second = db.scalar(
        "SELECT COUNT(*) FROM company WHERE enriched_at IS NOT NULL")

    assert after_second > after_first
    assert db.scalar("SELECT COUNT(*) FROM company WHERE enriched_at IS NULL "
                     "AND companies_house_no IS NOT NULL") == 0
    filings_calls = [u for u in second.requests if "filing-history" in u]
    assert len(filings_calls) < 12, "pass 1 was re-paid for companies already checked"


# --------------------------------------------------------- §8 the CI greps


# The two patterns 09-test-plan §8 asks CI to grep for:
#
#   ! grep -rn "token_set_ratio\|partial_ratio\|WRatio" radar/
#   ! grep -rn "or 0\b.*sub_score\|sub_score or 0" radar/score/
#
# Both are already asserted directly, by `test_banned_fuzzy_scorers_appear_nowhere`
# and `test_sub_score_is_never_coerced_to_zero` in test_schema_privacy.py. What
# is missing is the shell form §8 actually specifies, for a CI job that wants
# the guards before anything is installed — and a test that the shell form is
# real, executable, and still agrees with the Python one.
GUARD_PATTERNS = ("token_set_ratio", "partial_ratio", "WRatio",
                  "sub_score or 0", "or 0\\b.*sub_score")


def test_ci_guard_script_runs_both_greps_and_passes():
    """`scripts/ci-guards.sh` must exist, be executable, carry both §8 greps,
    and pass on the tree as it stands."""
    import subprocess

    script = REPO / "scripts" / "ci-guards.sh"
    assert script.is_file(), "scripts/ci-guards.sh is missing"
    assert os.access(script, os.X_OK), "scripts/ci-guards.sh is not executable"

    body = script.read_text()
    for pattern in GUARD_PATTERNS:
        assert pattern in body, f"{pattern!r} is not guarded by ci-guards.sh"

    done = subprocess.run(["bash", str(script)], cwd=REPO,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr


def test_ci_guard_script_actually_fails_on_a_breach(tmp_path):
    """A guard that never goes red is decoration. Plant one breach of each
    pattern in a throwaway copy of the tree and watch the script catch it."""
    import shutil
    import subprocess

    script = REPO / "scripts" / "ci-guards.sh"
    for relative, line in (("radar/resolve/_probe.py", "s = fuzz.WRatio(a, b)\n"),
                           ("radar/score/_probe.py", "x = component.sub_score or 0\n")):
        sandbox = tmp_path / relative.replace("/", "_")
        sandbox.mkdir()
        shutil.copytree(REPO / "radar", sandbox / "radar",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (sandbox / "scripts").mkdir()
        shutil.copy2(script, sandbox / "scripts" / "ci-guards.sh")
        (sandbox / relative).write_text(line)

        done = subprocess.run(["bash", str(sandbox / "scripts" / "ci-guards.sh")],
                              capture_output=True, text=True, timeout=120)
        assert done.returncode == 1, f"{relative} slipped past the guard"


# ------------------------------------------------------------------ .env


def test_env_file_is_loaded_by_the_cli(tmp_path, monkeypatch):
    """The README's quick start is `cp .env.example .env`, then `doctor`.

    Nothing in the process read that file. The systemd unit loads it with
    `EnvironmentFile=`, so the server was always fine — but anyone following
    the documented local steps filled in a Companies House key and was then
    told the key was missing, which is the exact wall this sits behind.
    """
    from radar.cli import load_env_file

    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        "COMPANIES_HOUSE_API_KEY=abc-123\n"
        'export TELEGRAM_CHAT_ID="98765"\n'
        "MALFORMED_LINE_NO_EQUALS\n"
        "EMPTY_VALUE=\n"
    )
    for name in ("COMPANIES_HOUSE_API_KEY", "TELEGRAM_CHAT_ID", "EMPTY_VALUE"):
        monkeypatch.delenv(name, raising=False)

    assert load_env_file(env) == 2
    assert os.environ["COMPANIES_HOUSE_API_KEY"] == "abc-123"
    assert os.environ["TELEGRAM_CHAT_ID"] == "98765"       # export + quotes
    assert "EMPTY_VALUE" not in os.environ                 # blank is not a value


def test_a_real_environment_variable_beats_the_env_file(tmp_path, monkeypatch):
    """`CH_API_KEY=... founder-radar run` has to keep working, and systemd's
    own EnvironmentFile must not be second-guessed by a stray file on disk."""
    from radar.cli import load_env_file

    env = tmp_path / ".env"
    env.write_text("COMPANIES_HOUSE_API_KEY=from-the-file\n")
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "from-the-shell")

    assert load_env_file(env) == 0
    assert os.environ["COMPANIES_HOUSE_API_KEY"] == "from-the-shell"


def test_a_missing_env_file_is_not_an_error(tmp_path):
    """Most runs have no .env at all — systemd injects the variables."""
    from radar.cli import load_env_file

    assert load_env_file(tmp_path / "nope.env") == 0
