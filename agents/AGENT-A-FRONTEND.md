# Агент A — Frontend (apps/web/)

Этот файл — твой единственный self-contained бриф. Прочитай его полностью, затем `docs/01-api-contract.md` (контракт) и `docs/03-design-system.md` (дизайн-токены). После — приступай к M0.

## Кто ты в этой команде

Ты пишешь **весь пользовательский интерфейс Omnia.AI** — лендинг, страницы auth, dashboard проектов и workspace (3-колоночный редактор: чат / preview / timeline).

Параллельно работают:
- **Агент B** (backend) — отдаёт REST API на `:8000` и WebSocket. Ты потребитель.
- **Агент C** (LLM Gateway) — для тебя невидим, ходит через B.

## Жёсткие границы

- **ПИШЕШЬ ТОЛЬКО в `apps/web/`.** Не лезь в `apps/api/`, `apps/llm-gateway/`, `infra/`.
- **ЧИТАЕШЬ:** `CLAUDE.md`, `docs/00..03`, `agents/AGENT-A-FRONTEND.md`. Не читай чужие брифы (их код тебе не нужен).
- Контракт API менять самостоятельно нельзя. Если нужна правка — оставь запись в `~/.claude/coordination/omnia-mvp/inbox/` и продолжай работу с заглушкой.

## Стек (фиксированный)

- **Next.js 15** (App Router, `app/` directory, RSC где можно)
- **React 19** (TypeScript strict)
- **Tailwind CSS v4** + **shadcn/ui** (canary под React 19, `components.json` с `style: "new-york"`)
- **framer-motion** для микроанимаций (hero typewriter, переходы snapshot)
- **TanStack Query v5** для REST (cache, retry, optimistic updates)
- **Zustand** для глобального UI-state (selected snapshot, sidebar collapsed)
- **next-auth v5** (Auth.js) — провайдер `Credentials`, JWT-сессия в httpOnly cookie
- **WebSocket** — нативный API в `lib/ws.ts` с auto-reconnect (exponential backoff)
- **Менеджер пакетов:** `pnpm`. Версия Node — `>=20`.

## Структура `apps/web/`

```
apps/web/
├── package.json
├── tsconfig.json
├── next.config.ts
├── tailwind.config.ts
├── components.json                 (shadcn)
├── postcss.config.mjs
├── .env.local.example
├── public/
│   └── favicon.svg
└── src/
    ├── app/
    │   ├── layout.tsx              (root: html, body, Providers, Toast portal)
    │   ├── globals.css             (Tailwind directives + CSS-переменные из docs/03)
    │   ├── (marketing)/
    │   │   ├── layout.tsx          (publike header без авторизации)
    │   │   ├── page.tsx            (landing)
    │   │   └── pricing/page.tsx    (опционально, если хватит времени)
    │   ├── (auth)/
    │   │   ├── login/page.tsx
    │   │   └── register/page.tsx
    │   ├── (app)/
    │   │   ├── layout.tsx          (auth-guard + TopBar)
    │   │   ├── projects/
    │   │   │   ├── page.tsx        (dashboard со списком)
    │   │   │   └── [id]/page.tsx   (workspace)
    │   │   └── settings/page.tsx   (опционально)
    │   ├── p/[slug]/
    │   │   └── page.tsx            (proxy на API /p/:slug)
    │   └── api/
    │       └── auth/[...nextauth]/route.ts
    ├── components/
    │   ├── ui/                      (shadcn-генерируемые, не редактируй вручную)
    │   ├── marketing/               (Hero, Features, Pricing, FAQ, Footer)
    │   ├── auth/                    (LoginForm, RegisterForm)
    │   ├── workspace/
    │   │   ├── ChatPanel.tsx
    │   │   ├── ChatMessage.tsx
    │   │   ├── PromptInput.tsx
    │   │   ├── PreviewFrame.tsx
    │   │   ├── Timeline.tsx
    │   │   ├── SnapshotCard.tsx
    │   │   └── ModelSelector.tsx
    │   └── shared/                  (TopBar, WalletBadge, etc.)
    ├── lib/
    │   ├── api/
    │   │   ├── client.ts            (fetch wrapper + types)
    │   │   ├── types.ts             (зеркало docs/01-api-contract.md)
    │   │   ├── projects.ts
    │   │   ├── snapshots.ts
    │   │   ├── messages.ts
    │   │   ├── wallet.ts
    │   │   └── auth.ts
    │   ├── ws.ts                    (WebSocket client с reconnect)
    │   ├── auth.ts                  (next-auth config)
    │   └── utils.ts                 (cn, formatRelativeTime, etc.)
    ├── hooks/
    │   ├── useProjectWS.ts          (подписка на /api/ws/projects/:id)
    │   ├── useSnapshots.ts
    │   └── useStreamingMessage.ts
    └── store/
        └── workspace.ts             (Zustand: selectedSnapshotId, sidebars)
```

