"""V4.9 — the beauty-floor gate for the viral pool.

``accept_gauntlet.viral_eligible_from_verdict`` is the bridge from pillar 1 (WOW
design) to pillar 4 (virality): a project may enter the viral pool only when its
shared surface is itself floor-green. These are pure, money-free assertions over
synthetic verdicts — one adversary fixture per way the floor can fail, so the
contract can never silently soften.
"""

from __future__ import annotations

from omnia_api.services import accept_gauntlet
from omnia_api.services.accept_gauntlet import (
    GateVerdict,
    GauntletVerdict,
    viral_eligible_from_verdict,
)


def _gate(gate: str, *, passed: bool, abstained: bool = False) -> GateVerdict:
    return GateVerdict(
        gate=gate,
        passed=passed,
        abstained=abstained,
        classes=() if passed else (f"{gate}-finding",),
        summary=f"{gate} {'pass' if passed else 'fail'}",
        subscore={"gate": gate},
    )


def _verdict(*gates: GateVerdict) -> GauntletVerdict:
    return GauntletVerdict(tuple(gates), render_expected=True)


# --- the happy path -------------------------------------------------------

def test_taste_and_hierarchy_pass_is_eligible():
    v = _verdict(
        _gate(accept_gauntlet.TASTE, passed=True),
        _gate(accept_gauntlet.HIERARCHY, passed=True),
    )
    assert viral_eligible_from_verdict(v) is True


def test_viral_leg_passing_alongside_floor_stays_eligible():
    # When the stranger-cold path also measured first-paint (folded into the
    # viral leg) and it passed, eligibility holds.
    v = _verdict(
        _gate(accept_gauntlet.TASTE, passed=True),
        _gate(accept_gauntlet.HIERARCHY, passed=True),
        _gate(accept_gauntlet.VIRAL, passed=True),
    )
    assert viral_eligible_from_verdict(v) is True


# --- adversary matrix: every way the floor can fail -----------------------

def test_taste_hard_fail_is_not_eligible():
    v = _verdict(
        _gate(accept_gauntlet.TASTE, passed=False),
        _gate(accept_gauntlet.HIERARCHY, passed=True),
    )
    assert viral_eligible_from_verdict(v) is False


def test_hierarchy_hard_fail_is_not_eligible():
    v = _verdict(
        _gate(accept_gauntlet.TASTE, passed=True),
        _gate(accept_gauntlet.HIERARCHY, passed=False),
    )
    assert viral_eligible_from_verdict(v) is False


def test_abstained_floor_leg_is_not_eligible():
    # A flaky render that produced no taste evidence is not a pass — a surface
    # we did not score cannot be vouched for.
    v = _verdict(
        _gate(accept_gauntlet.TASTE, passed=False, abstained=True),
        _gate(accept_gauntlet.HIERARCHY, passed=True),
    )
    assert viral_eligible_from_verdict(v) is False


def test_missing_floor_leg_is_not_eligible():
    # Hierarchy never ran — the floor was only half-measured.
    v = _verdict(_gate(accept_gauntlet.TASTE, passed=True))
    assert viral_eligible_from_verdict(v) is False


def test_empty_verdict_is_not_eligible():
    assert viral_eligible_from_verdict(_verdict()) is False


def test_floor_green_but_first_paint_hard_fails_is_not_eligible():
    # The stranger-cold first-paint leg (folded into the viral gate) measured a
    # real failure: the surface is pretty but the stranger sees a broken first
    # paint → it must not enter the viral pool.
    v = _verdict(
        _gate(accept_gauntlet.TASTE, passed=True),
        _gate(accept_gauntlet.HIERARCHY, passed=True),
        _gate(accept_gauntlet.VIRAL, passed=False),
    )
    assert viral_eligible_from_verdict(v) is False


def test_any_other_gate_hard_fail_disqualifies():
    # A real defect-registry finding alongside a green floor still blocks the
    # viral pool — no gate may carry a finding.
    v = _verdict(
        _gate(accept_gauntlet.TASTE, passed=True),
        _gate(accept_gauntlet.HIERARCHY, passed=True),
        _gate(accept_gauntlet.DEFECT_REGISTRY, passed=False),
    )
    assert viral_eligible_from_verdict(v) is False


def test_abstained_optional_leg_is_tolerated():
    # A flaky OPTIONAL leg (e.g. data) that abstained does not sink an otherwise
    # floor-green surface — abstain ≠ a real finding.
    v = _verdict(
        _gate(accept_gauntlet.TASTE, passed=True),
        _gate(accept_gauntlet.HIERARCHY, passed=True),
        _gate(accept_gauntlet.DATA, passed=False, abstained=True),
    )
    assert viral_eligible_from_verdict(v) is True
