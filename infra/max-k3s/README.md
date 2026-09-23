# max-k3s — инфраструктура MAX Studio на трёх VPS Serverum

## Что это простыми словами

Владелец решил (18.09.2026): платформа MAX Studio живёт на **трёх своих серверах Serverum**,
по одному на каждый кластер целевой схемы — **Core** (наш API, оркестратор, база платформы),
**Runtime** (приложения клиентов, реестр образов) и **Commerce** (подписки, биллинг). Клиентские
приложения на следующем этапе переедут в Яндекс Managed Kubernetes, а эти три сервера останутся
ядром. Эта папка — весь код, который превращает три «голых» Ubuntu-сервера в готовую площадку,
и его можно запускать повторно: скрипты идемпотентны (повторный прогон ничего не ломает).

Развёрнуто 22.09.2026. Всё, что здесь описано, — живое и проверено.

## Серверы

| Кластер | Публичный IP | LAN (enp2s0) | WireGuard | Ресурсы | Pod / Service CIDR | SSH-алиас |
|---|---|---|---|---|---|---|
| core | 2.153.248.98 | 172.197.102.2 | 10.10.0.1 | 8 vCPU · 31 GB · 500 GB | 10.42/16 · 10.43/16 | `max-core` |
| runtime | 2.153.248.99 | 172.197.102.3 | 10.10.0.2 | 8 vCPU · 31 GB · 500 GB | 10.44/16 · 10.45/16 | `max-runtime` |
| commerce | 2.153.248.100 | 172.197.102.4 | 10.10.0.3 | 8 vCPU · 31 GB · 200 GB | 10.46/16 · 10.47/16 | `max-commerce` |

Ubuntu 24.04.5, ядро 6.8.0-139, пользователь `zeuszcz` (sudo без пароля), домен **yleum.ru**.

**Особенность Serverum, которую надо знать:** публичный IP — это 1:1 NAT (на интерфейсе его нет),
а панель провайдера **каждые 5 минут** через `qemu-guest-agent` заново ставит пароль пользователя и
**обнуляет `~/.ssh/authorized_keys`**. Поэтому ключ администратора лежит в системном файле
`/etc/ssh/authorized_keys.d/zeuszcz` (sshd читает оба файла), вход по паролю выключен, root по ssh
закрыт. Пароль из панели нужен только для VNC-консоли.

## Что стоит на каждом сервере

- **ОС и защита** (`remote/00-bootstrap.sh`): обновления безопасности автоматически (без авто-reboot;
  k3s/postgres/wireguard из авто-обновлений исключены), `ufw` — снаружи открыты только 22/80/443,
  `fail2ban` на ssh (уже банит перебор), sysctl под контейнеры, лимит журнала 1 GB.
- **WireGuard-mesh 10.10.0.0/24** (`remote/10-wireguard.sh`) поверх приватной LAN: весь трафик
  между кластерами и к базам ходит только по нему. `node-exporter` каждого сервера слушает на WG-адресе.
- **K3s v1.36.4+k3s1** (`remote/20-k3s.sh`), одно-нодовый кластер: Traefik (ingress, порты 80/443),
  CoreDNS, local-path storage, metrics-server, шифрование секретов в etcd/sqlite. API-сервер
  анонсируется по WG-адресу (`advertise-address`) — без этого поды уходили к API через NAT-шлюз
  провайдера и упирались в firewall. Снаружи 6443 закрыт, kubectl с Mac — через SSH-туннели.
- **PostgreSQL 16.15** на хосте (`remote/40-postgres.sh`), слушает `127.0.0.1` + WG-адрес; база и
  роль кластера (`max_core` / `max_runtime` / `max_commerce`), DSN в `/etc/max-studio/postgres.env`
  (root, 0600). Доступ по scram из WG-сети и из подов своего кластера.
- **Бэкапы** (`40-postgres.sh` + `remote/50-backup-sync.sh`): каждую ночь 03:15 MSK
  (`max-backup.timer`) — `pg_dump` всех баз, state K3s, конфиги → `/var/backups/max-studio/<дата>`,
  14 дней. После этого копия уезжает по WireGuard на соседа (core→commerce, runtime→core,
  commerce→core) в `/var/backups/max-studio-peer/<кто>` — приёмник ограничен `rrsync -wo`, без шелла.
