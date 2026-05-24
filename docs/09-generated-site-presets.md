# 09 — Generated-Site Design Presets (Awwwards v3)

> **Single source of truth для всех 4 агентов.** Если правишь генератор —
> сначала читай этот файл, потом `04-generation-rules.md`, потом
> `05-design-references.md`.
>
> Оперативные данные пресетов (HEX, шрифты, классы) живут в коде:
> `apps/api/src/omnia_api/services/design_presets.py`. Этот документ —
> человекочитаемое зеркало. Держите синхронно.

## Зачем

Клиент должен за вечер собирать сайт уровня Awwwards/Godly, а не generic-AI
лендинг. Раньше первый промпт давал «скудный дизайн» (см. project memory
`omnia_output_quality_bar.md`): мёртвые кнопки, плоская графика, шаблонные
секции. Корневая причина — модель сочиняла дизайн с нуля. Лечение:
**declarative блоки** с готовыми токенами (палитра + шрифты + kit-классы +
copy-tone), которые дешёвая модель (Claude Haiku 4.5) копирует, а не творит.

## Как работает auto-classifier

Пользователь НЕ выбирает пресет вручную. Backend смотрит на
`(project_name, template, first_prompt)`:

1. **Heuristic pass** (`preset_classifier.classify_preset_sync`) — двуязычный
   keyword-match (ru/en) с **stem-prefix matching** (первые 5 символов —
   ловит русские падежи: «страховки» ⇄ «страховка»). Если есть однозначный
   лидер (≥1 match, опережает второго на ≥1) — возвращает `preset_id`.
2. **LLM fallback** (`preset_classifier.classify_preset`) — если эвристика
   амбивалентна. Один вызов Haiku-4.5 с закрытым списком 8 опций.
   ~150 input + 5 output токенов ≤ **₽0.05/проект**.
3. **Default** — `editorial-trust` если LLM вернул мусор.

Результат кешируется в `projects.design_preset_id` (migration `0007`).
Повторно не дёргаем.

### Точки вызова

| Когда | Где | Метод |
|---|---|---|
| Создание проекта (информативное имя ≥ 6 символов, не «Untitled») | `routers/projects.py:create_project` | `classify_preset_sync` (heuristic only, без LLM) |
| Первый промпт проекта (если `design_preset_id` всё ещё null) | `routers/messages.py:_process_prompt` | `classify_preset` (heuristic → Haiku fallback) |

## Каталог 8 пресетов

Detailed данные (палитра, шрифты, layout signatures, kit-классы,
copy-tone, anti-patterns) — в `services/design_presets.py:PRESETS`.

