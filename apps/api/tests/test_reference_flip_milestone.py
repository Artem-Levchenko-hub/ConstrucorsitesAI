"""V1.13c — deterministic coverage for the reference flip-milestone harness.

Every test here runs WITHOUT a browser, an LLM, or a corpus render: the decision
is pure (candidate verdicts + adversary regression counts are injected directly).
The standing teeth — ``test_committed_flag_default_is_consistent`` and
``test_flag_on_without_milestone_is_caught`` — guard the real config flag:
flipping ``acceptance_gauntlet_reference_gate`` ON without a passing milestone
turns this suite RED.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import reference_flip_milestone as rfm  # noqa: E402

from omnia_api.services import reference_corpus as rc  # noqa: E402

_NICHES = ("agency", "ecommerce", "editorial", "saas")
_TEETH = rc.MIN_REGRESSIONS  # adversary must regress on >= this many axes per niche


def _all_below(niches=_NICHES, *, regressions=_TEETH):
    """Adversary regression map where every niche is below the floor (teeth)."""
    return {n: regressions for n in niches}


# ── clause 1: candidates must clear the corpus ───────────────────────────────


def test_flip_permitted_when_all_clauses_hold():
    v = rfm.evaluate_reference_flip(
        [True, True], _all_below(), corpus_niches=_NICHES, min_candidates=2
    )
    assert v.flip_permitted is True
    assert v.reasons == ()


def test_one_below_candidate_blocks_the_flip():
    v = rfm.evaluate_reference_flip(
        [True, False], _all_below(), corpus_niches=_NICHES, min_candidates=2
    )
    assert v.candidates_cleared is False
    assert v.flip_permitted is False
    assert any("fell BELOW" in r for r in v.reasons)


def test_too_few_candidates_blocks_the_flip():
    v = rfm.evaluate_reference_flip(
        [True], _all_below(), corpus_niches=_NICHES, min_candidates=3
    )
    assert v.candidates_cleared is False
    assert any("need >= 3" in r for r in v.reasons)


def test_zero_candidates_never_clears():
    v = rfm.evaluate_reference_flip(
        [], _all_below(), corpus_niches=_NICHES, min_candidates=1
    )
    assert v.candidates_cleared is False
    assert v.flip_permitted is False


# ── clause 2: adversary must fall below EVERY niche (teeth) ───────────────────


def test_leaky_adversary_blocks_the_flip():
    regressions = _all_below()
    regressions["saas"] = _TEETH - 1  # adversary HELD the floor on saas
    v = rfm.evaluate_reference_flip([True], regressions, corpus_niches=_NICHES)
    assert v.adversary_below is False
    assert v.flip_permitted is False
    assert any("saas" in r and "no teeth" in r for r in v.reasons)


def test_niche_missing_from_regression_map_is_a_coverage_gap():
    # A niche with no recorded regression count defaults to 0 → below the floor →
    # adversary_below is False (a render miss must never read as silent teeth).
    partial = {"agency": _TEETH, "ecommerce": _TEETH}  # editorial + saas missing
    v = rfm.evaluate_reference_flip([True], partial, corpus_niches=_NICHES)
    assert v.adversary_below is False


# ── clause 3: corpus must be non-empty ───────────────────────────────────────


def test_empty_corpus_blocks_the_flip():
    v = rfm.evaluate_reference_flip([True], {}, corpus_niches=())
    assert v.corpus_present is False
    assert v.adversary_below is False
    assert v.flip_permitted is False
    assert any("corpus is empty" in r for r in v.reasons)


# ── report shape ─────────────────────────────────────────────────────────────


def test_report_is_machine_readable():
    v = rfm.evaluate_reference_flip([True], _all_below(), corpus_niches=_NICHES)
    rep = v.report()
    assert rep["flip_permitted"] is True
    assert set(rep) == {
        "candidates_cleared",
        "adversary_below",
        "corpus_present",
        "flip_permitted",
        "reasons",
    }


# ── consistency guard (the standing teeth on the real flag) ──────────────────


def test_off_flag_is_always_consistent():
    blocked = rfm.evaluate_reference_flip([], {}, corpus_niches=())
    ok, msg = rfm.check_consistency(False, blocked)
    assert ok is True
    assert "OFF" in msg


def test_on_flag_with_passing_milestone_is_consistent():
    passing = rfm.evaluate_reference_flip(
        [True], _all_below(), corpus_niches=_NICHES
    )
    ok, _ = rfm.check_consistency(True, passing)
    assert ok is True


def test_flag_on_without_milestone_is_caught():
    """Regression guard: an ON flag with a failing milestone is inconsistent."""
    blocked = rfm.evaluate_reference_flip([False], _all_below(), corpus_niches=_NICHES)
    ok, msg = rfm.check_consistency(True, blocked)
    assert ok is False
    assert "does NOT pass" in msg


def test_committed_flag_default_is_consistent():
    """The real flag, at its committed default, must be a consistent state.

    Today the flag is OFF → consistent regardless of milestone. The day someone
    flips it ON in config without recording a passing milestone, this assertion
    (paired with the guard above) is the thing that should already have caught it.
    """
    from omnia_api.core.config import get_settings

    flag_on = bool(getattr(get_settings(), rfm.FLAG_NAME))
    blocked = rfm.evaluate_reference_flip([], {}, corpus_niches=())  # nothing proven
    ok, msg = rfm.check_consistency(flag_on, blocked)
    if flag_on:
        pytest.fail(
            f"{rfm.FLAG_NAME} is ON but no milestone evidence is wired into the test "
            f"suite. Record the passing flip-milestone before enabling the flag. {msg}"
        )
    assert ok is True


def test_flag_name_matches_the_real_setting():
    from omnia_api.core.config import get_settings

    assert hasattr(get_settings(), rfm.FLAG_NAME)
