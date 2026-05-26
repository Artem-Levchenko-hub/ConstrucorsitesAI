"""Loaders for the vendored `ui-ux-pro-max` skill in `apps/api/skills/`.

R-01 (deep module): callers see a small set of helpers — `lookup_palette`,
`lookup_font_pairing`, `random_ux_guidelines`, `lookup_landing_pattern`,
`lookup_style_preset`, `lookup_icon_family`, `lookup_chart_types`,
`format_design_brief`. CSV parsing, in-memory caching, and the path
layout to `skills/ui-ux-pro-max/` are all private.

R-04 (different code at different layers): this module reads CSVs as the
source of truth. The data lives at runtime under `apps/api/skills/`; on dev
that's `apps/api/skills/`, on prod the container WORKDIR mirrors that path
because the Dockerfile copies the whole `apps/api` tree.

Why not import the upstream `scripts/` helpers directly: they pull
matplotlib / pandas / sklearn for the larger search experience the skill
runs in its own home. We only need cheap dict-of-lists lookups at request
time, so a 60-line CSV reader keeps the dependency surface flat.

See `apps/api/skills/README.md` for the snapshot date, license note, and
integration roadmap.
"""

from __future__ import annotations

import csv
import random
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

_REPO_ROOT = Path(__file__).resolve().parents[3]  # apps/api/
_SKILL_DATA = _REPO_ROOT / "skills" / "ui-ux-pro-max" / "data"


class Palette(TypedDict):
    """One row from `colors.csv` — WCAG-checked palette for a product type."""

    product_type: str
    primary: str
    on_primary: str
    secondary: str
    on_secondary: str
    accent: str
    on_accent: str
    background: str
    foreground: str
    card: str
    card_foreground: str
    muted: str
    muted_foreground: str
    border: str
    destructive: str
    on_destructive: str
    ring: str
    notes: str


class FontPairing(TypedDict):
    """One row from `typography.csv` — heading+body Google-Fonts pair."""

    name: str
    category: str
    heading: str
    body: str
    keywords: str
    best_for: str
    google_fonts_url: str
    css_import: str
    tailwind_config: str
    notes: str


class UxGuideline(TypedDict):
    """One row from `ux-guidelines.csv` — a do/don't rule with severity."""

    category: str
    issue: str
    platform: str
    description: str
    do: str
    dont: str
    severity: str


class LandingPattern(TypedDict):
    """One row from `landing.csv` — full structural pattern for a landing page.

    35 curated section-order templates (Hero+Features+CTA, Long-form Sales,
    Pricing-First, Story-driven, …) with CTA placement guidance, color
    strategy, and conversion-optimization tips. Picking the right pattern
    for the industry skips the model's tendency to default to a generic
    "hero + 3 features + pricing tiers + FAQ" stack.
    """

    name: str
    keywords: str
    section_order: str
    cta_placement: str
    color_strategy: str
    effects: str
    conversion: str


class StylePreset(TypedDict):
    """One row from `styles.csv` — full visual style with implementation hints.

    85 styles vs. our hand-rolled 10 in `_STYLE_KIT`. Adds: era/origin,
    complexity, framework compatibility, do-not-use-for warnings.
    """

    category: str
    type: str
    keywords: str
    primary_colors: str
    secondary_colors: str
    effects: str
    best_for: str
    avoid_for: str
    ai_prompt_keywords: str


class IconRow(TypedDict):
    """One row from `icons.csv` — one icon with import code and library."""

    category: str
    name: str
    keywords: str
    library: str
    import_code: str
    usage: str
    style: str


class ChartType(TypedDict):
    """One row from `charts.csv` — chart type with when-to-use guidance.

    Only injected when the prompt has data-viz signals (dashboard, аналитика,
    графики, метрики). Without it the model defaults to bar charts for
    everything.
    """

    data_type: str
    keywords: str
    best_chart: str
    secondary: str
    when_to_use: str
    when_not: str
    color_guidance: str
    library: str


