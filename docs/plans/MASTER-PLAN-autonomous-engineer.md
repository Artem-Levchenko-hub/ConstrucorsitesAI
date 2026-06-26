# Omnia — Master Plan: автономный ИИ-инженер

> Источник истины для «доделать идею до конца». Этот документ интегрирует 8 воркстримов,
> снимает все P0/P1 из adversarial-ревью и задаёт фазовую дорожную карту с флагами.
> Русская проза; идентификаторы/пути — на английском.
>
> **Статус методологический (честно).** Тела воркстримов F (Deploy/Export) и G (Delight/UX),
> переданные синтезатору, пришли пустыми (плейсхолдеры) — это и есть P0 фидбэк-ревьюера, и он
> прав. Поэтому настоящий план собран НЕ «склейкой статус-строк», а заново — на прямом чтении
> реального кода (см. file:line ниже) и зафиксированных решений-якорей. Каждое утверждение о
> существующем поведении привязано к строке в репозитории. Там, где воркстрим-тело отсутствовало,
> раздел спроектирован с нуля как часть мастер-плана, а не «восстановлен».

---

## 0. Executive summary (1 страница)

**Заголовок:** **Omnia = автономный ИИ-инженер, который строит настоящие приложения.**

Не managed-CRUD-рантайм, не конструктор-песочница. Пользователь говорит «создай что угодно, чего
пожелает душа» — а Omnia запускает **агентный цикл** `plan → act → observe → verify → fix until green`
на **одном фиксированном промышленном скелете**, и выдаёт **реальный, экспортируемый production-код**,
который пользователь владеет и может унести.

**Что уже есть (проверено по коду).** Агентный ReAct-цикл — не план, а работающий движок:
`apps/api/src/omnia_api/services/agent_builder.py` (796 строк). 11 инструментов
(`list_dir/read_file/grep/write_file/edit_file/build/bash/read_logs/runtime_check/see/done` —
`agent_builder.py:31-44`), цикл-брейкеры (`agent_builder.py:340-430`), эскалация модели
(`agent_builder.py:241-258`), оконный контекст ради стоимости (`agent_builder.py:153`,
`_window_messages`), реальный исполнитель против живого dev-контейнера через orchestrator
(`make_container_executor`, `agent_builder.py:663-796`). Флаг `use_agentic_builder` (default `False`,
`config.py:739`), точки вызова — `messages.py:2253, 2301, 2402, 2440, 2508, 2687`. Стек-скелеты уже
лежат в `apps/orchestrator/templates/`: `nextjs-postgres-drizzle` (TS-unified), `fastapi-postgres`
(Python), `nextjs-realtime` (WS-чат), `vite-react-spa`, `nextjs-entities` (на пенсию), `telegram-bot-aiogram`.

**Зафиксированные решения (не пересматриваем — проектируем ПОД них):**

1. Дефолтный скелет — **TS-unified**: Next.js 15 + Drizzle + Postgres + WS-gateway + RLS/policy.
   Второй благословлённый — **FastAPI + SQLModel + Alembic** для Python/compute/ML.
   Lane `code` для скриптов, lane `static` для одностраничников.
2. **`entities` как backend-архитектура — на пенсию.** Его schema-DSL может выжить ОПЦИОНАЛЬНО
   как генератор НАСТОЯЩЕГО кода (компиляция в Drizzle/SQLModel), но НЕ как скрытый рантайм.
3. **Надёжность = жёсткий acceptance-gate**, который БЛОКИРУЕТ ship на «красном», и применяется
   к контейнерным приложениям тоже (чиним баг пропуска гейта).
4. **Per-project hardened sandbox обязателен** (агент гоняет произвольный код + bash).
5. **Существующие entities-приложения НЕ ломаем** — продолжают работать; новый скелет только
   для НОВЫХ билдов; всё под `USE_*`-флагами с мгновенным откатом.

**Acceptance-тест всей идеи — корпоративный мессенджер.** Если автономный агент на скелете
`nextjs-realtime` строит работающий мессенджер (auth → каналы → real-time сообщения → presence →
membership ACL), который проходит жёсткий гейт и деплоится в прод — тезис доказан. Это и есть
финальная веха дорожной карты.