- **cert-manager v1.21.2** во всех трёх кластерах, ClusterIssuer `letsencrypt-prod` /
  `letsencrypt-staging` (HTTP-01 через Traefik) — `k8s/apply.sh cert-manager`.
- **Private registry** на runtime — `https://registry.yleum.ru` (`k8s/apply.sh registry`): basic-auth
  (пользователь `max`, пароль в `/etc/max-studio/registry.env` на runtime), диск 250 GB (local-path).
  Креды прописаны во все три K3s (`/etc/rancher/k3s/registries.yaml`), поды тянут образы напрямую.
- **Доступ оркестратора к runtime** (Фаза 3, этап A) — `k8s/apply.sh orchestrator-access`:
  ServiceAccount `omnia-orchestrator` в namespace `max-system` с ClusterRole ровно под объекты одного
  приложения (namespace создать/пометить — можно, удалить — нельзя); kubeconfig с токеном кладётся
  на core в `/etc/max-studio/runtime-kubeconfig.yaml` (root:zeuszcz, 0640), API — по WireGuard
  `10.10.0.2:6443`. `k8s/apply.sh publication-env` дописывает в `.env` оркестратора реестр,
  kubeconfig и `ARTIFACT_BASE_URL` (откуда init-контейнеры качают тёплые артефакты; ufw на core
  открывает 8003 для `10.10.0.0/24` и pod-сети runtime `10.44.0.0/16`). Сам флаг
  `PUBLICATION_BACKEND` эти фазы не включают.
- **Второй хост ячеек агента** (Фаза 3, этап B) — commerce с тем же Docker-стеком, что на core:
  `cells/10-cell-host-prep.sh` (servicelb off, публичный IP на lo, Docker mtu 1400, nginx-catch-all,
  acme.sh, uv, каталоги, ufw: контейнеры/ячейки/WG → 8003 и 80/443) и `cells/20-cell-host-orchestrator.sh`
  (`.env` оркестратора на основе core с переопределениями: суффикс превью `dev2`, адрес артефактов
  `http://10.10.0.3:8003`, Redis платформы `redis://10.10.0.1:6379` — на core его отдаёт
  `redis-mesh-forward.service` (socat, ufw только с 10.10.0.3; nginx `stream` на core НЕ использовать —
  модуля нет, `nginx -t` ломается), образы из реестра по digest; venv + systemd). Код на commerce —
  `rsync` с core по WireGuard (ключ `~/.ssh/id_ed25519_mesh`, `Host commerce` в ssh-конфиге core).
  Какая ячейка на каком хосте — решает API (`ORCHESTRATOR_HOSTS`, колонка `orchestrator`); подробности —
  `docs/plans/2026-09-23-k8s-publication-stage-a.md`, раздел «Этап B».
- **Ночные копии, которых не давал Stage 1:** `cells/30-cell-host-backup.sh` — на хосте ячеек базы
  ячеек + журнал оркестратора (`backup_cells.py`) в `/var/backups/max-studio/<день>/cells.tgz`;
  `cells/40-runtime-apps-backup.sh` — на runtime дамп баз каждого опубликованного приложения
  (`app-*` namespace, project-postgres и core-postgres) в `…/<день>/apps/`. Оба — `ExecStartPre`
  хостового `max-backup.service`, дальше `max-backup-push.sh` уносит всё на соседний хост.
