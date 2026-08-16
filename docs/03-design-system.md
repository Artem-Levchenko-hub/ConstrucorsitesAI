# 03. Design System — Omnia.AI

**Философия:** технологичный минимализм. Dark-first. Linear/Vercel-vibe. Никаких декоративных элементов — каждая тень, бордер и анимация работает на функцию. Контент важнее хрома.

Этот файл — для агента **A**. Агент B/C читают только секцию «токены» (нужны для Playwright-стилизации preview).

## Принципы

1. **Тёмная тема — основа.** Светлая — позже, после MVP.
2. **Один акцент на экран.** Если кнопка primary — она единственная.
3. **Контент держится сеткой 4px.** Все отступы и размеры кратны 4.
4. **Текст ведёт глаз.** Иерархия достигается размером и весом, не цветом.
5. **Анимация — обратная связь, не украшение.** 150–200ms ease-out на hover/focus, не больше.
6. **Skeleton > spinner.** Пустые состояния показывают форму, а не вращающийся круг.
7. **Доступность по умолчанию.** focus-ring всегда виден, контраст ≥ 4.5:1 для текста.

## Цветовые токены

Все цвета — в `apps/web/src/app/globals.css` (`@theme`, Tailwind v4) через CSS-переменные с
префиксом `--color-*`. **Никаких hex-значений в JSX.** Палитра — тёплый charcoal + фиолет/индиго:
дружелюбнее холодного `#000`/iOS-синего, инженерный Apple-system-вайб сохранён.

### Поверхности (фоны)

