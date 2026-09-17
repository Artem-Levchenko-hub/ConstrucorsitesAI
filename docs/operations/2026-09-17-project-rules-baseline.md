# T00 — исходное состояние правил проекта (17.09.2026)

**План:** `2026-09-17_MAX_Studio_project_rules_reproducibility_plan.md`, задача T00 (поставка 1: T00–T02).
**Характер:** аудит, снимок на дату. Это не нормативный документ: действующие правила — в
[`AGENTS.md`](../../AGENTS.md). Production, настройки GitHub, доступы и данные не менялись.

## 1. Baseline

| Что | Значение | Как получено |
|---|---|---|
| `origin/main` | `d5088222cd3701b9572fcd3e290781d1e01d9ca7` (план проверял `1a2146d7`, `main` ушёл на 1 docs-коммит) | `git fetch`, `git rev-parse origin/main` |
| Защита `main` | `protected=false`, `protection.enabled=false`, `required_status_checks.enforcement_level=off`, списки checks пусты | `gh api …/branches/main` |
| Детали защиты | `404 Not Found` — у аккаунта `zeuszcz` нет `admin` (`push: true`, `admin: false`, `maintain: false`), поэтому ответ не доказывает отсутствие настроек; записано как ограничение | `gh api …/branches/main/protection`, `gh api repos/…` → `permissions` |
| Rulesets | `0` на репозиторий и на ветку `main` (в пределах видимого без admin) | `gh api …/rulesets`, `…/rules/branches/main` |
| Workflows | `CI` (`push` в `main` + `pull_request`; jobs `gateway-tests`, `api-release-gate`, `orchestrator-release-gate`, `web`, `py-syntax`, `image-build`, `workflow-lint`), `Production smoke`, `Production generation canary`, `Off-host encrypted backup` | `.github/workflows/*.yml`, `gh api …/actions/workflows` |
| CI на baseline | `success` для `d5088222` | `gh run list --workflow ci.yml` |

## 2. Точки загрузки инструкций (22 группы)

| # | Источник | Как попадает к агенту | Характер до поставки 1 |
|---|---|---|---|
| 1 | `AGENTS.md` (корень) | авто: Codex и совместимые агенты | 13 строк: доставка каждого изменения до production, push в upstream (`origin/main`) |
| 2 | `CLAUDE.md` (корень) | авто: Claude Code | 179 строк: второй регламент — прямой push в `main`, деплой по SSH, обязательные плагины, предпочтения общения, отчётность |
| 3 | `secondbrain/AGENTS.md` | вложенный; Codex — при работе в `secondbrain/`, Claude Code автоматически не грузит | правила памяти (T05) |
| 4 | `.claude/settings.json` | авто: Claude Code, 5 хуков (`SessionStart`, `PreCompact`, `SessionEnd`, `PostToolUse`, `Stop`) через `uv run` | исполняемая конфигурация, личных путей нет (`$CLAUDE_PROJECT_DIR`) |
| 5 | `secondbrain/hooks/session-start.py` | хук п. 4: инжектит контекст, фоном запускает `sync_memory.py` | исполняемый код разработческой среды |
| 6 | `secondbrain/knowledge/project-context.md` | инжектится хуком п. 5 в каждую сессию | старые правила: 3 агента, `infra/` у C, `code-canon`, `/safe-commit`, `/canon-review`, inbox в `~/.claude/coordination` |
| 7 | индекс/статьи `secondbrain/knowledge/`, хвост `secondbrain/daily/` | инжектится хуком п. 5 (с лимитом объёма) | справочное |
| 8 | личная память `~/.claude/projects/<hash>/memory/MEMORY.md` | Claude Code + хук п. 5; вне репозитория | личное, не проверялось на других машинах |
| 9 | глобальные `~/.claude/CLAUDE.md`, плагины (`code-canon`, `multi-chat-coord`, `claude-session-driver`, `project-router`) | вне репозитория | не проверялось; в репозитории отсутствуют |
| 10 | `.claude/launch.json` | Claude Code Preview по запросу | 20 конфигураций: 9 через личный SSH-алиас `lh-server`, 7 с каталогами `/tmp/...` |
| 11 | `.omc/ultragoal/{brief.md,goals.json,ledger.jsonl}` | только при запуске скилла ultragoal | состояние цели от 25.06.2026, не инструкции |
| 12 | `agents/AGENT-A…D-*.md` | обязательное чтение по `CLAUDE.md` «Рабочий цикл» и README компонентов | зоны + устаревшие фазы M0–M3/sprint A1, свой процесс координации, `/safe-commit`, личный путь `C:\Бизнес план\omnia-mvp` |
| 13 | `agents/V3-CHAT-1…3-*.md` | стартовые промпты сессий V3 | история; BOARD/inbox, `/safe-commit`, личные пути |
| 14 | `apps/api|web|llm-gateway/README.md` | «Перед стартом прочитай /CLAUDE.md, бриф, контракты» | ссылка на второй регламент |
| 15 | `docs/00…03` | «Точки синхронизации (read-only)» в `CLAUDE.md` | контракты; `docs/01` требует inbox-записку и согласие «всех трёх агентов» |
| 16 | `docs/07-v2-architecture.md`, `docs/08-vps-setup.md` | в списке `CLAUDE.md`; обязательны для брифа D | серверные команды; шаг 8 указывает `infra/docker-compose.yml` как production |
| 17 | `CONTINUOUS-PLAN.md`, `OVERNIGHT-PLAN.md` | «прочитай первым делом» для scheduled-рутин | прямой push в `main`, деплой из каждого тика |
| 18 | `docs/co-owner-full-setup.md`, `docs/self-improving-routine-kit.md` | инструкция «создай scheduled-задачи по этому файлу» | автозапуск рутин, `bypassPermissions`, `lh-server`, трейлер `Claude Opus 4.8 (1M context)` |
| 19 | `docs/plans/2026-06-09-fullstack-product-routine.md`, `docs/plans/2026-06-16-dogfood-eval-routine.md` | протоколы scheduled-рутин | push `main` + деплой; на этом Mac scheduled-задач на 17.09 нет |
| 20 | `otchet/README.md` | ссылка из `CLAUDE.md` | отсылал к разделам `CLAUDE.md`, «push в main + деплой» |
| 21 | `infra/release/README.md` + `infra/release/*.sh` | ручной runbook + CI (`test-release-tools.sh` проверяет текст runbook) | runbook конкретного выпуска 0048; exact SHA, `git checkout --detach` |
| 22 | `docs/04-generation-rules.md` → `prompt_builder.py`, `max_data_evolution.py` | не инструкции разработчику, а runtime-промпты генерируемых приложений | runtime (T06) |