**Честный масштаб усилий.** Это не «допилить за вечер». Это ~6 фаз, каждая под флагом, с E2E на
живом контейнере как exit-критерием. Костяк (агент-цикл, 2 скелета, sandbox-база, deploy) уже стоит;
работа — довести цикл до green-надёжности, починить gate-skip, выкатить настоящий sandbox, замкнуть
export, и сделать процесс «восторгом». Ориентир — недели, не дни; см. §8.

---

## 1. Тезис → архитектура (как части складываются)

Один цикл, правильные примитивы. Агент не «знает» managed-рантайма — он редактирует **реальные
файлы реального скелета** и наблюдает **реальный мир** (typecheck, dev-server logs, HTTP-статус
маршрута, скриншот). Надёжность не от клетки, а от **петли проверки до зелёного**.

```
                        ┌────────────────────────── apps/web (Next.js) ──────────────────────────┐
   prompt  ───────────► │  чат → live preview (/p/<slug>) → timeline → "агентные шаги" (WS)        │
                        └──────────────────────────────────┬─────────────────────────────────────┘
                                                            │ POST /messages
                        ┌───────────────────────────────────▼─────────────────── apps/api ────────┐
                        │ discovery.py  ── выбор скелета (static/spa/ts-unified/fastapi/realtime/   │
                        │                  code) + детерминированные «полы» (_infer_*)              │
                        │           │                                                               │
                        │ stack_routing.py ── switch_to_stack + ensure_provisioned (флип шаблона,   │
                        │           │          re-scaffold git, provision контейнера)               │
                        │           ▼                                                               │
                        │ agent_builder.run_agent_build  ◄── ЕДИНЫЙ движок (plan→act→observe→fix)   │
                        │   system = LOOP_PROTOCOL + <stack>/SYSTEM_PROMPT.md                       │
                        │   executor = make_container_executor(project_id, slug)                    │
                        │           │                                                               │
                        │  acceptance gate (jobs/queue) ── БЛОКИРУЕТ ship; теперь и для контейнеров │
                        └───────────┼───────────────────────────────────────────────────────────-─┘
                                    │ httpx (internal token)
                        ┌───────────▼─────────────────────────────── apps/orchestrator (host) ────┐
                        │ /agent/{read-file,list-dir,grep,build,exec}  (runtime.py:384-528)         │
                        │ /hot-reload  /runtime-status  /deploy  /export(NEW)                       │
                        │ docker_client → per-project dev-контейнер (gVisor/microsandbox NEW)       │
                        │ nginx vhost (*.preview)  → prod deploy (TS: Node; Py: uvicorn)            │
                        └──────────────────────────────────────────────────────────────────────────┘
                                    │
                        ┌───────────▼───────────┐
                        │ Postgres (per-project  │  Redis (WS hub / presence)   MinIO (assets/export)
                        │ schema + RLS policy)   │
                        └────────────────────────┘
```

Все 8 воркстримов — это срезы ОДНОГО конвейера. Ниже — сведённые, без дублей.

---

## 2. Воркстрим A — Скелеты и retire `entities`

**Цель.** Сделать `nextjs-postgres-drizzle` дефолтным НАСТОЯЩИМ backend-скелетом для новых
account/CRUD-приложений вместо `nextjs_entities`; оставить `fastapi-postgres` для Python; не сломать
живые entities-аппы.

**Что есть.** Маршрутизация скелетов в `discovery.py:54-57` (`_STACKS`), маппинг типа результата в
`discovery.py:67-79` (`_RESULT_TYPE_TO_STACK` — сейчас `web_app → nextjs_entities`), детерминированные
полы `_infer_stack_from_text` (`discovery.py:204`) и `_infer_realtime_from_text` (`discovery.py:241`).
Флип шаблона — `stack_routing.switch_to_stack` (`stack_routing.py:75`), provision —
`ensure_provisioned` (`stack_routing.py:212`). Группа контейнерных стеков — `CONTAINER_NEXT`
(`messages.py:1557`).

**Изменения (конкретно):**

1. Новый стек-литерал `ts_unified` (или переиспользовать `nextjs_postgres_drizzle`). Добавить в
   `_STACKS` (`discovery.py:54`) и в `CONTAINER_NEXT` (`messages.py:1557`).
2. Маппинг `_RESULT_TYPE_TO_STACK["web_app"]` (`discovery.py:69`) переключить
   `nextjs_entities → ts_unified` — **только под флагом** `USE_TS_UNIFIED_DEFAULT` (default `False`).
   При OFF поведение байт-в-байт прежнее.
