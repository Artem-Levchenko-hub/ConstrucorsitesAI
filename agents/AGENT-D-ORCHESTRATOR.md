# Агент D — Orchestrator + DevOps (V2 Phase A)

Прочитай этот бриф, затем `docs/07-v2-architecture.md` (полный дизайн V2) и `docs/08-vps-setup.md` (команды для prod-сервера). Затем — приступай к sprint A1.

## Кто ты в этой команде

Ты владелец **runtime-плоскости V2**: per-project Docker контейнеры, hibernate-таймер, build-pipeline для deploy, nginx auto-config, изоляция через per-project Postgres schemas. Ты — Agent D, дополнение к существующим A/B/C, которые продолжают V1 maintenance параллельно.

Твой сервис называется **`omnia-orchestrator`**, лежит в `apps/orchestrator/`. Слушает `:8003` на хосте VPS (НЕ внутри docker-compose). Принимает запросы только от `apps/api` через header `X-Internal-Token` (shared secret).

## Жёсткие границы

- **ПИШЕШЬ ТОЛЬКО в `apps/orchestrator/` и `infra/`** (последнее — для docker-compose правок и nginx шаблонов).
- **НЕ ЛЕЗЬ в `apps/web/`, `apps/api/`, `apps/llm-gateway/`**. Если нужно расширение публичного API — правь `docs/01-api-contract.md` (раздел V2) и координируйся с B (он добавит проксирующие endpoints в apps/api).
- Контракт твоего internal API — `apps/orchestrator/src/omnia_orchestrator/schemas/runtime.py`. Меняешь — пишешь в координацию.

## Стек (фиксированный)

- **Python 3.12**, `uv`.
- **FastAPI 0.115+** (async).
- **docker-py 7.1+** — Docker Engine API SDK.
- **asyncpg** — admin connection к `omnia-postgres-users` для `CREATE SCHEMA`.
- **pydantic v2** + pydantic-settings.
- **structlog** — единый лог-формат.
- **sentry-sdk[fastapi]** — копия паттерна из `apps/api/core/sentry.py`.
- **httpx** — health-poll контейнеров после wake.
- `pytest + pytest-asyncio + pytest-mock` (мокаем docker).

## Что уже сделано (scaffold)

`apps/orchestrator/` уже содержит:
- `pyproject.toml`, `README.md`, `.env.example` — настройки и зависимости.
- `src/omnia_orchestrator/main.py` — FastAPI app + lifespan + error handlers.
- `core/config.py` — Settings с env-vars (DOCKER_HOST, PROJECTS_ROOT, BASE_DOMAIN, hibernate-времена, INTERNAL_TOKEN).
- `core/errors.py` — error envelope (тот же формат что в apps/api).
- `core/docker_client.py` — обёртка SDK с **stubbed методами** (`start_container`, `stop_container`, `container_status`, `destroy_container`). Все бросают `501 internal_error` с TODO-комментарием.
- `routers/health.py` — `GET /health`.
- `routers/runtime.py` — все endpoints internal API (provision/wake/stop/hot-reload/deploy/status/destroy) с stubbed бизнес-логикой.
- `schemas/runtime.py` — Pydantic DTO для всех запросов/ответов (стабильно).
- `services/port_allocator.py` — **реализован полностью** (file-backed registry в `{projects_root}/.port-registry.json`).
- `services/hibernate.py` — `start_hibernate_loop` stub.
- `templates/nextjs-postgres-drizzle/` — первый starter шаблон (Next.js 15 + Postgres + Drizzle ORM).

Тебе остаётся **наполнить stubbed методы** — без пере-архитектуры, контракты уже зафиксированы.

## Phase A роадмап (sprint A1 — твой)

| День | Что сделать |
|---|---|
| 1-2 | `core/docker_client.py`: реализовать `start_container`/`stop_container`/`container_status`/`destroy_container` через docker-py. Security: `--cap-drop=ALL`, `--read-only`, `--user 1000:1000`, `--memory=512m`, `--cpus=0.5`, `--network=proj-<id>`. |
| 3-4 | `core/postgres_admin.py` (новый): `create_schema`, `create_role`, `drop_schema`, secure connection string generation для контейнера. Из `omnia-postgres-users` admin connection (DATABASE_URL в env). |
| 5-6 | `core/nginx_writer.py` (новый): генерация `/opt/omnia-runtime/nginx/sites-enabled/<slug>.conf` (proxy_pass на host port, WebSocket upgrade для HMR, X-Forwarded-* headers). `subprocess` для `nginx -s reload`. |
| 7-8 | `services/provisioner.py`: end-to-end provision flow (clone template → port → schema → container → nginx → health-poll). Тесты с моком docker. |
| 9-10 | `services/hibernate.py`: Redis pub-sub listener для activity, sweep loop, tier-aware pause/stop. |
| 11-13 | `services/builder.py`: build → push → swap для deploy endpoint. Зеро-downtime через Nginx upstream swap. |
| 14-15 | Polish: Sentry init, observability, integration tests (start реальный docker-in-docker в CI). |

## Открытые вопросы (Phase A — решить с Артёмом)

1. **Subdomain DNS provider** — Cloudflare API token или Yandex Cloud DNS? Решает кто пишет Caddy/certbot DNS-01 интеграцию.
2. **Rootless Docker** — пробуем сразу или после первых 10 проектов? Сложно отлаживать, но resilient к escape.
3. **Регистрация юзерских БД в каталоге** — отдельная таблица `runtime_projects` в основном Postgres-е (apps/api domain) или просто JSON в orchestrator (потом мигрируем)?
4. **Volume для node_modules** — bind mount или named volume? Влияет на cold start.

## Связь с другими агентами

- **A (frontend)**: добавит deploy кнопку, runtime status panel, logs viewer. Контракт WS-событий — `docs/01-api-contract.md` раздел V2.
- **B (backend)**: добавит проксирующие endpoints в apps/api (`/api/projects/:id/runtime/*`, `/api/projects/:id/deploy`). Будет вызывать твой `/internal/projects/*`.
- **C (gateway)**: не пересекается с тобой в V2 Phase A.

## Когда заработает — что считается «sprint A1 done»

End-to-end smoke на VPS:
```bash
# С локалки:
curl -X POST https://omnia-internal.constructor.lead-generator.ru/internal/projects/provision \
  -H "X-Internal-Token: $TOKEN" \
  -d '{"project_id":"00000000-0000-0000-0000-000000000001","slug":"test","template":"nextjs-postgres-drizzle","tier":"free"}'

# Через 60 сек:
curl https://test.preview.omniadevelop.ru
# → 200, Next.js дефолтная страница

# Hibernate test:
sleep 900  # 15 минут idle
docker ps | grep proj-00000000  # → нет (free tier = stop)
curl https://test.preview.omniadevelop.ru
# → 200 (после 30-60 сек wake)
```
