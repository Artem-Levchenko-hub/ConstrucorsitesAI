# 09 — Инфраструктура MAX Studio на трёх VPS Serverum (Фаза 1, 22.09.2026)

Точка синхронизации для всех агентов: где живёт новая площадка и как к ней ходить.
Подробности, проверки и список DNS-записей — в [`infra/max-k3s/README.md`](../infra/max-k3s/README.md).

## Коротко

| Кластер | Роль по целевой схеме | Публичный IP | WireGuard | SSH |
|---|---|---|---|---|
| **core** | API, Auth, Builder, Orchestrator, LLM Gateway, Core PostgreSQL, мониторинг | 2.153.248.98 | 10.10.0.1 | `ssh max-core` |
| **runtime** | MAX-приложения клиентов, реестр образов `registry.yleum.ru` | 2.153.248.99 | 10.10.0.2 | `ssh max-runtime` |
| **commerce** | SaaS Operator, биллинг, Commerce PostgreSQL | 2.153.248.100 | 10.10.0.3 | `ssh max-commerce` |

- Домен: **yleum.ru** (reg.ru, DNS заведён 22.09). Корень и `www` → core (пока заглушка, namespace `landing`). Панель/мониторинг: `grafana.yleum.ru` (core). Реестр: `registry.yleum.ru` (runtime). Приложения клиентов: `*.apps.yleum.ru` (runtime). Сертификаты — Let's Encrypt через cert-manager, выпущены.
- Каждый сервер — отдельный одно-нодовый **K3s v1.36.4** (Traefik, cert-manager, local-path). Между собой — только по **WireGuard**.
- **PostgreSQL 16** на хосте каждого сервера (`/etc/max-studio/postgres.env`), ночные бэкапы + копия на соседа.
- kubectl с Mac: `infra/max-k3s/kube-tunnel.sh up` → `KUBECONFIG=~/.kube/max-studio.yaml`, контексты `max-core` / `max-runtime` / `max-commerce`.
- Весь провижининг — код: `infra/max-k3s/provision.sh <фаза|all>`, идемпотентно.

## Где платформа (Фаза 2a, 22.09.2026)

Платформа (api / worker / generation-worker / web / gateway / orchestrator) **работает на core** под
https://yleum.ru — той же связкой, что раньше на 170.168.72.200: compose-стек `omnia-prod-*` +
оркестратор как хост-сервис с Docker + nginx на хосте (80/443). Приложения клиентов (ячейки) тоже на
core; их превью и публикации — `*.apps.yleum.ru` (сертификат на каждое имя через acme.sh). K3s на
core держит только мониторинг. Как деплоить — правило доставки в `CLAUDE.md`; как это переезжало и
почему не в K3s — [`infra/max-k3s/migrate/README.md`](../infra/max-k3s/migrate/README.md).
Старый сервер 170.168.72.200 — запасной, чужие проекты на нём живут дальше.

**Фаза 2б (подготовлено 23.09, ждёт выката):** база платформы `omnia` переезжает из контейнера
`omnia-prod-postgres` на хостовый PostgreSQL core (`10.10.0.1:5432`, та же база/роль `omnia`,
пароль в `/etc/max-studio/platform-postgres.env`). Compose переключается вторым файлом
`docker-compose.hostdb.yml` через `.env` (`COMPOSE_FILE`, `PLATFORM_DATABASE_URL`); бэкапы,
restore-test, проверка релиза и ротация секретов понимают host-режим. Скрипт и порядок выката с
окном недоступности — [`infra/max-k3s/migrate/README.md`](../infra/max-k3s/migrate/README.md),
раздел «Фаза 2б». Журнал расхода и лимиты тарифа на сервере — `GET /api/billing/usage`
(`docs/01-api-contract.md`).

**Фаза 3, этап A (в работе с 23.09):** опубликованные приложения размещаются в кластере runtime
(`PUBLICATION_BACKEND=kubernetes`, код — `apps/orchestrator/.../k8s_publication.py` +
`k8s_placement.py`, план — `docs/plans/2026-09-23-k8s-publication-stage-a.md`). **Этап B (23.09):**
ячейки агента (генерация, dev-превью) размещаются на двух хостах с одинаковым Docker-стеком — core и
commerce (`infra/max-k3s/cells/*`), какая ячейка где — решает API (`ORCHESTRATOR_HOSTS`).
**Commerce-кластер (23.09, код готов, не включён):** биллинговый тик (продления, льготный период,
сверка заказов ЮKassa) умеет работать отдельным воркером в K3s commerce —
`infra/max-k3s/commerce/10-billing-workloads.sh` + `k8s/commerce/*.yaml`; база платформы для него
отдаётся с core по WireGuard (`10.10.0.1:15432`, роль `max_billing`). Пока тик живёт в RQ-воркере на
core; аудит биллинга, что нужно от владельца (креды ЮKassa, реквизиты) и порядок включения —
`docs/plans/2026-09-23-phase3-commerce.md`. Дополнительный хост ячеек заказывается у Serverum и
вводится в строй одним сценарием `infra/max-k3s/cells/60-order-cell-host.sh`. **Ещё не сделано:**
api/web/gateway в кластере core (этап C); K3s на commerce уживается с ячейками (порты 80/443 у
хостового nginx превью).

