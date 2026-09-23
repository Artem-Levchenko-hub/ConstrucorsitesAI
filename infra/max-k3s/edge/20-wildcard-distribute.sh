#!/usr/bin/env bash
# Edge (Фаза 1): раздача wildcard-сертификата с core на все хосты по WireGuard и применение на каждом.
# Ставится драйвером edge.sh на все три хоста как /usr/local/sbin/max-edge-distribute, работает от root.
#
#   keygen                 core:     ключ раздачи /etc/max-studio/edge/id_ed25519 (печатает публичную часть)
#   authorize PUBKEY ROLE  пир:      пользователь maxedge + forced-command ключ + sudoers ровно на «receive ROLE»
#   push                   core:     tar с сертификатом → каждому пиру из EDGE_PEERS, затем apply core локально;
#                                    это же зовёт acme.sh после каждого продления (--reloadcmd)
#   receive ROLE           пир:      forced command — принять tar из stdin, проверить, установить, apply ROLE
#   apply ROLE             любой:    применить лежащие в /etc/max-studio/edge файлы (idempotent, с проверкой)
#   rollback ROLE          любой:    вернуть хост на прежнюю схему (см. ниже)
#   status ROLE            любой:    что отдаётся на :443 для имён роли (openssl s_client)
#
# Роли: runtime  — Secret tls kube-system/wildcard-yleum + TLSStore default в Traefik (сертификат по умолчанию
#                  для всех Ingress без своего секрета — опубликованные приложения получают TLS сразу);
#       core     — раскладка для оркестратора (OMNIA_WILDCARD_CERT_ROOT → превью *.dev без HTTP-01) и платформенные
#                  vhost'ы nginx (yleum.ru, www, grafana) на wildcard; лineage certbot остаётся как запасной
#                  (installer=None — certbot больше не переписывает vhost при продлении);
#       commerce — раскладка для оркестратора (*.dev2).
# Catch-all nginx на core/commerce НАМЕРЕННО остаётся с самоподписанным сертификатом: оркестратор отличает
# «старые воркеры nginx после reload» от нового vhost именно по ошибке TLS (nginx_writer._probe_live, 239095b0).
set -euo pipefail
umask 077

EDGE_DIR=/etc/max-studio/edge
EDGE_ENV="$EDGE_DIR/edge.env"
# shellcheck disable=SC1090
[ -f "$EDGE_ENV" ] && . "$EDGE_ENV"
: "${EDGE_DOMAIN:=yleum.ru}"
: "${EDGE_PEERS:=runtime:10.10.0.2 commerce:10.10.0.3}"   # name:wg_ip; роль пира = его имя
: "${EDGE_K8S_SECRET:=wildcard-yleum}"
: "${EDGE_K8S_NAMESPACE:=kube-system}"
: "${EDGE_ORCH_ENV:=/opt/omnia/apps/orchestrator/.env}"
: "${EDGE_LETSENCRYPT_LINEAGE:=$EDGE_DOMAIN}"              # certbot: /etc/letsencrypt/live/<lineage>
KEY="$EDGE_DIR/wildcard.key"
FULLCHAIN="$EDGE_DIR/wildcard.fullchain.pem"
CERT="$EDGE_DIR/wildcard.cert.pem"
CA="$EDGE_DIR/wildcard.ca.pem"
FILES="wildcard.key wildcard.fullchain.pem wildcard.cert.pem wildcard.ca.pem"
SELF=/usr/local/sbin/max-edge-distribute
NGINX_MARK="# max-edge wildcard"

log() { printf '[%s] edge/%s: %s\n' "$(date +%H:%M:%S)" "$(hostname)" "$*"; }
die() { echo "max-edge-distribute($(hostname)): $*" >&2; exit 1; }
need_root() { [ "$(id -u)" = 0 ] || die "нужен root"; }

cert_sans() { openssl x509 -in "$1" -noout -ext subjectAltName 2>/dev/null | tr ',' '\n' | sed -n 's/^ *DNS://p' | sort; }
san_has() { cert_sans "$1" | grep -qxF "$2"; }

# Имена, которые сертификат обязан покрывать для роли (иначе применять его на этом хосте нельзя).
role_required() {
  case "$1" in
    runtime)  echo "*.apps.$EDGE_DOMAIN" ;;
    core)     echo "*.dev.$EDGE_DOMAIN" ;;
    commerce) echo "*.dev2.$EDGE_DOMAIN" ;;
    *) die "неизвестная роль '$1' (runtime|core|commerce)" ;;
  esac
}

