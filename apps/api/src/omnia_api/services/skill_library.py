"""Loaders for the vendored `ui-ux-pro-max` skill in `apps/api/skills/`.

R-01 (deep module): callers see four helpers — `lookup_palette`,
`lookup_font_pairing`, `random_ux_guidelines`, `format_design_brief`. CSV
parsing, in-memory caching, and the path layout to `skills/ui-ux-pro-max/`
are all private.

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
) -> str:
    """Render a compact, system-prompt-friendly block from any subset of inputs.

    Returns an empty string when every input is empty/None — safe to splice
    into a prompt assembler unconditionally.

    The format is deliberately terse: line-prefixed key:value, no markdown
    tables, no decorative wrappers. The model parses it reliably while the
    block stays well under 1 KB even with all three sections populated.
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

    if guidelines:
        parts.append(
            "UX RULES (severity High — non-negotiable):\n"
            + "\n".join(
                f"  • [{g['category']}/{g['issue']}] {g['do']} — NOT: {g['dont']}"
                for g in guidelines
            )
        )

    return "\n\n".join(parts)
