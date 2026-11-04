# Free: суточный HTTP-бюджет и парковка опубликованного приложения

Дата: 2026-11-04. Bounded source audit; продукт, БД, конфигурация и production не менялись. Время начала: 17:00 UTC. База исследования — reader-release-recovery, предоставленный root HEAD `7684009f`; параллельные UI-правки не затронуты. Документ — предложение, не реализованная политика.

## Вывод

Free уже включает путь создания и одной публикации. Общего ограничения «10–50 любых HTTP-запросов к приложению за сутки» в проверенных runtime/SDK/ingress-путях нет. Есть отдельные ограничения генераций и runtime AI. Запись тарифа `always_on_slots=0` не является доказательством парковки опубликованной Project Cell.

Из вариантов 10/50 рекомендован **50 как начальный жёсткий бюджет допуска HTTP к Free-приложению**, с явным временем сброса и только после измерения первого запуска. Это очень небольшой демонстрационный бюджет: обещать полноценную ежедневную работу магазина по нему пока нельзя. 10 не рекомендованы для текущего Next/MAX-пути.

Ограничитель запросов сам по себе не освобождает RAM простаивающего опубликованного runtime. Для уменьшения постоянно занятой инфраструктуры нужен отдельный безопасный lifecycle парковки публичной Cell; текущий reconciliation восстанавливает её после простого останова.

## Что доказано исходниками

| Область | Текущее поведение | Точная точка |
| --- | --- | --- |
| Free v4 | 1 первоначальная генерация и 1 правка за всё время; 1 проект, 1 публикация; integrations=true; always_on_slots=0; preview_idle_minutes=15 | `apps/api/src/yleum_api/models/billing.py:190`; `apps/api/tests/test_entitlements.py:69` |
| Допуск публикации | Free резервирует один конкретный проект до отправки controller. Повтор того же проекта допустим; неопределённый исход/удаление не сбрасывают Free-резерв | `services/entitlements.py:300`, `_reserve_free_publication:464`; `tests/test_free_publication_allowance.py:52,102` в apps/api |
| Реальный MAX-путь | До Cell publication нужны verified/active MAX bot своего проекта и подтверждение документов; затем publish-slot guard | `apps/api/src/yleum_api/services/cell_publication.py:396–424` |
| Активация MAX | Нужны сохранённый бот и завершённый deploy со стабильным HTTPS; регистрируется `/api/max/webhook`, сохраняются app_url/webhook_url | `apps/api/src/yleum_api/routers/max_integrations.py:249–297` |
| Текущий owner runtime | API отказывает проекту без Cell; start/read идут через Project Cell, не через legacy dev provisioning | `apps/api/src/yleum_api/routers/runtime.py:132–175` |
| Owner wake | Есть fenced wake при resources_paused/retained. Возвращаемый статус содержит hibernate_after_seconds=None, keep_alive=False | `apps/api/src/yleum_api/services/project_cell_runtime.py:162–222`, `_wake_owner_workspace:311` |
| Старый idle timer | Каждые 60s перебирает только `omnia.kind=dev`; default Free=15min, paid=60min. Сигналы: heartbeat, Redis activity, рост RX; это не HTTP-счётчик | `apps/orchestrator/src/yleum_orchestrator/services/hibernate.py:1–36,110–158,213–307`; `core/config.py:289–296` |
| Старый wake-on-request | Nginx 502/503/504 ведут к `/_omnia/wake`; host преобразуется в legacy `omnia-dev-*`/`omnia-app-*` | `apps/orchestrator/src/yleum_orchestrator/services/nginx_writer.py:238–332`; `routers/ingress.py:29–100` |
| Compute reservation | Освобождение старой брони требует отсутствия live containers и незавершённых операций; published identity специально исключена | `apps/orchestrator/src/yleum_orchestrator/services/docker_cell_resources.py:1083–1154`; `tests/test_cell_idle_reservations.py:77,99,121,152` |
| Published reconciliation | Для не disabled публичной публикации `_reconcile_project()` вызывает `_start()`, возвращает gateway/TLS. Просто остановить контейнеры недостаточно | `apps/orchestrator/src/yleum_orchestrator/services/cell_publication.py:1570–1628` |
| Удаление public compute | Отдельная операция после durable disable удаляет serving compute, сохраняя data/backups. Это не существующая idle-парковка с пользовательским wake | `apps/orchestrator/src/yleum_orchestrator/services/published_machine_backend.py:124` |