## Edge: один wildcard-сертификат на все три хоста (Фаза 1, код готов 23.09.2026)

Сегодня каждый хост выпускает сертификаты сам и по одному на имя (HTTP-01: nginx+acme.sh на core и
commerce для превью, cert-manager в runtime для опубликованных приложений). Это медленно (первая
публикация ждёт выпуска), ломается, когда HTTP-01 не проходит (default-deny, DNS-кэш провайдера — оба
случая уже ловили вживую), и никогда не покрывает новое имя заранее. Слой edge заменяет это одним
wildcard-сертификатом Let's Encrypt с SAN `yleum.ru`, `*.yleum.ru`, `*.apps.yleum.ru`, `*.dev.yleum.ru`,
`*.dev2.yleum.ru`, который выпускается на core через DNS-01 у reg.ru (acme.sh, плагин `dns_regru`),
хранится в `/etc/max-studio/edge/` (root, 0600), продлевается таймером и после каждого продления сам
разъезжается по WireGuard на runtime (Secret `kube-system/wildcard-yleum` + Traefik `TLSStore default`
— сертификат по умолчанию для любого Ingress без своего секрета) и на commerce/core (раскладка для
оркестратора `OMNIA_WILDCARD_CERT_ROOT` — превью получают https-блок сразу, без acme; платформенные
vhost'ы `yleum.ru`/`www`/`grafana` тоже на wildcard). Код: `infra/max-k3s/edge/` (`edge.sh` —
драйвер с Mac, `10-wildcard-issue.sh`, `20-wildcard-distribute.sh`); в оркестраторе — флаг
`K8S_TLS_MODE=cert-manager|wildcard` (`k8s_publication.py`: в режиме wildcard Ingress без аннотации
cert-manager и без `secretName`, публикация отказывает, если секрета в кластере нет).

**Что нужно от владельца, чтобы включить:** в личном кабинете reg.ru → «Настройки API» включить доступ
к API, задать отдельный пароль для API (не пароль аккаунта) и внести в белый список IP core
`2.153.248.98`; затем на Mac `REGRU_API_Username=… REGRU_API_Password=… infra/max-k3s/edge/edge.sh all`.
Порядок включения, проверка (`openssl s_client -servername …`), откат и принятые допущения — раздел
«Edge» в [`infra/max-k3s/README.md`](../infra/max-k3s/README.md).

## Особенности Serverum (важно при любых работах на этих серверах)

1. Публичный IP — 1:1 NAT, на интерфейсе его нет; всё, что «анонсирует» адрес наружу (K3s
   `advertise-address`, WireGuard endpoint), должно использовать WG- или LAN-адрес.
2. Панель провайдера каждые 5 минут через `qemu-guest-agent` переставляет пароль пользователя
   `zeuszcz` и обнуляет `~/.ssh/authorized_keys`. Ключи держим **только** в
   `/etc/ssh/authorized_keys.d/<user>`; вход по паролю выключен.
3. Снаружи открыты только 22/80/443 (ufw). K3s API 6443 — не публичный; доступ через SSH-туннель
   или из WG-сети.

## Два оркестратора (с 23.09.2026)

Ячейки агента живут на core и на commerce, на каждом хосте свой `omnia-orchestrator` из исходников
`/opt/omnia/apps/orchestrator`. Чекаут на commerce — копия core (`rsync` по WireGuard, включая `.git`;
`git fetch` там не работает и не нужен). Любой рестарт оркестратора — это две машины одной ревизией,
строго после пересборки API (новый оркестратор со старым API ломает ожидание ёмкости при пробуждении).
`/api/health` показывает `orchestrator_release_sha: "mixed"`, пока ревизии расходятся, и smoke краснеет —
это правильно. Изоляция опубликованных приложений: узел runtime получил gVisor (`remote/70-gvisor.sh`,
RuntimeClass `gvisor`), оркестратор включает его через `K8S_APP_RUNTIME_CLASS=gvisor`.

## Изолированные сборки (Фаза 1: rootless BuildKit, 23.09.2026)

**Что было.** Production-образ приложения пользователя (кнопка «Опубликовать» → `builder.py`)
собирался командой `docker build` через root-демон Docker хоста. Dockerfile и контекст пишет агент,
то есть это недоверенный код, а каждый его `RUN`-шаг исполнялся настоящим root'ом за одной границей
ядра — единственное, что отделяло Dockerfile пользователя от хоста, был сам runc.

**Что сделано.** Сборку можно переключить на **rootless BuildKit** — отдельный сборочный демон без
root'а и без доступа к docker.sock. Код: `apps/orchestrator/.../services/buildkit.py` (бэкенд),
`services/builder.py::_build_prod_image` (выбор бэкенда), настройки `BUILD_BACKEND` /
`BUILDKIT_SOCKET` / `BUILDCTL_BINARY`. Хост готовит `infra/max-k3s/cells/50-buildkit-rootless.sh`
(идемпотентно, для core и commerce):

