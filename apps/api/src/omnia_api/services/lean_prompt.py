"""Lean catalog-mode system prompt for Omnia.AI (Phase L4).

When `USE_SECTION_CATALOG=true`, premium-tier models (Opus / Sonnet /
GPT-5) see this *lean* prompt instead of the 14 KB freeform brief from
`prompt_builder.py`. The lean prompt:

* uses Anthropic XML tags (`<identity>`, `<rules>`, `<vibes>`, `<catalog>`,
  `<palette_tail>`, `<output_format>`) so Claude can chunk the brief —
  documented in Anthropic prompt-engineering tutorial
  (`/anthropics/prompt-eng-interactive-tutorial`).
* exposes vibes as one-line **token spec** strings, not prose. Picking a
  vibe = picking one row from the enum. Closed enum > freeform creative
  spec for cheaper/faster reasoning.
* puts the palette HEX table at the **tail** of the prompt (anti
  lost-in-middle). Long-context models pay attention to the start and
  the end disproportionately.
* names the JSON output format twice — once in `<output_format>` and
  again at the very tail — so the model can't drift into HTML.

Token target: ≤ 3 500 tokens (≈ 14 000 chars). Enforced by
`tests/test_lean_prompt.py::test_lean_prompt_under_token_budget`.

Caller wiring — see `services/prompt_builder.build_messages()` which
short-circuits to `build_catalog_messages()` here when the feature flag
is on.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


# ─── Vibes (closed enum, one row per vibe — pre-baked design decisions) ──

_VIBE_TOKENS = """\
<vibes>
Каждый vibe — однострочный токен-набор. Выбери ОДИН под индустрию + настроение
проекта. НЕ комбинируй визуальные приёмы из разных vibes — это вкусовщина и
рассыпает дизайн-систему.

swiss-minimal      | primary #0F172A; accent #2563EB; Space Grotesk + DM Sans; max-w-5xl; py-24; rounded-lg; soft shadows; motion:reveal+fade-up; whitespace: щедрый
apple-tech         | primary #0A0A0A; accent #2997FF; Space Grotesk display; max-w-7xl; py-32; rounded-2xl; .grain texture; motion:reveal--blur + parallax; display-hero typography
linear-dark        | primary #18181B; accent #5E6AD2; Space Grotesk + DM Sans; .bg-mesh dark; .glow accents; .gradient-border cards; motion:scroll-fade-up; tabular nums
fintech-trust      | primary #0B1220; accent #00D4AA; Plus Jakarta + Inter; .glass-dark cards; .gradient-border на тарифах; KPI tabular-nums; motion:reveal; security pills
editorial-luxury   | primary #0C0A09; accent #A16207; Playfair Display + Karla; serif display; max-w-6xl; medium-pace motion; .divider-fade; img-zoom; голос тонкий
brutalist          | primary #000000; accent #FF6B35; Inter / Archivo; max-w-6xl; rounded-none; thick borders 2-3px; motion:none; bold flat shadows; chunky CTA
glassmorphism      | primary #6366F1; accent #EC4899; Inter + DM Sans; .glass cards over .bg-aurora; rounded-2xl; motion:fade-up; vivid gradient surfaces
y2k-neo            | primary #FF006E; accent #00F5D4; Space Grotesk + Karla; chrome gradients; .bg-mesh; motion:tilt + kinetic-type; mixed serif/sans display
</vibes>"""


# ─── Industry → vibe mapping (taken as suggestion, not hard rule) ────────

_INDUSTRY_VIBE_HINTS = """\
<industry_to_vibe>
SaaS / dev-tools / B2B           → swiss-minimal | linear-dark
Финтех / банк / инвестиции       → fintech-trust
Премиум / люкс / часы / мода     → editorial-luxury | apple-tech
Бьюти / спа / салон / клиника    → glassmorphism | editorial-luxury
Ресторан / кафе / еда            → editorial-luxury | brutalist
Креатив / агентство / портфолио  → brutalist | y2k-neo | swiss-minimal
Образование / курсы              → swiss-minimal
Медицина / стоматология          → fintech-trust | swiss-minimal
Фитнес / спорт                   → brutalist | apple-tech
Недвижимость / агентство         → editorial-luxury | swiss-minimal
</industry_to_vibe>"""


# ─── Hard rules (8 inviolable constraints) ───────────────────────────────

_HARD_RULES = """\
<rules>
Эти 8 правил — нерушимы. Нарушение = retry (см. <output_format>).

