#!/usr/bin/env bash
# kubectl с Mac без публичного 6443: SSH-туннели к API каждого кластера.
#   ./kube-tunnel.sh up      # поднять туннели (core→16443, runtime→16444, commerce→16445)
#   ./kube-tunnel.sh down
#   ./kube-tunnel.sh status
# Потом: export KUBECONFIG=~/.kube/max-studio.yaml; kubectl --context max-core get nodes
set -euo pipefail
. "$(dirname "$0")/lib.sh"
CTL=/tmp/max-k3s-tunnel

case "${1:-status}" in
  up)
    for h in $HOSTS; do
      port=$(inv "$h" tunnel_port)
      if ssh -O check -S "$CTL-$h" "$(inv "$h" alias)" 2>/dev/null; then echo "$h: туннель уже поднят (:$port)"; continue; fi
      ssh -o BatchMode=yes -fN -M -S "$CTL-$h" -L "$port:127.0.0.1:6443" "$(inv "$h" alias)" && echo "$h: 127.0.0.1:$port → k3s api"
    done ;;
  down)
    for h in $HOSTS; do ssh -O exit -S "$CTL-$h" "$(inv "$h" alias)" 2>/dev/null && echo "$h: закрыт" || true; done ;;
  status)
    for h in $HOSTS; do
      if ssh -O check -S "$CTL-$h" "$(inv "$h" alias)" 2>/dev/null; then echo "$h: up (:$(inv "$h" tunnel_port))"; else echo "$h: down"; fi
    done ;;
  *) sed -n 2,6p "$0"; exit 1 ;;
esac