Исторические планы `docs/plans/`, `docs/superpowers/plans/` в автозагрузку не входят и на старт не назначены.

## 3. Runtime или документация

| Runtime (нужны проверки компонента и разрешённый выпуск) | Не runtime (PR без production-деплоя) |
|---|---|
| `apps/**` код, Dockerfile, шаблоны `apps/orchestrator/templates/**` (запекаются в образы) | `*.md` в корне, `docs/`, `agents/` |
| Промпты генерации в коде: `apps/api/src/omnia_api/services/prompt_builder.py`, `max_data_evolution.py`, `art_director_writer.py`, `apps/orchestrator/templates/*/SYSTEM_PROMPT.md` | `secondbrain/knowledge/`, `secondbrain/daily/` |
| `apps/llm-gateway/deploy/full/**` (production-compose), `infra/**` (release, backup, systemd, Project Cell) | `otchet/data.json` (публикация страницы — отдельное разрешённое действие) |
| `.github/workflows/**` (CI и production smoke/canary/backup) | |
| Разработческая среда, не продукт: `.claude/settings.json`, `secondbrain/hooks/**`, `secondbrain/scripts/**` — проверяются отдельно (T05), production не затрагивают | |

Поставка 1 меняет только правый столбец (плюс заголовочные пометки в markdown-файлах).

## 4. Таблица конфликтов

