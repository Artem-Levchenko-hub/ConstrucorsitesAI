#!/usr/bin/env bash
# Выкатка ревизии main на прод (core = yleum.ru) одной командой, в порядке, который требуют
# двусторонние изменения api ↔ оркестратор:
#   .env identity → проверка отрендеренного compose → сборка api+web (nohup на core, опрос)
#   → api/worker/generation-worker → оркестратор core → rsync → оркестратор commerce → web
#   → PRODUCTION_EXPECTED_* → воркер биллинга в K3s commerce на тот же образ api → smoke.
#
# Использование (после ЗЕЛЁНОГО CI этой ревизии — красное не выкатываем):
#   infra/release/deploy-prod.sh <полный sha> [--no-web] [--legal-version 2026-09-25]
#     --no-web            web не менялся: образ web не собирается и контейнер не трогается
#     --legal-version V   версия юридических документов: явно пишется в env воркера биллинга
#                         (compose и api берут её из docker-compose.yml / config.py)
#
# Замок: /opt/omnia/.deploy.lock на core — вторая выкатка одновременно не начнётся. Если
# сценарий умер и замок остался — посмотреть его содержимое (кто, когда, что) и удалить руками.
# Требования на Mac: ssh-алиас max-core (и commerce с него), gh (переменные репозитория),
# kubectl с ~/.kube/max-studio.yaml (воркер биллинга), git-чекаут репозитория.
set -euo pipefail

SHA="${1:?полный sha ревизии main}"; shift || true
WEB=1
LEGAL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-web) WEB=0 ;;
    --legal-version) LEGAL="${2:?}"; shift ;;
    *) echo "неизвестный аргумент: $1" >&2; exit 2 ;;
  esac
  shift
done
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "нужен полный 40-символьный sha, получено: $SHA" >&2; exit 2; }

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
LOG="/tmp/omnia-build-${SHA:0:12}.log"
SHORT="${SHA:0:8}"
say() { echo "== $* ($(date -u +%H:%M:%SZ))"; }

say "выкатка $SHA web=$WEB legal=${LEGAL:-по умолчанию образа}"
git -C "$REPO" fetch -q origin
git -C "$REPO" merge-base --is-ancestor "$SHA" origin/main || { echo "ревизия $SHORT не лежит в origin/main — выкатываем только то, что в main" >&2; exit 2; }

say "core: замок выкатки"
ssh max-core "set -o noclobber; echo \"$(whoami)@$(hostname -s) $(date -u +%FT%TZ) $SHA\" > /opt/omnia/.deploy.lock" \
  || { echo "на core уже идёт выкатка (или остался замок): $(ssh max-core cat /opt/omnia/.deploy.lock)" >&2; exit 75; }
trap 'ssh max-core rm -f /opt/omnia/.deploy.lock >/dev/null 2>&1 || true' EXIT

say "core: ff-merge + идентичность релиза в compose .env"
ssh max-core "set -e; cd /opt/omnia && git fetch -q origin && git merge --ff-only $SHA >/dev/null && [ \"\$(git rev-parse HEAD)\" = \"$SHA\" ] && git rev-parse --short=12 HEAD; cd apps/llm-gateway/deploy/full && sed -i -E 's#^(API_IMAGE=omnia-api:).*#\1$SHA#; s#^(OMNIA_RELEASE_SHA=).*#\1$SHA#' .env; [ $WEB = 1 ] && sed -i -E 's#^(WEB_IMAGE=omnia-web:).*#\1$SHA#' .env; grep -E '^(API_IMAGE|WEB_IMAGE|OMNIA_RELEASE_SHA|RESTORATION_ADAPTATION_REPAIR_SECONDS|MAX_GENERATION_DEADLINE_SECONDS|LEGAL_DOCUMENT_VERSION)=' .env | cut -c1-80"

if [ -n "$LEGAL" ]; then
  say "core: версия документов для воркера биллинга"
  ssh max-core 'sudo bash -s' <<EOF
f=/etc/max-studio/billing-worker.env
if grep -q '^LEGAL_DOCUMENT_VERSION=' "\$f"; then sed -i -E 's/^LEGAL_DOCUMENT_VERSION=.*/LEGAL_DOCUMENT_VERSION=$LEGAL/' "\$f"; else printf '\nLEGAL_DOCUMENT_VERSION=$LEGAL\n' >> "\$f"; fi
grep -n '^LEGAL_DOCUMENT_VERSION=' "\$f"
EOF
fi

