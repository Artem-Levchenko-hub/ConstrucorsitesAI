#!/usr/bin/env bash
# Драйвер провижининга трёх VPS Serverum под MAX Studio (запускать с Mac, bash 3.2 ok).
#
#   ./provision.sh bootstrap        # ОС, ssh-hardening, ufw, fail2ban, sysctl (параллельно на всех)
#   ./provision.sh wireguard        # mesh 10.10.0.0/24 + node-exporter на WG-адресе
#   ./provision.sh k3s              # одно-нодовый K3s на каждой VPS
#   ./provision.sh kubeconfigs      # ~/.kube/max-studio.yaml с контекстами max-core/max-runtime/max-commerce
#   ./provision.sh postgres         # хостовый Postgres 16 + ночной бэкап
#   ./provision.sh backup-sync      # перекрёстные копии бэкапов между VPS по WireGuard
#   ./provision.sh k8s              # cert-manager на всех, registry на runtime, мониторинг на core
#   ./provision.sh all              # всё по порядку
#   ./provision.sh status           # сводка по всем хостам
set -euo pipefail
. "$(dirname "$0")/lib.sh"
LOGDIR=${LOGDIR:-/tmp/max-k3s-logs}; install -d "$LOGDIR"
R="$HERE/remote"

phase_bootstrap() {
  local pub; pub=$(cat "$ADMIN_PUBKEY_FILE")
  log "bootstrap: параллельно на $HOSTS (логи в $LOGDIR)"
  for h in $HOSTS; do
    ( peer_lans=""; for p in $(peers "$h"); do peer_lans="$peer_lans $(inv "$p" lan)"; done
      WG_PORT=$WG_PORT run_remote "$h" "$R/00-bootstrap.sh" "$h" "$DOMAIN" "$(inv "$h" wg)" "$WG_NET" "$LAN_NET" \
        "$(inv "$h" cluster_cidr)" "$(inv "$h" service_cidr)" "$ADMIN_USER" "$pub" "$peer_lans" \
        > "$LOGDIR/bootstrap-$h.log" 2>&1 && echo "bootstrap $h: ok" || echo "bootstrap $h: FAILED (см. $LOGDIR/bootstrap-$h.log)" ) &
  done
  wait
  for h in $HOSTS; do
    tail -1 "$LOGDIR/bootstrap-$h.log"
    if grep -q REBOOT_REQUIRED "$LOGDIR/bootstrap-$h.log"; then sleep 5; wait_ssh "$h" && echo "reboot $h: снова в строю"; fi
  done
}

phase_wireguard() {
  log "wireguard: ключи"
  local pubs="" h p
  for h in $HOSTS; do
    k=$(run_remote "$h" "$R/10-wireguard.sh" keygen | tail -1)
    pubs="$pubs $h:$k"
  done
  log "wireguard: конфиги с пирами"
  for h in $HOSTS; do
    peerspec=""
    for p in $(peers "$h"); do
      k=$(echo "$pubs" | tr ' ' '\n' | awk -F: -v n="$p" '$1==n {print $2}')
      peerspec="$peerspec $p:$k:$(inv "$p" lan):$(inv "$p" wg)"
    done
    run_remote "$h" "$R/10-wireguard.sh" configure "$h" "$(inv "$h" wg)" "$WG_PORT" enp2s0 "$peerspec"
  done
}

phase_k3s() {
  for h in $HOSTS; do
    log "k3s: $h"
    run_remote "$h" "$R/20-k3s.sh" "$h" "$(inv "$h" public)" "$(inv "$h" lan)" "$(inv "$h" wg)" \
      "$(inv "$h" cluster_cidr)" "$(inv "$h" service_cidr)" "$(inv "$h" cluster_dns)" "$DOMAIN" "$K3S_VERSION" "$ADMIN_USER"
  done
}

