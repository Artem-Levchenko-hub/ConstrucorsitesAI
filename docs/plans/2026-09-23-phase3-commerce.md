# Фаза 3 — Commerce: подписки, биллинг, ЮKassa и автозаказ VPS

Статус: код готов, боевое включение ждёт данных владельца (креды ЮKassa, реквизиты оператора,
токен API Serverum). Дата: 23.09.2026. Ветка/коммиты: `a138693b` (API), `baa078d5` (клиент
Serverum), `fdece389` (инфраструктура), плюс этот документ.
Контекст: три VPS Serverum (`infra/max-k3s`), платформа на core, публикации в runtime, ячейки на
core и commerce. В целевой схеме commerce — это «SaaS Operator, биллинг, Commerce PostgreSQL»
(`docs/09-max-k3s-infra.md`); K3s на commerce до сих пор был пуст.

## 1. Аудит биллинга: что есть, чего не хватало, что сделано

Кодовая база биллинга: `apps/api/src/yleum_api/{routers/payments.py, routers/billing.py,
services/yookassa.py, services/subscription_lifecycle.py, services/payment_state.py,
services/billing_cycle.py, models/billing.py, models/account.py (Payment), workers/billing.py}`;
кабинет — `apps/web/src/components/account/*` и страницы `/billing`, `/billing/plan`,
`/billing/transactions`, `/account`.

