"""Deterministic branded share-card injector (P2 — viral unfurl, pillar 4).

The entity template's `src/app/layout.tsx` ships a static `<head>` («Omnia
project»), so every shared `/p/<slug>` link unfurls as a generic, brand-less
card — the first impression of the viral loop (a colleague pastes the link)
is dead. The freeform-landing prompt already instructs Open Graph, but the
flagship entity hot-path (≈80 % of the corpus) never got it.

This is the deterministic, model-independent lever (the `.tsx` analogue of
`entity_theme` / `palette_guard`): from the project name + first prompt +
resolved palette we derive a small `{title, tagline, accent}` card and write it
as `src/app/omnia-share.ts`. The template's `generateMetadata()` and
`opengraph-image.tsx` route both import that module, so one brief paints the
share card on every niche at zero per-app model cost.

Title preference: the human project name → a recognised niche label → a prompt
snippet → «Omnia project». Never crashes, never ships raw garbage.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from omnia_api.services.discovery import infer_niche_label

#: Where the generated card module lands in the entity app.
MODULE_PATH = "src/app/omnia-share.ts"

#: Brand fallback accent (matches the template's default `omnia-share.ts`).
_DEFAULT_ACCENT = "#6366f1"
_DEFAULT_TITLE = "Omnia project"

#: Generic names that make a useless share-card title — derive from the idea.
_UNTITLED = {"untitled", "untitled project", "новый проект", "проект", "project"}

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

#: MOTION-half of APP-DNA (mirrors the entities' brief-driven --omnia-ease /
#: --omnia-dur, but DETERMINISTIC here — no model). One entrance tempo drives the
#: birth of every drizzle surface, so a gallery and a kids' shop visibly "come
#: alive" apart from one build. The drizzle `brandTokens` turns the chosen name
#: into the CSS-var pair; an unclassifiable brief lands on the professional
#: `precise` default the enterprise template ships with.
_MOTION_CALM = (
    "люкс", "премиум", "ювелир", "отел", "спа", "галере", "фото", "портфолио",
    "медиа", "журнал", "бутик", "интерьер", "архитект", "вино", "парфюм", "часы",
    "искусств", "дизайн-студ", "luxury", "gallery", "portfolio",
)
_MOTION_SNAPPY = (
    "детск", "игрушк", "ребён", "ребен", "кафе", "кофейн", "магазин", "шоп",
    "shop", "ecom", "e-com", "маркетплейс", "доставк", "еда", "пицц", "бургер",
    "фитнес", "спорт", "лайфстайл", "блог", "ивент", "праздник", "цвет", "store",
)


def _derive_motion(prompt: str | None) -> str:
    """Deterministically pick an entrance tempo (`calm` / `snappy` / `precise`)
    from the brief's niche keywords. Defaults to `precise` — the crisp, composed
    feel that fits the fintech / B2B / SaaS bulk and the template's own default."""
    text = (prompt or "").lower()
    if any(kw in text for kw in _MOTION_CALM):
        return "calm"
    if any(kw in text for kw in _MOTION_SNAPPY):
        return "snappy"
    return "precise"


@dataclass(frozen=True)
class ShareCard:
    """The minimal brand payload a share card and `<head>` need."""

    title: str
    tagline: str
    accent: str
    #: Entrance tempo (`calm` / `snappy` / `precise`) — APP-DNA MOTION-half.
    motion: str = "precise"


def _clean_accent(accent_hex: str | None) -> str:
    """A safe 6-digit hex, or the brand default — never raw garbage on the card."""
    value = (accent_hex or "").strip()
    return value if _HEX_RE.match(value) else _DEFAULT_ACCENT


def _derive_title(name: str | None, prompt: str | None) -> str:
    """Human name → recognised niche → prompt snippet → «Omnia project»."""
    cleaned = (name or "").strip()
    if cleaned and cleaned.lower() not in _UNTITLED:
        return cleaned[:80]

    niche = infer_niche_label(prompt or "")
    if niche:
        # «школа / образование» → «Школа» — the leading concept, capitalised.
        head = niche.split("/", 1)[0].strip()
        if head:
            return head[:1].upper() + head[1:]

    snippet = " ".join((prompt or "").split())
    if snippet:
        return snippet[:60].rstrip()
    return _DEFAULT_TITLE


def build_share_card(
    name: str | None, prompt: str | None, accent_hex: str | None
) -> ShareCard:
    """Derive the deterministic share card for one project build."""
    return ShareCard(
        title=_derive_title(name, prompt),
        tagline=infer_niche_label(prompt or ""),
        accent=_clean_accent(accent_hex),
        motion=_derive_motion(prompt),
    )


def inject_share_module(files: dict[str, str], card: ShareCard) -> dict[str, str]:
    """Return ``files`` with the generated ``omnia-share.ts`` added.

    The payload is JSON-encoded (JSON is a subset of TS object syntax) so a
    title with quotes / Cyrillic / backslashes can never break the literal.
    Side-effect-free: the input dict is not mutated.
    """
    payload = json.dumps(asdict(card), ensure_ascii=False, indent=2)
    module = (
        "// Auto-generated by Omnia — per-project share-card brand payload.\n"
        "// Consumed by src/app/layout.tsx (generateMetadata) and\n"
        "// src/app/opengraph-image.tsx. Regenerated on every full build.\n"
        f"export const share = {payload} as const;\n"
    )
    return {**files, MODULE_PATH: module}


__all__ = ["MODULE_PATH", "ShareCard", "build_share_card", "inject_share_module"]
