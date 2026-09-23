#!/usr/bin/env bash
# Фаза 1 (инфраструктура целевой схемы): изолированный сборочный воркер — rootless BuildKit на хосте
# ячеек (core / commerce).
#
# Зачем. Production-образ приложения пользователя собирается из НЕДОВЕРЕННОГО Dockerfile и контекста
# (их пишет агент). Сегодня сборку делает root-демон Docker хоста: каждый RUN-шаг исполняется настоящим
# root'ом за одной границей ядра. Здесь поднимается buildkitd без root'а и без доступа к docker.sock:
#   - контейнер `omnia-buildkitd` из образа moby/buildkit:rootless (внутри uid 1000, capabilities
#     сброшены до SETUID/SETGID — их требует newuidmap для вложенного user namespace);
#   - RUN-шаги идут во вложенном user namespace, каждый — в своём PID namespace (режим «process
#     sandbox»; если ядро/Docker его не дают, скрипт сам откатывается на режим без него);
#   - ресурсы — cgroup-лимиты (CPU / память / число процессов), кэш слоёв — отдельный том;
#   - своя bridge-сеть, из которой хост закрыт ufw (шаги сборки не достают до оркестратора и превью);
#   - оркестратор ходит к демону как обычный пользователь через unix-сокет
#     /run/omnia-buildkit/buildkitd.sock (доступ группе omnia-buildkit через ACL каталога), клиентом
#     buildctl того же выпуска, вынутым из образа в /usr/local/bin.
#
# Запускается от root: ssh <host> sudo bash -s -- ADMIN_USER [BUILDKIT_IMAGE] < этот файл
# Переменные окружения (необязательно):
#   BUILDKIT_CPUS=3 BUILDKIT_MEMORY=6g BUILDKIT_PIDS=4096 BUILDKIT_KEEP_STORAGE_MB=20000
#   BUILDKIT_PROCESS_SANDBOX=auto|1|0   BUILDKIT_CAPS=auto|minimal|default
#   BUILDKIT_APPARMOR=profile|unconfined   BUILDKIT_SMOKE=1|0
# Идемпотентен: повторный прогон с теми же параметрами ничего не пересоздаёт; с другими — пересоздаёт
# контейнер. Оркестратор он НЕ переключает: BUILD_BACKEND в .env остаётся docker, включение — отдельный
# шаг оператора (docs/09-max-k3s-infra.md, «Изолированные сборки»).
set -euo pipefail
ADMIN_USER=$1
BUILDKIT_IMAGE=${2:-${BUILDKIT_IMAGE:-moby/buildkit:rootless}}
BUILDKIT_CPUS=${BUILDKIT_CPUS:-3}
BUILDKIT_MEMORY=${BUILDKIT_MEMORY:-6g}
BUILDKIT_PIDS=${BUILDKIT_PIDS:-4096}
BUILDKIT_KEEP_STORAGE_MB=${BUILDKIT_KEEP_STORAGE_MB:-20000}
BUILDKIT_PROCESS_SANDBOX=${BUILDKIT_PROCESS_SANDBOX:-auto}
BUILDKIT_CAPS=${BUILDKIT_CAPS:-auto}
BUILDKIT_APPARMOR=${BUILDKIT_APPARMOR:-profile}
BUILDKIT_SMOKE=${BUILDKIT_SMOKE:-1}
export DEBIAN_FRONTEND=noninteractive

NAME=omnia-buildkitd
GROUP=omnia-buildkit
NET=omnia-buildkit
VOLUME=omnia-buildkit-cache
RUN_DIR=/run/omnia-buildkit
SOCK=$RUN_DIR/buildkitd.sock
CONF_DIR=/opt/omnia-runtime/buildkit
MODE_FILE=$CONF_DIR/mode.env
BUILDCTL=/usr/local/bin/buildctl
ORCH_ENV=/opt/omnia/apps/orchestrator/.env
# uid пользователя `user` внутри образа: subuid/subgid в образе заведены только для него, поэтому
# контейнер запускается именно под ним, а не под uid оператора.
BUILDKIT_UID=1000
[ -n "$ADMIN_USER" ] && id "$ADMIN_USER" >/dev/null 2>&1 || { echo "нет пользователя оркестратора: '$ADMIN_USER'"; exit 1; }

