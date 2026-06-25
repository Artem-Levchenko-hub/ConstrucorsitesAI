"""Regression registry (G008 — durable week-long builds).

A week of prompting must COMPOUND, not regress: prompt N+1 must not silently
break the feature prompt N shipped. This registry records which feature-tests
passed for a project and, on every later build, flags any that USED to pass and
now fail — turning "it worked yesterday" into a hard, detectable signal.

The pure diff logic (:func:`find_regressions`) is unit-tested here; persistence is
a thin layer the caller wires to the project store (kept out of the pure core so
the rule is testable without a DB).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RegressionReport:
    regressed: list[str]   # features that passed before and fail now — BLOCK ship
    newly_passing: list[str]
    still_passing: list[str]

    @property
    def ok(self) -> bool:
        """A build is regression-clean iff nothing that worked before is broken."""
        return not self.regressed


def find_regressions(
    previously_passing: set[str],
    current_passing: set[str],
    current_run: set[str],
) -> RegressionReport:
    """Compare a project's prior green features against this run.

    - `previously_passing` — features green in any earlier build.
    - `current_passing`    — features green in THIS build.
    - `current_run`        — features actually EXERCISED this build (so a feature
      that wasn't run is not mistaken for a regression — absence != failure).

    A regression is a feature that was green before, was run again, and is no
    longer green. Features not exercised this run are carried forward untouched.
    """
    # Only features actually re-run can be judged; a feature we didn't test is
    # neither passing nor regressed this round — it keeps its prior state.
    rerun_failed = (previously_passing & current_run) - current_passing
    regressed = sorted(rerun_failed)
    newly_passing = sorted(current_passing - previously_passing)
    still_passing = sorted(previously_passing & current_passing)
    return RegressionReport(
        regressed=regressed,
        newly_passing=newly_passing,
        still_passing=still_passing,
    )


def merge_passing(previously_passing: set[str], current_passing: set[str]) -> set[str]:
    """The new baseline after a build: keep every feature that has ever passed and
    add this run's passes. (A regression is reported separately; the baseline is
    the high-water mark so a flaky drop doesn't quietly lower the bar.)"""
    return set(previously_passing) | set(current_passing)
