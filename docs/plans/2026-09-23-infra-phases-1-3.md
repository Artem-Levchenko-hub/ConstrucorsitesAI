# Фазы 1–3 целевой инфраструктуры: состояние на вечер 23.09.2026

Команда владельца: «закрыть все фазы как можно быстрее». Ниже — что уже живёт на проде, что
слито в `main` и выкачено за флагами, и что упирается в доступы, которые может дать только владелец.

## Фаза 1 — изоляция

| Пункт | Состояние | Где |
|---|---|---|
| runtime с gVisor | **Живёт.** RuntimeClass `gvisor` на узле runtime, поды app/boundary/core канарейки в песочнице (ядро 4.19.0-gvisor), оркестраторы с `K8S_APP_RUNTIME_CLASS=gvisor` | `infra/max-k3s/remote/70-gvisor.sh`, `k8s_publication.py` (H211) |
| отдельная БД на приложение | **Живёт с этапа A:** в каждом namespace приложения свои `core-postgres` и `project-postgres` | `k8s_publication.py` |
| изолированные build-воркеры (rootless BuildKit) | **Живёт с 23.09:** `omnia-buildkitd` на commerce и core (sandbox=1, caps minimal, AppArmor-профиль), оркестраторы на `BUILD_BACKEND=buildkit`, проверочная сборка через модуль оркестратора прошла на обоих хостах (H222) | `infra/max-k3s/cells/50-buildkit-rootless.sh`, `services/buildkit.py` |
| edge: ingress + wildcard-сертификат | **Код в main** (выпуск через DNS-01, раздача по WireGuard на runtime/commerce/core, режим `K8S_TLS_MODE=wildcard`). Владелец: API reg.ru **для начала не нужен** — тогда путь без API: делегирование `_acme-challenge` на наш acme-dns на core (владелец один раз добавляет NS/A/CNAME-записи, дальше выпуск и продление автоматические) либо оставить нынешние сертификаты «по имени» (HTTP-01, работают) | `infra/max-k3s/edge/*` (H217) |

## Фаза 2 — ядро

| Пункт | Состояние | Где |
|---|---|---|
| журнал расхода и Entitlements | **Живёт:** миграция 0070, `GET /api/billing/usage`, серверные лимиты тарифа (проекты, слоты публикации, интеграции), Free v2 без лимитов по решению владельца | `services/entitlements.py`, `services/billing_usage.py` (H216) |
| разделение Core PostgreSQL | **Живёт с 23.09 18:17:** база платформы на хостовом PostgreSQL core (окно ≈ 1 мин, сверка 47 таблиц, smoke и restore-test зелёные); контейнер остановлен, `retire` через 3–7 дней | `infra/max-k3s/migrate/50-platform-db-to-host.sh`, `migrate/README.md` |
| вход через VK ID и Яндекс ID | **Живёт как код:** миграция 0071, эндпоинты `/api/auth/oauth/*`, кнопки и экран согласий. **Нужны приложения VK ID и Яндекс OAuth** (client id/secret) | `routers/auth_oauth.py`, `docs/plans/2026-09-23-oauth-login.md` (H218) |

## Фаза 3 — коммерция

| Пункт | Состояние | Где |
|---|---|---|
| подписки, биллинг, ЮKassa | **Живёт частично:** проверка источника вебхука, чек возврата, сверка зависших платежей, отдельный биллинговый воркер (`billing_worker: ok` в `/api/health`). **Нужны боевые и тестовые ключи ЮKassa** и настройки в кабинете | `services/yookassa.py`, `workers/billing.py`, `docs/plans/2026-09-23-phase3-commerce.md` (H219) |
| Commerce-кластер | Манифесты и сценарий переноса воркера в K3s commerce готовы (`--dry-run` проходит); включение после кредов ЮKassa | `infra/max-k3s/commerce/*`, `k8s/commerce/*` |
| автозаказ VPS | Клиент API Serverum + сценарий «ещё один хост ячеек» с `--dry-run`. **Нужен токен API Serverum и подтверждение эндпоинтов** | `services/serverum.py`, `cells/60-order-cell-host.sh` |

## Что нужно от владельца (без этого фазы не закрыть до конца)

1. **DNS для wildcard без API reg.ru (если нужен edge сейчас):** один раз добавить записи `ns-acme.yleum.ru A 2.153.248.98`, `acme.yleum.ru NS ns-acme.yleum.ru` и CNAME `_acme-challenge` для `yleum.ru`, `apps.yleum.ru`, `dev.yleum.ru`, `dev2.yleum.ru` → на `<id>.acme.yleum.ru` (id выдаст acme-dns на core). Альтернатива — API reg.ru (`edge.sh all`). Без того и другого wildcard не выпустить: остаются сертификаты по имени, как сейчас.
2. **VK ID:** приложение Web для домена yleum.ru, redirect `https://yleum.ru/api/auth/oauth/vk/callback`, право `email` → `VK_ID_CLIENT_ID` (+ `VK_ID_CLIENT_SECRET`).
3. **Яндекс OAuth:** веб-сервис, redirect `https://yleum.ru/api/auth/oauth/yandex/callback`, право «адрес электронной почты» → `YANDEX_ID_CLIENT_ID`/`YANDEX_ID_CLIENT_SECRET`.
4. **ЮKassa:** боевой и тестовый магазин (`YOOKASSA_SHOP_ID`/`YOOKASSA_SECRET_KEY`), HTTP-уведомления на `https://yleum.ru/api/payments/yookassa/webhook`, включённые автоплатежи и чеки, СНО.
5. **Serverum:** токен API и подтверждение у поддержки, что API заказа VPS существует (публичной документации нет).
6. **Решение:** поднимать ли версию юридических документов после смены текстов.
