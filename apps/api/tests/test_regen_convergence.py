"""V2.5d — REGEN-CONVERGENCE-PROOF.

Proves, money-free (no LLM, no browser), that the chip→design reject→regenerate
loop CONVERGES now that the writer honours ``discovery_spec`` (V2.5c), and would
loop forever if it did not. The loop is driven by the REAL entity-writer prompt
builder (:func:`lean_prompt.build_lean_system_prompt`) and the REAL chip→pixel
gate (:func:`chip_pixel_gate.evaluate_fidelity`); only the render is modelled
(:func:`regen_convergence.honour_render`).

The headline pair:
* ``test_loop_converges_when_regen_honours_spec`` — initial raw build fails, the
  spec-bearing regenerate passes → converges in ≤2 attempts (green BECAUSE V2.5c
  forwards the spec into the regenerate prompt).
* ``test_loop_never_converges_when_spec_blind`` — the adversarial ratchet: a
  spec-blind regenerate never converges → the deterministic red-bar that proves
  the harmful loop the gate-leg alone would have created.
"""

from collections.abc import Callable

from omnia_api.services.chip_pixel_gate import (
    PALETTE_BG,
    PRIMARY_FAMILY,
    SECTION_ANCHOR,
    FidelitySpec,
    evaluate_fidelity,
    spec_prompt_directive,
)
from omnia_api.services.lean_prompt import build_lean_system_prompt
from omnia_api.services.regen_convergence import (
    honour_render,
    simulate_regen_loop,
)

# The user tapped: dark theme, violet accent, [catalog, reviews, contacts].
SPEC = FidelitySpec(
    dark_mode=True,
    primary_family="violet",
    sections=("catalog", "testimonials", "contacts"),
    tone="premium",
)
PROMPT = "интернет-магазин дизайнерского декора"
PRESET = "saas-product"  # a preset whose palette is emerald, NOT violet — conflicts with the chip


def _build(*, regen_carries_spec: bool) -> Callable[[int], str]:
    """A loop builder: attempt 1 is the raw initial build (spec-blind, the
    generation that triggered the mismatch); attempt 2+ is the regenerate, which
    forwards the spec exactly as ``messages._catalog_fallback_generate`` does
    when ``regen_carries_spec`` is True."""

    def build_prompt(attempt: int) -> str:
        spec_arg = SPEC.to_dict() if (attempt >= 2 and regen_carries_spec) else None
        return build_lean_system_prompt(
            preset_id=PRESET,
            skill_brief=None,
            user_prompt=PROMPT,
            discovery_spec=spec_arg,
        )

    return build_prompt


# ── the render model has teeth (not a trivial always-pass) ──────────────────────


def test_honour_render_compliant_when_directive_present():
    prompt = build_lean_system_prompt(
        preset_id=PRESET, skill_brief=None, user_prompt=PROMPT, discovery_spec=SPEC.to_dict()
    )
    assert spec_prompt_directive(SPEC) in prompt  # V2.5c wiring is live
    obs = honour_render(prompt, SPEC)
    assert evaluate_fidelity(obs, SPEC).passed


def test_honour_render_violates_when_directive_absent():
    prompt = build_lean_system_prompt(
        preset_id=PRESET, skill_brief=None, user_prompt=PROMPT, discovery_spec=None
    )
    assert spec_prompt_directive(SPEC) not in prompt
    rep = evaluate_fidelity(honour_render(prompt, SPEC), SPEC)
    assert not rep.passed
    # the unsteered house style misses every asserted axis of this spec
    assert set(rep.classes) == {PALETTE_BG, PRIMARY_FAMILY, SECTION_ANCHOR}


# ── the convergence contract ────────────────────────────────────────────────────


def test_loop_converges_when_regen_honours_spec():
    result = simulate_regen_loop(_build(regen_carries_spec=True), SPEC, max_attempts=2)
    assert result.converged
    assert result.attempts == 2  # raw attempt failed, spec-bearing regenerate passed


def test_loop_never_converges_when_spec_blind():
    """ADVERSARIAL RATCHET: with the generation leg open (pre-V2.5c), the
    regenerate is spec-blind, so the gate rejects every attempt — the infinite
    reject loop. This stays red until the loop honours the spec."""
    result = simulate_regen_loop(_build(regen_carries_spec=False), SPEC, max_attempts=2)
    assert not result.converged
    assert result.attempts == 2
    assert set(result.final_classes) == {PALETTE_BG, PRIMARY_FAMILY, SECTION_ANCHOR}


def test_empty_spec_is_inert_no_loop():
    """No chips → no directive → the gate asserts nothing → trivially converges
    on the first attempt, byte-identical to the pre-V2.5 no-op path (R-10)."""
    empty = FidelitySpec()

    def build_prompt(attempt: int) -> str:
        return build_lean_system_prompt(
            preset_id=PRESET, skill_brief=None, user_prompt=PROMPT, discovery_spec=None
        )

    result = simulate_regen_loop(build_prompt, empty, max_attempts=2)
    assert result.converged
    assert result.attempts == 1
