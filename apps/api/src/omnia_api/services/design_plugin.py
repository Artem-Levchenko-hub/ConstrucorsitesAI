"""Bounded design-intelligence plugin for container UI generation.

The plugin is deliberately a pure, local pre-build step.  It turns a product
brief into a compact design contract that the existing coding agent consumes
and the existing ``see`` tool audits.  It does not start another model, add an
acceptance phase, or own completion; the proven single-pass agent loop remains
the only loop.

The shape follows searchable design-intelligence skills: classify first, inject
only matched guidance. It reuses Omnia's vendored, version-pinned UI/UX Pro Max
tables through the narrow ``skill_library`` loader plus Omnia's own presets and
Design DNA. Upstream scripts are never executed and generation adds no network.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from omnia_api.services import skill_library
from omnia_api.services.design_dna import design_mood_directive
from omnia_api.services.design_presets import PRESETS
from omnia_api.services.design_tokens import DesignTokens, tokens_for_project
from omnia_api.services.preset_classifier import classify_preset_sync

PLUGIN_ID = "omnia-design-pro"
PLUGIN_VERSION = "1.1.0"
KNOWLEDGE_SOURCE = "ui-ux-pro-max@2.13.0+8a1a6d85"

_UI_TEMPLATES = frozenset({"fullstack", "nextjs_entities", "spa", "realtime", "max_miniapp"})


@dataclass(frozen=True)
class _ProductPattern:
    id: str
    label: str
    cues: tuple[str, ...]
    architecture: str
    main_flow: str


_PATTERNS = (
    _ProductPattern(
        "fitness-health",
        "здоровье и тренировки",
        (
            "фитнес",
            "трениров",
            "спортсмен",
            "тренер",
            "здоров",
            "fitness",
            "workout",
        ),
        "сводка прогресса → тренировки/планы → разбор → история → профиль",
        "данные тренировки → понятный анализ → следующая рекомендация",
    ),
    _ProductPattern(
        "commerce",
        "каталог и покупки",
        ("магазин", "товар", "каталог", "корзин", "заказ", "shop", "store", "commerce"),
        "каталог → карточка товара → корзина → оформление → заказы",
        "найти товар → сравнить главное → купить без тупика",
    ),
    _ProductPattern(
        "booking-service",
        "запись и услуги",
        ("запис", "бронь", "расписан", "слот", "услуг", "booking", "appointment"),
        "обзор → услуга → время → подтверждение → мои записи",
        "выбрать услугу и свободное время → подтвердить → увидеть результат",
    ),
    _ProductPattern(
        "communication",
        "общение и сообщество",
        ("чат", "сообщен", "мессендж", "сообществ", "канал", "chat", "messenger"),
        "входящие/каналы → диалог → участники/детали → профиль",
        "найти разговор → прочитать контекст → ответить → увидеть доставку",
    ),
    _ProductPattern(
        "learning-content",
        "обучение и контент",
        ("курс", "урок", "обуч", "школ", "контент", "lesson", "learning", "course"),
        "прогресс → программа → урок → практика → результаты",
        "продолжить с текущего места → выполнить шаг → увидеть прогресс",
    ),
    _ProductPattern(
        "operations",
        "операционная работа",
        (
            "crm",
            "задач",
            "заявк",
            "клиент",
            "проект",
            "склад",
            "task",
            "workflow",
        ),
        "рабочая сводка → очереди/объекты → деталь → действие → история",
        "увидеть приоритет → открыть контекст → завершить действие → получить статус",
    ),
    _ProductPattern(
        "analytics",
        "аналитика и мониторинг",
        (
            "аналит",
            "статист",
            "метрик",
            "отчёт",
            "отчет",
            "dashboard",
            "analytics",
        ),
        "ключевые показатели → тренды → детализация → фильтры → экспорт/действие",
        "заметить изменение → понять причину → перейти к действию",
    ),
)

_GENERIC = _ProductPattern(
    "productivity",
    "универсальный рабочий продукт",
    (),
    "главная сводка → основные объекты → деталь → история → профиль",
    "быстро понять состояние → выполнить главное действие → получить обратную связь",
)

_FALLBACK_PRESET = {
    "fitness-health": "wellness-casual",
    "commerce": "retail-product",
    "booking-service": "local-services",
    "communication": "saas-product",
    "learning-content": "education-bright",
    "operations": "saas-product",
    "analytics": "saas-product",
    "productivity": "saas-product",
}

_SKILL_TERMS = {
    "fitness-health": ("fitness", "workout", "healthcare", "mobile", "analytics"),
    "commerce": ("commerce", "e-commerce", "catalog", "checkout", "mobile"),
    "booking-service": ("booking", "appointment", "calendar", "forms", "mobile"),
    "communication": ("chat", "messaging", "community", "realtime", "mobile"),
    "learning-content": ("learning", "course", "progress", "content", "mobile"),
    "operations": ("dashboard", "workflow", "crm", "forms", "productivity"),
    "analytics": ("analytics", "dashboard", "metrics", "charts", "data"),
    "productivity": ("productivity", "dashboard", "forms", "navigation"),
}


@dataclass(frozen=True)
class DesignContract:
    """Versioned output consumed by both author and visual reviewer."""

    plugin_id: str
    version: str
    knowledge_source: str
    archetype: str
    preset_id: str
    prompt_block: str
    vision_context: str
    design_markdown: str


def _max_token_directive(tokens: DesignTokens) -> str:
    """A coherent product palette for MAX without overriding MAX UI internals."""
    palette = tokens.palette
    return f"""
