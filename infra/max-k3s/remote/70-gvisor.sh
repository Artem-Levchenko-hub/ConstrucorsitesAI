#!/usr/bin/env bash
# 70-gvisor.sh — gVisor (runsc) как runtime containerd на узле K3s + RuntimeClass `gvisor`.
#
# Зачем: опубликованные приложения пользователей — чужой код. gVisor ставит между ним и ядром
# хоста свой user-space kernel, так что уязвимость в приложении не становится уязвимостью узла.
# Оркестратор включает его через `K8S_APP_RUNTIME_CLASS=gvisor` (только контейнеры приложения,
# boundary и управляемого core; Postgres и Redis остаются на runc).
#
# Идемпотентен. Запуск на узле (runtime): bash 70-gvisor.sh [release-tag]
# Дистрибуция gVisor с 2026 года — только GitHub Releases (тарболы + SHA512SUMS);
# бакет storage.googleapis.com/gvisor отдаёт NoSuchKey.
set -euo pipefail
TAG="${1:-release-20260914.0}"
ARCH="$(uname -m)"
BASE="https://github.com/google/gvisor/releases/download/${TAG}"
PLATFORM="${GVISOR_PLATFORM:-systrap}" # kvm быстрее, но требует /dev/kvm и проверенной вложенной виртуализации
DROPIN_DIR=/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.d

log() { echo "[gvisor] $*"; }

if [ -x /usr/local/bin/runsc ] && [ -x /usr/local/bin/gvisor-bin/gvisor_sentry ] \
   && /usr/local/bin/runsc --version 2>/dev/null | grep -q "${TAG#release-}"; then
  log "runsc ${TAG} уже установлен"
else
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  cd "$work"
  # Узлы Ubuntu 24.04 без bzip2: берём zstd-тарбол (zstd есть в базовой системе).
  log "скачиваю gvisor-${ARCH}.tar.zstd (${TAG})..."
  curl -fsSLO "${BASE}/gvisor-${ARCH}.tar.zstd"
  curl -fsSLO "${BASE}/SHA512SUMS"
  grep " gvisor-${ARCH}.tar.zstd\$" SHA512SUMS | sha512sum -c - >/dev/null
  zstd -dc "gvisor-${ARCH}.tar.zstd" | tar -x
  # С 2026 года runsc — «тонкий» бинарь: ядро gVisor лежит рядом в gvisor-bin/
  # (gvisor_sentry и др.), и runsc ищет его относительно своего пути
  # (--sidecar-usage-policy=STRICT). Ставим весь layout тарбола целиком.
  root="$(dirname "$(find . -type f -name runsc | head -1)")"
  [ -f "$root/runsc" ] && [ -f "$root/containerd-shim-runsc-v1" ] || { log "в тарболе нет runsc/shim"; exit 1; }
  ( cd "$root" && find . -type f | sed 's|^\./||' ) | while read -r rel; do
    sudo install -D -m 0755 "$root/$rel" "/usr/local/bin/$rel"
  done
  cd /
fi
log "runsc: $(/usr/local/bin/runsc --version | head -1)"

sudo mkdir -p /etc/containerd "$DROPIN_DIR"
sudo tee /etc/containerd/runsc.toml >/dev/null <<EOF
[runsc_config]
  platform = "${PLATFORM}"
EOF
# K3s пересобирает config.toml при каждом старте, но подключает drop-in'ы из config-v3.toml.d.
sudo tee "${DROPIN_DIR}/runsc.toml" >/dev/null <<'EOF'
[plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.runsc]
  runtime_type = "io.containerd.runsc.v1"

[plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.runsc.options]
  TypeUrl = "io.containerd.runsc.v1.options"
  ConfigPath = "/etc/containerd/runsc.toml"
EOF

log "перезапуск k3s, чтобы containerd увидел runtime runsc..."
sudo systemctl restart k3s
for _ in $(seq 1 45); do
  sudo k3s kubectl get nodes >/dev/null 2>&1 && break
  sleep 2
done
sudo k3s kubectl get nodes >/dev/null

cat <<'EOF' | sudo k3s kubectl apply -f - >/dev/null
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
handler: runsc
EOF
log "RuntimeClass gvisor: $(sudo k3s kubectl get runtimeclass gvisor -o jsonpath='{.handler}')"

log "проверка: под с runtimeClassName=gvisor должен видеть ядро gVisor в dmesg"
sudo k3s kubectl delete pod gvisor-probe --ignore-not-found >/dev/null 2>&1 || true
sudo k3s kubectl run gvisor-probe --image=busybox:1.36 --restart=Never \
  --overrides='{"spec":{"runtimeClassName":"gvisor"}}' -- sh -c 'dmesg | head -2; uname -r' >/dev/null
for _ in $(seq 1 60); do
  phase="$(sudo k3s kubectl get pod gvisor-probe -o jsonpath='{.status.phase}' 2>/dev/null || true)"
  case "$phase" in Succeeded|Failed) break;; esac
  sleep 2
done
sudo k3s kubectl logs gvisor-probe || true
sudo k3s kubectl delete pod gvisor-probe >/dev/null 2>&1 || true
[ "$phase" = "Succeeded" ] || { log "проба не завершилась успешно (phase=${phase})"; exit 1; }
log "готово: gVisor доступен как RuntimeClass gvisor (platform=${PLATFORM})"
