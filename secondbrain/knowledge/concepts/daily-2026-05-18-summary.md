---
title: "Daily Summary 2026-05-18"
tags: [corporate-messanger, daily, fallback]
sources:
  - daily/2026-05-18.md
created: 2026-05-18
updated: 2026-05-18
---

# Daily Summary 2026-05-18

Compiled fallback summary for 2026-05-18.md

## Key Points

- `README.md`
- [Архитектура](docs/00-architecture.md)
- [API контракт](docs/01-api-contract.md)
- [Data model](docs/02-data-model.md)
- [Дизайн-система](docs/0
- `docs/00-architecture.md`
- `docs/01-api-contract.md`
- **Префиксы:** все REST endpoints публичного API под `/api/*`. Public preview — `/p/*`. WebSocket — `/api/ws/*`.

## Details

# Daily Log: 2026-05-18

## Sessions

## Docs Ingest

## Memory Maintenance

### Docs Ingest (14:25)

Project root: `D:\Бизнес план\Конструктор\ConstrucorsitesAI`


**Changed markdown files:**
- `README.md`

#### Source: README.md
```markdown
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
- [Дизайн-система](docs/0
...(truncated)
```

- `docs/00-architecture.md`

#### Source: docs/00-architecture.md
```markdown
# 00. Архитектура Omnia.AI MVP

## Обзор системы

```
┌─────────────────────────────────────────────────────────────────┐
│  БРАУЗЕР (пользователь)                                         │
│  ┌───────────────┐                                              │
│  │  Next.js 15   │  Landing + Workspace UI                      │
│  │  (apps/web)   │  React 19, Tailwind, shadcn/ui, framer       │
│  └───────┬───────┘                                              │
└──────────┼──────────────────────────────────────────────────────┘
           │ REST (fetch) + WebSocket (snapshot.created, preview.ready)
           ▼
┌─────────────────────────────────────────────────────────────────┐
│  CORE BACKEND  (apps/api, FastAPI :8000)                        │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ Endpoints (см. docs/01-api-contract.md)                 │    │
│  │ • /auth/*       — регистрация, JWT-логин                │    │
│  │ • /projects/*   — CRUD проектов                         │    │
│  │ • /projects/:id/prompt    — от
...(truncated)

## Related Concepts

- [[knowledge/concepts/secondbrain-runtime]]
- [[knowledge/concepts/daily-ingestion-process]]

## Sources

- [[daily/2026-05-18.md]]
