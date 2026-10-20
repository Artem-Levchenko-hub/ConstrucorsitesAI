#!/usr/bin/env bash
# 80-uptime-watchdog.sh — сторож доступности платформы. Выполняется НА runtime или commerce от root:
#   ssh max-runtime sudo bash -s -- install < этот файл      # таймер каждые 5 минут
#   ssh max-runtime sudo bash -s -- probe   < этот файл      # одна проверка вручную (с выводом)
#   ssh max-runtime sudo bash -s -- status  < этот файл
#
# Зачем: 24.09.2026 core (yleum.ru) лежал шесть часов, пока владелец сам не заметил — плановая
# дымовая проверка в GitHub (cron */5) за это время не запускалась ни разу (GitHub задерживает и
# пропускает частые расписания). Сторож живёт на НАШЕМ соседнем сервере, в той же сети провайдера:
# каждые 5 минут спрашивает yleum.ru, после трёх провалов подряд шлёт одно сообщение, после
# восстановления — ещё одно. Ставится на runtime и commerce обоих сразу — два независимых свидетеля.
#
# Куда слать: /etc/max-studio/watchdog.env (root, 0600):
#   TG_BOT_TOKEN=123456:ABC…   TG_CHAT_ID=-100…      # Telegram-бот владельца
# Без этих переменных сторож только пишет в журнал (journalctl -t max-watchdog) — механизм работает,
# оповещение включается добавлением двух строк, без переустановки.
set -euo pipefail
STEP=${1:-status}
ENV_FILE=/etc/max-studio/watchdog.env
BIN=/usr/local/sbin/max-watchdog.sh
STATE_DIR=/var/lib/max-watchdog
log() { printf '\n\033[1;34m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
[ "$(id -u)" = 0 ] || { echo "запускать от root (sudo)"; exit 1; }

install_files() {
  install -d -m 711 /etc/max-studio
  install -d -m 700 "$STATE_DIR"
  [ -f "$ENV_FILE" ] || { printf '# Оповещения сторожа доступности (max-watchdog).\n#TG_BOT_TOKEN=\n#TG_CHAT_ID=\n#WATCHDOG_BACKUP_READ_TOKEN=\n' > "$ENV_FILE"; chmod 600 "$ENV_FILE"; }
  cat > "$BIN" <<'EOF'
#!/usr/bin/env bash
# max-watchdog.sh — одна проверка платформы; вызывается таймером max-watchdog.timer каждые 5 минут.
# Состояние (счётчик провалов, момент первого провала, флаг «сообщение отправлено») — в /var/lib/max-watchdog.
set -uo pipefail
ENV_FILE=${WATCHDOG_ENV_FILE:-/etc/max-studio/watchdog.env}
STATE_DIR=${WATCHDOG_STATE_DIR:-/var/lib/max-watchdog}
# shellcheck disable=SC1090
[ ! -f "$ENV_FILE" ] || . "$ENV_FILE"
# /api/health — здоровье платформы (JSON, 200 только когда все проверки ok); /api/omnia/health — это
# путь MAX-приложений (канарейка), на самой платформе его нет (404).
URL=${WATCHDOG_URL:-https://yleum.ru/api/health}
WEB_URL=${WATCHDOG_WEB_URL:-https://yleum.ru/}
THRESHOLD=${WATCHDOG_THRESHOLD:-3}
HOST_TAG=$(hostname -s)
umask 077
mkdir -p "$STATE_DIR"
exec 9>"$STATE_DIR/lock"
flock -n 9 || { logger -t max-watchdog -- "probe skipped: another check holds the lock"; exit 75; }
case "$THRESHOLD" in ''|*[!0-9]*|0) logger -t max-watchdog -- "invalid threshold"; exit 1 ;; esac
THRESHOLD=$((10#$THRESHOLD))
[ "$THRESHOLD" -gt 0 ] || exit 1
FAILS_F=$STATE_DIR/fails; SINCE_F=$STATE_DIR/since; ALERTED_F=$STATE_DIR/alerted
fails=$(cat "$FAILS_F" 2>/dev/null || echo 0)
case "$fails" in ''|*[!0-9]*) fails=0 ;; esac
fails=$((10#$fails))

notify() { # notify <text>
  logger -t max-watchdog -- "$1"
  if [ -n "${TG_BOT_TOKEN:-}" ] && [ -n "${TG_CHAT_ID:-}" ]; then
    local reply="$STATE_DIR/notification-reply.json" delivered=0
    if curl --fail --silent --max-time 15 --output "$reply" \
      "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT_ID}" --data-urlencode "text=$1" 2>/dev/null; then
      if python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("ok") is True else 1)' "$reply" >/dev/null 2>&1; then
        delivered=1
      fi
    fi
    rm -f "$reply"
    [ "$delivered" = 1 ] && return 0
    logger -t max-watchdog -- "telegram delivery failed; retry remains pending"
  else
    logger -t max-watchdog -- "no TG_BOT_TOKEN/TG_CHAT_ID in $ENV_FILE — alert only in journal"
  fi
  return 1
}

probe() { # probe <url> → http code or 000
  local code
  code=$(curl -s -m 15 -o /dev/null -w '%{http_code}' "$1" 2>/dev/null) || code=000
  printf '%s' "$code"
}

probe_backup() {
  local reply="$STATE_DIR/backup-status.json" healthy=0
  if [ -n "${WATCHDOG_BACKUP_READ_TOKEN:-}" ] && curl --fail --silent --max-time 30 \
    --header "X-Offhost-Backup-Token: ${WATCHDOG_BACKUP_READ_TOKEN}" \
    --output "$reply" "${WATCHDOG_BACKUP_STATUS_URL:-https://yleum.ru/api/backups/offhost/status}" 2>/dev/null; then
    if python3 - "$reply" <<'PY'
import datetime,json,sys
data=json.load(open(sys.argv[1]))
now=datetime.datetime.now(datetime.timezone.utc)
created=datetime.datetime.fromisoformat(data['created_at'].replace('Z','+00:00'))
tested=datetime.datetime.fromisoformat(data['restore_tested_at'].replace('Z','+00:00'))
valid=(created.tzinfo is not None and tested.tzinfo is not None
       and data['restore_test_ok'] is True
       and -300 <= (now-created).total_seconds() <= 108000
       and -300 <= (now-tested).total_seconds() <= 604800)
raise SystemExit(0 if valid else 1)
PY
    then healthy=1; fi
  fi
  rm -f "$reply"
  [ "$healthy" = 1 ]
}

api=$(probe "$URL"); web=$(probe "$WEB_URL")
backup=failed
if probe_backup 2>/dev/null; then backup=ok; fi
if [ "$api" = 200 ] && [ "$web" = 200 ] && [ "$backup" = ok ]; then
  if [ -f "$ALERTED_F" ]; then
    since=$(cat "$SINCE_F" 2>/dev/null || date +%s)
    mins=$(( ( $(date +%s) - since ) / 60 ))
    notify "🟢 yleum.ru и резервная копия исправны (инцидент около ${mins} мин; сторож: ${HOST_TAG})" || exit 1
  fi
  rm -f "$FAILS_F" "$SINCE_F" "$ALERTED_F"
  logger -t max-watchdog -- "ok api=${api} web=${web} backup=${backup}"
  exit 0
fi
fails=$((fails + 1)); echo "$fails" > "$FAILS_F"
[ -f "$SINCE_F" ] || date +%s > "$SINCE_F"
logger -t max-watchdog -- "FAIL #${fails} api=${api} web=${web} backup=${backup}"
if [ "$fails" -ge "$THRESHOLD" ] && [ ! -f "$ALERTED_F" ]; then
  if notify "🔴 Yleum: api=${api}, web=${web}, backup=${backup}, ${fails} проверок подряд (~$((fails * 5)) мин). Сторож: ${HOST_TAG}"; then
    touch "$ALERTED_F"
  fi
fi
exit 1
EOF
  chmod 755 "$BIN"
  cat > /etc/systemd/system/max-watchdog.service <<EOF
[Unit]
Description=MAX Studio: platform availability probe (yleum.ru)
After=network-online.target

[Service]
Type=oneshot
ExecStart=$BIN
EOF
  cat > /etc/systemd/system/max-watchdog.timer <<'EOF'
[Unit]
Description=MAX Studio: platform availability probe every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now max-watchdog.timer >/dev/null 2>&1
}

case "$STEP" in
  install)
    log "сторож доступности: файлы, таймер каждые 5 минут"
    install_files
    first_status=0
    "$BIN" && echo "  первая проверка: ok" || { first_status=$?; echo "  первая проверка: FAIL (см. journalctl -t max-watchdog)"; }
    systemctl list-timers max-watchdog.timer --no-pager | sed -n 2p | cut -c1-100
    grep -q '^TG_BOT_TOKEN=.' "$ENV_FILE" && echo "  оповещения: Telegram включён" || echo "  оповещения: только журнал (заполните $ENV_FILE: TG_BOT_TOKEN, TG_CHAT_ID)"
    exit "$first_status"
    ;;
  probe)
    [ -x "$BIN" ] || { echo "сначала install"; exit 1; }
    probe_status=0
    "$BIN" && echo "probe: ok" || { probe_status=$?; echo "probe: FAIL"; }
    journalctl -t max-watchdog -n 3 --no-pager -o cat
    exit "$probe_status"
    ;;
  status)
    systemctl list-timers max-watchdog.timer --no-pager 2>/dev/null | sed -n 2p | cut -c1-100
    echo "fails=$(cat $STATE_DIR/fails 2>/dev/null || echo 0) alerted=$([ -f $STATE_DIR/alerted ] && echo yes || echo no)"
    journalctl -t max-watchdog -n 5 --no-pager -o cat 2>/dev/null
    ;;
  *) sed -n 2,16p "$0"; exit 1 ;;
esac