- **Изолированные сборки образов приложений** (Фаза 1) — `cells/50-buildkit-rootless.sh` на хосте
  ячеек поднимает rootless BuildKit: контейнер `omnia-buildkitd` (`moby/buildkit:rootless`, uid 1000,
  без docker.sock, `RUN`-шаги во вложенном user namespace + своём PID namespace, лимиты 3 CPU / 6 GB /
  4096 pids, кэш в томе `omnia-buildkit-cache`, своя сеть `omnia-buildkit` с ufw-запретом на хост),
  сокет `/run/omnia-buildkit/buildkitd.sock` для пользователя оркестратора (группа `omnia-buildkit`,
  ACL через tmpfiles), клиент `/usr/local/bin/buildctl` из того же образа, проверка `buildctl debug
  workers` + smoke-сборка. Скрипт дописывает в `.env` оркестратора `BUILDKIT_SOCKET`/`BUILDCTL_BINARY`
  и `BUILD_BACKEND=docker`; **включение** — `BUILD_BACKEND=buildkit` + `systemctl restart
  omnia-orchestrator`, **откат** — `BUILD_BACKEND=docker` + restart. Подробно —
  `docs/09-max-k3s-infra.md`, раздел «Изолированные сборки».
- **Биллинговый воркер в commerce** (Фаза 3, «Commerce-кластер», код готов 23.09, не включён —
  ждёт кредов ЮKassa) — `commerce/10-billing-workloads.sh` (`plan` / `all` / `handover` /
  `rollback`, всё с `--dry-run`) + манифесты `k8s/commerce/*.yaml`: Deployment `billing/billing-worker`
  (`python -m omnia_api.workers.billing`, образ api из реестра по digest, `/health` на :8090),
  NetworkPolicy, Secret из `/etc/max-studio/billing-worker.env` на core по ssh-потоку. База
  платформы — по WireGuard через `postgres-mesh-forward.service` на core (socat
  `10.10.0.1:15432 → omnia-prod-postgres`, роль `max_billing` только с правами на биллинговые
  таблицы, ufw только для commerce). Пока тик живёт потоком RQ-воркера на core
  (`BILLING_LIFECYCLE_ENABLED=true`); план и порядок включения — `docs/plans/2026-09-23-phase3-commerce.md`.
- **Ещё один хост ячеек по запросу** — `cells/60-order-cell-host.sh --name cells3` (`--dry-run`
  печатает весь план): заказ VPS у Serverum через `apps/orchestrator` (`serverum_cli`, эндпоинты
  панели — уточнить у поддержки, токен `SERVERUM_API_TOKEN`), строка в `inventory.env`, bootstrap,
  WireGuard на всех хостах, Postgres, подготовка хоста ячеек без K3s, rsync кода с core,
  оркестратор, ufw redis на core, ночная копия, запись в `ORCHESTRATOR_HOSTS` с `enabled=false`;
  после DNS `*.dev3` владельца — `--enable`.
- **Мониторинг** на core — kube-prometheus-stack 91.4.1 (`k8s/apply.sh monitoring`):
  Prometheus (15 дней / 50 GB), Alertmanager, Grafana 13 на `https://grafana.yleum.ru`
  (admin, пароль в `/etc/max-studio/grafana.env` на core). Собирает метрики кластера core и
  node-exporter всех трёх VPS. Метрики кластеров runtime/commerce — следующий шаг (см. ниже).

## DNS (reg.ru → yleum.ru) — заведено владельцем 22.09.2026

| Имя | Тип | Значение | Зачем |
|---|---|---|---|
| `@`, `www` | A | 2.153.248.98 | корень домена → core (сейчас заглушка `k8s/apply.sh placeholder`, при переезде платформы — `kubectl delete ns landing`) |
| `core` / `runtime` / `commerce` | A | .98 / .99 / .100 | имена серверов, SAN в сертификате K3s |
| `grafana` | A | 2.153.248.98 | мониторинг |
| `registry` | A | 2.153.248.99 | реестр образов |
| `*.apps` | A | **2.153.248.99** | опубликованные приложения клиентов — в кластере runtime (Traefik + cert-manager, `PUBLICATION_BACKEND=kubernetes`, `PUBLIC_HOST_SUFFIX=apps.yleum.ru`). До 23.09 указывала на core (Фаза 2a); переведена владельцем 23.09 |
| `*.dev` | A | 2.153.248.98 | dev-превью ячеек агента на core (`RUNTIME_HOST_SUFFIX=dev.yleum.ru`, в API — `PROJECT_CELL_PREVIEW_HOST_SUFFIX` и `GATE_PREVIEW_RESOLVER_RULES`). Заведена владельцем 23.09 |
| `*.dev2` | A | 2.153.248.100 | dev-превью ячеек на втором хосте ячеек (commerce, этап B); в API — запись `commerce` в `ORCHESTRATOR_HOSTS` |
| `api`, `app` | A | 2.153.248.98 | платформа |

