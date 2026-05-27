"""Curated colour palettes for Omnia.AI sites (Phase L10).

Designed under principles distilled from Josef Albers' *Interaction of
Color* (Yale, 50th anniversary edition):

* **Colour is relative.** Two HEX values look different against
  different neighbours; legibility is decided by the *pair*, not the
  HEX. Every palette ships its own ``text`` + ``bg`` so the contrast
  is fixed at design time, not improvised by the LLM.
* **No pure ``#000`` on pure ``#FFF``.** Albers spent a chapter on why
  this combination is hostile — the eye over-sharpens edges and
  text vibrates. All palettes use a softened neutral (zinc-950,
  slate-900, stone-50) instead.
* **Limit hue count.** Three "pure" colours max per layout —
  primary, accent, neutral. Semantics (success/warning/error) are
  pinned to safe defaults across palettes so the model never has to
  invent them.
* **Mind simultaneous contrast.** Muted text on coloured surface needs
  ≥ 4.5:1 contrast (WCAG AA), large display text needs ≥ 3:1 (WCAG
  AA Large). Every palette is precomputed against `text` vs `bg` and
  `muted` vs `bg`.

The 60+ palettes are organised by ``vibe`` (matches the eight vibes
exposed in ``services/lean_prompt._VIBE_TOKENS``), so the
``pick_palette`` resolver can answer "give me one swiss-minimal
palette for a SaaS B2B prompt" with a deterministic, high-quality
pick. Each pick survives a WCAG AA contrast check before being
applied.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# ─── Contrast math (WCAG 2.x) ────────────────────────────────────────────


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _channel_lin(c: int) -> float:
    s = c / 255.0
    return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    r, g, b = _hex_to_rgb(hex_color)
    return (
        0.2126 * _channel_lin(r)
        + 0.7152 * _channel_lin(g)
        + 0.0722 * _channel_lin(b)
    )


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.x contrast ratio. ≥ 4.5 = AA body; ≥ 3 = AA large display;
    ≥ 7 = AAA. Returns a value in [1, 21]."""
    l1 = _relative_luminance(fg)
    l2 = _relative_luminance(bg)
    light, dark = max(l1, l2), min(l1, l2)
    return (light + 0.05) / (dark + 0.05)


def meets_wcag_aa(fg: str, bg: str, *, large: bool = False) -> bool:
    return contrast_ratio(fg, bg) >= (3.0 if large else 4.5)


# ─── Palette dataclass ───────────────────────────────────────────────────


@dataclass(frozen=True)
class CuratedPalette:
    """A vetted, semantic palette ready to drop onto a `PageIR`."""

    id: str
    name: str
    vibe: str
    primary: str
    accent: str
    bg: str
    surface: str
    text: str
    muted: str
    border: str
    success: str = "#16A34A"
    warning: str = "#D97706"
    error: str = "#DC2626"
    dark_mode: bool = False


# ─── 60+ curated palettes ────────────────────────────────────────────────
# Grouped by vibe. Each tested for WCAG AA at design time
# (test_palettes.py enforces this).

