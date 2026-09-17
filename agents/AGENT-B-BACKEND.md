# Агент B — Backend (apps/api/)

> **Статус брифа.** Зона ответственности и особенности компонента. Общие правила — Git-процесс через PR,
> проверки, безопасность данных, выпуск и критерии готовности — в [`AGENTS.md`](../AGENTS.md).
> Стек и структура ниже — исходный замысел MVP (май 2026): перед опорой на них сверяй с кодом.

При работе в зоне читай `docs/01-api-contract.md` (контракт) и `docs/02-data-model.md` (схема БД).

## Кто ты в этой команде

Ты пишешь **ядро бэкенда Omnia.AI** — FastAPI-сервис на `:8000`. Твоя зона:
- Auth (регистрация, логин, JWT)
- CRUD проектов
- Snapshot'ы через `pygit2` поверх MinIO
- Playwright preview-worker (через Redis Queue)
- WebSocket-push событий клиенту
- Прокси к LLM Gateway: ты получаешь от A `POST /prompt` → формируешь контекст → дёргаешь LLM Gateway (агент C) → парсишь ответ → коммитишь файлы → создаёшь snapshot → пушишь в WS

Параллельно работают:
- **Агент A** (frontend) — потребитель твоего API.
- **Агент C** (LLM Gateway) — отдельный сервис на `:8001`. Ты дёргаешь его по HTTP.

## Зона ответственности

- Ответственность — `apps/api/` (включая промпты генерации `src/omnia_api/services/prompt_builder.py`, `max_data_evolution.py` — это runtime-код, не документация). `infra/` с V2 — зона агента D.
- Правка других зон — по правилам раздела 1 `AGENTS.md` (нужна задаче, указана в PR, согласована).
- Контракт API и схема БД (`docs/01-api-contract.md`, `docs/02-data-model.md`) меняются в том же PR, что реализация, миграции и тесты, с согласия ответственных за затронутые компоненты (A — web, C — gateway, D — orchestrator).

## Стек

- **Python 3.12**, менеджер — `uv` (быстрый, lock-файл).
- **FastAPI 0.115+** (async)
- **uvicorn** для разработки, **gunicorn + uvicorn workers** в проде.
- **SQLAlchemy 2.0** (async) + **asyncpg**.
- **Alembic** для миграций.
- **Redis 7** через `redis-py` (asyncio).
- **RQ** (Redis Queue) для preview-задач (синхронный воркер — Playwright всё равно запускает chromium).
- **MinIO**: `minio-py` SDK.
- **pygit2** (libgit2 биндинг) для git-операций.
- **Playwright** (Python). Headless Chromium.
- **python-jose[cryptography]** для JWT, **passlib[bcrypt]** для паролей.
- **pydantic v2** для схем.
- **structlog** для логов.
- **pytest + pytest-asyncio** для тестов.

## Структура `apps/api/`

```
apps/api/
├── pyproject.toml
├── uv.lock
├── .env.example
├── README.md
├── alembic.ini
├── migrations/
│   ├── env.py
│   └── versions/
│       ├── 0001_initial.py
│       ├── 0002_projects_snapshots.py
│       └── 0003_billing.py
├── src/
│   └── omnia_api/
│       ├── __init__.py
│       ├── main.py                       (FastAPI app, lifespan, middleware)
│       ├── core/
│       │   ├── config.py                 (pydantic-settings, чтение .env)
│       │   ├── db.py                     (engine, session factory)
│       │   ├── redis.py                  (redis client, pubsub helpers)
│       │   ├── minio.py                  (s3 client wrapper)
│       │   ├── security.py               (JWT, password hashing)
│       │   ├── deps.py                   (get_current_user, get_db, etc.)
│       │   └── errors.py                 (ApiError class, exception handlers)
│       ├── models/                       (SQLAlchemy ORM)
│       │   ├── user.py
│       │   ├── project.py
│       │   ├── snapshot.py
│       │   ├── message.py
│       │   ├── wallet.py
│       │   └── usage.py
│       ├── schemas/                      (Pydantic)
│       │   ├── user.py
│       │   ├── project.py
│       │   ├── snapshot.py
│       │   ├── message.py
│       │   └── common.py
│       ├── routers/
│       │   ├── auth.py
│       │   ├── projects.py
│       │   ├── snapshots.py
│       │   ├── messages.py               (POST /prompt + GET messages)
│       │   ├── wallet.py
│       │   ├── models_router.py          (GET /api/models — proxy to gateway)
│       │   ├── public.py                 (/p/:slug/* — статика)
│       │   └── ws.py                     (WebSocket hub)
│       ├── services/
│       │   ├── repo.py                   (pygit2 + MinIO bare repo)
│       │   ├── file_extractor.py         (парсер AI-ответа <file path="...">...</file>)
│       │   ├── prompt_builder.py         (формирует контекст для LLM)
│       │   ├── llm_client.py             (httpx-обёртка на :8001)
│       │   ├── ws_hub.py                 (множество WS соединений по project_id)
│       │   └── billing.py                (списания)
│       ├── workers/
│       │   ├── __init__.py
│       │   ├── preview.py                (RQ job: Playwright render → MinIO)
│       │   └── run.py                    (entrypoint: rq worker)
│       └── templates/                    (стартовые шаблоны для new project)
│           ├── blank/index.html
│           ├── landing/                  (index.html, style.css)
│           ├── portfolio/...
│           └── blog/...
└── tests/
    ├── conftest.py                       (test DB, factories)
    ├── test_auth.py
    ├── test_projects.py
    ├── test_snapshots.py
    └── test_e2e.py                       (полный flow: register → project → prompt → snapshot → rollback)
```

## История: исходный план фаз

План M0–M3 времён MVP — исторический; отметки и «Definition of Done» оттуда не описывают текущее
состояние и не обязательны к чтению при старте. Полный текст — в Git: `git show d5088222:agents/AGENT-B-BACKEND.md`.

## Команды

```bash
cd apps/api
uv sync
cp .env.example .env

# первая инициализация
uv run alembic upgrade head

# dev
uv run uvicorn omnia_api.main:app --reload --port 8000
uv run rq worker omnia-previews              # отдельный терминал

# проверки (обязательный набор — как в CI, раздел 6 AGENTS.md; БД и Redis — одноразовые)
uv sync --frozen
uv run ruff check . && uv run mypy src
uv run --frozen pytest
```

## Безопасность

- **bcrypt** 12 rounds.
- **JWT** HS256, секрет в `.env`, не коммитим.
- **Path traversal** — обязательная санитизация в `file_extractor.py`.
- **SQL injection** — только параметризованные запросы (SQLAlchemy сам).
- **Rate limit** на login/register и prompt.
- **CORS** — разрешить только `localhost:3000` в dev и домен прода.

## Координация

- Если LLM Gateway (агент C) не готов — мокаем `services/llm_client.py` через флаг `MOCK_LLM=true`. Возвращаем заранее заготовленный ответ с одним файлом `index.html`.
- Если фронт ещё не готов — тестируем через curl/Postman/pytest.
