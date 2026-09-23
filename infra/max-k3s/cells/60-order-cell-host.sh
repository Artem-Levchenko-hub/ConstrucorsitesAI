#!/usr/bin/env bash
# «Нужен ещё хост ячеек»: заказать VPS у Serverum и превратить его в хост ячеек агента (Docker-стек,
# как на core/commerce), затем добавить в реестр ORCHESTRATOR_HOSTS платформы. Запускать с Mac (bash 3.2 ok).
#
#   ./60-order-cell-host.sh --dry-run --name cells3                       # весь план без сети и без ssh
#   ./60-order-cell-host.sh --name cells3 [--plan vps-8-16-160] [--suffix dev3] [--from-step 3]
#   ./60-order-cell-host.sh --name cells3 --enable                        # после DNS *.dev3.<домен> → хост: enabled=true
#
# Шаги (каждый идемпотентен; --from-step N продолжает с шага N, тогда IP берутся из inventory.env):
#    1 order        Serverum: заказ VPS + ожидание IP (apps/orchestrator: python -m omnia_orchestrator.serverum_cli)
#    2 inventory    inventory.env: строка хоста (WG 10.10.0.N, pod/svc CIDR, порт туннеля); ~/.ssh/config: Host max-<name>
#    3 bootstrap    remote/00-bootstrap.sh: ключ в /etc/ssh/authorized_keys.d, ufw, fail2ban, sysctl (панель Serverum
#                   обнуляет ~/.ssh/authorized_keys каждые 5 минут — шаг запускается сразу после появления IP)
#    4 wireguard    provision.sh wireguard — все хосты: соседи узнают нового пира
#    5 postgres     remote/40-postgres.sh на новом хосте (legacy-пути оркестратора)
#    6 cell-prep    cells/10-cell-host-prep.sh — без K3s: Docker (mtu 1400), nginx catch-all, acme.sh, uv, ufw
#    7 code         rsync /opt/omnia с core по WireGuard, .env оркестратора core → .env.core, runtime-kubeconfig
#    8 orchestrator cells/20-cell-host-orchestrator.sh — .env под хост, образы из реестра, venv, systemd
#    9 core-access  core: ufw 6379 ← WG-адрес нового хоста (redis-mesh-forward), Host <name> в ssh-config core
#   10 backup       cells/30-cell-host-backup.sh — ночная копия ячеек в max-backup
#   11 register     ORCHESTRATOR_HOSTS в .env платформы (core): {name, url http://<wg>:8003, preview_host_suffix, enabled:false},
#                   пересоздать api/worker/generation-worker, проверить /api/health
#   Дальше владелец заводит DNS `*.<suffix>.<домен> → <публичный IP>` (reg.ru), и `--enable` включает хост в размещение.
#
# Допущения (проверить при первом живом прогоне): у Serverum нет публичной документации API — эндпоинты
# клиента помечены «уточнить» (services/serverum.py); панель создаёт того же пользователя ($ADMIN_USER)
# с sudo, что и на первых трёх VPS; приватная сеть 172.197.102.0/24 выдаётся новому VPS автоматически
# (иначе --lan-ip). Токен: SERVERUM_API_TOKEN в окружении или apps/orchestrator/.env.
set -euo pipefail
. "$(dirname "$0")/../lib.sh"
ORCH_DIR="$HERE/../../apps/orchestrator"
R="$HERE/remote"
C="$HERE/cells"
FULL=/opt/omnia/apps/llm-gateway/deploy/full
DRY_RUN=${DRY_RUN:-0}
NAME="" PLAN=${SERVERUM_PLAN:-vps-8-16-160} SUFFIX="" SSH_USER="$ADMIN_USER" ACME_EMAIL="admin@$DOMAIN"
FROM_STEP=1 ENABLE=0 LAN_IP_ARG="" PUBLIC_IP_ARG="" SERVER_ID_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --name) NAME=$2; shift ;;
    --plan) PLAN=$2; shift ;;
    --suffix) SUFFIX=$2; shift ;;
    --ssh-user) SSH_USER=$2; shift ;;
    --acme-email) ACME_EMAIL=$2; shift ;;
    --from-step) FROM_STEP=$2; shift ;;
    --lan-ip) LAN_IP_ARG=$2; shift ;;
    --public-ip) PUBLIC_IP_ARG=$2; shift ;;
    --server-id) SERVER_ID_ARG=$2; shift ;;
    --enable) ENABLE=1 ;;
    *) echo "неизвестный аргумент: $1" >&2; sed -n 2,7p "$0"; exit 1 ;;
  esac
  shift
