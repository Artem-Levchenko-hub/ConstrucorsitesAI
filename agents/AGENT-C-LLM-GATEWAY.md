# Агент C — LLM Gateway (apps/llm-gateway/)

Прочитай этот бриф, затем `docs/01-api-contract.md` (раздел «LLM Gateway internal API») и `docs/02-data-model.md` (таблицы `usage`, `wallet_charges`). После — приступай к M0.

## Кто ты в этой команде

Ты пишешь **умный прокси к LLM-провайдерам**. Принимаешь OpenAI-совместимые запросы от backend (агент B) на `:8001`, проксируешь в Anthropic / OpenAI / YandexGPT / Alibaba (Qwen) через **LiteLLM**, считаешь токены и стоимость в рублях, кэшируешь часто-повторяющиеся ответы.

Ты — изолированный сервис. Никто кроме B тебя не дёргает. Frontend (агент A) не знает о твоём существовании.

## Жёсткие границы

- **ПИШЕШЬ ТОЛЬКО в `apps/llm-gateway/`.**
- **НЕ ЛЕЗЬ в `apps/web/`, `apps/api/`, `infra/`** (последнее — зона B).
- Контракт API — `docs/01-api-contract.md`. Если нужна правка — записка в координацию + продолжай.

## Стек (фиксированный)

- **Python 3.12**, `uv`.
- **FastAPI 0.115+** (async).
- **LiteLLM** 1.50+ — унифицированный клиент к 100+ провайдерам.
- **httpx** (async) — для прямых вызовов, если LiteLLM не покрывает.
- **redis-py** (async) — кеш ответов.
- **asyncpg** или прямой psycopg для записи в `usage` (можно через ту же БД, что и B — общая Postgres).
- **tiktoken** + LiteLLM cost map — для подсчёта токенов и цены.
- **sse-starlette** для SSE-ответов.
- **pydantic v2**.
- **structlog** для логов.
- **pytest + pytest-asyncio**.

## Структура `apps/llm-gateway/`

```
apps/llm-gateway/
├── pyproject.toml
├── uv.lock
├── .env.example
├── README.md
├── src/
│   └── omnia_gateway/
│       ├── __init__.py
│       ├── main.py                       (FastAPI app)
│       ├── core/
│       │   ├── config.py                 (env-конфиг)
│       │   ├── db.py                     (минимальный — только usage)
│       │   ├── redis.py
│       │   └── errors.py
│       ├── services/
│       │   ├── litellm_router.py         (конфигурация LiteLLM, fallbacks)
│       │   ├── pricing.py                (RUB-цены, конвертация из USD)
│       │   ├── cache.py                  (sha256-ключ, TTL, get/set)
│       │   ├── safety.py                 (prompt-injection guard)
│       │   ├── usage_logger.py           (запись в Postgres.usage)
│       │   └── billing.py                (списание из wallets — общая БД)
│       ├── routers/
│       │   ├── chat.py                   (POST /v1/chat/completions)
│       │   ├── models.py                 (GET /v1/models)
│       │   └── health.py                 (GET /health)
│       └── prompts/
│           └── system.md                 (опционально — если хочется централизовать)
└── tests/
    ├── test_chat_non_streaming.py
    ├── test_chat_streaming.py
    ├── test_pricing.py
    ├── test_cache.py
    └── test_safety.py
```

## Поддерживаемые модели (MVP)

| Model ID | Provider | LiteLLM string | Key env |
|---|---|---|---|
| `claude-sonnet-4-6` | Anthropic | `anthropic/claude-sonnet-4-5` (или актуальный slug) | `ANTHROPIC_API_KEY` |
| `claude-opus-4-7` | Anthropic | `anthropic/claude-opus-4-5` | `ANTHROPIC_API_KEY` |
| `claude-haiku-4-5` | Anthropic (via proxyapi.ru) | `anthropic/claude-haiku-4-5` + `api_base=https://api.proxyapi.ru/anthropic` (LiteLLM adds `/v1/messages`) | `PROXYAPI_API_KEY` |
| `gpt-4.1` | OpenAI | `openai/gpt-4o` (как ближайший эквивалент в MVP) | `OPENAI_API_KEY` |
| `gpt-5-mini` | OpenAI | `openai/gpt-4o-mini` | `OPENAI_API_KEY` |
| `yandexgpt-5` | Yandex | `custom_provider/yandexgpt` (своя обёртка через httpx, если нет в LiteLLM) | `YANDEX_API_KEY` + `YANDEX_FOLDER_ID` |
| `qwen-3-coder` | Alibaba | `openrouter/qwen/qwen3-coder` (через OpenRouter — проще, чем регать Alibaba Cloud) | `OPENROUTER_API_KEY` |
| `gigachat-2{,-pro,-max}` | Sber | прямой `providers/sber.py` (OAuth + `httpx`, в LiteLLM нет) | `GIGACHAT_AUTH_KEY` |