echo "== пакеты (acl — для ACL каталога сокета)"
command -v setfacl >/dev/null || apt-get install -y -q --no-install-recommends acl >/dev/null

echo "== группа $GROUP и членство $ADMIN_USER"
getent group "$GROUP" >/dev/null || groupadd --system "$GROUP"
GID=$(getent group "$GROUP" | cut -d: -f3)
if id -nG "$ADMIN_USER" | tr ' ' '\n' | grep -qx "$GROUP"; then
  echo "  $ADMIN_USER уже в группе $GROUP (gid $GID)"
else
  usermod -aG "$GROUP" "$ADMIN_USER"
  echo "  $ADMIN_USER добавлен в $GROUP (gid $GID) — работающий omnia-orchestrator увидит группу после restart"
fi

echo "== каталог сокета $RUN_DIR (tmpfiles: переживает reboot)"
cat > /etc/tmpfiles.d/omnia-buildkit.conf <<EOF
# Каталог unix-сокета rootless BuildKit (omnia-buildkitd). Владелец — uid $BUILDKIT_UID (пользователь
# \`user\` образа): сам сокет создаёт buildkitd. Доступ оркестратору — через группу $GROUP по ACL:
# default-ACL наследуется сокетом при каждом старте демона, поэтому chown/chmod вручную не нужны.
d $RUN_DIR 0770 $BUILDKIT_UID root -
a+ $RUN_DIR - - - - group:$GROUP:rwx
a+ $RUN_DIR - - - - d:group:$GROUP:rw-
EOF
systemd-tmpfiles --create /etc/tmpfiles.d/omnia-buildkit.conf
setfacl -m "g:$GROUP:rwx" -m "d:g:$GROUP:rw-" "$RUN_DIR"
getfacl -p "$RUN_DIR" 2>/dev/null | sed 's/^/  /'

echo "== конфигурация buildkitd ($CONF_DIR)"
install -d -m 0755 "$CONF_DIR"
cat > "$CONF_DIR/buildkitd.toml" <<EOF
# Конфигурация rootless buildkitd (монтируется в контейнер omnia-buildkitd только на чтение).
# Кэш слоёв живёт в томе $VOLUME; демон сам чистит старое, когда занято больше gckeepstorage (МБ).
[worker.oci]
  enabled = true
  gc = true
  gckeepstorage = $BUILDKIT_KEEP_STORAGE_MB
[worker.containerd]
  enabled = false
EOF
chmod 0644 "$CONF_DIR/buildkitd.toml"

echo "== AppArmor"
# Ubuntu 24.04 (kernel.apparmor_restrict_unprivileged_userns=1) запрещает создавать user namespace
# процессам БЕЗ профиля, а rootlesskit внутри контейнера без него не стартует. docker-default не годится
# (запрещает mount — без него rootless-сборка не работает), поэтому свой профиль: без ограничений, как
# unconfined, но с правом userns. Если профиль не загружается (старый AppArmor) — sysctl-fallback.
AA_OPT="--security-opt apparmor=unconfined"
AA_MODE=unconfined
if [ "$BUILDKIT_APPARMOR" = "profile" ] && [ -d /sys/kernel/security/apparmor ] && command -v apparmor_parser >/dev/null; then
  cat > /etc/apparmor.d/omnia-buildkit <<'EOF'
# Профиль контейнера omnia-buildkitd (rootless BuildKit): без ограничений, но с правом создавать
# user namespace — см. infra/max-k3s/cells/50-buildkit-rootless.sh.
abi <abi/4.0>,
include <tunables/global>

