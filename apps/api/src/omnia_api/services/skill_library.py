"""Loaders for the vendored `ui-ux-pro-max` skill in `apps/api/skills/`.

R-01 (deep module): callers see a small set of helpers — `lookup_palette`,
`lookup_font_pairing`, `random_ux_guidelines`, `lookup_filtered_ux_guidelines`,
`lookup_landing_pattern`, `lookup_style_preset`, `lookup_icon_family`,
`lookup_chart_types`, `lookup_design_patterns`, `format_design_brief`.
CSV parsing, in-memory caching, and the path layout to
`skills/ui-ux-pro-max/` are all private.

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

import colorsys
import csv
import json
import random
import re
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

_REPO_ROOT = Path(__file__).resolve().parents[3]  # apps/api/
_SKILL_DATA = _REPO_ROOT / "skills" / "ui-ux-pro-max" / "data"
# Phase D.1' + D.2' — awwwards-corpus reference + design-patterns library.
# JSON-backed (not CSV) because the corpus entries carry nested palette/
# fonts/motion_signature dicts that don't flatten cleanly into rows.
_DATA_DIR = _REPO_ROOT / "data"


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


class DesignPattern(TypedDict):
    """One style block parsed out of `design.csv`.

    The file is not strict CSV — it's a hand-written reference document
    where each style starts with `Name（中文名）` followed by a Chinese
    summary, use-cases, and an optional `<design-system>` block with
    English design-token DNA (vibe, HEX palette, font family). We compress
    each block into ~6 short fields so a top-3 selection costs <600 bytes
    in the system prompt — but gives the model concrete visual anchors
    (real HEX, real font) instead of forcing it to invent indigo+violet.
    """

    name: str            # English style name (e.g. "Bauhaus", "Cyberpunk")
    summary: str         # One-line philosophy / core concept
    vibe_tags: str       # Emotional keywords (e.g. "Tactile, Bold, Geometric")
    color_tokens: str    # Up to 6 HEX values, comma-joined
    typography: str      # Font family + optional weight hints
    use_cases: str       # Free-text alias for keyword matching (ru/zh/en)


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


_STYLE_HEADER_RE = re.compile(r'^"?([A-Z][A-Za-z0-9 /+\-]+)（([^）]+)）"?\s*$')
_HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")
_VIBE_LABELS = ("Vibe:", "Emotional Keywords:", "Visual Vibe:")
_SUMMARY_LABELS = (
    "Core Concept:",
    "Core Principle:",
    "Core Idea:",
    "Design Philosophy:",
    "The Pocket",
    "The mobile",
)
_FONT_LABELS = ("Font Family:", "Font:", "Primary Font:")


def _strip_bullet(line: str) -> str:
    """Drop common bullet prefixes used in `design.csv` (`●`, `○`, dashes)."""
    return line.strip().lstrip("●○•-·　 \t").strip()


@lru_cache(maxsize=1)
def _design_patterns() -> tuple[DesignPattern, ...]:
    """Parse `design.csv` into one `DesignPattern` per style block.

    Strategy: scan line-by-line for the `Name（中文名）` header pattern;
    each header opens a block that ends at the next header (or EOF).
    Inside the block we cherry-pick the vibe line, the first English
    summary line, the first 6 unique HEX tokens, and the font family.

    Failures are localised — if a block has no HEX or no vibe we still
    emit a record with whatever we found. The function is wrapped in
    `lru_cache` so the regex pass runs exactly once per process.
    """
    path = _SKILL_DATA / "design.csv"
    if not path.exists():
        return ()

    lines = path.read_text(encoding="utf-8").splitlines()

    headers: list[tuple[int, str, str]] = []
    for i, raw in enumerate(lines):
        m = _STYLE_HEADER_RE.match(raw)
        if m:
            headers.append((i, m.group(1).strip(), m.group(2).strip()))

    patterns: list[DesignPattern] = []
    for idx, (start, en_name, zh_name) in enumerate(headers):
        end = headers[idx + 1][0] if idx + 1 < len(headers) else len(lines)
        block_lines = lines[start:end]
        block_text = "\n".join(block_lines)

        vibe = ""
        summary = ""
        font = ""
        for raw in block_lines:
            stripped = _strip_bullet(raw)
            if not vibe and stripped.startswith(_VIBE_LABELS):
                vibe = stripped.split(":", 1)[1].strip().rstrip(".")
            if not summary and stripped.startswith(_SUMMARY_LABELS):
                if ":" in stripped:
                    summary = stripped.split(":", 1)[1].strip()
                else:
                    summary = stripped
                summary = summary[:240].rstrip()
            if not font and stripped.startswith(_FONT_LABELS):
                font = stripped.split(":", 1)[1].strip().rstrip(".")
            if vibe and summary and font:
                break

        hexes: list[str] = []
        for h in _HEX_RE.findall(block_text):
            up = h.upper()
            if up not in hexes:
                hexes.append(up)
            if len(hexes) >= 6:
                break

        patterns.append(
            DesignPattern(
                name=en_name,
                summary=summary,
                vibe_tags=vibe,
                color_tokens=", ".join(hexes),
                typography=font,
                use_cases=zh_name,
            )
        )

    return tuple(patterns)


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


def lookup_filtered_ux_guidelines(
    *keywords: str,
    severity: str | None = "High",
    limit: int = 5,
    seed: int | None = None,
) -> tuple[UxGuideline, ...]:
    """Pick UX guidelines relevant to the request rather than random.

    Scores every rule of the requested severity by keyword overlap against
    `category + issue + description`. When the prompt has no industry/UX
    signal at all (no scoring hits), falls back to `random_ux_guidelines`
    so the model still receives `limit` must-follow rules — never zero.
    When some keywords score but fewer than `limit` rules hit, pads with
    seed-deterministic random picks so the brief is always exactly `limit`
    rules wide and stable across re-prompts of the same project.
    """
    pool = _ux_guidelines()
    if severity is not None:
        pool = tuple(g for g in pool if g["severity"].lower() == severity.lower())
    if not pool:
        return ()

    if keywords:
        scored = [
            (
                _score_match(
                    f"{g['category']} {g['issue']} {g['description']}",
                    keywords,
                ),
                g,
            )
            for g in pool
        ]
        scored.sort(key=lambda t: t[0], reverse=True)
        hits = tuple(g for score, g in scored[:limit] if score > 0)
        if hits:
            if len(hits) >= limit:
                return hits
            seen = {(g["category"], g["issue"]) for g in hits}
            rest = [g for g in pool if (g["category"], g["issue"]) not in seen]
            if not rest:
                return hits
            rng = random.Random(seed) if seed is not None else random
            fillers = tuple(rng.sample(rest, min(limit - len(hits), len(rest))))
            return hits + fillers

    rng = random.Random(seed) if seed is not None else random
    n = min(limit, len(pool))
    return tuple(rng.sample(list(pool), n))


def _usability_score_for_pattern(name: str, vibe_tags: str, summary: str) -> int:
    """Map a design pattern's name/vibe/summary to a Malewicz Ch24 usability
    score (1-10).

    Ch24 surveyed task-completion data across visual styles; the resulting
    "friction tax" is roughly:
      • pure flat → 6 (22% slower task completion vs. modern w/ subtle depth)
      • flat + 1 shadow / "modern" → 8
      • brutalist / kinetic → 5 (intentional friction, low for utility tasks)
      • editorial / minimal → 9
      • neumorphism → 4 (accessibility issues, low contrast)
      • glass / glassmorphism → 7
      • skeuomorphic → 5 (dated, but legible)

    Scoring is heuristic: lowercased keyword match over name + vibe_tags +
    summary. Most specific bucket wins (neumorphism before "modern" even if
    both could match). Default fallback is 7 — middle-of-the-road usability
    when nothing distinctive matches (so cheap models still get a number,
    not a None).
    """
    text = f"{name} {vibe_tags} {summary}".lower()
    if "neumorphism" in text or "neumorphic" in text or "soft ui" in text:
        return 4
    if "brutalist" in text or "brutalism" in text or "kinetic" in text or "anti-design" in text:
        return 5
    if "skeuomorphic" in text or "skeuomorphism" in text:
        return 5
    if "editorial" in text or "minimalism" in text or "minimal" in text:
        return 9
    if "glass" in text or "glassmorphism" in text or "frosted" in text:
        return 7
    if "modern" in text or "shadow" in text or "depth" in text or "elevation" in text:
        return 8
    if "flat" in text:
        return 6
    return 7


def lookup_design_patterns(
    *keywords: str, limit: int = 3
) -> tuple[dict, ...]:
    """Return up to `limit` design styles scored by keyword overlap.

    Matches keywords against `name + vibe_tags + summary + use_cases`. The
    `use_cases` field carries the Chinese alias from `design.csv` so a
    direct hit on a Russian/English term that maps to one of those style
    families still scores. Empty tuple when no signal — caller falls back
    to the prompt's built-in `_STYLE_KIT`.

    Returns at most `limit` patterns ordered by descending match score.
    Use this to give the model a *concrete* visual anchor (real HEX,
    real font) instead of forcing it to invent indigo+violet — especially
    valuable for cheap models that otherwise default to AI-generic colors.

    Phase J extension: each returned dict carries a `usability_score`
    (1-10) reflecting Malewicz Ch24 friction data. The score lets the
    caller (or model) trade off "edgy visual" vs. "usable interface" —
    pure flat is 6 (22% slower task completion), neumorphism 4, editorial 9.
    """
    if not keywords:
        return ()
    scored = [
        (
            _score_match(
                f"{dp['name']} {dp['vibe_tags']} {dp['summary']} {dp['use_cases']}",
                keywords,
            ),
            dp,
        )
        for dp in _design_patterns()
    ]
    scored.sort(key=lambda t: t[0], reverse=True)
    out: list[dict] = []
    for score, dp in scored[:limit]:
        if score <= 0:
            continue
        # Phase J — derive usability_score and merge into a plain dict
        # (we can't extend a TypedDict in-place; return a wider mapping).
        enriched: dict = dict(dp)
        enriched["usability_score"] = _usability_score_for_pattern(
            dp["name"], dp["vibe_tags"], dp["summary"]
        )
        out.append(enriched)
    return tuple(out)


# ══════════════════════════════════════════════════════════════════════════
# Phase J — Malewicz-derived smart lookups
#
# Where Phase G ships the RULES ("gradient = same temperature, hue shift
# 15-30°, saturation -10"), Phase J ships the APPLIED VALUES ("for brand
# #92400E use #92400e → #a16207"). Cheap models follow concrete tokens
# far better than abstract math — Haiku will obey "use these two hexes"
# but routinely miscomputes hue rotation on its own.
#
# All five helpers are deterministic, side-effect-free, stdlib-only.
# Bad inputs raise ValueError (NOT silent None) — the caller in
# _compute_skill_brief wraps in try/except so a malformed hex never
# blocks the whole prompt.
# ══════════════════════════════════════════════════════════════════════════


# ──────────────────────────────────────────────────────────────────────────
# J1 — lookup_micro_copy
#
# 10 verticals × 5 contexts = ~50 (vertical, context) pairs. Label language
# picks RU when the vertical historically reads RU (food/medical/legal/
# realestate/education — Russian consumer-facing markets where Cyrillic
# beats English) and EN otherwise (fitness/saas/wellness/media/commerce —
# globally branded categories where English calls-to-action read more
# professional). This is a tradeoff, not a rule — owner can override per
# project later.
# ──────────────────────────────────────────────────────────────────────────

# RU verticals — consumer-facing Russian markets where Cyrillic CTAs read better.
_RU_VERTICALS: frozenset[str] = frozenset({
    "food", "medical", "legal", "realestate", "education",
})

# Per-vertical micro-copy table. Each value is (primary_label, secondary_label).
# Missing pairs fall back to {primary: context.title(), secondary: "Отмена"}.
_MICRO_COPY: dict[tuple[str, str], tuple[str, str]] = {
    # ─── fitness (EN) ──────────────────────────────────────────────────
    ("save", "fitness"): ("Save workout", "Cancel"),
    ("delete", "fitness"): ("Delete workout", "Cancel"),
    ("subscribe", "fitness"): ("Start training", "Maybe later"),
    ("cancel", "fitness"): ("Cancel session", "Keep session"),
    ("submit", "fitness"): ("Log workout", "Cancel"),
    # ─── saas (EN) ─────────────────────────────────────────────────────
    ("save", "saas"): ("Save changes", "Cancel"),
    ("delete", "saas"): ("Delete project", "Cancel"),
    ("subscribe", "saas"): ("Start free trial", "Maybe later"),
    ("cancel", "saas"): ("Cancel subscription", "Keep plan"),
    ("submit", "saas"): ("Create project", "Cancel"),
    # ─── wellness (EN) ─────────────────────────────────────────────────
    ("save", "wellness"): ("Save progress", "Cancel"),
    ("delete", "wellness"): ("Delete entry", "Cancel"),
    ("subscribe", "wellness"): ("Подписаться на советы", "Не сейчас"),
    ("cancel", "wellness"): ("Cancel session", "Keep session"),
    ("submit", "wellness"): ("Submit reflection", "Cancel"),
    # ─── food (RU) ─────────────────────────────────────────────────────
    ("save", "food"): ("Сохранить заказ", "Отмена"),
    ("delete", "food"): ("Удалить блюдо", "Отмена"),
    ("subscribe", "food"): ("Подписаться на скидки", "Не сейчас"),
    ("cancel", "food"): ("Отменить заказ", "Оставить заказ"),
    ("submit", "food"): ("Оформить заказ", "Отмена"),
    # ─── medical (RU) ──────────────────────────────────────────────────
    ("save", "medical"): ("Сохранить запись", "Отмена"),
    ("delete", "medical"): ("Удалить запись", "Отмена"),
    ("subscribe", "medical"): ("Записаться к врачу", "Не сейчас"),
    ("cancel", "medical"): ("Отменить визит", "Оставить визит"),
    ("submit", "medical"): ("Записаться на приём", "Отмена"),
    # ─── legal (RU) ────────────────────────────────────────────────────
    ("save", "legal"): ("Сохранить документ", "Отмена"),
    ("delete", "legal"): ("Удалить документ", "Отмена"),
    ("subscribe", "legal"): ("Получить консультацию", "Не сейчас"),
    ("cancel", "legal"): ("Отменить консультацию", "Оставить запись"),
    ("submit", "legal"): ("Отправить заявку", "Отмена"),
    # ─── realestate (RU) ───────────────────────────────────────────────
    ("save", "realestate"): ("Сохранить объект", "Отмена"),
    ("delete", "realestate"): ("Удалить из избранного", "Отмена"),
    ("subscribe", "realestate"): ("Подписаться на подборку", "Не сейчас"),
    ("cancel", "realestate"): ("Отменить показ", "Оставить запись"),
    ("submit", "realestate"): ("Записаться на показ", "Отмена"),
    # ─── education (RU) ────────────────────────────────────────────────
    ("save", "education"): ("Сохранить прогресс", "Отмена"),
    ("delete", "education"): ("Удалить курс", "Отмена"),
    ("subscribe", "education"): ("Записаться на курс", "Не сейчас"),
    ("cancel", "education"): ("Отменить запись", "Оставить запись"),
    ("submit", "education"): ("Отправить задание", "Отмена"),
    # ─── media (EN) ────────────────────────────────────────────────────
    ("save", "media"): ("Save article", "Cancel"),
    ("delete", "media"): ("Delete post", "Cancel"),
    ("subscribe", "media"): ("Subscribe for updates", "Maybe later"),
    ("cancel", "media"): ("Cancel subscription", "Keep subscription"),
    ("submit", "media"): ("Publish", "Cancel"),
    # ─── commerce (EN) ─────────────────────────────────────────────────
    ("save", "commerce"): ("Save to wishlist", "Cancel"),
    ("delete", "commerce"): ("Remove item", "Cancel"),
    ("subscribe", "commerce"): ("Subscribe for deals", "Maybe later"),
    ("cancel", "commerce"): ("Cancel order", "Keep order"),
    ("submit", "commerce"): ("Place order", "Cancel"),
}


def lookup_micro_copy(context: str, vertical: str) -> dict[str, str]:
    """Return action-specific button labels seeded by vertical (Malewicz G13).

    Phase G13 says generic labels ("Save", "OK") signal AI slop. Phase J
    delivers the concrete replacement: a primary verb-noun pair tuned to
    the vertical's voice + a secondary "out" that's not just "Cancel"
    when context allows.

    Language picks: RU verticals (food/medical/legal/realestate/education)
    get Cyrillic; EN verticals (fitness/saas/wellness/media/commerce)
    get English. The choice is deliberate — Russian consumer-facing
    markets read better in Cyrillic; globally branded categories read
    more professional in English.

    Missing pairs return {"primary": context.title(), "secondary": "Отмена"}
    — a safe fallback so callers never get KeyError.

    >>> lookup_micro_copy("save", "fitness")
    {'primary': 'Save workout', 'secondary': 'Cancel'}
    >>> lookup_micro_copy("delete", "saas")
    {'primary': 'Delete project', 'secondary': 'Cancel'}
    """
    key = (context.lower().strip(), vertical.lower().strip())
    pair = _MICRO_COPY.get(key)
    if pair is None:
        return {"primary": context.title(), "secondary": "Отмена"}
    return {"primary": pair[0], "secondary": pair[1]}


# ──────────────────────────────────────────────────────────────────────────
# J2 — derive_gradient_pair
#
# Malewicz Ch3 / G3: gradient that doesn't read as garish uses the same
# temperature and shifts hue 15-30° with -10% saturation. We pin to hue+25°
# and sat×0.9 — middle of the band, predictable output.
# ──────────────────────────────────────────────────────────────────────────


def _expand_hex(hex_str: str) -> str:
    """Normalize a hex color to 6-digit form with leading `#`.

    Accepts `#fff`, `fff`, `#FFFFFF`, `FFFFFF`. Raises ValueError for
    anything that isn't 3 or 6 hex digits (optionally `#`-prefixed).
    """
    s = hex_str.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"expected 3 or 6 hex digits, got {hex_str!r}")
    try:
        int(s, 16)
    except ValueError as exc:
        raise ValueError(f"not a valid hex color: {hex_str!r}") from exc
    return f"#{s.lower()}"


def _hex_to_rgb01(hex_str: str) -> tuple[float, float, float]:
    """Convert `#rrggbb` to (r, g, b) in [0.0, 1.0]. Hex must be normalized."""
    s = hex_str.lstrip("#")
    r = int(s[0:2], 16) / 255.0
    g = int(s[2:4], 16) / 255.0
    b = int(s[4:6], 16) / 255.0
    return r, g, b


def _rgb01_to_hex(r: float, g: float, b: float) -> str:
    """Convert (r, g, b) in [0.0, 1.0] to `#rrggbb` lowercase, clamped."""
    def _clamp(x: float) -> int:
        return max(0, min(255, round(x * 255)))
    return f"#{_clamp(r):02x}{_clamp(g):02x}{_clamp(b):02x}"


def derive_gradient_pair(primary_hex: str) -> tuple[str, str]:
    """Derive a Malewicz-compliant gradient pair from a primary brand color.

    Formula (Ch3 / G3): same temperature, hue + 25° (mod 360), saturation
    × 0.9 (≈-10% relative). Operates in HLS space via stdlib `colorsys`
    (which uses HLS, not HSL — same model, swapped letter order).

    Returns `(primary_normalized, shifted)` as `#rrggbb` lowercase. The
    primary is round-tripped through normalisation so both outputs share
    the same format (no mix of `#FFF` and `#abc123`).

    Raises ValueError on a malformed hex — caller decides whether to skip
    the section or substitute a fallback.

    >>> derive_gradient_pair("#92400E")
    ('#92400e', '#a16207')
    """
    normalized = _expand_hex(primary_hex)
    r, g, b = _hex_to_rgb01(normalized)
    # colorsys uses HLS (hue, lightness, saturation) — order differs from
    # HSL but the model is identical.
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    # Hue rotation: + 25° on a 360° wheel == + (25/360) on the [0, 1) ring.
    # `% 1.0` handles wrap (340° + 25° → 5° / 0.014…).
    h_shifted = (h + 25.0 / 360.0) % 1.0
    # Saturation drop: × 0.9, clamped to [0, 1].
    s_shifted = max(0.0, min(1.0, s * 0.9))
    r2, g2, b2 = colorsys.hls_to_rgb(h_shifted, l, s_shifted)
    return normalized, _rgb01_to_hex(r2, g2, b2)


# ──────────────────────────────────────────────────────────────────────────
# J3 — derive_shadow_tint
#
# Malewicz Ch9 / G1: tinted box-shadow reads as depth, not as "blur layer
# pasted over white". Recipe: take the primary, drop saturation 10%, drop
# brightness 20%, render that tint at alpha 0.18 (≤ 0.4 floor) under the
# element. Soft offset (0, 8), blur 20, spread -2 to keep the shadow
# inside the visual silhouette rather than haloing out.
# ──────────────────────────────────────────────────────────────────────────


def derive_shadow_tint(primary_hex: str) -> dict:
    """Return Malewicz Ch9 box-shadow params for a primary brand color.

    Output dict carries:
      • x, y, blur, spread — the four numerics
      • color  — `rgba(r, g, b, alpha)` string at alpha 0.18 (≤ 0.4 floor)
      • tint_hex — primary with HSB sat -10, brightness -20 (the darker
        relative used when a solid-color tinted shadow is needed instead
        of an rgba)
      • css — directly pasteable `box-shadow: …;` declaration

    Raises ValueError on malformed hex.

    >>> derive_shadow_tint("#92400E")  # doctest: +ELLIPSIS
    {'x': 0, 'y': 8, 'blur': 20, 'spread': -2, ...}
    """
    normalized = _expand_hex(primary_hex)
    r, g, b = _hex_to_rgb01(normalized)
    # HSB (== HSV in stdlib): rgb_to_hsv returns (h, s, v) in [0, 1].
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    # Saturation - 10% absolute, brightness - 20% absolute. Both clamped.
    s_tint = max(0.0, min(1.0, s - 0.10))
    v_tint = max(0.0, min(1.0, v - 0.20))
    r2, g2, b2 = colorsys.hsv_to_rgb(h, s_tint, v_tint)
    tint_hex = _rgb01_to_hex(r2, g2, b2)
    # rgba uses the ORIGINAL primary at alpha 0.18 — that's what reads as
    # "tinted shadow" rather than "darker silhouette".
    r255 = max(0, min(255, round(r * 255)))
    g255 = max(0, min(255, round(g * 255)))
    b255 = max(0, min(255, round(b * 255)))
    alpha = 0.18  # well under the 0.4 max from Ch9
    color = f"rgba({r255}, {g255}, {b255}, {alpha})"
    x, y, blur, spread = 0, 8, 20, -2
    css = f"box-shadow: {x} {y}px {blur}px {spread}px {color};"
    return {
        "x": x,
        "y": y,
        "blur": blur,
        "spread": spread,
        "color": color,
        "tint_hex": tint_hex,
        "css": css,
    }


# ──────────────────────────────────────────────────────────────────────────
# J4 — auto_nav_style
#
# G17 enforcement at lookup time: hamburger MUST NOT be primary mobile nav.
# Bottom-tabs primary on mobile (thumb-reachable, always visible). Top-bar
# primary on desktop. Side-rail for app-style desktop secondaries (Slack,
# Linear, Discord layouts) where the top-bar carries only branding/profile.
# ──────────────────────────────────────────────────────────────────────────


def auto_nav_style(target: str, tier: str | None = None) -> str:
    """Pick the right navigation style for `target` + `tier` (G17).

    target: ``"mobile"`` | ``"desktop"``
    tier:   ``"primary"`` (default) | ``"secondary"``

    Mapping (G17 says hamburger as primary is a quality-tax fail):
      • (mobile, primary)    → "bottom-tabs"   — always; G17 forbids hamburger primary
      • (mobile, secondary)  → "hamburger"     — the one place hamburger is allowed
      • (desktop, primary)   → "top-bar"
      • (desktop, secondary) → "side-rail"     — app-style (Slack/Linear/Discord)

    Raises ValueError when target is unknown.

    >>> auto_nav_style("mobile")
    'bottom-tabs'
    >>> auto_nav_style("mobile", "secondary")
    'hamburger'
    >>> auto_nav_style("desktop", "primary")
    'top-bar'
    """
    t = (tier or "primary").lower().strip()
    target_l = target.lower().strip()
    if target_l == "mobile":
        if t == "primary":
            return "bottom-tabs"
        if t == "secondary":
            return "hamburger"
        raise ValueError(f"unknown tier {tier!r}; expected 'primary' or 'secondary'")
    if target_l == "desktop":
        if t == "primary":
            return "top-bar"
        if t == "secondary":
            return "side-rail"
        raise ValueError(f"unknown tier {tier!r}; expected 'primary' or 'secondary'")
    raise ValueError(f"unknown target {target!r}; expected 'mobile' or 'desktop'")


def format_design_brief(
    *,
    palette: Palette | None = None,
    font_pairing: FontPairing | None = None,
    guidelines: Sequence[UxGuideline] = (),
    landing_pattern: LandingPattern | None = None,
    style_preset: StylePreset | None = None,
    icon_family: IconRow | None = None,
    chart_types: Sequence[ChartType] = (),
    design_patterns: Sequence[dict] = (),
    gradient_pair: tuple[str, str] | None = None,
    shadow_tint: dict | None = None,
    micro_copy: dict[str, dict[str, str]] | None = None,
    nav_style: str | None = None,
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

    if design_patterns:
        rendered_patterns: list[str] = []
        for dp in design_patterns:
            line = f"  • {dp['name']}"
            score = dp.get("usability_score") if isinstance(dp, dict) else None
            if score is not None:
                line += f" [usability {score}/10]"
            if dp["vibe_tags"]:
                line += f" — {dp['vibe_tags']}"
            elif dp["summary"]:
                line += f" — {dp['summary'][:120]}"
            extras: list[str] = []
            if dp["color_tokens"]:
                extras.append(f"tokens: {dp['color_tokens']}")
            if dp["typography"]:
                extras.append(f"font: {dp['typography']}")
            if extras:
                line += "\n      " + " · ".join(extras)
            rendered_patterns.append(line)
        parts.append(
            "DESIGN STYLE REFERENCES (borrow vibe, HEX tokens, and font cues "
            "from the closest match — do not copy verbatim; higher "
            "usability_score = lower friction per Malewicz Ch24):\n"
            + "\n".join(rendered_patterns)
        )

    # Phase J — derived Malewicz tokens. Render only the lines that have
    # data so a partial brief (e.g. gradient + nav, no shadow/copy) still
    # produces a clean block without empty entries.
    j_lines: list[str] = []
    if gradient_pair is not None:
        a, b = gradient_pair
        j_lines.append(
            f"  gradient_pair:    {a} → {b}  (use на hero, кнопках)"
        )
    if shadow_tint is not None:
        j_lines.append(f"  shadow_tint:      {shadow_tint['css']}")
    if nav_style is not None:
        j_lines.append(f"  nav_style:        {nav_style}")
    if micro_copy:
        # Compact: render each (context → primary) on one continuation line.
        pieces = [
            f"{ctx} → \"{labels['primary']}\""
            for ctx, labels in micro_copy.items()
            if labels and labels.get("primary")
        ]
        if pieces:
            j_lines.append("  micro_copy:       " + ", ".join(pieces))
    if j_lines:
        parts.append(
            "ПРОИЗВОДНЫЕ ТОКЕНЫ (Malewicz):\n" + "\n".join(j_lines)
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


# ══════════════════════════════════════════════════════════════════════════
# Phase D.1' + D.2' — awwwards corpus + design-patterns lookup
#
# Two separate JSON files under ``apps/api/data/``:
#   • awwwards_corpus.json   — 10 reference sites (5 Western + 5 RU
#     synthesized) with palette/fonts/motion_signature. Stylistic anchor
#     when palette+vertical match.
#   • design_patterns.json   — ~50 section snippets (hero/features/CTA…)
#     with ready-to-paste tailwind class lists + kit-class names.
#
# Both loaders are `lru_cache`d so import cost stays zero until the first
# lookup, and `FileNotFoundError` degrades to `[]` instead of crashing —
# the prompt builder treats absent data the same as zero matches.
# ══════════════════════════════════════════════════════════════════════════


@lru_cache(maxsize=1)
def _load_awwwards_corpus() -> list[dict]:
    """Read ``apps/api/data/awwwards_corpus.json``. Lazy + cached so import
    cost stays zero when the corpus isn't queried.

    Returns an empty list on FileNotFoundError so the prompt builder
    treats a missing file as "no matches" instead of crashing.
    """
    try:
        return json.loads(
            (_DATA_DIR / "awwwards_corpus.json").read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        return []


def lookup_awwwards_reference(
    *tokens: str, region: str | None = None, limit: int = 3
) -> list[dict]:
    """Surface up-to-`limit` awwwards-tier reference entries matching the
    prompt tokens (industry_tags + style_id substring match).

    ``region`` ∈ {"western", "russian", None}; ``None`` merges both.
    Result is ordered by tag-overlap score, then by id (alpha) for
    deterministic ties. When ``tokens`` is empty the function returns the
    first `limit` entries from the (optionally region-filtered) corpus —
    callers asking "give me any X reference" get a stable answer.
    """
    corpus = _load_awwwards_corpus()
    if not corpus:
        return []

    pool = (
        [e for e in corpus if e.get("region") == region]
        if region is not None
        else list(corpus)
    )
    if not pool:
        return []

    if not tokens:
        # No tokens → deterministic order by id, take first `limit`.
        return sorted(pool, key=lambda e: e.get("id", ""))[:limit]

    lowered = [t.lower() for t in tokens]

    def _score(entry: dict) -> int:
        # Match tokens against industry_tags + style_id. Each tag-token
        # substring hit = +1; style_id hit = +1.
        hay = " ".join(entry.get("industry_tags", []) + [entry.get("style_id", "")]).lower()
        return sum(1 for t in lowered if t and t in hay)

    scored = [(_score(e), e) for e in pool]
    scored.sort(key=lambda t: (-t[0], t[1].get("id", "")))
    return [e for score, e in scored[:limit] if score > 0] or [
        # If no token scored, still return the first `limit` from sorted
        # pool — never an empty list when the corpus has entries.
        e for _, e in scored[:limit]
    ]


@lru_cache(maxsize=1)
def _load_design_patterns() -> list[dict]:
    """Read ``apps/api/data/design_patterns.json``. Lazy + cached.

    Returns an empty list on FileNotFoundError so callers treat missing
    data as "no patterns available" instead of crashing.
    """
    try:
        return json.loads(
            (_DATA_DIR / "design_patterns.json").read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        return []


def lookup_design_pattern_snippets(
    section_type: str, style_id: str | None = None, limit: int = 3
) -> list[dict]:
    """Return up-to-`limit` snippet patterns for the given ``section_type``,
    optionally filtered by ``style_id``.

    Used by the prompt builder to feed the AI concrete tailwind class
    lists instead of letting it invent hero layouts from scratch.

    Falls back to all ``section_type`` matches across all styles when
    ``style_id`` is None or no style match exists — caller always gets
    at least the section_type slice (never an empty list when the
    section_type exists in the corpus).
    """
    patterns = _load_design_patterns()
    if not patterns:
        return []

    section_pool = [p for p in patterns if p.get("section_type") == section_type]
    if not section_pool:
        return []

    if style_id is not None:
        matched = [p for p in section_pool if p.get("style_id") == style_id]
        if matched:
            return matched[:limit]
        # Fallback — section matches but no style match. Caller still
        # wants snippets for THIS section, not silence.

    return section_pool[:limit]
