"""The schema must make a GDPR mistake impossible, not merely unlikely.

Written before the pipeline exists, on purpose: the temptation to add a
"convenient" date-of-birth column arrives later, when Companies House is
already handing one over on every officers response.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

FORBIDDEN = {
    "email", "phone", "address", "postcode", "date_of_birth",
    "dob_month", "dob_year", "nationality", "country_of_residence",
}

REPO = Path(__file__).resolve().parents[2]


def test_founder_table_stores_no_sensitive_fields(db):
    cols = {r["name"] for r in db.execute("PRAGMA table_info(founder)")}
    assert cols, "founder table missing"
    assert not (cols & FORBIDDEN), f"forbidden columns present: {cols & FORBIDDEN}"


def test_migration_creates_every_table(db):
    expected = {
        "company", "company_source", "identifier", "observation", "founder",
        "signal", "score", "score_component", "score_history", "user_field",
        "merge_event", "run", "run_source", "fetch_log", "llm_cache",
        "quarantine", "config_snapshot", "postcode_region", "placeholder_name",
        "formation_agent_address", "suppression", "sheet_row_state", "_meta",
    }
    assert expected <= db.tables()


def test_companies_house_number_survives_leading_zeros(db):
    """CH numbers are TEXT. `00445790` cast to int becomes 445790 — a different
    company, or none at all."""
    from radar.store.db import now_iso

    db.execute(
        """INSERT INTO company(id, canonical_name, norm_key, companies_house_no,
                               first_seen, last_seen, created_at, updated_at)
           VALUES ('01','Acme','acme','00445790',?,?,?,?)""",
        (now_iso(),) * 4,
    )
    assert db.scalar("SELECT companies_house_no FROM company WHERE id='01'") == "00445790"


def test_total_funding_distinguishes_null_from_zero(db):
    """NULL (unknown) and 0 (known none) are different facts all the way to
    scoring, and collapsing them is how a company with no data scores well."""
    from radar.store.db import now_iso

    ts = now_iso()
    db.executemany(
        """INSERT INTO company(id, canonical_name, norm_key, total_funding_gbp,
                               first_seen, last_seen, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        [("a", "Unknown Co", "unknownco", None, ts, ts, ts, ts),
         ("b", "Bootstrapped Co", "bootstrappedco", 0.0, ts, ts, ts, ts)],
    )
    assert db.scalar("SELECT total_funding_gbp FROM company WHERE id='a'") is None
    assert db.scalar("SELECT total_funding_gbp FROM company WHERE id='b'") == 0.0


BANNED_SCORERS = ("token_set_ratio", "partial_ratio", "WRatio")


def test_banned_fuzzy_scorers_appear_nowhere():
    """These return 100 for a subset and will silently merge every
    parent/subsidiary pair. The build plan asks for this as a CI grep."""
    hits: list[str] = []
    for path in (REPO / "radar").rglob("*.py"):
        text = path.read_text()
        for name in BANNED_SCORERS:
            # The ban is on *calling* them; naming them in a comment that
            # explains the ban is fine.
            for line in text.splitlines():
                if name in line and not line.lstrip().startswith("#"):
                    hits.append(f"{path.relative_to(REPO)}: {line.strip()}")
    assert not hits, "banned rapidfuzz scorers used:\n" + "\n".join(hits)


NONE_IS_NOT_ZERO = re.compile(r"sub_score\s+or\s+0|or\s+0\b.*sub_score")


def test_sub_score_is_never_coerced_to_zero():
    """`None` means "we have no data"; `0` means "we have data and it's bad".

    Collapsing them lets a company with one known attribute score as though the
    other four were terrible — or as though they were fine. The test plan asks
    for this as a CI grep.
    """
    score_dir = REPO / "radar" / "score"
    if not score_dir.is_dir():
        pytest.skip("scoring package not built yet")
    hits = [
        f"{path.relative_to(REPO)}:{n}: {line.strip()}"
        for path in score_dir.rglob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if NONE_IS_NOT_ZERO.search(line) and not line.lstrip().startswith("#")
    ]
    assert not hits, "unknown coerced to zero:\n" + "\n".join(hits)


def test_no_stray_secrets_are_tracked_by_git():
    """The Google service-account key travelled through a chat thread. It must
    never be committed, even accidentally."""
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=False
    ).stdout.splitlines()
    bad = [f for f in out
           if "service-account" in f or f.startswith("founder-finder-")
           or f.endswith("google-sa.json") or f == ".env"]
    assert not bad, f"secret files tracked by git: {bad}"
