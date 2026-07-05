#!/usr/bin/env bash
# Prove the latest backup is RESTORABLE. Loads the platform dump into a SCRATCH
# database (NEVER the live `omnia`) and asserts it comes back with real tables,
# then drops the scratch DB. An untested backup is a hope, not a backup.
set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/opt/omnia-runtime/backups}"
PLATFORM_CTR="${PLATFORM_CTR:-omnia-prod-postgres}"
PLATFORM_USER="${PLATFORM_USER:-omnia}"
PLATFORM_DB="${PLATFORM_DB:-omnia}"
SCRATCH_DB="omnia_restore_test_$$"

latest="$(ls -1d "${BACKUP_ROOT}"/20* 2>/dev/null | tail -1 || true)"
[ -n "$latest" ] || { echo "[restore-test] no backup dir in $BACKUP_ROOT"; exit 1; }
dump="${latest}/platform-${PLATFORM_DB}.sql.gz"
[ -f "$dump" ] || { echo "[restore-test] no platform dump in $latest"; exit 1; }
echo "[restore-test] source: $dump"
echo "[restore-test] scratch DB: $SCRATCH_DB (live '${PLATFORM_DB}' is untouched)"

cleanup(){ docker exec "$PLATFORM_CTR" psql -U "$PLATFORM_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$SCRATCH_DB\";" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker exec "$PLATFORM_CTR" psql -U "$PLATFORM_USER" -d postgres -c "CREATE DATABASE \"$SCRATCH_DB\";" >/dev/null
gunzip -c "$dump" | docker exec -i "$PLATFORM_CTR" psql -U "$PLATFORM_USER" -d "$SCRATCH_DB" -q >/tmp/omnia-restore-test.log 2>&1 || true

tables=$(docker exec "$PLATFORM_CTR" psql -U "$PLATFORM_USER" -d "$SCRATCH_DB" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" | tr -d '[:space:]')
echo "[restore-test] tables restored in public schema: ${tables:-0}"
if [ "${tables:-0}" -ge 1 ]; then
  echo "[restore-test] OK — backup is loadable."
else
  echo "[restore-test] FAIL — dump restored 0 tables (see /tmp/omnia-restore-test.log)"; exit 1
fi