• Палитра продукта «{palette.name}» ({palette.vibe}):
  --app-bg: {palette.bg}; --app-surface: {palette.surface};
  --app-text: {palette.text}; --app-muted: {palette.muted};
  --app-border: {palette.border}; --app-brand: {palette.primary};
  --app-accent: {palette.accent}.
• Типографика продукта: display — {tokens.display_font}; body — {tokens.body_font}.
  Подключение: {tokens.google_fonts_url} (CSS `@import` ставь до Tailwind).
• В `globals.css` объяви эти `--app-*` переменные и используй их для продуктовых
  композиций. Не переопределяй внутренние semantic tokens MAX UI и не добавляй
  `@theme`: контролы уже получают тему от существующего `MaxAppProvider`.
""".strip()


def _render_design_markdown(
    *,
    pattern: _ProductPattern,
    preset_name: str,
    nav_rule: str,
    tokens: DesignTokens | None,
) -> str:
    """Persistent, secret-free design memory shipped inside the generated app."""
    token_lines = ""
    max_ui_lines = ""
    if tokens is not None:
        palette = tokens.palette
        token_lines = f"""
| Token | Value |
|---|---|
| `--app-bg` | `{palette.bg}` |
| `--app-surface` | `{palette.surface}` |
| `--app-text` | `{palette.text}` |
| `--app-muted` | `{palette.muted}` |
| `--app-border` | `{palette.border}` |
| `--app-brand` | `{palette.primary}` |
| `--app-accent` | `{palette.accent}` |
| Display font | `{tokens.display_font}` |
| Body font | `{tokens.body_font}` |
| Font source | `{tokens.google_fonts_url}` |
""".strip()
        max_ui_lines = """
## MAX UI

- Keep the existing `MaxAppProvider` and global MAX UI stylesheet.
- Use public `@maxhub/max-ui@0.2.0` controls for native interactions.
- Use `lucide-react` for missing icons; never emoji or hand-drawn SVG icons.
- Product layout uses the `--app-*` variables above. Do not override MAX UI's
  internal semantic variables.
""".strip()

    return (
        f"""# Product design contract

Managed by `{PLUGIN_ID}` `{PLUGIN_VERSION}` from `{KNOWLEDGE_SOURCE}`.
This file is persistent project memory. Keep it aligned when the product UI changes.

## Product

- Archetype: `{pattern.id}` ({pattern.label})
- Direction: {preset_name}
- Information architecture: {pattern.architecture}
- Main flow: {pattern.main_flow}
- Navigation: {nav_rule}

## Foundations

{token_lines or "Use the project design tokens already present in the application."}