## Фазы

### M0 — Скаффолд + дизайн-токены (день 1–2)

**Задачи:**
1. `pnpm create next-app@latest .` (App Router, TS, Tailwind, src/, import-alias `@/*`).
2. `pnpm dlx shadcn@canary init` (style: new-york, base color: zinc, CSS variables: yes).
3. Перенести **все** цветовые токены из `docs/03-design-system.md` в `globals.css` как CSS-переменные. Расширить `tailwind.config.ts`, чтобы они были доступны как `bg-surface-base`, `text-fg-primary`, `border-default` и т.д.
4. Подключить шрифты Inter + JetBrains Mono через `next/font` (subsets latin + cyrillic).
5. `app/layout.tsx`: `<html lang="ru" className="dark">`, body с `bg-surface-base text-fg-primary`, `font-sans antialiased`.
6. Создать `lib/api/client.ts` — обёртку над `fetch` с baseURL из `NEXT_PUBLIC_API_URL`, авто-сериализацией JSON, обработкой `ApiError`.
7. Создать `lib/api/types.ts` — скопировать TypeScript-типы из `docs/01-api-contract.md`. Это эталон.
8. Установить компоненты shadcn: `button`, `input`, `label`, `card`, `dialog`, `dropdown-menu`, `tooltip`, `tabs`, `scroll-area`, `avatar`, `skeleton`, `badge`, `toast` (sonner).
9. `Providers.tsx` (TanStack Query, `next-themes` если нужно, Toaster).

**Definition of Done M0:** `pnpm dev` запускает dummy-страницу `/`, видно тёмный фон с правильным шрифтом, lighthouse desktop ≥ 95 (на пустой странице).

### M1 — Лендинг + Auth (день 3–5)

**Лендинг (`app/(marketing)/page.tsx`):**
- **Hero:** бейдж «Beta · Скоро запуск», H1 `text-5xl font-semibold tracking-tight`, подзаголовок `text-fg-secondary`, CTA «Начать бесплатно». Справа — typewriter-промпт, который превращается в SVG-wireframe сайта. Через framer-motion (`AnimatePresence`).
- **Features:** 3 колонки — «Чат → сайт», «Лента версий», «Деплой одной кнопкой». Иконки lucide в `--accent` обводке.
- **Pricing:** 3 карточки. Pro выделен бордером `--accent`.
- **FAQ:** shadcn `Accordion`.
- **Footer:** 4 колонки.

**Auth (`app/(auth)/`):**
- Login: email + password, кнопка «Войти», ссылка на регистрацию. POST → `/api/auth/login`.
- Register: те же поля + подтверждение пароля. POST → `/api/auth/register`.
- next-auth настроен на Credentials провайдер, который под капотом дёргает наш backend `/api/auth/login`. JWT приходит в cookie от backend; next-auth валидирует через JWKS либо просто проверяет наличие cookie.
- После успеха — `redirect('/projects')`.

**Definition of Done M1:** работают login/register/logout, defended page `/projects` редиректит на `/login`, lighthouse landing ≥ 95.

### M2 — Workspace UI (день 6–10)

**Dashboard (`projects/page.tsx`):**
- Список карточек проектов. Кнопка «Новый проект» → модалка с выбором template (`blank`/`landing`/`portfolio`/`blog`) и именем.
- Создание → `POST /api/projects` → редирект на `/projects/:id`.

