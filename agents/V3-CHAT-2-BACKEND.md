# Chat-2 — Backend + Preset/Stack/Repo logic (V3 multi-stack pivot)

> **Self-contained.** Прочитай этот файл целиком, потом `docs/10-v3-multistack-pivot.md` (главная спека), `docs/01-api-contract.md` (V3 секция — endpoints + типы), `docs/02-data-model.md` (V3 секция — миграция 0008). Старый `agents/AGENT-B-BACKEND.md` — справочник по конвенциям FastAPI/SQLAlchemy/Alembic.

## Кто ты

Owner всего `apps/api/` монолита. Делаешь:
- миграцию 0008 (5 новых таблиц + 5 колонок к `projects`)
- onboarding state-machine + endpoints
- stack catalog + recommender (вызывает Chat-3 LLM Gateway)
- preset freeze-БД read API
- GitHub OAuth + linked-repo CRUD
- deploy-link pipeline (clone → commit → push)
- proxy на orchestrator для native deploy (без изменений V2)

Параллельно работают:
- **Chat-1** (Frontend) — потребитель твоих endpoints.
- **Chat-3** (LLM Gateway + Orchestrator + Infra) — ты их дёргаешь по HTTP (gateway `:8001`, orchestrator `:8003`).

## Жёсткие границы

- **Write только в `apps/api/`.** Никаких правок в `apps/web/`, `apps/llm-gateway/`, `apps/orchestrator/`, `infra/`, `apps/landing/`.
- **Read-only:** `docs/`, `agents/`, `apps/orchestrator/src/omnia_orchestrator/schemas/runtime.py` (internal-контракт, чтобы знать что Chat-3 ждёт).
- **Контракт** — `docs/01-api-contract.md` V3 секция. Меняешь — inbox-нотификация Chat-1 + Chat-3 + правка docs/01.

## Стек (без изменений vs AGENT-B)

Python 3.12 + uv + FastAPI 0.115+ + SQLAlchemy 2.0 async + asyncpg + Alembic + redis-py + RQ + minio-py + pygit2 + Playwright + python-jose[cryptography] + passlib[bcrypt] + pydantic v2 + structlog.

V3 добавляет: `cryptography.fernet` (для шифрования access_token линкованного репо), `httpx` (для GitHub API — он уже есть), `PyGithub` опционально (или сырой httpx — на выбор).

## Что добавить

### Миграция (`apps/api/migrations/versions/0008_v3_multistack.py`)

Одна атомарная миграция:
1. `ALTER TABLE projects ADD COLUMN stack_id text NULL` + FK
2. `ALTER TABLE projects ADD COLUMN preset_id text NULL` + FK (after `ui_kit_freeze` создан)
3. `ALTER TABLE projects ADD COLUMN onboarding_session_id uuid NULL` + FK
4. `ALTER TABLE projects ADD COLUMN linked_repo_id uuid NULL` + FK
5. `ALTER TABLE projects ADD COLUMN estimated_setup_cost_rub numeric(12,4) NULL`
6. `CREATE TABLE stack_templates (...)` (см. docs/02 V3)
7. `CREATE TABLE ui_kit_freeze (...)` + GIN индексы
8. `CREATE TABLE linked_repos (...)` + UNIQUE индекс
9. `CREATE TABLE onboarding_sessions (...)` + триггер set_updated_at
10. `CREATE TABLE onboarding_messages (...)`
11. `ALTER TABLE wallet_charges` расширить CHECK на `type`
12. `ALTER TABLE usage ADD COLUMN purpose text NULL`

Downgrade — обратный порядок, аккуратно с FK.

### Models (`apps/api/src/omnia_api/models/`)

| Файл | Класс |
|---|---|
| `stack_template.py` (новый) | `StackTemplate` |
| `ui_kit_entry.py` (новый) | `UiKitEntry` |
| `linked_repo.py` (новый) | `LinkedRepo` |
| `onboarding_session.py` (новый) | `OnboardingSession`, `OnboardingMessage` |
| `project.py` (расширить) | + 5 новых полей |
| `wallet.py` (расширить) | `WalletCharge.type` enum |
| `usage.py` (расширить) | `Usage.purpose` |

### Schemas (`apps/api/src/omnia_api/schemas/`)

| Файл | Pydantic-классы |
|---|---|
| `onboarding.py` (новый) | `OnboardingSessionPublic`, `OnboardingMessagePublic`, `OnboardingStartIn`, `OnboardingAnswerIn`, `ConfirmStackIn`, `ConfirmPresetIn` |
| `stack.py` (новый) | `StackTemplatePublic`, `StackRecommendation`, `StackRecommendIn`, `StackRecommendOut` |
| `preset.py` (новый) | `UiKitEntryPublic`, `UiKitEntryWithPreview` |
| `repo.py` (новый) | `LinkedRepoPublic`, `ConnectRepoIn`, `ListReposOut` |
| `deploy_link.py` (новый) | `DeployLinkStatusPublic`, `DeployLinkTriggerIn` |
| `project.py` (расширить) | + 5 новых V3-полей в `ProjectPublic` |

