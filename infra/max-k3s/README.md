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
- **Мониторинг** на core — kube-prometheus-stack 91.4.1 (`k8s/apply.sh monitoring`):
  Prometheus (15 дней / 50 GB), Alertmanager, Grafana 13 на `https://grafana.yleum.ru`
  (admin, пароль в `/etc/max-studio/grafana.env` на core). Собирает метрики кластера core и
  node-exporter всех трёх VPS. Метрики кластеров runtime/commerce — следующий шаг (см. ниже).

## DNS, который должен завести владелец (reg.ru → yleum.ru)

| Имя | Тип | Значение | Зачем |
|---|---|---|---|
| `core.yleum.ru` | A | 2.153.248.98 | имя сервера, SAN в сертификате K3s |
| `runtime.yleum.ru` | A | 2.153.248.99 | то же |
| `commerce.yleum.ru` | A | 2.153.248.100 | то же |
| `grafana.yleum.ru` | A | 2.153.248.98 | мониторинг (сертификат выпустится сам после появления записи) |
| `registry.yleum.ru` | A | 2.153.248.99 | реестр образов (то же) |
| `*.apps.yleum.ru` | A | 2.153.248.99 | будущие приложения клиентов на runtime |
| `api.yleum.ru`, `app.yleum.ru` | A | 2.153.248.98 | платформа (Фаза 2 — переезд api/web/gateway) |

Пока записей нет, Traefik отдаёт самоподписанный сертификат; cert-manager держит заказы в очереди и
выпустит Let's Encrypt автоматически, как только имя начнёт резолвиться (проверить:
`kubectl --context max-core get certificate -A`).

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

## Что дальше (не сделано сознательно)

1. **DNS-записи** выше — только владелец (доступ в reg.ru); после них — сертификаты сами.
2. **Wildcard `*.apps.yleum.ru`** для приложений клиентов: HTTP-01 даёт по сертификату на имя
   (лимит Let's Encrypt 50/неделю на домен); для wildcard нужен DNS-01 через API reg.ru —
   владелец включает API-доступ, ключ кладём в Secret cert-manager.
3. **Переезд платформы** (Фаза 2 по вектору V9): api/worker/web/gateway/orchestrator с
   docker-compose на 170.168.72.200 → Helm-чарт в кластере core, Redis/MinIO в кластере,
   миграция базы в `max_core`; биллинг → commerce; деплой MAX-приложений через
   `infra/max-app-chart` в runtime. Отдельная работа с переносом данных.
4. Метрики кластеров runtime/commerce в общий Prometheus (агент с remote_write по WireGuard),
   Loki для логов, `postgres_exporter`, алерты в Telegram.
5. Второе off-host место для бэкапов вне Serverum (сейчас копии только между этими же VPS).
