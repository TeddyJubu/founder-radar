"""FR-9.7 — hermes-ship.sh is the only merge door after review + test.

The person who asked for the fix is not an engineer. These tests are the
machine gate: a dirty tree, a scoring diff, or a branch that is not
`hermes/fix-*` must not touch live main.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHIP_SH = REPO / "deploy" / "hermes-ship.sh"
VENV_PYTHON = REPO / ".venv" / "bin" / "python"


def _git(cwd, env, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env,
        capture_output=True, text=True, check=check,
    )


def _clean_os_env():
    skip = {"PYTEST_CURRENT_TEST", "PYTEST_ADDOPTS"}
    return {
        k: v for k, v in os.environ.items()
        if not k.startswith("PYTEST_") and k not in skip
    }


def _layout(tmp_path):
    """Bare origin + live checkout + hermes/fix-* worktree with one commit."""
    if shutil.which("git") is None:
        pytest.skip("git is not available")

    origin = tmp_path / "origin.git"
    checkout = tmp_path / "app"
    worktree = tmp_path / "worktrees" / "fix-test"
    root = tmp_path / "root"
    (root / "logs").mkdir(parents=True)
    worktree.parent.mkdir(parents=True)

    env = {
        **_clean_os_env(),
        "ROOT": str(root),
        "APP_DIR": str(checkout),
        "RADAR_PYTHON": str(VENV_PYTHON if VENV_PYTHON.is_file() else "python3"),
        "RADAR_SHIP_LOG": str(root / "logs" / "ship.log"),
        "RADAR_SHIP_LOCK": str(tmp_path / "ship.lock"),
        "RADAR_HERMES_SCAN_ACTIVE": "0",
        "RADAR_SHIP_SKIP_INSTALL": "1",
        "GIT_AUTHOR_NAME": "radar-test",
        "GIT_AUTHOR_EMAIL": "radar-test@example.test",
        "GIT_COMMITTER_NAME": "radar-test",
        "GIT_COMMITTER_EMAIL": "radar-test@example.test",
    }

    _git(tmp_path, env, "init", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    _git(tmp_path, env, "clone", str(origin), str(seed))
    (seed / "README").write_text("live\n")
    _git(seed, env, "add", "README")
    _git(seed, env, "commit", "-m", "seed")
    _git(seed, env, "push", "origin", "main")
    _git(tmp_path, env, "clone", str(origin), str(checkout))
    _git(checkout, env, "worktree", "add", "-b", "hermes/fix-test", str(worktree))

    (worktree / "tests").mkdir()
    (worktree / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n"
    )
    (worktree / "adapter.py").write_text("# parse fix\n")
    _git(worktree, env, "add", "tests/test_ok.py", "adapter.py")
    _git(worktree, env, "commit", "-m", "fix adapter parse")

    return env, checkout, worktree, root


def _ship(env, worktree, extra=None):
    run_env = dict(env)
    if extra:
        run_env.update(extra)
    return subprocess.run(
        ["bash", str(SHIP_SH), str(worktree)],
        env=run_env, capture_output=True, text=True, timeout=60,
    )


def _combined(done, root):
    log = (root / "logs" / "ship.log")
    log_text = log.read_text() if log.exists() else ""
    return done.stdout + done.stderr + log_text


def test_hermes_ship_script_is_executable():
    assert SHIP_SH.is_file()
    assert os.access(SHIP_SH, os.X_OK)
    syntax = subprocess.run(["bash", "-n", str(SHIP_SH)], capture_output=True)
    assert syntax.returncode == 0, syntax.stderr


def test_hermes_ship_dry_run_leaves_live_main_untouched(tmp_path):
    env, checkout, worktree, root = _layout(tmp_path)
    before = _git(checkout, env, "rev-parse", "HEAD").stdout.strip()

    done = _ship(env, worktree, extra={"RADAR_SHIP_DRY_RUN": "1"})
    assert done.returncode == 0, _combined(done, root)
    assert "dry-run" in _combined(done, root)
    assert _git(checkout, env, "rev-parse", "HEAD").stdout.strip() == before
    assert worktree.is_dir()


def test_hermes_ship_merges_when_review_and_tests_already_passed(tmp_path):
    env, checkout, worktree, root = _layout(tmp_path)
    want = _git(worktree, env, "rev-parse", "HEAD").stdout.strip()

    done = _ship(env, worktree)
    assert done.returncode == 0, _combined(done, root)
    assert _git(checkout, env, "rev-parse", "HEAD").stdout.strip() == want
    assert "shipped hermes/fix-test" in _combined(done, root)
    assert not worktree.exists()
    origin_head = _git(checkout, env, "rev-parse", "origin/main").stdout.strip()
    assert origin_head == want


def test_hermes_ship_refuses_a_scoring_diff(tmp_path):
    env, checkout, worktree, root = _layout(tmp_path)
    before = _git(checkout, env, "rev-parse", "HEAD").stdout.strip()
    score = worktree / "radar" / "score"
    score.mkdir(parents=True)
    (score / "gates.py").write_text("THRESHOLD = 99\n")
    _git(worktree, env, "add", "radar/score/gates.py")
    _git(worktree, env, "commit", "-m", "retune gates")

    done = _ship(env, worktree, extra={"RADAR_SHIP_DRY_RUN": "1"})
    assert done.returncode != 0
    assert "radar/score/" in _combined(done, root)
    assert _git(checkout, env, "rev-parse", "HEAD").stdout.strip() == before


def test_hermes_ship_refuses_a_dirty_worktree(tmp_path):
    env, checkout, worktree, root = _layout(tmp_path)
    before = _git(checkout, env, "rev-parse", "HEAD").stdout.strip()
    (worktree / "adapter.py").write_text("# uncommitted\n")

    done = _ship(env, worktree, extra={"RADAR_SHIP_DRY_RUN": "1"})
    assert done.returncode != 0
    assert "dirty" in _combined(done, root)
    assert _git(checkout, env, "rev-parse", "HEAD").stdout.strip() == before


def test_hermes_ship_refuses_when_not_ahead_of_live_main(tmp_path):
    env, checkout, worktree, root = _layout(tmp_path)
    _git(worktree, env, "checkout", "--detach", "HEAD~1")
    _git(worktree, env, "checkout", "-B", "hermes/fix-stale")

    done = _ship(env, worktree, extra={"RADAR_SHIP_DRY_RUN": "1"})
    assert done.returncode != 0
    assert "no commits ahead" in _combined(done, root)


def test_hermes_ship_refuses_the_wrong_branch_name(tmp_path):
    env, checkout, worktree, root = _layout(tmp_path)
    other = tmp_path / "worktrees" / "wrong"
    _git(checkout, env, "worktree", "add", "-b", "wip/not-the-door", str(other))

    done = _ship(env, other, extra={"RADAR_SHIP_DRY_RUN": "1"})
    assert done.returncode != 0
    assert "hermes/fix-" in _combined(done, root)


def test_hermes_ship_refuses_while_the_daily_scan_is_running(tmp_path):
    env, checkout, worktree, root = _layout(tmp_path)
    before = _git(checkout, env, "rev-parse", "HEAD").stdout.strip()

    done = _ship(env, worktree, extra={"RADAR_HERMES_SCAN_ACTIVE": "1"})
    assert done.returncode != 0
    assert "daily scan is running" in _combined(done, root)
    assert _git(checkout, env, "rev-parse", "HEAD").stdout.strip() == before


def test_hermes_ship_refuses_a_secret_file(tmp_path):
    env, checkout, worktree, root = _layout(tmp_path)
    (worktree / ".env").write_text("LLM_API_KEY=nope\n")
    _git(worktree, env, "add", "-f", ".env")
    _git(worktree, env, "commit", "-m", "oops secret")

    done = _ship(env, worktree, extra={"RADAR_SHIP_DRY_RUN": "1"})
    assert done.returncode != 0
    assert "secret" in _combined(done, root)