### Routers (`apps/api/src/omnia_api/routers/`)

| Файл | Endpoints |
|---|---|
| `onboarding.py` (новый) | `POST /api/projects/onboarding/start`, `POST /:sid/answer`, `POST /:sid/confirm-stack`, `POST /:sid/confirm-preset`, `GET /:sid` |
| `stacks.py` (новый) | `GET /api/stacks`, `POST /api/projects/stack/recommend` |
| `presets.py` (новый) | `GET /api/presets`, `GET /api/presets/:slug/preview` |
| `repos.py` (новый) | `GET /api/auth/github/init`, `GET /api/auth/github/callback`, `POST /api/projects/connect-repo`, `GET /api/repos/list`, `GET /api/projects/:id/repo`, `DELETE /api/projects/:id/repo` |
| `deploy_link.py` (новый) | `POST /api/projects/:id/deploy-link`, `GET /api/projects/:id/deploy-link` |
| `main.py` (правка) | Подключить новые роутеры в `app.include_router(...)` |

### Services (`apps/api/src/omnia_api/services/`)

| Файл | Назначение |
|---|---|
| `onboarding_machine.py` (новый) | State-machine: переходы между asking-Q → recommending-stack → recommending-preset → complete. Вызывает LLM Gateway для next_question + stack_recommend. Записывает в БД. |
| `github_oauth.py` (новый) | OAuth code exchange, шифрование/дешифрование access_token (`cryptography.fernet`), GitHub user info fetch. |
| `github_repo.py` (новый) | List repos через access_token, clone (через pygit2 с https-cred), commit, push. |
| `deploy_link.py` (новый) | End-to-end: load project files (existing `services/repo.py`) → clone target → copy → commit → push → WS event. |
| `ui_kit_repo.py` (новый) | Read API для `ui_kit_freeze` (фильтры по category/stack/preset). |
| `stack_repo.py` (новый) | Read API для `stack_templates` + cache в Redis. |
| `prompt_builder.py` (расширить) | Если `project.preset_id` указывает на `ui_kit_freeze` — инжектить токены оттуда. Если нет — fallback на старый `design_presets.py`. |
| `llm_client.py` (расширить) | Новые методы: `next_onboarding_question()`, `recommend_stack()` — дёргают `:8001/v1/onboarding/next_question` и `:8001/v1/stack/recommend`. |
| `orchestrator_client.py` (расширить) | `provision()` теперь передаёт `stack_id` в orchestrator (контракт Chat-3 расширяет `ProvisionRequest.stack_id`). |
| `billing.py` (расширить) | Новые charge types: `onboarding`, `deploy_link`. |

### Seed (`apps/api/src/omnia_api/seed/`, новая папка)

| Файл | Что делает |
|---|---|
| `__init__.py` | пусто |
| `stack_templates.py` | INSERT 3 P0-шаблона (`static-html`, `nextjs-postgres-drizzle`, `astro-content`). Idempotent (ON CONFLICT). |
| `ui_kit_freeze.py` | Scaffolded INSERT-statements для палитр/font-pairs/паттернов. **Я экспортирую реальные данные из ui-ux-pro-max после твоей готовности — заглушку оставь.** |

Запуск: `python -m omnia_api.seed.stack_templates` (или alembic data-only migration).

### Environment (`.env.example` расширить)

```
# V3: GitHub OAuth
GITHUB_OAUTH_CLIENT_ID=
GITHUB_OAUTH_CLIENT_SECRET=
GITHUB_OAUTH_REDIRECT_URI=http://localhost:3000/projects/new/connect-repo
GITHUB_OAUTH_MOCK=true              # пока владелец не зарегистрировал App

# V3: encryption для access tokens
LINKED_REPO_ENCRYPTION_KEY=         # 32-byte base64 (Fernet)
```

### Mock mode

Когда `GITHUB_OAUTH_MOCK=true`:
- `/api/auth/github/init` сразу делает 302 на `?code=mock_code&state=mock`
- `/api/auth/github/callback` принимает любой code → создаёт `linked_repos` с `github_user_id=999999`, `github_username='mock-user'`, `access_token_encrypted=<encrypted "mock_token">`
- `github_repo.list_user_repos()` возвращает захардкоженный список `[{name:"omnia-test", full_name:"mock-user/omnia-test", default_branch:"main", private:false}]`
- `github_repo.push_to_branch()` пишет в локальный bare-repo в MinIO (`mock-pushes/<linked_repo_id>/`) вместо реального GitHub. WS event эмулируется.

