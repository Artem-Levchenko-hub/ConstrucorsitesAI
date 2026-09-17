# T22.1 — исходное состояние production (17.09.2026)

**План:** `2026-09-16_MAX_Studio_T22_checks_and_baseline_addendum.md`, пункт T22.1.

**Снято:** 17.09.2026, read-only, по SSH на `170.168.72.200` и публичным health endpoints.
На production ничего не менялось: контейнеры, `.env`, nginx, образы и БД не тронуты.
Секреты в документ не записаны.

**Проверяющий код:** ветка `claude/elegant-franklin-j8p5sm`, коммит T22.2/T22.4 `dfb339af`.
Workflow в `main` на момент отказов — `6e383a55`. HEAD `main` не считается доказательством
того, что именно он опубликован: ниже версии взяты из запущенных компонентов.

## 1. Проблемные запуски

| Workflow | Run / job | Время (UTC) | Commit workflow | Код отказа |
|---|---|---|---|---|
| Production smoke | `35108206175` / `104834793836` | 16.09 14:23 | `6e383a55` | `web.release_mismatch`, `max_health.http_502`, `max_webhook.http_502` |
| Production smoke (последний) | `35171806210` | 17.09 01:45 | `6e383a55` | те же три кода |
| Generation canary | `35069867816` / `104708628978` | 16.09 07:42 | `6e383a55` | `canary_failed`, нет события `release_health` |
| Off-host backup | `35079342668` / `104739293958` | 16.09 09:25 | `6e383a55` | `Backup is suspiciously small: 2157998 bytes` |

Все три workflow красные подряд с 14.09 (canary и backup — ещё на `d3931113`/`fb4eb66e`).

## 2. Матрица «компонент → разрешённая версия → health → запущенный образ → состояние»

| Компонент | Ожидание smoke (журнал 16.09) | Ожидание canary (журнал 16.09) | Версия в health | Запущенный образ | Объявлено в контейнере | Состояние |
|---|---|---|---|---|---|---|
| web | `deddd35b` | `bd3f499c` | `6e6f11fd` | `omnia-web:6e6f11fd` (`b728e014…`), создан 14.09 10:07 | `6e6f11fd` | healthy; **ожидания устарели в обоих workflow** |
| api | `d3931113` | `bd3f499c` | `d3931113` | `omnia-api:d3931113` (`0049f286…`), создан 13.09 19:53 | `d3931113` | healthy; совпадает со smoke, не совпадает с canary |
| worker | `d3931113` | `bd3f499c` | `d3931113` (через api) | тот же `0049f286…` | `d3931113` | running; совпадает со smoke |
| generation-worker | — (smoke не проверяет) | `bd3f499c` | `d3931113` (через api) | тот же `0049f286…` | `d3931113` | running; тот же образ, что api/worker |
| orchestrator | `d3931113` | `bd3f499c` | `d3931113` (`:8003/health` и через api) | host-процесс systemd `omnia-orchestrator`, запущен 13.09 22:53:42 MSK из `/opt/omnia` | `d3931113` (`apps/orchestrator/.env`) | active |
| MAX-canary | URL `fitnessstat-a379d4.preview…` | — | HTTP 502 (воспроизведено 17.09) | **контейнера нет** | — | удалён 09.09, см. §4 |

**Разрешённая поставка.** Письменной записи разрешённой поставки для текущих версий нет:
последние записи в `/opt/omnia-runtime/releases` — `report-H154-fad210f0` (13.09 19:01 UTC).
Для `d3931113` (API/worker/orchestrator) и `fb4eb66e` → `6e6f11fd` (web) записи нет.
Фактическая последовательность восстановлена по reflog `/opt/omnia`:

- 13.09 22:53 MSK — fast-forward до `d3931113`, в ту же секунду рестарт orchestrator; api/worker пересозданы 13.09 22:53.
- 14.09 12:40 MSK — fast-forward до `fb4eb66e` (web).
- 14.09 13:05 MSK — fast-forward до `6e6f11fd`, web пересоздан в 13:07.

`d3931113..6e6f11fd` меняет только `apps/web` (10 файлов), поэтому код api/worker/orchestrator
на диске совпадает с объявленной им версией `d3931113`. Это вывод из diff, а не запись поставки.

**Вывод по `web.release_mismatch`:** web развёрнут корректно по ходу обычной поставки
(`deddd35b` → `6e6f11fd` содержит только маркетинговые изменения web), а ожидание в Actions
не обновили после поставки 14.09. Ошибка в ожидании, а не в развёртывании. Автоматически
подставлять `6e6f11fd` нельзя (правило плана): новое значение должен подтвердить владелец
поставки, поскольку записи разрешённой поставки нет.

**Вывод по generation canary:** общий `PRODUCTION_EXPECTED_RELEASE_SHA=bd3f499c` (04.09)
не совпадает ни с одним компонентом, так что `_assert_release_health()` отказал бы на первом же
сравнении. Это согласуется с остановкой до `release_health`, но точный подтип в журнале 16.09
не опубликован — гипотеза остаётся гипотезой до первого прогона с T22.4-диагностикой.

