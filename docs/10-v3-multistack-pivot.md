# 10. V3 — Multi-stack pivot + onboarding + wow-эффект

> **Статус:** spec фиксирована 2026-05-25. Single source of truth для Chat-1/2/3.
> Параллельная разработка в 3 чатах по эксклюзивным зонам — `agents/V3-CHAT-{1,2,3}-*.md`.
> Координация — `~/.claude/coordination/omnia-mvp/{BOARD.md,inbox/}`.

## Что меняется в V3 (vs V1/V2)

| Аспект | V1 (static) | V2 Phase A (fullstack) | **V3 (этот спек)** |
|---|---|---|---|
| Что юзер генерирует | статичный сайт | fullstack-приложение в Next.js | приложение в **одном из 9+ стеков**, LLM выбирает сам |
| Старт работы | сразу промпт | сразу промпт | **онбординг**: new vs connect-repo → ideological-Q → recommend stack+preset → промпт |
| Wow-эффект первого экрана | system prompt + AWWWARDS_PRINCIPLES | + auto-classifier пресета (Haiku) | + **frozen ui-ux-pro-max каталог в БД** (палитры/font-pairs/паттерны/доки фреймворков) → preset_id выбирает из него, prompt-builder инжектит конкретные токены |
| Repo юзера | свой git в MinIO | свой git в MinIO + Postgres schema | то же + **linked external repo** (GitHub) как deploy target |
| Deploy | preview = деплой | кнопка → prod-контейнер на `<slug>.app.omniadevelop.ru` | то же + **deploy-link**: push в подключённый GitHub-репо юзера (на его инфру или на наш сервер) |
| Уточнения у юзера | нет | нет | **идеологические Q-A** (не технические): «Кто аудитория? Тон? 2-3 эмоции первого экрана?» — управляются state-machine на backend |

V1/V2 НЕ удаляются. Поле `kind: 'static'|'fullstack'` остаётся; добавляется `stack_id` (FK на `stack_templates`) и `onboarding_session_id` на проект.

## Главный flow (happy path)

```
┌──────────────────────────────────────────────────────────────────────┐
│  1. Юзер заходит на /projects → "Создать новый"                      │
│  2. Onboarding step 1: "новый проект" vs "подключить GitHub"         │
│        ├─ "новый" → step 2                                            │
│        └─ "подключить" → OAuth GitHub → выбор репо → step 2          │
│  3. Onboarding step 2 (ideological-Q chat):                          │
│        AI Haiku генерит 3-5 вопросов по описанию идеи:               │
│         • Кто целевая аудитория?                                      │
│         • Какой тон коммуникации (формальный / дружелюбный / etc.)?  │
│         • Какие 2-3 эмоции должен вызывать первый экран?             │
│         • Что критично НЕ показывать / избегать?                     │
│        Юзер отвечает в чате; state-machine хранит ответы.            │
│  4. Onboarding step 3: backend дёргает /stack/recommend              │
│        LLM (Haiku) видит {brief + answers} и возвращает top-3        │
│        stack_id с обоснованием. UI показывает карточки.              │
│        Юзер может override (выбрать другой из всего каталога).       │
│  5. Onboarding step 4: backend дёргает classifier пресета (uses      │
│        существующий services/preset_classifier.py + freeze-БД).      │
│        Возвращает preset_id. UI показывает preview-карусель,         │
│        юзер может override (или skip — берётся auto).                │
│  6. Onboarding complete → создаётся Project (stack_id, preset_id,    │
│        linked_repo_id?, onboarding_session_id) → редирект            │
│        на /projects/:id (workspace).                                  │
│  7. Orchestrator provision: Dockerfile-template по stack_id +        │
│        injected UI-kit-tokens по preset_id → контейнер живёт.        │
│  8. Юзер пишет первый промпт → prompt_builder инжектит               │
│        {SYSTEM_PROMPT_for_stack + preset_tokens + ideological        │
│        answers}. Sonnet/Opus генерит код для выбранного стека.       │
│  9. Hot-reload в контейнере (как V2) → юзер видит wow-первый экран.  │
│  10. Кнопка "Deploy":                                                │
│        ├─ если linked_repo_id ≠ null → push в GitHub юзера           │
│        │     (его CI/Vercel/etc. разворачивают)                       │
│        └─ иначе → наш orchestrator build/push/nginx                  │
│             → <slug>.app.omniadevelop.ru                              │
└──────────────────────────────────────────────────────────────────────┘
```

