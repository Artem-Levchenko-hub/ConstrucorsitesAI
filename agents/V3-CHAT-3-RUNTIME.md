# Chat-3 — LLM Gateway + Orchestrator + Infra (V3 multi-stack pivot)

> **Self-contained.** Прочитай этот файл целиком, потом `docs/10-v3-multistack-pivot.md` (главная спека), `docs/01-api-contract.md` (V3 секция, особенно LLM Gateway endpoints), `docs/07-v2-architecture.md` (V2 runtime — на чём строим). Старые `agents/AGENT-C-LLM-GATEWAY.md` и `agents/AGENT-D-ORCHESTRATOR.md` — справочники по стекам/конвенциям.

## Кто ты

Owner всего runtime/ops/external-API слоя:
- **LLM Gateway** (`apps/llm-gateway/`): добавляешь два новых endpoint'а — onboarding-вопросы и stack-recommend (оба на Haiku-4.5).
- **Orchestrator** (`apps/orchestrator/`): добавляешь generic builder, который выбирает Dockerfile.dev/prod по `stack_id`. Расширяешь `ProvisionRequest.stack_id`. Пишешь 3 P0-шаблона (один уже есть — `nextjs-postgres-drizzle`).
- **Infra** (`infra/`): docker-compose правки если появятся новые сервисы; nginx-шаблоны для новых поддоменов V3.
- **Landing** (`apps/landing/`): только deploy-конфиги (если придёт изменение под V3 анонс — пока не блокер).

Параллельно работают:
- **Chat-1** (Frontend) — для тебя невидим.
- **Chat-2** (Backend) — дёргает твой LLM Gateway по HTTP и оркестратор по HTTP. Ты — service-провайдер.

## Жёсткие границы

- **Write только в `apps/llm-gateway/`, `apps/orchestrator/`, `infra/`, `apps/landing/`.** Никаких правок в `apps/web/`, `apps/api/`.
- **Read-only:** `docs/`, `agents/`, `apps/api/src/omnia_api/schemas/` (публичный контракт — чтобы понимать какие типы Chat-2 экспортит наружу).
- **Контракт LLM Gateway** — `docs/01-api-contract.md` V3 LLM Gateway endpoints section. Меняешь — inbox в Chat-2 + правка docs/01.
- **Контракт orchestrator** — `apps/orchestrator/src/omnia_orchestrator/schemas/runtime.py`. Меняешь — inbox в Chat-2 + комментарий в schemas/.

## Стек (без изменений)

LLM Gateway: Python 3.12 + FastAPI + LiteLLM + httpx + redis + asyncpg + tiktoken + sse-starlette + structlog.

Orchestrator: Python 3.12 + FastAPI + docker-py 7.1+ + asyncpg + structlog + sentry-sdk + httpx + pytest-mock.

V3 добавляет — ничего нового на уровне зависимостей.

## Что добавить — LLM Gateway

### Новые endpoints (`apps/llm-gateway/src/omnia_gateway/routers/`)

| Файл | Endpoint |
|---|---|
| `onboarding.py` (новый) | `POST /v1/onboarding/next_question` |
| `stack.py` (новый) | `POST /v1/stack/recommend` |

Под капотом — **Haiku-4.5** через существующий `services/litellm_router.py`. Один вызов = 1 LLM запрос. Цены — в `docs/10` (≈₽0.05 за вопрос, ≈₽0.10 за recommend).

### Promtps (`apps/llm-gateway/src/omnia_gateway/prompts/`)

Создать (или расширить):
- `onboarding_q.md` — template для генератора идеологического вопроса (см. docs/10 «Q generation prompt»).
- `stack_recommend.md` — system prompt с закрытым списком `stack_id` + критериями выбора (см. docs/10 LLM-критерии).

### Сервисы (`apps/llm-gateway/src/omnia_gateway/services/`)

| Файл | Что |
|---|---|
| `onboarding_qgen.py` (новый) | Builds prompt из brief+qa_pairs, дёргает Haiku, парсит JSON `{done, question, why}`. На invalid JSON — retry 2x, потом дефолтный fallback question. |
| `stack_recommender.py` (новый) | Builds prompt из brief+answers, дёргает Haiku, парсит JSON `{recommendations: [{stack_id, score, reasoning}]}`. Валидирует что stack_id в закрытом списке. |
| `usage_logger.py` (расширить) | Записывать `purpose='onboarding_q'` или `'stack_recommend'` в `usage.purpose` (Chat-2 добавит колонку в миграции 0008). |

