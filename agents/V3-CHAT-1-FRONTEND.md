# Chat-1 — Frontend (V3 multi-stack pivot)

> **Self-contained.** Прочитай этот файл целиком, потом `docs/10-v3-multistack-pivot.md` (главная спека), потом `docs/01-api-contract.md` (V3 section — новые endpoints + типы). Старый `agents/AGENT-A-FRONTEND.md` — справочник по стеку/конвенциям, читай если нужно вспомнить тулчейн.

## Кто ты

Owner UX слоя Omnia.AI V3. Делаешь всё что юзер видит для нового онбординга, выбора стека, выбора пресета, подключения GitHub-репо и одно-кликового деплоя.

Параллельно работают:
- **Chat-2** (Backend) — отдаёт REST API + WebSocket. Ты потребитель.
- **Chat-3** (LLM Gateway + Orchestrator + Infra) — для тебя невидим, ходит через Chat-2.

## Жёсткие границы

- **Write только в `apps/web/`.** Никаких правок в `apps/api/`, `apps/llm-gateway/`, `apps/orchestrator/`, `infra/`, `apps/landing/`.
- **Read-only:** `docs/`, `agents/`, `apps/api/src/omnia_api/schemas/` (Pydantic — оттуда зеркалишь TypeScript-типы).
- **Контракт** — `docs/01-api-contract.md` V3 секция. Если нужна правка контракта — inbox-сообщение в Chat-2 + продолжай с моком (`NEXT_PUBLIC_USE_MOCKS=true` или `apps/web/src/lib/ws-mock.ts`).

## Стек (без изменений vs AGENT-A)

Next.js 15 (App Router) + React 19 + TypeScript strict + Tailwind v4 + shadcn/ui + framer-motion + TanStack Query 5 + Zustand + next-auth v5 + нативный WebSocket с reconnect (см. `lib/ws.ts`).

## Что добавить (новые файлы и роуты)

### Роуты (`apps/web/src/app/(app)/projects/`)

```
projects/
├── new/
│   ├── page.tsx                 # Step 1: новый vs connect-repo, ввод brief'а
│   ├── connect-repo/
│   │   └── page.tsx             # OAuth init redirect + repo-picker (после callback)
│   ├── chat/
│   │   └── [sessionId]/page.tsx # IdeologicalChat (Q-A loop)
│   ├── stack/
│   │   └── [sessionId]/page.tsx # StackPicker (top-3 + override из каталога)
│   └── preset/
│       └── [sessionId]/page.tsx # PresetCarousel (preview + override)
└── [id]/
    └── page.tsx                  # существующий workspace; добавить V3 DeployButton + RepoBadge
```

### Компоненты (`apps/web/src/components/onboarding/`)

| Компонент | Назначение |
|---|---|
| `OnboardingShell.tsx` | Wrapper layout с progress-баром (Step 1/4), navigation |
| `BriefInput.tsx` | Step 1: textarea + кнопки "Создать новый" / "Подключить GitHub" |
| `RepoConnectButton.tsx` | Запускает `/api/auth/github/init?redirect=/projects/new/connect-repo` |
| `RepoPicker.tsx` | Получает `/api/repos/list`, показывает выбор + branch field |
| `IdeologicalChat.tsx` | Чат-пузыри: системные вопросы + user-ответы, кнопка "Пропустить" |
| `StackPicker.tsx` | 3 карточки рекомендаций (StackRecommendation) + ссылка "выбрать другой" → модалка с полным каталогом `GET /api/stacks` |
| `StackCatalogModal.tsx` | Grid карточек всего каталога с фильтром по priority |
| `PresetCarousel.tsx` | Slider с preview-HTML (через iframe sandbox) + кнопка "Авто" |
| `PresetCard.tsx` | Карточка пресета: palette swatches + font sample + 1 паттерн |

### Workspace правки (`apps/web/src/components/workspace/`)

| Файл | Что менять |
|---|---|
| `TopBar.tsx` | Добавить `StackBadge` (показывает текущий `project.stack_id`) и `RepoBadge` (если `project.linked_repo_id`) рядом с `ModelSelector` |
| `DeployButton.tsx` (новый) | Заменить текущую V2-логику: dropdown с двумя опциями — "Деплой в Omnia" (native) и "Push в GitHub" (только если linked); WS-listener на `deploy.linked.progress` |

### API client (`apps/web/src/lib/api/`)

Новые модули:
```
onboarding.ts    # startOnboarding, answerQuestion, confirmStack, confirmPreset, getSession
stacks.ts        # listStacks, recommendStack
presets.ts       # listPresets, getPresetPreview
repos.ts         # initOAuth (redirect), listMyRepos, connectRepo, getProjectRepo, disconnectRepo
deployLink.ts    # triggerDeployLink, getDeployLinkStatus
```

Все используют `lib/api/client.ts` базовую обёртку. Все типы — зеркало `docs/01-api-contract.md` V3-секции, копировать в `apps/web/src/lib/api/types.ts` (расширить, не переписывать).

### Zustand stores (`apps/web/src/store/`)

| Store | Поля |
|---|---|
| `onboarding.ts` (новый) | `currentSession: OnboardingSession \| null`, `selectedStack: StackRecommendation \| null`, `selectedPreset: UiKitEntry \| null`, actions |
| `workspace.ts` (расширить) | + `deployTarget: 'native' \| 'linked'` |

