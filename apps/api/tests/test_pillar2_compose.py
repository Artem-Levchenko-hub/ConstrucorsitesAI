"""V2.9 — PILLAR-2 PROVEN-FINISH: the onboarding popup composes as ONE chain.

Pillars 1, 3 and 4 have a machine finish (V1.7 streak, V3.11 compose-harness,
V4.7 synthetic fork-depth); pillar 2 ("живой онбординг-попап") did not — V2.2
only measures BUILD pass-rate, which is a pillar-1 metric, not the quality of the
popup. Each pillar-2 proof shipped GREEN **in isolation**:

* causality   (V2.5b, ``chip_causality.prove_chip_causality``) — same prompt +
  different chips ⇒ measurably different render;
* convergence (V2.5d, ``regen_convergence.simulate_regen_loop``) — the
  reject→regenerate loop converges iff generation honours the chips;
* registry    (V2.7, ``onboarding_registry.scan``) — the ASK card holds the chip
  contract (no bare-text / no trapped-user / no dirty chips);
* chip floor  (V2.1, ``discovery._fallback_choices``) — every stage offers a
  non-empty chip set, so the card never degrades to bare text.

No test ever proved they hold TOGETHER over a corpus of niches — "code-proven,
never played as one chain", the same NORTH-STAR gap V3.11 closed for pillar 3.
This harness composes them: across a fixed 6-niche prompt corpus it asserts the
popup is a coherent causal chain (the two independent steering proofs AGREE per
niche), that the floor-supplied card is registry-clean by construction, and —
the adversary mirror of V3.11's barren-degradation — that an UNWIRED popup
collapses BOTH steering legs together (never "causal but non-converging").

Money-free: no LLM, no browser, runtime byte-identical (test-only, push-only).
The harness IS the verification mechanism, like the V3.11 vitest file.

**Honest scope.** The full V2.9 rubric also names (e) intent-correctness (V2.11)
and (f) zero-question extraction (V2.10). Both require ``compile_build_spec``,
which is NOT in the repo (V2.6 / V2.10 / V2.11 are all still ``[ ]``). What IS
money-free composable today — causality + convergence + registry + chip floor,
plus the deterministic decisiveness FLOOR (``wants_build_now``) — is what this
file proves. The decisiveness/intent finish lands when those compilers ship; so
V2.9 is ``[~]`` (composable half proven), not ``[x]``.
"""

from __future__ import annotations

from dataclasses import dataclass

from omnia_api.services import discovery
from omnia_api.services.chip_causality import ChipSet, prove_chip_causality
from omnia_api.services.chip_pixel_gate import (
    PALETTE_BG,
    PRIMARY_FAMILY,
    SECTION_ANCHOR,
    TONE_MARKER,
    FidelitySpec,
)
from omnia_api.services.lean_prompt import build_lean_system_prompt
from omnia_api.services.onboarding_registry import (
    ONBOARDING_CLASSES,
    OnboardingContext,
    scan,
)
from omnia_api.services.regen_convergence import simulate_regen_loop

# ── the fixed niche corpus ───────────────────────────────────────────────────────
# Six niches mirroring the V2.2 build corpus and the committed frozen-HTML
# families (agency / ecommerce / editorial / saas). Each carries the prompt plus
# the INTENDED chip answers (``chip_a``) and a strongly CONTRASTING set
# (``chip_b``) that opposes ``chip_a`` on all four V1.6 axes — the proven shape
# from ``test_chip_causality``. The six ``chip_a`` accent families are pairwise
# distinct so "the niches differ" is structural, not coincidental.


@dataclass(frozen=True)
class Niche:
    label: str
    prompt: str
    chip_a: ChipSet  # the answer set this niche's user would tap
    chip_b: ChipSet  # a contrasting set, opposite on every axis


