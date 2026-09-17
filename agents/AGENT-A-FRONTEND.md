# Агент A — Frontend (apps/web/)

> **Статус брифа.** Зона ответственности и особенности компонента. Общие правила — Git-процесс через PR,
> проверки, безопасность данных, выпуск и критерии готовности — в [`AGENTS.md`](../AGENTS.md).
> Стек и структура ниже — исходный замысел MVP (май 2026): перед опорой на них сверяй с кодом.

При работе в зоне читай `docs/01-api-contract.md` (контракт) и `docs/03-design-system.md` (дизайн-токены).

## Кто ты в этой команде

Ты пишешь **весь пользовательский интерфейс Omnia.AI** — лендинг, страницы auth, dashboard проектов и workspace (3-колоночный редактор: чат / preview / timeline).

Параллельно работают:
- **Агент B** (backend) — отдаёт REST API на `:8000` и WebSocket. Ты потребитель.
- **Агент C** (LLM Gateway) — для тебя невидим, ходит через B.

## Зона ответственности

- Ответственность — `apps/web/`. Правка других зон — по правилам раздела 1 `AGENTS.md` (нужна задаче, указана в PR, согласована).
- Контракт API (`docs/01-api-contract.md`, типы в `src/lib/api/types.ts`) меняется в том же PR, что реализация и тесты, с согласия ответственного за backend (агент B). Пока изменение не согласовано — работай с заглушкой.

## Стек

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

## История: исходный план фаз

План M0–M3 времён MVP — исторический; отметки и «Definition of Done» оттуда не описывают текущее
состояние и не обязательны к чтению при старте. Полный текст — в Git: `git show d5088222:agents/AGENT-A-FRONTEND.md`.

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

## Проверки

Обязательный набор — как в CI (раздел 6 `AGENTS.md`): `pnpm install --frozen-lockfile`, затем `pnpm typecheck && pnpm test && pnpm build`; `pnpm lint` — дополнительно. Перед PR — review diff по разделу 6 `AGENTS.md`.

## Что НЕ делать

- Не подключать UI-библиотеки помимо shadcn (никаких MUI, Chakra, Mantine).
- Не писать свой стейт-менеджер вокруг REST — это работа TanStack Query.
- Не писать кастомные кнопки/инпуты — расширяй shadcn через variants.
- Не использовать `getServerSideProps` (это Pages Router, у нас App Router).
- Не вставлять `<img>` в JSX — только `next/image` или blob URLs из API (для preview iframe).

## Координация

- Если backend ещё не готов — заглушки в `lib/api/client.ts` через `MSW` или просто `Promise.resolve(mock)` под `if (process.env.NEXT_PUBLIC_USE_MOCKS === 'true')`.
- Если непонятно поведение API — открой `docs/01-api-contract.md`. Если там не описано — задай вопрос ответственному за backend (в PR или задаче) и продолжай с заглушкой.
