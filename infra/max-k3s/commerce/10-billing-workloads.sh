#!/usr/bin/env bash
# Фаза 3 / commerce: биллинговый воркер платформы (продления подписок, льготный период, перевод на Free,
# сверка зависших заказов ЮKassa) в K3s-кластере commerce. Запускать с Mac (bash 3.2 ok).
#
#   ./10-billing-workloads.sh plan          # что будет сделано + отрисовка манифестов, ничего не меняет
#   ./10-billing-workloads.sh core-access   # core: роль max_billing на хостовом PostgreSQL (10.10.0.1:5432), pg_hba/ufw для commerce, env-файл
#   ./10-billing-workloads.sh host          # commerce: ufw pod-сеть → свой оркестратор :8003
#   ./10-billing-workloads.sh image         # core: omnia-api:prod → registry.yleum.ru/platform/omnia-api:<sha> (по digest)
#   ./10-billing-workloads.sh secret        # Secret billing-worker-env из /etc/max-studio/billing-worker.env (core, 0600) — по ssh, файл на Mac не создаётся
#   ./10-billing-workloads.sh apply         # namespace + Deployment + Service + NetworkPolicy (образ из шага image или IMAGE=…)
#   ./10-billing-workloads.sh verify        # rollout, GET /health воркера, dependencies.billing_worker в /api/health платформы
#   ./10-billing-workloads.sh handover      # core: BILLING_LIFECYCLE_ENABLED=false + пересоздать worker — тик остаётся только в commerce
#   ./10-billing-workloads.sh rollback      # обратно: воркер в 0 реплик, тик снова в RQ-воркере core
#   ./10-billing-workloads.sh all           # core-access → host → image → secret → apply → verify (handover — отдельно, осознанно)
#
# --dry-run (или DRY_RUN=1): печатает команды вместо выполнения; туннели и ssh не трогаются.
# Почему воркер отдельный процесс, а не поток RQ-воркера: apps/api/src/yleum_api/workers/billing.py и
# docs/plans/2026-09-23-phase3-commerce.md, раздел «Commerce-кластер».
set -euo pipefail
. "$(dirname "$0")/../lib.sh"
DRY_RUN=${DRY_RUN:-0}
ARGS=""
for a in "$@"; do
  case "$a" in
    --dry-run) DRY_RUN=1 ;;
    *) ARGS="$ARGS $a" ;;
  esac
done
# shellcheck disable=SC2086
set -- $ARGS

K8S_DIR="$HERE/k8s/commerce"
REMOTE="$HERE/commerce/remote"
NS=billing
CTX=max-commerce
# Порт хостового PostgreSQL core на WG-адресе (база платформы там с 23.09.2026, socat-форвард не нужен).
PG_PORT=${BILLING_PG_PORT:-5432}
REGISTRY_REPO=platform/omnia-api
FULL=/opt/omnia/apps/llm-gateway/deploy/full
export KUBECONFIG="$HOME/.kube/max-studio.yaml"

dry() { [ "$DRY_RUN" = 1 ]; }
run() { if dry; then printf '  [dry-run] %s\n' "$*"; else "$@"; fi; }
remote() { # remote <host> <script> [args...]
  local host=$1 script=$2; shift 2
  if dry; then printf '  [dry-run] ssh %s sudo bash -s -- %s < %s\n' "$(inv "$host" alias)" "$*" "${script#"$HERE/"}"; else run_remote "$host" "$script" "$@"; fi
}
remote_sh() { # remote_sh <host> <command>
  local host=$1; shift
  if dry; then printf '  [dry-run] ssh %s sudo bash -c %q\n' "$(inv "$host" alias)" "$*"; else sh_remote "$host" "$*"; fi
}
kube() { run kubectl --context "$CTX" "$@"; }
tunnel() { dry || "$HERE/kube-tunnel.sh" up >/dev/null; }

image_ref() {
  if [ -n "${IMAGE:-}" ]; then echo "$IMAGE"; return; fi
  if dry; then echo "registry.$DOMAIN/$REGISTRY_REPO@sha256:<digest из шага image>"; return; fi
  ssh -o BatchMode=yes "$(inv core alias)" "sudo sed -n 's/^IMAGE_REF=//p' /etc/max-studio/billing-image.ref" \
    || { echo "нет /etc/max-studio/billing-image.ref на core — сначала шаг image" >&2; exit 1; }
}
release_sha() {
  if dry; then echo "<sha>"; return; fi
  ssh -o BatchMode=yes "$(inv core alias)" "sudo sed -n 's/^RELEASE_SHA=//p' /etc/max-studio/billing-image.ref"
}
render() { # render <image> <sha> → манифесты воркера на stdout
  sed -e "s|__IMAGE__|$1|g" -e "s|__RELEASE_SHA__|$2|g" "$K8S_DIR/10-billing-worker.yaml"
  echo "---"
  cat "$K8S_DIR/20-networkpolicy.yaml"
}