## Quality floor

- One dominant block or action per screen; secondary content is quieter.
- Useful loading, empty, error and success states next to the action.
- Touch targets are at least 44px; focus is visible; icon-only controls have labels.
- At 360–390px: no horizontal or nested scrolling; safe areas remain usable.
- Real Russian copy and data; no dead decorative controls, emoji icons or generic hero.

{max_ui_lines}
""".strip()
        + "\n"
    )


def _pick_pattern(brief: str) -> _ProductPattern:
    low = (brief or "").casefold()
    scored = [(sum(cue in low for cue in pattern.cues), pattern) for pattern in _PATTERNS]
    score, pattern = max(scored, key=lambda item: item[0])
    return pattern if score else _GENERIC


def classify_product_archetype(brief: str) -> str:
    """Return the stable product archetype shared by design and advice layers."""
    return _pick_pattern(brief).id


def _stable_seed(project_id: str) -> int:
    digest = hashlib.sha256(f"design-plugin:{project_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _matched_skill_brief(
    *, project_id: str, pattern: _ProductPattern, mobile: bool, brief: str
) -> str:
    """Select only app-relevant rows from the vendored UI/UX Pro Max data.

    Landing structures and broad style references are intentionally excluded:
    those are useful for marketing sites but can turn a working product into a
    hero page or introduce conflicting palettes. Product architecture above plus
    Design DNA own those decisions; the skill supplies UX, icon and chart facts.
    """
    terms = _SKILL_TERMS[pattern.id]
    guidelines = skill_library.lookup_filtered_ux_guidelines(
        *terms,
        severity="High",
        limit=5,
        seed=_stable_seed(project_id),
    )
    low = brief.casefold()
    has_charts = pattern.id == "analytics" or any(
        cue in low for cue in ("аналит", "статист", "метрик", "график", "chart", "dashboard")
    )
    chart_types = (
        skill_library.lookup_chart_types(
            *terms,
            "progress",
            "trend",
            "performance",
            "comparison",
            limit=2,
        )
        if has_charts
        else ()
    )
    nav_style = skill_library.auto_nav_style(
        "mobile" if mobile else "desktop",
        tier="primary",
    )
    brief_block = skill_library.format_design_brief(
        guidelines=guidelines,
        nav_style=f"{nav_style} ({'mobile' if mobile else 'desktop'} primary)",
    )
    # Every UI template already ships lucide-react. Upstream icon/chart rows may
    # recommend Phosphor/D3/Plotly, which are not installed and MAX deliberately
    # blocks shell mutation. Preserve the matched design facts while binding the
    # implementation to dependencies the generated app can actually build with.
    app_safe = [
        "APP-SAFE IMPLEMENTATION (matched UI/UX Pro Max guidance):",
        "  icons: lucide-react only; one stroke style; never emoji or hand-drawn SVG",
    ]
    if chart_types:
        app_safe.append("  CHARTS (use only when the data shape matches):")
        app_safe.extend(
            f"    • {chart['data_type']} → {chart['best_chart']}; {chart['when_to_use'][:120]}"
            for chart in chart_types
        )
        app_safe.append(
            "    implement with semantic HTML/CSS or small inline SVG; "
            "do not import an uninstalled chart package"
        )
    return brief_block + "\n\n" + "\n".join(app_safe)


def _pick_preset(*, project_name: str, template: str, brief: str, preset_id: str | None) -> str:
    if preset_id in PRESETS:
        return str(preset_id)
    inferred = classify_preset_sync(project_name, template, brief)
    if inferred in PRESETS:
        return inferred
    return _FALLBACK_PRESET[_pick_pattern(brief).id]


def build_design_contract(
    *,
    project_id: str,
    project_name: str,
    template: str,
    brief: str,
    preset_id: str | None = None,
) -> DesignContract | None:
    """Build one compact contract for a UI container; non-UI stacks opt out."""
    if template not in _UI_TEMPLATES:
        return None

    pattern = _pick_pattern(brief)
    selected_preset_id = _pick_preset(
        project_name=project_name,
        template=template,
        brief=brief,
        preset_id=preset_id,
    )
    preset = PRESETS[selected_preset_id]
    mobile = template == "max_miniapp"
    nav_rule = (
        "mobile-first: 3–5 нижних вкладок, safe-area, главный CTA достижим большим пальцем"
        if mobile
        else "адаптивная навигация: desktop sidebar/header, на 390px без горизонтального скролла"
    )
    matched_skill_brief = _matched_skill_brief(
        project_id=project_id,
        pattern=pattern,
        mobile=mobile,
        brief=brief,
    )
    tokens = tokens_for_project(project_id, industry_hint=brief) if mobile else None
    mood = (
        _max_token_directive(tokens)
        if tokens is not None
        else design_mood_directive(project_id, industry_hint=brief)
    )
    design_markdown = _render_design_markdown(
        pattern=pattern,
        preset_name=preset.name,
        nav_rule=nav_rule,
        tokens=tokens,
    )
    visual_direction = (
        f"{preset.name}. Это направление, а не второй набор токенов: точная палитра "
        "ниже и постоянный `DESIGN.md` имеют приоритет."
        if mobile
        else f"{preset.name} — {preset.one_liner}"
    )
    prompt_block = f"""