say "core: проверка отрендеренного compose (секреты входа только у api, окно починки, теги образов)"
ssh max-core "cd /opt/omnia/apps/llm-gateway/deploy/full && docker compose config --format json 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)[\"services\"]
api = d[\"api\"][\"environment\"]; gw = d[\"generation-worker\"][\"environment\"]; wk = d[\"worker\"].get(\"environment\", {})
assert api.get(\"OAUTH_LOGIN_REDIRECT_BASE_URL\") == \"https://yleum.ru\", \"redirect base\"
assert not gw.get(\"YANDEX_ID_CLIENT_SECRET\") and not wk.get(\"YANDEX_ID_CLIENT_SECRET\"), \"secret leaked to a worker\"
assert not gw.get(\"VK_ID_CLIENT_SECRET\") and not wk.get(\"VK_ID_CLIENT_SECRET\"), \"secret leaked to a worker\"
assert api.get(\"RESTORATION_ADAPTATION_REPAIR_SECONDS\") == \"1800\", api.get(\"RESTORATION_ADAPTATION_REPAIR_SECONDS\")
assert d[\"api\"][\"image\"].endswith(\"$SHA\"), d[\"api\"][\"image\"]
if $WEB: assert d[\"web\"][\"image\"].endswith(\"$SHA\"), d[\"web\"][\"image\"]
legal = \"$LEGAL\"
if legal:
    assert api.get(\"LEGAL_DOCUMENT_VERSION\") == legal, api.get(\"LEGAL_DOCUMENT_VERSION\")
    assert (d[\"web\"][\"build\"].get(\"args\") or {}).get(\"NEXT_PUBLIC_LEGAL_DOCUMENT_VERSION\") == legal
print(\"compose ok: yandex creds at api:\", bool(api.get(\"YANDEX_ID_CLIENT_SECRET\")), \"| repair window\", api.get(\"RESTORATION_ADAPTATION_REPAIR_SECONDS\"), \"| legal\", api.get(\"LEGAL_DOCUMENT_VERSION\"))
'"

TARGETS="api"; [ $WEB = 1 ] && TARGETS="api web"
say "core: сборка $TARGETS под nohup, опрос до готовности"
ssh max-core "cd /opt/omnia/apps/llm-gateway/deploy/full && rm -f $LOG && (nohup docker compose build $TARGETS >$LOG 2>&1 </dev/null &) && echo сборка запущена"
IMAGES="omnia-api:$SHA"; [ $WEB = 1 ] && IMAGES="omnia-api:$SHA omnia-web:$SHA"
state=""
for i in $(seq 1 60); do
  sleep 30
  state=$(ssh max-core "if docker image inspect $IMAGES >/dev/null 2>&1; then echo built; elif pgrep -f 'compose build $TARGETS' >/dev/null; then echo building; else echo stopped; fi")
  case "$state" in
    built) echo "образы собраны за ~$((i*30)) с"; break ;;
    stopped) echo "сборка остановилась без образов:"; ssh max-core "tail -40 $LOG"; exit 1 ;;
  esac
  [ $((i % 6)) -eq 0 ] && echo "  идёт сборка ($((i*30)) с): $(ssh max-core "tail -1 $LOG | cut -c1-100")"
done
[ "$state" = built ] || { echo "сборка не уложилась в 30 минут"; exit 1; }
ssh max-core "grep -E 'ERROR|error:' $LOG | tail -3 || true"

say "core: образ api читаем для uid 10001 (воркер биллинга в K3s)?"
ssh max-core "docker run --rm --user 10001:10001 --entrypoint sh omnia-api:$SHA -c 'n=\$(find /app/src /app/migrations /orchestrator/templates -type f ! -perm -o=r 2>/dev/null | wc -l); echo \"нечитаемых файлов: \$n\"; [ \"\$n\" = 0 ]'"

say "core: api worker generation-worker (api раньше оркестраторов)"
ssh max-core "cd /opt/omnia/apps/llm-gateway/deploy/full && docker compose up -d --no-build api worker generation-worker 2>&1 | grep -E 'Started|Recreated|Error|error' | tail -5; for i in \$(seq 1 40); do s=\$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8200/api/health 2>/dev/null || true); [ \"\$s\" = 200 ] && { echo \"api health 200 с попытки \$i\"; break; }; sleep 3; done; docker compose logs --since 3m api 2>&1 | grep -i -E 'running upgrade|RuntimeError|Traceback' | tail -3; docker tag omnia-api:$SHA omnia-api:prod"

say "оркестраторы: core, затем commerce (одна ревизия)"
ssh max-core "set -e; f=/opt/omnia/apps/orchestrator/.env; sed -i -E 's/^OMNIA_RELEASE_SHA=.*/OMNIA_RELEASE_SHA=$SHA/' \$f; cd /opt/omnia/apps/orchestrator && ~/.local/bin/uv sync --frozen 2>&1 | tail -1 && sudo systemctl restart omnia-orchestrator; for i in \$(seq 1 30); do curl -sf 127.0.0.1:8003/health >/dev/null 2>&1 && break; sleep 2; done; echo core: \$(curl -s 127.0.0.1:8003/health); rsync -a --delete --exclude .venv --exclude .env --exclude node_modules --exclude .next --exclude __pycache__ --exclude '*.tsbuildinfo' /opt/omnia/ commerce:/opt/omnia/; ssh -o BatchMode=yes commerce \"set -e; f=/opt/omnia/apps/orchestrator/.env; sed -i -E 's/^OMNIA_RELEASE_SHA=.*/OMNIA_RELEASE_SHA=$SHA/' \\\$f; cd /opt/omnia/apps/orchestrator && ~/.local/bin/uv sync --frozen 2>&1 | tail -1 && sudo systemctl restart omnia-orchestrator; for i in \\\$(seq 1 30); do curl -sf 127.0.0.1:8003/health >/dev/null 2>&1 && break; sleep 2; done; echo commerce: \\\$(curl -s 127.0.0.1:8003/health)\""

if [ $WEB = 1 ]; then
  say "core: web"
  ssh max-core "cd /opt/omnia/apps/llm-gateway/deploy/full && docker compose up -d --no-build web 2>&1 | grep -E 'Started|Recreated|Error|error' | tail -2; docker tag omnia-web:$SHA omnia-web:prod; for i in \$(seq 1 30); do s=\$(curl -s -o /dev/null -w '%{http_code}' https://yleum.ru/web-health 2>/dev/null || true); [ \"\$s\" = 200 ] && { echo \"web-health 200 с попытки \$i\"; break; }; sleep 3; done"
fi

say "публичный health"
ssh max-core "curl -s https://yleum.ru/api/health | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[\"status\"], \"api\", d[\"release_sha\"][:8], \"orch\", d[\"dependencies\"].get(\"orchestrator_release_sha\",\"\")[:8], \"billing\", d[\"dependencies\"].get(\"billing_worker\"), {k:v for k,v in d[\"checks\"].items() if v!=\"ok\"})'; echo \"legal/config: \$(curl -s https://yleum.ru/api/legal/config | python3 -c 'import json,sys; print(json.load(sys.stdin).get(\"document_version\"))')\""

say "github: ожидаемые ревизии (пять + общая)"
cd "$REPO"
VARS="API WORKER GENERATION_WORKER ORCHESTRATOR"; [ $WEB = 1 ] && VARS="$VARS WEB"
for v in $VARS; do gh variable set "PRODUCTION_EXPECTED_${v}_RELEASE_SHA" --body "$SHA"; done
gh variable set PRODUCTION_EXPECTED_RELEASE_SHA --body "$SHA"
gh variable set PRODUCTION_EXPECTED_RELEASE_SHA --env production --body "$SHA"
echo "переменные выставлены"

say "воркер биллинга (K3s commerce) → тот же образ api"
cd "$REPO/infra/max-k3s"
./commerce/10-billing-workloads.sh image 2>&1 | grep -E 'IMAGE_PUSHED|error' | tail -1
./commerce/10-billing-workloads.sh core-access 2>&1 | grep -E 'BILLING_ACCESS_DONE'
./commerce/10-billing-workloads.sh secret 2>&1 | grep -E 'configured|created|перезапущен'
./commerce/10-billing-workloads.sh apply 2>&1 | grep -E 'configured|unchanged' | head -2
sleep 30
./commerce/10-billing-workloads.sh verify 2>&1 | grep -v '^\s*$' | tail -3
if [ -n "$LEGAL" ]; then
  ./kube-tunnel.sh up >/dev/null 2>&1 || true
  export KUBECONFIG="$HOME/.kube/max-studio.yaml"
  echo "secret LEGAL_DOCUMENT_VERSION = $(kubectl --context max-commerce -n billing get secret billing-worker-env -o jsonpath='{.data.LEGAL_DOCUMENT_VERSION}' | base64 -d)"
  kubectl --context max-commerce -n billing exec deploy/billing-worker -- sh -c 'P=/app/.venv/bin/python; [ -x "$P" ] || P=python; "$P" -c "from omnia_api.core.config import get_settings; print(\"воркер: legal_document_version =\", get_settings().legal_document_version)"'
fi

say "smoke"
cd "$REPO"
gh workflow run production-smoke.yml >/dev/null && echo "smoke запущен (gh run list --workflow production-smoke.yml --limit 1)"
say "готово: прод на $SHORT"