## Каталог стеков (`stack_templates` table — seeded Chat-2)

V3.0 launch — минимум 3 стека (закрывают 80% запросов), расширение до 9 поэтапно. Chat-3 пишет templates в `apps/orchestrator/templates/`. Chat-2 сидит каталог в БД (id + meta) и отдаёт через `GET /api/stacks`.

| `stack_id` | Когда выбирать (LLM-критерий) | Шаблон в orchestrator | V3 priority |
|---|---|---|---|
| `static-html` | визитка, лендинг без формы, портфолио, простой блог без CMS | `templates/static-html/` (готовый html+css, без сборки) | **P0** |
| `nextjs-postgres-drizzle` | fullstack с БД, личный кабинет, CRUD, SaaS | `templates/nextjs-postgres-drizzle/` (уже есть, V2) | **P0** |
| `astro-content` | блог с MDX, документация, статический сайт с CMS | `templates/astro-content/` | **P0** |
| `nextjs-supabase` | SaaS с auth/realtime/storage из коробки | `templates/nextjs-supabase/` | P1 |
| `sveltekit-pocketbase` | лёгкий fullstack, low-resource, embedded БД | `templates/sveltekit-pocketbase/` | P1 |
| `fastapi-react` | data-heavy, ML-фичи, нужен Python на бэке | `templates/fastapi-react/` | P1 |
| `vue-nuxt` | юзер явно говорит «хочу Vue», корп. интранет | `templates/vue-nuxt/` | P2 |
| `htmx-go` | минималистичный сервер-rendered, без JS-фреймворков | `templates/htmx-go/` | P2 |
| `express-react` | классический MERN-стиль | `templates/express-react/` | P2 |

**Структура каждого шаблона** (одинаковая, Chat-3 enforce):

```
templates/<stack_id>/
├── README.md            # описание для AI: какие conventions используются
├── SYSTEM_PROMPT.md     # инструкции для LLM: формат файлов, паттерны, чего НЕ делать
├── Dockerfile.dev       # dev-runtime (hot-reload, volume mount)
├── Dockerfile.prod      # standalone production build
├── docker-compose.dev.yml
├── .env.example
├── package.json / pyproject.toml / go.mod  # зависимости стека
└── src/                 # минимальное приложение (страница "Hello from <stack>" с tailwind)
```

**LLM-критерии для recommend** (закрытый список — отдаётся в prompt LLM Haiku):
- `static-html` — нет БД, нет auth, нет dynamic content
- `nextjs-postgres-drizzle` — есть БД, нужен SSR/RSC, нужна гибкость
- `astro-content` — много контента, mdx, статика с минимум JS
- `nextjs-supabase` — realtime/auth-as-a-service
- `sveltekit-pocketbase` — small footprint, all-in-one
- `fastapi-react` — Python required, ML/data-pipeline
- `vue-nuxt` — Vue-экосистема явно нужна
- `htmx-go` — сервер-rendered минимализм, без npm
- `express-react` — legacy-friendly, простой Node-стек

Top-3 recommendation идёт обратно с `score: 0..1` и `reasoning: string` (одно предложение).

## Preset wow-эффект — freeze ui-ux-pro-max в БД

Текущий `apps/api/src/omnia_api/services/design_presets.py` содержит 8 пресетов V2. V3 добавляет:
- **`ui_kit_freeze` table** (Chat-2): хранит экспорт каталога ui-ux-pro-max плагина (палитры, font-pairs, паттерны компонентов, кит-классы).
- **seed-скрипт** `apps/api/scripts/seed_ui_kit_freeze.py` (Chat-2 пишет, **я** запускаю в dev-сессии после готовности — экспортирую руками из `~/.claude/plugins/.../ui-ux-pro-max/`).
- **prompt_builder** (Chat-2): при сборке system prompt, помимо `_SIGNATURE_MOVES` из старого `design_presets.py`, **дополнительно** инжектит конкретные токены из `ui_kit_freeze` по `preset_id` (если запись есть). Старый каталог становится **fallback** на случай пустой freeze-БД.

