#!/usr/bin/env bash
# Edge (Фаза 1): один wildcard-сертификат Let's Encrypt на все имена платформы — выпуск и продление
# на core через DNS-01 у reg.ru (acme.sh + dns_regru). Ставится драйвером edge.sh как
# /usr/local/sbin/max-edge-issue, запускается от root на core:
#
#   max-edge-issue issue    выпустить (пропуск, если действующий сертификат уже покрывает все имена и годен
#                           ещё > EDGE_RENEW_DAYS), установить в /etc/max-studio/edge/ и раздать хостам
#                           (--reloadcmd → max-edge-distribute push; он же срабатывает при каждом продлении)
#   max-edge-issue renew    плановое продление (acme.sh --cron) — вызывает max-edge-renew.timer раз в сутки
#   max-edge-issue timer    поставить/обновить systemd-таймер продления
#   max-edge-issue status   срок действия, SAN, дни до конца; код 1, если осталось < EDGE_WARN_DAYS
#
# Секреты: /etc/max-studio/edge/regru.env (root, 0600) — REGRU_API_Username / REGRU_API_Password; кладёт
# владелец или edge.sh creds, в репозиторий не попадают. Ключ, сертификат, аккаунт acme.sh — root, 0600/0700.
# Параметры: /etc/max-studio/edge/edge.env (пишет edge.sh install); без него действуют значения ниже.
set -euo pipefail
umask 077

EDGE_DIR=/etc/max-studio/edge
EDGE_ENV="$EDGE_DIR/edge.env"
# shellcheck disable=SC1090
[ -f "$EDGE_ENV" ] && . "$EDGE_ENV"
: "${EDGE_DOMAIN:=yleum.ru}"
# Первое имя — «главное» для acme.sh (имя записи в его хранилище); остальные — SAN того же сертификата.
: "${EDGE_NAMES:=$EDGE_DOMAIN *.$EDGE_DOMAIN *.apps.$EDGE_DOMAIN *.dev.$EDGE_DOMAIN *.dev2.$EDGE_DOMAIN}"
: "${EDGE_ACME_EMAIL:=admin@$EDGE_DOMAIN}"
: "${EDGE_ACME_SERVER:=letsencrypt}"          # letsencrypt_test — для репетиции без расхода лимитов
: "${EDGE_ACME_HOME:=$EDGE_DIR/acme-home}"     # аккаунт, ключи, конфиги acme.sh (root, 0700)
: "${EDGE_ACME_SH_DIR:=/usr/local/lib/max-edge/acme.sh}"
: "${EDGE_ACME_SH_REF:=master}"                # тег или ветка acme.sh; master — как ставился acme.sh оператора
: "${EDGE_RENEW_DAYS:=30}"                     # выпустить заново, если годен меньше
: "${EDGE_WARN_DAYS:=20}"                      # status → код 1, если годен меньше
: "${EDGE_DNS_SLEEP:=}"                        # пусто = acme.sh сам ждёт появления TXT; иначе секунды слепого ожидания
: "${EDGE_DISTRIBUTE:=/usr/local/sbin/max-edge-distribute}"
KEY="$EDGE_DIR/wildcard.key"
FULLCHAIN="$EDGE_DIR/wildcard.fullchain.pem"
CERT="$EDGE_DIR/wildcard.cert.pem"
CA="$EDGE_DIR/wildcard.ca.pem"