| Область | Есть (до этой ветки) | Чего не хватало | Сделано / что делать |
|---|---|---|---|
| Тарифы и подписки | Версионные тарифы Free / Pro 1490 ₽ / Business 4990 ₽ (`DEFAULT_BILLING_PLANS`), личный billing-account на пользователя, машина состояний подписки (`pending_payment → active → past_due → expired`), уникальная живая подписка на аккаунт | — | Ничего не менялось. Цены пакетов пополнения (`PACKAGES` в роутере) живут в коде, тарифы — в БД; при смене цен править оба места |
| Покупка тарифа | `POST /api/payments/subscription`: идемпотентность по ключу клиента, подписка становится `active` в той же транзакции, что подтверждение оплаты; согласие на автопродление привязано к версии документов; `save_payment_method` для автоплатежей | — | Без изменений |
| Пополнение баланса | `POST /api/payments` пакетами start/business/pro, зачисление в ledger ровно один раз (`external_ref`) | — | Без изменений |
| Вебхук ЮKassa | `POST /api/payments/yookassa/webhook`: платёж перечитывается из API (единственная проверка подлинности, которую даёт ЮKassa — подписи у уведомлений нет), чужие/неизвестные события подтверждаются 204, повтор безопасен | Фильтр по сетям ЮKassa; событие `refund.succeeded` (объект — возврат, id платежа в `payment_id`) игнорировалось как «неизвестный платёж» | **Сделано:** `YOOKASSA_WEBHOOK_ALLOWED_CIDRS` (официальный список в `services/yookassa.YOOKASSA_NOTIFICATION_NETWORKS`; пусто = без фильтра), 403 для чужого источника; `refund.*` сверяет платёж по `payment_id`. Прод должен выставить список в `.env` |
| Идемпотентность | `Idempotence-Key` в каждом запросе к ЮKassa; уникальные `idempotency_key`, `provider_payment_id`; частичные уникальные индексы «одна открытая первая оплата на аккаунт» и «одно открытое продление на подписку»; повтор вебхука не удваивает зачисление | — | Без изменений |
| Чеки 54-ФЗ | В платеже и рекуррентном платеже — `receipt` (email плательщика, одна позиция «услуга», `payment_mode=full_payment`, `vat_code` из настройки) | Чек возврата: `POST /refunds` уходил без `receipt`, а при чеках через ЮKassa возврат обязан нести свой чек | **Сделано:** `create_refund(customer_email, description)` формирует чек возврата на email плательщика; роутер возврата подставляет email владельца платежа. Уточнить у владельца систему налогообложения: `YOOKASSA_VAT_CODE=1` = «без НДС» (УСН), 4 = НДС 20 %; `tax_system_code` нужен, только если в магазине несколько СНО |
| Возвраты | `POST /api/payments/{id}/refund` (только админы из `ADMIN_EMAILS`), полный возврат, запрещён если зачисленный кредит уже потрачен, запись в ledger | Частичные возвраты; чек возврата | Чек — сделано; частичные возвраты не нужны для MVP (вернуть можно только неиспользованный пакет/тариф целиком) |
| Продление | Фоновый цикл: списание с сохранённого способа, повтор каждые `BILLING_RENEWAL_RETRY_HOURS`=12 ч, льготный период `BILLING_GRACE_DAYS`=3 дня, затем перевод на Free и снятие keep-alive у проектов сверх лимита, письма пользователю | Цикл жил только потоком внутри RQ-воркера на core и был единственным источником heartbeat воркера | **Сделано:** цикл вынесен в `services/billing_cycle.py`, может работать как поток RQ-воркера (`BILLING_LIFECYCLE_ENABLED`, по умолчанию true) или как отдельный процесс `python -m yleum_api.workers.billing` с `GET /health` (для K3s commerce). Heartbeat RQ-воркера теперь отдельный поток, биллинг бьётся под своим ключом |
| Потерянный вебхук | Только ручной `POST /api/payments/{id}/reconcile` (пользователь/админ) | Автоматическая сверка: заказ, по которому вебхук не дошёл, оставался `pending` навсегда, а деньги — списанными | **Сделано:** каждый тик перечитывает из ЮKassa открытые заказы старше `BILLING_PAYMENT_RECONCILE_AFTER_MINUTES`=10 мин (пополнения и первые оплаты тарифа; продления — по своему графику), заказ без provider id закрывается как failed через `BILLING_PAYMENT_ABANDON_AFTER_HOURS`=24 ч |
| Отмена / восстановление | `PATCH /api/billing/subscription` cancel / restore (с повторным согласием), `can_restore` до конца оплаченного периода | Удаление сохранённой карты пользователем (сейчас карта «отвязывается» только отменой автопродления — списаний не будет, токен остаётся у ЮKassa); смена тарифа посреди периода без перерасчёта (покупка другого тарифа сразу закрывает текущий) | Не блокирует запуск. Следующим шагом: `DELETE /api/billing/payment-method` (status=revoked + auto_renew=false) и кнопка в кабинете; пропорциональный перерасчёт — по решению владельца |
| Кабинет | Тариф, автопродление, пакеты пополнения, история операций, устойчивый checkout (заказ сохраняется в браузере до ответа сервера, повтор не создаёт второй платёж), статусы `past_due`/`paused`, блокировка кнопок пока ЮKassa не настроена (`/api/payments/config.enabled`) | Кнопка «удалить карту» (см. выше) | Без изменений |
| Наблюдаемость | `/api/health` → `checks.worker` (heartbeat RQ-воркера) | Отдельного признака «биллинговый тик жив» не было; при переносе тика с core `checks.worker` покраснел бы | **Сделано:** `dependencies.billing_worker` (`ok`/`missing`) и `billing_worker_release_sha` в `/api/health`; у отдельного воркера — свой `/health` (200/503 по свежести тика и числу подряд провалов) |
| Юридический контур | Оферта/политики с реквизитами оператора, версия документов, согласие на автосписание, публичная страница с ИНН; checkout закрыт, пока нет `LEGAL_OPERATOR_NAME`+`LEGAL_OPERATOR_INN`+кредов ЮKassa | — | Владелец даёт реквизиты (см. §2) |
| Тесты | БД-тесты покупки, вебхука, продления, grace/downgrade, entitlement (CI с Postgres); характеризация запросов к ЮKassa | Тесты новых частей | **Сделано:** 48 unit-тестов без БД (цикл, проба, фильтр сетей, чек возврата, реестр хостов) + существующие БД-тесты не менялись по контракту |

Итог аудита: покупка, активация, продление, отмена, возврат и кабинет были готовы; до боевого
включения реально не хватало трёх вещей — защиты вебхука по источнику, чека возврата и
автоматической сверки при потерянном вебхуке. Все три закрыты кодом в `a138693b`.