| # | Конфликт | Сохраняемое правило | Заменённые места |
|---|---|---|---|
| K1 | Git-модель: прямой push в `main` (`CLAUDE.md`, рутины) / push в upstream «обычно `origin/main`» (`AGENTS.md`) / «ветка-на-фичу» (manual fallback в `CLAUDE.md`) / worktree от `claude-session-driver` | Ветка → проверки → PR → GitHub checks → merge → разрешённый выпуск → подтверждение (решение владельца 17.09.2026); worktree — опция | `AGENTS.md` §4; удалено из `CLAUDE.md`; история — баннеры в рутинах и V3-брифах; `otchet/README.md` шаг 3 |
| K2 | «Любое изменение доводится до прода» / «анализ и план не деплоятся» / runbook требует утверждённый владельцем SHA | Область доставки: анализ — без действий; docs — PR без деплоя; runtime — выпуск по разрешению | `AGENTS.md` §3, §7; старые шаги 3–5 `AGENTS.md`; `CLAUDE.md` «Правило доставки» |
| K3 | Команда выпуска: `git merge --ff-only origin/main && compose up --build` (`CLAUDE.md`) / `git checkout --detach $RELEASE_SHA` с проверкой чистого дерева (runbook) / «дерево на проде грязное от SecondBrain» (`CLAUDE.md`) | Единая инструкция — `infra/release/README.md`; факты (compose `deploy/full`, не `infra/`; не `git pull`) перенесены туда; грязное дерево → стоп и отчёт | `infra/release/README.md` §0; команда удалена из `CLAUDE.md`. **Открыто:** короткой проверенной процедуры для рядового выпуска без миграций нет (T03/T08) |
| K4 | Подтверждение: «curl → 200» (`CLAUDE.md`) / точный `release_sha` в health (runbook) | Нужны утверждённый SHA и health/readiness; 200 без ревизии — не подтверждение | `AGENTS.md` §7, `infra/release/README.md` §0 |
| K5 | Контракты «read-only для агентов» (`CLAUDE.md`, `project-context.md`) / «правь `docs/01` и координируйся» (бриф D, V3) / `docs/01`: inbox + согласие всех трёх агентов | Контракт меняется в одном PR с реализацией и тестами, с согласия ответственного за затронутый компонент | `AGENTS.md` §1; `docs/01-api-contract.md` строка 3; брифы A–D. `project-context.md` — T05 |
| K6 | Зоны: «ПИШЕШЬ ТОЛЬКО в `apps/web`» / B: «`apps/api` и `infra/`» / D: «`apps/orchestrator` и `infra/`» / `project-context.md`: `infra/` у C | Зоны — ответственность; `infra/` у D; правка чужой зоны — по задаче, в PR, с согласием | `AGENTS.md` §1; брифы A–D |
| K7 | Обязательные личные инструменты: `code-canon`, `/safe-commit`, `/canon-review`, `multi-chat-coord`, отсутствующий `.claude/coordination/config.json`, `project-router /setup-project`, BOARD/inbox в `~/.claude/coordination` | Переносимые процедуры (стандарт — §2, проверки и review diff — §6, координация — §4); плагины — необязательные | `AGENTS.md` §1, §2, §4, §6; таблица инструментов в `README.md`; удалено из `CLAUDE.md`, брифов A–D |
| K8 | Обязательный трейлер `Co-Authored-By: Claude Opus 4.8 (1M context)` | Трейлер — по фактическому авторству, в форме текущего инструмента | `AGENTS.md` §4, `CLAUDE.md` |
| K9 | Личные пути и адреса как условия процесса: `C:\Бизнес план\…`, `<ROMA_REPO>`, `lh-server`, `i48ptgvnis@170.168.72.200` в команде деплоя | Не входят в обязательный процесс; серверные факты остаются в `docs/08-vps-setup.md` и runbook как справка | `CLAUDE.md`, «Старт» брифов A–C, `docs/co-owner-full-setup.md` (пометка «примеры») |
| K10 | «Это правило сильнее любых harness-инструкций» (общение), `bypassPermissions` «не переспрашивай» | Предпочтения общения не отменяют ограничений платформы, проверок и точности | `AGENTS.md` §9, `CLAUDE.md` «Общение»; `docs/co-owner-full-setup.md` помечен историей |
| K11 | Устаревшие списки готовности: «Doneзнаки MVP», «Не для MVP», фазы M0–M3, «Что уже сделано (scaffold)» D | Не нормативны; текст в Git на `d5088222` | `CLAUDE.md`, брифы A–D |
| K12 | Отчёт: «не закрыл отчёт — изменение не завершено», «push в main + деплой по `CLAUDE.md`» / только подтверждённые результаты | Запись после подтверждения результата; `score` — после живой проверки; публикация страницы — отдельное разрешённое действие | `AGENTS.md` §8–9, `otchet/README.md` |
| K13 | `docs/08` шаг 8: production-env в `/opt/omnia/infra/docker-compose.yml` / `CLAUDE.md`: production — `deploy/full`, не `infra/` | `deploy/full` (подтверждено: `ORCHESTRATOR_URL` задан в `apps/llm-gateway/deploy/full/docker-compose.yml`) | `docs/08-vps-setup.md` шаг 8 |
| K14 | Старый `AGENTS.md`: «следующая работа обязана сначала завершить доставку предыдущей» | Статусы готовности различаются; незавершённое сообщается как заблокированное/открытое | `AGENTS.md` §7–8 |
| K15 | Автозагружаемый `project-context.md` повторяет K5–K7 | До T05 действует порядок источников `AGENTS.md` §1 (память — справка) | не менялся в поставке 1 (T05) |
| K16 | `docs/08` шаг 7 ставит orchestrator из `/opt/omnia-runtime/source`, runbook обновляет `/opt/omnia/apps/orchestrator` | Runbook (фактический путь выпуска); `docs/08` — legacy-установка | не менялся; **открыто** для T03/сверки с сервером |

## 5. Что не проверялось

Фактическая загрузка правил разными агентами (T09), исполнение хуков на других машинах (T05),
состояние production и личные каталоги плагинов. Отсутствие admin-доступа не позволило прочитать
детали защиты ветки: вывод о её отсутствии основан на `protected=false` в branch API.