- контейнер `omnia-buildkitd` из образа `moby/buildkit:rootless` — внутри uid 1000, capabilities
  сброшены до `SETUID`/`SETGID` (нужны `newuidmap` для вложенного user namespace), `seccomp=unconfined`
  и свой AppArmor-профиль `omnia-buildkit` (как unconfined, но с правом `userns` — Ubuntu 24.04
  иначе запрещает user namespace процессам без профиля; если профиль не грузится, скрипт сам
  откатывается на `sysctl kernel.apparmor_restrict_unprivileged_userns=0`);
- каждый `RUN`-шаг идёт во вложенном user namespace и в своём PID namespace (режим «process
  sandbox», требует `--security-opt systempaths=unconfined`); если ядро/Docker его не дают — скрипт
  переключается на `--oci-worker-no-process-sandbox` и говорит об этом в выводе. Итого между
  Dockerfile пользователя и хостом две границы (вложенный userns → контейнер buildkitd → хост)
  вместо одной, и ни на одной из них нет root'а;
- лимиты cgroup: `BUILDKIT_CPUS=3`, `BUILDKIT_MEMORY=6g`, `BUILDKIT_PIDS=4096` (прежний путь через
  dockerd ограничений не имел); кэш слоёв — том `omnia-buildkit-cache`, демон чистит его сам при
  `gckeepstorage=20000` МБ (`/opt/omnia-runtime/buildkit/buildkitd.toml`);
- своя bridge-сеть `omnia-buildkit` (mtu 1400), из которой хост закрыт правилом ufw
  `buildkit → host: deny` — шаги сборки не достают ни до оркестратора (8003), ни до превью (80/443);
  интернет для `FROM node:22-slim` и `pnpm install` остаётся;
- оркестратор ходит к демону как обычный пользователь `zeuszcz` через unix-сокет
  `/run/omnia-buildkit/buildkitd.sock` (каталог — `tmpfiles.d`, доступ группе `omnia-buildkit` по
  ACL, поэтому переживает reboot и пересоздание сокета). Клиент `/usr/local/bin/buildctl` вынимается
  из того же образа, так что версии клиента и демона всегда совпадают;
- контракт для остального конвейера не меняется: `buildctl build --output type=docker` стримит
  образ прямо в `docker load`, дальше — тот же immutable image id, та же проверка инвентаря,
  тот же запуск контейнера / перенос на BYO-VPS / prune. Тайм-ауты (840 с на две попытки), одна
  повторная попытка, убийство процессов при отмене и хвост лога в ошибке — как у `docker build`.
  Для рантаймов, которые тянут образ из реестра, в модуле есть `build_and_push`
  (`type=image,push=true` с кредами `IMAGE_REGISTRY_*`), сейчас он никем не вызывается.

**Как включить (по одному хосту ячеек, сначала commerce, потом core):**

```bash
cd infra/max-k3s && . ./lib.sh
run_remote commerce cells/50-buildkit-rootless.sh zeuszcz     # ждём BUILDKIT_ROOTLESS_DONE …
ssh max-commerce 'sudo -u zeuszcz /usr/local/bin/buildctl --addr unix:///run/omnia-buildkit/buildkitd.sock debug workers'
# .env оркестратора: скрипт уже дописал BUILDKIT_SOCKET / BUILDCTL_BINARY / BUILD_BACKEND=docker
ssh max-commerce "sudo sed -i 's/^BUILD_BACKEND=.*/BUILD_BACKEND=buildkit/' /opt/omnia/apps/orchestrator/.env \
  && sudo systemctl restart omnia-orchestrator && curl -s 127.0.0.1:8003/health"   # когда нет активных деплоев
```

Перезапуск обязателен: членство пользователя в группе `omnia-buildkit` работающий сервис не видит.
Проверка после включения — публикация любого проекта на этом хосте: в `journalctl -u
omnia-orchestrator` появляются `deploy.build_backend backend=buildkit` и `buildkit.build_image_done`,
а `docker top omnia-buildkitd` во время сборки показывает процессы под uid 1000, не root.

**Откат:** `BUILD_BACKEND=docker` в `.env` + `systemctl restart omnia-orchestrator` — снова
`docker build` через демон; контейнер `omnia-buildkitd` можно оставить (он ничего не делает без
запросов) или снять `docker rm -f omnia-buildkitd`. Ничего в базе/журналах не зависит от бэкенда.

**Что осталось / риски.** (1) Скрипт написан по документации BuildKit и Ubuntu 24.04, живой прогон
на core/commerce — часть включения, его результат (какой режим выбран: `mode.env` в
`/opt/omnia-runtime/buildkit/`) надо записать в отчёт. (2) Docker Hub для `FROM node:22-slim` тянет
buildkitd анонимно, как раньше dockerd, — кэш в томе снимает лимит после первой сборки. (3) Шаги
сборки по-прежнему имеют выход в интернет и, через FORWARD, в WireGuard-сеть; allowlist исходящего
трафика сборок — следующий шаг, как и для ячеек. (4) Образы dev-шаблонов (`Dockerfile.dev`,
доверенные, из репозитория) по-прежнему собираются через dockerd — это наш код, не пользователя.
