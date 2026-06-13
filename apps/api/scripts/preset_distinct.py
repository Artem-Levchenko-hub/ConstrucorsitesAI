#!/usr/bin/env python3
"""V1.6 8/5 — PRESET-DISTINCTNESS ASSERTION: the ratchet that stops 29 design
presets from silently collapsing into one template.

Pillar 1 ("WOW design from one generation") rests on the design *vocabulary*
being plural: a clinic, a crypto launch and a kids' studio must not render as the
same app with the brand colour swapped. Today nothing measures that. Worse, 18 of
29 presets share ``hero_type='mixed'`` — so "they're different" is trivially true
yet says nothing; two equally-generic templates also differ. The bar has to be
*distinctness-as-quality*: every pair must be separated on a multi-dimensional
fingerprint by at least :data:`DISTINCT_FLOOR`, where the dimensions are exactly
the fields that drive the render (painted accent, surface, focal hero, section
markers, type families, layout rhythm). A 30th preset that clones an existing one
— same hero + signature + hue + surface + font, differing only in name/keywords —
then fails *here, in CI, money-free*, instead of shipping as a duplicate.

Two halves, split so the valuable half is deterministic and money-free (the same
discipline as the 6/5 batch-runner):

  * DECLARED distinctness (this module's teeth): read the static ``PRESETS``
    table, build a fingerprint per preset from the render-driving fields, and
    assert every one of the 406 pairs is ≥ ``DISTINCT_FLOOR`` apart. No LLM, no
    browser — a necessary condition (if the *vocabulary* can't separate two
    presets, no generator can render them apart) that runs on every change.

  * RENDERED distinctness (an injected seam): proving the *generator* honours the
    vocabulary — same brief under 3 pinned presets → 3 measurably-different DOMs,
    each itself passing the 9/5 hierarchy floor — needs paid generation (the
    owner-authorized step). So :func:`score_rendered` takes already-running app
    URLs and reuses ``taste_gate`` + ``hierarchy_gate`` (R-04, no new extractor);
    it abstains money-free when no URLs are handed in.

Calibration (anti-over-strict, the 44px-trap lesson): the floor is 1.0 against a
real-corpus minimum of ~1.97 (kids-playful ↔ pet-care) — a ~2× margin. A pair
that shares hero+signature+hue+surface+font and differs only in a tint and some
layout copy scores ~0.7 < 1.0 and is correctly flagged as homogenised; a pair
that differs by a whole font family or a hue shift clears the bar.

Canon: R-01 (``evaluate`` hides fingerprinting + the full pairwise sweep behind
one call returning a rich verdict), R-04 (the colour science is imported from
``wow_dom_gate``; the rendered seam reuses the taste/hierarchy audits — nothing
duplicated), R-10 (a render miss in the seam degrades that app to a fail, never
crashes the sweep).
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Make the src-layout package importable when run as `python scripts/preset_distinct.py`.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from omnia_api.services.design_presets import PRESETS, DesignPreset  # noqa: E402
from omnia_api.services.wow_dom_gate import (  # noqa: E402
    Rgb,
    _hue_distance,
    relative_luminance,
    rgb_to_hsl,
)

# ── tunables ──────────────────────────────────────────────────────────────────

#: Below this accent saturation the hue is meaningless (black/white/grey accents
#: like editorial-trust's #0A0A0A); such an accent contributes via lightness and
#: the achromatic-vs-chromatic flag, never a phantom hue.
SAT_MIN = 0.15

#: Minimum acceptable pairwise design separation. 1.0 = at least one full
#: categorical axis (a different font family, hero type or section signature) or
#: a strong hue shift beyond minor tinting. Calibrated below the real-corpus
#: minimum (~1.97) for a ~2× margin while still failing a homogenised clone.
DISTINCT_FLOOR = 1.0

# Per-dimension weights. Hue, surface, hero, signature, display-font and layout
# rhythm each carry a full unit (a strong identity axis); saturation, lightness
# and body-font are secondary tints worth half. Max distance ≈ 7.5.
W_HUE = 1.0
W_BG = 1.0
W_SAT = 0.5
W_LIGHT = 0.5
W_HERO = 1.0
W_SIG = 1.0
W_FONT_DISPLAY = 1.0
W_FONT_BODY = 0.5
W_LAYOUT = 1.0

# Layout-signature tokeniser: keep meaningful word stems, drop short glue words
# (RU + EN) so the Jaccard reflects layout vocabulary, not prepositions.
_LAYOUT_STOP = frozenset(
    {
        "над",
        "под",
        "без",
        "для",
        "рядом",
        "каждая",
        "каждый",
        "каждое",
        "with",
        "without",
        "over",
        "each",
        "the",
        "and",
        "into",
    }
)
_WORD_RE = re.compile(r"[^0-9a-zа-яё]+", re.IGNORECASE)


# ── the fingerprint (the testable core) ───────────────────────────────────────


def _hex_to_rgb(hex_str: str) -> Rgb:
    """``#rrggbb`` → an sRGB triple. Presets carry clean 6-digit hex."""
    h = hex_str.strip().lstrip("#")
    return (float(int(h[0:2], 16)), float(int(h[2:4], 16)), float(int(h[4:6], 16)))


