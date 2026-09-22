#!/usr/bin/env bash
# Базовая подготовка одной VPS (запускается от root через infra/max-k3s/provision.sh).
# Идемпотентно: повторный запуск ничего не ломает.
#
# Аргументы: NAME DOMAIN WG_IP WG_NET LAN_NET CLUSTER_CIDR SERVICE_CIDR ADMIN_USER "ADMIN_PUBKEY" "PEER_LAN_IPS"
set -euo pipefail
NAME=$1 DOMAIN=$2 WG_IP=$3 WG_NET=$4 LAN_NET=$5 CLUSTER_CIDR=$6 SERVICE_CIDR=$7 ADMIN_USER=$8 ADMIN_PUBKEY=$9 PEER_LAN_IPS=${10}
WG_PORT=${WG_PORT:-51820}
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a

echo "== [$NAME] hostname / hosts"
hostnamectl set-hostname "$NAME"
grep -q "127.0.1.1 $NAME.$DOMAIN" /etc/hosts || sed -i "/^127.0.1.1/d" /etc/hosts
grep -q "127.0.1.1 $NAME.$DOMAIN" /etc/hosts || echo "127.0.1.1 $NAME.$DOMAIN $NAME" >> /etc/hosts

echo "== [$NAME] ssh: ключ администратора в системном файле (панель Serverum обнуляет ~/.ssh/authorized_keys каждые 5 минут)"
install -d -m 755 /etc/ssh/authorized_keys.d
printf '%s\n' "$ADMIN_PUBKEY" > /etc/ssh/authorized_keys.d/$ADMIN_USER
chmod 644 /etc/ssh/authorized_keys.d/$ADMIN_USER
# sshd берёт ПЕРВОЕ встреченное значение; 05-* сортируется раньше 50-cloud-init.conf (PasswordAuthentication yes)
rm -f /etc/ssh/sshd_config.d/05-max-authorized-keys.conf
cat > /etc/ssh/sshd_config.d/05-max-hardening.conf <<EOF
# MAX Studio hardening (infra/max-k3s/remote/00-bootstrap.sh)
AuthorizedKeysFile .ssh/authorized_keys /etc/ssh/authorized_keys.d/%u
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
AllowUsers $ADMIN_USER backupsync
MaxAuthTries 4
LoginGraceTime 30
X11Forwarding no
ClientAliveInterval 120
ClientAliveCountMax 3
EOF
sshd -t
systemctl reload ssh

echo "== [$NAME] apt: обновление + пакеты"
apt-get update -q
apt-get -y -q -o Dpkg::Options::=--force-confold dist-upgrade
apt-get install -y -q --no-install-recommends \
  ufw fail2ban wireguard wireguard-tools unattended-upgrades \
  curl jq git htop iotop rsync prometheus-node-exporter inotify-tools \
  apparmor-utils open-iscsi
apt-get -y -q autoremove --purge
# node-exporter привязывается к WG-адресу на этапе 10-wireguard (интерфейса пока нет)
systemctl disable --now prometheus-node-exporter >/dev/null 2>&1 || true
systemctl disable --now ModemManager >/dev/null 2>&1 || true

echo "== [$NAME] unattended-upgrades: только security, без авто-reboot"
cat > /etc/apt/apt.conf.d/52-max-unattended.conf <<'EOF'
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Package-Blacklist { "k3s"; "postgresql-16"; "wireguard"; };
EOF

echo "== [$NAME] sysctl под контейнеры"
cat > /etc/sysctl.d/90-max-k3s.conf <<'EOF'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
fs.inotify.max_user_instances = 8192
fs.inotify.max_user_watches = 1048576
fs.file-max = 2097152
vm.max_map_count = 262144
vm.overcommit_memory = 1
net.core.somaxconn = 4096
net.ipv4.neigh.default.gc_thresh1 = 4096
net.ipv4.neigh.default.gc_thresh2 = 8192
net.ipv4.neigh.default.gc_thresh3 = 16384
EOF
sysctl -q --system >/dev/null

echo "== [$NAME] journald: ограничить размер"
install -d /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=1G\nMaxRetentionSec=1month\n' > /etc/systemd/journald.conf.d/90-max.conf
systemctl restart systemd-journald

echo "== [$NAME] fail2ban (sshd)"
cat > /etc/fail2ban/jail.d/sshd.local <<EOF
[DEFAULT]
ignoreip = 127.0.0.1/8 $WG_NET $LAN_NET
bantime  = 1h
findtime = 10m
maxretry = 5
backend  = systemd

[sshd]
enabled = true
mode = aggressive
EOF
systemctl enable --now fail2ban >/dev/null
systemctl restart fail2ban

echo "== [$NAME] ufw: снаружи только 22/80/443; WG, LAN-пиры и CIDR кластера — внутрь"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw default deny routed >/dev/null
ufw logging low >/dev/null
ufw allow 22/tcp comment 'ssh (ключи + fail2ban)' >/dev/null
ufw allow 80/tcp  comment 'http  → traefik' >/dev/null
ufw allow 443/tcp comment 'https → traefik' >/dev/null
ufw allow in on wg0 comment 'wireguard mesh' >/dev/null
for ip in $PEER_LAN_IPS; do ufw allow from "$ip" to any port "$WG_PORT" proto udp comment 'wireguard handshake (LAN)' >/dev/null; done
ufw allow from "$CLUSTER_CIDR" comment 'k3s pods' >/dev/null
ufw allow from "$SERVICE_CIDR" comment 'k3s services' >/dev/null
ufw route allow from "$CLUSTER_CIDR" comment 'k3s pod egress' >/dev/null
ufw route allow to "$CLUSTER_CIDR" comment 'k3s pod ingress' >/dev/null
ufw --force enable >/dev/null
ufw status | head -3

echo "== [$NAME] каталог секретов платформы"
install -d -m 700 /etc/max-studio

if [ -f /var/run/reboot-required ]; then
  echo "== [$NAME] REBOOT_REQUIRED (обновилось ядро) — перезагружаюсь"
  nohup sh -c 'sleep 2; systemctl reboot' >/dev/null 2>&1 &
else
  echo "== [$NAME] BOOTSTRAP_DONE (reboot не нужен)"
fi
