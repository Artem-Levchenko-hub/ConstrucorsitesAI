"""Onboarding defect-registry — the money-free pillar-2 ratchet of the gauntlet (V2.7).

NORTH STAR pillar 2 (живой онбординг-попап): "пришёл промпт → СРАЗУ падают
поп-апы уточнений с чипами-вариантами + «Другое» с inline-ответом в том же
блоке". Every onboarding fix shipped so far lives as a guard inside
:mod:`discovery` — the deterministic chip FLOOR (``_fallback_choices`` so an ASK
never lands as bare text, V2.1), the always-open free-text escape
(``allow_custom``, the "Другое" chip), and the boundary normaliser
(``_clean_choices`` capping count/length and de-duping untrusted model output).
But none of those fixes was folded into a standing, falsifiable registry the way
the beauty defects are in :mod:`defect_registry` and the viral defects in
:mod:`viral_registry`. So an onboarding regression — a question served with no
chips, a chip set that traps the user with no "Другое", a flood of dirty/blank
model choices that skipped the normaliser — had no eternal gate that re-runs every
time and goes red on recurrence. This module is that registry.

It is the cheap CONTEXT-side sibling of the rendered legs (R-04, mirroring
:mod:`viral_registry`): it scores one *discovery ASK turn* — a structured
:class:`OnboardingContext` describing the question card the user is about to see —
against the falsifiable onboarding invariants, with **zero** browser and **zero**
LLM. Where a shipped guard already owns a truth it is reused, not re-proven:

  * the chip count/length/dedupe contract is single-sourced in
    :func:`discovery._clean_choices` (and its ``_MAX_CHOICES`` /
    ``_MAX_CHOICE_LEN`` caps) — the dirty-choices class fires iff re-running that
    very normaliser would *change* the offered chips, so the rubric can never
    drift away from what the boundary actually enforces.
  * the "every ASK carries chips" floor is the same floor
    :func:`discovery._fallback_choices` guarantees — the bare-text class fires
    when an ASK lands with that floor missing.

Each class carries an adversary fixture in the test suite that MUST fail its
assert (like the beauty / viral gates), and the registry is imported into the
acceptance gauntlet through an ``onboarding=`` dial so it is never an orphan. It
is pure, idempotent and fail-soft: a turn with nothing to judge (a BUILD turn, an
empty context) is INERT (passes), never raised (R-10).

Canon: R-01 (one deep registry fans the onboarding truth), R-04 (each guard's
truth lives in exactly one place — the chip-hygiene contract in
:func:`discovery._clean_choices`, reused here, never re-implemented), R-10 (fail
soft — a turn with no ASK to judge judges nothing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import discovery

# ── stable class ids — the vocabulary of the onboarding ratchet ─────────────────

#: An ASK turn landed with NO chips — the discovery card renders as bare text (the
#: UI hides the whole chip row, "Другое" included, when ``choices`` is empty), the
#: opposite of NORTH STAR pillar 2 «СРАЗУ падают поп-апы с чипами». The
#: deterministic floor (:func:`discovery._fallback_choices`) regressed.
BARE_TEXT_QUESTION = "bare-text-question"
#: An ASK turn disabled the free-text escape (``allow_custom`` False) — the
#: "Другое" chip that hands focus to the input is gone, so the offered chips TRAP
#: the user into the options. The «Другое» path must stay open on every ASK.
TRAPPED_NO_CUSTOM = "trapped-no-custom"
#: The offered chips skipped the boundary normaliser — too many (> ``_MAX_CHOICES``),
#: an over-long label (> ``_MAX_CHOICE_LEN``), a blank/whitespace label, or a
#: case-insensitive duplicate. Untrusted model output reached the card unclean
#: (the «модель-пустые-choices» class).
DIRTY_CHOICES = "dirty-choices"

#: Canonical order of every onboarding class. ``classes`` and the verdict table
#: render in this order.
ONBOARDING_CLASSES: tuple[str, ...] = (
    BARE_TEXT_QUESTION,
    TRAPPED_NO_CUSTOM,
    DIRTY_CHOICES,
)


# ── the turn under judgement ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class OnboardingContext:
    """One discovery turn, described structurally (no browser, no LLM).

    Only an ASK turn carries the chip contract, so the chip invariants are judged
    iff ``action == "ask"``. A BUILD turn (no chips by design), an unknown action,
    or an empty context is INERT — it judges nothing and passes.
    """

    #: The discovery action for this turn: ``"ask"`` (a question card with chips)
    #: or ``"build"`` (no chips). ``None`` ⇒ nothing to judge (inert).
    action: str | None = None
    #: The quick-reply chips offered beneath an ASK question — exactly what the UI
    #: renders as tappable buttons. Empty on an ASK ⇒ a bare-text question.
    choices: tuple[str, ...] = ()
    #: Whether the free-text "Другое" escape stays open on this ASK. Defaults to
    #: the discovery contract (always True); an explicit False traps the user.
    allow_custom: bool = True


# ── public result types ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OnboardingFinding:
    """One way the onboarding card regressed on this turn."""

    onboarding_class: str
    detail: str


@dataclass(frozen=True)
class OnboardingReport:
    """Verdict + JSON subscore of one onboarding-registry scan.

    Shares the pure context-scan interface (``judged`` / ``passed`` / ``classes``
    / ``summary`` / ``subscore``) with :class:`viral_registry.ViralReport` so the
    gauntlet folds it in through the same kind of adapter — it never abstains (a
    pure scan always has its evidence).
    """

    #: True iff this turn was an ASK with chip invariants to judge. When False the
    #: scan is INERT (no ASK card to judge) and ``passed`` is True.
    judged: bool
    findings: tuple[OnboardingFinding, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        if not self.judged:
            return True
        return not self.findings

    @property
    def classes(self) -> tuple[str, ...]:
        """The classes that fired, in canonical order (empty when inert/clean)."""
        hit = {f.onboarding_class for f in self.findings}
        return tuple(c for c in ONBOARDING_CLASSES if c in hit)

    def summary(self) -> str:
        if not self.judged:
            return "onboarding: inert (no ASK card to judge)"
        if self.passed:
            return "onboarding: clean (the question card held every invariant)"
        lines = [f"onboarding: {len(self.classes)} class(es) regressed:"]
        for f in self.findings:
            lines.append(f"  [{f.onboarding_class}] {f.detail}")
        return "\n".join(lines)

    def subscore(self) -> dict[str, Any]:
        return {
            "gate": "onboarding",
            "passed": self.passed,
            "judged": self.judged,
            "classes": list(self.classes),
            "count": len(self.findings),
            "detail": self.detail,
        }


# ── the rubric (pure) ───────────────────────────────────────────────────────────


def _dirty_reasons(choices: tuple[str, ...]) -> list[str]:
    """Why these chips would NOT survive the boundary normaliser (R-04).

    The verdict is single-sourced: chips are dirty iff re-running
    :func:`discovery._clean_choices` over them would change the set. This builds
    the human-readable reasons for the finding without re-deciding pass/fail.
    """
    reasons: list[str] = []
    if len(choices) > discovery._MAX_CHOICES:
        reasons.append(
            f"{len(choices)} chips offered (cap is {discovery._MAX_CHOICES})"
        )
    over_long = [c for c in choices if len(c) > discovery._MAX_CHOICE_LEN]
    if over_long:
        reasons.append(
            f"label longer than {discovery._MAX_CHOICE_LEN} chars: {over_long[0]!r}"
        )
    if any(not c.strip() for c in choices):
        reasons.append("a blank / whitespace-only chip")
    lowered = [c.strip().lower() for c in choices]
    if len(set(lowered)) != len(lowered):
        reasons.append("a case-insensitive duplicate chip")
    return reasons


def scan(ctx: OnboardingContext | None) -> OnboardingReport:
    """Score one discovery turn against the onboarding invariants (pure, fail-soft).

    Only an ASK turn carries the chip contract, so a BUILD / unknown-action /
    ``None`` / empty context is INERT (``judged=False`` → passes).
    """
    if ctx is None:
        return OnboardingReport(judged=False)

    action = (ctx.action or "").strip().lower()
    if action != discovery.ASK:
        # A BUILD turn (no chips by design) or an unrecognised action — nothing
        # to judge against the chip contract.
        return OnboardingReport(judged=False)

    findings: list[OnboardingFinding] = []
    detail: dict[str, Any] = {
        "action": discovery.ASK,
        "choice_count": len(ctx.choices),
        "allow_custom": ctx.allow_custom,
    }

    # ── (1) bare-text question — the chip floor regressed (V2.1) ───────────────
    if not ctx.choices:
        findings.append(
            OnboardingFinding(
                BARE_TEXT_QUESTION,
                "an ASK question landed with no chips — the discovery card renders "
                "as bare text (the UI hides the whole chip row when choices are "
                "empty), so the user gets no tappable options and no «Другое»",
            )
        )

    # ── (2) trapped — the «Другое» free-text escape is gone (R-04) ─────────────
    if not ctx.allow_custom:
        findings.append(
            OnboardingFinding(
                TRAPPED_NO_CUSTOM,
                "the free-text «Другое» escape is disabled on this ASK — the "
                "offered chips trap the user into the options instead of letting "
                "them answer in their own words",
            )
        )

    # ── (3) dirty chips — untrusted model output skipped the normaliser ────────
    # Single-source the verdict on discovery._clean_choices: the chips are dirty
    # iff re-running the very normaliser the boundary applies would change them.
    if ctx.choices:
        cleaned = discovery._clean_choices(list(ctx.choices))
        if cleaned != tuple(ctx.choices):
            reasons = _dirty_reasons(ctx.choices) or ["chips changed under cleanup"]
            detail["dirty_reasons"] = reasons
            findings.append(
                OnboardingFinding(
                    DIRTY_CHOICES,
                    "the offered chips would not survive the boundary normaliser — "
                    + "; ".join(reasons)
                    + " — untrusted model output reached the card unclean",
                )
            )

    return OnboardingReport(judged=True, findings=tuple(findings), detail=detail)


__all__ = [
    "BARE_TEXT_QUESTION",
    "DIRTY_CHOICES",
    "ONBOARDING_CLASSES",
    "TRAPPED_NO_CUSTOM",
    "OnboardingContext",
    "OnboardingFinding",
    "OnboardingReport",
    "scan",
]
