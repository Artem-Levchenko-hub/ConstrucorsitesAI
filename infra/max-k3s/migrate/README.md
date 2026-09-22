# Переезд платформы MAX Studio на core (Фаза 2a) — runbook

## Что сделано и почему так

Платформа переехала со старого сервера 170.168.72.200 (`constructor.lead-generator.ru`) на VPS **core**
(2.153.248.98) под доменом **yleum.ru** — 22.09.2026. Переехала **той же связкой, что работала на
старом сервере**: compose-стек `omnia-prod-*` (web, api, worker, generation-worker, gateway, postgres,
redis, minio), оркестратор как хост-сервис systemd с Docker, nginx на хосте как публичный вход,
Let's Encrypt через certbot (платформа) и acme.sh (per-app сертификаты приложений клиентов).

Почему не в K3s: код платформы завязан на Docker на том же хосте — оркестратор создаёт контейнеры
ячеек (`omnia-cell-*`, `omnia-machine-*`), worker ходит в них по docker-сети `omnia-runtime_default`,
api ходит в оркестратор через шлюз docker-моста, gate-браузер резолвит превью через хостовый nginx.
K8s-бэкенда у оркестратора нет (`infra/max-app-chart` — заготовка). Перенос в кластер — отдельная
работа (Фаза 3 вектора V9). K3s на core остаётся под мониторинг (`grafana.yleum.ru` → nginx →
Traefik ClusterIP) и будущий перенос; `servicelb` в нём выключен, чтобы 80/443 держал nginx.

Старый сервер **не тронут**: там продолжают жить чужие проекты (endless-war, innertalk, …), и MAX
Studio на нём остаётся запасным вариантом, пока владелец не решит его выключить.

## Что переехало, что нет

| Переехало | Как |
|---|---|
| Репозиторий `/opt/omnia` (HEAD 61b1c7bd на момент копии) | rsync без node_modules/.venv/.next |
| Секреты платформы, оркестратора, runtime (`.env`) | сервер→сервер, домен/суффикс/пути переписаны `30-bring-up.sh` |
| База платформы `omnia` (132 пользователя, 5 проектов, 23 МБ) | `pg_dump -Fc` → `pg_restore` в пустую базу |
| База `omnia_users` (postgres-users :5433) | `pg_dumpall` → psql |
| MinIO (превью, снапшоты, фото — 7 МБ) | tar тома `full_minio-data` |
| Бэкапы платформы, аккаунт acme.sh, история восстановлений кода | rsync `/opt/omnia-runtime/{backups,acme-home,state/code-restoration*,state/restoration-*}` |
| Страница `/otchet` | tar `/var/www/otchet` |
| Docker-образы: api/web по SHA релиза, gateway, project-machine + guard, max-public-core ×2, все `omnia-template-*:dev`, minio/minio, minio/mc | `docker save \| ssh \| docker load` — те же ID образов, что на проде (оркестратор ссылается на них по digest) |

**Не переехало сознательно:** Docker-ячейки пяти проектов (194 ГБ чекпойнтов в
`state/project-machines`, тома, контейнеры). В новой базе их `project_cell_workspaces` помечены
`deleted` — платформа считает, что ячеек нет, и создаст новые при следующей работе с проектом.
Код этих проектов остаётся на старом сервере; для двух проектов владельца («Мои задачи», «Клиенты»)
доступна история восстановлений (`state/code-restorations`). Канарейки мониторинга нужно
пересоздать на новой платформе (см. «Что дальше»).

## Скрипты (все идемпотентны)

| Шаг | Где запускать | Что делает |
|---|---|---|
| `10-core-host-prep.sh` | `ssh max-core sudo bash -s -- zeuszcz yleum.ru admin@yleum.ru < …` | servicelb off, Docker CE (mtu 1400), nginx+certbot, acme.sh, uv, каталоги, ufw для docker→host, catch-all nginx |
| `20-copy-from-old.sh` | на старом сервере от `i48ptgvnis` (`scp` → `nohup bash …`) | всё из таблицы выше; ключ старого сервера — в `/etc/ssh/authorized_keys.d/zeuszcz` на core |
| `30-bring-up.sh` | `ssh max-core sudo bash -s -- zeuszcz yleum.ru < …` | env под домен, чистое runtime-состояние, registry + postgres-users, compose-стек, восстановление баз/MinIO, оркестратор (systemd, `NoNewPrivileges=false`), nginx-vhost'ы, certbot |
| `40-smoke.sh` | с Mac: `./40-smoke.sh https://yleum.ru 25` | здоровье публичных точек + регистрация → проект → генерация → превью по https |

## Грабли, на которые наступили (и что теперь делает runbook)