Числа тарифа ранее в этой сессии совпали с публичным unauthenticated HTTP200 `https://yleum.ru/api/billing/plans`. Runtime env, Docker/Kubernetes state и внешний host-level Nginx в этом аудите не читались. Присутствие legacy-модуля в репозитории не означает применение его таймера к текущим Cell.

## Ресурсные потолки и что экономит парковка

`production_manager()` выделяет отдельный production profile, не уменьшает editor: `apps/orchestrator/src/yleum_orchestrator/services/cell_publication_capacity.py:12`. Defaults публичного product machine — 0.5 CPU / 2GiB, managed core — 0.2 CPU / 768MiB; это конфигурируемые **потолки**, не замер RAM и не стоимость Free (`core/config.py:337–342`).

Docker действительно получает memory/CPU/PID caps: product `docker_machine_backend.py:633–636`, Cell specs `docker_py_cell_backend.py:830–836`, trusted gateway `machine_adapter.py:1520–1546` (32MiB / 32 PID, отдельный startup CPU boost с последующим снижением). Это инфраструктурные профили, а не HTTP/day policy и не отдельный Free размер.

`CellResourceProfile.admission_quota` (`core/cell_resources.py:259–298`) отличает рабочую бронь от суммы hard caps. Освобождённая бронь допуска увеличивает доступную scheduler capacity; она не означает физическое освобождение такого же объёма RAM.

Корректный stop убирает CPU и resident memory остановленных процессов; Docker pause сохраняет их RAM. Данные, volumes, backups, дисковое место, адрес/TLS и общие сервисы остаются. Сохранённый per-app gateway/core/DB продолжает иметь свой расход. Проценты, рубли и число помещающихся приложений без текущего замера не утверждаются.

## Где идут HTTP и почему analytics не подходит

В production Cell Nginx направляет app-host traffic в controller-owned `machine_boundary`, тот маршрутизирует в generated product или trusted core. `/_next/static/` имеет отдельный Nginx location с immutable policy; прочие HTML/API ответы получают no-store (`nginx_writer.py:275–311`).

Общий контролируемый boundary: `BoundaryHandler` / `_forward()` в `apps/orchestrator/src/yleum_orchestrator/services/machine_boundary.py:414,542`. В нём есть auth, framing, canonical path, ограничения тела 8MiB и ответа 16MiB, upstream timeout120s; дневного HTTP-счётчика нет. `_forward()` обслуживает определённые HTTP verbs: placement только внутри этой функции пропустит методы, которые HTTP handler отклоняет раньше. Для «любых HTTP» admission нужен до метода/выбора upstream либо на общем ingress.

Пути SDK:

- `src/lib/omnia/business-client.ts:296` → `/api/omnia/business/{resource}`; exact orders/leads/bookings маршрутизируются boundary в product DB (`machine_boundary.py:560–635`).
- `src/lib/omnia/integration-client.ts:28` → `/api/omnia/integrations/*`; trusted core relay → platform `/api/runtime/projects/{project}/...`. Реализация: template `src/app/api/omnia/integrations/[...path]/route.ts:17`; platform `apps/api/src/yleum_api/routers/integration_runtime.py:187`.
- Catalog/profile, session, consent, telemetry, legal/core assets и ordinary generated routes тоже могут вызывать HTTP. Они не становятся заказами и не должны выпадать из visitor HTTP-бюджета.
- Подписанные server-to-server assertions и raw MAX initData — разные входы в platform `_runtime_context()`. Ограничение только app-host нельзя считать закрытием прямого platform capability path: для прямого внешнего запроса нужен тот же budget; trusted relay нельзя посчитать второй раз.

Имеющиеся ограничения другие:

- `apps/api/src/yleum_api/core/ratelimit.py:1–30` — in-memory rate buckets отдельных API dependencies; не общий durable per-app/day gate.
- `integration_runtime.py:343–370` — Redis/Lua лимиты только runtime AI: 8/min на пользователя, 120/day на пользователя, 2000/day на проект. Их нельзя переименовать в лимит любых HTTP.
- `template/src/lib/omnia/analytics.ts:1–58` и `routers/max_analytics.py` считают открытие/бизнес-события и доставку receipts, не HTML/assets/все API.

## Минимальная вертикаль для реализации после выбора политики

