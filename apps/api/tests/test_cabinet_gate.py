"""Authenticated-cabinet states gate tests (Area C, DARK).

Pure-scorer layer, mirroring the data/first_paint gates: a hand-built observation
dict → verdict, no browser. ABSTAIN unless authenticated, WAIVE when a populated
collection is present (a real list legitimately has no empty-state), FAIL with the
named class for each defect (no-empty-state, stuck-skeleton).

Also pins the R-04 single-source contract: the populated-collection floor MUST be
``data_gate.MIN_ROWS`` (not a re-declared inline number).
"""

from __future__ import annotations

from omnia_api.services import cabinet_gate, data_gate
from omnia_api.services.cabinet_gate import (
    NO_EMPTY_STATE,
    STUCK_SKELETON,
    evaluate_observation,
)

# ── R-04 single-source constant contract ───────────────────────────────────────


def test_cabinet_gate_width_is_desktop() -> None:
    """The cabinet is an authenticated desktop surface (GATE_WIDTH=1440)."""
    assert cabinet_gate.GATE_WIDTH == 1440


# ── pure scorer helpers ─────────────────────────────────────────────────────────


def _obs(**over: object) -> dict:
    """A brand-new cabinet that onboards correctly (empty-state present, no rows)."""
    obs = {
        "rows": 0,
        "has_empty": True,
        "has_checklist": False,
        "has_skeleton": False,
    }
    obs.update(over)
    return obs


# ── ABSTAIN: never judge the login wall ─────────────────────────────────────────


def test_unrendered_abstains() -> None:
    """rendered=False (unauthenticated harness / render error) → ABSTAIN, no class."""
    report = evaluate_observation({}, rendered=False)
    assert not report.rendered
    assert not report.passed  # ABSTAIN is not a pass
    assert report.classes == ()


# ── WAIVE: a populated list legitimately has no empty-state ─────────────────────


def test_populated_collection_waives() -> None:
    """A real list with rows ≥ MIN_ROWS passes even with no empty-state/checklist."""
    report = evaluate_observation(
        _obs(rows=data_gate.MIN_ROWS, has_empty=False, has_checklist=False)
    )
    assert report.passed
    assert report.classes == ()
    assert report.rendered
    assert report.subscore()["detail"]["populated"] is True


def test_populated_collection_waives_even_with_skeleton() -> None:
    """A populated cabinet is alive — a leftover skeleton next to real rows WAIVES."""
    report = evaluate_observation(
        _obs(rows=data_gate.MIN_ROWS + 4, has_empty=False, has_skeleton=True)
    )
    assert report.passed


def test_thin_collection_is_not_populated() -> None:
    """Below the MIN_ROWS floor a cabinet still needs an onboarding surface."""
    report = evaluate_observation(
        _obs(rows=data_gate.MIN_ROWS - 1, has_empty=False, has_checklist=False)
    )
    assert not report.passed
    assert NO_EMPTY_STATE in report.classes


# ── pass: onboarding surfaces ───────────────────────────────────────────────────


def test_empty_state_present_passes() -> None:
    """A brand-new cabinet that shows an empty-state passes (it onboards)."""
    report = evaluate_observation(_obs(has_empty=True, has_checklist=False))
    assert report.passed
    assert report.classes == ()


def test_checklist_present_passes() -> None:
    """A brand-new cabinet that shows an onboarding checklist passes."""
    report = evaluate_observation(_obs(has_empty=False, has_checklist=True))
    assert report.passed
    assert report.classes == ()


# ── FAIL: each defect fails with its named class ────────────────────────────────


def test_no_empty_no_checklist_fails_no_empty_state() -> None:
    """0 rows, no empty-state, no checklist = a dead first screen."""
    report = evaluate_observation(
        _obs(rows=0, has_empty=False, has_checklist=False)
    )
    assert not report.passed
    assert NO_EMPTY_STATE in report.classes


def test_skeleton_present_fails_stuck_skeleton() -> None:
    """A skeleton still mounted after settle = a screen that never resolved."""
    report = evaluate_observation(
        _obs(rows=0, has_empty=True, has_skeleton=True)
    )
    assert not report.passed
    assert STUCK_SKELETON in report.classes
    # the empty-state is present, so no-empty-state must NOT also fire.
    assert NO_EMPTY_STATE not in report.classes


def test_both_defects_named_in_canonical_order() -> None:
    """Dead screen AND stranded skeleton → both classes, in CHECKS order."""
    report = evaluate_observation(
        _obs(rows=0, has_empty=False, has_checklist=False, has_skeleton=True)
    )
    assert not report.passed
    assert report.classes == (NO_EMPTY_STATE, STUCK_SKELETON)


# ── report shape matches the rendered-gate contract ─────────────────────────────


def test_subscore_shape_matches_rendered_gate_contract() -> None:
    """The subscore carries the rendered-gate keys the gauntlet adapter reads."""
    report = evaluate_observation(_obs())
    sub = report.subscore()
    assert sub["gate"] == "cabinet"
    assert sub["rendered"] is True
    assert sub["passed"] is True
    assert isinstance(sub["classes"], list)
    assert sub["min_rows"] == data_gate.MIN_ROWS


def test_classes_are_a_subset_of_checks() -> None:
    """Every emitted class is a known check id (registry vocabulary)."""
    report = evaluate_observation(
        _obs(rows=0, has_empty=False, has_checklist=False, has_skeleton=True)
    )
    assert set(report.classes).issubset(set(cabinet_gate.CHECKS))