### 1. `editorial-trust`
**Когда**: B2B-услуги, консалтинг, юр-финфирмы, коммерческая недвижимость,
корпоративные advisory.
**Reference**: <https://www.carterco.us/>
**Палитра**: чисто ч/б (#FFFFFF / #0A0A0A / #6B7280)
**Шрифт**: Inter Display + Inter
**Сигнатура**: section numerals «0.1 / 0.2 / 0.3», BW client logos, headshot
основателя, личная нота в hero («with a Heart»).

### 2. `studio-showreel`
**Когда**: креативные студии, CGI/3D/motion портфолио, анимация, продакшн.
**Reference**: <https://www.studiosentempo.com/>
**Палитра**: high-contrast B&W
**Шрифт**: Space Grotesk + DM Sans
**Сигнатура**: asymmetric mosaic-grid, [PLAY REEL] кнопка, casual footer
(«mail us, don't be shy»).

### 3. `saas-product`
**Когда**: B2B SaaS, fintech, insurtech, dev-tools, API-продукты.
**Reference**: <https://www.evy.eu/>
**Палитра**: light + один acid emerald `#10B981`
**Шрифт**: Plus Jakarta Sans + Inter
**Сигнатура**: real UI-моки в hero (€800/IBAN/даты — не Lorem!),
persona-testimonials с реальными именами CEO.

### 4. `scandi-editorial`
**Когда**: арт-директора, фотографы, кураторы, минималистичные портфолио,
редакционные personal sites.
**Reference**: <https://staffansundstrom.com/>
**Палитра**: off-white #F7F5F1 + #1A1A1A, без акцентов
**Шрифт**: Newsreader + Inter
**Сигнатура**: single-column вертикальный каталог, воздушный tracking,
team-credits «Photography by X, styling by Y».

### 5. `festival-brutalist`
**Когда**: фестивали, лейблы, электронная музыка, цифровое искусство,
выставки, клубные программы.
**Reference**: <https://sonar.es/> (substitute for elektramontreal.ca — HTTP 522)
**Палитра**: dark #0A0A0A + неон cyan/magenta/lime `#00FFB2`
**Шрифт**: Unbounded + JetBrains Mono
**Сигнатура**: `.kinetic-marquee` бесконечная лента, gridless flowing,
тайминги в monospace «18:00 · 12.06 · MAIN STAGE».

### 6. `wellness-casual`
**Когда**: mobile-apps (фитнес, медитация, питание, привычки), B2C wellness,
lifestyle-стартапы.
**Reference**: <https://stryds.com/>
**Палитра**: light #FAFAF7 + wellness-green `#16A34A`
**Шрифт**: Bricolage Grotesque + DM Sans
**Сигнатура**: провокативный problem/solution tagline, `.cursor-blob`
(custom cursor через `<body data-cursor="blob">`), emoji в feedback-копи
(🥳, 🙌, 💚).

### 7. `boutique-reel`
**Когда**: VFX-студии, видео-продакшн, motion-агентства, post-production,
коммерческая видеография.
**Reference**: <https://oblio.io/>
**Палитра**: dark #0E0E0E / mono
**Шрифт**: Archivo + Inter
**Сигнатура**: full-bleed showreel autoplay в hero, капс-заголовки
monumentality, двухколонник `services | portfolio`, tangible contact
(телефон + физический адрес).

### 8. `editorial-publication`
**Когда**: журналы, культурные проекты, редакции, литературные издания,
art publications.
**Reference**: <https://bureauborsche.com/> (substitute for 3oo.store — placeholder)
**Палитра**: warm off-white #F4F1EC + #1A1A1A + красный акцент `#B91C1C`
**Шрифт**: Fraunces (serif display) + Newsreader
**Сигнатура**: `.justified-prose` длинные абзацы, `.film-grain` фактура,
footnote-style eyebrows «¹ Issue 12 · Spring 2026», табличная typeset
оглавления.

## AWWWARDS_PRINCIPLES — floor качества

Инжектится в system prompt **всегда**, даже без выбранного preset. 8 сквозных
правил (полный текст — `design_presets.py:AWWWARDS_PRINCIPLES`):

1. **Human-tone hero** — personal hook или провокация, НЕ «Innovative Solutions».
2. **Type-as-hero by default** — без stock-фото в шапке.
3. **Editorial whitespace** — py-24…py-32, max-w-3xl/4xl/7xl, никаких py-8.
4. **Asymmetric grid > symmetric** — чередовать колонки, mosaic.
5. **Real proof, not stock** — конкретные числа/имена/города.
6. **Section signature** — numerals / monogram / eyebrow / caps / footnote / none. Один тип на сайт.
7. **One characteristic motion** — cursor ИЛИ marquee ИЛИ kinetic-type. Не всё сразу.
8. **One accent, never rainbow** — 0 или 1 hue-accent.

## CSS-кит v3.0 — новые классы

В `templates/{blank,blog,landing,portfolio}/assets/omnia-kit.css`
(идентичные копии, синхронизированы по hash):

| Класс | Назначение | Использует пресет |
|---|---|---|
| `.section-numeral` (+ `.section-numerals` на родителе) | Auto-counter «0.1 / 0.2 / 0.3» через CSS counter, JS не нужен | `editorial-trust` |
| `.kinetic-marquee` + `.kinetic-marquee-track` | Бесшовная горизонтальная лента; JS дублирует children ×2 | `festival-brutalist` |
| `.cursor-blob-el` (создаётся kit.js) | Custom cursor blob; активация через `<body data-cursor="blob">` | `wellness-casual` |
| `.justified-prose` | Книжная вёрстка с переносами для длинных абзацев | `editorial-publication` |
| `.film-grain` | Редакционная зернистая фактура (SVG-noise blend-mode) | `editorial-publication`, `festival-brutalist` |

## API контракт

В `Project` ресурсе добавлены 2 optional поля:

```json
{
  "design_preset_id": "saas-product",
  "design_preset_name": "SaaS Product"
}
```

`design_preset_name` — computed на бэке из `id` через
`schemas/project.py:ProjectPublic.design_preset_name`. Frontend (Agent A)
показывает read-only badge `🎨 {design_preset_name}` рядом с ModelSelector
в TopBar. **Без UI смены пресета** — это auto-selected.

## Где править

| Изменение | Файл |
|---|---|
| Новый пресет / правка существующего | `apps/api/src/omnia_api/services/design_presets.py` |
| Логика классификатора | `apps/api/src/omnia_api/services/preset_classifier.py` |
| Новый kit-класс | `apps/api/src/omnia_api/templates/*/assets/omnia-kit.{css,js}` (×4, держать идентичными) |
| Инжект пресета в prompt | `apps/api/src/omnia_api/services/prompt_builder.py:build_system_prompt` |
| API-схема | `apps/api/src/omnia_api/schemas/project.py` |
| Frontend badge | `apps/web/src/components/workspace/TopBar.tsx`, `apps/web/src/lib/api/types.ts` |
| Документация | этот файл + `04-generation-rules.md` + `05-design-references.md` |

## Тестирование

```bash
cd apps/api && PYTHONPATH=src .venv/Scripts/python -c "
from omnia_api.services.preset_classifier import classify_preset_sync
print(classify_preset_sync('SaaS для страховки велосипедов', 'landing', None))
# expected: saas-product
"
```

8 эталонных кейсов с heuristic-accuracy 8/8 — см. unit-test
`apps/api/tests/services/test_preset_classifier.py` (отдельный тикет).

## История

- **2026-05-24** — v3.0 launch: 8 Awwwards-tier пресетов + auto-classifier.
  План: `~/.claude/plans/ethereal-herding-feather.md`.
  Предыдущие версии (v2.0–v2.4) — пресеты в `_STYLE_KIT` (`prompt_builder.py`),
  без auto-classifier, без структурированных declarative-блоков.