log() { printf '[%s] edge: %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { echo "max-edge-issue: $*" >&2; exit 1; }
need_root() { [ "$(id -u)" = 0 ] || die "нужен root"; }

load_creds() {
  [ -f "$EDGE_DIR/regru.env" ] || die "нет $EDGE_DIR/regru.env (REGRU_API_Username / REGRU_API_Password) — положить через edge.sh creds"
  chmod 600 "$EDGE_DIR/regru.env"
  set -a
  # shellcheck disable=SC1091
  . "$EDGE_DIR/regru.env"
  set +a
  [ -n "${REGRU_API_Username:-}" ] && [ -n "${REGRU_API_Password:-}" ] || die "$EDGE_DIR/regru.env неполный"
  export REGRU_API_Username REGRU_API_Password
}

ensure_acme() {
  command -v git >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends git >/dev/null
  if [ ! -x "$EDGE_ACME_SH_DIR/acme.sh" ]; then
    install -d -m 755 "$(dirname "$EDGE_ACME_SH_DIR")"
    git clone -q --depth 1 --branch "$EDGE_ACME_SH_REF" https://github.com/acmesh-official/acme.sh.git "$EDGE_ACME_SH_DIR"
  fi
  [ -f "$EDGE_ACME_SH_DIR/dnsapi/dns_regru.sh" ] || die "в $EDGE_ACME_SH_DIR нет dnsapi/dns_regru.sh"
  install -d -m 700 "$EDGE_DIR" "$EDGE_ACME_HOME"
}

# acme.sh считает LOG_LEVEL/DEBUG числами и падает на строковых значениях (см. nginx_writer.py) — вычищаем.
acme() { env -u LOG_LEVEL -u DEBUG "$EDGE_ACME_SH_DIR/acme.sh" --home "$EDGE_ACME_HOME" "$@"; }

cert_sans() { openssl x509 -in "$1" -noout -ext subjectAltName 2>/dev/null | tr ',' '\n' | sed -n 's/^ *DNS://p' | sort; }
wanted_sans() { printf '%s\n' $EDGE_NAMES | sort; }
cert_days_left() {
  local end; end=$(openssl x509 -in "$1" -noout -enddate | cut -d= -f2)
  echo $(( ( $(date -d "$end" +%s) - $(date +%s) ) / 86400 ))
}
cert_covers() { [ -s "$1" ] && [ "$(cert_sans "$1")" = "$(wanted_sans)" ]; }

verify_installed() {
  [ -s "$FULLCHAIN" ] && [ -s "$KEY" ] || die "файлы сертификата не установлены в $EDGE_DIR"
  cert_covers "$FULLCHAIN" || die "SAN сертификата не совпадает с EDGE_NAMES: $(cert_sans "$FULLCHAIN" | tr '\n' ' ')"
  [ "$(openssl x509 -in "$FULLCHAIN" -noout -pubkey)" = "$(openssl pkey -in "$KEY" -pubout)" ] || die "ключ не соответствует сертификату"
  chmod 600 "$KEY" "$FULLCHAIN" "$CERT" "$CA"
}

do_issue() {
  need_root; load_creds; ensure_acme
  local first; first=$(printf '%s\n' $EDGE_NAMES | head -1)
  # Пропуск только если и установленный файл, и хранилище acme.sh (откуда --install-cert берёт файлы) в порядке.
  if cert_covers "$FULLCHAIN" && [ "$(cert_days_left "$FULLCHAIN")" -gt "$EDGE_RENEW_DAYS" ] \
     && [ -s "$EDGE_ACME_HOME/${first}_ecc/fullchain.cer" ]; then
    log "действующий сертификат покрывает все имена и годен ещё $(cert_days_left "$FULLCHAIN") дн. — выпуск не нужен"
  else
    acme --register-account -m "$EDGE_ACME_EMAIL" --server "$EDGE_ACME_SERVER" >/dev/null
    local args=() name rc=0
    for name in $EDGE_NAMES; do args+=(-d "$name"); done
    [ -n "$EDGE_DNS_SLEEP" ] && args+=(--dnssleep "$EDGE_DNS_SLEEP")
    log "выпуск через DNS-01 reg.ru: $EDGE_NAMES"
    # acme.sh сам решает: имена изменились → новый выпуск; не изменились и срок не подошёл → код 2 («ещё рано»),
    # тогда ниже просто переустанавливаем файлы из его хранилища.
    set +e
    acme --issue --dns dns_regru "${args[@]}" --keylength ec-256 --server "$EDGE_ACME_SERVER"
    rc=$?
    set -e
    case $rc in
      0) log "сертификат выпущен" ;;
      2) log "acme.sh: действующий сертификат ещё не требует продления" ;;
      *) die "acme.sh --issue завершился с кодом $rc (см. вывод выше; DNS API reg.ru: доступ включён? IP core в белом списке?)" ;;
    esac
  fi
  # Файлы создаём заранее с 0600: acme.sh пишет в них cat'ом, права сохраняются.
  local f; for f in "$KEY" "$FULLCHAIN" "$CERT" "$CA"; do [ -e "$f" ] || install -m 600 /dev/null "$f"; done
  # --reloadcmd запоминается acme.sh и выполняется при каждом продлении: свежий сертификат уезжает на все хосты сам.
  local install_rc=0
  set +e
  acme --install-cert -d "$first" --ecc \
    --key-file "$KEY" --fullchain-file "$FULLCHAIN" --cert-file "$CERT" --ca-file "$CA" \
    --reloadcmd "$EDGE_DISTRIBUTE push"
  install_rc=$?
  set -e
  verify_installed
  log "установлено: $FULLCHAIN (до $(openssl x509 -in "$FULLCHAIN" -noout -enddate | cut -d= -f2))"
  do_timer
  if [ "$install_rc" -ne 0 ]; then
    die "сертификат установлен, но раздача ($EDGE_DISTRIBUTE push) вернула ошибку — см. выше; повторить: max-edge-distribute push"
  fi
}

