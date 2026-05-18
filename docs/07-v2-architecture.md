# 07. V2 архитектура — full-stack продукты с deploy-кнопкой (Phase A)

## Контекст

Omnia.AI V1 (этот репозиторий, ветка main + 8 коммитов P0 hardening на ветке `claude/affectionate-lalande-c79d4b`) — генератор **статичных** сайтов: AI пишет HTML/CSS/JS, `/p/<slug>` отдаёт файлы через FastAPI.

V2 расширяет это до полноценных **full-stack** продуктов:
- Next.js 15 + Postgres + Drizzle веб-приложения
- (Phase B) Telegram/Discord боты
- (Phase C) кастомные приложения, mini-apps, mobile

Этот документ покрывает **Phase A — full-stack веб**. Phase B/C — отдельные документы (`08-v2-bots.md`, `09-v2-custom.md`), будут написаны после Phase A.

## Что меняется vs V1

| Аспект | V1 (статика) | V2 Phase A (full-stack) |
|---|---|---|
| Артефакт AI | `<file path="index.html">` | целое приложение: `package.json`, `src/app/`, `drizzle/migrations/`, `Dockerfile` |
| Хранилище кода | bare git repo в MinIO | bare git repo в MinIO + Postgres schema-per-project |
| Live preview | iframe → `/p/<slug>` (статика) | iframe → `https://<slug>-dev.omnia.app` (real Next.js dev server) |
| Runtime | Playwright рендерит PNG один раз | **persistent dev-контейнер** живёт между промптами |
| Deploy | preview = «деплой» | preview = dev, **отдельная кнопка** → prod-контейнер на `<slug>.omnia.app` |
| Биллинг | только токены | токены + runtime hours + deploy slots |
| Domain | preview под `omnia.ai/p/<slug>` | dev и prod под subdomains `<slug>-dev.omnia.app` / `<slug>.omnia.app` |

## Новые компоненты

```
┌─────────────────────────────────────────────────────────────────┐
│  apps/web (Next.js 15) — UI не меняется кардинально             │
│  + Deploy button + Runtime status panel + Logs viewer           │
└──────┬──────────────────────────────────────────────────────────┘
       │ REST + WebSocket
       ▼
┌─────────────────────────────────────────────────────────────────┐
│  apps/api (FastAPI :8000) — V1 routers + новые:                 │
│  • POST /api/projects/:id/runtime/start   — wake dev container  │
│  • POST /api/projects/:id/runtime/stop    — force-hibernate     │
│  • GET  /api/projects/:id/runtime         — status + logs URL   │
│  • POST /api/projects/:id/deploy          — promote dev→prod    │
│  • GET  /api/projects/:id/deploy          — last deploy info    │
│  • WS events: runtime.started, runtime.stopped, deploy.progress │
└──────┬──────────────────────────────────────────────────────────┘
       │ POST /provision, /wake, /stop, /deploy (internal API)
       ▼
┌─────────────────────────────────────────────────────────────────┐
│  apps/orchestrator (FastAPI :8003) — НОВЫЙ                       │
│  • Управляет dev-контейнерами через Docker Engine API           │
│  • Hibernate timer (idle >15min → docker pause/stop по tier)    │
│  • Build pipeline: docker build из starter template + AI files  │
│  • Provision Postgres schema-per-project                        │
│  • Issue subdomain + Let's Encrypt SSL                          │
│  • Health monitor → восстановление упавших контейнеров          │
└──────┬──────────────────────────────────────────────────────────┘
       │ Docker Engine API (unix socket / TCP+TLS)
       ▼
┌─────────────────────────────────────────────────────────────────┐
│  VPS pool — /opt/omnia-runtime на Serverum VPS (Phase A: 1 VPS) │
│  ┌────────────────────────────────────────────────────────┐    │
│  │  Docker daemon (с docker-soci/lazy-pull)               │    │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐       │    │
│  │  │ proj-aaaa   │ │ proj-bbbb   │ │ proj-cccc   │  ...  │    │
│  │  │ Next.js dev │ │ Next.js dev │ │ Next.js dev │       │    │
│  │  │ :3001       │ │ :3002       │ │ paused      │       │    │
│  │  └─────────────┘ └─────────────┘ └─────────────┘       │    │
│  │                                                          │    │
│  │  Shared Postgres :5433 (schema-per-project)             │    │
│  └────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
       ▲
       │ nginx reverse proxy: <slug>-dev.omnia.app → :PORT
       │                      <slug>.omnia.app     → :PROD_PORT
       └─ ingress на VPS (Caddy или nginx + certbot)
```

## Поток V2: «AI напиши мне SaaS для учёта расходов»