**Workspace (`projects/[id]/page.tsx`):**
- TopBar: лого, имя проекта (редактируемое), `ModelSelector` (DropdownMenu с моделями из `GET /api/models` + цена), `WalletBadge` с балансом.
- 3 колонки (см. layout в `docs/03`).
- **ChatPanel:** список сообщений (user / assistant), внизу `PromptInput` — textarea с auto-resize, Enter = send, Shift+Enter = новая строка.
- **PreviewFrame:** `<iframe src="/p/:slug" />` (через прокси, чтобы JWT работало). Кнопка «Открыть в новой вкладке».
- **Timeline:** список `SnapshotCard` сверху вниз (новые сверху). Каждая карточка — миниатюра (PNG из `preview_url`), первые 50 символов промпта, relative time, кнопка «Откатить» (только если не текущий).

**Подключения:**
- На `mount` — `GET /api/projects/:id`, `GET /api/projects/:id/snapshots`, `GET /api/projects/:id/messages`.
- Открыть WebSocket через `useProjectWS(projectId)`.

**Definition of Done M2:** можно открыть проект, увидеть текущий preview, увидеть список snapshot'ов, отправить промпт (он появится в чате как user-сообщение), увидеть spinner ожидания ответа.

### M3 — LLM-стрим + rollback + polish (день 11–14)

**Стрим:**
- При отправке промпта: `POST /api/projects/:id/prompt` возвращает `{message_id}`. Сразу создаём в чате assistant-сообщение с `id = message_id` и пустым content.
- Подписка на WS события `llm.chunk` (по message_id) — добавляет дельты в content. `llm.done` — проставляет токены и стоимость в meta. `llm.error` — заменяет content на `[ошибка: ...]` красным.
- Когда приходит `snapshot.created` — добавляем карточку в timeline (preview_url пока null → skeleton). Когда `preview.ready` — обновляем миниатюру.
- `wallet.updated` — обновляем `WalletBadge`.

**Rollback:**
- Клик «Откатить» на snapshot → confirm-dialog → `POST /api/projects/:id/rollback {snapshot_id}` → новый snapshot прилетит через WS, текущий preview обновится.

**Polish:**
- Skeleton screens для всех загрузок.
- Toast'ы на ошибки (rate limit, wallet_empty).
- Keyboard-shortcut `Cmd/Ctrl+Enter` = отправить промпт.
- Empty state в timeline («ещё нет версий — отправь первый промпт»).

**Definition of Done M3:**
- Полный E2E (вручную): регистрация → создание проекта → промпт → стрим → preview → rollback. Без ошибок в консоли.
- Все tab-навигации работают, focus-ring везде виден.
- 0 hardcoded цветов вне токенов (grep по `#[0-9a-fA-F]{3,6}` в `src/` ничего не находит).
- `pnpm typecheck && pnpm lint` зелёные.

## Команды

```bash
cd apps/web
pnpm install
pnpm dev                     # http://localhost:3000
pnpm build && pnpm start
pnpm typecheck
pnpm lint
pnpm test                    # Vitest, если будут unit-тесты
```

## Перед каждым коммитом

`/safe-commit "msg"` — это запустит typecheck + lint + canon-review. После значимого блока (>3 файлов или новая фича) — `/canon-review`.

## Что НЕ делать

- Не подключать UI-библиотеки помимо shadcn (никаких MUI, Chakra, Mantine).
- Не писать свой стейт-менеджер вокруг REST — это работа TanStack Query.
- Не писать кастомные кнопки/инпуты — расширяй shadcn через variants.
- Не использовать `getServerSideProps` (это Pages Router, у нас App Router).
- Не вставлять `<img>` в JSX — только `next/image` или blob URLs из API (для preview iframe).

## Координация

- Если backend ещё не готов — заглушки в `lib/api/client.ts` через `MSW` или просто `Promise.resolve(mock)` под `if (process.env.NEXT_PUBLIC_USE_MOCKS === 'true')`.
- Если непонятно поведение API — открой `docs/01-api-contract.md`. Если там не описано — оставь записку в координации и продолжай с заглушкой.

## Старт

```bash
# В новом чате Claude, в C:\Бизнес план\omnia-mvp\:
# Driver автоматически создаст worktree.
# Первое сообщение — фраза из CLAUDE.md проекта (она же в README ниже).
```
