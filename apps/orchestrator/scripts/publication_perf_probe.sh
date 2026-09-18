#!/bin/bash
# Publication performance probe (plan P00/P01). Runs ON the production host as
# the deploy user; measures ONE publication of a QA-only project:
#   docker events + 1 s HTTPS probe + POST /deploy + 1 s polling of GET /deploy.
# Usage: publication_perf_probe.sh <label> [project_id] [public_host]
# Refuses any project that is not listed under qa_projects in
# docs/operations/publication-performance-baseline.json (pass QA_PROJECTS to override
# the allowlist when the repo checkout is not next to the script).
set -u
LABEL=${1:-run}
P=${2:-b958d03c-38c0-4374-8a47-3f06cb721096}
HOST=${3:-klienty-otkaty-qa-f631c0.preview.lead-generator.ru}
ALLOW=${QA_PROJECTS:-b958d03c-38c0-4374-8a47-3f06cb721096}
case " $ALLOW " in *" $P "*) ;; *) echo "refusing: $P is not a QA measurement project" >&2; exit 2;; esac
OUT=/tmp/pubperf/$LABEL-$(date +%H%M%S); mkdir -p "$OUT"
q() { printf '%s\n' "$1" > /tmp/.q.sql; sudo docker exec -i omnia-prod-postgres sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB -At' < /tmp/.q.sql; }
OWNER=$(q "select owner_id from projects where id='$P'")
T=$(sudo docker exec omnia-prod-api /app/.venv/bin/python -c "from omnia_api.core.security import create_access_token; from uuid import UUID; print(create_access_token(UUID('$OWNER')))")
H="Authorization: Bearer $T"; A=http://127.0.0.1:8200/api/projects/$P
echo "deploy before: $(curl -s -H "$H" $A/deploy | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('phase'), d.get('snapshot_id'), 'format', d.get('format_version'))")"
echo "head: $(curl -s -H "$H" $A | python3 -c "import json,sys; print(json.load(sys.stdin)['current_snapshot_id'])")"
sudo docker events --format '{{.Time}} {{.Type}} {{.Action}} {{.Actor.Attributes.name}} {{.Actor.ID}}' > "$OUT/docker-events.log" 2>&1 &
EV=$!
( while true; do printf '%s %s\n' "$(date +%s)" "$(curl -s -o /dev/null -m 3 -w '%{http_code} %{time_total}' https://$HOST/)"; sleep 1; done ) > "$OUT/public-probe.log" 2>&1 &
PR=$!
sleep 2
T0=$(date +%s)
R=$(curl -s -H "$H" -H "Content-Type: application/json" -X POST $A/deploy -d "{\"idempotency_key\":\"qa-perf-$LABEL-$T0\"}")
RUN=$(echo "$R" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('run_id') or '')" 2>/dev/null)
echo "submit: $(echo "$R" | cut -c1-220)"
S="$R"
if [ -n "$RUN" ]; then
  LAST=""
  for i in $(seq 1 450); do
    S=$(curl -s -H "$H" $A/deploy)
    PH=$(echo "$S" | python3 -c "
import json,sys; d=json.load(sys.stdin); p=d.get('progress') or {}
print(d.get('phase'), '|', d.get('stage'), '| hb', (d.get('heartbeat_at') or '')[11:19], '| bytes', p.get('bytes_done'), '/', p.get('bytes_total'), '| files', p.get('files_done'))" 2>/dev/null)
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
echo "== final"; python3 -c "
import json; d=json.load(open('$OUT/deploy-final.json'))
print(d.get('phase'), 'format', d.get('format_version'), 'error', d.get('error'), d.get('error_stage'), d.get('reason_code'))
print('metrics:', json.dumps(d.get('metrics'), sort_keys=True))
for s in d.get('stages') or []:
    print(f\"  {s['stage']:<18} {s.get('elapsed_ms'):>7} ms  bytes={s.get('bytes_done')}/{s.get('bytes_total')}\")
print('logs:', d.get('logs'))"
echo "== dev/prod lifecycle (relative s)"; grep -E " container (stop|start|die|destroy|create) " "$OUT/docker-events.log" | grep -E "(c0303feb|b84ba899)[0-9a-f]*-(dev|project-postgres|gateway)" | awk -v t0=$T0 '{print $1-t0"s", $3, $4}' | sed -E 's/omnia-machine-//; s/[0-9a-f]{32}-//' | head -40
echo "== public probe"; awk '{c[$2]++} END {for (k in c) print k, c[k]}' "$OUT/public-probe.log"; awk -v t0=$T0 '$2!="200"{print $1-t0"s", $2}' "$OUT/public-probe.log" | sed -n '1p;$p'
echo "window: $((T1-T0)) s out=$OUT"
rm -f /tmp/.q.sql
