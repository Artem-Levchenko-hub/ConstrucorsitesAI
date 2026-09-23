# Фаза 3, этап A — опубликованные MAX-приложения в кластере runtime (Kubernetes)

Статус: в работе с 23.09.2026. Владелец: «делай» (после вопроса «почему архитектура ещё не готова»).
Контекст: инфраструктура трёх VPS (K3s core/runtime/commerce, WireGuard, реестр `registry.yleum.ru`,
cert-manager, мониторинг) развёрнута 22.09 (`infra/max-k3s`), платформа переехала на core той же
Docker-связкой (`infra/max-k3s/migrate`). Боль: ёмкость одного хоста — одна генерация + одно
опубликованное приложение одновременно (диспетчер `cell_admission.py`).

## Цель этапа

Опубликованное приложение (результат `POST /api/projects/{id}/deploy`) работает **в кластере
runtime** (`max-runtime`, 2.153.248.99), а не в Docker на core. Публичный адрес
`https://<slug>.apps.yleum.ru` обслуживает Traefik кластера с сертификатом Let's Encrypt. Core
перестаёт тратить ~1,8 ядра на каждое опубликованное приложение; приложения масштабируются
добавлением нод в runtime (позже — Яндекс как второй кластер).

Dev-превью и генерация (ячейки агента) остаются в Docker на core — это этап B.

## Что НЕ меняется (контракты сохраняются)

`CellPublicationService.submit → _prepare` остаётся как есть: запечатанный релиз = образ
машины (`release["image_id"]`, локальный docker-образ без Env/Entrypoint/Cmd, код в `/workspace`),
тёплые тома (артефакты `tar`), `manifest` (services/routes/data_stores), `schema_digest`,
`prod_url`, доказательства, журнал `state/cell-publications`, бизнес-конфиг (`configure`),
identity/fencing, восстановление после рестарта (`reconcile`), политика сна.

## Что меняется — «размещение» (placement) за флагом

`settings.publication_backend: "docker" | "kubernetes"` (env `PUBLICATION_BACKEND`, дефолт
`docker`). При `kubernetes` шаги `_start`, `_activate`, `disable`, `_refresh_public_configuration`,
`reconcile` идут через `services/k8s_publication.py` (`KubernetesPublishedRuntime`):

| Docker сегодня (core) | Kubernetes (runtime) |
|---|---|
| образ релиза локально | `docker tag` + `docker push registry.yleum.ru/apps/<project_id>:<epoch>` (креды в `registries.yaml` кластеров уже есть) |
| контейнер-машина, сервисы через `docker exec` + `_SERVICE_WRAPPER` | Deployment `app`: тот же образ, command = супервизор (python) из ConfigMap: запускает `manifest.services` в порядке `service_order`, restart-политика, readiness по HTTP-порту маршрута; `/workspace` — из образа |
| project-postgres (тёплый том из артефакта) | StatefulSet `project-postgres` + PVC; initContainer забирает артефакт по одноразовой capability-ссылке с оркестратора (`GET /internal/publication-artifacts/{cap}`, WireGuard 10.10.0.1:8003, TTL минуты) и распаковывает в PVC; последующие публикации том НЕ пересеивают |
| managed postgres + redis production-workspace (для max-core) | StatefulSet `core-postgres` + Deployment `redis` в namespace приложения |
| `max-core` (образ `omnia-max-public-core:<sha>`) | Deployment `core`: образ из реестра `registry.yleum.ru/platform/max-public-core:<sha>`, env как в `_start_boundary` (AUTH_SECRET=public auth secret, OMNIA_PROJECT_ID, DATABASE_URL→core-postgres, REDIS_URL, runtime_env, NODE_ENV=production…) |
| gateway = guard-образ + `server.py` границы в tmpfs | Deployment `boundary`: guard-образ из реестра, `config.json` — Secret, `server.py` (`boundary_source()`) — ConfigMap, оба в memory-backed emptyDir `/run/omnia-boundary`; `core_host`/`machine_host` = DNS Service'ов; слушает :3000 |
| nginx vhost + acme.sh на core | Ingress `<slug>.apps.yleum.ru` → Service `boundary`:3000, аннотация cert-manager `letsencrypt-prod` |
| сеть: internal/egress bridge, guard | NetworkPolicy: default-deny; app → project-postgres/redis + DNS; boundary → app/core; core → core-postgres/redis + egress к MAX API (443) |
| `schema_digest` — `pg_dumpall --schema-only` в контейнере | exec в pod `project-postgres` (та же команда, тот же фильтр строк) |
| `disable` — снять vhost, `retire_compute`, тома retained | удалить Deployment/Service/Ingress/Secret; PVC остаются (retained), namespace помечается |
| `reconcile` при старте | сверка Deployment'ов с журналом, дожидание readiness |

