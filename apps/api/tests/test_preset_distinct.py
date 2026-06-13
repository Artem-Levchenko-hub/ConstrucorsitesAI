"""V1.6 8/5 — deterministic coverage for the preset-distinctness ratchet.

Everything here runs WITHOUT a browser, an LLM, or an orchestrator: the gate
reads the static ``PRESETS`` declarations and scores pairwise design separation.
That is the contract — the ratchet must be replayable money-free so a 30th
preset that clones an existing one fails in CI, not in production.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

# The gate lives in scripts/ (the plan's literal path 8/5), not src/.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import preset_distinct as pd  # noqa: E402

from omnia_api.services.design_presets import PRESETS  # noqa: E402
from omnia_api.services.wow_dom_gate import rgb_to_hsl  # noqa: E402

_EDITORIAL = PRESETS["editorial-trust"]  # achromatic black accent, white bg
_STUDIO = PRESETS["studio-showreel"]  # also achromatic black accent, white bg
_KIDS = PRESETS["kids-playful"]  # warm hue, mixed + eyebrow-labels (tight pair)


# ── R-04 reuse: the colour science is wow_dom_gate's, not reinvented ──────────


def test_fingerprint_reuses_shared_colour_math() -> None:
    """Accent hue/sat/light come from the shared ``rgb_to_hsl`` (R-04)."""
    fp = pd.fingerprint(_KIDS)
    acc = _KIDS.palette.get("accent") or _KIDS.palette["fg"]
    h, s, light = rgb_to_hsl(pd._hex_to_rgb(acc))
    assert fp.accent_hue == pytest.approx(h)
    assert fp.accent_sat == pytest.approx(s)
    assert fp.accent_light == pytest.approx(light)


# ── distance is a metric on the fingerprint ───────────────────────────────────


def test_distance_self_is_zero() -> None:
    fp = pd.fingerprint(_KIDS)
    assert pd.distance(fp, fp) == 0.0


def test_distance_is_symmetric() -> None:
    a, b = pd.fingerprint(_KIDS), pd.fingerprint(_EDITORIAL)
    assert pd.distance(a, b) == pytest.approx(pd.distance(b, a))


def test_categorical_change_increases_distance() -> None:
    """Flipping one categorical axis on a clone widens the gap by its weight."""
    base = pd.fingerprint(_KIDS)
    new_sig = "monogram" if _KIDS.section_signature != "monogram" else "numerals"
    flipped = pd.fingerprint(dataclasses.replace(_KIDS, section_signature=new_sig))
    assert pd.distance(base, flipped) == pytest.approx(pd.W_SIG)


# ── the real 29 presets must be pairwise distinct (the live ratchet) ──────────


def test_real_corpus_all_distinct() -> None:
    report = pd.evaluate(PRESETS)
    assert report.passed, report.summary()
    assert report.violations == ()


def test_real_corpus_clears_floor_with_margin() -> None:
    """Documents the safety margin: if a future preset clones an existing one,
    ``min_distance`` drops below the floor and this fails loudly."""
    report = pd.evaluate(PRESETS)
    assert report.min_distance > pd.DISTINCT_FLOOR
    # the empirically-tightest real pair, recorded so a regression is visible.
    assert set(report.min_pair) == {"kids-playful", "pet-care"}


# ── adversarial: homogenisation must FAIL ─────────────────────────────────────


def test_identical_corpus_fails() -> None:
    """Three byte-identical design intents collapse to one template → reject."""
    corpus = {
        "a": _KIDS,
        "b": dataclasses.replace(_KIDS, id="b"),
        "c": dataclasses.replace(_KIDS, id="c"),
    }
    report = pd.evaluate(corpus)
    assert not report.passed
    assert report.min_distance == 0.0
    assert len(report.violations) == 3  # all three pairs collide


def test_name_only_twin_fails() -> None:
    """A preset that differs ONLY in non-rendered metadata (name/keywords/
    one_liner/industries) is not a distinct design — the fingerprint ignores
    those fields, so the twin collides and the gate rejects it."""
    twin = dataclasses.replace(
        _KIDS,
        id="kids-twin",
        name="Totally Different Name",
        one_liner="completely different copy",
        keywords=("foo", "bar"),
        industries=("baz",),
    )
    corpus = {"orig": _KIDS, "twin": twin}
    report = pd.evaluate(corpus)
    assert not report.passed
    assert pd.distance(pd.fingerprint(_KIDS), pd.fingerprint(twin)) == 0.0


def test_achromatic_pair_still_separated_by_non_colour_dims() -> None:
    """Two presets with achromatic accents on white bg (hue+bg contribute 0)
    are still proven distinct via hero_type/section_signature/font — the whole
    point of a multi-dimensional fingerprint."""
    fa, fb = pd.fingerprint(_EDITORIAL), pd.fingerprint(_STUDIO)
    assert fa.accent_chromatic is False
    assert fb.accent_chromatic is False
    assert pd.distance(fa, fb) >= pd.DISTINCT_FLOOR


# ── the floor is the knob, and the gate is honest about coverage ──────────────


def test_floor_param_can_reject_the_real_corpus() -> None:
    """An absurdly high floor flags the whole corpus — proves the floor, not a
    hard-coded verdict, decides pass/fail."""
    report = pd.evaluate(PRESETS, floor=99.0)
    assert not report.passed
    assert report.violations


def test_main_clean_exit_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert pd._main([]) == 0
    assert pd._main(["--json"]) == 0
    out = capsys.readouterr().out
    assert '"passed": true' in out.lower()