@lru_cache(maxsize=1)
def _palettes() -> tuple[Palette, ...]:
    path = _SKILL_DATA / "colors.csv"
    rows: list[Palette] = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(
                Palette(
                    product_type=r["Product Type"],
                    primary=r["Primary"],
                    on_primary=r["On Primary"],
                    secondary=r["Secondary"],
                    on_secondary=r["On Secondary"],
                    accent=r["Accent"],
                    on_accent=r["On Accent"],
                    background=r["Background"],
                    foreground=r["Foreground"],
                    card=r["Card"],
                    card_foreground=r["Card Foreground"],
                    muted=r["Muted"],
                    muted_foreground=r["Muted Foreground"],
                    border=r["Border"],
                    destructive=r["Destructive"],
                    on_destructive=r["On Destructive"],
                    ring=r["Ring"],
                    notes=r.get("Notes", ""),
                )
            )
    return tuple(rows)


@lru_cache(maxsize=1)
def _font_pairings() -> tuple[FontPairing, ...]:
    path = _SKILL_DATA / "typography.csv"
    rows: list[FontPairing] = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(
                FontPairing(
                    name=r["Font Pairing Name"],
                    category=r["Category"],
                    heading=r["Heading Font"],
                    body=r["Body Font"],
                    keywords=r["Mood/Style Keywords"],
                    best_for=r["Best For"],
                    google_fonts_url=r["Google Fonts URL"],
                    css_import=r["CSS Import"],
                    tailwind_config=r["Tailwind Config"],
                    notes=r.get("Notes", ""),
                )
            )
    return tuple(rows)


@lru_cache(maxsize=1)
def _landing_patterns() -> tuple[LandingPattern, ...]:
    path = _SKILL_DATA / "landing.csv"
    rows: list[LandingPattern] = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(
                LandingPattern(
                    name=r["Pattern Name"],
                    keywords=r["Keywords"],
                    section_order=r["Section Order"],
                    cta_placement=r["Primary CTA Placement"],
                    color_strategy=r["Color Strategy"],
                    effects=r["Recommended Effects"],
                    conversion=r["Conversion Optimization"],
                )
            )
    return tuple(rows)


@lru_cache(maxsize=1)
def _style_presets() -> tuple[StylePreset, ...]:
    path = _SKILL_DATA / "styles.csv"
    rows: list[StylePreset] = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(
                StylePreset(
                    category=r["Style Category"],
                    type=r["Type"],
                    keywords=r["Keywords"],
                    primary_colors=r["Primary Colors"],
                    secondary_colors=r["Secondary Colors"],
                    effects=r["Effects & Animation"],
                    best_for=r["Best For"],
                    avoid_for=r["Do Not Use For"],
                    ai_prompt_keywords=r["AI Prompt Keywords"],
                )
            )
    return tuple(rows)


@lru_cache(maxsize=1)
def _icons() -> tuple[IconRow, ...]:
    path = _SKILL_DATA / "icons.csv"
    rows: list[IconRow] = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(
                IconRow(
                    category=r["Category"],
                    name=r["Icon Name"],
                    keywords=r["Keywords"],
                    library=r["Library"],
                    import_code=r["Import Code"],
                    usage=r["Usage"],
                    style=r["Style"],
                )
            )
    return tuple(rows)


@lru_cache(maxsize=1)
def _chart_types() -> tuple[ChartType, ...]:
    path = _SKILL_DATA / "charts.csv"
    rows: list[ChartType] = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(
                ChartType(
                    data_type=r["Data Type"],
                    keywords=r["Keywords"],
                    best_chart=r["Best Chart Type"],
                    secondary=r["Secondary Options"],
                    when_to_use=r["When to Use"],
                    when_not=r["When NOT to Use"],
                    color_guidance=r["Color Guidance"],
                    library=r["Library Recommendation"],
                )
            )
    return tuple(rows)


@lru_cache(maxsize=1)
def _ux_guidelines() -> tuple[UxGuideline, ...]:
    path = _SKILL_DATA / "ux-guidelines.csv"
    rows: list[UxGuideline] = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(
                UxGuideline(
                    category=r["Category"],
                    issue=r["Issue"],
                    platform=r["Platform"],
                    description=r["Description"],
                    do=r["Do"],
                    dont=r["Don't"],
                    severity=r["Severity"],
                )
            )
    return tuple(rows)


def _score_match(row_text: str, keywords: Sequence[str]) -> int:
    """Tiny case-insensitive substring scorer. Avoids pulling in scikit /
    fuzzywuzzy for what is, in practice, "does this product-type string
    contain any of the user's keywords"."""
    rt = row_text.lower()
    return sum(1 for kw in keywords if kw.lower() in rt)