# verify_files DIR ROLE — сертификат читается, ключ от него, срок не истёк, SAN покрывает имена роли.
verify_files() {
  local dir=$1 role=$2 f name
  for f in $FILES; do [ -s "$dir/$f" ] || die "нет файла $dir/$f"; done
  openssl x509 -in "$dir/wildcard.fullchain.pem" -noout >/dev/null 2>&1 || die "wildcard.fullchain.pem не читается"
  [ "$(openssl x509 -in "$dir/wildcard.fullchain.pem" -noout -pubkey)" = "$(openssl pkey -in "$dir/wildcard.key" -pubout 2>/dev/null)" ] \
    || die "ключ не соответствует сертификату"
  openssl x509 -in "$dir/wildcard.fullchain.pem" -noout -checkend 86400 >/dev/null || die "сертификат истёк или истекает в ближайшие сутки"
  for name in $(role_required "$role"); do
    san_has "$dir/wildcard.fullchain.pem" "$name" || die "сертификат не покрывает $name (SAN: $(cert_sans "$dir/wildcard.fullchain.pem" | tr '\n' ' '))"
  done
}

# probe_tls ADDR SNI EXPECTED_SAN [ATTEMPTS] — что отдаёт :443 за это имя; 0 = SAN совпал.
probe_tls() {
  local addr=$1 sni=$2 want=$3 attempts=${4:-1} i sans
  for i in $(seq 1 "$attempts"); do
    # «|| true»: при недоступном порте конвейер падает, а нам нужен именно return 1, не выход по errexit
    sans=$(echo | timeout 8 openssl s_client -connect "$addr:443" -servername "$sni" 2>/dev/null \
      | openssl x509 -noout -ext subjectAltName 2>/dev/null | tr ',' '\n' | sed -n 's/^ *DNS://p' | tr '\n' ' ' || true)
    if echo " $sans" | grep -qF " $want "; then echo "  $sni → $addr:443 отдаёт $want"; return 0; fi
    [ "$i" -lt "$attempts" ] && sleep 2
  done
  echo "  $sni → $addr:443 отдаёт: ${sans:-(нет ответа)} — ожидалось $want"
  return 1
}

nginx_reload() { nginx -t >/dev/null 2>&1 || { nginx -t; die "nginx -t не проходит — конфиг не применён"; }; systemctl reload nginx; }

# Раскладка для оркестратора: <root>/<runtime_host_suffix>/{fullchain.pem,privkey.pem}, где
# root = OMNIA_WILDCARD_CERT_ROOT = /etc/max-studio/edge/wildcard (nginx_writer._wildcard_cert_dir). Оркестратор
# файлы не читает (это делает nginx от root), поэтому 0700/0600 достаточно.
orchestrator_layout() {
  local suffix=$1 d="$EDGE_DIR/wildcard/$1"
  install -d -m 700 "$EDGE_DIR/wildcard" "$d"
  ln -sfn ../../wildcard.fullchain.pem "$d/fullchain.pem"
  ln -sfn ../../wildcard.key "$d/privkey.pem"
  log "раскладка оркестратора: $d → *.$suffix"
}

