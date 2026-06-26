# Runbook — Phase 1: Sandbox hardening (host enablement)

> Прод-хост `170.168.72.200`. Этот runbook ВКЛЮЧАЕТ уже выложенный, но инертный
> код Phase 1 (commits `3e3859d` + текущий). Весь код по умолчанию OFF — прод
> ведёт себя как раньше, пока эти шаги не выполнены. Каждый шаг обратим.
>
> ⚠️ Шаг 2 (регистрация gVisor) перезапускает **docker-демон** → на ~10–30 сек
> гаснут ВСЕ контейнеры (прод-аппы и dev-превью). Делать в окно обслуживания.
> Остальные шаги перезапускают только `omnia-orchestrator.service` (контейнеры
> живут) либо вообще без рестарта.

Что включаем (по нарастанию деликатности):
1. **kernel hardening** (`container_harden`) — безопасно, без host-изменений.
2. **gVisor runtime** (`container_runtime=runsc`) — нужен install + docker restart.
3. **egress allowlist** (`container_egress_proxy`) — нужен proxy-контейнер.
4. **per-project network** (`isolate_project_network`) — самое деликатное, в конце.

Делай по одному, проверяя после каждого. Откат — снять env + `systemctl restart omnia-orchestrator` (код снова инертен).

---

## Предпосылки

```bash
ssh lh-server                      # alias; сырой IP виснет (нет ключа)
cd /opt/omnia                      # репо прода
git pull                           # подтянуть код Phase 1
docker ps                          # запомнить число живых контейнеров (для проверки после)
```

Оркестратор — host systemd-сервис (`omnia-orchestrator.service`), env читается из
его `.env` / unit-Environment. Где именно лежит env:
```bash
systemctl cat omnia-orchestrator.service | grep -iE "EnvironmentFile|Environment="
```

---

## Шаг 1 — kernel hardening (безопасно, первым)

`no-new-privileges` + PID-потолок. Не трогает host, только новые dev-контейнеры.

```bash
# в env оркестратора:
CONTAINER_HARDEN=true
CONTAINER_PIDS_LIMIT=512
sudo systemctl restart omnia-orchestrator.service   # контейнеры НЕ гаснут
```

Проверка (создай тестовый проект из UI, затем):
```bash
docker inspect omnia-dev-<slug> --format '{{.HostConfig.SecurityOpt}} {{.HostConfig.PidsLimit}}'
# ждём: [no-new-privileges:true] 512
```
Откат: убрать `CONTAINER_HARDEN`, restart orchestrator.

---

## Шаг 2 — gVisor (runsc) ⚠️ docker restart, окно обслуживания

Userspace-ядро: перехватывает syscalls, побег из контейнера не достаёт host-ядро.

### 2.1 Установить runsc
```bash
(
  set -e
  ARCH=$(uname -m); URL=https://storage.googleapis.com/gvisor/releases/release/latest/${ARCH}
  wget -q "${URL}/runsc" "${URL}/runsc.sha512" "${URL}/containerd-shim-runsc-v1" "${URL}/containerd-shim-runsc-v1.sha512"
  sha512sum -c runsc.sha512 -c containerd-shim-runsc-v1.sha512
  sudo install -o root -g root -m 0755 runsc containerd-shim-runsc-v1 /usr/local/bin/
)
runsc --version
```

### 2.2 Зарегистрировать runtime в docker
```bash
sudo cp /etc/docker/daemon.json /etc/docker/daemon.json.bak 2>/dev/null || true
# добавить (или слить с существующим JSON):
sudo tee /etc/docker/daemon.json >/dev/null <<'JSON'
{
  "runtimes": { "runsc": { "path": "/usr/local/bin/runsc" } }
}
JSON
sudo systemctl restart docker        # ⚠️ ВСЕ контейнеры перезапускаются
docker info | grep -i runtimes       # ждём: runsc в списке
```
После рестарта docker оркестратор/прод-аппы поднимутся сами (restart-policy
`unless-stopped`). Проверь, что число контейнеров вернулось к запомненному.

### 2.3 Включить в оркестраторе
```bash
# env оркестратора:
OMNIA_CONTAINER_RUNTIME=runsc
sudo systemctl restart omnia-orchestrator.service
```
Проверка:
```bash
# новый тестовый проект, затем:
docker inspect omnia-dev-<slug> --format '{{.HostConfig.Runtime}}'   # ждём: runsc
docker exec omnia-dev-<slug> dmesg 2>&1 | head -1                    # gVisor отдаёт свой fake-dmesg
```
Откат runtime: убрать `OMNIA_CONTAINER_RUNTIME`, restart orchestrator (новые
контейнеры снова на runc; старые runsc-контейнеры пересоздадутся при следующем
provision). Полный откат gVisor: вернуть `daemon.json.bak` + restart docker.

⚠️ Совместимость: некоторые Node/Next-сборки чувствительны к gVisor (io_uring,
определённые syscalls). Сначала прогнать ОДИН тестовый билд (entities + realtime)
под runsc и убедиться, что dev-сервер стартует и HMR живой, ПЕРЕД флипом для всех.

---

## Шаг 3 — egress allowlist (proxy)

Форсит весь outbound контейнера через allowlisting-прокси. Код уже инжектит
`HTTP(S)_PROXY` + `NO_PROXY`, когда задан `CONTAINER_EGRESS_PROXY`.

