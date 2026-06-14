"""V2.7 — the onboarding defect-registry ratchet.

Every onboarding class carries an adversary fixture that MUST fail its assert
(like the beauty gates 5/5 · 7/5 and the viral registry V4.6), proving the gate
has teeth and is not a vacuous green. A clean ASK turn passes, a BUILD turn and an
empty context are INERT, the chip-hygiene verdict is single-sourced on
``discovery._clean_choices`` (R-04), and the registry is wired into the acceptance
gauntlet through the ``onboarding=`` dial (it is not an orphan). Pure Python — no
browser, no LLM, no DB.
"""

from __future__ import annotations

from omnia_api.services import accept_gauntlet, discovery, onboarding_registry
from omnia_api.services.onboarding_registry import OnboardingContext, scan

# ── shared building blocks ──────────────────────────────────────────────────────


def _clean_ctx(**over: object) -> OnboardingContext:
    """A regression-free discovery ASK turn (chips present, «Другое» open, clean)."""
    base: dict[str, object] = dict(
        action="ask",
        choices=("Премиум", "Дружелюбное", "Строгое"),
        allow_custom=True,
    )
    base.update(over)
    return OnboardingContext(**base)  # type: ignore[arg-type]


# ── the clean / inert baselines ─────────────────────────────────────────────────


def test_clean_ask_turn_passes() -> None:
    rep = scan(_clean_ctx())
    assert rep.judged
    assert rep.passed
    assert rep.classes == ()


def test_none_context_is_inert() -> None:
    rep = scan(None)
    assert not rep.judged
    assert rep.passed  # INERT — nothing to judge ≠ a failure
    assert rep.classes == ()


def test_empty_context_is_inert() -> None:
    rep = scan(OnboardingContext())
    assert not rep.judged
    assert rep.passed
    assert rep.classes == ()


def test_build_turn_is_inert() -> None:
    # A BUILD turn has no chips by design — nothing to judge against the contract.
    rep = scan(OnboardingContext(action="build"))
    assert not rep.judged
    assert rep.passed
    assert rep.classes == ()


def test_unknown_action_is_inert() -> None:
    rep = scan(OnboardingContext(action="???", choices=()))
    assert not rep.judged
    assert rep.passed


# ── one adversary fixture PER class — each MUST fail its assert ──────────────────


def test_adversary_bare_text_question() -> None:
    # An ASK with no chips → the card renders as bare text (V2.1 floor regressed).
    rep = scan(_clean_ctx(choices=()))
    assert not rep.passed
    assert onboarding_registry.BARE_TEXT_QUESTION in rep.classes


def test_adversary_trapped_no_custom() -> None:
    rep = scan(_clean_ctx(allow_custom=False))
    assert not rep.passed
    assert onboarding_registry.TRAPPED_NO_CUSTOM in rep.classes


def test_adversary_dirty_choices_over_cap() -> None:
    # More than _MAX_CHOICES chips would be truncated by the boundary normaliser.
    too_many = tuple(f"вариант{i}" for i in range(discovery._MAX_CHOICES + 2))
    rep = scan(_clean_ctx(choices=too_many))
    assert not rep.passed
    assert onboarding_registry.DIRTY_CHOICES in rep.classes


def test_adversary_dirty_choices_over_long_label() -> None:
    long_label = "x" * (discovery._MAX_CHOICE_LEN + 5)
    rep = scan(_clean_ctx(choices=("Да", long_label)))
    assert not rep.passed
    assert onboarding_registry.DIRTY_CHOICES in rep.classes


def test_adversary_dirty_choices_blank_label() -> None:
    rep = scan(_clean_ctx(choices=("Да", "   ")))
    assert not rep.passed
    assert onboarding_registry.DIRTY_CHOICES in rep.classes


def test_adversary_dirty_choices_duplicate() -> None:
    rep = scan(_clean_ctx(choices=("Да", "да")))  # case-insensitive dupe
    assert not rep.passed
    assert onboarding_registry.DIRTY_CHOICES in rep.classes