## 2. Что должен дать владелец (настройки и действия в кабинетах)

| Что | Где взять / что сделать | Куда положить |
|---|---|---|
| `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY` (боевые) | ЛК ЮKassa → Интеграция → Ключи API. Боевой магазин должен пройти модерацию (договор, сайт с офертой/реквизитами — уже есть на yleum.ru) | `.env` платформы на core (`apps/llm-gateway/deploy/full/.env`), затем `docker compose up -d api worker` |
| Тестовый магазин | ЛК ЮKassa → «Тестовый магазин»: свой shopId и ключ вида `test_…`, тот же адрес API. Тестовые карты — в документации ЮKassa (успех / отказ / 3-DS) | Те же переменные на время проверки (§3, шаг 1) |
| HTTP-уведомления | ЛК → Интеграция → HTTP-уведомления: URL `https://yleum.ru/api/payments/yookassa/webhook`, события `payment.succeeded`, `payment.canceled`, `payment.waiting_for_capture`, `refund.succeeded`. ЮKassa повторяет уведомление до 24 ч, пока не получит 200/204 | — |
| Автоплатежи (сохранение способа оплаты) | Запросить у менеджера ЮKassa включение «Автоплатежей» для магазина — без этого `save_payment_method=true` игнорируется, и продление не сможет списать деньги (подписка просто уйдёт в `past_due` → Free) | — |
| Чеки 54-ФЗ | Подключить «Чеки от ЮKassa» (или свою онлайн-кассу), указать СНО | `YOOKASSA_VAT_CODE`: 1 — без НДС (УСН), 4 — НДС 20 % |
| `YOOKASSA_WEBHOOK_ALLOWED_CIDRS` | Официальный список сетей ЮKassa — уже в `.env.example` | `.env` платформы |
| Реквизиты оператора | `LEGAL_OPERATOR_NAME`, `LEGAL_OPERATOR_INN`, `LEGAL_OPERATOR_ADDRESS`, `LEGAL_SUPPORT_EMAIL` — ИП/ООО, от чьего имени принимаются деньги (без них checkout закрыт) | `.env` платформы |
| `ADMIN_EMAILS` | Кто вправе делать возвраты | `.env` платформы |
| `SMTP_*` | Почта для писем «не удалось продлить» / «переведено на Free» (сейчас — проверить, заполнено ли на core) | `.env` платформы |
| `WEB_BASE_URL=https://yleum.ru` | Адрес возврата после оплаты (сейчас в compose по умолчанию старый домен) | `.env` платформы |
| `ALLOW_STUB_TOPUP=false` | Бесплатное самопополнение должно быть выключено на публичном проде (по умолчанию выключено) | `.env` платформы |
| `SERVERUM_API_TOKEN` + подтверждение эндпоинтов | Запросить у поддержки Serverum (hd@serverum.ru): есть ли API у панели my.serverum.ru, как получить токен, какие пути у «тарифы / заказ / статус / удаление», в каком поле приходит приватный адрес 172.197.102.x | `apps/orchestrator/.env` на Mac/core (для `serverum_cli`), см. §5 |
| DNS для нового хоста ячеек | `*.dev3.yleum.ru → <публичный IP>` в reg.ru после шага 11 сценария заказа | — |

## 3. Порядок боевого включения ЮKassa (по шагам)

1. **Тестовый магазин на проде.** В `.env` — тестовые `YOOKASSA_SHOP_ID`/`SECRET_KEY`, реквизиты
   оператора, `YOOKASSA_WEBHOOK_ALLOWED_CIDRS`, `WEB_BASE_URL`; `docker compose up -d api worker`.
   Проверка: `GET /api/payments/config` → `enabled: true`; регистрация с подтверждённым email →
   `/billing` → пакет «start» → тестовая карта → возврат на `/account?payment=…` → статус
   `succeeded`, баланс +500 ₽, в `payments` строка с `provider_payload`. В логах api — вход
   вебхука (`POST /api/payments/yookassa/webhook 204`); отправить руками запрос с чужого IP —
   должен быть 403.
