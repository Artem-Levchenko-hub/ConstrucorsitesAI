"""Render defect-registry — the money-free pillar-3 ratchet of the gauntlet (V3.12).

NORTH STAR pillar 3 (магия live-рендера + гипноз-анимации): the generated app
must *narrate its own design reasoning while it paints* — the «AI рисует» moment —
with the art-director's palette surfacing as swatches in the chat (V3.4), the
section list and motion signature driving the live narration overlay (V3.10), and
a brand-coloured joy note on completion (V3.8). Every one of those shipped units
hangs off ONE transport: the structured payload that
:func:`art_director_writer.parse_brief` extracts from the pass-1 brief and a single
``omnia:brief`` event carries to the client. If that payload comes through barren —
no palette, no motion, no sections — the spectacle silently degrades to a flat,
mute render: swatches show nothing, the narration overlay has nothing to say, the
section walk has no structure. Like the other three pillars, those fixes lived only
as guards (the regexes inside :mod:`art_director_writer`) and never as a standing,
falsifiable registry that re-runs every time and goes red on recurrence. This module
is that registry — the pillar-3 sibling of :mod:`defect_registry` (pillar 1),
:mod:`onboarding_registry` (pillar 2) and :mod:`viral_registry` (pillar 4).

It is a pure CONTEXT-side scan (R-04, mirroring :mod:`onboarding_registry`): it
scores one *rendered generation turn* — a :class:`RenderContext` carrying the
art-director brief the live render will narrate from — against the falsifiable
pillar-3 invariants, with **zero** browser and **zero** LLM. The verdict is
single-sourced on the very boundary that decides what the client receives:

  * the narratable payload is exactly :func:`art_director_writer.parse_brief`'s
    output — the same dict the ``omnia:brief`` event ships — so the rubric can
    never drift away from what the live render actually gets. A class fires iff
    re-running that extraction would hand the client a payload too barren to drive
    its pillar-3 surface.

Each class carries an adversary fixture in the test suite that MUST fail its assert
(like the beauty / onboarding / viral gates), and the registry is imported into the
acceptance gauntlet through a ``render=`` dial so it is never an orphan. It is
pure, idempotent and fail-soft: a turn with nothing to narrate (no brief — a
non-generation turn, an empty context) is INERT (passes), never raised (R-10).

Canon: R-01 (one deep registry fans the pillar-3 truth), R-04 (the narratable
contract lives in exactly one place — :func:`art_director_writer.parse_brief`,
reused here, never re-implemented), R-10 (fail soft — a turn with no brief to
narrate narrates nothing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import art_director_writer

# ── stable class ids — the vocabulary of the render ratchet ─────────────────────

#: A brief was produced but :func:`art_director_writer.parse_brief` surfaced NO
#: narratable signal at all — empty palette AND no fonts AND no sections AND no
#: motion. The ``omnia:brief`` event would carry an empty husk: no swatches, no
#: narration overlay, no section walk. The live render degrades to the flat, mute
#: state pillar 3 exists to defeat (the «AI рисует» moment never happens).
SILENT_RENDER = "silent-render"
#: The payload carries other signal but its PALETTE is empty — the art-director's
#: colour story never reaches the chat, so the V3.4 brief-swatches render blank.
#: (Even ``parse_brief``'s "first few distinct hexes" fallback found nothing.)
SWATCHLESS_RENDER = "swatchless-render"
#: The payload carries other signal but its MOTION-СИГНАТУРА is empty — the one
#: living-layer line the narration (V3.10) names while it paints dropped, so the
#: hypnosis layer that is pillar 3's core has nothing to announce.
MOTIONLESS_RENDER = "motionless-render"

#: Canonical order of every render class. ``classes`` and the verdict table render
#: in this order.
RENDER_CLASSES: tuple[str, ...] = (
    SILENT_RENDER,
    SWATCHLESS_RENDER,
    MOTIONLESS_RENDER,
)


# ── the turn under judgement ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class RenderContext:
    """One rendered generation turn, described structurally (no browser, no LLM).

    Only a turn that produced an art-director brief carries the pillar-3 narration
    contract, so the invariants are judged iff a non-empty ``brief`` is present. A
    non-generation turn (no brief), an empty/whitespace brief, or an empty context
    is INERT — it narrates nothing and passes.
    """

    #: The art-director pass-1 brief the live render narrates from (palette / fonts
    #: / motion signature / section list, in the format ``parse_brief`` reads).
    #: ``None`` / empty / whitespace ⇒ nothing to narrate (inert).
    brief: str | None = None


# ── public result types ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RenderFinding:
    """One way the live-render narration regressed on this turn."""

    render_class: str
    detail: str


@dataclass(frozen=True)
class RenderReport:
    """Verdict + JSON subscore of one render-registry scan.

    Shares the pure context-scan interface (``judged`` / ``passed`` / ``classes``
    / ``summary`` / ``subscore``) with :class:`onboarding_registry.OnboardingReport`
    so the gauntlet folds it in through the same kind of adapter — it never abstains
    (a pure scan always has its evidence).
    """

    #: True iff this turn carried a brief with narration invariants to judge. When
    #: False the scan is INERT (no brief to narrate) and ``passed`` is True.
    judged: bool
    findings: tuple[RenderFinding, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        if not self.judged:
            return True
        return not self.findings

    @property
    def classes(self) -> tuple[str, ...]:
        """The classes that fired, in canonical order (empty when inert/clean)."""
        hit = {f.render_class for f in self.findings}
        return tuple(c for c in RENDER_CLASSES if c in hit)

    def summary(self) -> str:
        if not self.judged:
            return "render: inert (no brief to narrate)"
        if self.passed:
            return "render: clean (the brief drives a full pillar-3 narration)"
        lines = [f"render: {len(self.classes)} class(es) regressed:"]
        for f in self.findings:
            lines.append(f"  [{f.render_class}] {f.detail}")
        return "\n".join(lines)

    def subscore(self) -> dict[str, Any]:
        return {
            "gate": "render",
            "passed": self.passed,
            "judged": self.judged,
            "classes": list(self.classes),
            "count": len(self.findings),
            "detail": self.detail,
        }


# ── the rubric (pure) ───────────────────────────────────────────────────────────


def scan(ctx: RenderContext | None) -> RenderReport:
    """Score one rendered turn against the pillar-3 narration invariants.

    Pure and fail-soft: only a turn carrying a non-empty brief narrates, so a
    ``None`` context, an empty context, or an empty/whitespace brief is INERT
    (``judged=False`` → passes). The verdict is single-sourced on
    :func:`art_director_writer.parse_brief` — the exact payload the ``omnia:brief``
    event ships — so the rubric tracks what the live render actually receives.
    """
    if ctx is None:
        return RenderReport(judged=False)

    brief = (ctx.brief or "").strip()
    if not brief:
        # A non-generation turn (no brief) — nothing for the live render to narrate.
        return RenderReport(judged=False)

    # Single-source the narratable payload on the very boundary the client reads.
    payload = art_director_writer.parse_brief(brief) or {}
    palette = payload.get("palette") or {}
    fonts = payload.get("fonts") or {}
    motion = (payload.get("motion") or "").strip()
    sections = payload.get("sections") or []

    findings: list[RenderFinding] = []
    detail: dict[str, Any] = {
        "palette_count": len(palette),
        "has_fonts": bool(fonts),
        "has_motion": bool(motion),
        "section_count": len(sections),
    }

    barren = not palette and not fonts and not motion and not sections

    # ── (1) silent render — the brief surfaced NO narratable signal (V3.10) ─────
    if barren:
        findings.append(
            RenderFinding(
                SILENT_RENDER,
                "a brief was produced but parse_brief surfaced no palette, fonts, "
                "motion or sections — the omnia:brief event carries an empty husk, "
                "so the live render shows no swatches and the narration overlay has "
                "nothing to say (pillar-3 «AI рисует» degraded to a flat, mute render)",
            )
        )
    else:
        # ── (2) swatchless — the colour story never reaches the chat (V3.4) ─────
        if not palette:
            findings.append(
                RenderFinding(
                    SWATCHLESS_RENDER,
                    "the brief carries narration signal but no palette HEX survived "
                    "extraction — the art-director's colour story never reaches the "
                    "chat, so the brief-swatches (V3.4) render blank",
                )
            )
        # ── (3) motionless — the hypnosis-layer signature dropped (V3.10) ───────
        if not motion:
            findings.append(
                RenderFinding(
                    MOTIONLESS_RENDER,
                    "the brief carries narration signal but no MOTION-СИГНАТУРА line "
                    "survived extraction — the one living-layer the narration names "
                    "while it paints is gone, so pillar-3's hypnosis layer is mute",
                )
            )

    return RenderReport(judged=True, findings=tuple(findings), detail=detail)


__all__ = [
    "MOTIONLESS_RENDER",
    "RENDER_CLASSES",
    "SILENT_RENDER",
    "SWATCHLESS_RENDER",
    "RenderContext",
    "RenderFinding",
    "RenderReport",
    "scan",
]