3. `_infer_stack_from_text` (`discovery.py:204-214`) возвращает `ts_unified` вместо `nextjs_entities`
   за тем же флагом.
4. `discovery_stack_to_template` (`stack_routing.py:66`) добавляет ветку
   `ts_unified → "nextjs-postgres-drizzle"`.
5. **entities остаётся в коде и в `_STACKS`** — живые проекты с `project.stack == "nextjs_entities"`
   роутятся как раньше; новый флаг влияет ТОЛЬКО на новые билды (роутинг по `_RESULT_TYPE_TO_STACK`
   и `_infer_*`, которые срабатывают на first-build).
6. **DSL-как-генератор (опционально, отдельный флаг `USE_ENTITY_DSL_CODEGEN`, поздняя фаза).**
   Если оставляем entity-DSL — он компилирует `entities/*.json` в РЕАЛЬНЫЕ `schema.ts` (Drizzle) +
   серверные actions, коммитит их как обычные файлы скелета. Никакого скрытого рантайма. Это не
   блокер MVP-мессенджера; держим за флагом, включаем когда основной путь зелёный.

**Риск/митигация.** Drizzle-скелет уже несёт Auth.js-таблицы и managed auth-колонки, которые модель
норовит снести — это уже защищено детерминированной ре-инъекцией (`_AUTH_USERS_COLUMNS`
`messages.py:1567`, `_AUTH_TABLES_BLOCK` `messages.py:1576`). Сохранить эту защиту при переходе
дефолта; добавить в acceptance-gate проверку «schema.ts экспортирует users/accounts/sessions/
verificationTokens» (см. §5).

---

## 3. Воркстрим B — Агент-цикл до green-надёжности

**Цель.** Довести существующий цикл до состояния, когда он стабильно доводит билд до зелёного, а не
упирается в цикл-брейкеры.

**Что есть.** Цикл `run_agent_build` (`agent_builder.py:207`), ретраи на gateway-ошибках 5×backoff
(`agent_builder.py:290-310`), оконный контекст + progress-note (`agent_builder.py:275-288`),
консекутив-репит гард (`agent_builder.py:380-398`), глобальный cycle-гард с эскалацией
(`agent_builder.py:355-378`), no-write-streak брейкер (`agent_builder.py:410-420`), exempt
verify-actions (`agent_builder.py:75`, `_VERIFY_ACTIONS`). Константы: `_NO_WRITE_ABORT_AT = 14`
(`:460`), `_REPEAT_NUDGE_AT = 2` (`:465`), `_REPEAT_ABORT_AT = 4` (`:466`).

**Изменения:**

1. **Гарантированный verify-перед-done.** Сейчас `done` принимается без проверки, что последний
   `build`/`runtime_check` был зелёным (`agent_builder.py:330-336`). Добавить инвариант: если
   `last_build_ok is not True` ИЛИ не было `runtime_check` после последнего write — `done`
   отклоняется с нуждж «сначала build+runtime_check». Это структурно поднимает долю зелёных билдов
   и питает acceptance-gate (§5). Под флагом `AGENT_REQUIRE_GREEN_BEFORE_DONE` (default ON после Phase 2).
2. **Бюджет шагов по сложности.** `max_steps=12` (`agent_builder.py:215`) маловат для мессенджера.
   Сделать функцией скелета/сложности: static/spa edit — 12; ts_unified first-build — 40;
   realtime — 60. Источник — discovery-сложность; передать в `run_agent_build`.
3. **Self-heal как часть цикла, не отдельный заход.** Точки `_heal`/`_fheal`
   (`messages.py:2508, 2687`) — оставить как safety-net, но первичный путь — verify-перед-done (п.1).
4. **Стоимость под контролем (без упоминания денег в UX).** Оконный контекст уже есть; добавить
   prompt-cache на статический системный промпт (LOOP_PROTOCOL + stack-guide неизменны в рамках билда).

**Риск.** Дешёвая модель деградирует в длинном цикле (это известно из памяти проекта — vsegpt 1req/s
+ char-billing + cheap-model loop-degeneration). Митигация: эскалация уже встроена
(`agent_builder.py:241`); для realtime-скелета поднять стартовую модель сразу (не cheap), т.к.
бюджет шагов высок и деградация дороже эскалации. Остаточный риск: на совсем длинных билдах цикл
может упереться в `_NO_WRITE_ABORT_AT`/`_REPEAT_ABORT_AT` — это by design (fail fast), gate поймает
незелёный результат и не зашипит.

