#!/usr/bin/env bash
# Фаза 3 / commerce, шаг «доступ»: выполняется НА CORE от root (через lib.sh run_remote).
#   ssh max-core sudo bash -s -- ADMIN_USER CORE_WG_IP COMMERCE_WG_IP COMMERCE_POD_CIDR PG_PORT < этот файл
#
# База платформы с 23.09.2026 живёт на ХОСТОВОМ PostgreSQL 16 core (migrate/50-platform-db-to-host.sh)
# и уже слушает CORE_WG_IP:5432 — socat-форвард к контейнеру omnia-prod-postgres, который был здесь
# раньше, больше не нужен (контейнер остановлен).
#
# Что делает (идемпотентно):
#   1. проверяет, что хостовый PostgreSQL слушает CORE_WG_IP:PG_PORT, и добавляет в pg_hba строки роли
#      max_billing для commerce (WG-адрес узла; pod-сеть — страховка: flannel маскирует поды в адрес узла);
#   2. ufw: PG_PORT для commerce (интерфейс WireGuard и так открыт целиком — правило делает доступ явным);
#   3. роль max_billing в базе платформы с правами ровно на таблицы биллингового тика
#      (пароль генерируется один раз → /etc/max-studio/billing.env, root 0600);
#   4. /etc/max-studio/billing-worker.env (root 0600) — состав Secret воркера: значения ЮKassa/SMTP/
#      реквизитов из .env платформы + DSN роли + адреса по WireGuard; ORCHESTRATOR_HOSTS переписан
#      так, чтобы core был доступен по WG, а не по docker-мосту;
#   5. проверка: psql от имени max_billing по WG-адресу читает billing_plans.
set -euo pipefail
ADMIN_USER=$1 CORE_WG_IP=$2 COMMERCE_WG_IP=$3 COMMERCE_POD_CIDR=$4 PG_PORT=${5:-5432}
FULL=/opt/omnia/apps/llm-gateway/deploy/full
PLATFORM_ENV=$FULL/.env
SECRETS=/etc/max-studio
[ -f "$PLATFORM_ENV" ] || { echo "нет $PLATFORM_ENV — платформа на этом хосте не развёрнута"; exit 1; }
install -d -m 711 "$SECRETS"  # 711: the orchestrator (zeuszcz) must traverse to runtime-kubeconfig.yaml
envval() { sed -n "s/^$1=//p" "$2" | tail -1; }
# Имя базы — из PLATFORM_DATABASE_URL (хостовый режим), иначе POSTGRES_DB, иначе omnia.
PG_DB=$(envval PLATFORM_DATABASE_URL "$PLATFORM_ENV" | sed -n 's|.*/\([^/?]*\)\(?.*\)\{0,1\}$|\1|p')
PG_DB=${PG_DB:-$(envval POSTGRES_DB "$PLATFORM_ENV")}; PG_DB=${PG_DB:-omnia}
psql_admin() { sudo -u postgres psql -d "$PG_DB" -v ON_ERROR_STOP=1 -q "$@"; }

echo "== 1. хостовый PostgreSQL: $CORE_WG_IP:$PG_PORT, база $PG_DB"
ss -ltn | grep -q "$CORE_WG_IP:$PG_PORT " || { echo "PostgreSQL не слушает $CORE_WG_IP:$PG_PORT (listen_addresses)"; exit 1; }
[ "$(sudo -u postgres psql -Atc "select 1 from pg_database where datname='$PG_DB'")" = 1 ] || { echo "базы $PG_DB нет на хостовом PostgreSQL"; exit 1; }
HBA=$(sudo -u postgres psql -Atc "show hba_file")
changed=0
for src in "$COMMERCE_WG_IP/32" "$COMMERCE_POD_CIDR"; do
  line=$(printf 'host    %s    max_billing    %s    scram-sha-256' "$PG_DB" "$src")
  grep -Fxq "$line" "$HBA" || { echo "$line" >> "$HBA"; changed=1; }
done
if [ "$changed" = 1 ]; then
  sudo -u postgres psql -Atc "select pg_reload_conf()" >/dev/null
  echo "  pg_hba: строки max_billing для $COMMERCE_WG_IP и $COMMERCE_POD_CIDR добавлены, конфигурация перечитана"
else
  echo "  pg_hba: строки max_billing для commerce уже есть"
fi

echo "== 2. ufw: $CORE_WG_IP:$PG_PORT для commerce"
ufw allow from "$COMMERCE_WG_IP" to "$CORE_WG_IP" port "$PG_PORT" proto tcp comment 'commerce billing worker → platform postgres' >/dev/null
ufw allow from "$COMMERCE_POD_CIDR" to "$CORE_WG_IP" port "$PG_PORT" proto tcp comment 'commerce pods → platform postgres' >/dev/null
ufw reload >/dev/null
ufw status | grep -E "$CORE_WG_IP $PG_PORT" | sed 's/^/  /'

echo "== 3. роль max_billing в базе платформы ($PG_DB)"
touch "$SECRETS/billing.env"; chmod 600 "$SECRETS/billing.env"
PW=$(envval BILLING_DB_PASSWORD "$SECRETS/billing.env")
if [ -z "$PW" ]; then
  PW=$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-32)
  echo "BILLING_DB_PASSWORD=$PW" >> "$SECRETS/billing.env"
fi
psql_admin <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'max_billing') THEN
    CREATE ROLE max_billing NOLOGIN;
  END IF;
