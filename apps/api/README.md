# apps/api — Backend Core (FastAPI)

Зона ответственности **агента B**. Перед стартом прочитай:

1. [`/CLAUDE.md`](../../CLAUDE.md)
2. [`/agents/AGENT-B-BACKEND.md`](../../agents/AGENT-B-BACKEND.md)
3. [`/docs/01-api-contract.md`](../../docs/01-api-contract.md)
4. [`/docs/02-data-model.md`](../../docs/02-data-model.md)

## Быстрый старт

```bash
# Сначала поднять инфру (Postgres + Redis + MinIO)
cd ../../infra
docker compose up -d

# Бэкенд
cd ../apps/api
uv sync
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn omnia_api.main:app --reload --port 8000
```

Воркер preview-рендера в отдельном терминале:

```bash
uv run rq worker omnia-previews
```

## Env

| Переменная | Значение в dev |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://omnia:omnia@localhost:5432/omnia` |
| `REDIS_URL` | `redis://localhost:6379/0` |
| `MINIO_ENDPOINT` | `localhost:9000` |
| `MINIO_ACCESS_KEY` | `omnia` |
| `MINIO_SECRET_KEY` | `omnia-secret` |
| `MINIO_BUCKET_PROJECTS` | `projects` |
| `MINIO_BUCKET_PREVIEWS` | `previews` |
| `JWT_SECRET` | `openssl rand -hex 32` |
| `LLM_GATEWAY_URL` | `http://localhost:8001` |
| `MOCK_LLM` | `false` (true — если C ещё не готов) |
| `CORS_ORIGINS` | `http://localhost:3000` |

## Команды

```bash
uv run uvicorn omnia_api.main:app --reload --port 8000
uv run rq worker omnia-previews
uv run alembic revision --autogenerate -m "msg"
uv run alembic upgrade head
uv run pytest -q
uv run ruff check . && uv run ruff format .
uv run mypy src/
```

Структура и фазы — в `agents/AGENT-B-BACKEND.md`.
