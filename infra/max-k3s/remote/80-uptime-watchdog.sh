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
  install -d -m 711 /etc/max-studio "$STATE_DIR"
  [ -f "$ENV_FILE" ] || { printf '# Оповещения сторожа доступности (max-watchdog). Заполнить и ничего не перезапускать.\n#TG_BOT_TOKEN=\n#TG_CHAT_ID=\n' > "$ENV_FILE"; chmod 600 "$ENV_FILE"; }
  cat > "$BIN" <<'EOF'
#!/usr/bin/env bash
# max-watchdog.sh — одна проверка платформы; вызывается таймером max-watchdog.timer каждые 5 минут.
# Состояние (счётчик провалов, момент первого провала, флаг «сообщение отправлено») — в /var/lib/max-watchdog.
set -uo pipefail
ENV_FILE=/etc/max-studio/watchdog.env
STATE_DIR=/var/lib/max-watchdog
# /api/health — здоровье платформы (JSON, 200 только когда все проверки ok); /api/omnia/health — это
# путь MAX-приложений (канарейка), на самой платформе его нет (404).
URL=${WATCHDOG_URL:-https://yleum.ru/api/health}
WEB_URL=${WATCHDOG_WEB_URL:-https://yleum.ru/}
THRESHOLD=${WATCHDOG_THRESHOLD:-3}
HOST_TAG=$(hostname -s)
# shellcheck disable=SC1090
[ -f "$ENV_FILE" ] && . "$ENV_FILE"
mkdir -p "$STATE_DIR"
FAILS_F=$STATE_DIR/fails; SINCE_F=$STATE_DIR/since; ALERTED_F=$STATE_DIR/alerted
fails=$(cat "$FAILS_F" 2>/dev/null || echo 0)

notify() { # notify <text>
  logger -t max-watchdog -- "$1"
  if [ -n "${TG_BOT_TOKEN:-}" ] && [ -n "${TG_CHAT_ID:-}" ]; then
    curl -sS -m 15 -o /dev/null -w '' "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT_ID}" --data-urlencode "text=$1" \
      || logger -t max-watchdog -- "telegram send failed"
  else
    logger -t max-watchdog -- "no TG_BOT_TOKEN/TG_CHAT_ID in $ENV_FILE — alert only in journal"
  fi
}

probe() { # probe <url> → http code or 000
  curl -s -m 15 -o /dev/null -w '%{http_code}' "$1" 2>/dev/null || echo 000
}

api=$(probe "$URL"); web=$(probe "$WEB_URL")
if [ "$api" = 200 ] && [ "$web" = 200 ]; then
  if [ -f "$ALERTED_F" ]; then
    since=$(cat "$SINCE_F" 2>/dev/null || date +%s)
    mins=$(( ( $(date +%s) - since ) / 60 ))
    notify "🟢 yleum.ru снова отвечает (простой около ${mins} мин; сторож: ${HOST_TAG})"
  fi
  rm -f "$FAILS_F" "$SINCE_F" "$ALERTED_F"
  logger -t max-watchdog -- "ok api=${api} web=${web}"
  exit 0
fi
fails=$((fails + 1)); echo "$fails" > "$FAILS_F"
[ -f "$SINCE_F" ] || date +%s > "$SINCE_F"
logger -t max-watchdog -- "FAIL #${fails} api=${api} web=${web}"
if [ "$fails" -ge "$THRESHOLD" ] && [ ! -f "$ALERTED_F" ]; then
  notify "🔴 yleum.ru не отвечает: api=${api} web=${web}, ${fails} проверок подряд (~$((fails * 5)) мин). Проверьте панель Serverum, VPS core 2.153.248.98. Сторож: ${HOST_TAG}"
  touch "$ALERTED_F"
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
    "$BIN" && echo "  первая проверка: ok" || echo "  первая проверка: FAIL (см. journalctl -t max-watchdog)"
    systemctl list-timers max-watchdog.timer --no-pager | sed -n 2p | cut -c1-100
    grep -q '^TG_BOT_TOKEN=.' "$ENV_FILE" && echo "  оповещения: Telegram включён" || echo "  оповещения: только журнал (заполните $ENV_FILE: TG_BOT_TOKEN, TG_CHAT_ID)"
    ;;
  probe)
    [ -x "$BIN" ] || { echo "сначала install"; exit 1; }
    "$BIN" && echo "probe: ok" || echo "probe: FAIL"
    journalctl -t max-watchdog -n 3 --no-pager -o cat
    ;;
  status)
    systemctl list-timers max-watchdog.timer --no-pager 2>/dev/null | sed -n 2p | cut -c1-100
    echo "fails=$(cat $STATE_DIR/fails 2>/dev/null || echo 0) alerted=$([ -f $STATE_DIR/alerted ] && echo yes || echo no)"
    journalctl -t max-watchdog -n 5 --no-pager -o cat 2>/dev/null
    ;;
  *) sed -n 2,16p "$0"; exit 1 ;;
esac
