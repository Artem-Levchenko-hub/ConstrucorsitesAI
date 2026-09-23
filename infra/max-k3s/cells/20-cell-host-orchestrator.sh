#!/usr/bin/env bash
# Фаза 3, этап B: оркестратор на втором хосте ячеек (тот же код, что на core; хост-сервис systemd).
# Запускается от root: ssh <host> sudo bash -s -- ADMIN_USER PREVIEW_SUFFIX WG_IP PUBLIC_IP CORE_WG_IP < этот файл
#
# Ожидает:
#   - /opt/omnia — свежая копия репозитория (rsync с core по WireGuard, без .venv);
#   - /opt/omnia/apps/orchestrator/.env.core — копия .env оркестратора core (снимается после применения);
#   - /etc/max-studio/runtime-kubeconfig.yaml — доступ к кластеру runtime (тот же ServiceAccount);
#   - /etc/max-studio/postgres.env — хостовый Postgres (для legacy-путей оркестратора, ячейкам не нужен).
#
# Что переопределяется относительно core: суффикс превью, адрес артефактов, Redis платформы по WG,
# запрещённые для машин сети (свой публичный IP), образы — из реестра по digest.
set -euo pipefail
ADMIN_USER=$1 PREVIEW_SUFFIX=$2 WG_IP=$3 PUBLIC_IP=$4 CORE_WG_IP=$5
ORCH=/opt/omnia/apps/orchestrator
HOME_ADMIN=$(getent passwd "$ADMIN_USER" | cut -d: -f6)
[ -f "$ORCH/.env.core" ] || { echo "нет $ORCH/.env.core — сначала скопировать .env оркестратора с core"; exit 1; }
. /etc/max-studio/postgres.env

echo "== .env оркестратора (на основе core)"
python3 - "$ORCH/.env.core" "$ORCH/.env" "$PREVIEW_SUFFIX" "$WG_IP" "$PUBLIC_IP" "$CORE_WG_IP" "$PG_DSN" <<'PY'
import json, re, sys
src, dst, suffix, wg_ip, public_ip, core_wg_ip, pg_dsn = sys.argv[1:]
text = open(src).read()
def setv(t, k, v):
    if re.search(rf"^{k}=", t, re.M):
        return re.sub(rf"^{k}=.*$", lambda _m: f"{k}={v}", t, flags=re.M)
    return t.rstrip("\n") + f"\n{k}={v}\n"
denied = json.loads(re.search(r"^CELL_MACHINE_DENIED_CIDRS=(.*)$", text, re.M).group(1))
denied = [c for c in denied if not c.startswith(("2.153.248.98/", "170.168.72.200/"))]
denied = sorted(set(denied + [f"{public_ip}/32", "2.153.248.98/32"]))
for k, v in [
    ("RUNTIME_HOST_SUFFIX", suffix),
    ("ARTIFACT_BASE_URL", f"http://{wg_ip}:8003"),
    ("REDIS_URL", f"redis://{core_wg_ip}:6379/0"),
    ("DATABASE_URL", pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1).replace(f"@{wg_ip}:", "@127.0.0.1:")),
    ("CELL_MACHINE_DENIED_CIDRS", json.dumps(denied)),
    ("BYO_BLOCKED_IPS", f"2.153.248.98,{public_ip},170.168.72.200"),
    ("CELL_MACHINE_BASE_IMAGE", "registry.yleum.ru/platform/project-machine@sha256:d4e02d40195591b4ed17fcaf9c85cb9a8f2741d087fc5054d253ade940e9bc7d"),
    ("CELL_MACHINE_GUARD_IMAGE", "registry.yleum.ru/platform/project-machine-guard@sha256:e4f2ea905894a3bceb6f27e092d4c040aabdb87a67f8f6129b7d91883be9c345"),
]:
    text = setv(text, k, v)
open(dst, "w").write(text)
print("  written", dst)
PY
chown "$ADMIN_USER:$ADMIN_USER" "$ORCH/.env"; chmod 600 "$ORCH/.env"; rm -f "$ORCH/.env.core"
sed -i "s|^OMNIA_RELEASE_SHA=.*|OMNIA_RELEASE_SHA=$(git -C /opt/omnia rev-parse HEAD)|" "$ORCH/.env"
grep -E "^(RUNTIME_HOST_SUFFIX|PUBLIC_HOST_SUFFIX|ARTIFACT_BASE_URL|REDIS_URL|CELL_MACHINE_DENIED_CIDRS|CELL_MACHINE_BASE_IMAGE|OMNIA_RELEASE_SHA)=" "$ORCH/.env" | cut -c1-120

echo "== образы из реестра"
u=$(sed -n 's/^IMAGE_REGISTRY_USERNAME=//p' "$ORCH/.env"); p=$(sed -n 's/^IMAGE_REGISTRY_PASSWORD=//p' "$ORCH/.env")
echo "$p" | docker login registry.yleum.ru -u "$u" --password-stdin >/dev/null
for ref in \
  registry.yleum.ru/platform/project-machine:main-stack-0d9f780c \
  registry.yleum.ru/platform/project-machine-guard:e4f2ea905894 \
  registry.yleum.ru/platform/max-public-core:3db4c3f1c20a \
  registry.yleum.ru/platform/max-public-core:6e8d2c6933a6 \
  "$(sed -n 's/^CELL_POSTGRES_IMAGE=//p' "$ORCH/.env")" \
  "$(sed -n 's/^CELL_REDIS_IMAGE=//p' "$ORCH/.env")" \
  "$(sed -n 's/^CELL_BACKUP_IMAGE=//p' "$ORCH/.env")"; do
  docker pull -q "$ref" >/dev/null && echo "  pulled $ref"
done
docker logout registry.yleum.ru >/dev/null
for key in CELL_MACHINE_BASE_IMAGE CELL_MACHINE_GUARD_IMAGE CELL_PUBLIC_CORE_IMAGE CELL_PREVIEW_CORE_IMAGE; do
  ref=$(sed -n "s/^$key=//p" "$ORCH/.env")
  docker image inspect "$ref" --format "  $key → {{.Id}}" | cut -c1-90
done

echo "== оркестратор: venv, systemd (пользователь $ADMIN_USER)"
chown -R "$ADMIN_USER:$ADMIN_USER" /opt/omnia
sudo -u "$ADMIN_USER" bash -c "cd $ORCH && $HOME_ADMIN/.local/bin/uv sync --frozen -q" 2>&1 | tail -2
sed -e "s/^User=.*/User=$ADMIN_USER/" -e "s/^Group=.*/Group=$ADMIN_USER/" /opt/omnia/infra/systemd/omnia-orchestrator.service > /etc/systemd/system/omnia-orchestrator.service
install -d /etc/systemd/system/omnia-orchestrator.service.d
printf '[Service]\nNoNewPrivileges=false\n' > /etc/systemd/system/omnia-orchestrator.service.d/override.conf
systemctl daemon-reload
systemctl enable --now omnia-orchestrator >/dev/null 2>&1
systemctl restart omnia-orchestrator
for i in $(seq 1 20); do curl -sf http://127.0.0.1:8003/health >/dev/null 2>&1 && break; sleep 2; done
curl -s http://127.0.0.1:8003/health; echo
nginx -t 2>&1 | tail -1
echo "CELL_HOST_ORCHESTRATOR_DONE $(hostname)"