### Тесты (`apps/llm-gateway/tests/`)

| Файл | Что |
|---|---|
| `test_onboarding_qgen.py` | Mock LiteLLM ответы; проверка парсинга {done:true} и {done:false,...}. |
| `test_stack_recommender.py` | Mock LiteLLM; проверка top-3 валидных stack_id. |

## Что добавить — Orchestrator

### Расширение `ProvisionRequest` schema

`apps/orchestrator/src/omnia_orchestrator/schemas/runtime.py`:

```python
class ProvisionRequest(BaseModel):
    project_id: str
    slug: str
    template: str = "nextjs-postgres-drizzle"  # legacy field, оставить
    stack_id: str = "nextjs-postgres-drizzle"  # V3 — приоритетнее template
    tier: Literal["free", "pro", "business"] = "free"
    # ... остальное без изменений
```

При наличии `stack_id` — игнорировать `template`. Backward-compatible (старые вызовы без stack_id используют template).

### Generic Dockerfile builder (`apps/orchestrator/src/omnia_orchestrator/services/`)

| Файл | Что |
|---|---|
| `template_loader.py` (новый) | `load_template(stack_id) -> TemplateMeta` — читает `templates/<stack_id>/`, валидирует наличие Dockerfile.dev, Dockerfile.prod, README, SYSTEM_PROMPT, src/. Возвращает meta для builder. |
| `provisioner.py` (расширить) | Использовать `template_loader.load_template(req.stack_id)` вместо хардкода `nextjs-postgres-drizzle`. |
| `builder.py` (расширить) | Deploy-pipeline `docker build -f <template.Dockerfile.prod>` вместо хардкода. |
| `nginx_writer.py` (расширить) | Шаблон nginx-conf генерится для любого стека — параметры из template-meta (`expose_port`, `health_path`). |

### Новые шаблоны (`apps/orchestrator/templates/`)

V3 launch — 3 P0:

```
templates/
├── nextjs-postgres-drizzle/    # уже есть (V2) — не трогать, но добавить SYSTEM_PROMPT V3 enhancements если нужны
├── static-html/                # НОВЫЙ
│   ├── README.md
│   ├── SYSTEM_PROMPT.md
│   ├── Dockerfile.dev          # nginx alpine с volume mount на src/
│   ├── Dockerfile.prod         # nginx alpine standalone copy
│   ├── docker-compose.dev.yml
│   ├── .env.example            # пустой (статика не требует)
│   ├── nginx.conf              # минимальный — отдаёт src/
│   └── src/
│       ├── index.html          # Hello with Tailwind via CDN
│       ├── style.css
│       └── assets/
└── astro-content/              # НОВЫЙ
    ├── README.md
    ├── SYSTEM_PROMPT.md
    ├── Dockerfile.dev          # node:20-alpine + astro dev --host 0.0.0.0
    ├── Dockerfile.prod         # multi-stage: builder npm run build → nginx
    ├── docker-compose.dev.yml
    ├── .env.example
    ├── package.json            # astro@4, @astrojs/mdx, tailwind integration
    ├── astro.config.mjs
    ├── tsconfig.json
    └── src/
        ├── pages/
        │   ├── index.astro     # Hello with Astro/Tailwind
        │   └── about.mdx       # demo MDX
        ├── layouts/
        │   └── Base.astro
        └── components/
            └── Hero.astro
```

**SYSTEM_PROMPT.md** для каждого шаблона: 1-2 страницы инструкций AI — формат `<file path>` блоков (как в V1), conventions стека (где компоненты, где стили, как добавить страницу), чего НЕ делать (не менять package.json, не добавлять npm-зависимости в первом промпте). Это критично для wow-эффекта.

### Тесты (`apps/orchestrator/tests/`)

| Файл | Что |
|---|---|
| `test_template_loader.py` | Все 3 P0 шаблона валидны (нужные файлы есть). |
| `test_provisioner_multistack.py` | Provision с разными stack_id создаёт правильный контейнер. Docker — mocked. |

## Что добавить — Infra / Landing

- `infra/nginx/snippets/` — если общий ingress нуждается в правках под V3 host-routing. Скорее не нужно (orchestrator-nginx-writer пишет per-project).
- `apps/landing/` — только если придёт явный запрос от владельца под V3-анонс. Скип в W1-W4.

## Фазы (W1-W4)

### W1 — 3 P0 шаблона + provision

