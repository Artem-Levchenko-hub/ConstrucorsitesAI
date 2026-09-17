# Агент C — LLM Gateway (apps/llm-gateway/)

> **Статус брифа.** Зона ответственности и особенности компонента. Общие правила — Git-процесс через PR,
> проверки, безопасность данных, выпуск и критерии готовности — в [`AGENTS.md`](../AGENTS.md).
> Стек и структура ниже — исходный замысел MVP (май 2026): перед опорой на них сверяй с кодом.

При работе в зоне читай `docs/01-api-contract.md` (раздел «LLM Gateway internal API») и `docs/02-data-model.md` (таблицы `usage`, `wallet_charges`).

## Кто ты в этой команде

Ты пишешь **умный прокси к LLM-провайдерам**. Принимаешь OpenAI-совместимые запросы от backend (агент B) на `:8001`, проксируешь в Anthropic / OpenAI / YandexGPT / Alibaba (Qwen) через **LiteLLM**, считаешь токены и стоимость в рублях, кэшируешь часто-повторяющиеся ответы.

Ты — изолированный сервис. Никто кроме B тебя не дёргает. Frontend (агент A) не знает о твоём существовании.

## Зона ответственности

- Ответственность — `apps/llm-gateway/`. Здесь же лежит production-compose `deploy/full/`: его правка — runtime-изменение (раздел 7 `AGENTS.md`), выпуск — по `infra/release/README.md`.
- Правка других зон — по правилам раздела 1 `AGENTS.md` (нужна задаче, указана в PR, согласована).
- Контракт (`docs/01-api-contract.md`) меняется в том же PR, что реализация и тесты, с согласия ответственного за backend (агент B).

## Стек

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

## Поддерживаемые модели (MVP, историческая таблица — актуальный список в коде)

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

## Цены в рублях (на момент MVP, май 2026; историческая таблица — актуальные цены в `pricing.py`)

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

## История: исходный план фаз

План M0–M3 времён MVP — исторический; отметки и «Definition of Done» оттуда не описывают текущее
состояние и не обязательны к чтению при старте. Полный текст — в Git: `git show d5088222:agents/AGENT-C-LLM-GATEWAY.md`.

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

# проверки (обязательный набор — как в CI, раздел 6 AGENTS.md)
uv run --frozen --extra dev pytest -q
uv run ruff check . && uv run mypy src/   # дополнительно
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