profile omnia-buildkit flags=(unconfined) {
  userns,
}
EOF
  if apparmor_parser -r -W /etc/apparmor.d/omnia-buildkit 2>/tmp/omnia-buildkit-aa.err; then
    AA_OPT="--security-opt apparmor=omnia-buildkit"; AA_MODE=profile
    echo "  профиль omnia-buildkit загружен"
  else
    echo "  WARNING: профиль не загрузился ($(tr '\n' ' ' </tmp/omnia-buildkit-aa.err)) — fallback на sysctl"
    rm -f /etc/apparmor.d/omnia-buildkit
  fi
fi
if [ "$AA_MODE" = "unconfined" ] && [ -f /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]; then
  echo 'kernel.apparmor_restrict_unprivileged_userns = 0' > /etc/sysctl.d/70-omnia-buildkit.conf
  sysctl -q -p /etc/sysctl.d/70-omnia-buildkit.conf
  echo "  apparmor_restrict_unprivileged_userns=0 (иначе rootlesskit без профиля не создаст userns)"
fi

echo "== образ $BUILDKIT_IMAGE и клиент buildctl того же выпуска"
docker pull -q "$BUILDKIT_IMAGE" >/dev/null
IMAGE_ID=$(docker image inspect --format '{{.Id}}' "$BUILDKIT_IMAGE")
tmpc=$(docker create "$BUILDKIT_IMAGE")
docker cp "$tmpc:/usr/bin/buildctl" "$BUILDCTL.tmp" >/dev/null
docker rm -f "$tmpc" >/dev/null
install -m 0755 "$BUILDCTL.tmp" "$BUILDCTL" && rm -f "$BUILDCTL.tmp"
echo "  $($BUILDCTL --version)  image=${IMAGE_ID:0:19}"

echo "== сеть $NET (mtu 1400, как у демона) и ufw: хост закрыт для шагов сборки"
docker network inspect "$NET" >/dev/null 2>&1 \
  || docker network create --opt com.docker.network.driver.mtu=1400 "$NET" >/dev/null
SUBNET=$(docker network inspect -f '{{(index .IPAM.Config 0).Subnet}}' "$NET")
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q '^Status: active'; then
  if ! ufw status | grep -q 'buildkit → host: deny'; then
    ufw insert 1 deny from "$SUBNET" to any comment 'buildkit → host: deny (untrusted build steps)' >/dev/null
  fi
  echo "  $SUBNET → хост: deny"
fi
[ -e /dev/fuse ] || modprobe fuse 2>/dev/null || true
FUSE_OPT=""; [ -e /dev/fuse ] && FUSE_OPT="--device /dev/fuse"