## Фазы (W1-W4)

### W1 — Миграция 0008 + onboarding endpoints

- [ ] Миграция 0008 (5 таблиц + 5 колонок) + downgrade
- [ ] Models + Schemas для онбординга
- [ ] `routers/onboarding.py` + state-machine **с MOCK Haiku** (без интеграции LLM Gateway пока — вернуть фиксированные вопросы из массива)
- [ ] Тесты: `tests/test_onboarding_e2e.py` — register → start → answer×3 → confirm-stack → confirm-preset → проверка `project_id` создан

**DoD W1:** `alembic upgrade head` создаёт V3 схему. POST/GET endpoints онбординга работают на mock-вопросах. Chat-1 может потреблять.

### W2 — Stack catalog + recommender + preset read

- [ ] `routers/stacks.py` + `services/stack_repo.py` + seed `stack_templates.py`
- [ ] `routers/presets.py` + `services/ui_kit_repo.py` (read freeze; scaffolded seed `ui_kit_freeze.py`)
- [ ] Интеграция с LLM Gateway: `services/llm_client.py` методы `next_onboarding_question()` + `recommend_stack()`
- [ ] Заменить mock Haiku в onboarding на реальные вызовы Gateway (когда Chat-3 W2 готов)
- [ ] Расширение `prompt_builder.py` под freeze-БД (с fallback на legacy `design_presets.py`)

**DoD W2:** полный онбординг с реальным Haiku-вопросами. Chat-1 видит реальные top-3 рекомендации. Preset preview-HTML работает (можно с заглушками если freeze пустой).

### W3 — GitHub OAuth + deploy-link

- [ ] `services/github_oauth.py` + `routers/repos.py`
- [ ] Шифрование access_token (Fernet)
- [ ] Mock mode (`GITHUB_OAUTH_MOCK=true`) полностью работает
- [ ] `services/github_repo.py` (list, clone, commit, push)
- [ ] `services/deploy_link.py` + `routers/deploy_link.py`
- [ ] WS события `deploy.linked.progress`, `repo.connected`
- [ ] Тесты mock-режима (без реального GitHub API)

**DoD W3:** Chat-1 видит full repo-connect flow с mock-токеном. Deploy-link push в локальный bare-repo работает с реальными файлами проекта.

### W4 — Billing types + real OAuth (когда владелец зарегистрирует) + polish

- [ ] `billing.py` расширение под `onboarding` + `deploy_link` charge types
- [ ] Cost prediction: `Project.estimated_setup_cost_rub` подсчёт на onboarding-complete
- [ ] Если владелец зарегистрировал OAuth App — выключить `GITHUB_OAUTH_MOCK=false`, прогнать вживую
- [ ] Rate limits для V3 endpoints (см. `docs/01` таблицу)
- [ ] Error codes V3 — добавить mapping в `core/errors.py`

**DoD W4:** все endpoints доступны, тесты зелёные. `pytest -q && ruff check . && mypy src/` без ошибок.

## Команды (без изменений)

```bash
cd apps/api
uv sync
uv run alembic upgrade head
uv run uvicorn omnia_api.main:app --reload --port 8000
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy src/

# V3 seeds
PYTHONPATH=src uv run python -m omnia_api.seed.stack_templates
PYTHONPATH=src uv run python -m omnia_api.seed.ui_kit_freeze   # (заглушка пока)
```

## Координация

**Перед стартом:**
1. Прочитать `~/.claude/coordination/omnia-mvp/BOARD.md`.
2. Прочитать новые `inbox/*.md` от Chat-1 и Chat-3.
3. Обновить свою строку `Chat-2`.

**Когда нужно от Chat-3 (LLM Gateway endpoints):**
- W2 момент — `inbox/YYYY-MM-DD-from-CHAT-2-to-CHAT-3-need-gateway-endpoints.md` с контрактом из docs/01.
- Параллельно продолжать с mock Haiku.

**Когда нужно от Chat-3 (orchestrator stack_id):**
- W3 момент — inbox запрос на расширение `ProvisionRequest.stack_id` в `apps/orchestrator/src/omnia_orchestrator/schemas/runtime.py`.

**Перед коммитом:**
- `/safe-commit "feat(api/v3): <что>"`.
- Особо аккуратно с миграциями — не пересоздавать 0008, всегда новая ревизия если нужны правки.

## Старт

```powershell
cd "C:\Бизнес план\omnia-mvp\apps\api"
claude
# Первый промпт:
# "Прочитай agents/V3-CHAT-2-BACKEND.md, docs/10-v3-multistack-pivot.md, docs/01-api-contract.md, docs/02-data-model.md.
#  Активируй code-canon. Начинай с W1: миграция 0008 + модели/схемы + onboarding роутер с mock Haiku."
```