### 3.1 Поднять прокси на runtime-сети
tinyproxy с whitelist (только npm/pip-registry + внутренние). Пример:
```bash
mkdir -p /opt/omnia-runtime/egress
cat > /opt/omnia-runtime/egress/tinyproxy.conf <<'CONF'
Port 3128
Listen 0.0.0.0
Timeout 600
# Allowlist: только нужные апстримы для install + наши сервисы
Filter "/etc/tinyproxy/filter"
FilterDefaultDeny Yes
FilterExtended On
CONF
cat > /opt/omnia-runtime/egress/filter <<'CONF'
^registry\.npmjs\.org$
\.npmjs\.org$
^registry\.yarnpkg\.com$
^pypi\.org$
^files\.pythonhosted\.org$
\.docker\.io$
CONF
docker run -d --name omnia-egress --restart unless-stopped \
  --network omnia-runtime_default \
  -v /opt/omnia-runtime/egress/tinyproxy.conf:/etc/tinyproxy/tinyproxy.conf:ro \
  -v /opt/omnia-runtime/egress/filter:/etc/tinyproxy/filter:ro \
  vimagick/tinyproxy
```
(Альтернатива — squid с более тонким ACL. Whitelist выверить под реальные
апстримы билда: смотри `docker logs omnia-egress` на DENY и добавляй нужное.)

### 3.2 Включить в оркестраторе
```bash
CONTAINER_EGRESS_PROXY=http://omnia-egress:3128
# NO_PROXY по умолчанию уже включает localhost/host.docker.internal/
# omnia-postgres-users/omnia-prod-gw/omnia-prod-minio — переопредели если имена иные:
# CONTAINER_EGRESS_NO_PROXY=localhost,127.0.0.1,host.docker.internal,omnia-postgres-users,omnia-prod-gw,omnia-prod-minio
sudo systemctl restart omnia-orchestrator.service
```
Проверка:
```bash
docker exec omnia-dev-<slug> sh -c 'curl -s -o /dev/null -w "%{http_code}" https://example.com'   # ждём: блок (403/000)
docker exec omnia-dev-<slug> sh -c 'curl -s -o /dev/null -w "%{http_code}" https://registry.npmjs.org'  # ждём: 200
docker exec omnia-dev-<slug> sh -c 'node -e "require(\"pg\")"'   # DB-reach всё ещё ок (NO_PROXY)
```
Откат: убрать `CONTAINER_EGRESS_PROXY`, restart orchestrator.

---

## Шаг 4 — per-project network (последним, самое деликатное)

Каждый контейнер на своей `omnia-proj-<id>` сети → нет латерального доступа к
чужим проектам. ⚠️ На своей сети контейнер ТЕРЯЕТ доступ к shared-сервисам по
имени (`omnia-prod-gw`, `omnia-prod-minio`, `omnia-postgres-users`). Поэтому
включать ТОЛЬКО после Шага 3, и обеспечить досягаемость внутренних сервисов:
- DB — DSN уже может идти через `host.docker.internal` (`extra_hosts` ставится),
  проверь `postgres_admin._user_facing_host`;
- gateway/minio — либо подключить эти контейнеры и к каждой `omnia-proj-*` сети,
  либо ходить через `host.docker.internal` + проброшенные порты.

```bash
ISOLATE_PROJECT_NETWORK=true
sudo systemctl restart omnia-orchestrator.service
```
Проверка (ОБЯЗАТЕЛЬНО на тестовом проекте до общего включения):
```bash
docker inspect omnia-dev-<slug> --format '{{json .NetworkSettings.Networks}}'   # ждём: omnia-proj-<id>
# из проекта A нельзя достучаться до контейнера проекта B:
docker exec omnia-dev-<A> sh -c 'ping -c1 -W1 omnia-dev-<B>'   # ждём: fail
# но DB/gateway/minio достижимы (иначе апп 500):
docker exec omnia-dev-<slug> sh -c 'node -e "require(\"pg\")" && echo db-ok'
```
Если апп теряет DB/gateway → откат: `ISOLATE_PROJECT_NETWORK` убрать, restart
orchestrator; вернуться к топологии shared-net + egress-proxy (Шаги 1–3 дают
основную защиту и без сетевой изоляции).

---

## Итоговая проверка Phase 1

```bash
docker inspect omnia-dev-<slug> --format \
 'runtime={{.HostConfig.Runtime}} secopt={{.HostConfig.SecurityOpt}} pids={{.HostConfig.PidsLimit}} net={{json .NetworkSettings.Networks}}'
# ждём: runtime=runsc secopt=[no-new-privileges:true] pids=512 net=omnia-proj-<id>
docker exec omnia-dev-<slug> sh -c 'curl -m5 -s -o /dev/null -w "%{http_code}" https://example.com'  # блок
```

Phase 1 закрыта, когда: runsc активен и тестовый билд (entities+realtime) под ним
живой; egress в произвольный хост заблокирован, в allowlist — открыт; (опц.)
проекты сетево изолированы, при этом DB/gateway/minio достижимы. После этого
безопасно широко включать `USE_AGENTIC_BUILDER` (агент гоняет произвольный bash).

## Полный откат Phase 1
```bash
# env оркестратора: убрать CONTAINER_HARDEN, OMNIA_CONTAINER_RUNTIME,
# CONTAINER_EGRESS_PROXY, ISOLATE_PROJECT_NETWORK
sudo systemctl restart omnia-orchestrator.service     # код снова инертен
docker rm -f omnia-egress                             # снять прокси
sudo cp /etc/docker/daemon.json.bak /etc/docker/daemon.json && sudo systemctl restart docker  # снять gVisor (окно!)
```