# ---- запуск контейнера в заданном режиме -----------------------------------------------------------
start_buildkitd() { # $1 = process sandbox (1|0), $2 = caps (minimal|default)
  local sandbox=$1 caps=$2 sandbox_opt="" sandbox_flag="" caps_opt="" spec
  if [ "$sandbox" = "1" ]; then
    # /proc без масок — иначе вложенный PID namespace не смонтирует свой procfs
    sandbox_opt="--security-opt systempaths=unconfined"
  else
    sandbox_flag="--oci-worker-no-process-sandbox"
  fi
  [ "$caps" = "minimal" ] && caps_opt="--cap-drop ALL --cap-add SETUID --cap-add SETGID"
  spec=$(printf '%s' "v1|$IMAGE_ID|$AA_OPT|$sandbox_opt|$sandbox_flag|$caps_opt|$FUSE_OPT|$BUILDKIT_CPUS|$BUILDKIT_MEMORY|$BUILDKIT_PIDS|$(sha256sum "$CONF_DIR/buildkitd.toml" | cut -c1-16)" | sha256sum | cut -c1-16)
  local current running
  current=$(docker inspect -f '{{index .Config.Labels "omnia.buildkit.spec"}}' "$NAME" 2>/dev/null || true)
  running=$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null || echo false)
  if [ "$current" = "$spec" ] && [ "$running" = "true" ]; then
    echo "  контейнер $NAME актуален (spec $spec), не трогаем"
  else
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    rm -f "$SOCK"
    # shellcheck disable=SC2086
    docker run -d --name "$NAME" --restart unless-stopped \
      --network "$NET" \
      --security-opt seccomp=unconfined $AA_OPT $sandbox_opt $caps_opt $FUSE_OPT \
      --cpus "$BUILDKIT_CPUS" --memory "$BUILDKIT_MEMORY" --memory-swap "$BUILDKIT_MEMORY" --pids-limit "$BUILDKIT_PIDS" \
      --log-opt max-size=20m --log-opt max-file=3 \
      -v "$VOLUME:/home/user/.local/share/buildkit" \
      -v "$RUN_DIR:/run/user/$BUILDKIT_UID/buildkit" \
      -v "$CONF_DIR/buildkitd.toml:/etc/buildkit/buildkitd.toml:ro" \
      --label "omnia.buildkit.spec=$spec" \
      "$BUILDKIT_IMAGE" \
      --addr "unix:///run/user/$BUILDKIT_UID/buildkit/buildkitd.sock" \
      --config /etc/buildkit/buildkitd.toml $sandbox_flag >/dev/null
    echo "  контейнер $NAME запущен (sandbox=$sandbox caps=$caps spec $spec)"
  fi
  local i
  for i in $(seq 1 30); do [ -S "$SOCK" ] && break; sleep 1; done
  if [ ! -S "$SOCK" ]; then
    echo "  сокет $SOCK не появился за 30 с; последние строки лога:"
    docker logs --tail 30 "$NAME" 2>&1 | sed 's/^/    /'
    return 1
  fi
  if ! sudo -u "$ADMIN_USER" -H "$BUILDCTL" --addr "unix://$SOCK" debug workers >/tmp/omnia-buildkit-workers.txt 2>&1; then
    echo "  buildctl debug workers от $ADMIN_USER не прошёл:"; sed 's/^/    /' /tmp/omnia-buildkit-workers.txt
    return 1
  fi
  sed 's/^/  /' /tmp/omnia-buildkit-workers.txt
}

smoke_build() {
  [ "$BUILDKIT_SMOKE" = "1" ] || { echo "  smoke пропущен (BUILDKIT_SMOKE=$BUILDKIT_SMOKE)"; return 0; }
  local dir status=0
  dir=$(mktemp -d /tmp/omnia-buildkit-smoke.XXXXXX)
  cat > "$dir/Dockerfile" <<'EOF'
FROM busybox:stable
RUN echo "smoke uid=$(id -u) pid=$$" > /smoke && cat /smoke
EOF
  install -d -m 0700 "$dir/docker"
  chown -R "$ADMIN_USER" "$dir"
  if sudo -u "$ADMIN_USER" -H env DOCKER_CONFIG="$dir/docker" "$BUILDCTL" --addr "unix://$SOCK" build \
      --frontend dockerfile.v0 --local context="$dir" --local dockerfile="$dir" --progress plain \
      --output type=docker,name=omnia-buildkit-smoke:test 2>"$dir/log" | docker load >"$dir/load" 2>&1; then
    echo "  smoke-сборка прошла: $(grep -E 'smoke uid=' "$dir/log" | tail -1 | sed 's/^.*smoke/smoke/') · $(cat "$dir/load")"
    docker image rm -f omnia-buildkit-smoke:test >/dev/null 2>&1 || true
  else
    status=1
    echo "  smoke-сборка НЕ прошла; хвост лога:"; tail -15 "$dir/log" | sed 's/^/    /'
  fi
  rm -rf "$dir"
  return $status
}

# Порядок попыток: самый изолированный режим первым; выбранный сохраняется в mode.env и на повторных
# прогонах пробуется первым (без сюрпризов при переустановке контейнера).
candidates=()
if [ -f "$MODE_FILE" ]; then
  # shellcheck disable=SC1090
  . "$MODE_FILE"
  candidates+=("${MODE_SANDBOX:-1}:${MODE_CAPS:-minimal}")