Уточни актуальные slug'и в LiteLLM docs (`/v1/model_info`). Если конкретной модели нет — оставь TODO в `services/litellm_router.py` и временно используй ближайшую.

## Цены в рублях (на момент MVP, май 2026)

Конвертация: курс ЦБ + наценка 20% (хедж против скачков). В коде — таблица в `pricing.py`. Можно подгружать из ENV для гибкости.

| Model | RUB / 1k input | RUB / 1k output |
|---|---:|---:|
| `claude-sonnet-4-6` | 0.30 | 1.50 |
| `claude-opus-4-7` | 1.50 | 7.50 |
| `gpt-4.1` | 0.50 | 2.00 |
| `gpt-5-mini` | 0.06 | 0.24 |
| `yandexgpt-5` | 0.10 | 0.40 |
| `qwen-3-coder` | 0.05 | 0.20 |

Это стартовые ориентиры — корректировать перед запуском беты на основе реального курса USD/RUB.

## Фазы

### M0 — Базовый /chat (день 1–2)

**Задачи:**
1. `uv init`, `pyproject.toml`, базовый FastAPI на `:8001`.
2. `core/config.py` — env-переменные: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `YANDEX_API_KEY`, `YANDEX_FOLDER_ID`, `OPENROUTER_API_KEY`, `REDIS_URL`, `POSTGRES_URL` (та же, что у B), `DEFAULT_MODEL`.
3. **`services/litellm_router.py`:**
   - Конфиг LiteLLM Router с моделями выше + fallbacks (`claude-sonnet-4-6 → gpt-4.1 → gpt-5-mini`).
   - YandexGPT — обёртка через httpx, если LiteLLM нативно не поддерживает (вызов `https://llm.api.cloud.yandex.net/foundationModels/v1/completion`).
4. `services/pricing.py` — таблица + функция `calculate_cost_rub(model_id, tokens_in, tokens_out) -> Decimal`.
5. **`POST /v1/chat/completions`** non-streaming:
   - Принимает OpenAI-формат + `metadata: {project_id, message_id}`, `user`.
   - Дёргает LiteLLM `acompletion(...)`.
   - Считает стоимость → пишет в `usage` через `usage_logger.py`.
   - Возвращает OpenAI-совместимый JSON.
6. **`GET /v1/models`** — возвращает список с ценами в рублях.
7. **`GET /health`** — `{"status": "ok"}`.

**Definition of Done M0:**
- `curl POST :8001/v1/chat/completions` с `claude-sonnet-4-6` возвращает ответ.
- В Postgres.usage появилась строка с правильными tokens и cost_rub.
- `pytest tests/test_chat_non_streaming.py tests/test_pricing.py` зелёный.

### M1 — SSE стриминг (день 3–5)

**Задачи:**
1. **`POST /v1/chat/completions`** с `stream: true`:
   - Использовать `litellm.acompletion(stream=True)`.
   - Отдавать через `sse_starlette.EventSourceResponse`:
     - На каждый chunk → `data: {"choices":[{"delta":{"content":"..."}}]}`.
     - В конце → подсчёт токенов (через `tiktoken` + finalChunk usage если есть) → `data: [DONE]`.
   - **Корректная отмена:** если клиент дисконнект (`request.is_disconnected()`) — прервать LiteLLM запрос (через cancel task) и НЕ списывать оплату за неотданный хвост.
2. **Backpressure:** asyncio.Queue размером 64, чтобы не залить медленного клиента.
3. Перед стримингом — посчитать input-токены (`tiktoken` для openai/anthropic, для yandex — берём по символам: ~1 token / 4 chars).
4. После стрима — записать `usage` атомарно: `INSERT INTO usage (...)`.

**Definition of Done M1:**
- `curl -N` стримит ответ символ-за-символом.
- Дисконнект клиента — нет призрачных списаний (проверить, что usage пишется только за реально отданные токены).
- `pytest tests/test_chat_streaming.py` зелёный.

### M2 — Кеш + safety (день 6–9)

**Задачи:**
1. **`services/cache.py`:**
   - Ключ = `sha256(model + system_prompt + last_user_message)`.
   - TTL 1 час.
   - **Кэш только non-streaming** (стримить из кэша = неестественно для UX; пропускаем).
   - Hit-rate метрика → лог.
