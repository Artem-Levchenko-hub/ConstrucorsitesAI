# Omnia.AI — MVP

**Что это:** AI-сайт-билдер «под ключ» для российского рынка. Пиши промпты — получай готовый сайт с backend, доменом, деплоем и кнопкой «вернуться назад» для каждого промпта. Всё в рублях.

**Бизнес-план:** `C:\Бизнес план\AI_Site_Builder_Business_Plan_v1.xlsx` (12 листов, владельцы — Артём Левченко + Рома Исакин).

## Параллельная разработка (4 агента в worktree-ах с V2)

Этот репозиторий разрабатывается **четырьмя параллельными Claude-сессиями** (V2 добавил Agent D). Чтобы 4 агента работали эффективно и не ломали друг другу работу — соблюдай протокол ниже.

| Агент | Папка (пишет ТОЛЬКО сюда) | Бриф |
|---|---|---|
| **A — Frontend** | `apps/web/` | [`agents/AGENT-A-FRONTEND.md`](agents/AGENT-A-FRONTEND.md) |
| **B — Backend** | `apps/api/` | [`agents/AGENT-B-BACKEND.md`](agents/AGENT-B-BACKEND.md) |
| **C — LLM Gateway** | `apps/llm-gateway/` | [`agents/AGENT-C-LLM-GATEWAY.md`](agents/AGENT-C-LLM-GATEWAY.md) |
| **D — Orchestrator + DevOps (V2)** | `apps/orchestrator/`, `infra/` | [`agents/AGENT-D-ORCHESTRATOR.md`](agents/AGENT-D-ORCHESTRATOR.md) |

**Правило #1 — границы зон.** Агент ПИШЕТ только в свою папку. Чужая папка и shared-файлы (`docs/**`, `CLAUDE.md`, `docker-compose*.yml`, `.github/**`, корневые конфиги) — только через координацию (см. ниже).

**Канал координации** (общий, лежит **вне** worktree-ов → виден всем сессиям сразу, без git): `~/.claude/coordination/omnia-mvp/`
- `BOARD.md` — живой статус: кто что трогает, блокировки/заморозки, открытые запросы между агентами.
- `inbox/<дата>-agent-<X>-<тема>.md` — сообщения агентам (анонс правки, запрос, предупреждение).

**Протокол координации — обязателен каждой сессии:**
1. **В начале сессии** прочитай `BOARD.md` и новые `inbox/*.md`; обнови свою строку в `BOARD.md`.
2. **Перед правкой shared-файла или чужой папки** — проверь блокировки в `BOARD.md`, поставь claim там же и оставь сообщение в `inbox/` затронутым агентам. После правки коммить быстро (atomic), чтобы другие могли rebase.
3. **Контракт** (`docs/01-api-contract.md`) — менять только backward-compatible + анонс в `inbox/`.
4. **Прод** (`omnia-prod-*` на VPS) — НЕ пересобирать/деплоить без согласования: в `/opt/omnia` копятся незадеплоенные правки нескольких агентов. Смотри `DEPLOY FREEZE` в `BOARD.md`.
5. **Ветка-на-фичу**, частые atomic-коммиты, перед shared-правкой — `git pull --rebase`.
6. **Гигиена общего рабочего дерева** (сессии иногда делят один worktree): перед правкой shared-файла **перечитай его прямо перед `Edit`** — другой агент мог переписать содержимое (не только git-статус); не завязывайся на поля, которые другая сессия откатывает. В общем дереве **никогда** `git stash` / `reset` / `checkout` — стэш выдёргивает файлы из-под активной сессии; чтобы сохранить незавершённое, делай **`commit`** (снимок не мешает чужим несохранённым правкам). Свою фичу держи в отдельном worktree на **ASCII-пути** (Glob/Grep с параметром `path` молча ломается на кириллице+пробеле, как в `C:\Бизнес план\…`).

Note: до V2 launch `infra/` оставался зоной агента C. С V2 (Phase A) — переходит в зону D, потому что инфра-композиция теперь связана с runtime-orchestration юзерских проектов, а не только с обслуживающим стеком.

**Worktrees:** плагин `claude-session-driver` автоматически создаёт worktree под каждую сессию в `.claude/worktrees/`. Не редактируй там вручную. (Полная авто-координация с `claims.json`/`active-sessions.json` через хуки — как в проекте CorporateMessanger — для omnia пока НЕ установлена; координация ручная через `BOARD.md` + `inbox/`.)

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