def _layout_tokens(preset: DesignPreset) -> frozenset[str]:
    """Coarse vocabulary of a preset's layout signatures (lower-cased word stems
    of length ≥ 4, glue words dropped) — the basis for a Jaccard layout distance."""
    blob = " ".join(preset.layout_signatures).lower()
    return frozenset(
        w for w in _WORD_RE.split(blob) if len(w) >= 4 and w not in _LAYOUT_STOP
    )


@dataclass(frozen=True)
class PresetFingerprint:
    """The render-driving signature of a preset, reduced to comparable numbers
    plus a few categoricals. Built ONLY from fields a generator paints — name,
    keywords, industries and prose are deliberately excluded, so two presets that
    differ only in metadata collide here (and are rejected)."""

    preset_id: str
    accent_hue: float  # 0..360 (0 and meaningless when achromatic — see flag)
    accent_sat: float  # 0..1
    accent_light: float  # 0..1
    accent_chromatic: bool  # accent_sat >= SAT_MIN
    bg_lum: float  # 0..1 WCAG relative luminance of the page surface
    hero_type: str
    section_signature: str
    font_display: str
    font_body: str
    layout_tokens: frozenset[str]


def fingerprint(preset: DesignPreset) -> PresetFingerprint:
    """Reduce a preset to its render-driving fingerprint (R-04: hue/sat/light and
    luminance come from ``wow_dom_gate``'s colour science, not a local copy)."""
    accent = preset.palette.get("accent") or preset.palette.get("fg", "#000000")
    hue, sat, light = rgb_to_hsl(_hex_to_rgb(accent))
    bg = preset.palette.get("bg", "#FFFFFF")
    return PresetFingerprint(
        preset_id=preset.id,
        accent_hue=hue,
        accent_sat=sat,
        accent_light=light,
        accent_chromatic=sat >= SAT_MIN,
        bg_lum=relative_luminance(_hex_to_rgb(bg)),
        hero_type=preset.hero_type,
        section_signature=preset.section_signature,
        font_display=preset.fonts.get("display", "").strip().lower(),
        font_body=preset.fonts.get("body", "").strip().lower(),
        layout_tokens=_layout_tokens(preset),
    )


def _hue_term(a: PresetFingerprint, b: PresetFingerprint) -> float:
    """Normalised hue separation, achromatic-aware. Two saturated accents: their
    circular wheel distance (R-04 ``_hue_distance``). One saturated, one neutral:
    a full unit (a colour identity vs a monochrome one is a strong difference).
    Both neutral: zero — they're separated by lightness/other dims instead."""
    if a.accent_chromatic and b.accent_chromatic:
        return _hue_distance(a.accent_hue, b.accent_hue) / 180.0
    if a.accent_chromatic != b.accent_chromatic:
        return 1.0
    return 0.0


def _layout_term(a: PresetFingerprint, b: PresetFingerprint) -> float:
    """Jaccard distance of the two layout vocabularies (0 = identical sets)."""
    union = a.layout_tokens | b.layout_tokens
    if not union:
        return 0.0
    return 1.0 - len(a.layout_tokens & b.layout_tokens) / len(union)


def distance(a: PresetFingerprint, b: PresetFingerprint) -> float:
    """Weighted multi-axis distance between two preset fingerprints. A metric:
    ``distance(x, x) == 0`` and symmetric. Larger = more visually distinct."""
    return (
        W_HUE * _hue_term(a, b)
        + W_BG * abs(a.bg_lum - b.bg_lum)
        + W_SAT * abs(a.accent_sat - b.accent_sat)
        + W_LIGHT * abs(a.accent_light - b.accent_light)
        + W_HERO * (0.0 if a.hero_type == b.hero_type else 1.0)
        + W_SIG * (0.0 if a.section_signature == b.section_signature else 1.0)
        + W_FONT_DISPLAY * (0.0 if a.font_display == b.font_display else 1.0)
        + W_FONT_BODY * (0.0 if a.font_body == b.font_body else 1.0)
        + W_LAYOUT * _layout_term(a, b)
    )


