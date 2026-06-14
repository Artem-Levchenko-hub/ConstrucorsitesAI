"""Viral defect-registry — the money-free pillar-4 ratchet of the gauntlet (V4.6).

NORTH STAR pillar 4 (виральная шарабельность): "поделился → коллега открыл → за
секунды завёл свой аккаунт → пользуется продуктом". Every shipped viral primitive
— the share→deploy redirect guard (V4.6.1), the stranger first-paint budget
(V4.0b), the zero-signup fork (V4.1b) with its isolation asserts, and the
return-edge composer-inherit (V4.2b) — landed as an *isolated* unit-proof. None
was folded into a standing, falsifiable registry the way the beauty defects are in
:mod:`defect_registry`. So a viral regression — a link served before the build is
done, a remix CTA that silently vanished, a fork that shares its source's
identity, a forked niche dropped before the composer sees it — had no eternal
gate that re-runs every time and goes red on recurrence. This module is that
registry.

It is the cheap CONTEXT-side sibling of the rendered legs (R-04, mirroring
:mod:`compose_gate`): it scores one *share→fork episode* — a structured
``ViralContext`` describing what the share link served and what the fork
inherited — against the falsifiable viral invariants, with **zero** browser and
**zero** LLM. Where a shipped guard already owns a truth it is reused, not
re-proven:

  * the served-surface health segment **folds** ``first_paint_gate`` — the
    registry calls its pure :func:`first_paint_gate.evaluate_observation` scorer
    over the share-link observation, so ``auth-wall`` / ``empty-shell`` /
    ``slow-first-paint`` are surfaced here without re-implementing the rubric
    (first_paint's own docstring already declares it "folds into the viral
    defect-registry (V4.6)").
  * the first-paint budget and the seeded-row floor stay single-sourced in
    :mod:`render_settle` / :mod:`data_gate` via that fold.

Each class carries an adversary fixture in the test suite that MUST fail its
assert (like the beauty gates), and the registry is imported into the acceptance
gauntlet through a ``viral=`` dial so it is never an orphan. It is pure,
idempotent and fail-soft: a context with nothing to judge is INERT (passes),
never raised (R-10).

Canon: R-01 (one deep registry fans the viral truth), R-04 (each guard's truth
lives in exactly one place — first-paint's in :mod:`first_paint_gate`, reused
here), R-10 (fail soft — a half-described episode judges only what it can).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import first_paint_gate

# ── stable class ids — the vocabulary of the viral ratchet ──────────────────────

#: A share link served a live app surface before the build reached ``done`` — the
#: stranger lands on a half-built dev container (the V4.6.1 redirect-leak class).
LINK_BEFORE_DONE = "link-served-before-done"
#: The served surface carries no remix/fork entry-point — the viral loop is dead
#: at the first hop (the "Remix this" CTA, V4.1b, silently regressed).
DEAD_REMIX_CTA = "dead-remix-cta"
#: A fork shares its source project's identity (same / missing ``project_id``) —
#: a tenant leak: the colleague's edits would mutate the original (V4.1b isolation).
LEAKED_TENANT_FORK = "leaked-tenant-fork"
#: The source's niche (``design_preset_id`` / ``discovery_spec``) was dropped on
#: the fork — the remixer faces a blank onboarding instead of a 1-click re-gen, so
#: k-factor never compounds (the V4.2b return-edge composer-inherit regressed).
DROPPED_SEED_PARAM = "dropped-seed-param"

#: Canonical order of every viral class, including the folded first-paint checks
#: (reused from :mod:`first_paint_gate`, not re-declared — R-04). ``classes`` and
#: the verdict table render in this order.
VIRAL_CLASSES: tuple[str, ...] = (
    LINK_BEFORE_DONE,
    first_paint_gate.AUTH_WALL,
    first_paint_gate.EMPTY_SHELL,
    first_paint_gate.SLOW_FIRST_PAINT,
    DEAD_REMIX_CTA,
    LEAKED_TENANT_FORK,
    DROPPED_SEED_PARAM,
)


# ── the episode under judgement ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ViralContext:
    """One share→fork episode, described structurally (no browser, no LLM).

    Every field is optional: a context that describes only the served surface
    judges only the share segment, one that describes only the fork judges only
    the fork segment, and an empty context is INERT. A segment is judged iff its
    inputs are present, so a half-described episode never false-fails.
    """

    # ── served-surface segment ──────────────────────────────────────────────
    #: The build phase the project was in when its live surface was served. When
    #: not ``"done"`` the share link should serve a tasteful "building" stub, not
    #: the live dev surface. ``None`` ⇒ the phase segment is not judged.
    phase: str | None = None
    #: Whether a real live app surface (a dev/app URL) — not the branded building
    #: stub — was actually served for that ``phase``.
    served_live: bool = False
    #: Whether the served surface carries a remix/fork entry-point (the
    #: "Remix this" CTA / ``/remix`` link). ``None`` ⇒ not checked (inert).
    remix_cta_present: bool | None = None
    #: A raw first-paint observation (exactly what
    #: :func:`first_paint_gate.evaluate_observation` consumes). When present the
    #: served-surface health is scored by folding that gate. ``None`` ⇒ inert.
    served_observation: first_paint_gate.Obs | None = None

    # ── fork segment ────────────────────────────────────────────────────────
    #: The source project's id (the app that was shared).
    source_project_id: str | None = None
    #: The fork's id. Judged for distinctness only when both ids are present.
    fork_project_id: str | None = None
    #: The source's design preset (its frozen niche/look).
    source_design_preset_id: str | None = None
    #: The fork's design preset — must inherit the source's, not blank.
    fork_design_preset_id: str | None = None
    #: The source's persisted discovery answers (its onboarding niche).
    source_discovery_spec: dict[str, Any] | None = None
    #: The fork's discovery spec — must inherit a non-empty source spec.
    fork_discovery_spec: dict[str, Any] | None = None


# ── public result types ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ViralFinding:
    """One way the viral loop regressed in this episode."""

    viral_class: str
    detail: str


@dataclass(frozen=True)
class ViralReport:
    """Verdict + JSON subscore of one viral-registry scan.

    Shares the pure source/context-scan interface (``judged`` / ``passed`` /
    ``classes`` / ``summary`` / ``subscore``) with :class:`compose_gate.ComposeReport`
    so the gauntlet folds it in through the same kind of adapter as the compose
    floor — it never abstains (a pure scan always has its evidence).
    """

    #: True iff at least one segment had inputs to judge. When False the scan is
    #: INERT (no episode to judge) and ``passed`` is True.
    judged: bool
    findings: tuple[ViralFinding, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        if not self.judged:
            return True
        return not self.findings

    @property
    def classes(self) -> tuple[str, ...]:
        """The classes that fired, in canonical order (empty when inert/clean)."""
        hit = {f.viral_class for f in self.findings}
        return tuple(c for c in VIRAL_CLASSES if c in hit)

    def summary(self) -> str:
        if not self.judged:
            return "viral: inert (no share→fork episode to judge)"
        if self.passed:
            return "viral: clean (every judged segment held)"
        lines = [f"viral: {len(self.classes)} class(es) regressed:"]
        for f in self.findings:
            lines.append(f"  [{f.viral_class}] {f.detail}")
        return "\n".join(lines)

    def subscore(self) -> dict[str, Any]:
        return {
            "gate": "viral",
            "passed": self.passed,
            "judged": self.judged,
            "classes": list(self.classes),
            "count": len(self.findings),
            "detail": self.detail,
        }


# ── the rubric (pure) ───────────────────────────────────────────────────────────


def _seed_nonempty(preset: str | None, spec: dict[str, Any] | None) -> bool:
    """True if the source carried any niche worth inheriting."""
    return bool(preset and preset.strip()) or bool(spec)


def scan(ctx: ViralContext | None) -> ViralReport:
    """Score one share→fork episode against the viral invariants (pure, fail-soft).

    Each segment is judged independently and only when its inputs are present, so
    a partially-described episode judges what it can and stays inert on the rest.
    A ``None`` / fully-empty context is INERT (``judged=False`` → passes).
    """
    if ctx is None:
        return ViralReport(judged=False)

    findings: list[ViralFinding] = []
    judged = False
    detail: dict[str, Any] = {}

    # ── (1) served BEFORE done — the redirect-leak class (V4.6.1) ─────────────
    if ctx.phase is not None:
        judged = True
        detail["phase"] = ctx.phase
        detail["served_live"] = ctx.served_live
        if ctx.served_live and ctx.phase != "done":
            findings.append(
                ViralFinding(
                    LINK_BEFORE_DONE,
                    f"a live app surface was served while phase={ctx.phase!r} "
                    "(≠ 'done') — the stranger lands on a half-built container, "
                    "not a tasteful 'building' stub",
                )
            )

    # ── (2) served-surface health — FOLD first_paint_gate (R-04) ──────────────
    if ctx.served_observation is not None:
        judged = True
        fp = first_paint_gate.evaluate_observation(ctx.served_observation)
        detail["first_paint"] = fp.subscore()
        for f in fp.findings:
            findings.append(ViralFinding(f.check, f.detail))

    # ── (3) dead remix CTA — the loop is dead at the first hop (V4.1b) ─────────
    if ctx.remix_cta_present is not None:
        judged = True
        detail["remix_cta_present"] = ctx.remix_cta_present
        if not ctx.remix_cta_present:
            findings.append(
                ViralFinding(
                    DEAD_REMIX_CTA,
                    "the served surface carries no remix/fork entry-point — the "
                    "colleague has no one-click way to make it theirs, so the "
                    "viral loop dies at the first hop",
                )
            )

    # ── (4) tenant leak — fork must be its own project (V4.1b isolation) ───────
    if ctx.source_project_id is not None and ctx.fork_project_id is not None:
        judged = True
        detail["source_project_id"] = ctx.source_project_id
        detail["fork_project_id"] = ctx.fork_project_id
        if not ctx.fork_project_id or ctx.fork_project_id == ctx.source_project_id:
            findings.append(
                ViralFinding(
                    LEAKED_TENANT_FORK,
                    f"the fork's project id ({ctx.fork_project_id!r}) is missing or "
                    f"identical to the source ({ctx.source_project_id!r}) — a tenant "
                    "leak: the colleague's edits would mutate the original app",
                )
            )

        # ── (5) dropped seed — the niche must flow to the fork (V4.2b) ─────────
        # Only meaningful once we know this IS a fork (both ids present).
        if _seed_nonempty(ctx.source_design_preset_id, ctx.source_discovery_spec):
            dropped: list[str] = []
            preset_inherited = bool(
                ctx.fork_design_preset_id
                and ctx.fork_design_preset_id == ctx.source_design_preset_id
            )
            if (
                ctx.source_design_preset_id
                and ctx.source_design_preset_id.strip()
                and not preset_inherited
            ):
                dropped.append(
                    f"design_preset_id ({ctx.source_design_preset_id!r} → "
                    f"{ctx.fork_design_preset_id!r})"
                )
            if ctx.source_discovery_spec and not ctx.fork_discovery_spec:
                dropped.append("discovery_spec (source had answers, fork is blank)")
            if dropped:
                findings.append(
                    ViralFinding(
                        DROPPED_SEED_PARAM,
                        "the source's niche was dropped on the fork — "
                        + "; ".join(dropped)
                        + " — the remixer faces a blank onboarding instead of a "
                        "1-click re-gen, so k-factor never compounds",
                    )
                )

    return ViralReport(judged=judged, findings=tuple(findings), detail=detail)


__all__ = [
    "DEAD_REMIX_CTA",
    "DROPPED_SEED_PARAM",
    "LEAKED_TENANT_FORK",
    "LINK_BEFORE_DONE",
    "VIRAL_CLASSES",
    "ViralContext",
    "ViralFinding",
    "ViralReport",
    "scan",
]
