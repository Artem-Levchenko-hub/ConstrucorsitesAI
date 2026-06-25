"""Unit tests for the regression diff logic (G008)."""

from __future__ import annotations

from omnia_api.services.regression_registry import find_regressions, merge_passing


def test_broken_feature_is_a_regression() -> None:
    report = find_regressions(
        previously_passing={"login", "chat", "checkout"},
        current_passing={"login", "checkout"},   # chat broke
        current_run={"login", "chat", "checkout"},
    )
    assert report.regressed == ["chat"]
    assert report.ok is False


def test_feature_not_rerun_is_not_a_regression() -> None:
    # chat wasn't exercised this build — absence is not failure.
    report = find_regressions(
        previously_passing={"login", "chat"},
        current_passing={"login"},
        current_run={"login"},   # chat not run
    )
    assert report.regressed == []
    assert report.ok is True


def test_new_feature_is_reported_not_regressed() -> None:
    report = find_regressions(
        previously_passing={"login"},
        current_passing={"login", "chat"},
        current_run={"login", "chat"},
    )
    assert report.regressed == []
    assert report.newly_passing == ["chat"]
    assert report.still_passing == ["login"]


def test_baseline_is_high_water_mark() -> None:
    base = merge_passing({"login", "chat"}, {"login", "checkout"})
    assert base == {"login", "chat", "checkout"}


def test_clean_build_is_ok() -> None:
    report = find_regressions(
        previously_passing={"login", "chat"},
        current_passing={"login", "chat"},
        current_run={"login", "chat"},
    )
    assert report.ok is True
    assert report.regressed == []
