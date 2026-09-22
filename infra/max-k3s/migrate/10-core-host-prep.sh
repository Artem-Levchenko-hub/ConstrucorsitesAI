#!/usr/bin/env bash
# Переезд платформы (Фаза 2a): подготовка хоста core под Docker-стек MAX Studio.
# Запускается от root через provision-подобный драйвер: ssh max-core sudo bash -s -- ARGS < этот файл
#
# Что делает (идемпотентно):
#   - K3s: отключает servicelb (Traefik больше не держит 80/443 на хосте; кластер остаётся под мониторинг)
#   - Docker CE + compose plugin, пользователь-оператор в группе docker
#   - nginx + certbot (nginx plugin), acme.sh под оператором (per-host сертификаты приложений)
#   - uv для оркестратора, каталоги /opt/omnia и /opt/omnia-runtime, ufw-правила для docker→host
#
# Аргументы: ADMIN_USER DOMAIN ACME_EMAIL
set -euo pipefail
ADMIN_USER=$1 DOMAIN=$2 ACME_EMAIL=$3
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a

echo "== k3s: servicelb off (освобождаем 80/443), traefik → ClusterIP"
if ! grep -q "^disable:" /etc/rancher/k3s/config.yaml; then
  printf 'disable:\n  - servicelb\n' >> /etc/rancher/k3s/config.yaml
  systemctl restart k3s
  for i in $(seq 1 30); do k3s kubectl get node >/dev/null 2>&1 && break; sleep 3; done
fi
# Одного servicelb-off мало: у сервиса traefik остаётся LoadBalancer-статус с публичным IP, и kube-proxy
# DNAT-ит ВЕСЬ трафик на <public ip>:80/443 (в т.ч. из docker-контейнеров платформы) в Traefik с его
# самоподписанным сертификатом. Переводим сервис в ClusterIP через HelmChartConfig (переживает апгрейды).
cat > /var/lib/rancher/k3s/server/manifests/traefik-config.yaml <<'EOF'
apiVersion: helm.cattle.io/v1
kind: HelmChartConfig
metadata:
  name: traefik
  namespace: kube-system
spec:
  valuesContent: |-
    service:
      type: ClusterIP
EOF
k3s kubectl -n kube-system patch svc traefik -p '{"spec":{"type":"ClusterIP"}}' >/dev/null 2>&1 || true
# klipper-lb поды исчезают сами после рестарта; подчистим, если остались
k3s kubectl -n kube-system delete pod -l svccontroller.k3s.cattle.io/svcname=traefik --ignore-not-found >/dev/null 2>&1 || true
for i in $(seq 1 20); do ss -tlnH | awk '{print $4}' | grep -qE ':(80|443)$' || break; sleep 3; done
ss -tlnH | awk '{print $4}' | grep -E ':(80|443)$' && { echo "порты 80/443 всё ещё заняты"; exit 1; } || echo "  80/443 свободны"
for i in $(seq 1 20); do [ "$(iptables-save | grep -c "$(dig +short A "$DOMAIN" @1.1.1.1 | head -1)")" = "0" ] && break; sleep 3; done

echo "== публичный IP на lo (Serverum: 1:1 NAT, hairpin к самому себе не работает)"
PUBLIC_IP=$(dig +short A "$DOMAIN" @1.1.1.1 | head -1)
cat > /etc/netplan/60-public-ip-hairpin.yaml <<EOF
# Публичный IP у Serverum — 1:1 NAT и на интерфейсе его нет. Вешаем его на lo, чтобы платформа
# (api в Docker) могла ходить на свои же публичные имена (*.apps.$DOMAIN, $DOMAIN) локально.
network:
  version: 2
  ethernets:
    lo:
      match: {name: lo}
      addresses: [$PUBLIC_IP/32]
EOF
chmod 600 /etc/netplan/60-public-ip-hairpin.yaml
ip addr show lo | grep -q "$PUBLIC_IP/32" || ip addr add "$PUBLIC_IP/32" dev lo