phase_kubeconfigs() {
  log "kubeconfigs → ~/.kube/max-studio.yaml (server = 127.0.0.1:<порт туннеля>, см. kube-tunnel.sh)"
  install -d -m 700 "$HOME/.kube"
  local out="$HOME/.kube/max-studio.yaml" tmp; tmp=$(mktemp -d)
  for h in $HOSTS; do
    ssh -o BatchMode=yes "$(inv "$h" alias)" cat /etc/rancher/k3s/k3s.yaml \
      | sed -e "s#https://127.0.0.1:6443#https://127.0.0.1:$(inv "$h" tunnel_port)#" \
            -e "s#: default#: max-$h#g" -e "s#name: default#name: max-$h#g" > "$tmp/$h.yaml"
  done
  KUBECONFIG=$(ls "$tmp"/*.yaml | tr '\n' ':') kubectl config view --flatten > "$out"
  chmod 600 "$out"; rm -rf "$tmp"
  KUBECONFIG=$out kubectl config get-contexts
  echo "export KUBECONFIG=$out"
}

phase_postgres() {
  for h in $HOSTS; do
    log "postgres: $h"
    run_remote "$h" "$R/40-postgres.sh" "$h" "$(inv "$h" wg)" "$WG_NET" "$(inv "$h" cluster_cidr)" "$(pg_db "$h")" "$(pg_user "$h")"
  done
}

phase_backup_sync() {
  log "backup-sync: перекрёстные копии бэкапов (core→commerce, runtime→core, commerce→core)"
  local pubdir h target; pubdir=$(mktemp -d)
  for h in $HOSTS; do run_remote "$h" "$R/50-backup-sync.sh" keygen | tail -1 > "$pubdir/$h"; done
  for h in $HOSTS; do
    target=$(inv "$h" backup_peer)
    run_remote "$target" "$R/50-backup-sync.sh" authorize "$h" "$(cat "$pubdir/$h")"
    run_remote "$h" "$R/50-backup-sync.sh" schedule "$target" "$(inv "$target" wg)"
  done
  rm -rf "$pubdir"
}

phase_k8s() { "$HERE/k8s/apply.sh" all; }

phase_status() {
  for h in $HOSTS; do
    log "status: $h ($(inv "$h" public))"
    ssh -o BatchMode=yes "$(inv "$h" alias)" 'echo "  uptime: $(uptime -p) | load $(cut -d" " -f1-3 /proc/loadavg) | mem $(free -g | awk "NR==2{print \$3\"/\"\$2\"G\"}") | disk $(df -h / | awk "NR==2{print \$3\"/\"\$2}")";
      echo "  ufw: $(sudo ufw status | head -1) | fail2ban: $(sudo fail2ban-client status sshd 2>/dev/null | grep "Currently banned" | awk "{print \$NF\" banned\"}")";
      echo "  wg: $(sudo wg show wg0 2>/dev/null | grep -c "latest handshake") peers with handshake";
      echo "  k3s: $(sudo k3s kubectl get node --no-headers 2>/dev/null | awk "{print \$2\" \"\$5}") | pods not running: $(sudo k3s kubectl get pods -A --no-headers 2>/dev/null | grep -vcE "Running|Completed")";
      echo "  postgres: $(pg_isready -h 127.0.0.1 2>/dev/null | cut -d- -f2-) | last backup: $(sudo ls -1 /var/backups/max-studio 2>/dev/null | tail -1) | peer copies: $(sudo find /var/backups/max-studio-peer -mindepth 2 -maxdepth 2 -type d 2>/dev/null | wc -l | tr -d " ")"'
  done
}

case "${1:-}" in
  bootstrap|wireguard|k3s|kubeconfigs|postgres|k8s|status) "phase_$1" ;;
  backup-sync) phase_backup_sync ;;
  all) phase_bootstrap; phase_wireguard; phase_k3s; phase_kubeconfigs; phase_postgres; phase_backup_sync; phase_k8s; phase_status ;;
  *) sed -n 2,12p "$0"; exit 1 ;;
esac
