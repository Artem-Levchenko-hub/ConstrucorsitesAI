# omnia-orchestrator

V2 Phase A runtime orchestrator: управляет dev-контейнерами полнокачественных проектов пользователей (Next.js, FastAPI, etc.).

**Status: scaffold.** Это skeleton с stubbed-методами + комментариями `TODO`. Полная реализация — задача sprint A1 (см. `docs/07-v2-architecture.md` Phase A roadmap).

## Что делает

1. **Provision** — клонирует starter template в `/opt/omnia-runtime/projects/<id>/`, создаёт Postgres schema, аллоцирует порт.
2. **Wake / Sleep** — запускает / приостанавливает dev-контейнеры по hibernate-таймеру (free → stop, pro → pause).
3. **Hot-reload** — копирует AI-сгенерированный diff в работающий контейнер для HMR.
4. **Deploy** — `docker build` prod-образа, push в local registry, switch nginx subdomain.
5. **Health monitor** — отслеживает crashes, OOM, перезапускает.

## API (внутренний, не публичный)

| Method | Path | Description |
|---|---|---|
| `GET`  | `/health` | service + docker + postgres status |
| `POST` | `/internal/projects/provision` | create container + DB schema |
| `POST` | `/internal/projects/wake` | start/unpause |
| `POST` | `/internal/projects/stop` | pause or stop based on tier |
| `POST` | `/internal/projects/hot-reload` | copy files into running container |
| `POST` | `/internal/projects/deploy` | build + push + run prod container |
| `GET`  | `/internal/projects/:id/status` | container state, port, last activity |
| `POST` | `/internal/projects/:id/destroy` | full cleanup |

Все endpoints защищены header `X-Internal-Token` (shared secret между api↔orchestrator).

## Запуск локально (для разработчиков)

```bash
cd apps/orchestrator
uv sync --extra dev
uv run uvicorn omnia_orchestrator.main:app --reload --port 8003
```

## Запуск на VPS

См. `docs/08-vps-setup.md` (systemd unit, /opt/omnia-runtime layout, postgres-users provisioning).

## TODO для sprint A1

См. `TODO:` маркеры в `src/omnia_orchestrator/`. Главное:
- `services/provisioner.py` — собственно flow provision (Docker + Postgres + nginx).
- `services/hibernate.py` — таймер + tier-aware pause/stop.
- `services/builder.py` — docker build для deploy.
- `core/postgres_admin.py` — `CREATE SCHEMA` + role provisioning.
- `core/nginx_writer.py` — генерация sites-enabled/*.conf + reload.