2. **Подписка и автопродление (тест).** `/billing/plan` → Pro с согласием на автопродление →
   тестовая карта → подписка `active`, `next_charge_at = current_period_end`, в
   `billing_payment_methods` — сохранённый способ. Затем в БД сдвинуть
   `subscriptions.current_period_end` и `next_charge_at` в прошлое → в течение минуты тик
   создаёт `subscription_renewal`, списывает с сохранённого способа (в тестовом магазине —
   успешно), период продлевается, кредит зачисляется один раз (`wallet_charges`).
3. **Отказ и льготный период (тест).** Тестовая карта «отказ» на продление → `past_due`,
   `grace_period_ends_at = период + 3 дня`, письмо; после сдвига grace в прошлое — `expired` +
   Free, keep-alive снят. **Возврат:** админом `POST /api/payments/{id}/refund` → в ЛК ЮKassa
   возврат с чеком возврата.
4. **Потерянный вебхук (тест).** Временно закрыть вебхук (например, выставить в
   `YOOKASSA_WEBHOOK_ALLOWED_CIDRS` заведомо чужую сеть), оплатить пакет → через 10 минут тик
   сам перечитывает платёж и зачисляет баланс (`payments.reconciled` в логах воркера); вернуть
   список.
5. **Боевой магазин.** Заменить креды на боевые, включить HTTP-уведомления и автоплатежи в
   боевом ЛК, `docker compose up -d api worker`, повторить шаг 1 на минимальную сумму реальной
   картой владельца, сделать возврат.
6. **Контроль.** `/api/health`: `checks.worker = ok`, `dependencies.billing_worker = ok`;
   `production-smoke` зелёный; в Grafana пока нет метрик биллинга — первую неделю смотреть
   `docker logs omnia-prod-worker | grep billing` и таблицу `payments` раз в день.
7. **Отчёт:** гипотеза H211 в `otchet/data.json` → `worked`/`partial` с оценкой после живых шагов 1–5.

Откат на любом шаге: убрать `YOOKASSA_*` из `.env` и пересоздать `api`/`worker` — checkout
закрывается (`enabled: false`), уже созданные подписки продолжают жить по своим датам.

## 4. Commerce-кластер: где запускать биллинговый тик

**Решение (мой выбор, менять не обязательно):** первую неделю после включения ЮKassa тик
остаётся там, где он живёт сейчас — потоком RQ-воркера на core (`BILLING_LIFECYCLE_ENABLED=true`).
Причина: это уже работающий путь без новых сетевых зависимостей, а первые живые продления
удобнее наблюдать в одном месте. Перенос в commerce — второй шаг тем же кодом; манифесты и
сценарий готовы и проверены dry-run.

Почему всё-таки commerce, а не «навсегда в compose»: тик — единственная фоновая работа, которой
опасны задержки (продление ровно в срок, льготный период), а на core она делит процесс с
генерациями и очередью превью; в commerce у неё свой pod, свои пробы и свои лимиты. Это первая
настоящая нагрузка commerce-кластера по целевой схеме, и она не требует ни томов, ни ingress.

Что сделано (`infra/max-k3s/k8s/commerce/*.yaml`, `infra/max-k3s/commerce/10-billing-workloads.sh`):

- **Воркер:** Deployment `billing/billing-worker` — тот же образ api (`omnia-api:prod`),
  опубликованный в реестр как `registry.yleum.ru/platform/omnia-api:<sha>` и взятый по digest,
  команда `python -m yleum_api.workers.billing`, 1 реплика / Recreate, без root, read-only fs,
  `/health` на :8090 как readiness/liveness (503 — если тик не завершался 3×`poll` или ≥3 провала
  подряд), Service для проверки из кластера, NetworkPolicy (внутрь — только проба; наружу — DNS,
  платформа по WireGuard: postgres 5432 / redis 6379 / оркестраторы 8003, интернет только
  443/465/587/25 для ЮKassa и SMTP).
