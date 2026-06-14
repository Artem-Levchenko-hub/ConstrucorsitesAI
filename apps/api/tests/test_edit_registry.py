"""V1.11 — the edit-loop regression registry ratchet (iteration).

The biggest admitted coverage gap: every other gate judges the FIRST generation,
but iteration is the dominant real usage. This registry asserts a SECOND prompt
does not silently break what the FIRST one earned. Every edit class carries an
adversary fixture that MUST fail its assert (like the beauty gates 5/5 · 7/5, the
viral registry V4.6, the onboarding registry V2.7 and the render registry V3.12),
proving the gate has teeth and is not a vacuous green. A targeted-only edit passes,
a non-edit / empty context is INERT, the verdict is single-sourced on the gauntlet's
own ``passed_classes`` and the snapshot path's section signatures (R-04), and the
registry is wired into the acceptance gauntlet through the ``edit=`` dial (it is not
an orphan). Pure Python — no browser, no LLM, no DB.
"""

from __future__ import annotations

from omnia_api.services import accept_gauntlet, edit_registry
from omnia_api.services.edit_registry import EditContext, EditSnapshot, scan

# ── shared building blocks ──────────────────────────────────────────────────────

#: A gen-1 render that scored a healthy set of gauntlet classes clean, with three
#: stable sections (the DOM identity the rollback path snapshots).
_GEN1 = EditSnapshot(
    passed_classes=frozenset({"contrast", "hierarchy-floor", "type-scale"}),
    sections={"hero": "h-1", "services": "s-1", "footer": "f-1"},
)


def _after(
    *,
    passed: frozenset[str] = frozenset({"contrast", "hierarchy-floor", "type-scale"}),
    sections: dict[str, str] | None = None,
) -> EditSnapshot:
    return EditSnapshot(
        passed_classes=passed,
        sections=sections if sections is not None else dict(_GEN1.sections),
    )


# ── the clean / inert baselines ─────────────────────────────────────────────────


def test_targeted_only_edit_passes() -> None:
    # The edit targets the hero and only the hero moved; classes all still clean.
    ctx = EditContext(
        before=_GEN1,
        after=_after(sections={"hero": "h-2", "services": "s-1", "footer": "f-1"}),
        targeted=frozenset({"hero"}),
    )
    rep = scan(ctx)
    assert rep.judged
    assert rep.passed
    assert rep.classes == ()


def test_additive_section_passes() -> None:
    # An additive edit adds a NEW section (a testimonials block) and touches nothing
    # already present — adding content is fine, only mutating/dropping is collateral.
    ctx = EditContext(
        before=_GEN1,
        after=_after(
            sections={"hero": "h-1", "services": "s-1", "footer": "f-1", "reviews": "r-1"}
        ),
        targeted=frozenset({"reviews"}),
    )
    rep = scan(ctx)
    assert rep.passed
    assert rep.classes == ()


def test_none_context_is_inert() -> None:
    rep = scan(None)
    assert not rep.judged
    assert rep.passed  # INERT — not an edit turn ≠ a failure
    assert rep.classes == ()


def test_empty_context_is_inert() -> None:
    rep = scan(EditContext())
    assert not rep.judged
    assert rep.passed


def test_missing_before_is_inert() -> None:
    # A first generation has no BEFORE to compare against — INERT, not judged.
    rep = scan(EditContext(after=_after()))
    assert not rep.judged
    assert rep.passed


def test_missing_after_is_inert() -> None:
    # An incomplete turn (no post-edit render yet) — nothing to compare.
    rep = scan(EditContext(before=_GEN1))
    assert not rep.judged
    assert rep.passed


# ── one adversary fixture PER class — each MUST fail its assert ──────────────────


def test_adversary_class_regression() -> None:
    # The edit kept the layout identical but a previously-clean class (contrast)
    # regressed — the second prompt broke quality the first had earned.
    ctx = EditContext(
        before=_GEN1,
        after=_after(passed=frozenset({"hierarchy-floor", "type-scale"})),
        targeted=frozenset({"hero"}),
    )
    rep = scan(ctx)
    assert not rep.passed
    assert edit_registry.CLASS_REGRESSION in rep.classes
    assert "contrast" in rep.summary()


def test_adversary_collateral_mutation() -> None:
    # The edit targeted only the hero, but the footer mutated as collateral — a
    # surface that should have stayed identical changed.
    ctx = EditContext(
        before=_GEN1,
        after=_after(sections={"hero": "h-2", "services": "s-1", "footer": "f-2"}),
        targeted=frozenset({"hero"}),
    )
    rep = scan(ctx)
    assert not rep.passed
    assert edit_registry.COLLATERAL_MUTATION in rep.classes
    assert edit_registry.CLASS_REGRESSION not in rep.classes  # classes held


def test_adversary_collateral_drop() -> None:
    # An untargeted section vanished entirely — a dropped section is collateral too.
    ctx = EditContext(
        before=_GEN1,
        after=_after(sections={"hero": "h-2", "services": "s-1"}),  # footer gone
        targeted=frozenset({"hero"}),
    )
    rep = scan(ctx)
    assert not rep.passed
    assert edit_registry.COLLATERAL_MUTATION in rep.classes
    assert "footer (dropped)" in rep.summary()


def test_adversary_rollback_divergence_sections() -> None:
    # The rollback snapshot does NOT restore the gen-1 section signatures — the
    # «вернуться назад» promise is broken.
    bad_rollback = EditSnapshot(
        passed_classes=_GEN1.passed_classes,
        sections={"hero": "h-DIFFERENT", "services": "s-1", "footer": "f-1"},
    )
    ctx = EditContext(
        before=_GEN1,
        after=_after(sections={"hero": "h-2", "services": "s-1", "footer": "f-1"}),
        targeted=frozenset({"hero"}),
        rollback=bad_rollback,
    )
    rep = scan(ctx)
    assert not rep.passed
    assert edit_registry.ROLLBACK_DIVERGENCE in rep.classes


