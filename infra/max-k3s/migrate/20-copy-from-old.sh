#!/usr/bin/env bash
# Переезд платформы (Фаза 2a), шаг 2: копирование со СТАРОГО сервера (170.168.72.200) на core.
# Запускать НА СТАРОМ сервере от i48ptgvnis (его ключ добавлен в /etc/ssh/authorized_keys.d/zeuszcz на core):
#   bash 20-copy-from-old.sh
# Ничего на старом сервере не меняет — только читает. Повторный запуск безопасен.
set -euo pipefail
CORE=${CORE:-zeuszcz@2.153.248.98}
IN=/opt/omnia-runtime/migrate-in
SSH_KEY=/home/i48ptgvnis/.ssh/id_ed25519
RSYNC_SSH="ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new"
ssh -i "$SSH_KEY" "$CORE" "install -d -m 700 $IN; sudo chown -R \$(id -un):\$(id -un) /opt/omnia /opt/omnia-runtime"
# rsync code 23 = «часть атрибутов не выставлена» (времена на чужих каталогах) — не ошибка переноса
rs() { sudo rsync "$@" || [ $? -eq 23 ]; }

echo "== 1/7 репозиторий /opt/omnia (без node_modules/.venv/.next)"
rs -a --no-owner --no-group -e "$RSYNC_SSH" \
  --exclude node_modules --exclude .venv --exclude .next --exclude __pycache__ --exclude .pytest_cache \
  /opt/omnia/ "$CORE:/opt/omnia/"

echo "== 2/7 env-файлы (секреты, идут только сервер→сервер)"
sudo cat /opt/omnia/apps/llm-gateway/deploy/full/.env | ssh -i "$SSH_KEY" "$CORE" "cat > $IN/platform.env && chmod 600 $IN/platform.env"
sudo cat /opt/omnia/apps/orchestrator/.env            | ssh -i "$SSH_KEY" "$CORE" "cat > $IN/orchestrator.env && chmod 600 $IN/orchestrator.env"
sudo cat /opt/omnia-runtime/.env                       | ssh -i "$SSH_KEY" "$CORE" "cat > $IN/runtime.env && chmod 600 $IN/runtime.env"

echo "== 3/7 дампы баз (платформа + базы пользовательских проектов)"
docker exec omnia-prod-postgres pg_dump -U omnia -Fc omnia | ssh -i "$SSH_KEY" "$CORE" "cat > $IN/omnia.dump"
docker exec omnia-postgres-users pg_dumpall -U omnia_root  | ssh -i "$SSH_KEY" "$CORE" "cat > $IN/omnia_users.sql"

echo "== 4/7 MinIO (git-архивы проектов, превью, фото) — ТОЛЬКО через S3-API"
# Копия «сырых» файлов тома (tar) НЕ работает: новый MinIO не признаёт чужие xl.meta и вычищает их
# (потеряли repos/ при первом переезде). Выгружаем объекты через mc, заливаем на core тоже через mc.
MINIO_USER=$(sudo sed -n 's/^MINIO_ROOT_USER=//p' /opt/omnia/apps/llm-gateway/deploy/full/.env)
MINIO_PASS=$(sudo sed -n 's/^MINIO_ROOT_PASSWORD=//p' /opt/omnia/apps/llm-gateway/deploy/full/.env)
rm -rf /tmp/minio-export; mkdir -p /tmp/minio-export
docker run --rm --network full_omnia-prod -v /tmp/minio-export:/out --entrypoint sh minio/mc:latest -c \
  "mc alias set m http://minio:9000 \"$MINIO_USER\" \"$MINIO_PASS\" >/dev/null && for b in projects previews omnia-photos task-board omnia-images omnia-videos; do mkdir -p /out/\$b; mc cp -r m/\$b/ /out/\$b/ >/dev/null 2>&1 || true; done"
tar -C /tmp -czf - minio-export | ssh -i "$SSH_KEY" "$CORE" "cat > $IN/minio-export.tgz"
rm -rf /tmp/minio-export

echo "== 5/7 runtime-каталоги платформы (бэкапы, аккаунт acme, история восстановлений)"
# Состояние ячеек старого сервера (state/project-machines — 194 ГБ чекпойнтов, резервации, локи,
# журналы публикаций, секреты и релизы ячеек, vhost-конфиги) НЕ переезжает — ячейки на новом
# сервере создаются заново. 30-bring-up.sh дополнительно вычищает, если что-то просочилось.
rs -a --no-owner --no-group -e "$RSYNC_SSH" \
  --exclude 'state/project-machines' --exclude 'state/project-cells' --exclude 'state/project-cells-*' \
  --exclude 'state/cell-*' --exclude 'state/locks' --exclude 'state/deploy-runs.json' \
  --exclude 'secrets' --exclude 'projects' --exclude 'certs' --exclude 'releases' --exclude 'recovery' \
  --exclude 'release-evidence' --exclude 'project-cell' --exclude 'nginx/sites-enabled/*.conf' \
  --exclude 'max-core-canary-*' --exclude 'agent-sandboxes' --exclude 'exe-builds' --exclude 'docker-cli' \
  --exclude 'registry-data' --exclude 'postgres-users-data' --exclude 'migrate-in' --exclude '*.lock' \
  /opt/omnia-runtime/ "$CORE:/opt/omnia-runtime/"

echo "== 6/7 страница /otchet"
sudo tar -C /var/www -czf - otchet | ssh -i "$SSH_KEY" "$CORE" "cat > $IN/otchet.tgz"

echo "== 7/7 docker-образы (те же байты и теги, что на проде)"
API_IMAGE=$(sudo sed -n 's/^API_IMAGE=//p' /opt/omnia/apps/llm-gateway/deploy/full/.env)
WEB_IMAGE=$(sudo sed -n 's/^WEB_IMAGE=//p' /opt/omnia/apps/llm-gateway/deploy/full/.env)
tag_of() { docker images --no-trunc --format '{{.Repository}}:{{.Tag}} {{.ID}}' | grep "$1" | grep -v '<none>' | awk 'NR==1{print $1}'; }
ORCH_ENV=/opt/omnia/apps/orchestrator/.env
MACHINE=$(tag_of "$(sudo sed -n 's/^CELL_MACHINE_BASE_IMAGE=.*@//p' $ORCH_ENV)")
GUARD=$(tag_of "$(sudo sed -n 's/^CELL_MACHINE_GUARD_IMAGE=.*@//p' $ORCH_ENV)")
PUBCORE=$(tag_of "$(sudo sed -n 's/^CELL_PUBLIC_CORE_IMAGE=//p' $ORCH_ENV)")
PREVCORE=$(tag_of "$(sudo sed -n 's/^CELL_PREVIEW_CORE_IMAGE=//p' $ORCH_ENV)")
TEMPLATES=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^omnia-template-[^:]+:dev$' | tr '\n' ' ')
# minio/minio и minio/mc с Docker Hub исчезли (MinIO убрал образы в 2025) — везём кэшированные копии
IMAGES="$API_IMAGE $WEB_IMAGE omnia-gateway:prod $MACHINE $GUARD $PUBCORE $PREVCORE $TEMPLATES minio/minio:latest minio/mc:latest"
echo "   $IMAGES"
docker save $IMAGES | ssh -i "$SSH_KEY" "$CORE" "docker load" | tail -3
echo "COPY_DONE"