---

## 4. Воркстрим C — Real-time (мессенджер как acceptance-тест)

**Цель.** Скелет `nextjs-realtime` + агент-цикл строят корпоративный мессенджер, проходящий гейт.

**Что есть.** Шаблон `nextjs-realtime` существует (Dockerfile.dev/prod, SYSTEM_PROMPT.md, src,
drizzle.config.ts, scripts). Стек `realtime` в `_STACKS` (`discovery.py:54`), детект
`_infer_realtime_from_text` (`discovery.py:241`), в `CONTAINER_NEXT` (`messages.py:1557`). Память
проекта: «realtime = SSE+Redis hub + membership ACL + presence».

**Изменения:**

1. **Presence — самая дорогая steady-state-нагрузка (структурно квадратична).** В скелете presence
   делать через Redis (TTL-heartbeat + pub/sub на канал), НЕ через in-memory per-node — чтобы
   экспортируемый код был корректен и масштабируем. Зафиксировать паттерн в
   `nextjs-realtime/SYSTEM_PROMPT.md` как обязательный примитив (агент не изобретает presence заново).
2. **Membership ACL на сервере** (RLS/policy, §6) — сообщение видно только членам канала. Это
   acceptance-критерий мессенджера: посторонний не читает чужой канал (есть прецедент isolation-теста
   в gold-clinic-stranger-isolated проверках).
3. **runtime_check для WS.** Текущий `runtime_check` бьёт HTTP-маршрут (`agent_builder.py:758-778`).
   Для WS добавить агентный примитив/чек «open WS, отправь сообщение, получи его на втором клиенте»
   — иначе цикл «увидит» только HTML-оболочку, не realtime-поведение. Реализовать как `bash`-скрипт
   в контейнере (playwright two-tab) или новый orchestrator endpoint `/agent/ws-check`.

**Риск.** WS-проверка в цикле — медленная и flaky. Митигация: держать её как ОДИН verify-шаг ближе к
концу билда, не на каждой итерации; на flaky — ретрай 2×, затем degrade в HTTP-runtime_check (не
блокировать билд бесконечно). Остаточный риск: presence quadratic-load в проде на больших каналах —
честно вне scope MVP-беттеста (50 пользователей), документируется как known-limit.

---

## 5. Воркстрим D/E — Acceptance-gate для контейнеров (P0 надёжности)

**Цель.** Закрыть **известный баг #1**: контейнерные приложения ПРОПУСКАЮТ acceptance-gate.

**Что есть (баг, дословно из кода).** `messages.py:1550-1557`: контейнерные React-шаблоны
«…skip the static-only guards … **and the landing-page acceptance gate**». То есть build, который не
typecheck-ится / 500-ит на рендере, может быть зашипен. Гейт сегодня — для статики;
`use_acceptance_gate` (`config.py:245`, default True) на контейнеры не распространяется.

**Изменения:**

1. **Container acceptance-gate** — новый блокирующий пост-билд чек для `CONTAINER_NEXT`, питается
   наблюдениями агента, которые УЖЕ есть: последний `build` (typecheck) зелёный
   (`runtime.py:461` agent_build), `runtime_check` ключевых маршрутов = 2xx
   (`agent_builder.py:758`), и для realtime — ws-check (§4). Флаг
   `USE_CONTAINER_ACCEPTANCE_GATE` (default `False` → включаем в Phase 3).
2. **Контракт гейта:** на «красном» — НЕ менять `current_snapshot_id`, вернуть в чат карточку
   ошибки (механизм карточек уже есть, `use_error_cards` `config.py:365`) + авто-self-heal заход
   (`run_agent_build` heal-режим, `messages.py:2508`). Ship только на зелёном.
3. **Acceptance-проверки для ts_unified:** (a) typecheck clean; (b) `/`, `/signin`, `/dashboard`
   рендерят без 5xx; (c) анонимный `/dashboard` → 307 на `/signin` (auth-floor, есть прецедент
   `src/middleware.ts`); (d) schema.ts экспортирует 4 auth-таблицы (§2).
4. **Переиспользовать существующий gate-движок.** Не плодить второй гейт: расширить текущий
   acceptance-путь так, чтобы для `project.stack in CONTAINER_NEXT` источником сигналов были
   orchestrator runtime-пробы, а не статический скриншот.

