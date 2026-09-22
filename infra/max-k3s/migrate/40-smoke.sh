#!/usr/bin/env bash
# Проверка платформы после переезда (запускать с Mac): здоровье публичных точек + сквозной сценарий
# «регистрация → проект → запуск ячейки → генерация → превью по своему https-имени».
#   ./40-smoke.sh [https://yleum.ru] [минут ожидания генерации]
set -uo pipefail
ORIGIN=${1:-https://yleum.ru}; WAIT_MIN=${2:-25}
J=$(mktemp); TS=$(date +%s)
jf() { python3 -c "import sys,json; d=json.load(sys.stdin); print(d$1)" 2>/dev/null; }

echo "== health $ORIGIN"
for p in / /api/health /otchet/ /minio/previews/; do
  printf "  %-18s %s\n" "$p" "$(curl -s -o /dev/null -w '%{http_code} ssl=%{ssl_verify_result} %{time_total}s' -m 30 "$ORIGIN$p")"
done

EMAIL="smoke-$TS@yleum.ru"; PW="Smoke-$TS-Pass"
echo "== register $EMAIL"
curl -s -c "$J" -b "$J" -m 30 -H 'Content-Type: application/json' -d "{\"email\":\"$EMAIL\",\"password\":\"$PW\"}" "$ORIGIN/api/auth/register" | head -c 200; echo
echo "== me: $(curl -s -b "$J" -m 30 "$ORIGIN/api/auth/me" | head -c 160)"

echo "== create project"
P=$(curl -s -b "$J" -m 30 -H 'Content-Type: application/json' -d '{"name":"Smoke после переезда","template":"max_miniapp"}' "$ORIGIN/api/projects")
PID=$(echo "$P" | jf '["id"]'); SLUG=$(echo "$P" | jf '["slug"]')
[ -n "$PID" ] || { echo "  не создался: $(echo "$P" | head -c 300)"; exit 1; }
echo "  id=$PID slug=$SLUG"

echo "== runtime/start"
curl -s -b "$J" -m 120 -X POST "$ORIGIN/api/projects/$PID/runtime/start" | head -c 300; echo

echo "== prompt"
curl -s -b "$J" -m 60 -H 'Content-Type: application/json' \
  -d '{"prompt":"Сделай мини-приложение «Список дел»: добавление задачи, отметка выполнения, удаление. Данные хранить в базе.","skip_clarify":true}' \
  "$ORIGIN/api/projects/$PID/prompt" | head -c 300; echo

echo "== ожидание генерации (до $WAIT_MIN мин)"
DEADLINE=$(( $(date +%s) + WAIT_MIN*60 ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  R=$(curl -s -b "$J" -m 30 "$ORIGIN/api/projects/$PID/runtime")
  M=$(curl -s -b "$J" -m 30 "$ORIGIN/api/projects/$PID/messages" | python3 -c "
import sys,json
try:
    ms=json.load(sys.stdin); a=[m for m in ms if m.get('role')=='assistant']
    print((a[-1].get('status') or a[-1].get('state') or '?') if a else 'no-assistant-msg', len(ms))
except Exception as e: print('?')" 2>/dev/null)
  printf "  %s runtime=%s | messages=%s\n" "$(date +%H:%M:%S)" "$(echo "$R" | python3 -c 'import sys,json; d=json.load(sys.stdin); print({k:d[k] for k in d if k in ("state","status","url","preview_url","dev_url","phase")})' 2>/dev/null | head -c 200)" "$M"
  echo "$M" | grep -qE "^(done|completed|complete|succeeded|ready|failed|error)" && break
  sleep 45
done

echo "== итог"
PR=$(curl -s -b "$J" -m 30 "$ORIGIN/api/projects/$PID")
echo "  project: $(echo "$PR" | python3 -c 'import sys,json; d=json.load(sys.stdin); print({k:d.get(k) for k in ("slug","status","preview_url","updated_at")})' 2>/dev/null)"
PREV=$(echo "$PR" | jf '.get("preview_url") or ""')
if [ -n "$PREV" ]; then
  printf "  preview %s → %s\n" "$PREV" "$(curl -s -o /dev/null -w '%{http_code} ssl=%{ssl_verify_result}' -m 30 "$PREV/")"
fi
echo "  (проект $PID оставлен для осмотра; удалить: DELETE $ORIGIN/api/projects/$PID)"
rm -f "$J"