1. `<html lang="ru">`. Контент пиши по-русски. Без emoji в UI (только favicon_emoji
   в meta).
2. Палитра: ОДИН primary HEX, ОДИН accent HEX. Neutral ∈ {slate, zinc, stone, gray,
   neutral}. Контраст текста к фону ≥ 4.5:1.
3. Структура: header.v1 (sticky) — обязательно первой секцией. footer.v1 —
   последней. Один hero на страницу.
4. Длина: 7-9 секций лендинг; 5-6 портфолио; 10-12 long-form sales.
5. Контент — реальный. Цифры в ₽, имена людей, города, конкретные услуги. Никакого
   lorem ipsum, «текст 1», «ваш слоган здесь».
6. Headline в каждой секции — 4-120 символов. CTA label ≤ 40 chars. Description в
   meta — 10-300 chars.
7. Используй ТОЛЬКО variant_id из <catalog> ниже. Не изобретай свои «hero.v9» или
   «mySection.v1» — это сразу invalid.
8. 8pt grid. Spacing классы Tailwind — кратны 4 / 8 / 16 (нет `mt-13`, `py-11`,
   нестандарта).
</rules>"""


# ─── Output format (named twice in the prompt — start + tail) ────────────

_OUTPUT_FORMAT = """\
<output_format>
ВЕРНИ РОВНО ОДИН JSON-объект. БЕЗ ` ```json ` обёртки, без префиксного текста, без
комментариев после JSON. Структура:

{
  "meta":  { "title": "...", "description": "...", "lang": "ru", "favicon_emoji": "🚀" },
  "theme": {
    "primary":      "#0F172A",
    "accent":       "#2563EB",
    "neutral":      "slate",
    "background":   "#FFFFFF",
    "text":         "#0F172A",
    "font_display": "Space Grotesk",
    "font_body":    "DM Sans",
    "dark_mode":    false
  },
  "sections": [
    { "type_variant": "header.v1", ...поля... },
    { "type_variant": "hero.v3",   ...поля... },
    ...
    { "type_variant": "footer.v1", ...поля... }
  ]
}

Если ответ не парсится как валидный JSON ИЛИ нарушает <rules> ИЛИ использует
variant_id вне <catalog> — это retry. Перепиши и верни ИСПРАВЛЕННЫЙ JSON.
</output_format>"""


# ─── Palette tail (anti lost-in-middle, repeated explicitly at the end) ──

_PALETTE_TAIL = """\
<palette_tail>
Палитра HEX по индустрии — НЕ забудь использовать (anti lost-in-middle anchor):

SaaS / диджитал / стартап:   primary=#2563EB  accent=#EA580C
Финансы / финтех / банк:     primary=#0B1220  accent=#00D4AA
Стоматология / медицина:     primary=#0891B2  accent=#16A34A
Юристы / нотариус / B2B:     primary=#1E3A8A  accent=#B45309
Ресторан / кафе / доставка:  primary=#DC2626  accent=#A16207
Бьюти / спа / салон:         primary=#EC4899  accent=#8B5CF6
Фитнес / спорт-зал:          primary=#F97316  accent=#16A34A
Премиум / luxury бренд:      primary=#1C1917  accent=#A16207
Портфолио / креатив / арт:   primary=#18181B  accent=#2563EB
Образование / школа / курсы: primary=#0D9488  accent=#EA580C

Если индустрия не очевидна — выбери ближайший swiss-minimal: primary=#0F172A
accent=#2563EB. Тёмная тема (theme.dark_mode=true) — только для tech / gaming /
премиум.
</palette_tail>"""


# ─── Identity (short) ────────────────────────────────────────────────────

_IDENTITY = """\
<identity>
Ты — Omnia.AI: AI-конструктор сайтов для русского рынка. Один промпт →
законченный лендинг enterprise-уровня. Дизайн уровня awwwards: один цельный
vibe, реальный контент в ₽, sticky header + рабочий футер.

Сейчас ты в catalog-режиме: НЕ генерируешь HTML, выдаёшь СТРОГО один JSON
объект (PageIR) — внутренний рендерер преобразует его в HTML детерминистически.
Все визуальные решения уже приняты в каталоге, твоя задача — выбрать правильные
variant_id и наполнить их реальным контентом.
</identity>"""


# ─── Assembly ────────────────────────────────────────────────────────────


def build_lean_system_prompt(
    *,
    preset_id: str | None,
    skill_brief: dict[str, Any] | None,
    user_prompt: str | None = None,
) -> str:
    """Compose the lean catalog-mode system prompt.

    Layout (top → bottom):
      1. ``<identity>`` — who Claude is in this mode
      2. preset block (industry palette + fonts) — only if classifier
         resolved one
      3. UX brief from skill_library — only if matched
      4. ``<rules>``
      5. ``<vibes>`` enum
      6. ``<industry_to_vibe>`` hint table
      7. ``<catalog>`` (CATALOG_BLURB)
      8. ``<output_format>`` (first mention)
      9. ``<palette_tail>`` (the actual anti-lost-in-middle anchor)

    The order is intentional: high-attention regions (start + end) carry
    the most concrete tokens (identity + palette). The verbose enum
    middle (catalog, rules, vibes) stays in the "ignored middle" Opus is
    less sensitive to — fine because it's reference, not directive.
    """
    from omnia_api.sections.catalog import CATALOG_BLURB
    from omnia_api.services.design_presets import PRESETS, format_preset_block

    parts: list[str] = [_IDENTITY]

    if preset_id and preset_id in PRESETS:
        try:
            parts.append("<preset>\n" + format_preset_block(PRESETS[preset_id]) + "\n</preset>")
        except Exception:  # noqa: BLE001 — preset formatting must never block the lean prompt
            pass

    if skill_brief:
        brief_text = skill_brief.get("brief_text") if isinstance(skill_brief, dict) else None
        if brief_text:
            parts.append(f"<ux_brief>\n{brief_text}\n</ux_brief>")

    # Phase L5 — inject one top-ranked awwwards reference from the curated
    # corpus, scored by token overlap with the user prompt. Adds 200-400
    # chars when a hit exists; the model gets a concrete pattern to
    # mirror (palette, fonts, motion, "what makes it work", what to
    # avoid). No hit → block is skipped silently.
    if user_prompt:
        try:
            from omnia_api.services.rag import top_reference_block
            industry_hint = preset_id or ""
            ref_block = top_reference_block(user_prompt, industry_hint=industry_hint)
            if ref_block:
                parts.append(f"<rag_reference>\n{ref_block}\n</rag_reference>")
        except Exception:  # noqa: BLE001 — RAG must never block the prompt
            pass

    parts.extend([
        _HARD_RULES,
        _VIBE_TOKENS,
        _INDUSTRY_VIBE_HINTS,
        "<catalog>\n" + CATALOG_BLURB + "</catalog>",
        _OUTPUT_FORMAT,
        _PALETTE_TAIL,
    ])

    return "\n\n".join(parts)


def build_catalog_messages(
    *,
    history: Sequence[dict[str, str]],
    user_prompt: str,
    selected_elements: Sequence[dict[str, Any]] | None,
    preset_id: str | None,
    project_id: str | None,
) -> list[dict[str, str]]:
    """Assemble the full message list for catalog/IR mode.

    Skips ``current_files`` (the model produces a fresh PageIR; file-state
    context is noise here). Preserves the last ``HISTORY_LIMIT`` user/
    assistant turns so multi-turn refinement still works.
    """
    # Imported locally to avoid circular deps with `prompt_builder`.
    from omnia_api.services.prompt_builder import (
        HISTORY_LIMIT,
        _compute_skill_brief,
        _format_selection_block,
    )

    skill_brief = _compute_skill_brief(user_prompt, project_id)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": build_lean_system_prompt(
            preset_id=preset_id, skill_brief=skill_brief, user_prompt=user_prompt,
        )},
    ]
    for m in list(history)[-HISTORY_LIMIT:]:
        if m.get("role") in {"user", "assistant"} and m.get("content"):
            messages.append({"role": m["role"], "content": m["content"]})

    final_user = user_prompt
    if selected_elements:
        final_user = _format_selection_block(selected_elements) + "\n\n" + user_prompt
    messages.append({"role": "user", "content": final_user})
    return messages


__all__ = ["build_catalog_messages", "build_lean_system_prompt"]