1. **Явно определить метр.** Один внешний HTTP к опубликованному app host = одна попытка: document, CSS/JS/images/fonts, core assets, session/bootstrap, GET/POST/HEAD/OPTIONS, dynamic routes, 304/4xx/5xx. Browser cache без HTTP не считается; HTTP cache validation считается. Не считать внутренний proxy hop повторно. Предлагаемый reset — календарные сутки UTC с видимым reset_at; это решение предложения, не существующее условие тарифа.
2. **Разделить наблюдение и допуск.** `observed_requests` может расти после N: сервер всё равно принимает и отклоняет попытки. `admitted_requests <= N` ограничивает обращение к runtime. Лимит не гарантирует физически не более N входящих TCP/HTTP, не ограничивает стоимость одного тяжёлого запроса, фоновые процессы или бесконечный stream.
3. **Один атомарный бюджет per project/day.** Использовать existing platform PostgreSQL с агрегированной строкой/атомарным admission либо durable shared Redis с доказанным persistence/restart behavior. Не `MemoryStorage`, не process-local dict, не RX bytes. Tier/limit берутся из доверенной платформы, project identity — из controller host mapping/config, не browser header/cookie/IP. При недоступном счётчике обычный public execution fail-closed; никаких бесплатных expensive fallbacks.
4. **Gate до продукта и wake.** Самый близкий существующий общий runtime-контур — trusted boundary, но для parked boundary нужен внешний shared ingress/controller admission. Для существующих активных Cell можно начать с boundary + закрытия direct platform ingress; отдельный Nginx location для assets не должен обходить admission. Все primary и alias/custom hosts одного app используют общий key. Ответ N+1 — маленький platform-owned 429 с Retry-After/reset_at; клиент не входит в reload/retry loop. Existing Nginx перехватывает 502/503/504 как legacy wake: ошибку счётчика нельзя случайно превратить в эту страницу пробуждения.
5. **Не блокировать отдельный доверенный служебный контур.** Owner studio/editor/preview идут своим аутентифицированным control path; internal health/readiness/ACME не расходуют visitor budget. OAuth callback находится на platform `/api/integrations/oauth/.../callback`, platform payment webhook — `/api/payments/yookassa/webhook`. MAX callback сейчас на app-host `/api/max/webhook`: нужна проверенная provider-authenticated callback lane, которая продолжает доставку/очередь при cap и parked runtime. Нельзя освобождать запрос только по пути, IP пользователя, User-Agent или присланному `X-Internal-*`; обычный внешний hit в health/callback не получает unlimited execution.
6. **Зафиксировать предел термина «любые».** N включает все виды обычных внешних app-запросов, а не только actions/API. Служебные callbacks/editor/probes находятся вне этого visitor budget и наблюдаются отдельно. Буквальное «N вообще всех запросов, включая обязательные callbacks» несовместимо с требованием продолжать принимать callbacks после N. Не публиковать более широкое обещание, чем выбранный контракт.

Суточный admission сам по себе экономит работу backend на N+1 и далее. Для экономии idle RAM завершённая вертикаль дополнительно требует **durable parked state публичной Cell**: reconcile распознаёт его и не перезапускает compute; live volumes/release/URL сохраняются; wake выполняет admission и fenced lifecycle с теми же данными, без новой генерации и без восстановления старого checkpoint поверх live data. Quota denial не будит приложение. Concurrent wake/park/publish/delete/callback обрабатываются по существующим production locks/fences. Это отдельное недостающее поведение; existing owner wake нельзя объявить готовым public visitor wake.

## 10 или 50

Первый публичный MAX вход уже содержит минимум четыре app HTTP: GET trusted bootstrap document → POST `/api/max/session` → GET `/__omnia/identity` → reload document. Это прямо в `machine_boundary.py:180–234,658–680`. Скрипт MAX со `st.max.ru` — отдельный сервер; далее добавляются собственные Next chunks/CSS/fonts, data/consent/catalog/session и prefetch/retry. Их количество зависит от приложения и состояния кеша; измерения здесь не проводились.

| Вариант | Вывод |
| --- | --- |
| 10 любых app HTTP/сутки | Очень вероятен обрыв первого полного cold path или следующего клика. Не рекомендован без специализированной очень малой single-response архитектуры и доказанного E2E. |
| 50 любых app HTTP/сутки | Рекомендуемый из этих двух стартовый вариант только после cold E2E; может хватать лишь на несколько действий/одно открытие. Не равно 50 посетителям/заказам и не гарантирует работающий магазин весь день. |

