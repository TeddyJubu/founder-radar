from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
def test_root_update_inputs_are_protected():
    install = (ROOT / "deploy/install.sh").read_text()
    update = (ROOT / "deploy/update-from-main.sh").read_text()
    unit = (ROOT / "deploy/founder-radar-update.service").read_text()
    assert 'install -d -o root -g root -m 755 "$ROOT"' in install
    assert 'chown -R root:root "$APP_DIR"' in install
    assert 'sudo -H -u "$APP_USER" git' not in update
    assert 'unsafe root update input' in update
    assert 'ExecStart=/usr/local/libexec/founder-radar/update-from-main.sh' in unit
    assert 'install -o root -g root -m 755 "$HERE/update-from-main.sh"' in install

def test_production_dependencies_are_locked():
    install = (ROOT / "deploy/install.sh").read_text()
    assert '--require-hashes -r deploy/requirements.lock' in install
    assert '--no-deps -e .' in install

def test_scratch_sheet_requires_marker_before_mutating():
    from tests.integration.conftest import blank
    class Untagged:
        def sheets(self): return {"Outreach": 1}
        def batch_requests(self, requests): raise AssertionError("must not mutate")
        def add_tabs(self, tabs): raise AssertionError("must not mutate")
    import pytest
    with pytest.raises(ValueError, match="scratch"):
        blank(Untagged())


def test_update_refuses_untrusted_parent_before_git(tmp_path):
    import os, subprocess
    tools = tmp_path / "bin"
    tools.mkdir()
    for name, body in {
        "id": "echo 0",
        "stat": "echo 1000",
        "git": 'touch "$GIT_SENTINEL"; exit 99',
    }.items():
        executable = tools / name
        executable.write_text("#!/bin/sh\n" + body + "\n")
        executable.chmod(0o755)
    sentinel = tmp_path / "git-ran"
    env = {"PATH": str(tools) + ":/usr/bin:/bin", "HOME": str(tmp_path),
           "ROOT": str(tmp_path), "APP_DIR": str(tmp_path / "app"),
           "GIT_SENTINEL": str(sentinel)}
    result = subprocess.run(["/bin/bash", str(ROOT / "deploy/update-from-main.sh")],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "unsafe root update input" in result.stderr
    assert not sentinel.exists()

def test_locked_requirements_match_uv_artifacts():
    import tomllib
    packages = {p["name"]: p for p in tomllib.loads((ROOT / "uv.lock").read_text())["package"]}
    for line in (ROOT / "deploy/requirements.lock").read_text().splitlines():
        if line.startswith("#"): continue
        spec, *hashes = line.split()
        name, version = spec.split("==")
        pkg = packages[name]
        assert version == pkg["version"]
        available = {a["hash"] for a in pkg.get("wheels", [])}
        if "sdist" in pkg: available.add(pkg["sdist"]["hash"])
        assert {h.removeprefix("--hash=") for h in hashes} == available


def test_installer_refuses_untrusted_venv_before_launcher(tmp_path):
    import subprocess
    script = (ROOT / "deploy/install.sh").read_text()
    assert "assert_trusted_install_input" in script
    checks = script.split("# BEGIN trusted install checks")[1].split("# END trusted install checks")[0]
    tools = tmp_path / "bin"
    tools.mkdir()
    for name, body in {
        "stat": 'case "$*" in *venv*) echo 1000;; *) echo 0;; esac',
        "find": "exit 0",
    }.items():
        executable = tools / name
        executable.write_text("#!/bin/sh\n" + body + "\n")
        executable.chmod(0o755)
    app = tmp_path / "app"
    app.mkdir()
    venv = tmp_path / "venv"
    venv.mkdir()
    launcher = venv / "pip"
    sentinel = tmp_path / "executed"
    launcher.write_text('#!/bin/sh\ntouch "' + str(sentinel) + '"\n')
    launcher.chmod(0o755)
    payload = 'set -eu\nAPP_DIR="' + str(app) + '"\nVENV="' + str(venv) + '"\n' + checks + '\n"' + str(launcher) + '"\n'
    result = subprocess.run(["/bin/bash", "-c", payload],
                            env={"PATH": str(tools) + ":/usr/bin:/bin"},
                            text=True, capture_output=True)
    assert result.returncode == 1
    assert "untrusted existing installation" in result.stderr
    assert not sentinel.exists()
