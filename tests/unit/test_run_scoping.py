"""`--fund` and `--since` must scope the run, not decorate the log line.

Both options were accepted by the CLI, threaded into `run_pipeline`, and then
dropped before they reached the code that would honour them: `--fund` reached
only the run-log label while the scoring loop still scored all four funds, and
`--since` was never copied into `FetchContext` even though nine adapters read
it. A run exited 0 and looked normal, so nothing failed.

Every test here is written to fail against that behaviour. A test that merely
asserts "the run completes" would have passed the whole time and is worthless
as a regression guard, which is the point these tests exist to make.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from radar.pipeline import evaluate, funds_in_scope, run_pipeline, score_company
from tests.factories import C, store_company

TODAY = date(2026, 8, 8)


class FakeHttp:
    """Every adapter sees a quiet source rather than a crash."""

    def get(self, url, **kw):  # noqa: ARG002 - the adapter protocol
        return SimpleNamespace(ok=True, status=200, text="",
                               json=lambda: {})


# --------------------------------------------------------------------- --fund


def test_fund_scope_writes_only_that_fund(db, config):
    """The regression: scoring scoped to one fund must write one fund's rows.

    Against the old code every one of the four funds got a row regardless of
    `--fund`, so this asserts the exact thing that silently did not happen.
    """
    cid = store_company(db, C(age_months=6))

    written = score_company(db, cid, config, today=TODAY, fund_key="northstar")

    keys = {r["fund_key"] for r in db.query("SELECT DISTINCT fund_key FROM score")}
    assert keys == {"northstar"}
    assert written == len(db.query("SELECT id FROM score"))


def test_fund_scope_upserts_rather_than_forking_config_hash(db, config):
    """A scoped run must refresh the full run's row, not shadow it.

    The tempting implementation — trim `cfg.funds` to the one fund — changes
    `cfg.hash()`, and `config_hash` is part of the `ON CONFLICT` target. That
    version leaves two generations of northstar rows behind. This pins the
    filter-at-the-loop choice that avoids it.
    """
    cid = store_company(db, C(age_months=6))

    score_company(db, cid, config, today=TODAY)                       # full run
    before = db.query("SELECT config_hash FROM score WHERE fund_key = 'northstar'")
    score_company(db, cid, config, today=TODAY, fund_key="northstar")  # scoped
    after = db.query("SELECT config_hash FROM score WHERE fund_key = 'northstar'")

    assert len(before) == len(after) == 1
    assert before[0]["config_hash"] == after[0]["config_hash"]


def test_fund_scope_leaves_other_funds_alone(db, config):
    """Scoping refreshes one fund; it does not retire the other three."""
    cid = store_company(db, C(age_months=6))

    score_company(db, cid, config, today=TODAY)
    all_funds = {r["fund_key"] for r in db.query("SELECT DISTINCT fund_key FROM score")}
    score_company(db, cid, config, today=TODAY, fund_key="northstar")

    still = {r["fund_key"] for r in db.query("SELECT DISTINCT fund_key FROM score")}
    assert still == all_funds
    assert len(all_funds) > 1, "fixture must cover more than one fund to prove this"


def test_unknown_fund_key_raises_rather_than_scoring_nothing(db, config):
    """A typo must not read as a run that legitimately matched nothing.

    Filtering to an unknown key yields an empty fund list, which would exit 0
    with zero shortlisted — indistinguishable from a quiet day.
    """
    cid = store_company(db, C(age_months=6))

    with pytest.raises(ValueError, match="unknown fund"):
        score_company(db, cid, config, today=TODAY, fund_key="northstarr")

    with pytest.raises(ValueError, match="unknown fund"):
        run_pipeline(db, fund_key="northstarr", dry_run=True, use_llm=False,
                     http=FakeHttp(), config=config, now=TODAY)

    assert db.scalar("SELECT COUNT(*) FROM score") == 0


def test_fund_scope_reaches_the_gate_reject_path(db, config):
    """The early return for a gate failure builds one row *per fund* too.

    It is a separate comprehension from the main loop, so it needs its own
    assertion — scoping the loop but not the reject path is an easy miss.
    """
    cid = store_company(db, C(age_months=400))       # far past the age gate

    score_company(db, cid, config, today=TODAY, fund_key="northstar")

    rows = db.query("SELECT fund_key, tier FROM score")
    assert {r["fund_key"] for r in rows} == {"northstar"}
    assert {r["tier"] for r in rows} == {"reject"}


def test_evaluate_is_unscoped_by_default(db, config):
    """The default must stay "every fund" — scoping is opt-in."""
    scores = evaluate(C(age_months=6), config, today=TODAY)
    assert {s.fund_key for s in scores} == {f.key for f in config.funds}


def test_funds_in_scope_returns_every_fund_for_none(config):
    assert funds_in_scope(config, None) == list(config.funds)


# -------------------------------------------------------------------- --since


@pytest.mark.parametrize("since", [date(2026, 7, 1), None])
def test_since_reaches_the_fetch_context(db, config, monkeypatch, since):
    """`--since` must arrive in `FetchContext`, where nine adapters read it.

    Asserting through `run_pipeline` rather than `fetch_stage` covers both
    halves of the old break: the run dropped it on the way to the stage, and
    the stage dropped it on the way to the context.
    """
    captured: dict = {}

    def fake_fetch_all(ctx, **kw):  # noqa: ARG001 - keys/db/run_id unused here
        captured["since"] = ctx.since
        return SimpleNamespace(items=[], sources=[], status="ok")

    monkeypatch.setattr("radar.sources.fetch_all", fake_fetch_all)

    run_pipeline(db, since=since, dry_run=True, use_llm=False,
                 http=FakeHttp(), config=config, now=TODAY)

    assert captured["since"] == since


def test_cli_since_arrives_as_a_date_not_a_string(monkeypatch, tmp_path):
    """`--since` was a bare string option, which only worked while it was ignored.

    `after()` compares it against `published_at` and Companies House subtracts
    it from today — both raise on a `str`. Honouring the option and parsing it
    are the same fix; testing one without the other ships a crash.
    """
    from click.testing import CliRunner

    from radar.cli import cli

    seen: dict = {}

    def fake_run(_db, **kw):
        seen.update(kw)
        return SimpleNamespace(status="ok", summary=lambda: {})

    monkeypatch.setattr("radar.pipeline.run_pipeline", fake_run)
    result = CliRunner().invoke(
        cli, ["--db", str(tmp_path / "r.db"), "run", "--since", "2026-07-01",
              "--dry-run", "--no-llm"], obj={})

    assert result.exit_code == 0, result.output
    assert seen["since"] == date(2026, 7, 1)
    assert type(seen["since"]) is date, "a datetime here breaks date arithmetic"


def test_cli_rejects_an_unknown_fund_cleanly(monkeypatch, tmp_path):
    """A typo should be a usage error, not a traceback."""
    from click.testing import CliRunner

    from radar.cli import cli

    result = CliRunner().invoke(
        cli, ["--db", str(tmp_path / "r.db"), "run", "--fund", "northstarr",
              "--dry-run", "--no-llm"], obj={})

    assert result.exit_code == 2                      # click's usage-error code
    assert "unknown fund" in result.output
    assert "Traceback" not in result.output


def test_since_narrows_the_companies_house_window(config):
    """Companies House turns `since` into its `incorporated_from` window.

    The other adapters filter after fetching; Companies House is the one that
    changes what it asks for, so a dropped `since` costs real request budget.
    """
    from radar.sources.base import FetchContext
    from radar.sources.companies_house import CompaniesHouseAdapter

    adapter = CompaniesHouseAdapter()
    ctx = FetchContext(http=None, config=config, db=None, now=TODAY,
                       since=date(2026, 7, 1))
    assert adapter._days_back(ctx) == 38          # 2026-07-01 → 2026-08-08

    assert adapter._days_back(
        FetchContext(http=None, config=config, db=None, now=TODAY)
    ) != 38