Бюджет не следует тайно увеличивать для assets и auth: при требовании «любых» они тоже расходуют N. Снижение потребления first load можно рассматривать после замера (production build, cache, отсутствие лишнего prefetch/polling), сохраняя правильный auth/data flow. Static hosting для совместимых приложений мог бы уменьшить постоянный runtime, но универсальный перевод текущих Next/generated apps в static не доказан и не является маленьким переключателем.

## Проверка и rollback

До enforcement нужен один безопасный synthetic cold E2E без новых платных генераций: существующий disposable опубликованный app, реальный MAX bot/launch, desktop/mobile, пустой и тёплый кеш. Считать aggregate methods/path classes/status/count только на входе, без URL queries, cookies, initData, user IDs, source или payload. Пройти create/build evidence → publish → MAX URL launch → read/create собственных тестовых данных → refresh/reconnect. Для этого аудита live-проверка не выполнялась.

Обязательные falsifying проверки реализации:

- N=10/50: ровно N admitted requests, N+1=429; параллельный burst не превышает N. GET/HEAD/OPTIONS/assets/304/errors покрыты; прямой platform path не обходит gate, relay не удваивает расход.
- День/reset/timezone, два app/два owner, alias hosts одного app, process/redeploy/Redis-or-DB restart, paid transition — без сброса/утечки чужого лимита. Недоступный meter не запускает expensive runtime и не попадает в legacy wake loop.
- Cap не ломает provider-authenticated MAX/payment callbacks, OAuth completion, internal readiness и owner editor; forged exemption headers не дают обхода. После queued callback данные и итоговый статус подтверждены обычным readback.
- Idle park действительно освобождает нужные processes/reservations, public reconcile не будит их, volume/data/release identity сохранены. Cold wake проходит capacity admission; concurrency даёт один wake, без генерации/оплаты/rollback live DB; exhausted budget не будит Cell.
- В день после reset первоначальный cold user path снова работает. Для 10 отдельно доказать, что его вообще можно закончить; для 50 доказать реальный остаток после cold bootstrap.

Existing тесты для расширения, **не запускались в этом source audit**:

- API: `test_free_publication_allowance.py`, `test_entitlements.py`, `test_project_cell_runtime_api.py`, `test_cell_publication_api.py`, `test_integration_runtime_auth.py`, `test_runtime_ai_gateway.py`, `test_ratelimit.py`.
- Controller: `test_hibernate.py`, `test_ingress.py`, `test_cell_idle_reservations.py`, `test_cell_publication_capacity.py`, `test_machine_boundary.py`, `test_public_core_runtime.py`, `test_published_machine_backend.py`, `test_nginx_writer.py`.

Rollback новой политики должен отдельно отключать enforcement и public idle parking, сохраняя counters/audit и live volumes. Перед возвратом к прежнему reconciliation нужно явно разморозить выбранные parked apps под admission/fence; не терять ownership и не запускать все без проверки host capacity. Нельзя откатывать DB/data ради отмены лимита. В этом аудите никаких переключателей и migrations не добавлено.

## MAX и «маркет»

Подключение Mini App в MAX — HTTPS URL у конкретного бота, а не автоматическая публикация во все каталоги/магазины. Текущие официальные инструкции требуют профиль платформы, создание и модерацию бота; затем владелец задаёт URL и кнопку в настройках. Наличие bot token или Yleum public URL само по себе не доказывает завершение внешнего шага.

Первичные источники проверены 2026-11-04:

- [MAX: подключение Mini App, HTTPS URL и кнопка](https://dev.max.ru/docs/webapps/introduction).
- [MAX: выбор сервисов и модерация бота](https://dev.max.ru/docs/maxbusiness/selectionservices).
- [NGINX limit_req](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html): leaky-bucket rate, не durable календарный daily quota.
- [NGINX auth_request](https://nginx.org/en/docs/http/ngx_http_auth_request_module.html): 2xx допускает, 401/403 запрещает, другие статусы — ошибка. Нельзя считать bare 429 quota-subrequest готовым корректным edge 429; наличие модуля и mapping надо проверить отдельно.

App Store/Google Play, отдельный MAX marketplace placement/moderation и прочие значения «маркет» здесь не реализованы и не принимались. Обещание относится только к подтверждённому каналу после его обычных внешних prerequisites.