2. **`services/safety.py`** — простой regex-фильтр на:
   - `ignore (all )?previous instructions`
   - `system:` в user-сообщении
   - `</file>` в пользовательском промпте (попытка инъекции файла напрямую)
   - длинные base64-блоки (>1k символов).
   - Если триггер — заменить на `[фильтровано]` И залогировать.
3. Применять фильтр **только** к user-сообщениям, не к system.
4. Опционально — конфиг включить/выключить через ENV `SAFETY_FILTER_ENABLED=true`.

**Definition of Done M2:**
- Повторный запрос с теми же messages — приходит из кэша за <50ms.
- Попытка инъекции `Ignore previous instructions and...` — фильтруется.
- `pytest tests/test_cache.py tests/test_safety.py` зелёный.

### M3 — Биллинг + fallbacks + observability (день 10–14)

**Задачи:**
1. **`services/billing.py`:**
   - Функция `reserve_and_charge(user_id, message_id, model_id, tokens_in, tokens_out, cost_rub)`:
     - Транзакция в общей БД с B: `UPDATE wallets SET balance_rub = balance_rub - cost_rub WHERE user_id = ? AND balance_rub >= cost_rub` → если RowCount=0, бросить `WalletEmptyError`.
     - `INSERT INTO usage` + `INSERT INTO wallet_charges`.
   - Перед запросом к модели — pre-check баланса (отказ с 402 `wallet_empty`, если `< min_threshold`).
2. **Fallbacks через LiteLLM Router:**
   - Если основная модель отвечает 5xx или превышает timeout 60s — пробуем следующую.
   - В ответе указываем `metadata.actual_model_used`, чтобы B мог логировать.
3. **Логи в JSON-line файл `logs/llm-{date}.jsonl`** (с rotation):
   - Каждый запрос: `timestamp, user_id, project_id, message_id, model, tokens_in, tokens_out, cost_rub, cache_hit, fallback_used`.
   - **Без content** (PII).
4. **`/v1/models`** — добавить поле `available: bool` (проверка ключей в env).
5. **Smoke тест в M3:** прогнать 10 запросов с разными моделями, проверить, что usage и wallet_charges консистентны.

**Definition of Done M3:**
- При балансе ниже стоимости запроса возвращается 402 `wallet_empty`, ничего не списывается.
- При выпадении основного провайдера автоматически срабатывает fallback.
- Логи пишутся, ротация работает.
- E2E тест: B шлёт запрос → C стримит → C списывает → B видит обновлённый баланс.

## Согласование с агентом B (важно)

**Где живёт списание?**

Вариант 1 (рекомендуется): **C ходит напрямую в общую Postgres** (та же БД, что у B). У него только write-доступ к таблицам `usage`, `wallet_charges`, `wallets`. Это атомарно и просто.

Вариант 2: **C дёргает internal endpoint у B** (`POST /api/internal/billing/charge`). Сложнее (ещё один HTTP), но строже разделение.

**Решение для MVP — вариант 1.** Создать миграции — задача B (в M0/M3), но C должен иметь доступ к `core.db` для записи. Просто переиспользует `models/usage.py` или пишет сырым SQL.

## Команды

```bash
cd apps/llm-gateway
uv sync
cp .env.example .env

uv run uvicorn omnia_gateway.main:app --reload --port 8001

uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy src/
```

## Безопасность

- **API-ключи провайдеров** — только в `.env`, не в логах, не в ответах.
- **Кэш** — без user_id в ключе (один ответ для одинаковых вопросов от разных users — экономия).
- **Логи** не содержат content промптов и ответов (PII).
- **Pre-flight cost check** обязателен до отправки в LLM (защита от пустых кошельков и DoS).

## Что НЕ делать

- Не писать собственную реализацию роутера моделей — LiteLLM это делает.
- Не писать свою токенизацию — `tiktoken` + LiteLLM отдают usage.
- Не дёргать LLM-провайдеров напрямую через httpx без LiteLLM (исключение — YandexGPT, если в LiteLLM нет).
- Не возвращать stream-формат, отличный от OpenAI SSE — иначе B придётся переписывать клиент.
- Не дублировать логику биллинга в B и в C — она ровно в одном месте (по согласованию выше — в C).

## Координация

- Если B ещё не написал миграции `usage` и `wallet_charges` — пиши SQL сам через `init.sql`, его потом B перепишет в Alembic.
- Если backend ещё не дёргает /chat — тестируй через curl и в `tests/`.

## Старт

В новом чате Claude в `C:\Бизнес план\omnia-mvp\`:
> Прочитай `agents/AGENT-C-LLM-GATEWAY.md`, `docs/01-api-contract.md` (секция LLM Gateway internal), `docs/02-data-model.md`. Активируй skill code-canon. Начинай с M0.