**Риск.** Гейт может ложно блокировать на медленном холодном контейнере (remote-MinIO картинки не
успевают — известный артефакт замера из памяти). Митигация: гейт судит **код-сигналы** (typecheck,
HTTP-статус), НЕ перцепцию по умолчанию; vision-судья (`see`) остаётся advisory, не блокирующим
(флаг `acceptance_vision_block_enabled` уже отдельный, `config.py:401`). Остаточный риск: false-green
если runtime_check бьёт не тот маршрут — митигация: гейт требует чек ВСЕХ маршрутов из discovery-плана.

---

## 6. Воркстрим (RLS/policy) — серверная изоляция арендатора

**Цель.** Дать экспортируемому коду ту же гарантию владения, что давал entities-движок, но честным,
портируемым способом — Postgres RLS (force row level security).

**Изменения:**

1. В `nextjs-postgres-drizzle` и `nextjs-realtime` скелетах: каждая пользовательская таблица несёт
   `owner_id`/`channel_id`; миграция включает `ALTER TABLE … ENABLE ROW LEVEL SECURITY` +
   `FORCE ROW LEVEL SECURITY` + policy, привязанную к `current_setting('app.user_id')`.
2. Серверный слой (server actions / route handlers) ставит `SET LOCAL app.user_id = …` из JWT в
   начале транзакции. Даже сырой запрос не видит чужие строки.
3. Зафиксировать паттерн в обоих SYSTEM_PROMPT.md как обязательный — агент не должен «изобретать»
   ACL в коде приложения; политика — на уровне БД.

**Риск.** Агент может забыть `SET LOCAL` → пустые результаты или утечка. Митигация: вынести в
ФИКСИРОВАННЫЙ template-файл (`src/lib/db/scoped.ts`), который агент НЕ переписывает (как auth.ts);
acceptance-gate проверяет наличие RLS-политики на новых таблицах (`SELECT … FROM pg_policies`).
Остаточный риск: сложные cross-tenant отчёты потребуют bypass-роли — документируется, вне MVP.

---

## 7. Воркстримы F + G — Deploy/Export и Восторг (восстановлены с нуля — были пустыми в драфте)

> Эти два раздела пришли синтезатору ПУСТЫМИ (P0 ревьюера). Здесь они спроектированы как часть
> мастер-плана, привязаны к реальному коду.

### 7.1 Деплой в прод + экспорт (бывш. F)

**Что есть.** Orchestrator deploy-эндпоинты: `POST /deploy` (`runtime.py:545`), `GET /deploy`
(`runtime.py:562`), deploy-state record (`_deploy_record_to_response`, `runtime.py:530`).
Dockerfile.prod есть у каждого скелета (`nextjs-postgres-drizzle/Dockerfile.prod`,
`nextjs-realtime/Dockerfile.prod`, `fastapi-postgres`). wake-on-request инфра жива (из памяти).

**Изменения:**

1. **Prod-build без Next-coupling (известный half-done blocker из ресёрча).** Деплой ts_unified —
   `Dockerfile.prod` (Node standalone), realtime — Node + WS, fastapi — uvicorn. Каждый скелет
   деплоится своим Dockerfile.prod; orchestrator `/deploy` выбирает по `project.stack`. Это снимает
   «Next-coupled prod-build» как блокер entities→fullapp.
2. **Export = реальный, переносимый репозиторий.** Новый orchestrator endpoint `GET /export`
   (рядом с `/agent/read-file`, `runtime.py:384`): tar.gz рабочего git-дерева контейнера + README с
   инструкцией `docker compose up`. Drizzle-миграции (`drizzle-kit generate`) уже версионируются →
   код самодостаточен. Для fastapi — Alembic-миграции в дереве. Флаг `USE_PROJECT_EXPORT`.
3. **Push-to-GitHub** (бэкенд готов по памяти, db77cbc) — дотянуть UI-кнопку (зона A).

**Риск.** Egress в dev-контейнере открыт (`runtime.py` комментарий «Egress lockdown is a follow-up»).
Для prod-деплоя это не блокер; для sandbox — см. §8 Phase 1. Остаточный риск: prod-ресурсы на одном
VPS — масштаб беттеста (50) выдержит; за порогом нужен оркестратор нод (вне MVP).

### 7.2 Восторг от процесса / UX (бывш. G)

