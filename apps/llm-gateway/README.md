# apps/llm-gateway — LLM Gateway (FastAPI + LiteLLM)

Зона ответственности **агента C**. Перед стартом прочитай:

1. [`/CLAUDE.md`](../../CLAUDE.md)
2. [`/agents/AGENT-C-LLM-GATEWAY.md`](../../agents/AGENT-C-LLM-GATEWAY.md)
3. [`/docs/01-api-contract.md`](../../docs/01-api-contract.md) — секция «LLM Gateway internal API»
4. [`/docs/02-data-model.md`](../../docs/02-data-model.md) — таблицы `usage`, `wallet_charges`

## Быстрый старт

```bash
# Postgres + Redis нужны (запускает агент B через infra/docker-compose.yml)
cd apps/llm-gateway
uv sync
cp .env.example .env
# Внести API-ключи провайдеров
uv run uvicorn omnia_gateway.main:app --reload --port 8001
```

## Env

| Переменная | Значение |
|---|---|
| `ANTHROPIC_API_KEY` | sk-ant-... |
| `OPENAI_API_KEY` | sk-... |
| `YANDEX_API_KEY` | (от Yandex Cloud) |
| `YANDEX_FOLDER_ID` | (от Yandex Cloud) |
| `OPENROUTER_API_KEY` | (для Qwen через OpenRouter) |
| `DATABASE_URL` | `postgresql+asyncpg://omnia:omnia@localhost:5432/omnia` (та же, что у `apps/api`) |
| `REDIS_URL` | `redis://localhost:6379/1` |
| `DEFAULT_MODEL` | `claude-sonnet-4-6` |
| `SAFETY_FILTER_ENABLED` | `true` |
| `CACHE_TTL_SECONDS` | `3600` |
| `MIN_BALANCE_RUB` | `5.0` |

## Команды

```bash
uv run uvicorn omnia_gateway.main:app --reload --port 8001
uv run pytest -q
uv run ruff check . && uv run ruff format .
uv run mypy src/
```

## Endpoints (резюме)

- `POST /v1/chat/completions` — OpenAI-совместимый, поддерживает `stream`
- `GET /v1/models` — список моделей с RUB-ценами
- `GET /health`

Структура и фазы — в `agents/AGENT-C-LLM-GATEWAY.md`.