| Токен | Hex | Назначение |
|---|---|---|
| `--surface-base` | `#0d0d12` | главный фон (тёплый charcoal, не #000) — совпадает с фоном TopBar |
| `--surface-raised` | `#1a1a20` | sidebar, header, card |
| `--surface-overlay` | `#26262e` | модалки, dropdown, popover |
| `--surface-input` | `#1a1a20` | textarea, input |

### Бордеры

| Токен | Hex | Назначение |
|---|---|---|
| `--border-subtle` | `#1F1F1F` | разделители без выделения |
| `--border-default` | `#262626` | дефолтный бордер карточек, инпутов |
| `--border-strong` | `#404040` | hover-state бордеров |
| `--border-focus` | `--accent` | focus-ring |

### Текст

| Токен | Hex | Назначение |
|---|---|---|
| `--fg-primary` | `#FAFAFA` | основной текст, заголовки |
| `--fg-secondary` | `#A1A1AA` | вторичный текст, подписи |
| `--fg-tertiary` | `#71717A` | placeholder, disabled |
| `--fg-on-accent` | `#FFFFFF` | текст на акцентной кнопке |

### Акцент (один на весь продукт)

| Токен | Hex | Назначение |
|---|---|---|
| `--accent` | `#6e5be8` | primary CTA, активные ссылки, focus-ring (фиолет/индиго под лого Omnia; white-on-accent 4.86:1) |
| `--accent-hover` | `#5d4ad0` | hover на primary |
| `--accent-subtle` | `#6e5be8` (alpha 0.14) | мягкий фон для активного состояния |

### Семантика

| Токен | Hex | Когда |
|---|---|---|
| `--success` | `#10B981` | OK, snapshot создан |
| `--warning` | `#F59E0B` | низкий баланс |
| `--danger` | `#EF4444` | ошибка, удаление |
| `--info` | `#06B6D4` | подсказки |

## Типографика

**Шрифты:** [Inter](https://rsms.me/inter/) для UI, [JetBrains Mono](https://www.jetbrains.com/lp/mono/) для кода и числовых значений.

Подключение через `next/font` (subsets: `latin`, `cyrillic`).

### Шкала

| Размер | Tailwind | px / line-height | Где |
|---|---|---|---|
| Display | `text-5xl font-semibold tracking-tight` | 48 / 56 | Hero лендинга |
| H1 | `text-3xl font-semibold tracking-tight` | 30 / 36 | Главный заголовок страницы |
| H2 | `text-2xl font-semibold` | 24 / 32 | Секции |
| H3 | `text-lg font-medium` | 18 / 28 | Карточки, подсекции |
| Body | `text-sm` (default) | 14 / 20 | Основной текст |
| Body-lg | `text-base` | 16 / 24 | Лендинг параграфы |
| Caption | `text-xs text-fg-secondary` | 12 / 16 | Подписи, метаданные |
| Mono | `font-mono text-xs` | 12 / 16 | SHA снапшота, токены |

**Tracking:** заголовки H1/Display всегда `tracking-tight` (-0.02em). Всё остальное — default.

## Сетка и отступы

- **Базовая единица:** 4px.
- **Layout-сетка:** 12 колонок в landing, fluid в workspace.
- **Внутренние отступы карточек:** `p-4` (16px) минимум, `p-6` (24px) для крупных.
- **Между секциями лендинга:** `py-24` (96px).
- **Workspace 3-колонник:** `grid-cols-[320px_1fr_280px]` с `gap-0` (между колонками — бордер).

## Радиусы

| Токен | px | Где |
|---|---|---|
| `rounded-sm` | 4 | бейджи, теги |
| `rounded-md` | 6 | кнопки, инпуты, карточки |
| `rounded-lg` | 8 | модалки, large cards |
| `rounded-full` | — | аватары, переключатели |

## Тени

Минимально. Используем `ring-1` (1px бордер) чаще, чем `box-shadow`. Если нужна тень:

| Токен | Значение | Где |
|---|---|---|
| `shadow-sm` | `0 1px 2px rgb(0 0 0 / 0.4)` | hover на карточке |
| `shadow-md` | `0 4px 12px rgb(0 0 0 / 0.5)` | popover, dropdown |
| `shadow-lg` | `0 16px 48px rgb(0 0 0 / 0.6)` | модалка |

## Анимации

| Свойство | Длительность | Easing |
|---|---|---|
| hover/focus state (фон, бордер) | 150ms | `ease-out` |
| появление popover, tooltip | 180ms | `ease-out` |
| открытие модалки | 220ms | `cubic-bezier(0.16, 1, 0.3, 1)` |
| смена preview iframe | 300ms (fade) | `ease-in-out` |

Используем `framer-motion` для composites (модалки, переходы между snapshot'ами в timeline). Для hover — обычный CSS-transition.

**Запрещено:** spinning loaders в основных flow. Только skeleton screens. Spinner — только для долгих фоновых действий (>3s).

## Компоненты — приоритеты

Берём из `shadcn/ui` (canary под React 19). Конфигурируем через CSS-переменные выше.

### MVP-набор (нужен агенту A в M0–M2)

- `Button` (variants: primary / secondary / ghost / danger; sizes: sm / md / lg)
- `Input`, `Textarea`, `Label`, `FormField` (с inline-валидацией)
- `Card` (с `CardHeader` / `CardContent` / `CardFooter`)
- `Dialog` (модалка)
- `DropdownMenu` (для меню пользователя, селектора моделей)
- `Tooltip`
- `Tabs`
- `ScrollArea` (для чата и timeline)
- `Avatar` (инициалы на фоне `--accent-subtle`)
- `Skeleton`
- `Badge` (для тегов моделей: «Claude», «GPT», «Yandex»)
- `Toast` (для нотификаций — successful save, error)

### Кастомные (пишутся в `apps/web/src/components/`)

- `ChatMessage` — пузырь сообщения (user vs assistant), с метаданными модели и токенов
- `SnapshotCard` — карточка в timeline: миниатюра + промпт + relative time + кнопка rollback
- `PreviewFrame` — iframe с loading-state и кнопкой «открыть в новой вкладке»
- `WalletBadge` — индикатор баланса в header (зелёный → жёлтый → красный)
- `ModelSelector` — DropdownMenu с моделями + ценой за 1k токенов

## Иконки

[Lucide](https://lucide.dev) (поставляется с shadcn). Размеры: `w-4 h-4` в кнопках, `w-5 h-5` в навигации, `w-6 h-6` в hero.

## Лендинг — конкретика стиля

- **Hero:** `text-5xl` H1 в центре, под ним `text-base text-fg-secondary` подзаголовок (1 предложение), CTA кнопка `lg primary`. Над H1 — маленький бейдж типа `Beta · Запуск октябрь 2026`.
- **Демо в hero:** анимированный «typewriter» промпт `Сделай лендинг кофейни в Казани...` → справа появляется wireframe сайта (можно SVG-имитация). Эффект через framer-motion.
- **Фичи:** 3 колонки с иконками `w-6 h-6` в `--accent` обводке-кружке, заголовок H3, описание `body`.
- **Pricing:** 3 карточки (Старт, Про, Команда), у Pro — бордер `--accent` + бейдж «Популярный».
- **FAQ:** аккордеон через shadcn.
- **Footer:** 4 колонки (Продукт / Ресурсы / Юридическое / Контакты) на тёмно-сером фоне `--surface-raised`.

## Workspace — раскладка

```
┌─────────────────────────────────────────────────────────────────┐
│ TopBar  [logo]      [project name]    [model selector] [wallet] │ 56px
├─────────────────────────────────────────────────────────────────┤
│           │                                          │          │
│  CHAT     │           PREVIEW iframe                 │ TIMELINE │
│  320px    │           flex-1                         │  280px   │
│           │                                          │          │
│  scroll   │           (responsive viewport)          │ list of  │
│  area     │                                          │ snapshot │
│           │                                          │ cards    │
│           │                                          │          │
├───────────┤                                          ├──────────┤
│ INPUT box │                                          │          │
│ textarea  │                                          │          │
│ + send    │                                          │          │
│ 96px      │                                          │          │
└───────────┴──────────────────────────────────────────┴──────────┘
```

- Колонки разделены `border-r border-default` (1px). Никаких теней между колонками.
- Sidebar (chat) сворачивается на узких экранах (<1280px) в иконку.
- Timeline сворачивается аналогично.

## Состояния

Каждый интерактивный элемент имеет 5 состояний: **default / hover / active / focus / disabled**. focus-ring всегда видим (2px `--accent`, offset 2px от элемента, на тёмном фоне).

## Что запрещено

- ❌ Градиенты (кроме одного-единственного на hero — и то опционально).
- ❌ Эмодзи в UI (можно только в чате как часть пользовательского контента).
- ❌ Цветные иконки. Только monochrome — текущий цвет текста.
- ❌ Тени-«пушинки» (большие blur > 24px).
- ❌ Animated gradients, glassmorphism, neumorphism.
- ❌ Decorative svg-волны, частицы, parallax-фоны.