Namespace на приложение: `app-<project_id>`; все объекты с метками `omnia.project_id`,
`omnia.release_id`, `omnia.epoch`. Доступ к кластеру: kubeconfig `/etc/max-studio/runtime-kubeconfig.yaml`
на core (сервер `https://10.10.0.2:6443`, SAN есть), клиент — `kubernetes` (python), без helm.
`infra/max-app-chart` остаётся справочной формой (CI), после стабилизации — привести к коду или убрать.

## DNS и суффиксы

- `*.apps.yleum.ru` → **runtime** (2.153.248.99) — публичные приложения (вернуть запись, как в
  исходном плане).
- dev-превью ячеек на core получают отдельный суффикс `*.dev.yleum.ru` → 2.153.248.98
  (`RUNTIME_HOST_SUFFIX=dev.yleum.ru`), публичный — новая настройка `PUBLIC_HOST_SUFFIX=apps.yleum.ru`
  (`nginx_writer.prod_host` берёт её). Обе записи заводит владелец.

## Порядок работ (каждый шаг — коммит, тесты, деплой)

1. **I1 — фундамент**: настройки (`publication_backend`, kubeconfig, реестр, суффикс),
   `k8s_publication.py` (клиент, apply/wait/exec/delete, генерация манифестов из релиза),
   endpoint артефактов с capability, push образов. Юнит-тесты с фейковым K8s-клиентом (без кластера).
2. **I2 — включение за флагом** в `CellPublicationService` (`_start/_activate/disable/refresh/reconcile`),
   тесты сервиса с фейковым runtime. **Сделано** (`7f60a87f`, `2286b3c5`): где живёт выпуск, решает
   его запись в журнале (`placement`), а не глобальный флаг — docker-публикации обслуживаются
   по-старому, новые идут в кластер; при первом переезде прежний docker-бандл гасится best-effort
   (тома сохраняются). Уточнения к I1 по итогам: тома кода — PVC на релиз (emptyDir терял код при
   перезапуске пода), ссылки на артефакты — в Secret `seed-links` (смена конфига не перезапускает
   приложение), маркер `.omnia-seeded` (оборванная распаковка переделывается), egress к
   оркестратору из init-контейнеров, `destroy` без права удалять namespace (роль SA).
3. **I3 — живая проверка** на runtime: публикация канарейки через Kubernetes, DNS-переключение
   `*.apps` → runtime, `PRODUCTION_MAX_CANARY_URL`, production-smoke зелёный; затем флаг
   `PUBLICATION_BACKEND=kubernetes` глобально. Доступ и окружение — `k8s/apply.sh orchestrator-access`
   + `publication-env` (сделано 23.09). Первый живой прогон идёт под суффиксом
   `2-153-248-99.sslip.io` (резолвится в runtime без правки DNS у владельца); после переключения
   `*.apps` → .99 приложение переезжает на `apps.yleum.ru` само: сменившийся публичный суффикс
   делает выпуск «не текущим», и следующая публикация переводит ingress на новое имя.
   **Сделано 23.09:** канарейка переехала в кластер (публикация `012c82e7`, временный суффикс),
   владелец перевёл `*.apps` → .99 и завёл `*.dev` → .98, суффиксы переключены
   (`RUNTIME_HOST_SUFFIX=dev.yleum.ru`, `PUBLIC_HOST_SUFFIX=apps.yleum.ru`, в API —
   `PROJECT_CELL_PREVIEW_HOST_SUFFIX` / `GATE_PREVIEW_RESOLVER_RULES`), канарейка переопубликована
   на `apps.yleum.ru` (см. отчёт H207). Кластер — цель по умолчанию для всех новых публикаций.