Сертификаты Let's Encrypt выпущены для `yleum.ru`, `www`, `grafana`, `registry` (до 21.12.2026,
обновляются сами). Две ловушки, встреченные при этом: провайдерские резолверы (Yandex DNS)
кэшируют «имени нет» до 3 часов — поэтому `k8s/apply.sh coredns` заставляет кластеры резолвить
`yleum.ru` напрямую с NS reg.ru; и у forward-плагина CoreDNS лимит 15 upstream'ов, а у reg.ru
16 NS-адресов — берём по два от каждого NS.

## Как этим пользоваться с Mac

```bash
cd infra/max-k3s
./provision.sh status                 # сводка по трём серверам
./kube-tunnel.sh up                   # туннели к API: core :16443, runtime :16444, commerce :16445
export KUBECONFIG=~/.kube/max-studio.yaml
kubectl --context max-runtime get pods -A
helm --kube-context max-core list -A
ssh max-core                          # на сервере kubectl уже настроен (KUBECONFIG в .bashrc)
```

Полный цикл с нуля: `./provision.sh all` (bootstrap → wireguard → k3s → kubeconfigs → postgres →
backup-sync → k8s → status). Любую фазу можно запускать отдельно и повторно. Логи bootstrap —
`/tmp/max-k3s-logs/`. Секреты (пароли Postgres/registry/Grafana) генерируются один раз и живут
только на серверах в `/etc/max-studio/*.env`; в репозиторий и чат они не попадают.

## Проверено 22.09.2026

- `ssh max-{core,runtime,commerce}` по ключу; пароль отключён; fail2ban уже банит перебор.
- WireGuard: все 6 направлений ping OK, handshake у всех пиров.
- K3s: 3 × Ready, все системные поды Running, Traefik отвечает на публичных IP (404 на `/`).
- Postgres: вход по DSN с WG-адреса и **из пода** (`max_core@10.10.0.1`) — OK.
- Registry: `/v2/` без пароля → 401, с паролем → 200. Grafana `/api/health` → ok (13.2.2).
- Prometheus: 15 целей up, в т.ч. `node-exporter` 10.10.0.1/2/3.
- Бэкап: прогон вручную на всех трёх, копии легли на соседей (6.2–6.6 MB каждая).
- После DNS: `https://yleum.ru`, `https://www.yleum.ru`, `https://grafana.yleum.ru/api/health`,
  `https://registry.yleum.ru/v2/` — настоящие сертификаты Let's Encrypt, `curl` без `-k` проходит;
  внутри кластера `yleum.ru` резолвится через coredns-custom, `kubernetes.default` — по-прежнему.

## Edge: единый wildcard-сертификат для всех трёх хостов (код готов 23.09.2026, ждёт доступа к API reg.ru)

### Зачем