- **База платформы по WireGuard:** с 23.09 база платформы живёт на хостовом PostgreSQL core, который
  слушает `10.10.0.1:5432` — воркер ходит в него напрямую (socat-форвард к контейнеру, который был в
  первой версии сценария, убран); в pg_hba строки роли `max_billing` только для `10.10.0.3` и pod-сети
  commerce, ufw — то же; роль `max_billing` с правами
  SELECT/INSERT/UPDATE ровно на `billing_*`, `subscriptions`, `payments`, `wallets`,
  `wallet_charges`, `projects` (UPDATE — снятие keep-alive) и SELECT на `users` (email).
- **Секреты:** `/etc/max-studio/billing-worker.env` (root, 0600) собирается на core из `.env`
  платформы (ЮKassa, реквизиты, SMTP, `JWT_SECRET`, токен оркестратора) плюс DSN роли и адреса по
  WireGuard; Secret `billing-worker-env` создаётся из него по ssh-потоку
  (`kubectl create secret … --from-env-file=/dev/stdin`) — файл на Mac не появляется.
  `ORCHESTRATOR_HOSTS` переписан на адреса WireGuard (core `10.10.0.1:8003`, commerce
  `10.10.0.3:8003`).
- **Проверка:** `verify` — rollout, `curl /health` внутри пода, `dependencies.billing_worker` в
  `/api/health` платформы. **Передача:** `handover` выключает поток на core
  (`BILLING_LIFECYCLE_ENABLED=false`, пересоздание `worker`); **откат:** `rollback` (0 реплик +
  поток обратно). Двойной запуск не опасен (row-locks `SKIP LOCKED`, уникальное открытое
  продление), просто шумит в логах.

Как запускать (с Mac, после `./provision.sh status` и `./kube-tunnel.sh up`):

```bash
cd infra/max-k3s
./commerce/10-billing-workloads.sh plan               # ничего не меняет
./commerce/10-billing-workloads.sh all --dry-run       # команды без выполнения
./commerce/10-billing-workloads.sh all                 # core-access → host → image → secret → apply → verify
./commerce/10-billing-workloads.sh handover            # тик только в commerce (после наблюдения)
./commerce/10-billing-workloads.sh rollback            # если dependencies.billing_worker=missing > 3 мин
```

После смены кредов ЮKassa на core: `core-access` (пересобирает env-файл) → `secret` (сам
перезапускает pod). После деплоя нового релиза api: `image` → `apply`.

## 5. Автозаказ VPS у Serverum

**Что выяснено (23.09):** у Serverum нет публичной документации API — ни на serverum.ru, ни в
блоге, ни в панели. Панель `https://my.serverum.ru` — собственное SPA-приложение (не ISPsystem
BILLmanager: `/billmgr` отдаёт оболочку SPA), международная витрина serverum.com ведёт в
`secure.serverum.com/clientarea`. Прайс-лист публичный (Москва, NVMe, помесячно/почасово):
4 vCPU / 8 GB / 80 GB — 1 134 ₽, 8 vCPU / 12 GB / 100 GB — 1 768 ₽, 8 vCPU / 16 GB / 160 GB —
2 189 ₽ в месяц.

**Что сделано:** `apps/orchestrator/src/yleum_orchestrator/services/serverum.py` — клиент по
стандарту REST-панелей хостеров (bearer-токен; `GET /plans`, `POST /servers`,
`GET/DELETE /servers/{id}`), терпимый к именам полей, с ожиданием готовности сервера и картой
эндпоинтов, переопределяемой через `SERVERUM_ENDPOINTS_JSON` без правки кода; режим dry-run без
сети; CLI `python -m yleum_orchestrator.serverum_cli` (plans / servers / order / status / wait-ip /
delete, `--json`, коды выхода). 14 контрактных тестов на подменённом httpx.
**Пометка «эндпоинты уточнить»** стоит в docstring модуля и в этом документе: при получении токена
от поддержки нужно свериться по трём вещам — базовый URL и пути, имена полей заказа
(`plan_id`, `hostname`, `os`, `location`, `ssh_keys`) и в каком поле приходит приватный адрес
(172.197.102.x) — всё это меняется настройками/парсерами, сценарий не меняется.