**Что есть.** Агентные шаги уже стримятся в UI структурно: `_agent_emit` → WS-событие `agent.step`
с `kind` (step/escalate/stalled/retry) и `path` (`messages.py:2275-2300`). Карточки ошибок в чате
(`use_error_cards`). Timeline-превью, rollback за 1 сек (Done-знаки MVP).

**Изменения:**

1. **«Как Claude Code» лента шагов** — рендерить `agent.step` как живой список инструментов
   (читаю файл → пишу → собираю → проверяю маршрут → смотрю) с путями. Данные уже летят; нужен
   фронт-компонент в `apps/web`. Это и есть «восторг»: пользователь ВИДИТ инженера за работой.
2. **Антиципация без вранья.** Прогресс-бар отражает реальные фазы (provision → build → verify →
   deploy), не фейковый таймер. На `escalate`/`stalled` — честная микрокопия («усиливаю модель»,
   уже в `_agent_emit`).
3. **Verify-as-spectacle.** Когда агент делает `see`/`runtime_check` зелёным — показать «проверил:
   маршрут рендерит, выглядит так» со скриншотом. Превращает надёжность в видимую ценность.

**Риск.** Слишком болтливый поток шагов перегружает UI. Митигация: схлопывать однотипные шаги,
показывать последние N (как окно контекста в `_window_messages`). Остаточный риск: на длинном
realtime-билде лента длинная — свернуть в «N шагов, развернуть».

---

## 8. P0/P1 из критики — разрешение (inline)

| # | Критика | Разрешение |
|---|---------|-----------|
| **P0** (feasibility) | Черновики F и G пусты — нечего ревьюить | Признано и исправлено: F и G спроектированы заново в §7 на реальном коде с file:line. Мастер-план НЕ склеивает статус-строки. |
| **P1.1** | Восстановить фактический Markdown F и G | §7.1 / §7.2 — полные секции с якорными путями (`runtime.py:545`, `messages.py:2275`). |
| **P1.2** | Оркестратор должен передавать ТЕЛО ответа воркстрима, не статус-строку | Зафиксировано как процессное требование (см. §9, «контракт воркстрим-вывода»): пост-условие на каждый sub-agent. |
| **P1.3** | Пост-условие: непусто + `##` + якорный путь + ≥1 file:line, иначе реджект+рестарт | Принято как gate воркфлоу — §9. Настоящий документ ему удовлетворяет. |
| **P1.4** | Перезапустить adversarial-проход с непустым черновиком | Этот документ — непустой черновик; готов к повторному ревью. |
| risk-критика | «Complete» (пусто) | Нечего разрешать; sandbox/cost/presence-риски разобраны inline в §3–§7 и §8 ниже. |

**Сквозные риски, не закрытые воркстримами (честно):**

- **Sandbox (P0 из якорей).** Сегодня dev-контейнер cap-dropped/non-root/mem-capped/isolated-net/
  schema-scoped DB-роль + denylist (`runtime.py` agent_exec комментарий), но **egress открыт** и это
  НЕ gVisor/microVM. Агент гоняет произвольный bash — это реальный риск. **Митигация (Phase 1,
  блокирующая широкий rollout):** gVisor (runsc) ИЛИ microsandbox как runtime для dev-контейнеров +
  egress allowlist (только npm/pip-registry + gateway). Остаточный риск до Phase 1: широкий публичный
  доступ держать закрытым, агент-флаг ON только на доверенных проектах.
- **Cost/устойчивость gateway.** vsegpt 1req/s + char-billing. Митигация: окно контекста
  (`_window_messages`) + prompt-cache + эскалация только при застревании. Остаточный: длинные
  realtime-билды дороже — приемлемо, т.к. за гейтом и редки.
- **Presence quadratic-load** — §4, вне MVP-scope, документируется.

---

## 9. Контракт воркстрим-вывода (фикс процесса, чтобы P0 не повторился)

Любой sub-agent, возвращающий design-секцию, проходит пост-условие ПЕРЕД склейкой:
непусто; содержит заголовок `##`; ≥1 якорный путь (`apps/api` | `apps/orchestrator` | `apps/web`);
≥1 `file:line`. Провал → реджект + рестарт sub-agent'а, НЕ передача дальше. Оркестратор склеивает
ТЕЛО ответа (возвращаемое значение), не финальную статус-реплику.

---

## 10. Фазовая дорожная карта (флаги, entry/exit)