**Поля `ui_kit_freeze`** (см. `docs/02-data-model.md` V3 section):
```
id, slug, source ('ui-ux-pro-max' | 'context7' | 'manual'),
category ('palette' | 'font_pair' | 'pattern' | 'component' | 'framework_docs'),
name, payload jsonb, applicable_stacks text[], applicable_presets text[],
created_at, updated_at
```

`payload` — структурированный JSON, формат на каждую категорию свой. Examples (для seed):

```json
// palette
{"primary":"#0a84ff","bg":"#FFFFFF","fg":"#0A0A0A","muted":"#6B7280","accent":null}

// font_pair
{"display":"Plus Jakarta Sans","body":"Inter","weights":[400,500,700],"google_url":"..."}

// pattern
{"name":"editorial-hero","html":"<section class='py-32'>...</section>","tailwind":[],"why":"..."}

// framework_docs (Context7)
{"library":"nextjs","version":"15","topic":"app-router","content":"...","source_url":"..."}
```

## Ideological-Q flow — `onboarding_sessions` state-machine

Backend (Chat-2) держит state-machine. UI (Chat-1) — простой чат.

**State diagram:**

```
[start]
   │ POST /api/projects/onboarding/start {brief: string}
   ▼
[asking-Q] ─── /answer ────▶ [asking-Q] (до 5 вопросов)
   │                            │
   │ когда LLM решает (или      │ юзер пишет "skip"
   │ исчерпали 5 Q)             │
   ▼                            ▼
[recommending-stack] ◀──────────┘
   │ /confirm-stack
   ▼
[recommending-preset]
   │ /confirm-preset
   ▼
[complete] — создаётся Project, выдаётся project_id
```

**Persisted:**
- `onboarding_sessions` (id, user_id, state, brief, stack_recommendations jsonb, chosen_stack_id, chosen_preset_id, linked_repo_id, created_at)
- `onboarding_messages` (id, session_id, role 'system'|'ai'|'user', content, created_at)

**Q generation prompt** (Haiku, ~200 tokens, ₽0.05/onboarding):

```
Ты помощник в онбординге. Юзер хочет создать продукт: "<brief>".
Уже спрошено: <previous Q+A pairs>.
Задай СЛЕДУЮЩИЙ один идеологический вопрос (НЕ технический).
Цель — понять аудиторию, тон, эмоцию, ограничения, ценности.
НЕ спрашивай про языки, фреймворки, БД, дизайн-системы.
Если у тебя уже есть достаточно информации (>=3 содержательных ответа), верни {"done": true}.
Иначе верни {"done": false, "question": "...", "why": "одно предложение зачем спрашиваешь"}.
```

UI Chat-1 показывает вопросы как чат-пузыри, ответы — обычный input. После 3-5 итераций или `done:true` → переход к `/stack/recommend`.

## Connect-repo (GitHub OAuth, linked deploy target)

**V1 scope (этот спек):** только **linked deploy target**. То есть юзер связывает GitHub-репо как **место куда мы пушим** результат генерации. AI работает в **нашем** git внутри MinIO (как сейчас). Не редактируем чужой код.

**OAuth flow:**