| # | Где | Действие | Время |
|---|---|---|---|
| 1 | Web | `POST /api/projects` с `{template: "nextjs-postgres"}` | <100ms |
| 2 | API | Создаёт project в БД, инициализирует git с template | <300ms |
| 3 | API | `POST orchestrator:8003/provision {project_id}` | <100ms |
| 4 | Orchestrator | `git clone` template в `/opt/omnia-runtime/projects/<id>/` | <1s |
| 5 | Orchestrator | `docker compose up -d` базовый dev-stack (Next.js + Postgres schema) | 30-60s (cold) |
| 6 | Orchestrator | nginx config + DNS `<slug>-dev.omnia.app` → `:PORT` | <2s |
| 7 | API → Web | WS `runtime.started {url, port}` | мгновенно |
| 8 | Web | iframe загружает `https://<slug>-dev.omnia.app` (template defaults) | <1s |
| 9 | User | Пишет промпт «добавь страницу учёта расходов» | — |
| 10 | API | Стримит LLM (V1 path), парсит `<file>` блоки | 5-30s |
| 11 | API | `pygit2` коммитит файлы в bare-repo (как в V1) | <100ms |
| 12 | API | `POST orchestrator/hot-reload {project_id}` — копирует diff в работающий контейнер | <500ms |
| 13 | Orchestrator | Next.js dev server подхватывает изменения (HMR) | мгновенно |
| 14 | Web | iframe сам обновляется через HMR — без reload | мгновенно |
| 15 | User | Видит результат, через 15 мин неактивности → hibernate (free tier) | — |

## Hybrid runtime model (тариф = поведение)

| Tier | Idle behaviour | Cold start | Цена в день (приблиз.) |
|---|---|---|---|
| Free | `docker stop` после 15 мин | 30-60 сек | 0 ₽ (только storage) |
| Pro | `docker pause` после 60 мин | 1-3 сек | ~5-10 ₽/день (1 проект always-on) |
| Business | `always running` | 0 сек | ~30-50 ₽/день |

Hibernate реализован в orchestrator: idle timer per project + Redis pub-sub `runtime.activity` (любой HTTP к dev container reset-ит таймер).

**Wake flow:** Web запрос на iframe → ingress-nginx видит «контейнер paused/stopped» → дёргает `orchestrator/wake {project_id}` → ждёт container ready (max 60с) → проксирует. UI показывает «Просыпается…» loader.

## Starter templates

Расположены в `apps/orchestrator/templates/`. Каждый — самостоятельное приложение, готовое к `docker compose up`. Phase A покрывает один шаблон:

### `nextjs-postgres-drizzle/`
```
package.json              # Next.js 15, React 19, Tailwind, Drizzle ORM, pg
next.config.ts
tsconfig.json
src/app/                  # App Router + Drizzle schema + миграции
src/lib/db/
  schema.ts
  migrations/0000_init.sql
drizzle.config.ts
docker-compose.dev.yml    # web + postgres (для dev в контейнере)
Dockerfile.dev            # с volume mount для HMR
Dockerfile.prod           # standalone build для deploy
.env.example
README.md                 # инструкции для AI: «как добавить страницу», «как добавить таблицу»
SYSTEM_PROMPT.md          # инструкции AI: формат файлов, conventions
```

После Phase A: `+nextjs-supabase/`, `+fastapi-postgres/`, `+nextjs-redis-queue/`. В Phase B: `+telegram-bot-python/`, `+discord-bot-node/`.

## Deploy «по кнопке»

Юзер нажал «Deploy» → backend orchestrator:
1. `docker build` из `Dockerfile.prod` (standalone Next.js output).
2. Push образа в local registry на VPS (`registry:5000`).
3. Provision prod-контейнера с новым контейнером name `proj-<id>-prod`.
4. `docker run --restart=unless-stopped` (не hibernate).
5. nginx update: `<slug>.omnia.app` → новый prod-порт.
6. Health check (10 retries × 3с): GET `/` → 200.
7. WS `deploy.progress` со стадиями build → push → run → healthy.
8. Запись в БД `deploys (project_id, image_tag, status, deployed_at)`.

Rollback: prev `image_tag` хранится → одна кнопка → nginx switch → старый prod-контейнер.

## VPS layout (Phase A: 1 VPS на Serverum)

```
170.168.72.200 (текущий VPS, уже в проде)
/opt/
├── omnia-mvp/                  # V1 stack (apps/api, web, gateway, postgres, redis, minio)
│   └── docker-compose.yml
└── omnia-runtime/              # НОВОЕ (V2)
    ├── docker-compose.yml      # registry + ingress + shared postgres
    ├── projects/
    │   ├── <project-id-1>/     # bare git checkout + container metadata
    │   ├── <project-id-2>/
    │   └── ...
    └── nginx/sites-enabled/    # auto-generated per project subdomain
```

**Подробные команды установки и provisioning** — см. `docs/08-vps-setup.md`.

## Биллинг — новая ось

`wallet_charges` получает новый `type`:
- `tokens` (V1 — за LLM)
- `runtime_hours` (V2 — за время работы dev-контейнера)
- `deploy_slot` (V2 — фиксированная подписка)
- `domain` (V2.5 — кастомный домен)

