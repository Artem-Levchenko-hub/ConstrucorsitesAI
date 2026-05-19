# Omnia.AI — MVP

**Что это:** AI-сайт-билдер «под ключ» для российского рынка. Пиши промпты — получай готовый сайт с backend, доменом, деплоем и кнопкой «вернуться назад» для каждого промпта. Всё в рублях.

**Бизнес-план:** `C:\Бизнес план\AI_Site_Builder_Business_Plan_v1.xlsx` (12 листов, владельцы — Артём Левченко + Рома Исакин).

## Параллельная разработка (4 агента в worktree-ах с V2)

Этот репозиторий разрабатывается **четырьмя параллельными Claude-сессиями** (V2 добавил Agent D):

| Агент | Папка | Бриф |
|---|---|---|
| **A — Frontend** | `apps/web/` | [`agents/AGENT-A-FRONTEND.md`](agents/AGENT-A-FRONTEND.md) |
| **B — Backend** | `apps/api/` | [`agents/AGENT-B-BACKEND.md`](agents/AGENT-B-BACKEND.md) |
| **C — LLM Gateway** | `apps/llm-gateway/` | [`agents/AGENT-C-LLM-GATEWAY.md`](agents/AGENT-C-LLM-GATEWAY.md) |
| **D — Orchestrator + DevOps (V2)** | `apps/orchestrator/`, `infra/` | [`agents/AGENT-D-ORCHESTRATOR.md`](agents/AGENT-D-ORCHESTRATOR.md) |

**Правило #1:** агент ПИШЕТ только в свою папку. Если нужно поменять контракт — правь `docs/01-api-contract.md` и сообщай через `~/.claude/coordination/<slug>/inbox/`.

Note: до V2 launch `infra/` оставался зоной агента C. С V2 (Phase A) — переходит в зону D, потому что инфра-композиция теперь связана с runtime-orchestration юзерских проектов, а не только с обслуживающим стеком.

**Worktrees:** плагин `claude-session-driver` автоматически создаёт worktree под каждую сессию в `.claude/worktrees/`. Не редактируй там вручную.

## Точки синхронизации (read-only для агентов, single source of truth)

- [`docs/00-architecture.md`](docs/00-architecture.md) — как A/B/C соединяются (V1)
- [`docs/01-api-contract.md`](docs/01-api-contract.md) — REST + WebSocket контракт (V1 + V2)
- [`docs/02-data-model.md`](docs/02-data-model.md) — Postgres-схема
- [`docs/03-design-system.md`](docs/03-design-system.md) — палитра, типографика, компоненты
- [`docs/07-v2-architecture.md`](docs/07-v2-architecture.md) — **V2 Phase A**: full-stack runtime, orchestrator, deploy
- [`docs/08-vps-setup.md`](docs/08-vps-setup.md) — конкретные shell-команды для VPS под V2

## Стек (фиксированный — не менять без обсуждения)

- **Frontend:** Next.js 15 (App Router) + React 19 + TypeScript + Tailwind v4 + shadcn/ui + framer-motion
- **Backend:** FastAPI (Python 3.12) + Postgres 16 + Redis 7 + MinIO + pygit2 + Playwright
- **LLM Gateway:** FastAPI + LiteLLM (proxy к Anthropic/OpenAI/YandexGPT)
- **Infra:** Docker Compose локально → Ansible+Docker на VPS Serverum.ru в проде
- **Auth:** Auth.js (NextAuth) с JWT, бэкенд верифицирует через JWKS
- **Платежи:** ЮKassa (только stub в MVP — реальная интеграция после 50 беттестеров)

## Рабочий цикл (для каждого агента в своей сессии)

1. Прочитать свой `agents/AGENT-X-*.md`
2. Прочитать соответствующие места в `docs/` (контракт обязателен; data-model — для B и C; design-system — для A)
3. Активировать `code-canon` skill перед первым `Edit`/`Write`
4. Работать в своей папке, фиксы коммитить часто (atomic commits)
5. Перед коммитом — `/safe-commit` (typecheck + lint + test + canon-review)
6. После значимого блока — `/canon-review` для diff'а

## Doneзнаки MVP (всё, всё)

- [ ] Регистрация / логин (email + пароль, JWT в httpOnly cookie)
- [ ] Лендинг: hero, "как работает", фичи, цены, FAQ, footer
- [ ] Workspace: чат → промпт → AI генерирует сайт → live preview обновляется → snapshot в timeline
- [ ] Timeline: лента превьюшек (PNG), клик → откат за 1 sec
- [ ] 3 стартовых шаблона: лендинг бизнеса, портфолио, простой блог
- [ ] Селектор моделей в UI (Claude Sonnet 4.6, GPT-4.1, YandexGPT 5)
- [ ] Кошелёк: показ баланса, списания за токены (биллинг настоящий или mock — без оплаты)
- [ ] Доступ к проекту по `/p/<slug>` для preview без логина
- [ ] Rate limit и базовая защита от prompt injection в LLM-proxy
- [ ] `docker-compose up` поднимает весь стек локально за 1 команду

## Не для MVP (специально вне scope)

- ❌ Реальная оплата через ЮKassa (только UI кошелька)
- ❌ Регистрация доменов (только наш `*.omnia.ai` поддомен)
- ❌ GitHub-синк
- ❌ White-label / партнёрки
- ❌ A/B-тесты, аналитика
- ❌ Мобильные приложения

## Как добавить проект в `project-router` (после первого коммита)

```bash
# Из любой папки:
/setup-project
# → выбрать omnia-mvp, указать путь C:\Бизнес план\omnia-mvp
```

После этого глобальная память будет автоматически подгружать контекст этого проекта.