phase_plan() {
  log "plan: биллинговый воркер → кластер commerce ($(inv commerce public), K3s ctx $CTX, namespace $NS)"
  cat <<EOF
  1. core-access  ssh $(inv core alias): хостовый PostgreSQL слушает $(inv core wg):$PG_PORT (pg_hba + ufw для
                  $(inv commerce wg) и $(inv commerce cluster_cidr)), роль max_billing (права только на биллинговые
                  таблицы), /etc/max-studio/billing-worker.env из .env платформы
  2. host         ssh $(inv commerce alias): ufw $(inv commerce cluster_cidr) → :8003 (поды → свой оркестратор)
  3. image        ssh $(inv core alias): docker tag omnia-api:prod registry.$DOMAIN/$REGISTRY_REPO:<sha> && docker push
  4. secret       ssh core cat billing-worker.env | kubectl -n $NS create secret generic billing-worker-env --from-env-file=/dev/stdin
  5. apply        kubectl apply: k8s/commerce/00-namespace.yaml, 10-billing-worker.yaml (образ по digest), 20-networkpolicy.yaml
  6. verify       rollout status; kubectl exec … curl :8090/health; curl https://$DOMAIN/api/health → dependencies.billing_worker
  7. handover     (отдельно) core: BILLING_LIFECYCLE_ENABLED=false в $FULL/.env, docker compose up -d worker
EOF
  log "plan: манифесты после подстановки (образ и sha — из шага image)"
  local rendered; rendered=$(render "registry.$DOMAIN/$REGISTRY_REPO@sha256:<digest>" "<sha>")
  if python3 -c 'import yaml' >/dev/null 2>&1; then
    { cat "$K8S_DIR/00-namespace.yaml"; echo "---"; echo "$rendered"; } | python3 -c '
import sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if d]
for d in docs: print("  ok:", d["kind"], d["metadata"]["name"])'
  else
    echo "  PyYAML не установлен — структура YAML не проверена (kubectl apply проверит на кластере)"
  fi
  if ! dry && command -v kubectl >/dev/null 2>&1 && "$HERE/kube-tunnel.sh" up >/dev/null 2>&1 \
     && kubectl --context "$CTX" get ns >/dev/null 2>&1; then
    { cat "$K8S_DIR/00-namespace.yaml"; echo "---"; echo "$rendered"; } \
      | kubectl --context "$CTX" apply --dry-run=server -f - | sed 's/^/  cluster: /'
  else
    echo "  кластер $CTX не опрашивался (dry-run или нет доступа) — серверная проверка манифестов пропущена"
  fi
  echo "$rendered" | grep -nE "image:|command:|secretRef|containerPort|cidr:" | sed 's/^/  /'
}

phase_core_access() {
  log "core-access → $(inv core alias): хостовый postgres (pg_hba, ufw), роль max_billing, env-файл воркера"
  remote core "$REMOTE/10-core-billing-access.sh" "$ADMIN_USER" "$(inv core wg)" "$(inv commerce wg)" "$(inv commerce cluster_cidr)" "$PG_PORT"
}

phase_host() {
  log "host → $(inv commerce alias): ufw для pod-сети → оркестратор"
  remote commerce "$REMOTE/30-commerce-host-billing.sh" "$(inv commerce cluster_cidr)"
}

phase_image() {
  log "image → $(inv core alias): omnia-api:prod → registry.$DOMAIN/$REGISTRY_REPO:<sha>"
  remote core "$REMOTE/20-core-push-api-image.sh" "registry.$DOMAIN" "$REGISTRY_REPO"
}

phase_secret() {
  log "secret → $CTX/$NS: billing-worker-env из /etc/max-studio/billing-worker.env (core), без файла на Mac"
  tunnel
  kube apply -f "$K8S_DIR/00-namespace.yaml"
  if dry; then
    printf '  [dry-run] ssh %s sudo cat /etc/max-studio/billing-worker.env | kubectl --context %s -n %s create secret generic billing-worker-env --from-env-file=/dev/stdin --dry-run=client -o yaml | kubectl --context %s apply -f -\n' "$(inv core alias)" "$CTX" "$NS" "$CTX"
    return
  fi
  ssh -o BatchMode=yes "$(inv core alias)" "sudo cat /etc/max-studio/billing-worker.env" \
    | kubectl --context "$CTX" -n "$NS" create secret generic billing-worker-env --from-env-file=/dev/stdin --dry-run=client -o yaml \
    | kubectl --context "$CTX" apply -f - | sed 's/^/  /'
  kubectl --context "$CTX" -n "$NS" get secret billing-worker-env -o jsonpath='{.data}' | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  ключей в Secret:", len(d), "| YOOKASSA_SHOP_ID:", "есть" if d.get("YOOKASSA_SHOP_ID") else "нет")'
  # новое содержимое Secret подхватывается только новым подом
  kubectl --context "$CTX" -n "$NS" rollout restart deploy/billing-worker >/dev/null 2>&1 && echo "  deploy/billing-worker перезапущен под новый Secret" || true
}

