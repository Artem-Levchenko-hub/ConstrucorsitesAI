# Omnia.AI

> Пиши промпты, получай готовый сайт. С backend, доменом, деплоем и кнопкой «вернуться назад» для каждого промпта. Всё в рублях, всё на одной платформе.

## Структура монорепо

```
├── apps/
│   ├── web/              Next.js — лендинг + workspace UI
│   ├── api/              FastAPI — проекты, генерация, snapshots, preview, WebSocket
│   ├── llm-gateway/      FastAPI + LiteLLM — прокси к LLM; deploy/full — production-compose
│   ├── orchestrator/     runtime пользовательских проектов, шаблоны
│   ├── agent-runner/     доверенный runner Project Cell (каркас)
│   └── landing/          слепок production-лендинга и nginx-конфигов
├── docs/                 архитектура, контракты, спецификации, операционные записи
├── agents/               брифы по зонам ответственности
├── infra/                dev-стек (docker-compose), релиз, бэкап, Project Cell
├── otchet/               отчёт о прогрессе
└── secondbrain/          справочная память проекта
```

**Правила работы** — [`AGENTS.md`](AGENTS.md) (единый регламент: Git-процесс через PR, проверки,
безопасность данных, выпуск, критерии готовности). `CLAUDE.md` импортирует его для Claude Code.

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

- [Общий регламент](AGENTS.md)
- [Архитектура](docs/00-architecture.md)
- [API контракт](docs/01-api-contract.md)
- [Data model](docs/02-data-model.md)
- [Дизайн-система](docs/03-design-system.md)
- [Правила генерации](docs/04-generation-rules.md)
- [Проверка и выпуск платформы](infra/release/README.md)

## Инструменты

Базовый цикл (правка → проверки → PR) выполняется без личных плагинов и памяти.

| Категория | Что | Назначение и поведение при отсутствии |
|---|---|---|
| Обязательные для проекта | Git, GitHub (PR), Docker + Compose; Python 3.12 + `uv` (api, gateway, orchestrator); Node + `pnpm` 9.15.0 (web) | Нужны для проверок из раздела 6 [`AGENTS.md`](AGENTS.md) и CI [`.github/workflows/ci.yml`](.github/workflows/ci.yml). Нужен только набор для затронутого компонента. |
| Необязательные помощники | `code-canon`, `/safe-commit`, `/canon-review`, Superpowers, `multi-chat-coord`, `claude-session-driver`, `project-router`, дизайн-скиллы, MCP (Mobbin, Playwright и др.) | Не входят в репозиторий, версии не зафиксированы. Если есть — могут помогать; если нет — выполняются переносимые процедуры `AGENTS.md`: стандарты кода — раздел 2, «безопасный коммит» и «канон-ревью» — проверки и review diff из раздела 6, координация — отдельные ветки/worktree и явное согласование общего файла (раздел 4). |
| Настройки агента в репозитории | [`.claude/settings.json`](.claude/settings.json) (хуки SecondBrain, нужен `uv`), [`.claude/launch.json`](.claude/launch.json) (предпросмотры, часть — через личный SSH-алиас) | Необязательны для работы; при сбое хука общие правила продолжают действовать. См. [`CLAUDE.md`](CLAUDE.md). |
| Локальные настройки пользователя | `.claude/settings.local.json`, режимы разрешений, личная память агента, SSH-алиасы, пути к локальным файлам, расписания (scheduled tasks), `.omc/` (состояние скилла ultragoal) | Остаются на машине пользователя, не являются требованием проекта и не расширяют полномочий. |

## Команда

- **Артём Левченко** — продукт, бизнес, маркетинг
- **Рома Исакин** — техлид, AI, ops

## Лицензия

Proprietary © 2026 Omnia.AI