Тарифы (preliminary, утвердить):
- Free: 30 минут runtime/день включено, потом hibernate-only
- Pro 990₽/мес: 1 always-on проект + 10 часов runtime/мес для остальных
- Business 2990₽/мес: 3 always-on проекта + 50 часов

## Безопасность

**Sandboxing — критично, т.к. user code крутится на нашей инфре.**

Подход Phase A — defense in depth, но не паранойя:
1. **Docker rootless** обязательно — daemon работает под non-root user.
2. **Per-project Docker network** — `--network proj-<id>` изолирует один проект от другого.
3. **--read-only root filesystem** + tmpfs для `/tmp` — мутируемые мнимы лишь `/app/data` (volume).
4. **--cap-drop=ALL --cap-add=NET_BIND_SERVICE** — никаких privileged операций.
5. **--memory=512m --cpus=0.5** для free tier (предотвращает crypto-mining).
6. **Shared Postgres `:5433`**: schema-per-project + Postgres `REVOKE ALL` для cross-schema. Один проект **физически не может** прочитать таблицы другого.
7. **Secrets**: orchestrator имеет own keystore (`/opt/omnia-runtime/secrets/<project_id>/.env`, chmod 600, owner=orchestrator). Контейнер получает только свои env. **Никогда** в коде/git.
8. **Network egress**: dev-контейнеру можно ходить наружу (для `npm install`, API calls). Prod — то же. SSRF к internal IP (172.17.x.x, 10.x.x.x) **должен** блокироваться через iptables.
9. **No real DNS for free tier** — `<slug>-dev.omnia.app` указывает на наш VPS, а сам proj не имеет доступа к public IP без routing.

## Phase A roadmap (6-8 недель, что нужно сделать)

| Sprint | Зачем | Кто (агент) |
|---|---|---|
| **A0 (2-3 дня)** — этот документ, scaffold orchestrator (skeleton FastAPI), один template `nextjs-postgres-drizzle`, обновление `docs/01-api-contract.md`. **Сделано в этой сессии** | Архитектурный мостик | этот ассистент |
| **A1 (1 неделя)** — реализация orchestrator: provision/wake/stop через Docker SDK, hibernate timer, shared Postgres schema-per-project | Backend orchestrator | новый Agent D (DevOps) |
| **A2 (1 неделя)** — ingress nginx auto-config + Let's Encrypt wildcard cert + DNS automation (Yandex Cloud DNS API или manual) | Ingress | Agent D |
| **A3 (2 недели)** — UI в apps/web: deploy button, runtime status, logs viewer, billing-by-hours; hot-reload через WS из orchestrator в dev-контейнер | UX V2 | Agent A |
| **A4 (1 неделя)** — billing: новые типы charge, тариф auto-pick, deploy slot allocation | Backend | Agent B |
| **A5 (1 неделя)** — Sentry/observability для контейнеров, prometheus metrics, alerts на OOM/crash | Stability | Agent D |
| **A6 (1 неделя)** — beta launch на 10 проверенных юзерах, fix critical, performance tuning | Launch | все |

## Что нужно сделать пользователю на сервере **прежде чем V2 можно тестировать**

Подробно в `docs/08-vps-setup.md`. Краткий список:
1. SSH на 170.168.72.200, создать папку `/opt/omnia-runtime/`.
2. Установить Docker (если ещё нет — V1 уже использует Docker).
3. Открыть порты 80/443 (если ещё закрыты).
4. Купить домен `omnia.app` или `omnia.ai` (.app дешевле и подходит больше — нужно подтвердить).
5. Wildcard SSL cert для `*.omnia.app` через Let's Encrypt DNS-01.
6. Создать Docker user группу orchestrator.
7. Установить Docker SDK Python через uv.

## Open questions

1. **Домен.** `omnia.app` ($14/год Namecheap) vs `omnia.ai` (~$150-1000/год) vs `omnia.ru` (`reg.ru`, ~700₽/год). Решение нужно до Phase A2.
2. **Postgres-per-project vs shared with schemas.** Phase A — shared (проще). Если масштаб >100 проектов — pgbouncer + reads/writes split.
3. **Docker rootless** на текущем Serverum VPS — нужна 4.18+ kernel, проверить.
4. **Stripe-style runtime metering** — почасово ловим через Prometheus или внутренний таймер orchestrator? Phase A: внутренний таймер достаточно.
5. **CI для starter templates** — кто обновляет шаблон когда Next.js выходит 15.2? Auto-PR + visual regression test?
6. **WebContainers** — стоит ли исследовать как альтернативу dev-контейнерам для frontend-only проектов (минус cold start)? Phase A — нет, добавляет зависимость от StackBlitz.

## Связь с V1

V1 (status static site) **не уходит**. После V2 запуска:
- Старые проекты с template `blank/landing/portfolio/blog` остаются в V1-режиме.
- При создании проекта в UI: выбор «static site» (V1) или «full-stack app» (V2).
- LLM Gateway, billing core, auth — общие.
- API contract расширяется (новые endpoints), но не ломается.