# ── single-source contract: clean iff _clean_choices is a no-op (R-04) ──────────


def test_dirty_verdict_tracks_clean_choices() -> None:
    # Whatever discovery's own normaliser would leave unchanged must read CLEAN,
    # and whatever it would change must read DIRTY — the rubric never drifts.
    pristine = ("Каталог", "Корзина", "Запись")
    assert discovery._clean_choices(list(pristine)) == pristine
    assert onboarding_registry.DIRTY_CHOICES not in scan(
        _clean_ctx(choices=pristine)
    ).classes

    needs_clean = ("Каталог ", "Каталог")  # trailing space + dupe
    assert discovery._clean_choices(list(needs_clean)) != needs_clean
    assert onboarding_registry.DIRTY_CHOICES in scan(
        _clean_ctx(choices=needs_clean)
    ).classes


# ── multiple classes + invariants ───────────────────────────────────────────────


def test_bare_text_and_trapped_compound() -> None:
    # No chips AND the escape disabled → both classes fire, in canonical order.
    rep = scan(_clean_ctx(choices=(), allow_custom=False))
    assert rep.classes == (
        onboarding_registry.BARE_TEXT_QUESTION,
        onboarding_registry.TRAPPED_NO_CUSTOM,
    )


def test_classes_are_in_canonical_order() -> None:
    rep = scan(_clean_ctx(choices=("дубль", "дубль"), allow_custom=False))
    # trapped (2nd) is reported before dirty (3rd) regardless of detection order.
    assert rep.classes == (
        onboarding_registry.TRAPPED_NO_CUSTOM,
        onboarding_registry.DIRTY_CHOICES,
    )


def test_deterministic_floor_is_clean() -> None:
    # The shipped fallback floor itself must pass the gate (no self-regression).
    floor = discovery._fallback_choices(0)
    rep = scan(_clean_ctx(choices=floor))
    assert rep.passed


def test_subscore_shape() -> None:
    rep = scan(_clean_ctx(allow_custom=False))
    sub = rep.subscore()
    assert sub["gate"] == "onboarding"
    assert sub["passed"] is False
    assert sub["judged"] is True
    assert onboarding_registry.TRAPPED_NO_CUSTOM in sub["classes"]
    assert sub["count"] >= 1


# ── the registry is WIRED into the gauntlet (not an orphan) ──────────────────────


async def test_gauntlet_onboarding_dial_off_by_default() -> None:
    verdict = await accept_gauntlet.run(onboarding_context=_clean_ctx())
    assert accept_gauntlet.ONBOARDING not in [g.gate for g in verdict.gates]


async def test_gauntlet_onboarding_dial_passes_clean_turn() -> None:
    verdict = await accept_gauntlet.run(onboarding=True, onboarding_context=_clean_ctx())
    leg = next(g for g in verdict.gates if g.gate == accept_gauntlet.ONBOARDING)
    assert leg.passed
    assert not leg.abstained


async def test_gauntlet_onboarding_dial_hard_fails_on_regression() -> None:
    ctx = _clean_ctx(allow_custom=False)
    verdict = await accept_gauntlet.run(onboarding=True, onboarding_context=ctx)
    leg = next(g for g in verdict.gates if g.gate == accept_gauntlet.ONBOARDING)
    assert not leg.passed
    assert leg in verdict.hard_failed  # a real finding blocks ship, not an abstain
    assert f"{accept_gauntlet.ONBOARDING}:{onboarding_registry.TRAPPED_NO_CUSTOM}" in (
        verdict.failed_classes
    )


async def test_gauntlet_onboarding_dial_inert_on_empty_context() -> None:
    verdict = await accept_gauntlet.run(onboarding=True)
    leg = next(g for g in verdict.gates if g.gate == accept_gauntlet.ONBOARDING)
    assert leg.passed
    assert not leg.abstained
    assert leg not in verdict.hard_failed
