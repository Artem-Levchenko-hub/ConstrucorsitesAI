# shellcheck shell=bash
# Общие функции драйвера. Совместимо с bash 3.2 (macOS): без ассоциативных массивов.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=inventory.env
. "$HERE/inventory.env"

# inv <name> <field>  — поле инвентаря: alias public lan wg cluster_cidr service_cidr cluster_dns backup_peer tunnel_port
inv() {
  local col
  case "$2" in
    alias) col=2 ;; public) col=3 ;; lan) col=4 ;; wg) col=5 ;; cluster_cidr) col=6 ;;
    service_cidr) col=7 ;; cluster_dns) col=8 ;; backup_peer) col=9 ;; tunnel_port) col=10 ;;
    *) echo "inv: unknown field $2" >&2; return 1 ;;
  esac
  echo "$INVENTORY" | awk -v n="$1" -v c="$col" '$1==n {print $c}'
}

# peers <name> — остальные хосты
peers() { local h; for h in $HOSTS; do [ "$h" = "$1" ] || printf '%s ' "$h"; done; }

# run_remote <name> <local script> [args...] — выполняет скрипт на хосте от root (stdin → sudo bash -s)
run_remote() {
  local name=$1 script=$2; shift 2
  ssh -o BatchMode=yes "$(inv "$name" alias)" "sudo bash -s -- $(printf '%q ' "$@")" < "$script"
}

# sh_remote <name> <command> — короткая команда от root
sh_remote() { local name=$1; shift; ssh -o BatchMode=yes "$(inv "$name" alias)" "sudo bash -c $(printf '%q' "$*")"; }

# wait_ssh <name> [timeout s] — ждём, пока хост снова отвечает по ssh (после reboot)
wait_ssh() {
  local name=$1 t=${2:-300} i=0
  until ssh -o BatchMode=yes -o ConnectTimeout=5 "$(inv "$name" alias)" true 2>/dev/null; do
    i=$((i+5)); [ "$i" -ge "$t" ] && { echo "wait_ssh: $name не поднялся за ${t}s" >&2; return 1; }; sleep 5
  done
}

log() { printf '\n\033[1;34m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
