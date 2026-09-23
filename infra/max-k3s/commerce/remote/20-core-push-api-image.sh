#!/usr/bin/env bash
# Фаза 3 / commerce, шаг «образ»: выполняется НА CORE от root.
#   ssh max-core sudo bash -s -- REGISTRY REPO < этот файл        # напр. registry.yleum.ru platform/omnia-api
#
# Публикует образ api, собранный compose на core (omnia-api:prod — тот же, что у api/worker/
# generation-worker), в приватный реестр под тегом = sha релиза, и записывает ссылку по digest в
# /etc/max-studio/billing-image.ref, откуда её берёт 10-billing-workloads.sh apply. Креды реестра —
# из .env оркестратора (IMAGE_REGISTRY_USERNAME/PASSWORD), как в cells/20-cell-host-orchestrator.sh.
set -euo pipefail
REGISTRY=$1 REPO=${2:-platform/omnia-api}
ORCH_ENV=/opt/omnia/apps/orchestrator/.env
PLATFORM_ENV=/opt/omnia/apps/llm-gateway/deploy/full/.env
SRC=$(sed -n 's/^API_IMAGE=//p' "$PLATFORM_ENV" | tail -1); SRC=${SRC:-omnia-api:prod}
docker image inspect "$SRC" >/dev/null 2>&1 || { echo "нет образа $SRC — сначала docker compose build api"; exit 1; }
SHA=$(git -C /opt/omnia rev-parse --short=12 HEAD 2>/dev/null || date +%Y%m%d%H%M)
REF="$REGISTRY/$REPO:$SHA"
u=$(sed -n 's/^IMAGE_REGISTRY_USERNAME=//p' "$ORCH_ENV" | tail -1)
p=$(sed -n 's/^IMAGE_REGISTRY_PASSWORD=//p' "$ORCH_ENV" | tail -1)
[ -n "$u" ] && [ -n "$p" ] || { echo "в $ORCH_ENV нет IMAGE_REGISTRY_USERNAME/PASSWORD (k8s/apply.sh publication-env)"; exit 1; }
echo "$p" | docker login "$REGISTRY" -u "$u" --password-stdin >/dev/null
docker tag "$SRC" "$REF"
docker push -q "$REF" >/dev/null
docker logout "$REGISTRY" >/dev/null
DIGEST=$(docker image inspect "$REF" --format '{{range .RepoDigests}}{{println .}}{{end}}' | grep "^$REGISTRY/$REPO@" | head -1)
[ -n "$DIGEST" ] || { echo "не удалось получить digest для $REF"; exit 1; }
install -d -m 700 /etc/max-studio
printf 'IMAGE_REF=%s\nIMAGE_TAG=%s\nRELEASE_SHA=%s\n' "$DIGEST" "$REF" "$SHA" > /etc/max-studio/billing-image.ref
chmod 600 /etc/max-studio/billing-image.ref
echo "IMAGE_PUSHED $DIGEST (tag $REF)"