fi
for s in 1 0; do
  for c in minimal default; do
    { [ "$BUILDKIT_PROCESS_SANDBOX" = auto ] || [ "$BUILDKIT_PROCESS_SANDBOX" = "$s" ]; } || continue
    { [ "$BUILDKIT_CAPS" = auto ] || [ "$BUILDKIT_CAPS" = "$c" ]; } || continue
    printf '%s\n' "${candidates[@]:-}" | grep -qx "$s:$c" || candidates+=("$s:$c")
  done
done
[ "${#candidates[@]}" -gt 0 ] || { echo "нет допустимых режимов: BUILDKIT_PROCESS_SANDBOX=$BUILDKIT_PROCESS_SANDBOX BUILDKIT_CAPS=$BUILDKIT_CAPS"; exit 1; }
CHOSEN=""
for cand in "${candidates[@]}"; do
  s=${cand%%:*}; c=${cand##*:}
  echo "== buildkitd: process sandbox=$s, caps=$c"
  if start_buildkitd "$s" "$c" && smoke_build; then CHOSEN="$cand"; break; fi
  echo "  режим $cand не заработал, пробуем следующий"
done
[ -n "$CHOSEN" ] || { echo "rootless BuildKit не удалось запустить ни в одном режиме"; docker logs --tail 50 "$NAME" 2>&1 | sed 's/^/    /' || true; exit 1; }
s=${CHOSEN%%:*}; c=${CHOSEN##*:}
printf 'MODE_SANDBOX=%s\nMODE_CAPS=%s\nMODE_APPARMOR=%s\n' "$s" "$c" "$AA_MODE" > "$MODE_FILE"
[ "$s" = "1" ] || echo "  NOTE: работает режим без PID-sandbox (--oci-worker-no-process-sandbox): шаги сборки делят PID namespace с buildkitd"
[ "$c" = "minimal" ] || echo "  NOTE: контейнер запущен с capabilities Docker по умолчанию (минимальный набор не заработал)"

echo "== что получилось"
docker inspect -f '  security: {{.HostConfig.SecurityOpt}} capdrop={{.HostConfig.CapDrop}} capadd={{.HostConfig.CapAdd}}
  limits: cpus={{.HostConfig.NanoCpus}}n mem={{.HostConfig.Memory}} pids={{.HostConfig.PidsLimit}} restart={{.HostConfig.RestartPolicy.Name}}
  mounts: {{range .Mounts}}{{.Source}}→{{.Destination}} {{end}}' "$NAME"
ls -la "$SOCK" | sed 's/^/  /'
docker top "$NAME" -eo user,pid,comm 2>/dev/null | head -4 | sed 's/^/  /'

if [ -f "$ORCH_ENV" ]; then
  echo "== $ORCH_ENV: путь к сокету и клиенту (BUILD_BACKEND не трогаем — включение отдельно)"
  grep -q '^BUILDKIT_SOCKET=' "$ORCH_ENV" || printf 'BUILDKIT_SOCKET=%s\n' "$SOCK" >> "$ORCH_ENV"
  grep -q '^BUILDCTL_BINARY=' "$ORCH_ENV" || printf 'BUILDCTL_BINARY=%s\n' "$BUILDCTL" >> "$ORCH_ENV"
  grep -q '^BUILD_BACKEND=' "$ORCH_ENV" || printf 'BUILD_BACKEND=docker\n' >> "$ORCH_ENV"
  grep -E '^(BUILD_BACKEND|BUILDKIT_SOCKET|BUILDCTL_BINARY)=' "$ORCH_ENV" | sed 's/^/  /'
fi
echo "BUILDKIT_ROOTLESS_DONE $(hostname) socket=$SOCK sandbox=$s caps=$c apparmor=$AA_MODE image=$BUILDKIT_IMAGE"
echo "  включить сборки через него: BUILD_BACKEND=buildkit в $ORCH_ENV + systemctl restart omnia-orchestrator (когда нет активных деплоев)"
echo "  откат: BUILD_BACKEND=docker + restart; контейнер можно оставить или docker rm -f $NAME"
