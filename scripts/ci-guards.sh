#!/usr/bin/env bash
#
# The two greps from 09-test-plan §8. Cheap, and worth having as a CI step in
# their own right: they need nothing installed, so a pull request that
# reintroduces either mistake goes red in seconds rather than after the whole
# suite has been provisioned.
#
#   bash scripts/ci-guards.sh
#
# Both are also asserted from pytest — `test_banned_fuzzy_scorers_appear_nowhere`
# and `test_sub_score_is_never_coerced_to_zero` in
# tests/unit/test_schema_privacy.py — so neither can be lost by someone dropping
# this step from a workflow. `test_ci_guard_script_runs_both_greps_and_passes`
# in tests/unit/test_operations.py keeps this file honest in the other
# direction.
#
# Both guards ignore comment lines. The ban is on *calling* these things;
# writing down why they are banned is the opposite of the problem.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Drop `path:line:   # ...` — a comment that explains the rule is not a breach.
drop_comments() { grep -Ev '^[^:]+:[0-9]+:[[:space:]]*#'; }

fail=0

# Guard 1 — the fuzzy scorers that return 100 for a subset.
#
# token_set_ratio("Acme Robotics", "Acme Robotics Automotive Division") is 100.
# Use one of these and every parent/subsidiary pair in the country merges into
# one row, silently, forever. token_sort_ratio is the one that is safe.
if grep -rnE --include='*.py' 'token_set_ratio|partial_ratio|WRatio' radar/ \
     | drop_comments; then
  echo "ci-guards: banned fuzzy scorer above — use token_sort_ratio" >&2
  fail=1
fi

# Guard 2 — None is not 0.
#
# An unknown attribute stays out of the denominator; it does not score zero.
# `sub_score or 0` turns "we have no idea" into "we checked, it's bad", which
# is how a company nobody knows anything about reaches the shortlist.
if grep -rnE --include='*.py' 'sub_score or 0|or 0\b.*sub_score' radar/score/ \
     | drop_comments; then
  echo "ci-guards: None coerced to 0 above — None is not 0" >&2
  fail=1
fi

if [ "$fail" -eq 0 ]; then
  echo "ci-guards: both guards clean"
fi
exit "$fail"