Что показала ёмкость core при живом прогоне: квота одной ячейки 3.2 ядра (машина 1 + бандл 2 +
помощники 0.2), хостовый резерв 1, опубликованная канарейка в Docker 1.8 → пока публикация живёт на
core, одновременно помещается **одна** ячейка; после переезда публикаций в runtime — две; для трёх
нужен бандл ≤ 1 ядра, а это смена профиля ресурсов (пересоздание бандлов ячеек: проверка identity
не пускает изменённые квоты к существующим контейнерам — `resource identity mismatch:cpu_quota`).
4. **Отложено (после A)**: сон/пробуждение опубликованных приложений в K8s (scale 0/1),
   egress-прокси приложений, перенос данных уже опубликованных Docker-приложений (сейчас их
   одно — канарейка).

## Риски и как проверяем

- Супервизор сервисов ≠ `docker exec`: readiness/логи/restart — покрыть тестами на манифесте
  канарейки и «Мои задачи» (два сервиса? см. `manifest.service_order`).
- Сидирование тома Postgres через capability-ссылку: проверка `schema_digest` после старта — та
  же гарантия, что и сегодня («publication startup changed database schema»).
- Граница (`machine_boundary.py`) в K8s: те же `_PUBLIC_ANONYMOUS`, CSRF-origin, cookies —
  проверяется смоком (`/api/omnia/health` 200 без сессии, `/` 401/200 по протоколу).

## Этап B — ячейки агента на втором хосте (решение 23.09, вечер)

Исходный план предполагал перенос ячеек в Kubernetes. От этого отказались на этап B: модель ячейки
построена на снимках Docker (checkpoint = `docker commit`/`export` + тома), интерактивном `exec`
и хостовом nginx с сертификатом на каждое превью — в Kubernetes это не переносится, а переписывается
(недели). Вместо этого **второй хост ячеек с тем же Docker-стеком** и **размещение ячеек на уровне
API**: даёт параллельные генерации сразу, а логика «ячейка живёт на одном из хостов» понадобится и
для любого будущего рантайма.

- **Хост:** commerce (2.153.248.100). runtime занят под кластер приложений (80/443 у Traefik), на
  commerce же K3s пока пуст. Подготовка — `infra/max-k3s/cells/10-cell-host-prep.sh`
  (servicelb off, публичный IP на lo, Docker, nginx-catch-all, acme.sh, uv, ufw) и
  `20-cell-host-orchestrator.sh` (`.env` на основе core: суффикс превью `dev2.yleum.ru`,
  `ARTIFACT_BASE_URL=http://10.10.0.3:8003`, Redis платформы по WireGuard `10.10.0.1:6379`
  — на core его отдаёт `redis-mesh-forward.service` (socat), образы из реестра по digest).
- **API (`e2128074`):** реестр `ORCHESTRATOR_HOSTS`, колонка `project_cell_workspaces.orchestrator`
  (миграция 0068), маршрутизация вызовов по идентичности в пути, размещение новой ячейки на
  наименее загруженный хост, очередь ёмкости и «жертвы» сна — по хостам, превью с любым
  известным суффиксом, `/api/health` по всем оркестраторам.
- **Включение:** сначала `ORCHESTRATOR_HOSTS` с `commerce.enabled=false` (ничего не меняется,
  привязки работают), затем DNS `*.dev2.yleum.ru → 2.153.248.100` (владелец) и
  `enabled=true` — новые проекты уходят на commerce.
- **Ёмкость после включения:** core 2 ячейки + commerce 2 = 4 одновременные генерации; квота
  бандла 2 → 1 ядро (новая версия профиля) даст по 3 на хост.
- **Хвосты:** ночная копия состояния ячеек с commerce (как на core), Redis платформы для второго
  хоста — через compose `ports` на WG-адрес вместо socat, панель наблюдения за обоими
  оркестраторами в smoke.
