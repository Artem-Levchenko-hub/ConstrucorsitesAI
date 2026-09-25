#!/usr/bin/env bash
# Переезд платформы (Фаза 2a), шаг 3: запуск стека MAX Studio на core из того, что привёз 20-copy-from-old.sh.
# Запускается от root: ssh max-core sudo bash -s -- ADMIN_USER DOMAIN OLD_ORIGIN OLD_SUFFIX < этот файл
# Идемпотентно: env-файлы пересобираются из migrate-in, дампы восстанавливаются только в пустую базу.
set -euo pipefail
ADMIN_USER=$1 DOMAIN=$2 OLD_ORIGIN=${3:-https://constructor.lead-generator.ru} OLD_SUFFIX=${4:-preview.lead-generator.ru}
IN=/opt/omnia-runtime/migrate-in
NEW_ORIGIN="https://$DOMAIN"; APPS_SUFFIX="apps.$DOMAIN"
PUBLIC_IP=$(dig +short A "$DOMAIN" @1.1.1.1 | head -1)
FULL=/opt/omnia/apps/llm-gateway/deploy/full
ORCH=/opt/omnia/apps/orchestrator
HOME_ADMIN=$(getent passwd "$ADMIN_USER" | cut -d: -f6)
export DEBIAN_FRONTEND=noninteractive

upsert() { # upsert FILE KEY VALUE
  local f=$1 k=$2 v=$3
  if grep -qE "^$k=" "$f"; then sed -i "s#^$k=.*#$k=$v#" "$f"; else printf '%s=%s\n' "$k" "$v" >> "$f"; fi
}

echo "== 0. права и чистое runtime-состояние"
# данные контейнеров postgres-users (uid 70) и registry не трогаем — chown ломает работающую базу
find /opt/omnia /opt/omnia-runtime -maxdepth 1 -mindepth 1 -not -name postgres-users-data -not -name registry-data \
  -exec chown -R "$ADMIN_USER:$ADMIN_USER" {} +
chmod 700 "$IN"
# Ячейки старого сервера сюда не переезжают: их резервации CPU, локи, журналы публикаций, секреты,
# vhost-конфиги и релизы на новом хосте только мешают (admission отвечает insufficient_cpu,
# nginx -t падает на старом wildcard-сертификате). Убираем в migrate-in/old-state, каталоги создаём пустыми.
OLD_STATE="$IN/old-state"; install -d -m 700 "$OLD_STATE"
for d in state/project-cells state/project-cells-capacity-reservations state/cell-publications state/cell-deletions \
         state/project-cells-credentials state/locks state/deploy-runs.json secrets projects certs releases recovery \
         release-evidence project-cell; do
  if [ -e "/opt/omnia-runtime/$d" ] && [ ! -e "$OLD_STATE/$(basename "$d")" ]; then mv "/opt/omnia-runtime/$d" "$OLD_STATE/"; fi
done
for d in secrets projects certs releases recovery release-evidence project-cell; do
  install -d -o "$ADMIN_USER" -g "$ADMIN_USER" "/opt/omnia-runtime/$d"
done
# каталоги локов оркестратор принимает только с режимом 0700 (services/cell_lock.py), иначе — workspace_lock_unavailable
install -d -m 700 -o "$ADMIN_USER" -g "$ADMIN_USER" /opt/omnia-runtime/state/locks /opt/omnia-runtime/locks
if ls /opt/omnia-runtime/nginx/sites-enabled/*.conf >/dev/null 2>&1; then
  install -d -m 700 "$OLD_STATE/nginx-sites"; mv /opt/omnia-runtime/nginx/sites-enabled/*.conf "$OLD_STATE/nginx-sites/"
fi

echo "== 1. env-файлы под домен $DOMAIN"
sed -e "s#$OLD_ORIGIN#$NEW_ORIGIN#g" -e "s#wss://${OLD_ORIGIN#https://}#wss://$DOMAIN#g" -e "s#$OLD_SUFFIX#$APPS_SUFFIX#g" "$IN/platform.env" > "$FULL/.env"
upsert "$FULL/.env" PROJECT_CELL_PREVIEW_HOST_SUFFIX "$APPS_SUFFIX"
upsert "$FULL/.env" INTEGRATION_OAUTH_CALLBACK_BASE_URL "$NEW_ORIGIN/api/integrations/oauth"
sed -e "s#$OLD_ORIGIN#$NEW_ORIGIN#g" -e "s#$OLD_SUFFIX#$APPS_SUFFIX#g" -e "s#^BASE_DOMAIN=.*#BASE_DOMAIN=$DOMAIN#" \
    -e "/^OMNIA_WILDCARD_CERT_ROOT=/d" "$IN/orchestrator.env" > "$ORCH/.env"
upsert "$ORCH/.env" BYO_BLOCKED_IPS "$PUBLIC_IP,170.168.72.200"
upsert "$ORCH/.env" CELL_MACHINE_DENIED_CIDRS "[\"$PUBLIC_IP/32\",\"10.10.0.0/24\",\"172.197.102.0/24\",\"10.16.0.0/16\",\"170.168.72.200/32\"]"
cp "$IN/runtime.env" /opt/omnia-runtime/.env
chown "$ADMIN_USER:$ADMIN_USER" "$FULL/.env" "$ORCH/.env" /opt/omnia-runtime/.env
chmod 600 "$FULL/.env" "$ORCH/.env" /opt/omnia-runtime/.env

echo "== 2. образы ячеек с Docker Hub (по digest, как в .env оркестратора)"
for ref in $(sed -nE 's/^CELL_(POSTGRES|REDIS|BACKUP)_IMAGE=(.*)/\2/p' "$ORCH/.env"); do
  docker image inspect "$ref" >/dev/null 2>&1 || docker pull -q "$ref" >/dev/null
done

echo "== 3. runtime-стек: registry + postgres-users (compose-проект omnia-runtime → сеть omnia-runtime_default)"
cd /opt/omnia-runtime
docker compose -p omnia-runtime -f registry-compose.yml up -d >/dev/null 2>&1
docker compose -p omnia-runtime -f postgres-compose.yml up -d >/dev/null 2>&1
for i in $(seq 1 30); do docker exec omnia-postgres-users pg_isready -U omnia_root -q 2>/dev/null && break; sleep 2; done
if [ "$(docker exec omnia-postgres-users psql -U omnia_root -d postgres -tAc "select count(*) from pg_database where datistemplate=false and datname not in ('postgres','omnia_users')")" = "0" ]; then
  echo "   восстанавливаю базы пользовательских проектов"
  docker exec -i omnia-postgres-users psql -U omnia_root -d postgres -q -v ON_ERROR_STOP=0 < "$IN/omnia_users.sql" 2>&1 | grep -vE "already exists|^ERROR:  role" | head -5 || true
fi

echo "== 4. платформа: postgres/redis/minio → восстановление → весь стек"
cd "$FULL"
docker compose up -d postgres redis minio minio-init >/dev/null 2>&1
for i in $(seq 1 30); do docker exec omnia-prod-postgres pg_isready -U omnia -q 2>/dev/null && break; sleep 2; done
if [ "$(docker exec omnia-prod-postgres psql -U omnia -d omnia -tAc "select count(*) from pg_tables where schemaname='public'")" = "0" ]; then
  echo "   восстанавливаю базу платформы"
  docker exec -i omnia-prod-postgres pg_restore -U omnia -d omnia --no-owner --no-privileges < "$IN/omnia.dump"
  docker exec omnia-prod-postgres psql -U omnia -d omnia -tAc "select 'users='||count(*) from users"
  # Ячейки старого сервера сюда не переехали: их записи УДАЛЯЕМ (каскадом уходят доказательства,
  # операции, лизы старых ячеек). Помечать state='deleted' нельзя — тогда api считает, что проект
  # «удаляется», и блокирует запуск среды; без записи платформа создаёт ячейку заново по запросу.
  docker exec omnia-prod-postgres psql -U omnia -d omnia -tAc "delete from project_cell_workspaces" | sed 's/^/   записи старых ячеек: /'
fi
MINIO_USER=$(sed -n 's/^MINIO_ROOT_USER=//p' "$FULL/.env"); MINIO_PASS=$(sed -n 's/^MINIO_ROOT_PASSWORD=//p' "$FULL/.env")
if [ -f "$IN/minio-export.tgz" ]; then
  # Копия «сырых» файлов тома НЕ работает (MinIO вычищает чужие xl.meta) — только через S3-API.
  echo "   заливаю объекты MinIO через mc (повтор безопасен — объекты перезаписываются)"
  rm -rf /tmp/minio-export; tar -C /tmp -xzf "$IN/minio-export.tgz"
  docker run --rm --network full_omnia-prod -v /tmp/minio-export:/in --entrypoint sh minio/mc:latest -c \
    "mc alias set m http://minio:9000 \"$MINIO_USER\" \"$MINIO_PASS\" >/dev/null && for b in projects previews omnia-photos task-board omnia-images omnia-videos; do [ -d /in/\$b ] && mc cp -r /in/\$b/ m/\$b/ >/dev/null 2>&1; done"
  rm -rf /tmp/minio-export
  for b in projects previews omnia-photos; do
    printf '   %-14s %s objects\n' "$b" "$(docker run --rm --network full_omnia-prod --entrypoint sh minio/mc:latest -c "mc alias set m http://minio:9000 \"$MINIO_USER\" \"$MINIO_PASS\" >/dev/null && mc ls -r m/$b" 2>/dev/null | wc -l | tr -d ' ')"
  done
fi
GW=$(docker network inspect full_omnia-prod -f '{{(index .IPAM.Config 0).Gateway}}')
upsert "$FULL/.env" ORCHESTRATOR_URL "http://$GW:8003"
upsert "$FULL/.env" GATE_PREVIEW_RESOLVER_RULES "MAP *.$APPS_SUFFIX $GW"
docker network inspect omnia-runtime_default >/dev/null 2>&1 || docker network create omnia-runtime_default >/dev/null
docker compose up -d 2>&1 | grep -vE "^\s*$" | tail -3
for i in $(seq 1 60); do curl -fsS -m 3 http://127.0.0.1:8200/health >/dev/null 2>&1 && break; sleep 5; done
echo "   api: $(curl -fsS -m 5 http://127.0.0.1:8200/health | head -c 120)"
echo "   web: $(curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:3100/)"

echo "== 5. оркестратор (host systemd, пользователь $ADMIN_USER)"
sudo -u "$ADMIN_USER" bash -c "cd $ORCH && $HOME_ADMIN/.local/bin/uv sync --frozen -q" 2>&1 | tail -2
[ -d /opt/omnia-runtime/acme-home ] && [ -f /opt/omnia-runtime/acme-home/account.conf ] || sudo -u "$ADMIN_USER" cp -a "$HOME_ADMIN/.acme.sh/." /opt/omnia-runtime/acme-home/
sed -e "s/^User=.*/User=$ADMIN_USER/" -e "s/^Group=.*/Group=$ADMIN_USER/" /opt/omnia/infra/systemd/yleum-orchestrator.service > /etc/systemd/system/yleum-orchestrator.service
install -d /etc/systemd/system/yleum-orchestrator.service.d
printf '[Service]\nNoNewPrivileges=false\n' > /etc/systemd/system/yleum-orchestrator.service.d/override.conf
systemctl daemon-reload
systemctl enable --now yleum-orchestrator >/dev/null 2>&1
systemctl restart yleum-orchestrator
for i in $(seq 1 20); do curl -fsS -m 3 http://127.0.0.1:8003/health >/dev/null 2>&1 && break; sleep 3; done
echo "   orchestrator: $(curl -fsS -m 5 http://127.0.0.1:8003/health || echo DOWN)"

echo "== 6. nginx: $DOMAIN (платформа) + grafana.$DOMAIN + /otchet, сертификаты certbot"
tar -C /var/www -xzf "$IN/otchet.tgz" && chown -R www-data:www-data /var/www/otchet
TRAEFIK_IP=$(k3s kubectl -n kube-system get svc traefik -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo 127.0.0.1)
cat > "/etc/nginx/sites-available/$DOMAIN" <<EOF
# MAX Studio — платформа (генерируется infra/max-k3s/migrate/30-bring-up.sh; certbot дописывает TLS)
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN www.$DOMAIN;
    client_max_body_size 50m;
    location /.well-known/acme-challenge/ { root /opt/omnia-runtime/acme-webroot; }
    location = /otchet { return 301 /otchet/; }
    location ^~ /otchet/ {
        alias /var/www/otchet/;
        index index.html;
        try_files \$uri \$uri/ =404;
        add_header Cache-Control "no-cache";
    }
    location / {
        proxy_pass         http://127.0.0.1:3100;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_set_header   Upgrade           \$http_upgrade;
        proxy_set_header   Connection        "upgrade";
        proxy_buffering    off;
        proxy_read_timeout 60s;
    }
    location /api/ {
        proxy_pass         http://127.0.0.1:8200;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_set_header   Upgrade           \$http_upgrade;
        proxy_set_header   Connection        "upgrade";
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }
    location /p/ {
        proxy_pass         http://127.0.0.1:8200;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
    location /llm/ {
        allow 127.0.0.1;
        allow ::1;
        deny all;
        proxy_pass         http://127.0.0.1:8101/;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_buffering    off;
        proxy_read_timeout 600s;
    }
    location /minio/ {
        proxy_pass         http://127.0.0.1:9000/;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_buffering    off;
        proxy_read_timeout 60s;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
EOF
cat > "/etc/nginx/sites-available/grafana.$DOMAIN" <<EOF
# Grafana живёт в K3s (kube-prometheus-stack); Traefik без публичных портов → проксируем в его ClusterIP
server {
    listen 80;
    listen [::]:80;
    server_name grafana.$DOMAIN;
    location / {
        proxy_pass         http://$TRAEFIK_IP:80;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_set_header   Upgrade           \$http_upgrade;
        proxy_set_header   Connection        \$omnia_connection_upgrade;
        proxy_read_timeout 120s;
    }
}
EOF
ln -sf "/etc/nginx/sites-available/$DOMAIN" "/etc/nginx/sites-enabled/$DOMAIN"
ln -sf "/etc/nginx/sites-available/grafana.$DOMAIN" "/etc/nginx/sites-enabled/grafana.$DOMAIN"
nginx -t 2>&1 | tail -1 && systemctl reload nginx
if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
  certbot --nginx --non-interactive --agree-tos --register-unsafely-without-email --redirect \
    -d "$DOMAIN" -d "www.$DOMAIN" -d "grafana.$DOMAIN" 2>&1 | grep -E "Successfully|Certificate|error|Error" | head -5
fi
nginx -t 2>&1 | tail -1 && systemctl reload nginx
echo "BRING_UP_DONE $DOMAIN"