# ── the verdict ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DistinctReport:
    """Outcome of the pairwise distinctness sweep over a preset set."""

    floor: float
    count: int
    min_distance: float
    min_pair: tuple[str, str]
    # every pair below the floor: (id_a, id_b, distance), sorted closest-first.
    violations: tuple[tuple[str, str, float], ...]

    @property
    def passed(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "floor": self.floor,
            "count": self.count,
            "min_distance": round(self.min_distance, 4),
            "min_pair": list(self.min_pair),
            "violations": [
                {"a": a, "b": b, "distance": round(d, 4)}
                for a, b, d in self.violations
            ],
        }

    def summary(self) -> str:
        head = (
            f"preset-distinct: {self.count} presets, "
            f"{'PASS' if self.passed else 'FAIL'} "
            f"(floor {self.floor}, min {self.min_distance:.3f} "
            f"between {self.min_pair[0]} ↔ {self.min_pair[1]})"
        )
        if self.passed:
            return head
        lines = [head, f"  {len(self.violations)} pair(s) below floor:"]
        lines += [f"    {d:.3f}  {a} ↔ {b}" for a, b, d in self.violations]
        return "\n".join(lines)


def evaluate(
    presets: Mapping[str, DesignPreset], *, floor: float = DISTINCT_FLOOR
) -> DistinctReport:
    """Score every pair in ``presets`` and flag any closer than ``floor``.

    Pure and deterministic — the testable core (no browser, no LLM). ``passed``
    iff no pair collides; ``min_pair`` records the tightest separation so a
    regression (a near-clone preset) is visible even when the gate still passes.
    """
    fps = {pid: fingerprint(p) for pid, p in presets.items()}
    ids = list(fps)
    min_distance = float("inf")
    min_pair: tuple[str, str] = ("", "")
    violations: list[tuple[str, str, float]] = []
    for a, b in itertools.combinations(ids, 2):
        d = distance(fps[a], fps[b])
        if d < min_distance:
            min_distance, min_pair = d, (a, b)
        if d < floor:
            violations.append((a, b, d))
    if min_distance == float("inf"):  # 0 or 1 preset → nothing to compare
        min_distance = 0.0
    violations.sort(key=lambda v: v[2])
    return DistinctReport(
        floor=floor,
        count=len(ids),
        min_distance=min_distance,
        min_pair=min_pair,
        violations=tuple(violations),
    )


def evaluate_default(*, floor: float = DISTINCT_FLOOR) -> DistinctReport:
    """Run the gate over the shipped ``PRESETS`` table."""
    return evaluate(PRESETS, floor=floor)


# ── rendered seam (injected, money-free by default) ───────────────────────────


@dataclass(frozen=True)
class RenderedDistinct:
    """Per-app rendered-quality result for the paid distinctness verification."""

    rendered: bool
    per_app_quality: dict[str, bool]  # preset_id → passed 9/5 hierarchy + taste
    note: str


async def score_rendered(urls: Mapping[str, str]) -> RenderedDistinct:
    """Verify the *generator* honours the vocabulary: every preset-app URL must
    itself clear the composition floor (taste 7/5 + hierarchy 9/5, R-04 reuse).

    This is the paid half (the URLs come from owner-authorized generations); it
    abstains money-free when handed nothing. It deliberately reuses the existing
    audits rather than re-extracting a DOM vector — the declared-distinctness gate
    above already proves the *vocabulary* is plural; this proves the *render*
    obeys it, per-app.
    """
    if not urls:
        return RenderedDistinct(
            rendered=False, per_app_quality={}, note="no URLs — money-free abstain"
        )
    # Imported lazily so the money-free path never pulls Playwright.
    from omnia_api.services import hierarchy_gate, taste_gate

    quality: dict[str, bool] = {}
    for pid, url in urls.items():
        try:
            taste = await taste_gate.audit_url(url)
            hier = await hierarchy_gate.audit_url(url)
            quality[pid] = bool(taste.passed and hier.passed)
        except Exception:  # R-10: a render miss fails that app, never crashes
            quality[pid] = False
    return RenderedDistinct(
        rendered=True,
        per_app_quality=quality,
        note=f"audited {len(quality)} rendered preset app(s)",
    )


# ── CLI ───────────────────────────────────────────────────────────────────────


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assert the design presets are pairwise distinct (8/5 ratchet)."
    )
    parser.add_argument(
        "--floor",
        type=float,
        default=DISTINCT_FLOOR,
        help=f"minimum pairwise distance (default {DISTINCT_FLOOR})",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    report = evaluate_default(floor=args.floor)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.summary())
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover — thin CLI wrapper
    raise SystemExit(_main(sys.argv[1:]))