do_renew() {
  need_root
  # Таймер ставится вместе со скриптами (edge.sh install) — до первого выпуска ему нечего делать.
  [ -s "$FULLCHAIN" ] || { log "сертификат ещё не выпущен — продлевать нечего (max-edge-issue issue)"; exit 0; }
  load_creds; ensure_acme
  # Продлевает только то, что подошло по сроку (60 дней с выпуска); после успеха acme.sh сам зовёт reloadcmd.
  acme --cron >/dev/null || die "acme.sh --cron завершился с ошибкой"
  do_status
}

do_timer() {
  need_root
  cat > /etc/systemd/system/max-edge-renew.service <<EOF
# generated by infra/max-k3s/edge/10-wildcard-issue.sh — продление wildcard-сертификата edge
[Unit]
Description=MAX Studio edge: renew the wildcard certificate (acme.sh, DNS-01 reg.ru)
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
  [ -s "$FULLCHAIN" ] || { echo "edge: сертификата нет ($FULLCHAIN) — max-edge-issue issue"; exit 1; }
  local days; days=$(cert_days_left "$FULLCHAIN")
  echo "edge: $(openssl x509 -in "$FULLCHAIN" -noout -subject -issuer -enddate | tr '\n' ' ')"
  echo "edge: SAN: $(cert_sans "$FULLCHAIN" | tr '\n' ' ')"
  echo "edge: осталось $days дн. (порог предупреждения $EDGE_WARN_DAYS, переиздание $EDGE_RENEW_DAYS)"
  [ -f "$EDGE_DIR/last-push" ] && echo "edge: последняя раздача: $(cat "$EDGE_DIR/last-push")"
  echo "edge: таймер: $(systemctl is-enabled max-edge-renew.timer 2>/dev/null || echo 'не установлен')"
  if [ "$days" -lt "$EDGE_WARN_DAYS" ]; then echo "edge: ВНИМАНИЕ — меньше $EDGE_WARN_DAYS дн., проверить журнал max-edge-renew.service"; exit 1; fi
}

case "${1:-issue}" in
  issue) do_issue ;;
  renew) do_renew ;;
  timer) do_timer ;;
  status) do_status ;;
  *) sed -n '2,15p' "$0"; exit 1 ;;
esac