done
[ -n "$NAME" ] || { echo "--name обязателен (напр. cells3)" >&2; exit 1; }
echo "$NAME" | grep -qE '^[a-z][a-z0-9-]{0,31}$' || { echo "--name: строчные буквы, цифры, дефис" >&2; exit 1; }
[ -n "$SUFFIX" ] || SUFFIX="dev$(echo "$NAME" | tr -dc '0-9')"; [ "$SUFFIX" != "dev" ] || SUFFIX="dev-$NAME"
ALIAS="max-$NAME"
PREVIEW_SUFFIX="$SUFFIX.$DOMAIN"

dry() { [ "$DRY_RUN" = 1 ]; }
run() { if dry; then printf '  [dry-run] %s\n' "$*"; else "$@"; fi; }
remote_new() { # remote_new <script> [args...] — на новом хосте от root
  local script=$1; shift
  if dry; then printf '  [dry-run] ssh %s sudo bash -s -- %s < %s\n' "$ALIAS" "$*" "${script#"$HERE/"}"; else ssh -o BatchMode=yes "$ALIAS" "sudo bash -s -- $(printf '%q ' "$@")" < "$script"; fi
}
remote_core() { if dry; then printf '  [dry-run] ssh %s sudo bash -c %q\n' "$(inv core alias)" "$*"; else sh_remote core "$*"; fi; }
wait_alias() {
  local t=${1:-600} i=0
  dry && { echo "  [dry-run] wait ssh $ALIAS"; return; }
  until ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new "$ALIAS" true 2>/dev/null; do
    i=$((i+10)); [ "$i" -ge "$t" ] && { echo "$ALIAS не отвечает по ssh за ${t}s (панель могла обнулить ~/.ssh/authorized_keys — VNC)" >&2; return 1; }; sleep 10
  done
}

# ---------------------------------------------------------------- адреса нового хоста
compute_addresses() {
  local last_wg last_cidr last_port
  last_wg=$(echo "$INVENTORY" | awk 'NF {print $5}' | awk -F. '{print $4}' | sort -n | tail -1)
  last_cidr=$(echo "$INVENTORY" | awk 'NF {print $6}' | awk -F. '{print $2}' | sort -n | tail -1)
  last_port=$(echo "$INVENTORY" | awk 'NF {print $10}' | sort -n | tail -1)
  WG_IP="10.10.0.$((last_wg + 1))"
  CLUSTER_CIDR="10.$((last_cidr + 2)).0.0/16"
  SERVICE_CIDR="10.$((last_cidr + 3)).0.0/16"
  CLUSTER_DNS="10.$((last_cidr + 3)).0.10"
  TUNNEL_PORT=$((last_port + 1))
}
load_from_inventory() {
  PUBLIC_IP=$(inv "$NAME" public); LAN_IP=$(inv "$NAME" lan); WG_IP=$(inv "$NAME" wg)
  CLUSTER_CIDR=$(inv "$NAME" cluster_cidr); SERVICE_CIDR=$(inv "$NAME" service_cidr)
  [ -n "$WG_IP" ] || { echo "хост $NAME не найден в inventory.env — начните с шага 1/2" >&2; exit 1; }
}