- [ ] `templates/static-html/` (полный набор файлов)
- [ ] `templates/astro-content/` (полный набор файлов)
- [ ] `services/template_loader.py` + тесты
- [ ] Расширение `ProvisionRequest.stack_id` в schemas
- [ ] `provisioner.py` использует `template_loader`
- [ ] Локальный smoke-test: `curl POST /internal/projects/provision {stack_id:"static-html"}` → контейнер живой

**DoD W1:** Chat-2 может дёрнуть provision с `stack_id=static-html` или `astro-content` — orchestrator поднимает соответствующий контейнер.

### W2 — LLM Gateway V3 endpoints

- [ ] `routers/onboarding.py` + `services/onboarding_qgen.py` + prompt `onboarding_q.md`
- [ ] `routers/stack.py` + `services/stack_recommender.py` + prompt `stack_recommend.md`
- [ ] `usage_logger.py` — пишет `purpose` (Chat-2 миграция 0008 должна быть применена)
- [ ] Тесты обоих сервисов с mocked LiteLLM
- [ ] Дополнить `GET /v1/models` чтобы `claude-haiku-4-5` точно доступен (если ещё нет)

**DoD W2:** Chat-2 может дёрнуть оба endpoint'а вживую → получить JSON правильного формата.

### W3 — Multi-stack deploy

- [ ] `builder.py` расширение под `template.Dockerfile.prod`
- [ ] `nginx_writer.py` — generic параметры из template-meta
- [ ] Локальный E2E: provision static-html → AI-edit (mock) → deploy → `<slug>.app.omniadevelop.ru` отдаёт результат
- [ ] То же для astro-content

**DoD W3:** все 3 P0 стека деплоятся через native deploy без изменения кода Chat-2.

### W4 — P1 шаблоны (если W1-W3 в графике)

- [ ] `templates/nextjs-supabase/`
- [ ] `templates/sveltekit-pocketbase/`
- [ ] `templates/fastapi-react/`

**DoD W4:** P1 шаблоны проходят валидацию `template_loader`; провижн+deploy локально хотя бы 1 из них.

## Команды

```bash
# LLM Gateway
cd apps/llm-gateway
uv sync
uv run uvicorn omnia_gateway.main:app --reload --port 8001
uv run pytest -q

# Orchestrator
cd apps/orchestrator
uv sync
uv run uvicorn omnia_orchestrator.main:app --reload --port 8003
uv run pytest -q

# Smoke test шаблона локально (без orchestrator)
cd apps/orchestrator/templates/static-html
docker compose -f docker-compose.dev.yml up
# → http://localhost:8080 должен отдать "Hello from static-html"
```

## Координация

**Перед стартом:**
1. Прочитать `~/.claude/coordination/omnia-mvp/BOARD.md` (твоя строка `Chat-3`).
2. Прочитать новые `inbox/*.md` (особенно от Chat-2 на тему gateway/orchestrator-контракта).
3. Обновить свою строку.

**Когда расширяешь `apps/orchestrator/src/omnia_orchestrator/schemas/runtime.py`** (shared-файл, читает Chat-2):
- Делать backward-compatible (новые поля Optional с default).
- Inbox-нотификация в Chat-2: `~/.claude/coordination/omnia-mvp/inbox/YYYY-MM-DD-from-CHAT-3-to-CHAT-2-runtime-schema-change.md`.

**VPS-операции:**
- `omnia-prod-*` контейнеры — НЕ пересобирать без явного разрешения владельца (DEPLOY FREEZE в BOARD).
- Если нужен рестарт `omnia-orchestrator` на VPS — обновить через `scp` + `systemctl restart omnia-orchestrator`. SSH alias `kanavto-vps` или `lh-server` (см. memory `omnia_vps_inventory`).

**Перед коммитом:**
- `/safe-commit "feat(orchestrator/v3): <что>"` или `feat(gateway/v3): <что>`.
- Для новых шаблонов — `feat(orchestrator/templates): add <stack_id>`.

## Старт

```powershell
cd "C:\Бизнес план\omnia-mvp\apps\orchestrator"
claude
# Первый промпт:
# "Прочитай agents/V3-CHAT-3-RUNTIME.md, docs/10-v3-multistack-pivot.md, docs/01-api-contract.md, docs/07-v2-architecture.md.
#  Активируй code-canon. Начинай с W1: 3 P0 шаблона (static-html, astro-content) + template_loader + расширение ProvisionRequest.stack_id."
```
