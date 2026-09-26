#!/bin/bash
# Замер одной публикации (план P00/P01). Запускается НА боевом хосте core от
# пользователя выкатки и измеряет ровно одну публикацию проекта, отведённого под
# замеры.
#
#   publication_perf_probe.sh <метка> <project_id> <публичный хост>
#
# Что меряется: длительности стадий из самой платформы (они durable, их пишет
# publication_trace), недоступность публичного адреса по секундной пробе и общее
# окно. Стадии — главное: по ним видно, ЧТО именно долго, а не только «долго».
#
# Обновлено 26.09.2026 под текущую инфраструктуру:
#   * база платформы живёт на ХОСТЕ (10.10.0.1), а не в контейнере
#     omnia-prod-postgres — тот остановлен и держит только том отката;
#   * контейнеры называются yleum-prod-*;
#   * опубликованные приложения уезжают в кластер runtime (PUBLICATION_BACKEND=
#     kubernetes), поэтому события docker на core показывают только исходную
#     ячейку. Раздел про жизненный цикл контейнеров оставлен для размещения в
#     docker и молчит при размещении в кубернетесе — это не ошибка.
#
# Предохранитель: публиковать можно ТОЛЬКО проект из списка замеров. Публикация
# чужого проекта — это вмешательство в работу живого владельца, а не измерение.
set -u

LABEL=${1:?метка замера, например warm-1}
P=${2:?project_id проекта для замеров}
HOST=${3:?публичный хост проекта, например slug.apps.yleum.ru}
ALLOW=${QA_PROJECTS:-}
API=${API_CONTAINER:-yleum-prod-api}

if [ -z "$ALLOW" ]; then
  echo "отказ: список проектов для замеров пуст." >&2
  echo "Задайте QA_PROJECTS='<id> <id>' — публиковать чужой проект нельзя." >&2
  exit 2
fi
case " $ALLOW " in
  *" $P "*) ;;
  *) echo "отказ: $P не в списке проектов для замеров (QA_PROJECTS)" >&2; exit 2;;
esac

OUT=/tmp/pubperf/$LABEL-$(date +%H%M%S); mkdir -p "$OUT"

q() { sudo -u postgres psql -d omnia -At -c "$1"; }

OWNER=$(q "select owner_id from projects where id='$P'")
[ -n "$OWNER" ] || { echo "отказ: проект $P не найден в базе платформы" >&2; exit 2; }
T=$(sudo docker exec "$API" /app/.venv/bin/python -c \
  "from yleum_api.core.security import create_access_token; from uuid import UUID; print(create_access_token(UUID('$OWNER')))")
H="Authorization: Bearer $T"; A=http://127.0.0.1:8200/api/projects/$P

echo "до публикации: $(curl -s -H "$H" $A/deploy | python3 -c "
import json,sys; d=json.load(sys.stdin)
print(d.get('phase'), d.get('snapshot_id'), 'формат', d.get('format_version'))")"
echo "текущая версия: $(curl -s -H "$H" $A | python3 -c "import json,sys; print(json.load(sys.stdin)['current_snapshot_id'])")"

sudo docker events --format '{{.Time}} {{.Type}} {{.Action}} {{.Actor.Attributes.name}}' > "$OUT/docker-events.log" 2>&1 &
EV=$!
( while true; do printf '%s %s\n' "$(date +%s)" "$(curl -s -o /dev/null -m 3 -w '%{http_code} %{time_total}' "https://$HOST/")"; sleep 1; done ) > "$OUT/public-probe.log" 2>&1 &
PR=$!
sleep 2
T0=$(date +%s)
R=$(curl -s -H "$H" -H "Content-Type: application/json" -X POST $A/deploy -d "{\"idempotency_key\":\"perf-$LABEL-$T0\"}")
RUN=$(echo "$R" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('run_id') or '')" 2>/dev/null)
echo "заявка: $(echo "$R" | cut -c1-220)"
S="$R"
if [ -n "$RUN" ]; then
  LAST=""
  for _ in $(seq 1 450); do
    S=$(curl -s -H "$H" $A/deploy)
    PH=$(echo "$S" | python3 -c "
import json,sys; d=json.load(sys.stdin); p=d.get('progress') or {}
print(d.get('phase'), '|', d.get('stage'), '| пульс', (d.get('heartbeat_at') or '')[11:19],
      '| байт', p.get('bytes_done'), '/', p.get('bytes_total'), '| файлов', p.get('files_done'))" 2>/dev/null)
    KEY=$(echo "$PH" | cut -d'|' -f1,2)
    if [ "$KEY" != "$LAST" ]; then echo "  +$(( $(date +%s) - T0 ))s $PH"; LAST=$KEY; fi
    case "$PH" in done*|failed*|cancelled*) break;; esac
    sleep 1
  done
fi
echo "$S" > "$OUT/deploy-final.json"
sleep 3
sudo kill $EV 2>/dev/null; kill $PR 2>/dev/null; wait 2>/dev/null
T1=$(date +%s)

echo "== итог"
python3 -c "
import json
d = json.load(open('$OUT/deploy-final.json'))
print(d.get('phase'), 'формат', d.get('format_version'), 'ошибка', d.get('error'), d.get('error_stage'), d.get('reason_code'))
print('метрики:', json.dumps(d.get('metrics'), sort_keys=True, ensure_ascii=False))
for s in d.get('stages') or []:
    print(f\"  {s['stage']:<18} {s.get('elapsed_ms'):>7} мс  байт={s.get('bytes_done')}/{s.get('bytes_total')}\")
print('журналы:', d.get('logs'))"

echo "== жизненный цикл контейнеров исходной ячейки (относительные секунды)"
echo "   (при размещении опубликованного приложения в кубернетесе здесь пусто — так и должно быть)"
grep -E " container (stop|start|die|destroy|create) " "$OUT/docker-events.log" 2>/dev/null \
  | awk -v t0=$T0 '{print $1-t0"s", $3, $4}' | sed -E 's/omnia-machine-//; s/[0-9a-f]{32}-//' | head -40

echo "== публичная проба"
awk '{c[$2]++} END {for (k in c) print k, c[k]}' "$OUT/public-probe.log"
awk -v t0=$T0 '$2!="200"{print $1-t0"s", $2}' "$OUT/public-probe.log" | sed -n '1p;$p'
echo "окно: $((T1-T0)) с, вывод в $OUT"
