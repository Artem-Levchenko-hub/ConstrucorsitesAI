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

## Особенности Serverum (важно при любых работах на этих серверах)

1. Публичный IP — 1:1 NAT, на интерфейсе его нет; всё, что «анонсирует» адрес наружу (K3s
   `advertise-address`, WireGuard endpoint), должно использовать WG- или LAN-адрес.
2. Панель провайдера каждые 5 минут через `qemu-guest-agent` переставляет пароль пользователя
   `zeuszcz` и обнуляет `~/.ssh/authorized_keys`. Ключи держим **только** в
   `/etc/ssh/authorized_keys.d/<user>`; вход по паролю выключен.
3. Снаружи открыты только 22/80/443 (ufw). K3s API 6443 — не публичный; доступ через SSH-туннель
   или из WG-сети.