END
$$;
SQL
# пароль — только [A-Za-z0-9] (см. генерацию выше), поэтому его безопасно подставлять в SQL-литерал
psql_admin -c "ALTER ROLE max_billing WITH LOGIN PASSWORD '$PW' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;"
psql_admin -v db="$PG_DB" <<'SQL'
GRANT CONNECT ON DATABASE :"db" TO max_billing;
GRANT USAGE ON SCHEMA public TO max_billing;
-- Тик пишет только в биллинговые таблицы; SELECT … FOR UPDATE требует UPDATE.
GRANT SELECT, INSERT, UPDATE ON TABLE
  billing_accounts, billing_plans, billing_payment_methods, subscriptions, payments,
  wallets, wallet_charges TO max_billing;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO max_billing;
-- Снятие keep-alive при переводе на Free / смене тарифа; email адресата уведомлений.
GRANT SELECT, UPDATE ON TABLE projects TO max_billing;
GRANT SELECT ON TABLE users TO max_billing;
SQL
echo "  роль max_billing: права выданы"

echo "== 4. $SECRETS/billing-worker.env"
RELEASE_SHA=$(git -C /opt/omnia rev-parse --short=12 HEAD 2>/dev/null || envval OMNIA_RELEASE_SHA "$PLATFORM_ENV")
python3 - "$PLATFORM_ENV" "$SECRETS/billing-worker.env" "$CORE_WG_IP" "$COMMERCE_WG_IP" "$PG_PORT" "$PW" "$PG_DB" "$RELEASE_SHA" <<'PY'
import json, re, sys
src, dst, core_wg, commerce_wg, port, pw, db, sha = sys.argv[1:]
text = open(src).read()
def get(key, default=""):
    m = re.search(rf"^{re.escape(key)}=(.*)$", text, re.M)
    return m.group(1).strip() if m else default
copied = [
    "JWT_SECRET", "ORCHESTRATOR_INTERNAL_TOKEN", "DEFAULT_ORCHESTRATOR",
    "YOOKASSA_SHOP_ID", "YOOKASSA_SECRET_KEY", "YOOKASSA_VAT_CODE",
    "LEGAL_OPERATOR_NAME", "LEGAL_OPERATOR_INN", "LEGAL_OPERATOR_ADDRESS", "LEGAL_SUPPORT_EMAIL",
    "LEGAL_DOCUMENT_VERSION", "WEB_BASE_URL", "ADMIN_EMAILS",
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_STARTTLS",
    "BILLING_LIFECYCLE_POLL_SECONDS", "BILLING_RENEWAL_RETRY_HOURS", "BILLING_GRACE_DAYS",
    "BILLING_PAYMENT_RECONCILE_AFTER_MINUTES", "BILLING_PAYMENT_ABANDON_AFTER_HOURS",
]
out = {
    "ENV": "prod",
    "LOG_LEVEL": get("LOG_LEVEL", "INFO"),
    "OMNIA_RELEASE_SHA": sha,
    "DATABASE_URL": f"postgresql+asyncpg://max_billing:{pw}@{core_wg}:{port}/{db}",
    "REDIS_URL": f"redis://{core_wg}:6379/0",
    "ORCHESTRATOR_URL": f"http://{core_wg}:8003",
    "BILLING_LIFECYCLE_ENABLED": "true",
    "USE_GENERATION_WORKER": "false",
}
for key in copied:
    value = get(key)
    if value:
        out[key] = value
hosts = get("ORCHESTRATOR_HOSTS")
if hosts:
    items = json.loads(hosts)
    for item in items:
        if item.get("name") == get("DEFAULT_ORCHESTRATOR", "core"):
            item["url"] = f"http://{core_wg}:8003"
            item.pop("preview_resolver_rules", None)
        elif item.get("name") == "commerce":
            item["url"] = f"http://{commerce_wg}:8003"
            item.pop("preview_resolver_rules", None)
    out["ORCHESTRATOR_HOSTS"] = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
missing = [k for k in ("JWT_SECRET", "ORCHESTRATOR_INTERNAL_TOKEN") if k not in out]
if missing:
    sys.exit(f"в {src} нет обязательных ключей: {missing}")
with open(dst, "w") as fh:
    for key, value in out.items():
        fh.write(f"{key}={value}\n")
print("  записан", dst, "ключей:", len(out), "| YooKassa:", "настроена" if out.get("YOOKASSA_SHOP_ID") else "НЕТ shop id (воркер стартует, продления упадут в past_due)")
PY
chmod 600 "$SECRETS/billing-worker.env"; chown root:root "$SECRETS/billing-worker.env"

echo "== 5. проверка доступа по WG-адресу от имени max_billing"
PGPASSWORD="$PW" psql -h "$CORE_WG_IP" -p "$PG_PORT" -U max_billing -d "$PG_DB" -tAc \
  "select 'plans='||count(*) from billing_plans" | sed 's/^/  /'
PGPASSWORD="$PW" psql -h "$CORE_WG_IP" -p "$PG_PORT" -U max_billing -d "$PG_DB" -tAc \
  "select 'users_write='||has_table_privilege('max_billing','users','UPDATE')" | sed 's/^/  /'
echo "BILLING_ACCESS_DONE core postgres=$CORE_WG_IP:$PG_PORT role=max_billing env=$SECRETS/billing-worker.env"