Сейчас сертификаты выпускаются на каждом хосте отдельно и по одному на имя: nginx + acme.sh (HTTP-01)
для превью `*.dev`/`*.dev2` на core и commerce, cert-manager (HTTP-01 через Traefik) для опубликованных
приложений `*.apps` в runtime, certbot для `yleum.ru`/`www`/`grafana`. Три последствия, все уже
проявлялись: первая публикация ждёт выпуска (десятки секунд — минуты), любая неполадка HTTP-01
(default-deny в namespace, 3-часовой кэш «имени нет» у Yandex DNS, DNS не на тот хост) оставляет
приложение без TLS, и новое имя никогда не покрыто заранее. Edge решает это одним сертификатом
Let's Encrypt с SAN `yleum.ru`, `*.yleum.ru`, `*.apps.yleum.ru`, `*.dev.yleum.ru`, `*.dev2.yleum.ru`,
выпущенным через DNS-01 у reg.ru (TXT-записи ставит acme.sh плагином `dns_regru`; HTTP-01 для
wildcard невозможен по правилам Let's Encrypt).

### Что делают скрипты (`infra/max-k3s/edge/`)

| Скрипт | Где | Что |
|---|---|---|
| `edge.sh` | Mac | драйвер: `install` (скрипты на хосты как `/usr/local/sbin/max-edge-*`, `edge.env`, таймер), `creds`, `authorize`, `issue`, `distribute`, `orchestrator-env on\|off`, `k8s-mode wildcard\|cert-manager`, `status`, `rollback`, `all` |
| `10-wildcard-issue.sh` (`max-edge-issue`) | core, root | `issue`: acme.sh (`/usr/local/lib/max-edge/acme.sh`, home `/etc/max-studio/edge/acme-home`, отдельный от acme.sh оператора — реквизиты reg.ru не видны пользователю оркестратора) выпускает сертификат по DNS-01, `--install-cert` кладёт `wildcard.key` / `wildcard.fullchain.pem` / `wildcard.cert.pem` / `wildcard.ca.pem` в `/etc/max-studio/edge/` (0600 root) и запоминает `--reloadcmd max-edge-distribute push`; проверка `openssl x509`: SAN = список имён, ключ соответствует сертификату. Повторный запуск при действующем сертификате ничего не выпускает. `renew`: `acme.sh --cron` (продление за 30 дней до конца, затем reloadcmd → раздача), запускается таймером `max-edge-renew.timer` ежедневно 04:40. `status`: срок, SAN, дни; код 1 при < 20 дней |
| `20-wildcard-distribute.sh` (`max-edge-distribute`) | все три, root | `push` (core): tar с четырьмя файлами → по WireGuard `ssh maxedge@10.10.0.2/.3` (ключ `/etc/max-studio/edge/id_ed25519`, на пирах — forced command `sudo -n max-edge-distribute receive <роль>` + sudoers ровно на эту команду, `restrict`; вход `maxedge` разрешён drop-in'ом `sshd_config.d/06-max-edge.conf`, потому что bootstrap ограничивает `AllowUsers`), затем `apply core` локально. Приёмник проверяет сертификат (читается, ключ от него, не истёк, SAN покрывает имена роли) и только потом устанавливает и применяет. **runtime**: `Secret tls kube-system/wildcard-yleum` + `TLSStore default` (Traefik, группа `traefik.io` определяется по CRD) → сертификат по умолчанию для всех Ingress без своего `secretName`; проверка `openssl s_client -servername edge-probe.apps.yleum.ru`. **core**: раскладка `/etc/max-studio/edge/wildcard/dev.yleum.ru/{fullchain.pem,privkey.pem}` для оркестратора; платформенные vhost'ы (`sites-available/yleum.ru`, `grafana.yleum.ru`) с certbot-строк на wildcard (бэкап `*.max-edge.orig`, `nginx -t` до reload, при ошибке — возврат); в `/etc/letsencrypt/renewal/yleum.ru.conf` `installer = None` — certbot продолжает продлевать свой сертификат как запасной, но больше не переписывает vhost. **commerce**: раскладка `…/wildcard/dev2.yleum.ru/`. `rollback <роль>` — обратно (см. ниже) |

Секреты: `/etc/max-studio/edge/regru.env` (root, 0600) с `REGRU_API_Username` / `REGRU_API_Password`
— только на core, пишется через stdin (`edge.sh creds`), в argv/логи/репозиторий не попадает; acme.sh
дублирует их в свой `account.conf` (тот же каталог, 0600) для продлений.

### Что нужно от владельца

1. Личный кабинет reg.ru → «Настройки API»: включить доступ к API; задать **отдельный пароль для API**
   (не пароль аккаунта); в «Разрешённые IP» добавить публичный IP core **2.153.248.98** (вызовы идут с
   него; при переезде выпуска на другой хост — поменять). Домен `yleum.ru` должен обслуживаться DNS
   reg.ru (так и есть).
2. На Mac: `REGRU_API_Username=<логин> REGRU_API_Password=<api-пароль> infra/max-k3s/edge/edge.sh all`.
   Без переменных `edge.sh creds` спросит их в терминале (пароль не печатается).

### Порядок включения (что делает `edge.sh all`, можно по шагам)

1. `install` — скрипты, `edge.env` (домен, пиры `runtime:10.10.0.2 commerce:10.10.0.3`, email), таймер.
2. `creds` — реквизиты reg.ru на core.
3. `authorize` — ключ раздачи core → пользователь `maxedge` на runtime и commerce.
4. `issue` — выпуск (первый раз 1–5 минут: acme.sh ждёт, пока TXT-записи появятся на NS reg.ru; если
   валидация падает по времени — `EDGE_DNS_SLEEP=180` в `/etc/max-studio/edge/edge.env`), установка,
   раздача на все хосты. С этого момента **runtime** отдаёт wildcard для любого `*.apps.yleum.ru`
   (в т.ч. ещё не опубликованного) вместо самоподписанного «TRAEFIK DEFAULT CERT»; существующие
   приложения с cert-manager продолжают отдавать свои сертификаты (Traefik выбирает по SNI из секретов
   Ingress'ов, wildcard — запасной по умолчанию).
5. `orchestrator-env on` — `OMNIA_WILDCARD_CERT_ROOT=/etc/max-studio/edge/wildcard` в `.env`
   оркестратора на core и commerce; **перезапуск оркестратора — вручную**, когда нет активных
   генераций (`sudo systemctl restart omnia-orchestrator`). После него новые превью получают https-блок
   с wildcard сразу (`nginx_writer._wildcard_cert_dir`, acme не вызывается), старые переезжают на
   wildcard при следующем `ensure_tls` (открытие превью / reconcile).
6. `k8s-mode wildcard` — `K8S_TLS_MODE=wildcard` в `.env` оркестратора на core (драйвер сначала
   проверяет, что Secret `wildcard-yleum` есть в runtime), перезапуск оркестратора. Новые публикации
   создают Ingress без аннотации cert-manager и без `secretName` → TLS с первой секунды. Переизданная
   публикация старого приложения тоже переезжает на wildcard (server-side apply снимает аннотацию и
   `secretName`); его `Certificate`/`public-tls` остаются в namespace и безвредно продлеваются.

### Как проверить

```bash
infra/max-k3s/edge/edge.sh status          # срок на core, применение на каждом хосте, SAN на публичных :443
openssl s_client -connect 2.153.248.99:443 -servername edge-probe.apps.yleum.ru </dev/null 2>/dev/null \
  | openssl x509 -noout -issuer -ext subjectAltName      # runtime: Let's Encrypt, DNS:*.apps.yleum.ru …
openssl s_client -connect 2.153.248.98:443 -servername yleum.ru </dev/null 2>/dev/null \
  | openssl x509 -noout -enddate -ext subjectAltName     # core: платформа на wildcard
ssh max-core 'sudo /usr/local/sbin/max-edge-issue status; systemctl list-timers max-edge-renew.timer'
```

`edge-probe.dev.yleum.ru` / `edge-probe.dev2.yleum.ru` без живого превью отдают catch-all с
самоподписанным сертификатом — **так задумано**: catch-all nginx на core/commerce остаётся на
snakeoil, потому что оркестратор отличает «старые воркеры nginx после reload» от нового vhost
именно по ошибке TLS (`nginx_writer._probe_live`, гонка из фикса 239095b0); wildcard подставляется в
vhost каждого превью. Проверять превью — по живому имени `cell-<id>-dev.dev.yleum.ru`.

### Откат

`infra/max-k3s/edge/edge.sh rollback` делает всё разом (снимает `OMNIA_WILDCARD_CERT_ROOT`,
`rollback <роль>` на каждом хосте, `K8S_TLS_MODE=cert-manager`, выключает таймер); по частям:

- **runtime**: `max-edge-distribute rollback runtime` — удаляет `TLSStore default` и секрет (Traefik
  снова с самоподписанным по умолчанию; приложения с cert-manager не затронуты) и приложениям,
  опубликованным в режиме wildcard (Ingress без `secretName`), возвращает аннотацию
  `cert-manager.io/cluster-issuer=letsencrypt-prod` + `secretName: public-tls` — cert-manager выпускает
  им сертификаты по HTTP-01 (NetworkPolicy `acme-solver` в режиме wildcard не снималась). Оркестратор
  при следующей публикации перепишет Ingress по `K8S_TLS_MODE`.
- **core**: `max-edge-distribute rollback core` — vhost'ы обратно на `/etc/letsencrypt/live/yleum.ru/`
  (lineage всё это время продлевался; если его нет — `certbot --nginx --redirect -d yleum.ru -d
  www.yleum.ru -d grafana.yleum.ru`, как в `migrate/30-bring-up.sh`), `installer = nginx`, раскладка
  оркестратора убрана. **commerce**: раскладка убрана. На обоих — снять `OMNIA_WILDCARD_CERT_ROOT`
  (`edge.sh orchestrator-env off`) и перезапустить оркестратор: превью снова выпускают сертификаты
  acme.sh по HTTP-01 при следующем `ensure_tls` (vhost'ы, уже указывающие на файлы wildcard, продолжают
  работать, пока файлы лежат — их скрипт не удаляет).

### Допущения и риски (проверить при первом живом прогоне)

- Traefik в runtime — стоковый k3s: `TLSStore` с именем `default` принимается из любого namespace
  (`defaultTLSResourcesNamespace` не задан), на `websecure` TLS включён по умолчанию (chart:
  `ports.websecure.http.tls.enabled=true`), поэтому Ingress без `secretName` получает TLS с
  сертификатом по умолчанию, а `:80` продолжает отвечать без редиректа — как и сейчас. Аннотацию
  `router.tls` не ставим: она отключила бы HTTP на `:80`.
- Два TXT-значения на одном имени `_acme-challenge.yleum.ru` (для `yleum.ru` и `*.yleum.ru`):
  `zone/add_txt` у reg.ru добавляет запись, не заменяет. Если reg.ru откажет — убрать `yleum.ru` и
  `*.yleum.ru` из `EDGE_NAMES` в `edge.env` (сертификат останется для `*.apps`/`*.dev`/`*.dev2`, скрипт
  сам пропустит переключение платформенных vhost'ов и оставит их на certbot).
- acme.sh ставится из `master` GitHub (как и acme.sh оператора через get.acme.sh); закрепить версию —
  `EDGE_ACME_SH_REF=<тег>` в `edge.env` до `install`/`issue`.
- Лимиты Let's Encrypt: 5 одинаковых сертификатов в неделю — скрипт не переиздаёт действующий, для
  репетиции есть `EDGE_ACME_SERVER=letsencrypt_test`.
- Один ключ на три хоста: компрометация любого хоста = компрометация всех имён домена. Ключ
  раздачи core умеет на пирах только `receive`; сам wildcard читает только root и nginx.
- Мониторинг срока: `max-edge-issue status` (код 1 при < 20 дней) — добавить в смоук/алерты
  отдельным шагом; пока это ручная проверка.

## Что дальше (не сделано сознательно)

1. **Wildcard-сертификат (edge)** — код готов (раздел «Edge» выше), включение ждёт доступа к API reg.ru
   от владельца; после включения — перевести `K8S_TLS_MODE=wildcard` и снять per-host HTTP-01 с горячего
   пути публикации.
2. **Переезд платформы** (Фаза 2 по вектору V9): api/worker/web/gateway/orchestrator с
   docker-compose на 170.168.72.200 → Helm-чарт в кластере core, Redis/MinIO в кластере,
   миграция базы в `max_core`; биллинг → commerce; деплой MAX-приложений через
   `infra/max-app-chart` в runtime. Отдельная работа с переносом данных.
3. Метрики кластеров runtime/commerce в общий Prometheus (агент с remote_write по WireGuard),
   Loki для логов, `postgres_exporter`, алерты в Telegram.
4. Второе off-host место для бэкапов вне Serverum (сейчас копии только между этими же VPS).