1. **`minio/minio`, `minio/mc` пропали с Docker Hub** («pull access denied») — везём кэшированные образы со старого сервера.
2. **Повторный `chown -R` сломал данные postgres-users** (uid 70) — chown теперь обходит `postgres-users-data` и `registry-data`.
3. **Старые vhost-конфиги ячеек** ссылались на wildcard-сертификат `*.preview.lead-generator.ru` → `nginx -t` падал, certbot не ставился — конфиги ячеек не копируются/убираются в `migrate-in/old-state`.
4. **Резервации CPU и локи старых ячеек** (`state/project-cells*`, `state/locks`) → admission отвечал `insufficient_cpu` — состояние ячеек не копируется.
5. **Каталог локов должен быть `0700`** (`services/cell_lock.py`), иначе `workspace_lock_unavailable` → ячейки не создаются.
6. **api/worker бесконечно дёргали оркестратор** за workspace старых ячеек («workspace state missing») — после восстановления базы workspaces помечаются `deleted`.
7. Docker-мосты за NAT провайдера — `mtu 1400`, как на старом сервере.
8. **`*.apps.yleum.ru` указывал на runtime (.99)**, а ячейки живут на core → HTTP-01 для превью
   получал 404 → «draft preview TLS provisioning failed». Запись переведена на .98 (владелец, reg.ru).
9. **kube-proxy перехватывал публичный IP.** После отключения servicelb у сервиса `traefik` остался
   LoadBalancer-статус с 2.153.248.98, и правила `KUBE-SERVICES … loadbalancer IP` DNAT-или весь
   трафик контейнеров на `<public ip>:443` в Traefik с самоподписанным сертификатом → api получал
   `ConnectError` на проверке data-plane приложения. Лечится `service.type: ClusterIP` через
   HelmChartConfig (`/var/lib/rancher/k3s/server/manifests/traefik-config.yaml`).
10. **Публичный IP — NAT, сервер сам себя по нему не видит.** Адрес повешен на `lo`
    (`/etc/netplan/60-public-ip-hairpin.yaml`), чтобы api из Docker ходил на `*.apps.yleum.ru`
    и `yleum.ru` локально; ufw пускает docker-подсети и сеть ячеек (10.253/16) на 80/443.
11. **Гонка с перезагрузкой nginx** (проявилась только здесь, потому что на старом сервере
    сертификат был wildcard и https-блок писался сразу): `systemctl reload nginx` возвращается
    до того, как новые воркеры начинают отвечать, а старые ~200 мс отдают catch-all с
    самоподписанным сертификатом. Проверка data-plane финализации MAX стреляла в ту же
    секунду и валила прогон (`ConnectError`). Исправлено в оркестраторе (коммит 239095b0):
    после reload публикация ждёт, пока vhost реально отвечает за хост (`_wait_live`).

## Как деплоить на новый прод

```bash
ssh max-core 'cd /opt/omnia && git fetch origin && git merge --ff-only origin/main \
  && cd apps/llm-gateway/deploy/full && docker compose up -d --build api generation-worker worker web gateway'
# оркестратор (хост-сервис, из исходников):
ssh max-core 'cd /opt/omnia/apps/orchestrator && ~/.local/bin/uv sync --frozen && sudo systemctl restart omnia-orchestrator && curl -s 127.0.0.1:8003/health'
curl -s https://yleum.ru/api/health
```

Секреты: `/opt/omnia/apps/llm-gateway/deploy/full/.env`, `/opt/omnia/apps/orchestrator/.env`,
`/opt/omnia-runtime/.env` (все 0600, владелец `zeuszcz`). Логи: `docker logs omnia-prod-api`,
`journalctl -u omnia-orchestrator`, `/var/log/nginx/`. Ночной бэкап хоста (`max-backup.timer`)
снимает `pg_dump` только хостового Postgres — compose-postgres платформы бэкапится своим
механизмом (`infra/backup/backup-omnia.sh`, `/api/backups/offhost`) — см. «Что дальше».

## Сквозная проверка — пройдена 22.09 (вечер)

`40-smoke.sh https://yleum.ru 25`: регистрация → проект → ячейка (6 контейнеров) → сборка агентом
(claude-sonnet-5 через llmgw) → сертификат Let's Encrypt для `cell-<id>-dev.apps.yleum.ru` →
финализация MAX `complete` (bootstrap, fast_check, full_build, runtime_probe, promote, snapshot) →
версия проекта, ответ «Готово — приложение собрано и проверено». Снаружи превью отвечает 401
(граница входа MAX) с настоящим сертификатом. В `/runtime` после сборки состояние `failed` — это
завершившаяся dev-машина, не сбой.

По дороге: llmgw.ru после серии 429 (несколько параллельных прогонов) **заблокировал ключ**; новый
ключ владельца разрешает модели только под именами с префиксом провайдера — шлюз и так шлёт их
(`providers/llmgw.py`, `native_slug`), поэтому ручные пробы «голыми» именами (403) — ложная тревога.
Не гонять несколько смоков параллельно. Удаление смок-проекта раньше падало 500 (FK на
`project_cell_proofs`) — исправлено в 2261f4fc.

## Что дальше

- Перевести `infra/backup` (offhost workflow, restore-test) и GitHub-переменные release-identity
  (`PRODUCTION_EXPECTED_*_RELEASE_SHA`, `PLATFORM_URL` в `production-smoke.yml`) на новый сервер.
- Пересоздать канарейки мониторинга под аккаунтом владельца на yleum.ru (нужен MAX-бот), обновить
  `PRODUCTION_MAX_CANARY_URL`.
- Решение владельца по старому серверу: redirect `constructor.lead-generator.ru → yleum.ru` и/или
  остановка `omnia-prod-*` там.
- Фаза 3: api/web/gateway в K3s core, приложения клиентов в runtime через `infra/max-app-chart`.