**Сценарий «нужен ещё хост ячеек»** — `infra/max-k3s/cells/60-order-cell-host.sh` (11 шагов, каждый
идемпотентен, `--from-step N` продолжает, `--dry-run` печатает всё без сети и ssh):
заказ и ожидание IP → строка в `inventory.env` (WG `10.10.0.N`, pod/svc CIDR, порт туннеля) и
ssh-алиас → bootstrap (ключ в `/etc/ssh/authorized_keys.d`, ufw, fail2ban) → WireGuard на всех
хостах → Postgres → подготовка хоста ячеек без K3s (`10-cell-host-prep.sh` научился пропускать
k3s-часть и принимать публичный IP явно) → rsync кода с core по WireGuard + `.env.core` +
kubeconfig runtime → оркестратор (`20-cell-host-orchestrator.sh`) → ufw redis на core → ночная
копия → запись в `ORCHESTRATOR_HOSTS` платформы с `enabled=false` и пересоздание api/worker.
После DNS `*.dev3.yleum.ru` (владелец) — `--enable`. Dry-run для `cells3` даёт адреса
`10.10.0.4 / 10.48.0.0/16 / 16446`.

Допущения к живому прогону: панель создаёт того же пользователя `zeuszcz` с sudo, что и на
первых трёх VPS; приватная сеть 172.197.102.0/24 выдаётся новому VPS автоматически (иначе
`--lan-ip`); bootstrap нужно запускать сразу после появления IP — панель обнуляет
`~/.ssh/authorized_keys` каждые 5 минут (bootstrap переносит ключ в системный файл).

## 6. Риски

- **Автоплатежи не включены в ЛК ЮKassa** → продления не проходят, все платные уходят в Free
  через 3 дня. Лечится включением у менеджера до первого продления; тест — шаг 2 §3.
- **Чеки:** если у магазина чеки через свою кассу, а не ЮKassa, объект `receipt` в платеже
  избыточен, но не вреден; если СНО «с НДС», `YOOKASSA_VAT_CODE` надо сменить до первого чека.
- **Фильтр сетей вебхука** при смене адресов ЮKassa начнёт отдавать 403 — симптом: платежи
  зачисляются с задержкой 10 мин через сверку, в логах api 403 на вебхук; обновить список.
- **Commerce:** доступ к базе платформы по WireGuard — новая дорога; ограничена ролью и ufw.
  Ячейки на commerce ходят через guard с запретом `10.10.0.0/24`, но это ещё одна причина держать
  `CELL_MACHINE_DENIED_CIDRS` актуальным.
- **Serverum API** может не существовать вовсе (только панель) — тогда шаг 1 сценария делается
  руками в панели, а шаги 2–11 запускаются с `--from-step 2 --public-ip … --lan-ip …` (адреса
  берутся из аргументов).
- Не проверено вживую в этой ветке (нет прод-доступа у агента): реальный вебхук ЮKassa, живой
  прогон обоих сценариев на серверах. Всё, что можно проверить без прода, проверено (§7).

## 7. Что проверено

- `apps/api`: `uv run --frozen ruff check .` — чисто; `uv run --frozen mypy src` — чисто
  (244 файла); 48 unit-тестов без БД зелёные (`test_billing_worker.py`,
  `test_yookassa_notification_source.py`, `test_yookassa_requests.py`, `test_orchestrator_hosts.py`).
  БД-тесты покупки/вебхука/продления (`test_payments.py`, `test_subscription_lifecycle.py`) по
  контракту не менялись — их прогонит CI с Postgres.
- `apps/orchestrator`: ruff чисто; mypy — единственная ошибка `errno.EDEADLOCK` в
  `cell_lock.py` (файл не трогался, атрибута нет только на macOS); 14 тестов `test_serverum.py`.
- Скрипты: `bash -n` для всех; `10-billing-workloads.sh plan` и `all --dry-run`,
  `60-order-cell-host.sh --dry-run --name cells3` и `--enable --dry-run` проходят от начала до
  конца локально.
