# Агент B — Backend (apps/api/) + базовая инфраструктура

Прочитай этот бриф, затем `docs/01-api-contract.md` (контракт), `docs/02-data-model.md` (схема БД). После — приступай к M0.

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

## Жёсткие границы

- **ПИШЕШЬ ТОЛЬКО в `apps/api/` и `infra/`.**
- **НЕ ЛЕЗЬ в `apps/web/` и `apps/llm-gateway/`.**
- Контракт API — единственный источник правды. Если нужна правка — записка в `~/.claude/coordination/omnia-mvp/inbox/` + продолжай с прежним контрактом.

## Стек (фиксированный)

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

## Фазы

### M0 — Инфра + auth (день 1–2)

**Задачи:**
1. **`infra/docker-compose.yml`:** services — `postgres-16`, `redis-7`, `minio` (с consoleAddress :9001) + healthchecks. Volume для каждого. Сеть `omnia-net`.
2. `infra/.env.example` со всеми переменными (POSTGRES_PASSWORD, MINIO_ROOT_USER, etc.).
3. **`apps/api/`:** `uv init`, `pyproject.toml` со всеми зависимостями.
4. `core/config.py`: `Settings(BaseSettings)` — читает `.env`, валидирует.
5. `core/db.py`: async engine, `get_session()` зависимость.
6. **Миграция `0001`:** включить расширения `citext`, `uuid-ossp`. Создать `users`, `wallets`. Триггер `set_updated_at` (см. `docs/02`).
7. **Auth router:**
   - `POST /api/auth/register` → создаёт user + wallet (балланс 100₽) в одной транзакции, возвращает user, ставит JWT cookie.
   - `POST /api/auth/login` → верифицирует пароль, обновляет `last_login_at`, ставит cookie.
   - `POST /api/auth/logout` → удаляет cookie.
   - `GET /api/auth/me` → возвращает текущего user.
8. `core/security.py`: bcrypt-хэширование, JWT (HS256, 7 дней), `create_access_token`, `decode_access_token`.
9. `core/deps.py`: `get_current_user` → читает cookie или Authorization header → декодирует → возвращает User или 401.

**Definition of Done M0:**
- `docker compose up` поднимает Postgres + Redis + MinIO без ошибок.
- `uv run alembic upgrade head` создаёт схему.
- `uv run uvicorn omnia_api.main:app --reload` стартует на :8000.
- `pytest tests/test_auth.py` зелёный (register → login → me → logout).

### M1 — Проекты + snapshots (день 3–6)

**Задачи:**
1. **Миграция `0002`:** `projects`, `snapshots`, `messages` (см. `docs/02`).
2. **`services/repo.py`** — самый ответственный сервис:
   - `init_repo(project_id, template) -> commit_sha`: создаёт bare repo в MinIO bucket `projects/`, копирует шаблон из `templates/`, делает initial commit «Initial: <template>».
   - `commit_files(project_id, files: dict[path, content], message, parent_sha) -> commit_sha`: достаёт bare repo из MinIO в temp dir, применяет файлы, коммитит, пушит обратно в MinIO. **Использовать `pygit2.Repository`, `Index`, `Signature`.** Очищать temp dir в `finally`.
   - `read_files(project_id, commit_sha) -> dict[path, content]`: то же, но без записи. Лимит — 100 файлов, иначе ошибка.
   - `checkout(project_id, commit_sha) -> new_commit_sha`: создаёт новый коммит на основе указанного, родитель — текущий HEAD (это и есть rollback — он не уничтожает историю).
3. **Path-санитизация:** в `file_extractor.py` отфильтровывать пути с `..`, абсолютные, начинающиеся с `/`, `~`, `.git/`. Максимум 2 МБ на файл.
4. **Шаблоны** (`templates/`): минимум `blank` (`index.html` с `<h1>Здравствуй, мир</h1>`), `landing` (готовая лендинг-болванка с tailwind via CDN — нам же не надо собирать), `portfolio`, `blog`.
5. **Projects router:**
   - `POST /api/projects` → транзакция: insert project → init_repo → insert initial snapshot → update `current_snapshot_id`. Slug — `slugify(name) + '-' + short_id`.
   - `GET /api/projects` → свои.
   - `GET /api/projects/:id`, `DELETE /api/projects/:id` (cascade удаляет snapshots, messages; bucket очищать в фоне).
6. **Snapshots router:**
   - `GET /api/projects/:id/snapshots` (DESC).
   - `GET /api/projects/:id/snapshots/:sid` (включая files via `repo.read_files`).
7. **Public preview:** `routers/public.py` — `GET /p/:slug` и `/p/:slug/*` отдают статику из `read_files(current_snapshot_id)` с правильными mime-типами. Без auth.

**Definition of Done M1:**
- Создание проекта работает, инит-snapshot существует.
- Можно вручную (через curl + jwt) сделать `commit_files` через какой-то test endpoint, потом получить snapshot и его files.
- `/p/:slug` отдаёт `index.html` из шаблона.
- `pytest tests/test_projects.py tests/test_snapshots.py` зелёный.

### M2 — Preview worker + WebSocket (день 7–10)

