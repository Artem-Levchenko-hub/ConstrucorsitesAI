"""Hot-fork recap — the seed message that lands a remixer in a WARM workspace.

NORTH STAR pillar 4 (viral shareability). ``perform_fork`` already carries the
source's design DNA (``discovery_spec`` + ``design_preset`` + the HEAD snapshot),
but it created **zero** chat rows, so the remixer opened a forked app over a
COLD empty chat that greeted them with the generic "Поговорим о вашем сайте" —
nothing told them what they just remixed or what to try first.

This module builds ONE deterministic, LLM-free assistant message that names the
source, echoes its captured design DNA (niche · theme · accent · tone), and
offers a few one-tap starter edits. It is emitted as a structured ``<remix …>``
tag (same convention as ``<file>`` / ``<edit>`` / ``<app-error>``) so the client
renders it as a rich recap card, not a wall of prose — see
``apps/web/src/lib/parse-assistant.ts`` (mirror) + ``RemixRecapCard.tsx``.

Pure + niche-aware: the same fork always recaps identically (no gateway call),
so it can be unit-pinned and never adds a build cost or a failure surface.
"""

from __future__ import annotations

from typing import Any

from omnia_api.services.chip_pixel_gate import FidelitySpec
from omnia_api.services.discovery import infer_niche_label

# Canonical accent family → short Russian word for the DNA chip. Keyed on the
# family names ``FidelitySpec.primary_family`` carries (see chip_pixel_gate
# ``_FAMILY_BANDS``); an unmapped family just drops out of the chip line.
_FAMILY_RU: dict[str, str] = {
    "red": "красный",
    "orange": "оранжевый",
    "amber": "янтарный",
    "yellow": "жёлтый",
    "green": "зелёный",
    "emerald": "изумрудный",
    "teal": "бирюзовый",
    "cyan": "циан",
    "blue": "синий",
    "indigo": "индиго",
    "violet": "фиолетовый",
    "purple": "пурпурный",
    "magenta": "малиновый",
    "pink": "розовый",
}

# Canonical tone token → short Russian word (see chip_pixel_gate ``_TONE_ALIASES``).
_TONE_RU: dict[str, str] = {
    "premium": "премиум",
    "friendly": "дружелюбный",
    "playful": "игривый",
    "minimal": "минимализм",
    "corporate": "деловой",
}

# Niche label (the same labels discovery's ``_NICHE_LABELS`` emits) → the FIRST
# starter-edit chip, tailored so the suggestion feels native to what was remixed
# (a café gets "меню с фото", a school gets "расписание"). Unmapped niche falls
# back to a universally-safe "add testimonials" nudge.
_NICHE_STARTER: dict[str, str] = {
    "школа / образование": "Добавь расписание и ленту новостей",
    "клиника / медицина": "Добавь онлайн-запись на приём",
    "салон красоты": "Добавь прайс на услуги с длительностью",
    "фитнес / спорт": "Добавь расписание тренировок",
    "кафе / ресторан": "Добавь меню с фотографиями блюд",
    "автосервис": "Добавь прайс на услуги с ценами",
    "недвижимость": "Добавь каталог объектов с фото",
    "туризм / путешествия": "Добавь популярные направления",
    "мероприятия / события": "Добавь программу и спикеров",
    "интернет-магазин": "Добавь карточки популярных товаров",
    "CRM / управление": "Добавь доску сделок с этапами",
    "портфолио": "Добавь галерею работ с превью",
    "блог / медиа": "Добавь ленту последних статей",
}

# A second swap nudge that is always safe on any forked surface — picks an accent
# DIFFERENT from the one already in play so the tap visibly changes something.
_DEFAULT_ACCENT_SWAP = "Сменить акцентный цвет на изумрудный"
_ALT_ACCENT_SWAP = "Сменить акцентный цвет на тёплый янтарный"


def _attr_escape(value: str) -> str:
    """Make a string safe inside a double-quoted tag attribute. Drops the few
    metacharacters that would break the ``parse-assistant`` regex; this is
    cosmetic chrome, never markup the model authored, so a lossy clean is fine.
    """
    return value.replace('"', "'").replace("<", "").replace(">", "").strip()


def _dna_chips(niche: str, spec: FidelitySpec, preset_name: str | None) -> list[str]:
    """The design-DNA chips, most-recognisable first. Empty axes drop out; if
    nothing at all was captured, fall back to the preset name (or a copied-1:1
    note) so the card never shows a bare 'Дизайн-ДНК:' with no chips."""
    chips: list[str] = []
    if niche:
        chips.append(niche)
    if spec.dark_mode is True:
        chips.append("тёмная тема")
    elif spec.dark_mode is False:
        chips.append("светлая тема")
    if spec.primary_family and (fam := _FAMILY_RU.get(spec.primary_family)):
        chips.append(f"{fam} акцент")
    if spec.tone and (tone := _TONE_RU.get(spec.tone)):
        chips.append(tone)
    if not chips:
        chips.append(preset_name or "стиль скопирован 1:1")
    return chips


def _starter_edits(niche: str, spec: FidelitySpec) -> list[str]:
    """Three one-tap starter prompts: a niche-native add, an accent swap, and a
    hero-copy polish. Each is a complete edit instruction the chat can submit
    verbatim — the remixer's first move is a single click, not a blank page."""
    niche_add = _NICHE_STARTER.get(niche, "Добавь раздел с отзывами клиентов")
    accent_swap = (
        _ALT_ACCENT_SWAP if spec.primary_family == "emerald" else _DEFAULT_ACCENT_SWAP
    )
    return [niche_add, accent_swap, "Сделай заголовок на первом экране ярче и короче"]


def build_fork_recap(
    name: str,
    discovery_spec: dict[str, Any] | None,
    design_preset_name: str | None = None,
) -> str:
    """Build the ``<remix>`` seed-message content for a freshly forked project.

    ``name`` — the source app's name (the fork copies it). ``discovery_spec`` —
    the persisted ``projects.discovery_spec`` JSONB (or None). ``design_preset_name``
    — a human preset label used only as a DNA fallback when discovery captured
    nothing. Returns a single tag string; suggestions go in the body (one per
    line) so an un-upgraded client degrades to readable text.
    """
    spec = FidelitySpec.from_dict(discovery_spec)
    niche = infer_niche_label(name)
    dna = " · ".join(_dna_chips(niche, spec, design_preset_name))
    body = "\n".join(_starter_edits(niche, spec))
    clean_name = _attr_escape(name) or "проект"
    return f'<remix name="{clean_name}" dna="{_attr_escape(dna)}">{body}</remix>'


__all__ = ["build_fork_recap"]