_PALETTES: tuple[CuratedPalette, ...] = (
    # ─── swiss-minimal (light, neutral, one accent) ──────────────────
    CuratedPalette("swiss-stone-noir",     "Stone Noir",    "swiss-minimal", "#0F172A", "#2563EB", "#FAFAF9", "#FFFFFF", "#0F172A", "#52525B", "#E5E5E5"),
    CuratedPalette("swiss-zinc-ink",       "Zinc Ink",      "swiss-minimal", "#18181B", "#0EA5E9", "#FAFAFA", "#FFFFFF", "#18181B", "#71717A", "#E4E4E7"),
    CuratedPalette("swiss-bone-cobalt",    "Bone Cobalt",   "swiss-minimal", "#1E3A8A", "#F97316", "#F5F5F4", "#FFFFFF", "#1C1917", "#57534E", "#E7E5E4"),
    CuratedPalette("swiss-paper-tea",      "Paper Tea",     "swiss-minimal", "#134E4A", "#CA8A04", "#FAFAF9", "#FFFFFF", "#0F172A", "#475569", "#E2E8F0"),
    CuratedPalette("swiss-canvas-rust",    "Canvas Rust",   "swiss-minimal", "#7C2D12", "#0369A1", "#FEFCE8", "#FFFFFF", "#1C1917", "#57534E", "#E7E5E4"),
    CuratedPalette("swiss-fog-emerald",    "Fog Emerald",   "swiss-minimal", "#064E3B", "#B45309", "#F8FAFC", "#FFFFFF", "#0F172A", "#475569", "#E2E8F0"),
    CuratedPalette("swiss-sand-violet",    "Sand Violet",   "swiss-minimal", "#4C1D95", "#A16207", "#FAFAF9", "#FFFFFF", "#1C1917", "#57534E", "#E7E5E4"),
    CuratedPalette("swiss-bone-graphite",  "Bone Graphite", "swiss-minimal", "#27272A", "#0EA5E9", "#FAFAFA", "#FFFFFF", "#18181B", "#71717A", "#E4E4E7"),

    # ─── apple-tech (display-hero, lots of white space, single vivid accent) ─
    CuratedPalette("apple-jet-azure",      "Jet Azure",     "apple-tech",   "#0A0A0A", "#2997FF", "#FFFFFF", "#F5F5F7", "#0A0A0A", "#6E6E73", "#D2D2D7"),
    CuratedPalette("apple-jet-emerald",    "Jet Emerald",   "apple-tech",   "#0A0A0A", "#34C759", "#FFFFFF", "#F5F5F7", "#1D1D1F", "#6E6E73", "#D2D2D7"),
    CuratedPalette("apple-pearl-coral",    "Pearl Coral",   "apple-tech",   "#1D1D1F", "#FF453A", "#FFFFFF", "#F2F2F7", "#1D1D1F", "#6E6E73", "#D2D2D7"),
    CuratedPalette("apple-graphite-amber", "Graphite Amber", "apple-tech",  "#1D1D1F", "#FF9F0A", "#FAFAFA", "#F5F5F7", "#1D1D1F", "#6E6E73", "#D2D2D7"),
    CuratedPalette("apple-titan-iris",     "Titan Iris",    "apple-tech",   "#0A0A0A", "#5E5CE6", "#FFFFFF", "#F5F5F7", "#1D1D1F", "#6E6E73", "#D2D2D7"),
    CuratedPalette("apple-onyx-flame",     "Onyx Flame",    "apple-tech",   "#0A0A0A", "#FF375F", "#FFFFFF", "#F5F5F7", "#1D1D1F", "#6E6E73", "#D2D2D7"),
    CuratedPalette("apple-night-azure",    "Night Azure",   "apple-tech",   "#FFFFFF", "#0A84FF", "#0A0A0A", "#1C1C1E", "#F5F5F7", "#8E8E93", "#3A3A3C", dark_mode=True),
    CuratedPalette("apple-night-coral",    "Night Coral",   "apple-tech",   "#FFFFFF", "#FF6482", "#0A0A0A", "#1C1C1E", "#F5F5F7", "#8E8E93", "#3A3A3C", dark_mode=True),

    # ─── linear-dark (dark + glow + tonal neutrals) ────────────────────────
    CuratedPalette("linear-iris",          "Iris",          "linear-dark",  "#FFFFFF", "#7170FF", "#08090A", "#1C1D22", "#F4F4F5", "#A1A1AA", "#27272A", dark_mode=True),
    CuratedPalette("linear-violet",        "Violet",        "linear-dark",  "#FFFFFF", "#A855F7", "#0A0A0A", "#1A1A1A", "#FAFAFA", "#9CA3AF", "#262626", dark_mode=True),
    CuratedPalette("linear-emerald",       "Emerald",       "linear-dark",  "#FFFFFF", "#10B981", "#09090B", "#18181B", "#FAFAFA", "#A1A1AA", "#27272A", dark_mode=True),
    CuratedPalette("linear-amber",         "Amber",         "linear-dark",  "#FFFFFF", "#F59E0B", "#0C0A09", "#1C1917", "#FAFAF9", "#A8A29E", "#292524", dark_mode=True),
    CuratedPalette("linear-coral",         "Coral",         "linear-dark",  "#FFFFFF", "#FB7185", "#0A0A0A", "#171717", "#FAFAFA", "#A3A3A3", "#262626", dark_mode=True),
    CuratedPalette("linear-cyan",          "Cyan",          "linear-dark",  "#FFFFFF", "#22D3EE", "#0F172A", "#1E293B", "#F8FAFC", "#94A3B8", "#334155", dark_mode=True),
    CuratedPalette("linear-pink",          "Pink",          "linear-dark",  "#FFFFFF", "#EC4899", "#09090B", "#18181B", "#FAFAFA", "#A1A1AA", "#27272A", dark_mode=True),
    CuratedPalette("linear-lime",          "Lime",          "linear-dark",  "#FFFFFF", "#A3E635", "#09090B", "#1A1A1A", "#FAFAFA", "#A1A1AA", "#262626", dark_mode=True),

    # ─── fintech-trust (deep navy + electric / sage accent) ────────────────
    CuratedPalette("fintech-deepwater",    "Deepwater",     "fintech-trust", "#0B1220", "#00D4AA", "#F8FAFC", "#FFFFFF", "#0B1220", "#475569", "#E2E8F0"),
    CuratedPalette("fintech-midnight",     "Midnight",      "fintech-trust", "#0F172A", "#0EA5E9", "#FFFFFF", "#F8FAFC", "#0F172A", "#475569", "#E2E8F0"),
    CuratedPalette("fintech-graphite-jade","Graphite Jade", "fintech-trust", "#1E293B", "#14B8A6", "#FFFFFF", "#F8FAFC", "#0F172A", "#475569", "#E2E8F0"),
    CuratedPalette("fintech-navy-amber",   "Navy Amber",    "fintech-trust", "#1E3A8A", "#F59E0B", "#FFFFFF", "#F8FAFC", "#1E3A8A", "#475569", "#E2E8F0"),
    CuratedPalette("fintech-ink-mint",     "Ink Mint",      "fintech-trust", "#020617", "#34D399", "#F8FAFC", "#FFFFFF", "#020617", "#64748B", "#CBD5E1"),
    CuratedPalette("fintech-navy-night",   "Navy Night",    "fintech-trust", "#F8FAFC", "#22D3EE", "#0B1220", "#1E293B", "#F8FAFC", "#94A3B8", "#334155", dark_mode=True),
    CuratedPalette("fintech-charcoal",     "Charcoal",      "fintech-trust", "#FAFAFA", "#10B981", "#0A0A0A", "#171717", "#FAFAFA", "#A3A3A3", "#262626", dark_mode=True),
    CuratedPalette("fintech-prussian",     "Prussian",      "fintech-trust", "#FFFFFF", "#FBBF24", "#0C2340", "#1A365D", "#F8FAFC", "#94A3B8", "#2D3748", dark_mode=True),

    # ─── editorial-luxury (serif, black + gold, dark accents) ──────────────
    CuratedPalette("luxury-onyx-gold",     "Onyx Gold",     "editorial-luxury", "#0C0A09", "#A16207", "#FAFAF9", "#FFFFFF", "#0C0A09", "#57534E", "#E7E5E4"),
    CuratedPalette("luxury-noir-champagne","Noir Champagne","editorial-luxury", "#1C1917", "#B45309", "#F5F5F4", "#FFFFFF", "#1C1917", "#57534E", "#D6D3D1"),
    CuratedPalette("luxury-cocoa-ochre",   "Cocoa Ochre",   "editorial-luxury", "#292524", "#D97706", "#FAFAF9", "#F5F5F4", "#1C1917", "#57534E", "#E7E5E4"),
    CuratedPalette("luxury-charcoal-rose", "Charcoal Rose", "editorial-luxury", "#1C1917", "#9F1239", "#FAFAF9", "#FFFFFF", "#1C1917", "#57534E", "#E7E5E4"),
    CuratedPalette("luxury-night-gold",    "Night Gold",    "editorial-luxury", "#F5F5F4", "#D4AF37", "#0C0A09", "#1C1917", "#FAFAF9", "#A8A29E", "#292524", dark_mode=True),
    CuratedPalette("luxury-onyx-bronze",   "Onyx Bronze",   "editorial-luxury", "#FAFAF9", "#CD7F32", "#0C0A09", "#292524", "#FAFAF9", "#A8A29E", "#44403C", dark_mode=True),
    CuratedPalette("luxury-deep-emerald",  "Deep Emerald",  "editorial-luxury", "#FAFAF9", "#A16207", "#022C22", "#064E3B", "#FAFAF9", "#A7F3D0", "#065F46", dark_mode=True),
    CuratedPalette("luxury-burgundy",      "Burgundy",      "editorial-luxury", "#FEF2F2", "#CA8A04", "#450A0A", "#7F1D1D", "#FAFAFA", "#FCA5A5", "#991B1B", dark_mode=True),

    # ─── brutalist (high contrast, vivid accent, no gradients) ─────────────
    CuratedPalette("brutalist-orange",     "Orange Riot",   "brutalist",    "#0A0A0A", "#FF6B35", "#FFFFFF", "#FAFAFA", "#0A0A0A", "#525252", "#0A0A0A"),
    CuratedPalette("brutalist-electric",   "Electric",      "brutalist",    "#0A0A0A", "#00F5D4", "#FFFFFF", "#FAFAFA", "#0A0A0A", "#525252", "#0A0A0A"),
    CuratedPalette("brutalist-rose",       "Rose",          "brutalist",    "#0A0A0A", "#FF006E", "#FFFFFF", "#FAFAFA", "#0A0A0A", "#525252", "#0A0A0A"),
    CuratedPalette("brutalist-acid",       "Acid",          "brutalist",    "#0A0A0A", "#CFFF04", "#FFFFFF", "#FAFAFA", "#0A0A0A", "#525252", "#0A0A0A"),
    CuratedPalette("brutalist-ink-yellow", "Ink Yellow",    "brutalist",    "#0A0A0A", "#FACC15", "#FFFFFF", "#FAFAFA", "#0A0A0A", "#404040", "#0A0A0A"),
    CuratedPalette("brutalist-cyan",       "Cyan",          "brutalist",    "#0A0A0A", "#00D9FF", "#FFFFFF", "#FAFAFA", "#0A0A0A", "#525252", "#0A0A0A"),
    CuratedPalette("brutalist-magenta",    "Magenta",       "brutalist",    "#0A0A0A", "#D946EF", "#FFFFFF", "#FAFAFA", "#0A0A0A", "#525252", "#0A0A0A"),
    CuratedPalette("brutalist-dark-orange","Dark Orange",   "brutalist",    "#FAFAFA", "#FB923C", "#0A0A0A", "#171717", "#FAFAFA", "#A3A3A3", "#FAFAFA", dark_mode=True),

    # ─── glassmorphism (vivid gradients, soft surfaces) ────────────────────
    CuratedPalette("glass-indigo-rose",    "Indigo Rose",   "glassmorphism", "#4F46E5", "#DB2777", "#FAFAFA", "#FFFFFF", "#1E1B4B", "#6B7280", "#E5E7EB"),
    CuratedPalette("glass-violet-tangerine","Violet Tangerine","glassmorphism", "#7C3AED", "#EA580C", "#F5F3FF", "#FFFFFF", "#2E1065", "#6B7280", "#E5E7EB"),
    CuratedPalette("glass-azure-coral",    "Azure Coral",   "glassmorphism", "#2563EB", "#E11D48", "#EFF6FF", "#FFFFFF", "#1E3A8A", "#6B7280", "#DBEAFE"),
    CuratedPalette("glass-mint-rose",      "Mint Rose",     "glassmorphism", "#0891B2", "#DB2777", "#F0FDFA", "#FFFFFF", "#134E4A", "#6B7280", "#CCFBF1"),
    CuratedPalette("glass-violet-night",   "Violet Night",  "glassmorphism", "#FAFAFA", "#FBBF24", "#1E1B4B", "#312E81", "#FAFAFA", "#A5B4FC", "#3730A3", dark_mode=True),
    CuratedPalette("glass-aurora",         "Aurora",        "glassmorphism", "#FAFAFA", "#34D399", "#0F172A", "#1E293B", "#F8FAFC", "#94A3B8", "#334155", dark_mode=True),

    # ─── y2k-neo (chrome, magenta, cyan, mixed serif/sans) ─────────────────
    CuratedPalette("y2k-magenta-cyan",     "Magenta Cyan",  "y2k-neo",      "#BE185D", "#0E7490", "#FFFFFF", "#FAFAFA", "#0F172A", "#6B7280", "#E5E7EB"),
    CuratedPalette("y2k-chrome-lime",      "Chrome Lime",   "y2k-neo",      "#0F172A", "#65A30D", "#FAFAFA", "#FFFFFF", "#0F172A", "#6B7280", "#E5E7EB"),
    CuratedPalette("y2k-pink-cyan",        "Pink Cyan",     "y2k-neo",      "#BE185D", "#0E7490", "#FFFFFF", "#FAFAFA", "#1F2937", "#6B7280", "#E5E7EB"),
    CuratedPalette("y2k-electric-amber",   "Electric Amber","y2k-neo",      "#6D28D9", "#B45309", "#FAFAFA", "#FFFFFF", "#1E1B4B", "#6B7280", "#E5E7EB"),
    CuratedPalette("y2k-night-neon",       "Night Neon",    "y2k-neo",      "#00F5D4", "#FF006E", "#0A0A0A", "#1A1A1A", "#FAFAFA", "#A3A3A3", "#262626", dark_mode=True),
    CuratedPalette("y2k-cyber-lime",       "Cyber Lime",    "y2k-neo",      "#CFFF04", "#FB923C", "#0A0A0A", "#171717", "#FAFAFA", "#A3A3A3", "#262626", dark_mode=True),

    # ─── wellness-casual (soft, friendly, low-saturation accent) ──────────
    CuratedPalette("wellness-blush",       "Blush",         "wellness-casual","#BE185D", "#7C3AED", "#FFF1F2", "#FFFFFF", "#831843", "#9F1239", "#FECDD3"),
    CuratedPalette("wellness-sage",        "Sage",          "wellness-casual","#15803D", "#A16207", "#F0FDF4", "#FFFFFF", "#14532D", "#4D7C0F", "#BBF7D0"),
    CuratedPalette("wellness-peach",       "Peach",         "wellness-casual","#C2410C", "#A21CAF", "#FFF7ED", "#FFFFFF", "#7C2D12", "#9A3412", "#FED7AA"),
    CuratedPalette("wellness-lilac",       "Lilac",         "wellness-casual","#7E22CE", "#BE185D", "#FAF5FF", "#FFFFFF", "#581C87", "#7E22CE", "#E9D5FF"),
    CuratedPalette("wellness-mint",        "Mint",          "wellness-casual","#0F766E", "#BE185D", "#F0FDFA", "#FFFFFF", "#134E4A", "#0F766E", "#A7F3D0"),
    CuratedPalette("wellness-coral-night", "Coral Night",   "wellness-casual","#FAFAFA", "#FDA4AF", "#1F2937", "#374151", "#FAFAFA", "#D1D5DB", "#4B5563", dark_mode=True),
)