phase_apply() {
  local image sha; image=$(image_ref); sha=$(release_sha)
  log "apply → $CTX/$NS: образ $image (release $sha)"
  tunnel
  kube apply -f "$K8S_DIR/00-namespace.yaml"
  if dry; then
    printf '  [dry-run] render | kubectl --context %s apply -f -\n' "$CTX"
    render "$image" "$sha" | grep -nE "image:|omnia.release_sha" | sed 's/^/  /'
    return
  fi
  render "$image" "$sha" | kubectl --context "$CTX" apply -f - | sed 's/^/  /'
}

phase_verify() {
  log "verify → $CTX/$NS"
  tunnel
  kube -n "$NS" rollout status deploy/billing-worker --timeout=3m
  if dry; then
    printf '  [dry-run] kubectl --context %s -n %s exec deploy/billing-worker -- curl -fsS http://127.0.0.1:8090/health\n' "$CTX" "$NS"
    printf '  [dry-run] ssh %s curl -fsS https://%s/api/health → dependencies.billing_worker / billing_worker_release_sha\n' "$(inv core alias)" "$DOMAIN"
    return
  fi
  kubectl --context "$CTX" -n "$NS" get pods -o wide --no-headers | sed 's/^/  /'
  kubectl --context "$CTX" -n "$NS" exec deploy/billing-worker -- curl -fsS http://127.0.0.1:8090/health \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  worker /health:", d["status"], "| cycles:", d["cycles"], "| last_completed_at:", d["last_completed_at"], "| failures:", d["consecutive_failures"], "| last_error:", d["last_error"])'
  ssh -o BatchMode=yes "$(inv core alias)" "curl -fsS https://$DOMAIN/api/health || curl -sS https://$DOMAIN/api/health" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); dep=d.get("dependencies",{}); print("  platform /api/health:", d["status"], "| checks.worker:", d["checks"].get("worker"), "| billing_worker:", dep.get("billing_worker"), dep.get("billing_worker_release_sha"))'
}

phase_handover() {
  log "handover → core: тик только в commerce (BILLING_LIFECYCLE_ENABLED=false, пересоздать worker)"
  remote_sh core "cd $FULL && sudo -u $ADMIN_USER python3 - <<'PY'
import re
p='.env'; t=open(p).read()
t = re.sub(r'^BILLING_LIFECYCLE_ENABLED=.*$', 'BILLING_LIFECYCLE_ENABLED=false', t, flags=re.M) if re.search(r'^BILLING_LIFECYCLE_ENABLED=', t, re.M) else t.rstrip('\n')+'\nBILLING_LIFECYCLE_ENABLED=false\n'
open(p,'w').write(t); print('  .env: BILLING_LIFECYCLE_ENABLED=false')
PY
docker compose up -d --no-build worker >/dev/null 2>&1 && echo '  worker пересоздан'
sleep 20; curl -sS https://$DOMAIN/api/health | python3 -c 'import json,sys; d=json.load(sys.stdin); print(\"  /api/health:\", d[\"status\"], \"| worker:\", d[\"checks\"].get(\"worker\"), \"| billing_worker:\", d[\"dependencies\"].get(\"billing_worker\"))'"
  echo "  heartbeat биллинга (omnia:health:billing-worker) теперь пишет только под commerce; если dependencies.billing_worker=missing дольше 3 минут — rollback"
}

phase_rollback() {
  log "rollback → воркер в 0 реплик, тик снова в RQ-воркере core"
  tunnel
  kube -n "$NS" scale deploy/billing-worker --replicas=0
  remote_sh core "cd $FULL && sudo -u $ADMIN_USER sed -i 's/^BILLING_LIFECYCLE_ENABLED=.*/BILLING_LIFECYCLE_ENABLED=true/' .env && docker compose up -d --no-build worker >/dev/null 2>&1 && echo '  worker пересоздан, BILLING_LIFECYCLE_ENABLED=true'"
}

case "${1:-}" in
  plan) phase_plan ;;
  core-access) phase_core_access ;;
  host) phase_host ;;
  image) phase_image ;;
  secret) phase_secret ;;
  apply) phase_apply ;;
  verify) phase_verify ;;
  handover) phase_handover ;;
  rollback) phase_rollback ;;
  all) phase_core_access; phase_host; phase_image; phase_secret; phase_apply; phase_verify ;;
  *) sed -n 2,17p "$0"; exit 1 ;;
esac
