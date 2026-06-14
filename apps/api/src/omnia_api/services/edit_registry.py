"""Edit-loop regression registry — the money-free iteration ratchet (V1.11).

The biggest *admitted* coverage gap of the whole quality apparatus: every other
gate in the gauntlet (defect / wow-dom / perf-a11y / chip-pixel / taste /
hierarchy / data / compose / reference / onboarding / render / viral) judges the
**first** generation. But the dominant real usage of the product is ITERATION —
the user keeps sending prompts to refine an app («смени акцентный цвет», «добавь
секцию отзывов») — and NORTH STAR's core promise («без потолка, твори дальше» +
the per-prompt «вернуться назад» button) lives entirely on that loop. Nothing
asserted that a *second* prompt does not silently break what the *first* one got
right: a previously-passing gauntlet class quietly regressing, an untargeted
section mutating as collateral of a narrow edit, or the rollback snapshot failing
to restore the gen-1 surface byte-for-byte. That whole class — «2-й-промпт-ломает-
1-й» — ran ungated.

This module is that ratchet — the iteration sibling of :mod:`defect_registry`
(pillar 1 first-gen source-scan), :mod:`render_registry` (pillar 3),
:mod:`onboarding_registry` (pillar 2) and :mod:`viral_registry` (pillar 4). Like
them it is a pure, deterministic, money-free CONTEXT-scan: it scores ONE edit turn
— a :class:`EditContext` carrying a structural BEFORE snapshot (gen-1), an AFTER
snapshot (the post-edit re-render) and the set of sections the edit was meant to
touch — against the falsifiable edit-loop invariants, with **zero** browser and
**zero** LLM. (The live leg — wiring a real generation → edit-prompt → re-render
through this scan — is the paid owner-corpus / paid-run-manifest step; the
falsifiable rubric ships money-free now, exactly as the other registries did.)

It is single-sourced on the gauntlet's own output (R-04): the BEFORE/AFTER
``passed_classes`` are exactly the classes a gauntlet run reported clean, and the
section signatures are the DOM-identity the rollback path (``rollback.py`` /
``snapshots.py``) already snapshots — so the rubric can never drift from what the
quality apparatus and the timeline actually record. A turn that is not an edit
(no BEFORE, or no AFTER — a first generation has no pair to compare) is INERT
(passes), never raised (R-10).

Canon: R-01 (one deep registry fans the iteration-regression truth), R-04 (the
verdict reuses the gauntlet's own ``passed_classes`` and the snapshot's own
section signatures — no re-implemented metric), R-10 (fail soft — a non-edit turn
has nothing to compare and compares nothing).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# ── stable class ids — the vocabulary of the edit ratchet ───────────────────────

#: A gauntlet class that was CLEAN in the gen-1 (BEFORE) render is no longer clean
#: in the post-edit (AFTER) render. The second prompt regressed quality the first
#: prompt had already earned — a contrast that held now fails, a hierarchy floor
#: that passed now sinks. This is the core «2-й-промпт-ломает-1-й» defect.
CLASS_REGRESSION = "class-regression"
#: A section present in the BEFORE render changed (its content signature moved) or
#: dropped entirely in the AFTER render WITHOUT being one of the edit's targeted
#: sections. The edit was «change the accent colour» but the hero re-flowed too —
#: collateral damage the user never asked for (the surface should be identical
#: except where the prompt explicitly steered).
COLLATERAL_MUTATION = "collateral-mutation"
#: The rollback snapshot does NOT restore the gen-1 surface: the ROLLBACK snapshot's
#: section signatures (or its clean-class set) diverge from the BEFORE snapshot. The
#: per-prompt «вернуться назад» promise (rollback to a byte-identical gen-1 DOM
#: identity, NORTH STAR) is broken. INERT when no rollback snapshot is supplied.
ROLLBACK_DIVERGENCE = "rollback-divergence"

#: Canonical order of every edit class. ``classes`` and the verdict table render
#: in this order.
EDIT_CLASSES: tuple[str, ...] = (
    CLASS_REGRESSION,
    COLLATERAL_MUTATION,
    ROLLBACK_DIVERGENCE,
)


# ── the snapshots + turn under judgement ────────────────────────────────────────


@dataclass(frozen=True)
class EditSnapshot:
    """One render's structural identity, as the gauntlet + timeline already record it.

    * ``passed_classes`` — the gauntlet classes that scored CLEAN on this render
      (single-sourced on a real :func:`accept_gauntlet.run` verdict, R-04).
    * ``sections`` — a section-name → content-signature map: the DOM identity the
      rollback / snapshot path persists (a stable hash per section). Two renders
      are byte-identical on a section iff their signatures match.
    """

    passed_classes: frozenset[str] = frozenset()
    sections: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EditContext:
    """One edit turn, described structurally (no browser, no LLM).

    Only a turn that carries BOTH a BEFORE (gen-1) and an AFTER (post-edit) snapshot
    is an edit with something to compare; a first generation (no BEFORE) or an
    incomplete turn (no AFTER) is INERT and passes. ``targeted`` names the sections
    the edit explicitly meant to change (everything else must stay identical).
    ``rollback`` is the snapshot restored from the gen-1 timeline entry, judged
    against BEFORE when present (omitted ⇒ that class is not exercised).
    """

    before: EditSnapshot | None = None
    after: EditSnapshot | None = None
    targeted: frozenset[str] = frozenset()
    rollback: EditSnapshot | None = None


# ── public result types ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EditFinding:
    """One way the second prompt regressed what the first prompt earned."""

    edit_class: str
    detail: str


@dataclass(frozen=True)
class EditReport:
    """Verdict + JSON subscore of one edit-registry scan.

    Shares the pure context-scan interface (``judged`` / ``passed`` / ``classes``
    / ``summary`` / ``subscore``) with :class:`render_registry.RenderReport` so the
    gauntlet folds it in through the same kind of adapter — it never abstains (a
    pure scan always has its evidence).
    """

    #: True iff this turn carried a BEFORE+AFTER pair with invariants to judge. When
    #: False the scan is INERT (not an edit turn) and ``passed`` is True.
    judged: bool
    findings: tuple[EditFinding, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        if not self.judged:
            return True
        return not self.findings

    @property
    def classes(self) -> tuple[str, ...]:
        """The classes that fired, in canonical order (empty when inert/clean)."""
        hit = {f.edit_class for f in self.findings}
        return tuple(c for c in EDIT_CLASSES if c in hit)

    def summary(self) -> str:
        if not self.judged:
            return "edit: inert (not an edit turn — nothing to compare)"
        if self.passed:
            return "edit: clean (the second prompt kept everything the first earned)"
        lines = [f"edit: {len(self.classes)} class(es) regressed:"]
        for f in self.findings:
            lines.append(f"  [{f.edit_class}] {f.detail}")
        return "\n".join(lines)

    def subscore(self) -> dict[str, Any]:
        return {
            "gate": "edit",
            "passed": self.passed,
            "judged": self.judged,
            "classes": list(self.classes),
            "count": len(self.findings),
            "detail": self.detail,
        }


# ── the rubric (pure) ───────────────────────────────────────────────────────────


def scan(ctx: EditContext | None) -> EditReport:
    """Score one edit turn against the iteration-regression invariants.

    Pure and fail-soft: only a turn carrying BOTH a BEFORE and an AFTER snapshot is
    an edit with something to compare, so a ``None`` context, a missing BEFORE (a
    first generation) or a missing AFTER (an incomplete turn) is INERT
    (``judged=False`` → passes). The verdict is single-sourced on the gauntlet's own
    ``passed_classes`` and the snapshot path's own section signatures (R-04), so the
    rubric tracks what the quality apparatus and the timeline actually record.
    """
    if ctx is None or ctx.before is None or ctx.after is None:
        return EditReport(judged=False)

    before, after, targeted = ctx.before, ctx.after, ctx.targeted

    findings: list[EditFinding] = []

    # ── (1) class-regression — a previously-clean gauntlet class no longer holds ──
    regressed = sorted(before.passed_classes - after.passed_classes)
    if regressed:
        findings.append(
            EditFinding(
                CLASS_REGRESSION,
                "the second prompt regressed gauntlet class(es) the first generation "
                f"had clean: {', '.join(regressed)} — iteration broke earned quality",
            )
        )

    # ── (2) collateral-mutation — an untargeted section moved or dropped ──────────
    collateral: list[str] = []
    for name, sig in before.sections.items():
        if name in targeted:
            continue  # the edit explicitly steered this section — change is expected
        if name not in after.sections:
            collateral.append(f"{name} (dropped)")
        elif after.sections[name] != sig:
            collateral.append(f"{name} (mutated)")
    if collateral:
        findings.append(
            EditFinding(
                COLLATERAL_MUTATION,
                "the edit changed section(s) it never targeted: "
                f"{', '.join(sorted(collateral))} — collateral damage to a surface "
                "that should stay identical except where the prompt steered "
                f"(targeted: {', '.join(sorted(targeted)) or 'none'})",
            )
        )

    # ── (3) rollback-divergence — «вернуться назад» fails to restore gen-1 ────────
    rollback = ctx.rollback
    if rollback is not None and (
        dict(rollback.sections) != dict(before.sections)
        or rollback.passed_classes != before.passed_classes
    ):
        findings.append(
            EditFinding(
                ROLLBACK_DIVERGENCE,
                "the rollback snapshot does not restore the gen-1 surface — its "
                "section signatures or clean-class set diverge from BEFORE, so the "
                "per-prompt «вернуться назад» promise is broken",
            )
        )

    detail: dict[str, Any] = {
        "before_passed": sorted(before.passed_classes),
        "after_passed": sorted(after.passed_classes),
        "targeted": sorted(targeted),
        "before_sections": sorted(before.sections),
        "after_sections": sorted(after.sections),
        "has_rollback": rollback is not None,
    }

    return EditReport(judged=True, findings=tuple(findings), detail=detail)


__all__ = [
    "CLASS_REGRESSION",
    "COLLATERAL_MUTATION",
    "EDIT_CLASSES",
    "ROLLBACK_DIVERGENCE",
    "EditContext",
    "EditFinding",
    "EditReport",
    "EditSnapshot",
    "scan",
]