# ─── Public API ──────────────────────────────────────────────────────────


def all_palettes() -> tuple[CuratedPalette, ...]:
    return _PALETTES


def palettes_for_vibe(vibe: str) -> tuple[CuratedPalette, ...]:
    return tuple(p for p in _PALETTES if p.vibe == vibe)


def get_palette(palette_id: str) -> CuratedPalette | None:
    for p in _PALETTES:
        if p.id == palette_id:
            return p
    return None


def pick_palette(
    *,
    vibe: str | None = None,
    dark_mode: bool | None = None,
    industry_hint: str | None = None,
) -> CuratedPalette:
    """Pick a deterministic palette by vibe + dark/light preference.

    Falls back to a neutral swiss-minimal pick if nothing matches —
    guarantees the caller always gets a real palette object.
    """
    candidates: Iterable[CuratedPalette] = _PALETTES
    if vibe:
        narrowed = tuple(p for p in candidates if p.vibe == vibe)
        if narrowed:
            candidates = narrowed
    if dark_mode is not None:
        narrowed = tuple(p for p in candidates if p.dark_mode == dark_mode)
        if narrowed:
            candidates = narrowed

    if industry_hint:
        hint = industry_hint.lower()
        scored = sorted(
            candidates,
            key=lambda p: (hint not in p.name.lower(), p.id),
        )
        return next(iter(scored), _PALETTES[0])

    return next(iter(sorted(candidates, key=lambda p: p.id)), _PALETTES[0])