# ------------------------------------------------------------------ core: платформенные vhost'ы
platform_vhost_files() {
  grep -lsE "^\s*ssl_certificate(_key)?\s+(/etc/letsencrypt/live/$EDGE_LETSENCRYPT_LINEAGE/|$EDGE_DIR/wildcard\.)" /etc/nginx/sites-available/* 2>/dev/null || true
}

platform_vhost_switch() {
  # Только если сертификат покрывает и корень домена, и *.domain (www, grafana).
  if ! san_has "$FULLCHAIN" "$EDGE_DOMAIN" || ! san_has "$FULLCHAIN" "*.$EDGE_DOMAIN"; then
    log "сертификат не покрывает $EDGE_DOMAIN + *.$EDGE_DOMAIN — платформенные vhost'ы оставлены на certbot"; return 0
  fi
  local f changed=0
  for f in $(platform_vhost_files); do
    grep -q "$FULLCHAIN" "$f" && continue
    [ -f "$f.max-edge.orig" ] || cp -a "$f" "$f.max-edge.orig"
    # разделитель «|»: в путях его нет, а в метке есть «#»
    sed -i -E \
      -e "s|^(\s*)ssl_certificate\s+/etc/letsencrypt/live/$EDGE_LETSENCRYPT_LINEAGE/fullchain\.pem;.*|\1ssl_certificate $FULLCHAIN; $NGINX_MARK|" \
      -e "s|^(\s*)ssl_certificate_key\s+/etc/letsencrypt/live/$EDGE_LETSENCRYPT_LINEAGE/privkey\.pem;.*|\1ssl_certificate_key $KEY; $NGINX_MARK|" "$f"
    changed=1; log "vhost $(basename "$f") → wildcard"
  done
  # certbot при продлении переписал бы ssl_certificate обратно на свой lineage: оставляем его продлеваться
  # как запасной (certonly), но без установки в nginx.
  local renewal="/etc/letsencrypt/renewal/$EDGE_LETSENCRYPT_LINEAGE.conf"
  if [ -f "$renewal" ] && grep -q '^installer = nginx' "$renewal"; then
    sed -i 's/^installer = nginx/installer = None/' "$renewal"; log "certbot: $renewal → installer = None (lineage остаётся запасным)"
  fi
  if [ "$changed" = 1 ]; then
    if ! nginx -t >/dev/null 2>&1; then
      for f in $(platform_vhost_files); do [ -f "$f.max-edge.orig" ] && cp -a "$f.max-edge.orig" "$f"; done
      nginx -t; die "nginx -t не прошёл после переключения — vhost'ы возвращены"
    fi
  fi
}

platform_vhost_rollback() {
  local f live="/etc/letsencrypt/live/$EDGE_LETSENCRYPT_LINEAGE"
  [ -s "$live/fullchain.pem" ] || { log "нет $live/fullchain.pem — сначала certbot --nginx -d $EDGE_DOMAIN -d www.$EDGE_DOMAIN -d grafana.$EDGE_DOMAIN"; return 1; }
  for f in $(platform_vhost_files); do
    grep -q "$NGINX_MARK" "$f" || continue
    sed -i -E \
      -e "s|^(\s*)ssl_certificate\s+$FULLCHAIN;.*|\1ssl_certificate $live/fullchain.pem; # managed by Certbot|" \
      -e "s|^(\s*)ssl_certificate_key\s+$KEY;.*|\1ssl_certificate_key $live/privkey.pem; # managed by Certbot|" "$f"
    log "vhost $(basename "$f") → certbot"
  done
  local renewal="/etc/letsencrypt/renewal/$EDGE_LETSENCRYPT_LINEAGE.conf"
  [ -f "$renewal" ] && sed -i 's/^installer = None/installer = nginx/' "$renewal"
}

# ------------------------------------------------------------------ runtime: Traefik
k8s() { /usr/local/bin/k3s kubectl "$@"; }
tlsstore_group() {
  if k8s get crd tlsstores.traefik.io >/dev/null 2>&1; then echo traefik.io
  elif k8s get crd tlsstores.traefik.containo.us >/dev/null 2>&1; then echo traefik.containo.us
  else return 1; fi
}

apply_runtime() {
  local group; group=$(tlsstore_group) || die "в кластере нет CRD TLSStore (Traefik не установлен?)"
  k8s -n "$EDGE_K8S_NAMESPACE" create secret tls "$EDGE_K8S_SECRET" --cert="$FULLCHAIN" --key="$KEY" --dry-run=client -o yaml \
    | k8s apply -f - >/dev/null
  # Traefik принимает TLSStore с именем default из любого namespace (defaultTLSResourcesNamespace не задан);
  # секрет должен лежать в том же namespace, что и TLSStore.
  k8s apply -f - >/dev/null <<EOF
apiVersion: $group/v1alpha1
kind: TLSStore
metadata:
  name: default
  namespace: $EDGE_K8S_NAMESPACE
spec:
  defaultCertificate:
    secretName: $EDGE_K8S_SECRET
EOF
  log "Secret $EDGE_K8S_NAMESPACE/$EDGE_K8S_SECRET + TLSStore default ($group) применены"
  status_runtime || die "Traefik не отдаёт wildcard за *.apps.$EDGE_DOMAIN — проверить: k3s kubectl -n $EDGE_K8S_NAMESPACE logs deploy/traefik"
}

status_runtime() {
  # Traefik слушает на hostPort через klipper-lb: 127.0.0.1 обычно работает, иначе — адреса хоста (WG/LAN).
  local addr
  for addr in 127.0.0.1 $(hostname -I 2>/dev/null); do
    probe_tls "$addr" "edge-probe.apps.$EDGE_DOMAIN" "*.apps.$EDGE_DOMAIN" 8 && return 0
  done
  return 1
}

rollback_runtime() {
  local group; group=$(tlsstore_group) || return 0
  k8s -n "$EDGE_K8S_NAMESPACE" delete tlsstore.$group default --ignore-not-found >/dev/null
  k8s -n "$EDGE_K8S_NAMESPACE" delete secret "$EDGE_K8S_SECRET" --ignore-not-found >/dev/null
  log "TLSStore default и секрет удалены — Traefik вернулся к самоподписанному сертификату по умолчанию"
  # Приложения, опубликованные в режиме wildcard, остались без своего сертификата: вернуть им cert-manager
  # (оркестратор при следующей публикации перепишет Ingress сам; до этого — руками здесь).
  local ns host
  for ns in $(k8s get ingress -A -l app.kubernetes.io/managed-by=omnia-orchestrator -o jsonpath='{range .items[?(@.metadata.name=="public")]}{.metadata.namespace}{"\n"}{end}'); do
    k8s -n "$ns" get ingress public -o jsonpath='{.spec.tls[0].secretName}' | grep -q . && continue
    host=$(k8s -n "$ns" get ingress public -o jsonpath='{.spec.rules[0].host}')
    k8s -n "$ns" annotate ingress public cert-manager.io/cluster-issuer=letsencrypt-prod --overwrite >/dev/null
    k8s -n "$ns" patch ingress public --type=merge -p "{\"spec\":{\"tls\":[{\"hosts\":[\"$host\"],\"secretName\":\"public-tls\"}]}}" >/dev/null
    log "$ns: Ingress public → cert-manager (letsencrypt-prod, public-tls) для $host"
  done
}

# ------------------------------------------------------------------ apply / rollback / status
do_apply() {
  local role=$1
  need_root
  verify_files "$EDGE_DIR" "$role"
  case "$role" in
    runtime)  apply_runtime ;;
    core)     orchestrator_layout "dev.$EDGE_DOMAIN"; platform_vhost_switch; nginx_reload; status_core || true ;;
    commerce) orchestrator_layout "dev2.$EDGE_DOMAIN"; nginx_reload ;;
  esac
  log "apply $role: готово"
}

status_core() {
  local ok=0
  if platform_vhost_files | xargs -r grep -l "$NGINX_MARK" >/dev/null 2>&1; then
    probe_tls 127.0.0.1 "$EDGE_DOMAIN" "*.$EDGE_DOMAIN" 3 || ok=1
    probe_tls 127.0.0.1 "grafana.$EDGE_DOMAIN" "*.$EDGE_DOMAIN" 3 || ok=1
  else
    echo "  платформенные vhost'ы на certbot (wildcard не применён к yleum.ru)"
  fi
  echo "  превью *.dev.$EDGE_DOMAIN: сертификат подставляет оркестратор в vhost каждого превью (OMNIA_WILDCARD_CERT_ROOT в $EDGE_ORCH_ENV: $(grep -c '^OMNIA_WILDCARD_CERT_ROOT=' "$EDGE_ORCH_ENV" 2>/dev/null || echo 0) записей); catch-all намеренно самоподписанный"
  return $ok
}

do_status() {
  local role=$1
  echo "edge/$(hostname) [$role]: $( [ -s "$FULLCHAIN" ] && openssl x509 -in "$FULLCHAIN" -noout -enddate | sed 's/notAfter=/до /' || echo 'сертификата нет')"
  case "$role" in
    runtime)  status_runtime ;;
    core)     status_core ;;
    commerce) echo "  превью *.dev2.$EDGE_DOMAIN: сертификат подставляет оркестратор (OMNIA_WILDCARD_CERT_ROOT в $EDGE_ORCH_ENV: $(grep -c '^OMNIA_WILDCARD_CERT_ROOT=' "$EDGE_ORCH_ENV" 2>/dev/null || echo 0) записей)" ;;
  esac
}

do_rollback() {
  local role=$1
  need_root
  case "$role" in
    runtime)  rollback_runtime ;;
    core)     platform_vhost_rollback; rm -rf "$EDGE_DIR/wildcard"; nginx_reload; log "core: vhost'ы на certbot; убрать OMNIA_WILDCARD_CERT_ROOT из $EDGE_ORCH_ENV (edge.sh orchestrator-env off) и перезапустить оркестратор" ;;
    commerce) rm -rf "$EDGE_DIR/wildcard"; nginx_reload; log "commerce: раскладка убрана; убрать OMNIA_WILDCARD_CERT_ROOT из $EDGE_ORCH_ENV и перезапустить оркестратор" ;;
  esac
  log "rollback $role: готово (сами файлы в $EDGE_DIR оставлены)"
}

# ------------------------------------------------------------------ транспорт core → пиры
do_keygen() {
  need_root
  install -d -m 700 "$EDGE_DIR"
  [ -s "$EDGE_DIR/id_ed25519" ] || ssh-keygen -q -t ed25519 -N '' -C "max-edge@$(hostname)" -f "$EDGE_DIR/id_ed25519"
  cat "$EDGE_DIR/id_ed25519.pub"
}

do_authorize() {
  local pub=$1 role=$2 keys=/etc/ssh/authorized_keys.d/maxedge
  need_root
  role_required "$role" >/dev/null
  [ -x "$SELF" ] || die "нет $SELF — сначала edge.sh install"
  id maxedge >/dev/null 2>&1 || useradd --system --home-dir /var/lib/max-edge --shell /bin/sh --create-home maxedge
  install -d -m 755 /etc/ssh/authorized_keys.d
  touch "$keys"; chmod 644 "$keys"
  sed -i '/ max-edge@/d' "$keys"
  echo "command=\"sudo -n $SELF receive $role\",restrict $pub" >> "$keys"
  printf 'maxedge ALL=(root) NOPASSWD: %s receive %s\n' "$SELF" "$role" > /etc/sudoers.d/max-edge
  chmod 440 /etc/sudoers.d/max-edge
  visudo -cf /etc/sudoers.d/max-edge >/dev/null
  # bootstrap ограничивает вход «AllowUsers zeuszcz backupsync»; у sshd списки AllowUsers из разных
  # директив складываются, поэтому отдельный drop-in добавляет maxedge, не трогая файл bootstrap'а.
  if ! grep -qsx 'AllowUsers maxedge' /etc/ssh/sshd_config.d/06-max-edge.conf; then
    printf '# MAX Studio edge (infra/max-k3s/edge/20-wildcard-distribute.sh authorize)\nAllowUsers maxedge\n' > /etc/ssh/sshd_config.d/06-max-edge.conf
    sshd -t && systemctl reload ssh
  fi
  install -d -m 700 "$EDGE_DIR"
  log "authorize: ключ core может только «$SELF receive $role» (пользователь maxedge)"
}

do_receive() {
  local role=$1 tmp f
  need_root
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  tar -xf - -C "$tmp"
  verify_files "$tmp" "$role"
  install -d -m 700 "$EDGE_DIR"
  for f in $FILES; do install -m 600 -o root -g root "$tmp/$f" "$EDGE_DIR/$f.new"; mv -f "$EDGE_DIR/$f.new" "$EDGE_DIR/$f"; done
  log "receive $role: файлы установлены в $EDGE_DIR"
  do_apply "$role"
}

do_push() {
  local peer name ip failed="" ok=""
  need_root
  verify_files "$EDGE_DIR" core
  [ -s "$EDGE_DIR/id_ed25519" ] || die "нет ключа раздачи — max-edge-distribute keygen + edge.sh authorize"
  for peer in $EDGE_PEERS; do
    name=${peer%%:*}; ip=${peer#*:}
    log "push → $name ($ip)"
    if tar -C "$EDGE_DIR" -cf - $FILES | ssh -i "$EDGE_DIR/id_ed25519" -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
         -o UserKnownHostsFile="$EDGE_DIR/known_hosts" -o ConnectTimeout=15 "maxedge@$ip" receive; then
      ok="$ok $name"
    else
      failed="$failed $name"; log "push → $name: ОШИБКА"
    fi
  done
  if do_apply core; then ok="$ok core"; else failed="$failed core"; fi
  echo "$(date -Is) ok:[${ok# }] failed:[${failed# }]" > "$EDGE_DIR/last-push"
  log "раздача: ok [${ok# }] failed [${failed# }]"
  [ -z "$failed" ] || die "не все хосты приняли сертификат:${failed}"
}

case "${1:-}" in
  keygen)    do_keygen ;;
  authorize) [ $# -eq 3 ] || die "authorize PUBKEY ROLE"; do_authorize "$2" "$3" ;;
  push)      do_push ;;
  receive)   [ $# -eq 2 ] || die "receive ROLE"; do_receive "$2" ;;
  apply)     do_apply "${2:-$(hostname)}" ;;
  rollback)  do_rollback "${2:-$(hostname)}" ;;
  status)    do_status "${2:-$(hostname)}" ;;
  *) sed -n '2,20p' "$0"; exit 1 ;;
esac