OMNIA DESIGN PRO · {PLUGIN_VERSION} — обязательный дизайн-контракт первого билда.
Это часть текущего единого прохода: сразу реализуй контракт в продукте; не создавай
отдельный дизайн-этап, отчёт, макет или дополнительный цикл.
Источник UX-знаний: `{KNOWLEDGE_SOURCE}` (локальный snapshot; правила уже отобраны).

• Тип продукта: {pattern.label} (`{pattern.id}`).
• Информационная архитектура: {pattern.architecture}.
• Главный путь: {pattern.main_flow}.
• Навигация: {nav_rule}.
• Визуальное направление: {visual_direction}
• Иерархия: один доминирующий блок/действие на экран; не превращай всё в
  одинаковые карточки; вторичное тише, но читаемо.
• Состояния: loading, empty, error, success должны быть полезными и находиться
  рядом с действием. Ошибка объясняет, что сделать дальше.
• Управление: touch target ≥44px; видимый focus; текстовые подписи для неочевидных
  иконок; реальные русские данные; никакой декоративной кнопки без действия.
• Mobile QA: 360–390px, без обрезания/вложенного скролла; клавиатура и safe-area
  не закрывают поля/CTA; длинный текст и пустые данные не ломают сетку.
• Анти-generic: без emoji вместо иконок, без радуги градиентов, без hero лендинга
  внутри рабочего приложения, без одинаковой сетки из карточек на каждом экране.
{matched_skill_brief}
{mood}
""".strip()

    # ``vision_audit`` intentionally bounds this to 300 chars. Put measurable
    # design intent first so the single existing visual check evaluates the same
    # contract the author received, rather than only the raw product nouns.
    vision_context = (
        f"{PLUGIN_ID} {PLUGIN_VERSION}; продукт: {pattern.label}; "
        f"IA: {pattern.architecture}; стиль: {preset.name}; {nav_rule}; "
        "проверить иерархию, states, touch targets, overflow. "
        f"Запрос: {brief.strip()}"
    )[:300]

    return DesignContract(
        plugin_id=PLUGIN_ID,
        version=PLUGIN_VERSION,
        knowledge_source=KNOWLEDGE_SOURCE,
        archetype=pattern.id,
        preset_id=selected_preset_id,
        prompt_block=prompt_block,
        vision_context=vision_context,
        design_markdown=design_markdown,
    )


def seed_design_memory(files: dict[str, str], contract: DesignContract | None) -> dict[str, str]:
    """Attach persistent design memory to an initial seed without another pass."""
    if contract is None:
        return files
    return {**files, "DESIGN.md": contract.design_markdown}


__all__ = [
    "KNOWLEDGE_SOURCE",
    "PLUGIN_ID",
    "PLUGIN_VERSION",
    "DesignContract",
    "build_design_contract",
    "classify_product_archetype",
    "seed_design_memory",
]
