"""Compose gate — the money-free composition floor of the acceptance gauntlet.

V3.3. A deterministic, browser-free source-scan that runs BEFORE any render or
vision pass and hard-blocks a freeform page that is catastrophically flat — one
uniform type size, no section rhythm, no hero. Such a page can never read as
"awwwards / enterprise with one generation" (pillar 1), so there is no point
paying for a render or a vision verdict on it: the floor catches it for free.

It is the cheap SOURCE-side sibling of the rendered taste/hierarchy legs, not a
replacement (R-04): the rich, pixel-level rubric lives in those legs and reads
the live DOM; here we only assert a catastrophe floor on the model's authored
HTML. Scope is therefore the freeform page — a standalone ``index.html``. An
entity/fullstack stack composes in the rendered DOM (a React tree, not a single
authored document), so this scan finds no ``index.html`` and is INERT (judged
nothing → passes); those stacks are judged by the rendered legs.

The three floors are CATASTROPHE-ONLY by design, so a real enterprise generation
can never trip them — the gate adds teeth without a false-positive on the live
hot path (CLAUDE.md: never force a needless regenerate). It is pure, idempotent,
and fail-soft: a pathological file is skipped, never raised (R-10).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Stable class ids — the vocabulary of the compose floor.
FLAT_TYPE = "flat-type"
TOO_FEW_SECTIONS = "too-few-sections"
NO_HERO = "no-hero"

COMPOSE_CLASSES: tuple[str, ...] = (FLAT_TYPE, TOO_FEW_SECTIONS, NO_HERO)

#: Catastrophe floors. A real generation clears these by a wide margin.
MIN_FONT_SIZES = 2
MIN_SECTIONS = 3

# ── detection patterns ────────────────────────────────────────────────────────

#: A heading tag. Each distinct level (h1..h6) is one type-scale step.
_HEADING = re.compile(r"<h([1-6])\b", re.IGNORECASE)
#: An explicit CSS size — ``font-size: 1.25rem`` in a <style> block or inline.
_FONT_SIZE = re.compile(
    r"font-size\s*:\s*([0-9.]+(?:px|rem|em|pt|%|vw|vh))", re.IGNORECASE
)
#: A Tailwind/utility text-size class (``text-2xl``) or arbitrary value
#: (``text-[28px]``) — harmless on hand-written HTML, a real signal when present.
_TEXT_SIZE_CLASS = re.compile(
    r"\btext-(xs|sm|base|lg|xl|[2-9]xl|\[[^\]]+\])", re.IGNORECASE
)
#: Semantic section landmarks — the structural rhythm of a composed page.
_LANDMARK = re.compile(
    r"<(section|header|footer|main|nav|article|aside)\b", re.IGNORECASE
)
#: Lower section headings (h2/h3) — each typically opens a section.
_SUBHEADING = re.compile(r"<h[23]\b", re.IGNORECASE)
#: A class/id whose value names a section role — the non-semantic way a page is
#: still clearly sectioned (``<div class="pricing">``).
_SECTION_WORD = (
    "hero",
    "feature",
    "pricing",
    "testimonial",
    "faq",
    "cta",
    "gallery",
    "contact",
    "about",
    "services",
    "benefits",
    "footer",
    "header",
    "banner",
    "hero-section",
)
_SECTION_MARKER = re.compile(
    r"(?:class|id)\s*=\s*[\"'][^\"']*\b(?:" + "|".join(_SECTION_WORD) + r")\b",
    re.IGNORECASE,
)
#: A hero: a dominant first heading, a banner landmark, or a hero-named block.
_HERO = re.compile(
    r"<h1\b|<header\b|(?:class|id)\s*=\s*[\"'][^\"']*\bhero\b", re.IGNORECASE
)

#: The page surface this floor judges — the model's authored, standalone HTML.
_PAGE_BASENAMES = ("index.html", "index.htm")


@dataclass(frozen=True)
class ComposeReport:
    """Verdict of one compose scan."""

    #: True iff a standalone HTML page was found and graded. When False the scan
    #: is INERT (no freeform surface to judge) and ``passed`` is True.
    judged: bool
    #: Distinct type-scale steps detected (heading levels + CSS/utility sizes).
    font_sizes: int
    #: Section-rhythm signal (landmarks + section-named blocks + sub-headings).
    sections: int
    #: Whether a hero (h1 / banner landmark / hero-named block) is present.
    hero: bool

    @property
    def passed(self) -> bool:
        if not self.judged:
            return True
        return (
            self.font_sizes >= MIN_FONT_SIZES
            and self.sections >= MIN_SECTIONS
            and self.hero
        )

    @property
    def classes(self) -> tuple[str, ...]:
        """Floors that fired, in registry order (empty when inert or clean)."""
        if not self.judged or self.passed:
            return ()
        hit: list[str] = []
        if self.font_sizes < MIN_FONT_SIZES:
            hit.append(FLAT_TYPE)
        if self.sections < MIN_SECTIONS:
            hit.append(TOO_FEW_SECTIONS)
        if not self.hero:
            hit.append(NO_HERO)
        return tuple(c for c in COMPOSE_CLASSES if c in hit)

    def summary(self) -> str:
        if not self.judged:
            return "compose: inert (no standalone HTML page to judge)"
        if self.passed:
            return (
                f"compose: clean ({self.font_sizes} type sizes, "
                f"{self.sections} sections, hero={self.hero})"
            )
        return (
            f"compose: {len(self.classes)} floor(s) below the line "
            f"[{', '.join(self.classes)}] "
            f"({self.font_sizes} type sizes, {self.sections} sections, "
            f"hero={self.hero})"
        )

    def subscore(self) -> dict[str, Any]:
        return {
            "gate": "compose",
            "passed": self.passed,
            "judged": self.judged,
            "classes": list(self.classes),
            "font_sizes": self.font_sizes,
            "sections": self.sections,
            "hero": self.hero,
        }


def _find_page(files: dict[str, str]) -> str | None:
    """Return the body of the standalone HTML page, if the set carries one."""
    for path, body in files.items():
        base = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if base in _PAGE_BASENAMES and isinstance(body, str):
            return body
    return None


def _count_font_sizes(body: str) -> int:
    steps: set[str] = set()
    steps.update("h" + m.group(1) for m in _HEADING.finditer(body))
    steps.update(m.group(1).lower() for m in _FONT_SIZE.finditer(body))
    steps.update(m.group(0).lower() for m in _TEXT_SIZE_CLASS.finditer(body))
    return len(steps)


def _count_sections(body: str) -> int:
    # Sum three independent signals; overlap only INFLATES the count (so a
    # genuinely composed page is never under-counted into a false failure).
    return (
        len(_LANDMARK.findall(body))
        + len(_SECTION_MARKER.findall(body))
        + len(_SUBHEADING.findall(body))
    )


def scan(files: dict[str, str]) -> ComposeReport:
    """Grade the freeform page's composition floor; INERT if there is none.

    Pure and fail-soft (R-10): if no standalone HTML page is present, or the body
    is not analysable, the report is INERT (``judged=False`` → passes).
    """
    body = _find_page(files)
    if not body:
        return ComposeReport(judged=False, font_sizes=0, sections=0, hero=False)
    try:
        font_sizes = _count_font_sizes(body)
        sections = _count_sections(body)
        hero = _HERO.search(body) is not None
    except Exception:  # pragma: no cover — defensive; a regex never raises here
        return ComposeReport(judged=False, font_sizes=0, sections=0, hero=False)
    return ComposeReport(
        judged=True, font_sizes=font_sizes, sections=sections, hero=hero
    )


# ── CLI: scan a provisioned app directory (mirror of defect_registry) ─────────


def _main(argv: list[str]) -> int:  # pragma: no cover — thin CLI wrapper
    from omnia_api.services.defect_registry import _read_tree

    root = argv[1] if len(argv) > 1 else "."
    report = scan(_read_tree(root))
    print(report.summary())
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv))


__all__ = [
    "COMPOSE_CLASSES",
    "FLAT_TYPE",
    "MIN_FONT_SIZES",
    "MIN_SECTIONS",
    "NO_HERO",
    "TOO_FEW_SECTIONS",
    "ComposeReport",
    "scan",
]
