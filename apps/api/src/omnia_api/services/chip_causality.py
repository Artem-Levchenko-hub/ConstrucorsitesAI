"""V2.5b — CHIP-CAUSALITY GATE (proof the onboarding popup is not cosmetic).

Pillar 2 of the North Star ("живой онбординг-попап") rests on one claim: the
chips the user taps **steer the build**. If the same prompt renders the same
page no matter which chips were picked, the popup is decoration and the whole
pillar is a lie. None of the sibling proofs test this directly — V2.5c shows
the writer *can* honour a spec, V2.5d shows the reject→regenerate loop
*converges*, but neither pins the load-bearing fact that **two contrasting chip
sets over ONE prompt diverge in the painted pixels**. This module is that
single machine proof.

It runs ONE entity brief through the REAL generation path
(:func:`lean_prompt.build_lean_system_prompt`, forwarding ``discovery_spec``
exactly as the catalog writer does) under two CONTRASTING chip sets, renders
each through the deterministic writer model
(:func:`regen_convergence.honour_render`), and asserts on the V1.6 fidelity
axes (palette background, primary hue family, section anchors, tone marker) that:

1. **the two renders measurably DIVERGE** — same prompt, different chips, a
   different page;
2. **each render honours its OWN chips** — divergence is *toward* the picks, not
   noise; and
3. **each render VIOLATES the other set's chips** — the difference is causally
   bound to the choice, so swapping the chip set flips the verdict.

Money-free, browser-free, LLM-free — same split as V2.5c/V2.5d, all scoring in
Python over hand-built or model-emitted observations. Because
:func:`regen_convergence.honour_render` keys off the *actual* builder output
(``spec_prompt_directive(spec) in prompt``), the proof is coupled to the real
wiring: drop ``discovery_spec`` from :func:`lean_prompt.build_lean_system_prompt`
and BOTH renders collapse to the unsteered house default → divergence vanishes
→ this gate goes red. That is the ratchet — green only while the popup actually
reaches the writer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from omnia_api.services.chip_pixel_gate import (
    _DARK_LUM_MAX,
    _LIGHT_LUM_MIN,
    _SECTION_KEYWORDS,
    PALETTE_BG,
    PRIMARY_FAMILY,
    SECTION_ANCHOR,
    TONE_MARKER,
    FidelitySpec,
    Obs,
    _detect_tone,
    _dominant_accent,
    _section_present,
    evaluate_fidelity,
    family_of_hue,
)
from omnia_api.services.lean_prompt import build_lean_system_prompt
from omnia_api.services.regen_convergence import honour_render
from omnia_api.services.wow_dom_gate import relative_luminance

# A prompt builder: turns one chip set's spec into the writer prompt. The real
# default forwards ``discovery_spec`` through the production lean builder; tests
# inject a spec-blind builder to prove the gate goes red when the popup is
# unwired (see ``test_chip_causality``).
PromptBuilder = Callable[[FidelitySpec], str]


@dataclass(frozen=True)
class ChipSet:
    """One onboarding answer-set, as the raw scripted chip taps / "Другое" text.

    ``label`` is for diagnostics only. The three axes mirror
    :meth:`FidelitySpec.from_answers` — undecided axes stay ``None`` and the
    derived spec abstains on them.
    """

    label: str
    palette: str | None = None
    sections: str | tuple[str, ...] | None = None
    tone: str | None = None

    def to_spec(self) -> FidelitySpec:
        # Tone routes through the same ``_detect_tone`` canonicaliser the
        # production marshaller (:func:`chip_pixel_gate.spec_from_discovery`)
        # uses, so a chip label like "Премиум" reifies to the canonical token
        # the writer and gate share (R-04 single source).
        return FidelitySpec.from_answers(
            palette=self.palette,
            sections=self.sections,
            tone=_detect_tone(self.tone),
        )


@dataclass(frozen=True)
class CausalityResult:
    """Verdict of one same-prompt / two-chip-set causality run."""

    diverged_axes: tuple[str, ...]
    a_honours_self: bool
    b_honours_self: bool
    cross_violates: bool
    obs_a: Obs
    obs_b: Obs

    @property
    def diverged(self) -> bool:
        """The two renders differ on at least one fidelity axis."""
        return bool(self.diverged_axes)

    @property
    def proven(self) -> bool:
        """Chips are causal iff the renders measurably diverge, each honours its
        OWN chips, and each render violates the OTHER set's chips. All three are
        required: divergence alone could be noise; honour-self alone could be two
        pages that happen to look alike; cross-violation alone is vacuous without
        an actual difference. Together they pin "the popup steers the build"."""
        return (
            self.diverged
            and self.a_honours_self
            and self.b_honours_self
            and self.cross_violates
        )


def _bg_bucket(obs: Obs) -> str | None:
    """Coarse light/dark/mid bucket of the painted page background.

    Reuses the gate's own luminance thresholds (R-04) so "diverged on palette"
    means the same thing here as a palette-bg finding does in
    :func:`chip_pixel_gate.evaluate_fidelity`. ``None`` when the page declared no
    background.
    """
    bg = obs.get("pageBg")
    if not bg or len(bg) < 3:
        return None
    lum = relative_luminance((float(bg[0]), float(bg[1]), float(bg[2])))
    if lum <= _DARK_LUM_MAX:
        return "dark"
    if lum >= _LIGHT_LUM_MIN:
        return "light"
    return "mid"


def _accent_family(obs: Obs) -> str | None:
    """Colour family of the painted CTA the eye lands on (``None`` if none)."""
    accent = _dominant_accent(obs)
    if accent is None:
        return None
    return family_of_hue(accent[0])


def _present_sections(obs: Obs) -> frozenset[str]:
    """Canonical sections that actually have an anchor/heading on the page."""
    return frozenset(c for c in _SECTION_KEYWORDS if _section_present(c, obs))


def _diverged_axes(obs_a: Obs, obs_b: Obs) -> tuple[str, ...]:
    """The fidelity axes on which two renders measurably differ.

    Each axis is compared by the same observable the gate scores: background
    light/dark bucket, dominant CTA colour family, the set of present section
    anchors, and the declared tone marker. An axis counts as diverged only when
    both sides carry a signal and they disagree — a missing signal on either
    side is not a difference.
    """
    axes: list[str] = []

    bg_a, bg_b = _bg_bucket(obs_a), _bg_bucket(obs_b)
    if bg_a is not None and bg_b is not None and bg_a != bg_b:
        axes.append(PALETTE_BG)

    fam_a, fam_b = _accent_family(obs_a), _accent_family(obs_b)
    if fam_a is not None and fam_b is not None and fam_a != fam_b:
        axes.append(PRIMARY_FAMILY)

    if _present_sections(obs_a) != _present_sections(obs_b):
        axes.append(SECTION_ANCHOR)

    tone_a = obs_a.get("declaredTone")
    tone_b = obs_b.get("declaredTone")
    if tone_a and tone_b and str(tone_a).lower() != str(tone_b).lower():
        axes.append(TONE_MARKER)

    return tuple(axes)


def _default_prompt_builder(base_prompt: str, *, preset_id: str | None) -> PromptBuilder:
    """The REAL generation path: same brief + preset, only the chips differ.

    Mirrors how the catalog writer composes its system prompt — the ONLY thing
    that changes between the two chip sets is ``discovery_spec``. So any render
    divergence is attributable to the chips and nothing else.
    """

    def build(spec: FidelitySpec) -> str:
        return build_lean_system_prompt(
            preset_id=preset_id,
            skill_brief=None,
            user_prompt=base_prompt,
            discovery_spec=spec.to_dict(),
        )

    return build


def prove_chip_causality(
    base_prompt: str,
    chip_a: ChipSet,
    chip_b: ChipSet,
    *,
    preset_id: str | None = None,
    build_prompt: PromptBuilder | None = None,
    render: Callable[[str, FidelitySpec], Obs] = honour_render,
) -> CausalityResult:
    """Run ONE prompt through two chip sets and measure render divergence.

    ``build_prompt`` defaults to the production lean builder (chips → directive →
    prompt); pass a spec-blind builder to assert the gate goes red when the popup
    is unwired. ``render`` defaults to the deterministic writer model.
    """
    spec_a = chip_a.to_spec()
    spec_b = chip_b.to_spec()
    builder = build_prompt or _default_prompt_builder(base_prompt, preset_id=preset_id)

    obs_a = render(builder(spec_a), spec_a)
    obs_b = render(builder(spec_b), spec_b)

    return CausalityResult(
        diverged_axes=_diverged_axes(obs_a, obs_b),
        a_honours_self=evaluate_fidelity(obs_a, spec_a).passed,
        b_honours_self=evaluate_fidelity(obs_b, spec_b).passed,
        # Each render must FAIL the other set's spec — the difference is bound to
        # the chips, not incidental. Both directions, so neither set is a superset
        # of the other on the asserted axes.
        cross_violates=(
            not evaluate_fidelity(obs_a, spec_b).passed
            and not evaluate_fidelity(obs_b, spec_a).passed
        ),
        obs_a=obs_a,
        obs_b=obs_b,
    )


__all__ = [
    "CausalityResult",
    "ChipSet",
    "PromptBuilder",
    "prove_chip_causality",
]