def lookup_palette(*keywords: str) -> Palette | None:
    """Best-effort palette pick by keyword overlap against `product_type`.

    Returns None when nothing scores above zero — the caller should fall
    back to the prompt's built-in `_DESIGN_KIT` rather than guess.
    """
    if not keywords:
        return None
    scored = [
        (_score_match(p["product_type"], keywords), p)
        for p in _palettes()
    ]
    best_score, best_row = max(scored, key=lambda t: t[0])
    return best_row if best_score > 0 else None


def lookup_font_pairing(*keywords: str) -> FontPairing | None:
    """Best-effort font-pair pick scored against keywords + best_for + name."""
    if not keywords:
        return None
    scored = [
        (
            _score_match(
                f"{fp['name']} {fp['keywords']} {fp['best_for']}", keywords
            ),
            fp,
        )
        for fp in _font_pairings()
    ]
    best_score, best_row = max(scored, key=lambda t: t[0])
    return best_row if best_score > 0 else None


def lookup_landing_pattern(
    *keywords: str, seed: int | None = None
) -> LandingPattern | None:
    """Pick a landing structural pattern.

    landing.csv is indexed by STRUCTURE (hero-centric, long-form sales,
    pricing-first) not industry, so keyword scoring on industry tokens
    almost never hits. Strategy: try keyword match first; if nothing
    scores, fall back to a seed-determinist pick from the 35 patterns
    so each project gets variety instead of every site collapsing into
    the same hero+features+pricing+FAQ stack.

    Returns None only when seed is None AND no keyword hit — that's the
    callsite saying "no project context at all". With a seed, always
    returns one row.
    """
    pool = _landing_patterns()
    if not pool:
        return None
    if keywords:
        scored = [
            (_score_match(f"{lp['name']} {lp['keywords']}", keywords), lp)
            for lp in pool
        ]
        best_score, best_row = max(scored, key=lambda t: t[0])
        if best_score > 0:
            return best_row
    if seed is not None:
        return pool[seed % len(pool)]
    return None


def lookup_style_preset(*keywords: str) -> StylePreset | None:
    """Best-effort style preset pick scored against type + keywords + best_for.

    Returns None when nothing matches — caller falls back to `_STYLE_KIT`'s
    hand-rolled 10 presets in `prompt_builder.py`.
    """
    if not keywords:
        return None
    scored = [
        (
            _score_match(
                f"{sp['type']} {sp['keywords']} {sp['best_for']}", keywords
            ),
            sp,
        )
        for sp in _style_presets()
    ]
    best_score, best_row = max(scored, key=lambda t: t[0])
    return best_row if best_score > 0 else None


def lookup_icon_family(*keywords: str) -> IconRow | None:
    """Pick a representative icon scored against name + keywords + category.

    Returns one row; caller surfaces the LIBRARY name + import pattern to
    the model so it doesn't default to inline SVG or emoji.
    """
    if not keywords:
        return None
    scored = [
        (
            _score_match(
                f"{ic['name']} {ic['keywords']} {ic['category']}", keywords
            ),
            ic,
        )
        for ic in _icons()
    ]
    best_score, best_row = max(scored, key=lambda t: t[0])
    return best_row if best_score > 0 else None


def lookup_chart_types(*keywords: str, limit: int = 2) -> tuple[ChartType, ...]:
    """Return up to `limit` chart types scored against keywords + data_type.

    Empty tuple when no data-viz signal — most landings don't need charts.
    Caller should only invoke this when the prompt mentions dashboard,
    аналитика, графики, метрики, statistics, etc.
    """
    if not keywords:
        return ()
    scored = [
        (
            _score_match(
                f"{ct['data_type']} {ct['keywords']}", keywords
            ),
            ct,
        )
        for ct in _chart_types()
    ]
    scored.sort(key=lambda t: t[0], reverse=True)
    return tuple(ct for score, ct in scored[:limit] if score > 0)