```
Chat-1 (UI) ──"Connect GitHub"──▶ /api/auth/github/init
                                       │
                                       ▼
                    redirect → github.com/login/oauth/authorize
                                       │
                                       ▼
                    GitHub → /api/auth/github/callback?code=...
                                       │
                                       ▼ Chat-2:
                    1. Exchange code → access_token (secrets — в env-secret-store)
                    2. INSERT linked_repos {user_id, github_user_id, access_token_encrypted, ...}
                    3. Set-Cookie sessionish state for UI
                                       │
                                       ▼
                    redirect → /projects/new?repo_connect=<linked_repo_id>
                                       │
                                       ▼ Chat-1:
                    Show repo-picker (list user's repos via /api/repos/list)
                    On pick → POST /api/projects/connect-repo {linked_repo_id, repo_full_name, branch}
                                       │
                                       ▼ Chat-2:
                    Persist linked_repos.target_repo + project.linked_repo_id
```

**Deploy-link** (V1 — простой push):

`POST /api/projects/:id/deploy-link` →
1. Chat-2 рендерит project files (как сейчас, для preview).
2. Clone target_repo в temp dir (через access_token).
3. Скопировать файлы → commit "Deploy from Omnia <prompt_summary>" → push.
4. Возврат `{commit_url, success}`.

**Что V1 НЕ делает** (отложено V2):
- Не открывает PR (просто push в `omnia/deploy` branch юзера).
- Не запускает CI юзера / Vercel hook (полагаемся на их auto-deploy).
- Не reads существующий код для inspiration (только write).
- Не обрабатывает merge conflicts (force-push в `omnia/deploy` branch).

**OAuth App credentials:** владелец регистрирует GitHub OAuth App, кладёт `GITHUB_OAUTH_CLIENT_ID` + `GITHUB_OAUTH_CLIENT_SECRET` в `.env`. До регистрации — Chat-2 делает заглушку через ENV `GITHUB_OAUTH_MOCK=true` (возвращает фиктивный токен, push в локальный bare repo в MinIO).

## Deploy pipeline — расширение V2 под multi-stack

V2 деплоит только `nextjs-postgres-drizzle`. V3 — generic builder в Chat-3 zone:

```
POST /api/projects/:id/deploy
   │
   ▼ Chat-2 (api): прокси на orchestrator
   │
   ▼ Chat-3 (orchestrator):
   1. Load project files (git from MinIO).
   2. Load stack_template by stack_id → use Dockerfile.prod.
   3. docker build -t proj-<id>:<commit-sha> -f Dockerfile.prod .
   4. docker push registry:5000/proj-<id>:<commit-sha>
   5. docker run --rm --name proj-<id>-prod --network proj-<id> ...
   6. nginx conf write → reload (uses acme.sh cert)
   7. health-poll loop (10 × 3s) → 200/healthy
   8. WS deploy.complete {prod_url, image_tag}
```

Каждый шаблон должен предоставлять валидный `Dockerfile.prod` и эндпоинт `/health` или `/`. Это constraint каталога стеков.

## Биллинг (V3 дополнения)

- `tokens` (V1) — за LLM (уже есть)
- `runtime_hours` (V2) — за время dev-контейнера (уже есть)
- `deploy_slot` (V2) — фикс-подписка (уже есть)
- **`onboarding`** (V3) — за Haiku-вызовы во время онбординга (~₽0.50/онбординг — ideological-Q + stack_recommend + preset classifier)
- **`deploy_link`** (V3) — за push в чужой репо (фикс ₽5 за один deploy-link, покрывает API-quota GitHub)

Chat-2 расширяет `wallet_charges.type` enum.

## Roadmap по чатам (V3.0 launch)

| Sprint | Chat-1 (web) | Chat-2 (api) | Chat-3 (llm/orchestrator/infra) |
|---|---|---|---|
| **W1** | `OnboardingFlow` route + step machine + mock data | миграции (5 новых таблиц) + `/onboarding/*` endpoints | 3 шаблона (static-html, nextjs-pg-drizzle уже есть, astro-content) |
| **W2** | `IdeologicalChat` + `StackPicker` UI + `PresetCarousel` | `/stack/recommend` (через LLM Gateway) + `/presets` (read freeze) | `/v1/stack/recommend` endpoint в Gateway (Haiku) + generic Dockerfile builder |
| **W3** | `RepoConnect` UI + `DeployButton` (с link/native выбором) | GitHub OAuth + `/connect-repo` + `/deploy-link` | расширение `deploy` под stack_id → Dockerfile.prod lookup, nginx auto-conf для V3 |
| **W4** | polish, E2E flow, mobile-responsive onboarding | seed `ui_kit_freeze` (я делаю в отдельной сессии после Chat-2 готов) + biling-types | 3 дополнительных шаблона (nextjs-supabase, sveltekit-pocketbase, fastapi-react) если W1-W3 пошли по графику |

