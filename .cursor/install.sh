#!/usr/bin/env bash
#
# Cloud Agent install phase for UK Founder Radar.
#
# Idempotent: safe to run repeatedly and against a cached/partial state. It
# only prepares durable, source-derived setup (system packages, the project
# virtualenv, and the Chromium the Today prototype browser suite needs). No
# servers, migrations, or tests run here.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# System packages the base image lacks: the venv module for the pinned Python
# and a compiler toolchain for any wheels built from source. apt install is
# idempotent, so re-runs are cheap no-ops.
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends python3.12-venv build-essential

# Project virtualenv. `python -m venv` is idempotent — it reuses an existing
# .venv rather than rebuilding it.
python3 -m venv .venv
.venv/bin/pip install --upgrade pip

# The full developer surface: runtime + dev (pytest), extract (LLM extraction),
# and browser (playwright) extras. CI installs only [dev]; this environment
# installs everything so both the offline suite and the Today browser suite run.
.venv/bin/pip install -e ".[dev,extract,browser]"

# Chromium + its system libraries for the Today prototype browser tests
# (prototype/TESTING.md). --with-deps needs sudo, which the install phase has.
.venv/bin/python -m playwright install --with-deps chromium

echo "install: founder-radar environment ready"