def random_ux_guidelines(
    *, severity: str | None = "High", limit: int = 5, seed: int | None = None
) -> tuple[UxGuideline, ...]:
    """Sample `limit` UX guidelines at the requested severity.

    Used to surface a handful of must-follow rules into the system prompt
    without flooding it with all 99. `seed` lets the caller pin selections
    per request (e.g., hash on project_id) so re-prompts stay stable.
    """
    pool = _ux_guidelines()
    if severity is not None:
        pool = tuple(g for g in pool if g["severity"].lower() == severity.lower())
    if not pool:
        return ()
    rng = random.Random(seed) if seed is not None else random
    n = min(limit, len(pool))
    return tuple(rng.sample(list(pool), n))


def format_design_brief(
    *,
    palette: Palette | None = None,
    font_pairing: FontPairing | None = None,
    guidelines: Sequence[UxGuideline] = (),
    landing_pattern: LandingPattern | None = None,
    style_preset: StylePreset | None = None,
    icon_family: IconRow | None = None,
    chart_types: Sequence[ChartType] = (),
) -> str:
    """Render a compact, system-prompt-friendly block from any subset of inputs.

    Returns an empty string when every input is empty/None — safe to splice
    into a prompt assembler unconditionally.

    The format is deliberately terse: line-prefixed key:value, no markdown
    tables, no decorative wrappers. The model parses it reliably while the
    block stays well under ~3 KB even with every section populated.
    """
    parts: list[str] = []

    if palette is not None:
        parts.append(
            "PALETTE (WCAG-safe — use these tokens, not free-form hex):\n"
            f"  product_type: {palette['product_type']}\n"
            f"  primary: {palette['primary']}  on_primary: {palette['on_primary']}\n"
            f"  accent:  {palette['accent']}  on_accent:  {palette['on_accent']}\n"
            f"  bg:      {palette['background']}  fg:        {palette['foreground']}\n"
            f"  muted:   {palette['muted']}  border:    {palette['border']}\n"
            f"  destructive: {palette['destructive']}  ring: {palette['ring']}"
        )

    if font_pairing is not None:
        parts.append(
            "FONTS (Google Fonts):\n"
            f"  pair: {font_pairing['name']} ({font_pairing['category']})\n"
            f"  heading: {font_pairing['heading']}\n"
            f"  body:    {font_pairing['body']}\n"
            f"  css:     {font_pairing['css_import']}"
        )

    if landing_pattern is not None:
        parts.append(
            "LANDING PATTERN (curated structure for this vertical — follow this section order):\n"
            f"  pattern: {landing_pattern['name']}\n"
            f"  sections: {landing_pattern['section_order']}\n"
            f"  CTA placement: {landing_pattern['cta_placement']}\n"
            f"  color strategy: {landing_pattern['color_strategy']}\n"
            f"  effects: {landing_pattern['effects']}\n"
            f"  conversion tips: {landing_pattern['conversion']}"
        )

    if style_preset is not None:
        parts.append(
            "VISUAL STYLE (matched preset — apply effects, primary colors, mood):\n"
            f"  style: {style_preset['type']} ({style_preset['category']})\n"
            f"  primary colors: {style_preset['primary_colors']}\n"
            f"  secondary colors: {style_preset['secondary_colors']}\n"
            f"  effects: {style_preset['effects']}\n"
            f"  best for: {style_preset['best_for']}\n"
            f"  AVOID using when: {style_preset['avoid_for']}\n"
            f"  prompt-keyword anchors: {style_preset['ai_prompt_keywords']}"
        )

    if icon_family is not None:
        parts.append(
            "ICONS (use this library; inline SVG with the import pattern shown):\n"
            f"  library: {icon_family['library']}  ·  style: {icon_family['style']}\n"
            f"  example import: {icon_family['import_code']}\n"
            f"  rule: НИКОГДА emoji в UI; всегда {icon_family['library']}-style stroke icons."
        )

    if chart_types:
        parts.append(
            "CHARTS (only when the site has data viz — pick from these):\n"
            + "\n".join(
                f"  • {ct['data_type']} → {ct['best_chart']} "
                f"(library: {ct['library']}; use when: {ct['when_to_use'][:90]}…)"
                for ct in chart_types
            )
        )

    if guidelines:
        parts.append(
            "UX RULES (severity High — non-negotiable):\n"
            + "\n".join(
                f"  • [{g['category']}/{g['issue']}] {g['do']} — NOT: {g['dont']}"
                for g in guidelines
            )
        )

    return "\n\n".join(parts)