**Definition of Done V3.0:**
- Юзер заходит на `/projects/new` → проходит онбординг → видит recommend стек + пресет → подтверждает → попадает в workspace → пишет промпт → видит wow-результат для выбранного стека → нажимает deploy → видит prod URL (или GitHub commit URL если linked).
- 3 базовых стека работают end-to-end (static-html, nextjs-pg-drizzle, astro-content).
- Минимум 8 пресетов из freeze-БД (= то что сейчас в `design_presets.py` плюс 0+ из ui-ux-pro-max).

## Open questions / для решения по ходу

1. **GitHub OAuth App** — владелец зарегистрирует когда Chat-2 дойдёт до OAuth работы (~W3). До тех пор Chat-2 работает с `GITHUB_OAUTH_MOCK=true`.
2. **Реальный freeze ui-ux-pro-max** — я экспортирую вручную после того как Chat-2 закончит migration + read-API. Не блокирует параллельную работу: `prompt_builder` всегда имеет fallback на static `design_presets.py`.
3. **Force-push vs PR** на deploy-link — для V1 force-push в `omnia/deploy` branch. Если юзеры будут жаловаться — V1.1 переключим на PR.
4. **Cost prediction в UI** — UI должен показывать примерную стоимость онбординга (₽0.50) и deploy (₽5). Включено в `Project` ответе после онбординга (`estimated_setup_cost`). Chat-2 добавляет.
5. **Mobile onboarding** — Chat-1 делает sequential layout на мобиле (один шаг = один экран). Не блокер для V3.0, можно отложить W4.
6. **Linked-repo первичная синхронизация** — если у юзера в репо уже есть код, мы его перезаписываем при first deploy. UI должен предупредить. Chat-1 добавляет warning-modal.

## Где править / shared-файлы

| Изменение | Файл | Кто пишет |
|---|---|---|
| API контракт | `docs/01-api-contract.md` (V3 секция) | я (этот документ-сосед) |
| Schema БД | `docs/02-data-model.md` (V3 секция) | я (этот документ-сосед) |
| Этот спек | `docs/10-v3-multistack-pivot.md` | я; правки только через inbox-запрос к мне |
| Каталог стеков (БД) | `apps/api/src/omnia_api/seed/stack_templates.py` (новый) | Chat-2 |
| Каталог стеков (templates) | `apps/orchestrator/templates/<stack_id>/` | Chat-3 |
| Freeze ui-ux-pro-max | `apps/api/src/omnia_api/seed/ui_kit_freeze.py` (новый) | Chat-2 (структура) + я (заполнение данными) |
| Onboarding state machine | `apps/api/src/omnia_api/services/onboarding.py` (новый) | Chat-2 |
| Stack recommender | `apps/llm-gateway/src/omnia_gateway/routers/stack.py` (новый) | Chat-3 |
| Ideological-Q генератор | `apps/llm-gateway/src/omnia_gateway/routers/onboarding.py` (новый) | Chat-3 |
| UI онбординга | `apps/web/src/app/(app)/projects/new/` (новый каталог) | Chat-1 |

## Связь с предыдущими спеками

- V2 (`docs/07-v2-architecture.md`) — full-stack runtime/deploy остаётся живым. V3 расширяет multi-stack и onboarding на эту базу.
- Пресет-классификатор v3.0 (`docs/09-generated-site-presets.md`) — остаётся, теперь работает поверх `ui_kit_freeze` (старый `design_presets.py` = fallback).
- VPS setup (`docs/08-vps-setup.md`) — без изменений; новые поддомены для V3 шаблонов сидят на том же `*.preview.omniadevelop.ru` wildcard cert.
