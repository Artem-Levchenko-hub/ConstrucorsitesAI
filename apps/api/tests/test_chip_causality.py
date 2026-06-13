"""V2.5b — CHIP-CAUSALITY GATE tests (the popup is not cosmetic).

The single proof of pillar 2's premise: ONE entity brief, two CONTRASTING chip
sets, run through the REAL prompt builder + deterministic writer model, must
produce measurably DIFFERENT painted pages — each honouring its own chips and
violating the other's. The control tests (identical chips, empty chips, unwired
popup) pin that the gate is not vacuously true: it goes red exactly when the
chips stop mattering.
"""

from __future__ import annotations

from omnia_api.services.chip_causality import (
    ChipSet,
    prove_chip_causality,
)
from omnia_api.services.chip_pixel_gate import (
    PALETTE_BG,
    PRIMARY_FAMILY,
    SECTION_ANCHOR,
    TONE_MARKER,
    FidelitySpec,
    Obs,
)

# ONE prompt, reused across every chip set — the only thing that may change a
# render is the chips, never the brief.
_BRIEF = "Сайт для местного бизнеса с витриной и информацией"

# Two onboarding answer sets that contrast on every assertable axis.
_PREMIUM_SHOP = ChipSet(
    label="премиум-магазин",
    palette="тёмная фиолетовая",
    sections="каталог, цены",
    tone="премиум",
)
_FRIENDLY_STUDIO = ChipSet(
    label="дружелюбная студия",
    palette="светлая изумрудная",
    sections="галерея, контакты",
    tone="дружелюбное",
)


def test_contrasting_chips_prove_causality() -> None:
    """Same prompt + opposite chips ⇒ proven causal: diverge, each honours self,
    each violates the other. This is the whole point of the popup."""
    result = prove_chip_causality(_BRIEF, _PREMIUM_SHOP, _FRIENDLY_STUDIO)
    assert result.proven, result
    assert result.diverged
    assert result.a_honours_self
    assert result.b_honours_self
    assert result.cross_violates


def test_divergence_names_every_contrasted_axis() -> None:
    """The two renders differ on palette, hue family, sections AND tone — the
    contrast was designed across all four V1.6 axes, so all four must register."""
    result = prove_chip_causality(_BRIEF, _PREMIUM_SHOP, _FRIENDLY_STUDIO)
    assert set(result.diverged_axes) == {
        PALETTE_BG,
        PRIMARY_FAMILY,
        SECTION_ANCHOR,
        TONE_MARKER,
    }, result.diverged_axes


def test_swap_chip_sets_flips_the_render() -> None:
    """Order is irrelevant — swapping which set is A and which is B yields the
    same proof. Causality is symmetric, not an artefact of argument position."""
    forward = prove_chip_causality(_BRIEF, _PREMIUM_SHOP, _FRIENDLY_STUDIO)
    reverse = prove_chip_causality(_BRIEF, _FRIENDLY_STUDIO, _PREMIUM_SHOP)
    assert forward.proven and reverse.proven
    assert set(forward.diverged_axes) == set(reverse.diverged_axes)
    # The render the premium set produced is identical regardless of slot.
    assert forward.obs_a == reverse.obs_b


def test_identical_chips_do_not_diverge() -> None:
    """Control: the SAME chips on both sides render the SAME page. If this proved
    causality, the gate would be vacuous — divergence must come from contrast."""
    result = prove_chip_causality(_BRIEF, _PREMIUM_SHOP, _PREMIUM_SHOP)
    assert not result.diverged
    assert not result.proven
    # Each still honours its (identical) own spec — it is divergence that is missing.
    assert result.a_honours_self and result.b_honours_self
    assert result.obs_a == result.obs_b


def test_empty_chips_do_not_prove_causality() -> None:
    """Two undecided onboardings carry no directive → both collapse to the house
    default → no divergence → not proven. Saying nothing steers nothing."""
    blank_a = ChipSet(label="blank-a")
    blank_b = ChipSet(label="blank-b")
    assert blank_a.to_spec().is_empty and blank_b.to_spec().is_empty
    result = prove_chip_causality(_BRIEF, blank_a, blank_b)
    assert not result.diverged
    assert not result.proven


def test_unwired_popup_goes_red() -> None:
    """The ratchet: if the chips never reach the writer, both renders collapse to
    the unsteered default and divergence vanishes. A spec-blind builder simulates
    that broken wiring — the gate MUST refuse to certify causality. Drop
    ``discovery_spec`` from the real prompt builder and this is what happens."""

    def spec_blind_builder(_spec: FidelitySpec) -> str:
        # A prompt that never carries the chip directive — the popup is unwired.
        return "<identity>catalog writer</identity>"

    result = prove_chip_causality(
        _BRIEF,
        _PREMIUM_SHOP,
        _FRIENDLY_STUDIO,
        build_prompt=spec_blind_builder,
    )
    assert not result.diverged
    assert not result.proven
    # Both renders are the same unsteered house default.
    assert result.obs_a == result.obs_b


def test_real_builder_carries_the_chip_directive() -> None:
    """Sanity on the production path: the default builder forwards the chips into
    the prompt, so the deterministic writer renders compliantly. Without this the
    'unwired' control would prove nothing — it must differ from the live path."""
    result = prove_chip_causality(_BRIEF, _PREMIUM_SHOP, _FRIENDLY_STUDIO)
    # The premium render is dark + violet — the live wiring, not the light/emerald
    # house default the unwired builder would emit.
    obs_a: Obs = result.obs_a
    assert obs_a["pageBg"][0] < 60  # dark background
    assert FidelitySpec.from_answers(palette="тёмная фиолетовая").dark_mode is True


def test_single_axis_contrast_still_proves() -> None:
    """Causality holds even when only ONE axis contrasts. Two sets identical
    except palette (violet vs emerald) still diverge + cross-violate on that axis
    alone — the gate does not need a maximal contrast to fire."""
    violet = ChipSet(label="violet", palette="фиолетовая", sections="каталог")
    emerald = ChipSet(label="emerald", palette="изумрудная", sections="каталог")
    result = prove_chip_causality(_BRIEF, violet, emerald)
    assert result.proven, result
    assert result.diverged_axes == (PRIMARY_FAMILY,)