## 3. Скрытый дрейф конфигурации (риск, не отказ)

`apps/llm-gateway/deploy/full/.env` сейчас содержит `OMNIA_RELEASE_SHA=6e6f11fd`
и `API_IMAGE=omnia-api:d3931113`. Отрендеренный compose даёт api/worker/generation-worker
образ `d3931113`, но объявленную версию `6e6f11fd`.

Следствие: любой `docker compose up -d api` (или worker) без правки `.env` запустит тот же код
`d3931113`, а health начнёт сообщать `6e6f11fd`. Проверка идентичности станет «зелёной» при
неверной метке. Сейчас это не проявилось только потому, что api/worker не пересоздавались
с 13.09. Это нужно учесть до следующей поставки API (раздельные переменные версий web и API
в `.env`, либо обязательная сверка метки с тегом образа).

Дополнительно: теги `omnia-api:prod` (`0b3c7bb6…`) и `omnia-web:prod` (`d679e6ff…`) указывают
на старые образы и работающими контейнерами не используются — README поставки
(`infra/release/README.md`, §7) по-прежнему опирается на них, а фактический деплой — нет.

## 4. MAX-canary: идентичность и режим жизни

- Хост `fitnessstat-a379d4.preview.lead-generator.ru` сейчас обслуживает catch-all
  `/etc/nginx/sites-enabled/preview-default`, который **всегда** отвечает 502 с текстом
  «Omnia preview: no live project at this hostname». Тело ответа 17.09 — ровно этот текст.
- Проекта со slug `fitnessstat-a379d4` в платформенной БД нет; упоминаний `a379d4` нет
  ни в `projects`, ни в `max_project_configs`, `max_integrations`, `deploy_targets`,
  `project_cell_workspaces`, `project_cell_operations`; контейнеров и per-project nginx
  конфигураций нет.
- Журнал `omnia-orchestrator`: 09.09 17:15:30–35 MSK выполнены `docker.destroy_container`
  для `omnia-dev-fitnessstat-a379d4` и `omnia-app-fitnessstat-a379d4` и `nginx.unpublished`
  для обоих хостов. До этого (01.09–09.09) контейнер был обычной спящей preview-средой:
  `ingress.woke … was=exited`.
- Это был обычный проект (не выделенный служебный canary), и он удалён целиком.
  Кем инициировано удаление, по сохранившимся журналам не установлено: логи api
  пересозданного 13.09 контейнера этот период не содержат.
- В БД есть похожий `fitnessstat-8f97ff` (MAX mini app, создан 13.09) — это **другой** проект;
  подменять им canary URL нельзя без решения владельца (правило плана).

**Причина 502 установлена серверным доказательством:** canary URL указывает на удалённый
проект, запрос попадает в catch-all nginx. Это не падение runtime/core и не порт. Решение
(выделенный постоянный служебный canary) — предмет T22.3.

## 5. Настройки Actions

Прочитать переменные repository/environment не удалось: на этом Mac нет `gh` и GitHub-кредов,
а токен `gh` на сервере просрочен («Failed to log in … Artem-Levchenko-hub»). Репозиторий
публичный: история запусков доступна, но значения переменных и журналы — только с авторизацией.

Последние известные значения (из журналов 16.09, приведены в плане):

| Переменная | Где используется | Значение | Совпадает с prod |
|---|---|---|---|
| `PRODUCTION_EXPECTED_WEB_RELEASE_SHA` | smoke (repo-level, без environment) | `deddd35b` | нет (`6e6f11fd`) |
| `PRODUCTION_EXPECTED_API_RELEASE_SHA` | smoke | `d3931113` | да |
| `PRODUCTION_EXPECTED_WORKER_RELEASE_SHA` | smoke | `d3931113` | да |
| `PRODUCTION_EXPECTED_ORCHESTRATOR_RELEASE_SHA` | smoke | `d3931113` | да |
| `PRODUCTION_EXPECTED_RELEASE_SHA` | canary (`environment: production`) в `main` | `bd3f499c` | нет ни для одного |
| `PRODUCTION_EXPECTED_*_RELEASE_SHA` на уровне environment `production` | canary после T22.2 | **не считано** | — |
| `PRODUCTION_EXPECTED_GENERATION_WORKER_RELEASE_SHA` | canary после T22.2 (необязательная) | **не считано** | — |

Открыто: canary после T22.2 читает покомпонентные переменные внутри environment `production`.
Если они заданы только на уровне репозитория, environment-переопределения нет и будут взяты
repo-значения; если в environment остались старые — они перекроют repo. Нужна проверка с токеном.

## 6. Что из T22.1 не выполнено

- [ ] Настройки Actions — нужен действующий GitHub-токен (на сервере `gh auth login` или доступ владельца).
- [ ] Свежая резервная копия с подтверждённым восстановлением **до** смены production —
  не делалась; production в T22.1 не менялся. Пересекается с T22.5.
- [ ] Решение владельца: какая версия web разрешена (`6e6f11fd`?) и каким будет выделенный MAX-canary.