**Задачи:**
1. **`workers/preview.py`** — RQ job-функция `render_preview(snapshot_id)`:
   - Прочитать snapshot и его файлы.
   - Записать во временную папку.
   - Запустить Playwright headless Chromium → открыть `index.html` → screenshot 1280×800 PNG.
   - Сохранить в MinIO: `previews/{snapshot_id}.png` (bucket `previews`, public read).
   - `UPDATE snapshots SET preview_key = ...`.
   - Опубликовать в Redis pubsub `project:{project_id}` событие `preview.ready`.
2. **`workers/run.py`** — entrypoint для `rq worker omnia-previews`. Запускается отдельным процессом в docker-compose как сервис `worker`.
3. **`services/ws_hub.py`** — `Hub` класс: словарь `{project_id: set[WebSocket]}`. Методы `connect`, `disconnect`, `publish(project_id, event)`. Отдельная задача в lifespan слушает Redis pubsub и распределяет события по соединениям.
4. **`routers/ws.py`** — `/api/ws/projects/:id`:
   - Аутентификация: cookie `omnia_session` ИЛИ `?token=<jwt>` query.
   - Проверка, что user — owner проекта (или 403).
   - `await hub.connect(project_id, ws)` → `try: while True: await ws.receive_json()` (для ping) `finally: hub.disconnect`.
5. **Энкьюинг preview** — после `commit_files` в любом месте (создание проекта, prompt, rollback) ставим в очередь `enqueue("render_preview", snapshot_id)`.
6. **Pubsub события `snapshot.created`** — публикуются сразу после INSERT в snapshots.

**Definition of Done M2:**
- Воркер видит задачу в очереди, рендерит PNG, заливает в MinIO.
- WebSocket-клиент (можно протестировать `wscat` или Postman) подписывается и получает `snapshot.created` + `preview.ready` после создания нового snapshot.
- Тест в `test_e2e.py`: создать проект → форсированно вызвать `render_preview.delay(snapshot_id)` → дождаться → проверить, что `preview_key` заполнен.

### M3 — LLM-мост + rollback + биллинг (день 11–14)

**Задачи:**
1. **`services/llm_client.py`** — async httpx-клиент для `:8001/v1/chat/completions`. Стримит SSE.
2. **`services/prompt_builder.py`** — собирает messages для LLM:
   - System prompt: «Ты AI-сайт-билдер Omnia. Отдавай файлы в формате `<file path="...">...</file>`. Используй tailwind via CDN. Сайты на русском.»
   - User: текущее состояние проекта (все файлы, скомпонованные в один markdown-блок) + новый промпт.
   - История последних 6 сообщений.
3. **`POST /api/projects/:id/prompt`** flow:
   - Проверить `wallet.balance_rub >= reserved_amount` (например 5₽).
   - Insert user-message в БД → получить `message_id`.
   - Insert empty assistant-message (для стрима).
   - **Запустить background task** (`asyncio.create_task`):
     - Вызвать llm_client → стримить чанки.
     - Каждый чанк публиковать в pubsub `llm.chunk` с `message_id`.
     - По завершении: распарсить файлы, вызвать `commit_files`, создать snapshot, обновить `current_snapshot_id`, обновить assistant-message (content + snapshot_id), enqueue preview, опубликовать `snapshot.created` и `llm.done`.
     - Ошибка → `llm.error`.
4. **Миграция `0003`:** `usage`, `wallet_charges`. Индексы из `docs/02`.
5. **`services/billing.py`** — `charge_for_message(user_id, message_id, model_id, tokens_in, tokens_out, cost_rub)`: транзакция insert usage + insert wallet_charge + update wallet. Если баланс < 0 — rollback (но в MVP считаем, что Gateway уже проверил перед запросом).
   _Примечание:_ списание делает агент C (Gateway) — он зовёт **B** через internal endpoint `POST /api/internal/billing/charge` (опционально) или просто пишет напрямую в БД, если они шарят базу. Согласовать в первый день.
6. **`POST /api/projects/:id/rollback`** — `repo.checkout(snapshot.commit_sha)` → новый snapshot с `is_rollback_target=true` на исходном → enqueue preview.
7. **`GET /api/models`** — прокси на `GET :8001/v1/models`, кэш 60 секунд.
8. **Rate limit** — `slowapi` или ручной через Redis. По спекам из `docs/01`.
9. **E2E тест** в `test_e2e.py`: пройти весь флоу register → project → prompt (с замокированным LLM Gateway) → snapshot → rollback.

**Definition of Done M3:**
- `POST /api/projects/:id/prompt` стримит ответ в WS, в конце — новый snapshot с preview, баланс уменьшился, usage записан.
- `rollback` создаёт новый snapshot, preview обновляется.
- Все тесты зелёные.
- `docker compose up --build` поднимает api + worker + postgres + redis + minio + (gateway, если C готов) — и всё работает end-to-end вместе с агентом A.

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

# тесты
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy src/
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

## Старт

В новом чате Claude в `C:\Бизнес план\omnia-mvp\`:
> Прочитай `agents/AGENT-B-BACKEND.md`, `docs/01-api-contract.md`, `docs/02-data-model.md`. Активируй skill code-canon. Начинай с фазы M0.