def test_adversary_rollback_divergence_classes() -> None:
    # Rollback restored the sections byte-for-byte but lost a clean class — still a
    # divergence (the restored surface is not the gen-1 we promised to return to).
    bad_rollback = EditSnapshot(
        passed_classes=frozenset({"contrast"}),  # lost two classes
        sections=dict(_GEN1.sections),
    )
    ctx = EditContext(
        before=_GEN1,
        after=_after(sections={"hero": "h-2", "services": "s-1", "footer": "f-1"}),
        targeted=frozenset({"hero"}),
        rollback=bad_rollback,
    )
    rep = scan(ctx)
    assert not rep.passed
    assert edit_registry.ROLLBACK_DIVERGENCE in rep.classes


def test_clean_rollback_passes() -> None:
    # A faithful rollback (byte-identical sections + same clean classes) does NOT fire.
    ctx = EditContext(
        before=_GEN1,
        after=_after(sections={"hero": "h-2", "services": "s-1", "footer": "f-1"}),
        targeted=frozenset({"hero"}),
        rollback=EditSnapshot(
            passed_classes=_GEN1.passed_classes, sections=dict(_GEN1.sections)
        ),
    )
    rep = scan(ctx)
    assert rep.passed
    assert edit_registry.ROLLBACK_DIVERGENCE not in rep.classes


# ── invariants: ordering, compounding, subscore ─────────────────────────────────


def test_compound_fires_in_canonical_order() -> None:
    # An edit that regresses a class AND mutates an untargeted section reports both,
    # in canonical order.
    ctx = EditContext(
        before=_GEN1,
        after=_after(
            passed=frozenset({"hierarchy-floor", "type-scale"}),
            sections={"hero": "h-2", "services": "s-CHANGED", "footer": "f-1"},
        ),
        targeted=frozenset({"hero"}),
    )
    rep = scan(ctx)
    assert rep.classes == (
        edit_registry.CLASS_REGRESSION,
        edit_registry.COLLATERAL_MUTATION,
    )


def test_subscore_shape() -> None:
    ctx = EditContext(
        before=_GEN1,
        after=_after(passed=frozenset({"hierarchy-floor"})),
        targeted=frozenset({"hero"}),
    )
    sub = scan(ctx).subscore()
    assert sub["gate"] == "edit"
    assert sub["passed"] is False
    assert sub["judged"] is True
    assert edit_registry.CLASS_REGRESSION in sub["classes"]
    assert sub["count"] >= 1


def test_inert_subscore_shape() -> None:
    sub = scan(None).subscore()
    assert sub["gate"] == "edit"
    assert sub["passed"] is True
    assert sub["judged"] is False
    assert sub["classes"] == []


# ── single-source contract: verdict tracks the gauntlet's own passed_classes ─────


def test_verdict_tracks_gauntlet_passed_classes() -> None:
    # A class disappearing from AFTER.passed_classes (the exact set a gauntlet run
    # reports) is precisely what fires class-regression — the rubric never drifts
    # from what the quality apparatus records.
    before = EditSnapshot(passed_classes=frozenset({"a", "b"}))
    same = EditContext(before=before, after=EditSnapshot(passed_classes=frozenset({"a", "b"})))
    assert edit_registry.CLASS_REGRESSION not in scan(same).classes
    lost = EditContext(before=before, after=EditSnapshot(passed_classes=frozenset({"a"})))
    assert edit_registry.CLASS_REGRESSION in scan(lost).classes


# ── the registry is WIRED into the gauntlet (not an orphan) ──────────────────────


async def test_gauntlet_edit_dial_off_by_default() -> None:
    ctx = EditContext(before=_GEN1, after=_after(), targeted=frozenset({"hero"}))
    verdict = await accept_gauntlet.run(edit_context=ctx)
    assert accept_gauntlet.EDIT not in [g.gate for g in verdict.gates]


async def test_gauntlet_edit_dial_passes_clean_edit() -> None:
    ctx = EditContext(
        before=_GEN1,
        after=_after(sections={"hero": "h-2", "services": "s-1", "footer": "f-1"}),
        targeted=frozenset({"hero"}),
    )
    verdict = await accept_gauntlet.run(edit=True, edit_context=ctx)
    leg = next(g for g in verdict.gates if g.gate == accept_gauntlet.EDIT)
    assert leg.passed
    assert not leg.abstained


async def test_gauntlet_edit_dial_hard_fails_on_regression() -> None:
    ctx = EditContext(
        before=_GEN1,
        after=_after(passed=frozenset({"hierarchy-floor", "type-scale"})),
        targeted=frozenset({"hero"}),
    )
    verdict = await accept_gauntlet.run(edit=True, edit_context=ctx)
    leg = next(g for g in verdict.gates if g.gate == accept_gauntlet.EDIT)
    assert not leg.passed
    assert leg in verdict.hard_failed  # a real finding blocks ship, not an abstain
    assert f"{accept_gauntlet.EDIT}:{edit_registry.CLASS_REGRESSION}" in (
        verdict.failed_classes
    )


async def test_gauntlet_edit_dial_inert_on_empty_context() -> None:
    verdict = await accept_gauntlet.run(edit=True)
    leg = next(g for g in verdict.gates if g.gate == accept_gauntlet.EDIT)
    assert leg.passed
    assert not leg.abstained
    assert leg not in verdict.hard_failed