echo "== docker ce"
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
install -d /etc/docker
# mtu 1400 — как на старом сервере: за NAT провайдера PMTUD внутри docker-мостов ненадёжен
[ -f /etc/docker/daemon.json ] || cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "50m", "max-file": "3" },
  "live-restore": true,
  "mtu": 1400
}
EOF
systemctl enable --now docker >/dev/null
usermod -aG docker "$ADMIN_USER"
docker --version; docker compose version

echo "== nginx + certbot + acme.sh + rsync + pg client"
apt-get install -y -q --no-install-recommends nginx certbot python3-certbot-nginx rsync jq ssl-cert postgresql-client-16 >/dev/null
systemctl enable --now nginx >/dev/null
# acme.sh под оператором: аккаунт Let's Encrypt живёт в /opt/omnia-runtime/acme-home (см. docs/08, фикс 2)
if [ ! -x "/home/$ADMIN_USER/.acme.sh/acme.sh" ]; then
  sudo -u "$ADMIN_USER" bash -c "curl -fsSL https://get.acme.sh | sh -s email=$ACME_EMAIL" >/dev/null
fi

echo "== uv для оркестратора"
if [ ! -x "/home/$ADMIN_USER/.local/bin/uv" ]; then
  sudo -u "$ADMIN_USER" bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' >/dev/null
fi

echo "== каталоги"
install -d -o "$ADMIN_USER" -g "$ADMIN_USER" /opt/omnia /opt/omnia-runtime
for d in projects nginx/sites-enabled secrets certs acme-webroot acme-home state backups releases registry-data postgres-users-data logs locks recovery release-evidence project-cell; do
  install -d -o "$ADMIN_USER" -g "$ADMIN_USER" "/opt/omnia-runtime/$d"
done
install -d -o www-data -g www-data /var/www/otchet

echo "== ufw: контейнеры → хост (оркестратор :8003, registry :5000, postgres-users :5433)"
ufw allow from 172.16.0.0/12 to any port 8003 proto tcp comment 'docker → orchestrator' >/dev/null
ufw allow from 10.253.0.0/16 to any port 8003 proto tcp comment 'cells → orchestrator' >/dev/null
ufw allow from 172.16.0.0/12 to any port 80,443 proto tcp comment 'docker → nginx (preview gate)' >/dev/null
ufw allow from 10.253.0.0/16 to any port 80,443 proto tcp comment 'cells → nginx (hairpin)' >/dev/null
ufw reload >/dev/null

echo "== nginx: базовые конфиги (catch-all, runtime include, upgrade map)"
cat > /etc/nginx/conf.d/omnia-runtime.conf <<'EOF'
map $http_upgrade $omnia_connection_upgrade {
    default upgrade;
    ''      close;
}
include /opt/omnia-runtime/nginx/sites-enabled/*.conf;
EOF
cat > /etc/nginx/conf.d/consilium-limits.conf <<'EOF'
limit_req_zone $binary_remote_addr zone=consilium_login:1m rate=10r/m;
EOF
rm -f /etc/nginx/sites-enabled/default
cat > /etc/nginx/sites-available/00-default-catchall <<'EOF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    # return на уровне server выполняется ДО выбора location и глушил бы ACME-проверку
    location /.well-known/acme-challenge/ { root /opt/omnia-runtime/acme-webroot; }
    location / { return 444; }
}
server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    server_name _;
    ssl_certificate /etc/ssl/certs/ssl-cert-snakeoil.pem;
    ssl_certificate_key /etc/ssl/private/ssl-cert-snakeoil.key;
    return 444;
}
EOF
ln -sf /etc/nginx/sites-available/00-default-catchall /etc/nginx/sites-enabled/00-default-catchall
sed -i 's/^\s*# server_names_hash_bucket_size 64;/\tserver_names_hash_bucket_size 128;/' /etc/nginx/nginx.conf
grep -q "server_names_hash_bucket_size" /etc/nginx/nginx.conf || sed -i 's/^http {/http {\n\tserver_names_hash_bucket_size 128;/' /etc/nginx/nginx.conf
nginx -t && systemctl reload nginx
echo "HOST_PREP_DONE $(hostname)"
