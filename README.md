# Omnia.AI

> Пиши промпты, получай готовый сайт. С backend, доменом, деплоем и кнопкой «вернуться назад» для каждого промпта. Всё в рублях, всё на одной платформе.

## Структура монорепо

```
omnia-mvp/
├── apps/
│   ├── web/              Next.js 15 — лендинг + workspace UI
│   ├── api/              FastAPI — проекты, snapshots, preview, WebSocket
│   └── llm-gateway/      FastAPI + LiteLLM — прокси к LLM-провайдерам
├── docs/                 Single source of truth для всех 3 агентов
├── agents/               Брифы для параллельной разработки
└── infra/                docker-compose для локалки
```

## Быстрый старт (для разработчика)

```bash
# 1. Поднять инфраструктуру (Postgres, Redis, MinIO)
cd infra && docker compose up -d

# 2. Backend
cd apps/api && uv sync && uv run alembic upgrade head && uv run uvicorn main:app --reload --port 8000

# 3. LLM Gateway
cd apps/llm-gateway && uv sync && uv run uvicorn main:app --reload --port 8001

# 4. Frontend
cd apps/web && pnpm install && pnpm dev   # → http://localhost:3000
```

## Документы

- [Архитектура](docs/00-architecture.md)
- [API контракт](docs/01-api-contract.md)
- [Data model](docs/02-data-model.md)
- [Дизайн-система](docs/03-design-system.md)

## Команда

- **Артём Левченко** — продукт, бизнес, маркетинг
- **Рома Исакин** — техлид, AI, ops

## Лицензия

Proprietary © 2026 Omnia.AI