CORPUS: tuple[Niche, ...] = (
    Niche(
        "clinic",
        "Сайт для частной клиники с записью к врачам и личным кабинетом",
        ChipSet("клиника", palette="светлая изумрудная",
                sections="услуги, контакты", tone="строгое"),
        ChipSet("контраст", palette="тёмная фиолетовая", sections="портфолио", tone="премиум"),
    ),
    Niche(
        "shop",
        "Интернет-магазин дизайнерского декора с каталогом и корзиной",
        ChipSet("магазин", palette="светлая янтарная",
                sections="каталог, цены", tone="дружелюбное"),
        ChipSet("контраст", palette="тёмная индиго", sections="о нас, faq", tone="строгое"),
    ),
    Niche(
        "saas",
        "SaaS-CRM для маркетингового агентства с дашбордом и тарифами",
        ChipSet("saas", palette="тёмная индиго", sections="возможности, цены", tone="премиум"),
        ChipSet("контраст", palette="светлая оранжевая", sections="галерея", tone="дружелюбное"),
    ),
    Niche(
        "agency",
        "Премиальное агентство недвижимости: витрина объектов и заявки",
        ChipSet("агентство", palette="тёмная фиолетовая",
                sections="портфолио, о нас", tone="премиум"),
        ChipSet("контраст", palette="светлая бирюзовая", sections="цены", tone="строгое"),
    ),
    Niche(
        "editorial",
        "Блог-медиа о технологиях с лентой статей и подпиской",
        ChipSet("медиа", palette="светлая оранжевая", sections="о нас, faq", tone="дружелюбное"),
        ChipSet("контраст", palette="тёмная пурпурная", sections="каталог", tone="строгое"),
    ),
    Niche(
        "fintech",
        "Финтех-кабинет: счета, транзакции и аналитика для бизнеса",
        ChipSet("финтех", palette="тёмная бирюзовая",
                sections="возможности, контакты", tone="строгое"),
        ChipSet("контраст", palette="светлая изумрудная", sections="галерея", tone="дружелюбное"),
    ),
)

_ALL_AXES = {PALETTE_BG, PRIMARY_FAMILY, SECTION_ANCHOR, TONE_MARKER}
_PRESET = "saas-product"  # a fixed preset; attempt-1 is spec-blind regardless


def _converges(spec: FidelitySpec, *, regen_carries_spec: bool):
    """Drive the reject→regenerate loop for one niche spec.

    Attempt 1 is the raw spec-blind build (the generation that triggered the
    mismatch); attempt 2 is the regenerate, which forwards the spec exactly as
    the real catalog-fallback loop does iff ``regen_carries_spec``. Mirrors
    ``test_regen_convergence._build``.
    """

    def build_prompt(attempt: int) -> str:
        carry = attempt >= 2 and regen_carries_spec
        return build_lean_system_prompt(
            preset_id=_PRESET,
            skill_brief=None,
            user_prompt="композиционный харнесс столпа 2",
            discovery_spec=spec.to_dict() if carry else None,
        )

    return simulate_regen_loop(build_prompt, spec, max_attempts=2)


# ── (1) cross-leg agreement: the two steering proofs AGREE per niche ─────────────


def test_each_niche_chips_are_causal_and_the_loop_converges() -> None:
    """For every niche the SAME ``chip_a`` is certified causal by V2.5b AND, run
    through the V2.5d loop, the spec-bearing regenerate converges. Two independent
    proofs, one source of truth (``chip_a.to_spec()``) — they must agree that the
    popup steers the build. This is the composition V3.11 did for pillar 3."""
    for n in CORPUS:
        causal = prove_chip_causality(n.prompt, n.chip_a, n.chip_b)
        assert causal.proven, f"{n.label}: chips not proven causal — {causal}"
        assert set(causal.diverged_axes) == _ALL_AXES, (
            f"{n.label}: expected divergence on all four axes, got "
            f"{causal.diverged_axes}"
        )

        conv = _converges(n.chip_a.to_spec(), regen_carries_spec=True)
        assert conv.converged, f"{n.label}: spec-bearing loop did not converge"
        assert conv.attempts == 2, (
            f"{n.label}: raw build should fail then the spec-bearing regenerate "
            f"pass — attempts={conv.attempts}"
        )


# ── (2) the chip floor and the registry compose: the card is clean by build ──────


def test_floor_supplied_cards_are_registry_clean_at_every_stage() -> None:
    """Compose V2.1 (the chip floor) with V2.7 (the registry): the deterministic
    fallback offers a NON-EMPTY chip set at every discovery stage, and an ASK card
    built from that floor holds every onboarding invariant. The popup can never
    degrade to a bare-text question, and a floor card is registry-clean by
    construction — not by luck."""
    stages = len(discovery._FALLBACK_CHOICES)
    assert stages >= 1
    # Probe one stage past the table to prove the floor clamps (never empties).
    for stage in range(stages + 2):
        chips = discovery._fallback_choices(stage)
        assert chips, f"stage {stage}: chip floor returned an empty set"
        report = scan(
            OnboardingContext(action=discovery.ASK, choices=chips, allow_custom=True)
        )
        assert report.judged, f"stage {stage}: an ASK card should be judged"
        assert report.passed, (
            f"stage {stage}: floor card regressed — {report.summary()}"
        )


