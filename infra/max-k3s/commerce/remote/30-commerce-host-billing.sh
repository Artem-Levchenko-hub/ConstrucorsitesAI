#!/usr/bin/env bash
# Фаза 3 / commerce, шаг «хост»: выполняется НА COMMERCE от root.
#   ssh max-commerce sudo bash -s -- POD_CIDR < этот файл
#
# Поды биллингового воркера ходят в свой же оркестратор (10.10.0.3:8003 — снятие keep-alive
# у ячеек на этом хосте) с адресами pod-сети: для локального узла flannel их не маскирует,
# поэтому ufw должен пускать pod-сеть на 8003 (cells/10-cell-host-prep.sh открывал только
# docker/ячейки/WG/runtime). Всё остальное (Postgres/Redis платформы) — на core, через WG.
set -euo pipefail
POD_CIDR=${1:-10.46.0.0/16}
ufw allow from "$POD_CIDR" to any port 8003 proto tcp comment 'commerce pods (billing worker) → orchestrator' >/dev/null
ufw reload >/dev/null
ufw status | grep -F "$POD_CIDR" | sed 's/^/  /'
k3s kubectl get node --no-headers | sed 's/^/  k3s: /'
echo "COMMERCE_HOST_BILLING_DONE pods=$POD_CIDR"
