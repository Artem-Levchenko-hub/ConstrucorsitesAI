"""Canonical catalog of fonts the in-preview font picker can apply.

Single source of truth, merged from the two places the generator already knows
fonts: the freeform pairings in ``design_tokens._FONT_PAIRINGS`` and the per-
preset fonts in ``design_presets.PRESETS``. Used BOTH by ``GET /api/fonts`` (the
picker) and by the style-patch validator (``is_known_family`` / ``href_for``), so
a font the UI offers is exactly a font the backend will accept — no drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from omnia_api.services.design_presets import PRESETS
from omnia_api.services.design_tokens import _FONT_PAIRINGS, _google_fonts_url

# Family → category. Anything not listed defaults to "sans". Categories drive the
# CSS fallback stack so a swapped font degrades sensibly if the webfont is slow.
_SERIF: frozenset[str] = frozenset(
    {
        "Playfair Display", "Fraunces", "Cormorant Garamond", "Spectral",
        "Instrument Serif", "Lora", "Newsreader",
    }
)
_DISPLAY: frozenset[str] = frozenset({"Unbounded", "Syne", "Bricolage Grotesque"})
_MONO: frozenset[str] = frozenset({"JetBrains Mono"})


def _category(family: str) -> str:
    if family in _SERIF:
        return "serif"
    if family in _MONO:
        return "mono"
    if family in _DISPLAY:
        return "display"
    return "sans"


def _css_stack(family: str, category: str) -> str:
    if category == "serif":
        return f"'{family}', Georgia, 'Times New Roman', serif"
    if category == "mono":
        return f"'{family}', ui-monospace, SFMono-Regular, monospace"
    return f"'{family}', system-ui, -apple-system, sans-serif"


@dataclass(frozen=True)
class FontOption:
    family: str
    category: str
    google_fonts_url: str
    css_stack: str


def _collect_families() -> list[str]:
    seen: set[str] = set()
    families: list[str] = []

    def _add(name: str | None) -> None:
        n = (name or "").strip()
        if n and n not in seen:
            seen.add(n)
            families.append(n)

    for display, body in _FONT_PAIRINGS:
        _add(display)
        _add(body)
    for preset in PRESETS.values():
        _add(preset.fonts.get("display"))
        _add(preset.fonts.get("body"))
    return families


@lru_cache(maxsize=1)
def font_catalog() -> tuple[FontOption, ...]:
    """All offerable fonts. Cached — the source lists are static."""
    cat_order = {"sans": 0, "serif": 1, "display": 2, "mono": 3}
    items: list[FontOption] = []
    for family in _collect_families():
        category = _category(family)
        # css2 link for a single family (display==body collapses to one entry).
        url = _google_fonts_url(family, family)
        items.append(FontOption(family, category, url, _css_stack(family, category)))
    items.sort(key=lambda f: (cat_order.get(f.category, 9), f.family))
    return tuple(items)


@lru_cache(maxsize=1)
def _family_index() -> dict[str, FontOption]:
    return {f.family: f for f in font_catalog()}


def is_known_family(family: str) -> bool:
    return family in _family_index()


def href_for(family: str) -> str | None:
    """The Google Fonts href for a known family — re-derived server-side so the
    style-patch never trusts a client-supplied <link> URL."""
    opt = _family_index().get(family)
    return opt.google_fonts_url if opt else None


def css_stack_for(family: str) -> str | None:
    opt = _family_index().get(family)
    return opt.css_stack if opt else None