# ------------------------------------------------------------------------- шаги
step_order() {
  log "1/11 order: Serverum — VPS '$NAME', тариф $PLAN$(dry && echo ' (dry-run, без сети)')"
  local flags="" keyflag=""
  dry && flags="--dry-run"
  if [ -f "$ADMIN_PUBKEY_FILE" ]; then keyflag="--ssh-key-file $ADMIN_PUBKEY_FILE"; else echo "  нет $ADMIN_PUBKEY_FILE — заказ без ключа, вход по паролю из панели (bootstrap положит ключ сам)"; fi
  local json
  # shellcheck disable=SC2086
  json=$(cd "$ORCH_DIR" && uv run --frozen python -m omnia_orchestrator.serverum_cli $flags --json order \
    --plan "$PLAN" --hostname "$NAME" $keyflag --note "MAX Studio cells host" --wait --timeout 900 --poll "$(dry && echo 0 || echo 15)")
  SERVER_ID=$(echo "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
  PUBLIC_IP=$(echo "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["public_ip"] or "")')
  LAN_IP=$(echo "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["private_ip"] or "")')
  [ -n "$LAN_IP_ARG" ] && LAN_IP=$LAN_IP_ARG
  [ -n "$PUBLIC_IP" ] || { echo "Serverum не вернул публичный IP"; exit 1; }
  [ -n "$LAN_IP" ] || { echo "Serverum не вернул адрес приватной сети 172.197.102.0/24 — включите её в панели и передайте --lan-ip"; exit 1; }
  echo "  сервер $SERVER_ID: public $PUBLIC_IP, lan $LAN_IP"
}

step_inventory() {
  log "2/11 inventory: $NAME → inventory.env + ~/.ssh/config"
  compute_addresses
  local line="$NAME $ALIAS $PUBLIC_IP $LAN_IP $WG_IP $CLUSTER_CIDR $SERVICE_CIDR $CLUSTER_DNS core $TUNNEL_PORT"
  echo "  строка инвентаря: $line"
  if dry; then
    echo "  [dry-run] python3 — добавить строку в INVENTORY и '$NAME' в HOSTS файла $HERE/inventory.env"
    echo "  [dry-run] ~/.ssh/config += Host $ALIAS (HostName $PUBLIC_IP, User $SSH_USER, IdentityFile ${ADMIN_PUBKEY_FILE%.pub})"
    return
  fi
  python3 - "$HERE/inventory.env" "$NAME" "$line" <<'PY'
import re, sys
path, name, line = sys.argv[1:]
text = open(path).read()
if re.search(rf"^{re.escape(name)}\s", text, re.M):
    print("  inventory.env: хост уже есть, оставляю")
else:
    text = text.replace("\n'\n", f"\n{line}\n'\n", 1)
    text = re.sub(r'^HOSTS="([^"]*)"', lambda m: f'HOSTS="{m.group(1)} {name}"', text, flags=re.M)
    open(path, "w").write(text)
    print("  inventory.env: добавлено")
PY
  local cfg="$HOME/.ssh/config"; touch "$cfg"; chmod 600 "$cfg"
  if ! grep -qE "^Host $ALIAS\$" "$cfg"; then
    printf '\nHost %s\n  HostName %s\n  User %s\n  IdentityFile %s\n  IdentitiesOnly yes\n' "$ALIAS" "$PUBLIC_IP" "$SSH_USER" "${ADMIN_PUBKEY_FILE%.pub}" >> "$cfg"
    echo "  ~/.ssh/config: Host $ALIAS добавлен"
  fi
  # shellcheck source=inventory.env
  . "$HERE/inventory.env"
}

step_bootstrap() {
  log "3/11 bootstrap: $ALIAS ($PUBLIC_IP)"
  wait_alias 600
  local pub peer_lans="" p
  pub=$(cat "$ADMIN_PUBKEY_FILE")
  for p in core runtime commerce; do peer_lans="$peer_lans $(inv "$p" lan)"; done
  if dry; then
    printf '  [dry-run] WG_PORT=%s ssh %s sudo bash -s -- %s %s %s %s %s %s %s %s "<pubkey>" "%s" < remote/00-bootstrap.sh\n' "$WG_PORT" "$ALIAS" "$NAME" "$DOMAIN" "$WG_IP" "$WG_NET" "$LAN_NET" "$CLUSTER_CIDR" "$SERVICE_CIDR" "$ADMIN_USER" "$peer_lans"
    return
  fi
  local out; out=$(mktemp)
  WG_PORT=$WG_PORT ssh -o BatchMode=yes "$ALIAS" "sudo bash -s -- $(printf '%q ' "$NAME" "$DOMAIN" "$WG_IP" "$WG_NET" "$LAN_NET" "$CLUSTER_CIDR" "$SERVICE_CIDR" "$ADMIN_USER" "$pub" "$peer_lans")" < "$R/00-bootstrap.sh" | tee "$out" | tail -3
  if grep -q REBOOT_REQUIRED "$out"; then sleep 5; wait_alias 600 && echo "  reboot $NAME: снова в строю"; fi
  rm -f "$out"
}

step_wireguard() {
  log "4/11 wireguard: все хосты (новый пир $NAME $WG_IP)"
  if dry; then echo "  [dry-run] $HERE/provision.sh wireguard"; return; fi
  "$HERE/provision.sh" wireguard
}

step_postgres() {
  log "5/11 postgres: $ALIAS"
  remote_new "$R/40-postgres.sh" "$NAME" "$WG_IP" "$WG_NET" "$CLUSTER_CIDR" "$(pg_db "$NAME")" "$(pg_user "$NAME")"
}

step_cell_prep() {
  log "6/11 cell-prep: $ALIAS (превью *.$PREVIEW_SUFFIX)"
  remote_new "$C/10-cell-host-prep.sh" "$ADMIN_USER" "$DOMAIN" "$PREVIEW_SUFFIX" "$ACME_EMAIL" "$PUBLIC_IP"
}

step_code() {
  log "7/11 code: /opt/omnia с core → $ALIAS по WireGuard ($WG_IP)"
  local core_alias; core_alias=$(inv core alias)
  if dry; then
    echo "  [dry-run] ssh $core_alias cat ~/.ssh/id_ed25519_mesh.pub | ssh $ALIAS sudo tee -a /etc/ssh/authorized_keys.d/$ADMIN_USER"
    echo "  [dry-run] ssh $core_alias: ~/.ssh/config += Host $NAME (HostName $WG_IP, IdentityFile ~/.ssh/id_ed25519_mesh)"
    echo "  [dry-run] ssh $core_alias rsync -a --delete --exclude .venv --exclude node_modules --exclude .next --exclude __pycache__ /opt/omnia/ $NAME:/opt/omnia/"
    echo "  [dry-run] ssh $core_alias cat /opt/omnia/apps/orchestrator/.env | ssh $ALIAS 'cat > /opt/omnia/apps/orchestrator/.env.core'"
    echo "  [dry-run] ssh $core_alias sudo cat /etc/max-studio/runtime-kubeconfig.yaml | ssh $ALIAS sudo tee /etc/max-studio/runtime-kubeconfig.yaml (root:$ADMIN_USER 0640)"
    return
  fi
  local mesh_pub; mesh_pub=$(ssh -o BatchMode=yes "$core_alias" "cat ~/.ssh/id_ed25519_mesh.pub")
  ssh -o BatchMode=yes "$ALIAS" "grep -qF '$mesh_pub' /etc/ssh/authorized_keys.d/$ADMIN_USER || echo '$mesh_pub' | sudo tee -a /etc/ssh/authorized_keys.d/$ADMIN_USER >/dev/null"
  ssh -o BatchMode=yes "$core_alias" "grep -qE '^Host $NAME\$' ~/.ssh/config 2>/dev/null || printf '\nHost %s\n  HostName %s\n  User %s\n  IdentityFile ~/.ssh/id_ed25519_mesh\n  StrictHostKeyChecking accept-new\n' '$NAME' '$WG_IP' '$ADMIN_USER' >> ~/.ssh/config"
  ssh -o BatchMode=yes "$core_alias" "rsync -a --delete --exclude .venv --exclude node_modules --exclude .next --exclude __pycache__ /opt/omnia/ $NAME:/opt/omnia/" && echo "  rsync: ok"
  ssh -o BatchMode=yes "$core_alias" "cat /opt/omnia/apps/orchestrator/.env" \
    | ssh -o BatchMode=yes "$ALIAS" "cat > /opt/omnia/apps/orchestrator/.env.core && chmod 600 /opt/omnia/apps/orchestrator/.env.core"
  ssh -o BatchMode=yes "$core_alias" "sudo cat /etc/max-studio/runtime-kubeconfig.yaml" \
    | ssh -o BatchMode=yes "$ALIAS" "sudo install -d -m 755 /etc/max-studio && sudo tee /etc/max-studio/runtime-kubeconfig.yaml >/dev/null && sudo chown root:$ADMIN_USER /etc/max-studio/runtime-kubeconfig.yaml && sudo chmod 640 /etc/max-studio/runtime-kubeconfig.yaml"
  echo "  .env.core и runtime-kubeconfig скопированы"
}

step_orchestrator() {
  log "8/11 orchestrator: $ALIAS"
  remote_new "$C/20-cell-host-orchestrator.sh" "$ADMIN_USER" "$PREVIEW_SUFFIX" "$WG_IP" "$PUBLIC_IP" "$(inv core wg)"
}

step_core_access() {
  log "9/11 core-access: redis платформы для $NAME ($WG_IP)"
  remote_core "ufw allow from $WG_IP to any port 6379 proto tcp comment 'cells host $NAME → platform redis' >/dev/null; ufw reload >/dev/null; ufw status | grep -F '$WG_IP' | sed 's/^/  /'"
}

step_backup() {
  log "10/11 backup: $ALIAS"
  remote_new "$C/30-cell-host-backup.sh" "$ADMIN_USER"
}

register_hosts_py() { # register_hosts_py <enabled: true|false> — программа для python3 на core (stdin)
  cat <<PY
import json, re
p = '.env'; t = open(p).read()
m = re.search(r'^ORCHESTRATOR_HOSTS=(.*)$', t, re.M)
hosts = json.loads(m.group(1)) if m and m.group(1).strip() else []
entry = {"name": "$NAME", "url": "http://$WG_IP:8003", "preview_host_suffix": "$PREVIEW_SUFFIX",
         "preview_resolver_rules": "MAP *.$PREVIEW_SUFFIX $WG_IP", "enabled": $1}
hosts = [h for h in hosts if h.get("name") != "$NAME"] + [entry]
line = "ORCHESTRATOR_HOSTS=" + json.dumps(hosts, ensure_ascii=False, separators=(",", ":"))
t = re.sub(r'^ORCHESTRATOR_HOSTS=.*$', lambda _m: line, t, flags=re.M) if m else t.rstrip('\n') + '\n' + line + '\n'
open(p, 'w').write(t)
print('  ORCHESTRATOR_HOSTS:', [(h['name'], h.get('enabled', True)) for h in hosts])
PY
}
apply_registry_on_core() { # apply_registry_on_core <enabled> — программа уходит по stdin ssh, дальше пересоздание api/worker
  local script
  script="cd $FULL && sudo -u $ADMIN_USER python3 - && docker compose up -d --no-build api worker generation-worker >/dev/null 2>&1 \
&& for i in 1 2 3 4 5 6 7 8 9 10 11 12; do curl -fsS https://$DOMAIN/api/health >/dev/null 2>&1 && break; sleep 5; done; \
curl -sS https://$DOMAIN/api/health | python3 -c 'import json,sys; d=json.load(sys.stdin); print(\"  /api/health:\", d[\"status\"], \"| deploy_control_plane:\", d[\"checks\"].get(\"deploy_control_plane\"), \"| orchestrator_release_sha:\", d[\"dependencies\"].get(\"orchestrator_release_sha\"))'"
  register_hosts_py "$1" | ssh -o BatchMode=yes "$(inv core alias)" "sudo bash -c $(printf '%q' "$script")"
}

step_register() {
  log "11/11 register: $NAME в ORCHESTRATOR_HOSTS платформы (enabled=false), пересоздать api/worker/generation-worker"
  if dry; then
    echo "  [dry-run] ssh $(inv core alias): в $FULL/.env добавить {\"name\":\"$NAME\",\"url\":\"http://$WG_IP:8003\",\"preview_host_suffix\":\"$PREVIEW_SUFFIX\",\"preview_resolver_rules\":\"MAP *.$PREVIEW_SUFFIX $WG_IP\",\"enabled\":false}"
    echo "  [dry-run] ssh $(inv core alias) cd $FULL && docker compose up -d --no-build api worker generation-worker; curl https://$DOMAIN/api/health → checks.deploy_control_plane"
  else
    apply_registry_on_core false
  fi
  cat <<EOF

  Готово: $NAME зарегистрирован выключенным. Дальше:
    1. владелец заводит DNS: *.$PREVIEW_SUFFIX → $PUBLIC_IP (reg.ru);
    2. $0 --name $NAME --enable   # enabled=true → новые ячейки идут и на $NAME
EOF
}

step_enable() {
  log "enable: $NAME → enabled=true в ORCHESTRATOR_HOSTS, пересоздать api/worker/generation-worker"
  if dry; then echo "  [dry-run] ssh $(inv core alias): ORCHESTRATOR_HOSTS[$NAME].enabled=true; docker compose up -d --no-build api worker generation-worker"; return; fi
  local resolved; resolved=$(dig +short A "probe.$PREVIEW_SUFFIX" @1.1.1.1 | head -1)
  [ "$resolved" = "$PUBLIC_IP" ] || { echo "DNS *.$PREVIEW_SUFFIX → '$resolved', ожидается $PUBLIC_IP — сначала запись у reg.ru" >&2; exit 1; }
  apply_registry_on_core true
}

# ------------------------------------------------------------------------ прогон
if [ "$ENABLE" = 1 ]; then
  load_from_inventory; step_enable; exit 0
fi
if [ "$FROM_STEP" -le 1 ]; then
  step_order
else
  load_from_inventory
fi
[ "$FROM_STEP" -le 2 ] && step_inventory
[ -n "${PUBLIC_IP_ARG}" ] && PUBLIC_IP=$PUBLIC_IP_ARG
[ "$FROM_STEP" -le 3 ] && step_bootstrap
[ "$FROM_STEP" -le 4 ] && step_wireguard
[ "$FROM_STEP" -le 5 ] && step_postgres
[ "$FROM_STEP" -le 6 ] && step_cell_prep
[ "$FROM_STEP" -le 7 ] && step_code
[ "$FROM_STEP" -le 8 ] && step_orchestrator
[ "$FROM_STEP" -le 9 ] && step_core_access
[ "$FROM_STEP" -le 10 ] && step_backup
[ "$FROM_STEP" -le 11 ] && step_register
exit 0
