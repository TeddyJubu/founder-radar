"""Shared machinery for the `perf` suite (09-test-plan §8).

Three rules, because a flaky performance test is worse than no performance
test and gets muted the same week it lands:

* **Deterministic workloads.** No network, no AI, no clock-dependent data.
  The same corpus every run, built from the same factories the unit tests use.
* **Best of N, not a single sample.** Wall time on a shared box is a noisy
  measurement of a quiet quantity. The fastest of three runs is the closest
  thing to "how long does this take when nothing else is happening".
* **Ceilings with room in them.** Every budget here is the published NFR times
  an explicit allowance, and the allowance is written down next to it. The
  measured number is attached to the test as a property either way, so a slow
  drift is visible long before the ceiling is hit.
"""

from __future__ import annotations

import time
from typing import Callable

import pytest


def best_of(fn: Callable[[], object], runs: int = 3) -> float:
    """Fastest wall-clock time over `runs` calls, in seconds.

    The first call also warms every lazy import the code path does on the way
    through, which on this codebase is most of them — the CLI imports its
    implementation inside the command body on purpose.
    """
    timings: list[float] = []
    for _ in range(runs):
        started = time.perf_counter()
        fn()
        timings.append(time.perf_counter() - started)
    return min(timings)


@pytest.fixture
def report(record_property, capsys):
    """Record a measurement on the test and print it.

    `pytest -m perf -s` then reads as a table of numbers rather than a wall of
    dots, which is the only way anyone notices a budget being half-consumed.
    """

    def _report(name: str, seconds: float, budget: float) -> None:
        record_property(name, round(seconds, 4))
        with capsys.disabled():
            share = (seconds / budget * 100) if budget else 0.0
            print(f"\n  {name}: {seconds:.3f}s  ({share:.0f}% of the {budget:g}s budget)")

    return _report