Все фазы — dark behind `USE_*`, мгновенный откат. Exit каждой фазы — **E2E на живом
provisioned-контейнере зелёный** (паттерн из памяти проекта: «verified by live provisioned-container
E2E»), не только unit.

### Phase 0 — ГОТОВО (проверено)
Агент-цикл существует, флаг `use_agentic_builder` (`config.py:739`). 11 инструментов, цикл-брейкеры,
эскалация, executor против контейнера. Exit: ✅ (11 green unit-тестов по памяти).

### Phase 1 — Sandbox hardening (БЛОКЕР широкого rollout)
- gVisor/microsandbox runtime для dev-контейнеров + egress allowlist.
- Entry: Phase 0. Exit: агент гоняет bash, blast-radius доказанно ограничен (E2E: попытка egress на
  произвольный хост заблокирована; попытка выхода из контейнера провалена).

### Phase 2 — Агент-цикл до green + container acceptance-gate
- `AGENT_REQUIRE_GREEN_BEFORE_DONE` ON; бюджет шагов по сложности; `USE_CONTAINER_ACCEPTANCE_GATE`.
- Чинит баг #1 (`messages.py:1550` skip-gate).
- Entry: Phase 1. Exit: ts_unified first-build на живом контейнере — typecheck clean + 3 маршрута
  2xx + анон `/dashboard`→307, на «красном» НЕ шипится (E2E green).

### Phase 3 — ts_unified как дефолт для web_app + RLS
- `USE_TS_UNIFIED_DEFAULT` ON; RLS/policy в скелете + gate-проверка политик.
- entities-аппы не тронуты (роутинг по stack живых проектов прежний).
- Entry: Phase 2. Exit: новый «CRM для X» билдится на drizzle-скелете, чужой пользователь НЕ видит
  чужие строки (live isolation E2E green).

### Phase 4 — Real-time мессенджер (ACCEPTANCE-ВЕХА ВСЕЙ ИДЕИ)
- realtime presence через Redis + membership ACL (RLS) + ws-check примитив.
- Entry: Phase 3. **Exit / MILESTONE:** автономный агент строит корпоративный мессенджер
  (auth → каналы → real-time сообщения между двумя клиентами → presence → посторонний изолирован),
  проходит container-gate, деплоится в прод, health 200. **Тезис доказан.**

### Phase 5 — Deploy/Export + Восторг-UX
- per-stack `/deploy` (Dockerfile.prod), `GET /export` (tar + миграции), push-to-GitHub UI,
  «Claude-Code» лента шагов + verify-as-spectacle.
- Entry: Phase 4. Exit: пользователь экспортирует мессенджер, `docker compose up` поднимает его
  локально из коробки; в UI виден живой ход инженера (E2E + ручная проверка экспорта).

### Phase 6 — Опционально: entity-DSL → real codegen
- `USE_ENTITY_DSL_CODEGEN`: DSL компилирует в Drizzle/SQLModel-код, не рантайм.
- Entry: Phase 5. Exit: entities-аппа, пересобранная через codegen, эквивалентна по поведению, но
  это РЕАЛЬНЫЙ экспортируемый код.

### Что режем, если прижмёт (cut-list, по убыванию готовности резать)
1. Phase 6 (entity-DSL codegen) — целиком, держим entities-аппы as-is на старом рантайме.
2. Export tar (7.1.2) — деплой важнее экспорта; export можно после.
3. push-to-GitHub UI — бэкенд готов, кнопка подождёт.
4. vision-as-spectacle (7.2.3) — лента шагов важнее скриншот-восторга.
5. fastapi-postgres lane как дефолт — оставить только для явных Python-задач; не блокирует мессенджер.

**Нельзя резать:** Phase 1 (sandbox), Phase 2 (gate-fix + green-цикл), Phase 4 (мессенджер-веха) —
это и есть доказательство тезиса.

### Масштаб усилий (честно)
Костяк стоит (агент-цикл, 2 скелета, realtime-шаблон, deploy-эндпоинты, флаг-паттерн). Объём —
довести цикл до надёжного зелёного, выкатить настоящий sandbox, починить gate-skip, RLS, замкнуть
export и UX. Это недели инженерной работы фазами, каждая за флагом с живым E2E как воротами, а не
разовый патч. Полумер и prototype-tier нет — каждая фаза доводится до прода или не включается.