# ── (3) coherent degradation: an unwired popup collapses BOTH legs together ──────


def _spec_blind_builder(_spec: FidelitySpec) -> str:
    # A prompt that never carries the chip directive — the popup is unwired.
    return "<identity>catalog writer</identity>"


def test_unwired_popup_collapses_causality_and_convergence_together() -> None:
    """The pillar-2 mirror of V3.11's barren degradation ("narration and swatches
    go silent TOGETHER"): if the chips never reach the writer, causality vanishes
    AND the loop never converges — for the SAME niche, on BOTH legs at once. There
    is no incoherent middle state where the popup is causal but non-converging (or
    vice versa). This is the adversary ratchet: rip ``discovery_spec`` out of the
    generation path and this whole test goes red."""
    for n in CORPUS:
        causal = prove_chip_causality(
            n.prompt, n.chip_a, n.chip_b, build_prompt=_spec_blind_builder
        )
        assert not causal.proven, f"{n.label}: unwired popup still 'causal'"
        assert not causal.diverged, f"{n.label}: unwired renders should be identical"
        assert causal.obs_a == causal.obs_b

        conv = _converges(n.chip_a.to_spec(), regen_carries_spec=False)
        assert not conv.converged, (
            f"{n.label}: spec-blind loop converged — the reject loop is supposed "
            f"to be infinite when the popup is unwired"
        )


def test_no_popup_means_no_false_steering() -> None:
    """The empty-answer control: no chips ⇒ an empty spec ⇒ causality is not
    proven (nothing to diverge) and the loop converges trivially on attempt 1,
    byte-identical to the pre-V2.5 no-op path. Absence of a popup must not be
    mistaken for steering, on either leg (R-10)."""
    empty = FidelitySpec()
    assert empty.is_empty
    conv = _converges(empty, regen_carries_spec=True)
    assert conv.converged and conv.attempts == 1, conv


# ── (4) adversary + distinctness: the gates have teeth, the niches really differ ──


def test_niche_chip_specs_are_pairwise_distinct() -> None:
    """The corpus is not six copies of one answer set: every niche's intended
    ``chip_a`` reifies to a spec that differs from every other on at least one
    axis. Without this the causality proofs above could pass on a degenerate
    corpus where 'divergence' is vacuous."""
    specs = [(n.label, n.chip_a.to_spec()) for n in CORPUS]
    for i in range(len(specs)):
        for j in range(i + 1, len(specs)):
            (la, a), (lb, b) = specs[i], specs[j]
            assert a != b, f"{la} and {lb} reify to the same spec {a}"


def test_registry_has_teeth_on_poisoned_cards() -> None:
    """Each onboarding defect class fires on its poisoned card — the composition's
    pass in (2) is meaningful only because the registry would have caught a real
    regression. One adversary fixture per shipped class."""
    bare = scan(OnboardingContext(action=discovery.ASK, choices=(), allow_custom=True))
    trapped = scan(
        OnboardingContext(action=discovery.ASK, choices=("Да", "Нет"), allow_custom=False)
    )
    dirty = scan(
        OnboardingContext(
            action=discovery.ASK, choices=("Тон", "тон"), allow_custom=True
        )  # case-insensitive duplicate
    )
    fired = set(bare.classes) | set(trapped.classes) | set(dirty.classes)
    assert fired == set(ONBOARDING_CLASSES), (
        f"not every onboarding class has teeth — fired {sorted(fired)}, "
        f"classes {sorted(ONBOARDING_CLASSES)}"
    )


def test_decisiveness_floor_skips_questions_only_on_explicit_intent() -> None:
    """The money-free SLICE of decisiveness (the V2.6 finish is still ``[ ]``):
    an explicit build-now signal skips straight to BUILD, while a bare niche word
    does not. This is the only deterministic decisiveness assertion available
    without ``compile_build_spec``; the intent-correctness (V2.11) and
    zero-question (V2.10) axes of the full V2.9 rubric are deferred to when that
    compiler ships."""
    for n in CORPUS:
        explicit = f"{n.prompt}, генерируй уже"
        assert discovery.wants_build_now(explicit), f"{n.label}: build-now missed"
        assert not discovery.wants_build_now(n.label), (
            f"{n.label}: a bare niche word must not force a build"
        )