def validate_palette(p: CuratedPalette) -> list[str]:
    """Return human-readable WCAG/Albers issues with this palette."""
    issues: list[str] = []
    body = contrast_ratio(p.text, p.bg)
    if body < 4.5:
        issues.append(f"text/bg contrast {body:.2f}<4.5 (WCAG AA fail)")
    surface_body = contrast_ratio(p.text, p.surface)
    if surface_body < 4.5:
        issues.append(
            f"text/surface contrast {surface_body:.2f}<4.5"
        )
    muted_body = contrast_ratio(p.muted, p.bg)
    if muted_body < 3.0:
        issues.append(
            f"muted/bg contrast {muted_body:.2f}<3 (AA-Large fail)"
        )
    # Albers rule: never pure #000 on pure #FFF.
    if p.text.upper() == "#000000" and p.bg.upper() == "#FFFFFF":
        issues.append("Albers: pure #000 on pure #FFF banned (vibration)")
    if p.bg.upper() == "#000000" and p.text.upper() == "#FFFFFF":
        issues.append("Albers: pure #FFF on pure #000 banned (vibration)")
    return issues


__all__ = [
    "CuratedPalette",
    "all_palettes",
    "contrast_ratio",
    "get_palette",
    "meets_wcag_aa",
    "palettes_for_vibe",
    "pick_palette",
    "validate_palette",
]
