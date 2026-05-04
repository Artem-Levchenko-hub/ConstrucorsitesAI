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
│  │ • /projects/:id/prompt    — отправить промпт            │    │
│  │ • /projects/:id/snapshots — список версий               │    │
│  │ • /projects/:id/rollback  — откат                       │    │
│  │ • /ws/projects/:id        — push событий                │    │
│  └─────────────────────────────────────────────────────────┘    │
│         │              │              │              │          │
│         ▼              ▼              ▼              ▼          │
│  ┌──────────┐  ┌──────────────┐ ┌────────┐  ┌───────────────┐   │
│  │ Postgres │  │   pygit2     │ │ Redis  │  │  Playwright   │   │
│  │ метаданные│  │ git-снапшоты │ │очередь │  │ preview-render│   │
│  │ (alembic)│  │   → MinIO    │ │ (RQ)   │  │ headless      │   │
│  └──────────┘  └──────────────┘ └────────┘  └───────┬───────┘   │
│                                                     ▼           │
│                                              ┌──────────┐       │
│                                              │  MinIO   │       │
│                                              │ (S3-comp)│       │
│                                              │ snapshots│       │
│                                              │ + preview│       │
│                                              │   .png   │       │
│                                              └──────────┘       │
└─────────┬───────────────────────────────────────────────────────┘
          │ POST /v1/chat/completions (OpenAI-compatible)
          ▼
┌─────────────────────────────────────────────────────────────────┐
│  LLM GATEWAY  (apps/llm-gateway, FastAPI :8001)                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ • LiteLLM proxy (Claude, GPT, YandexGPT, Qwen)          │    │
│  │ • Кеш промптов в Redis (по hash системного prompt+user) │    │
│  │ • Учёт токенов → Postgres.usage                         │    │
│  │ • Rate limit (per user, per model)                      │    │
│  │ • Sanitize / prompt injection guard                     │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────┬───────────────────────────────────────────────────────┘
          │ HTTPS
          ▼
   ┌──────────────────────────────────────┐
   │  Anthropic / OpenAI / Yandex / Qwen  │
   └──────────────────────────────────────┘
```

## Главный поток (happy path)

**Сценарий:** пользователь пишет промпт «Сделай лендинг для пиццерии».

| # | Где | Что происходит | Время |
|---|-----|----------------|-------|
| 1 | Web | `POST /api/projects/:id/prompt` с `{prompt, model_id}` | мгновенно |
| 2 | API | Валидирует JWT → вычитывает текущее состояние проекта из git (HEAD) | <50ms |
| 3 | API | Формирует system prompt + контекст файлов → `POST llm-gateway:8001/v1/chat/completions` | <50ms |
| 4 | LLM Gateway | Кеш-чек → если miss → отправка в Anthropic/OpenAI → стриминг ответа | 5–30s |
| 5 | LLM Gateway | По завершении: учёт токенов → списание из `wallets` → ответ обратно | <100ms |
| 6 | API | Парсит ответ AI: ожидаются файлы в формате `<file path="...">...</file>` | <50ms |
| 7 | API | `pygit2` применяет изменения → создаёт коммит с метаданными промпта | <100ms |
| 8 | API | Энкью-ит задачу `render_preview(snapshot_id)` в Redis Queue | <10ms |
| 9 | API | Через WebSocket пушит `snapshot.created` клиенту | мгновенно |
| 10 | Worker | Playwright запускает headless Chromium → грузит проект → screenshot | 2–5s |
| 11 | Worker | PNG → MinIO `previews/{snapshot_id}.png` → URL в Postgres | <500ms |
| 12 | Worker | Через WebSocket пушит `preview.ready` с URL картинки | мгновенно |
| 13 | Web | Обновляет ленту версий — добавляет миниатюру | мгновенно |

## Откат к версии (1 клик)

| # | Где | Что |
|---|-----|-----|
| 1 | Web | `POST /api/projects/:id/rollback` `{snapshot_id}` |
| 2 | API | Создаёт safety-snapshot (на случай отмены отката) |
| 3 | API | `git reset --hard <target_commit>` через pygit2 |
| 4 | API | Энкью-ит preview-render для нового HEAD |
| 5 | API | WebSocket `snapshot.created` (новая версия после отката) |
| 6 | Web | Обновляет timeline, ставит активный маркер |

## Live preview (как пользователь видит сайт прямо сейчас)

Каждый проект имеет публичный URL: `https://{slug}.preview.omnia.ai` (в проде) или `http://localhost:8000/p/{slug}` (локально).

API endpoint `GET /p/{slug}/*` отдаёт статику текущего HEAD проекта. Слот для будущих интеграций (deploy на Vercel-аналог) — сейчас просто статика из git.

## Изоляция и безопасность

- **Каждый проект = отдельный git-репо в MinIO bucket `projects/{project_id}/`** — namespace изоляция.
- **JWT с `project_id` в claims** проверяется на каждый snapshot/rollback.
- **AI-сгенерированный код НЕ исполняется на наших серверах** — только статика отдаётся как preview.
- **LLM-промпты sanitized** через простой regex на «ignore previous instructions» / «system:» и т.п. в LLM Gateway.
- **Rate limit:** 10 prompts/минуту на пользователя, 100/час.

## Что хранится где

| Данные | Хранилище | Зачем именно тут |
|---|---|---|
| Пользователи, проекты, метаданные снапшотов, кошельки, usage | Postgres | Транзакции, JOIN'ы, индексы |
| Файлы проектов (git-объекты) | MinIO bucket `projects/` | S3-совместимо, дешевле БД для blob'ов |
| PNG-превью | MinIO bucket `previews/` | То же |
| Очередь preview-задач | Redis | Лёгкая, быстрая, RQ |
| Кеш LLM-ответов | Redis | TTL 1ч, ключ = SHA256(system+user) |
| Сессии (refresh-токены) | Redis | Низкая латентность |

## Внутренние сервисы — как они общаются

- **web → api**: REST (HTTP/JSON) + WebSocket (для push)
- **api → llm-gateway**: REST (внутренний docker network), без авторизации (но network-isolated)
- **api → Postgres / Redis / MinIO**: прямые драйверы
- **api workers (RQ)**: подписаны на ту же Redis-очередь

## Среды

| Среда | Где | Когда |
|---|---|---|
| `dev` | localhost (docker-compose) | разработка |
| `staging` | VPS Serverum (поддомен `staging.omnia.ai`) | тесты с реальным LLM, перед релизом |
| `prod` | VPS Serverum (`omnia.ai`) | боевой |

В MVP реально настраиваем только **dev** + первый набросок **staging** (без CI/CD, ручной деплой).

## Что осознанно НЕ делаем в MVP

- **Kubernetes** — Docker Compose + Ansible достаточно до 1000 пользователей
- **Микросервисы** дальше 3-х (web/api/llm-gateway) — следующий распил после Series A
- **Свой git-сервер вместо libgit2** — pygit2 in-process хватает
- **Кастомный LLM-router** — LiteLLM закрывает 95%, своё писать после Seed
- **Realtime collab** — пока 1 пользователь = 1 проект
