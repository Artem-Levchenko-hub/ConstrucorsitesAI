#!/usr/bin/env bash
# Edge (Фаза 1): wildcard-сертификаты Let's Encrypt для имён платформы — выпуск и продление на core через
# DNS-01 у СВОЕГО acme-dns (05-acme-dns.sh, hook acme.sh `dns_acmedns`; API reg.ru не нужен) или, по выбору,
# через API reg.ru (`dns_regru`). Ставится драйвером edge.sh как /usr/local/sbin/max-edge-issue, от root на core:
#
#   max-edge-issue issue    выпустить недостающие / истекающие сертификаты по группам, установить в
#                           /etc/max-studio/edge/certs/<группа>/ и раздать хостам (max-edge-distribute push;
#                           он же запоминается acme.sh как --reloadcmd и срабатывает при каждом продлении)
#   max-edge-issue renew    плановое продление (acme.sh --cron) — max-edge-renew.timer раз в сутки
#   max-edge-issue timer    поставить/обновить systemd-таймер продления
#   max-edge-issue status   срок, SAN, дни до конца по группам; код 1, если где-то осталось < EDGE_WARN_DAYS
#
# Почему несколько сертификатов, а не один на все имена: acme-dns хранит только ДВА последних TXT на своей
# записи, а все _acme-challenge.<база> ведут CNAME'ом на одну и ту же запись. Поэтому выпуск идёт группами,
# каждая — не больше двух имён с одной точкой проверки: root = yleum.ru + *.yleum.ru (обе проверяются через
# _acme-challenge.yleum.ru), apps = *.apps, dev = *.dev, dev2 = *.dev2. Группы выпускаются по очереди.
#
# Секреты: /etc/max-studio/edge/acme-dns.env (пишет 05-acme-dns.sh) или regru.env (edge.sh creds); root, 0600.
# Параметры: /etc/max-studio/edge/edge.env (пишет edge.sh install); без него действуют значения ниже.
set -euo pipefail
umask 077
EDGE_DIR=/etc/max-studio/edge
EDGE_ENV="$EDGE_DIR/edge.env"
# shellcheck disable=SC1090
[ -f "$EDGE_ENV" ] && . "$EDGE_ENV"
: "${EDGE_DOMAIN:=yleum.ru}"
: "${EDGE_DNS_HOOK:=dns_acmedns}"              # dns_acmedns (свой acme-dns) | dns_regru (API reg.ru)
# Группы «имя:SAN,SAN» — первое имя группы = ключ сертификата в хранилище acme.sh.
: "${EDGE_GROUPS:=root:$EDGE_DOMAIN,*.$EDGE_DOMAIN apps:*.apps.$EDGE_DOMAIN dev:*.dev.$EDGE_DOMAIN dev2:*.dev2.$EDGE_DOMAIN}"
: "${EDGE_ACME_EMAIL:=admin@$EDGE_DOMAIN}"
: "${EDGE_ACME_SERVER:=letsencrypt}"          # letsencrypt_test — репетиция без расхода лимитов (см. EDGE_FORCE)
: "${EDGE_ACME_HOME:=$EDGE_DIR/acme-home}"     # аккаунт, ключи, конфиги acme.sh (root, 0700)
: "${EDGE_ACME_SH_DIR:=/usr/local/lib/max-edge/acme.sh}"
: "${EDGE_ACME_SH_REF:=master}"
: "${EDGE_RENEW_DAYS:=30}"                     # переиздать, если годен меньше
: "${EDGE_WARN_DAYS:=20}"                      # status → код 1, если годен меньше
: "${EDGE_DNS_SLEEP:=}"                        # пусто = acme.sh сам ждёт появления TXT; иначе секунды слепого ожидания
: "${EDGE_FORCE:=0}"                           # 1 = --force (например, после репетиции на letsencrypt_test)
: "${EDGE_DISTRIBUTE:=/usr/local/sbin/max-edge-distribute}"
CERTS_DIR="$EDGE_DIR/certs"
log() { printf '[%s] edge: %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { echo "max-edge-issue: $*" >&2; exit 1; }
need_root() { [ "$(id -u)" = 0 ] || die "нужен root"; }

load_creds() {
  case "$EDGE_DNS_HOOK" in
    dns_acmedns)
      [ -f "$EDGE_DIR/acme-dns.env" ] || die "нет $EDGE_DIR/acme-dns.env — сначала 05-acme-dns.sh install"
      chmod 600 "$EDGE_DIR/acme-dns.env"
      set -a
      # shellcheck disable=SC1091
      . "$EDGE_DIR/acme-dns.env"
      set +a
      [ -n "${ACMEDNS_BASE_URL:-}" ] && [ -n "${ACMEDNS_USERNAME:-}" ] && [ -n "${ACMEDNS_PASSWORD:-}" ] \
        && [ -n "${ACMEDNS_SUBDOMAIN:-}" ] || die "$EDGE_DIR/acme-dns.env неполный"
      export ACMEDNS_BASE_URL ACMEDNS_USERNAME ACMEDNS_PASSWORD ACMEDNS_SUBDOMAIN
      ;;
    dns_regru)
      [ -f "$EDGE_DIR/regru.env" ] || die "нет $EDGE_DIR/regru.env (REGRU_API_Username / REGRU_API_Password) — edge.sh creds"
      chmod 600 "$EDGE_DIR/regru.env"
      set -a
      # shellcheck disable=SC1091
      . "$EDGE_DIR/regru.env"
      set +a
      [ -n "${REGRU_API_Username:-}" ] && [ -n "${REGRU_API_Password:-}" ] || die "$EDGE_DIR/regru.env неполный"
      export REGRU_API_Username REGRU_API_Password
      ;;
    *) die "EDGE_DNS_HOOK=$EDGE_DNS_HOOK: поддерживаются dns_acmedns и dns_regru" ;;
  esac
}
ensure_acme() {
  command -v git >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends git >/dev/null
  if [ ! -x "$EDGE_ACME_SH_DIR/acme.sh" ]; then
    install -d -m 755 "$(dirname "$EDGE_ACME_SH_DIR")"
    git clone -q --depth 1 --branch "$EDGE_ACME_SH_REF" https://github.com/acmesh-official/acme.sh.git "$EDGE_ACME_SH_DIR"
  fi
  [ -f "$EDGE_ACME_SH_DIR/dnsapi/$EDGE_DNS_HOOK.sh" ] || die "в $EDGE_ACME_SH_DIR нет dnsapi/$EDGE_DNS_HOOK.sh"
  install -d -m 700 "$EDGE_DIR" "$EDGE_ACME_HOME" "$CERTS_DIR"
}
# acme.sh считает LOG_LEVEL/DEBUG числами и падает на строковых значениях (см. nginx_writer.py) — вычищаем.
acme() { env -u LOG_LEVEL -u DEBUG "$EDGE_ACME_SH_DIR/acme.sh" --home "$EDGE_ACME_HOME" "$@"; }
cert_sans() { openssl x509 -in "$1" -noout -ext subjectAltName 2>/dev/null | tr ',' '\n' | sed -n 's/^ *DNS://p' | sort; }
cert_days_left() {
  local end; end=$(openssl x509 -in "$1" -noout -enddate | cut -d= -f2)
  echo $(( ( $(date -d "$end" +%s) - $(date +%s) ) / 86400 ))
}
cert_is_staging() { openssl x509 -in "$1" -noout -issuer 2>/dev/null | grep -qi staging; }
# cert_covers FILE "san1,san2" — установленный сертификат читается и его SAN ровно равен списку группы
cert_covers() { [ -s "$1" ] && [ "$(cert_sans "$1")" = "$(printf '%s\n' ${2//,/ } | sort)" ]; }
verify_group() { # verify_group DIR "san1,san2"
  local dir=$1 sans=$2
  [ -s "$dir/fullchain.pem" ] && [ -s "$dir/privkey.pem" ] || die "файлы сертификата не установлены в $dir"
  cert_covers "$dir/fullchain.pem" "$sans" || die "SAN $dir не совпадает с группой ($sans): $(cert_sans "$dir/fullchain.pem" | tr '\n' ' ')"
  [ "$(openssl x509 -in "$dir/fullchain.pem" -noout -pubkey)" = "$(openssl pkey -in "$dir/privkey.pem" -pubout)" ] \
    || die "ключ не соответствует сертификату в $dir"
  chmod 600 "$dir"/*.pem
}
group_up_to_date() { # группа выпущена не на staging, годна > EDGE_RENEW_DAYS и есть в хранилище acme.sh
  local dir=$1 sans=$2 first=${2%%,*}
  cert_covers "$dir/fullchain.pem" "$sans" && ! cert_is_staging "$dir/fullchain.pem" \
    && [ "$(cert_days_left "$dir/fullchain.pem")" -gt "$EDGE_RENEW_DAYS" ] \
    && [ -s "$EDGE_ACME_HOME/${first}_ecc/fullchain.cer" ] && [ "$EDGE_FORCE" != 1 ]
}
do_issue() {
  need_root; load_creds; ensure_acme
  acme --register-account -m "$EDGE_ACME_EMAIL" --server "$EDGE_ACME_SERVER" >/dev/null
  local g name sans first dir args n rc failed="" issued=0
  for g in $EDGE_GROUPS; do
    name=${g%%:*}; sans=${g#*:}; first=${sans%%,*}; dir="$CERTS_DIR/$name"
    install -d -m 700 "$dir"
    if group_up_to_date "$dir" "$sans"; then
      log "$name: сертификат покрывает $sans и годен ещё $(cert_days_left "$dir/fullchain.pem") дн. — выпуск не нужен"
    else
      args=(); for n in ${sans//,/ }; do args+=(-d "$n"); done
      [ -n "$EDGE_DNS_SLEEP" ] && args+=(--dnssleep "$EDGE_DNS_SLEEP")
      [ "$EDGE_FORCE" = 1 ] && args+=(--force)
      log "$name: выпуск через DNS-01 ($EDGE_DNS_HOOK, $EDGE_ACME_SERVER): $sans"
      set +e
      acme --issue --dns "$EDGE_DNS_HOOK" "${args[@]}" --keylength ec-256 --server "$EDGE_ACME_SERVER"
      rc=$?
      set -e
      case $rc in
        0) log "$name: выпущен"; issued=1 ;;
        2) log "$name: acme.sh — действующий сертификат ещё не требует продления" ;;
        *) log "$name: acme.sh --issue завершился с кодом $rc (DNS-делегирование на месте? 05-acme-dns.sh check)"; failed="$failed $name"; continue ;;
      esac
    fi
    # Файлы создаём заранее с 0600: acme.sh пишет в них cat'ом, права сохраняются. --reloadcmd запоминается
    # acme.sh и выполняется при каждом продлении: свежий сертификат уезжает на все хосты сам. При первом
    # выпуске раздача может отказать (другие группы ещё не выпущены) — итоговый push ниже.
    for n in privkey fullchain cert ca; do [ -e "$dir/$n.pem" ] || install -m 600 /dev/null "$dir/$n.pem"; done
    set +e
    acme --install-cert -d "$first" --ecc \
      --key-file "$dir/privkey.pem" --fullchain-file "$dir/fullchain.pem" --cert-file "$dir/cert.pem" --ca-file "$dir/ca.pem" \
      --reloadcmd "$EDGE_DISTRIBUTE push" >/dev/null 2>&1
    set -e
    verify_group "$dir" "$sans"
    log "$name: установлен в $dir (до $(openssl x509 -in "$dir/fullchain.pem" -noout -enddate | cut -d= -f2))"
  done
  do_timer
  [ -z "$failed" ] || die "не выпущены группы:$failed — остальные установлены; повторить: max-edge-issue issue"
  if [ "$issued" = 1 ] || [ ! -f "$EDGE_DIR/last-push" ]; then
    log "раздача на все хосты"
    "$EDGE_DISTRIBUTE" push || die "сертификаты выпущены, но раздача вернула ошибку — повторить: max-edge-distribute push"
  fi
}
do_renew() {
  need_root
  [ -d "$CERTS_DIR" ] && ls "$CERTS_DIR"/*/fullchain.pem >/dev/null 2>&1 || { log "сертификаты ещё не выпущены — продлевать нечего"; exit 0; }
  load_creds; ensure_acme
  # Продлевает только то, что подошло по сроку; после каждого успеха acme.sh сам зовёт reloadcmd (push).
  acme --cron >/dev/null || die "acme.sh --cron завершился с ошибкой"
  do_status
}
do_timer() {
  need_root
  cat > /etc/systemd/system/max-edge-renew.service <<EOF
# generated by infra/max-k3s/edge/10-wildcard-issue.sh — продление wildcard-сертификатов edge
[Unit]
Description=MAX Studio edge: renew wildcard certificates (acme.sh, DNS-01 $EDGE_DNS_HOOK)
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/max-edge-issue renew
EOF
  cat > /etc/systemd/system/max-edge-renew.timer <<'EOF'
# generated by infra/max-k3s/edge/10-wildcard-issue.sh
[Unit]
Description=MAX Studio edge: daily wildcard certificate renewal check
[Timer]
OnCalendar=*-*-* 04:40:00
RandomizedDelaySec=30min
Persistent=true
[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now max-edge-renew.timer >/dev/null 2>&1
  log "таймер продления: $(systemctl list-timers max-edge-renew.timer --no-legend 2>/dev/null | awk '{print $1" "$2" "$3}')"
}
do_status() {
  local g name sans dir days rc=0 any=0
  for g in $EDGE_GROUPS; do
    name=${g%%:*}; sans=${g#*:}; dir="$CERTS_DIR/$name"
    if [ ! -s "$dir/fullchain.pem" ]; then echo "edge: $name ($sans): сертификата нет — max-edge-issue issue"; rc=1; continue; fi
    any=1; days=$(cert_days_left "$dir/fullchain.pem")
    echo "edge: $name: $(openssl x509 -in "$dir/fullchain.pem" -noout -issuer -enddate | tr '\n' ' ') SAN: $(cert_sans "$dir/fullchain.pem" | tr '\n' ' ')— осталось $days дн."
    [ "$days" -lt "$EDGE_WARN_DAYS" ] && { echo "edge: ВНИМАНИЕ — $name годен меньше $EDGE_WARN_DAYS дн., проверить журнал max-edge-renew.service"; rc=1; }
  done
  [ -f "$EDGE_DIR/last-push" ] && echo "edge: последняя раздача: $(cat "$EDGE_DIR/last-push")"
  echo "edge: таймер: $(systemctl is-enabled max-edge-renew.timer 2>/dev/null || echo 'не установлен'); DNS-01: $EDGE_DNS_HOOK"
  [ "$any" = 1 ] || rc=1
  return $rc
}
case "${1:-issue}" in
  issue) do_issue ;;
  renew) do_renew ;;
  timer) do_timer ;;
  status) do_status ;;
  *) sed -n '2,13p' "$0"; exit 1 ;;
esac