### WebSocket события (`apps/web/src/hooks/useOnboardingWS.ts`, новый)

Подписка на онбординг-канал (либо переиспользовать `/api/ws/projects/:id` после complete; в onboarding state до создания проекта — пока polling через `GET /api/projects/onboarding/:sid`, можно перейти на WS позже).

`useProjectWS.ts` — добавить handlers для `deploy.linked.progress` и `repo.connected`.

## Фазы (W1-W4)

### W1 — Mock onboarding flow + skeleton

- [ ] Скаффолд роутов `projects/new/{,connect-repo,chat/[sid],stack/[sid],preset/[sid]}`
- [ ] `OnboardingShell` с прогресс-баром
- [ ] `BriefInput`, mock state-machine (без backend)
- [ ] `lib/api/onboarding.ts` с реальными типами + mock implementation за флагом `NEXT_PUBLIC_USE_MOCKS=true`
- [ ] Mock JSON fixtures для `OnboardingSession`, `StackRecommendation`, `UiKitEntry`

**DoD W1:** клик "Создать новый" → ввод brief → переход через 4 экрана с mock-данными до `/projects/[id]`, который пока 404. Lighthouse mobile ≥ 85 на onboarding-роутах.

### W2 — IdeologicalChat + StackPicker + PresetCarousel

- [ ] `IdeologicalChat` с реальным `POST /api/projects/onboarding/:sid/answer` (от Chat-2 — координируйся через inbox)
- [ ] `StackPicker` с `POST /api/projects/stack/recommend` + `GET /api/stacks` модалкой
- [ ] `PresetCarousel` с `GET /api/presets` + `GET /api/presets/:slug/preview`
- [ ] Сохранение state в Zustand между шагами, восстановление через `GET /api/projects/onboarding/:sid`
- [ ] Анимации переходов между шагами (framer-motion AnimatePresence)

**DoD W2:** полный happy path онбординга работает на mock backend → выбранный стек/пресет сохраняются → редирект на `/projects/[id]`.

### W3 — RepoConnect + DeployButton

- [ ] `RepoConnectButton` → `/api/auth/github/init` (Chat-2 заглушка `GITHUB_OAUTH_MOCK=true` сначала)
- [ ] Обработка callback-redirect с `?repo_connect=<id>` → `RepoPicker`
- [ ] `RepoPicker` с `GET /api/repos/list` + выбор branch (default `omnia/deploy`)
- [ ] `POST /api/projects/connect-repo` → переход дальше по онбордингу
- [ ] `DeployButton` в workspace с dropdown "Native / GitHub"
- [ ] WS handler для `deploy.linked.progress` + toast
- [ ] Warning-modal при first deploy в существующий репо ("Существующий код в `omnia/deploy` branch будет перезаписан")

**DoD W3:** юзер с подключённым GitHub-репо может пройти онбординг → создать проект → нажать "Push в GitHub" → увидеть commit URL в toast.

### W4 — Polish, mobile, E2E

- [ ] Mobile layout онбординга (один шаг = один экран на ≤640px)
- [ ] Cost prediction в UI (показывать `estimated_setup_cost_rub` после онбординга)
- [ ] Skeleton screens для всех загрузок
- [ ] Empty states + error boundaries
- [ ] Toast'ы на все error codes из ApiError enum (включая V3 коды)
- [ ] E2E (Playwright) сценарий: register → onboarding → connect-repo → workspace → deploy-link

**DoD W4:** все 3 потока зелёные. `pnpm typecheck && pnpm lint && pnpm test:e2e` без ошибок.

## Команды (без изменений)

```bash
cd apps/web
pnpm install
pnpm dev                              # :3000
pnpm typecheck && pnpm lint
pnpm test                             # Vitest
NEXT_PUBLIC_USE_MOCKS=true pnpm dev  # mock backend mode
```

## Координация

**Перед стартом:**
1. Прочитать `~/.claude/coordination/omnia-mvp/BOARD.md` (твоя строка `Chat-1`).
2. Прочитать новые `inbox/*.md` (последние от Chat-2/Chat-3).
3. Обновить свою строку в BOARD: что делаешь, какие файлы трогаешь.

**Когда нужно от Chat-2:**
- Файл inbox: `~/.claude/coordination/omnia-mvp/inbox/YYYY-MM-DD-from-CHAT-1-to-CHAT-2-<topic>.md`
- Содержимое: один абзац — что нужно, к какому сроку (или "когда сможешь"), и что я делаю пока через мок.
- НЕ блокироваться: продолжать с `NEXT_PUBLIC_USE_MOCKS=true`.

**Перед коммитом:**
- `/safe-commit "feat(web/v3): <что>"` — запустит typecheck + lint + canon-review.
- После значимого блока — `/canon-review` для diff'а.

## Старт

```powershell
cd "C:\Бизнес план\omnia-mvp\apps\web"
claude
# Первый промпт:
# "Прочитай agents/V3-CHAT-1-FRONTEND.md, docs/10-v3-multistack-pivot.md, docs/01-api-contract.md.
#  Активируй code-canon. Начинай с W1: скаффолд роутов projects/new/* и mock onboarding flow."
```
