#!/usr/bin/env bash
# Prove the latest backup is RESTORABLE. It verifies every payload checksum,
# restores both PostgreSQL dumps into scratch databases, and extracts MinIO and
# project archives into a temporary directory. Live databases/volumes are never
# used as restore targets.
set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/opt/omnia-runtime/backups}"
PLATFORM_CTR="${PLATFORM_CTR:-omnia-prod-postgres}"
PLATFORM_USER="${PLATFORM_USER:-omnia}"
PLATFORM_DB="${PLATFORM_DB:-omnia}"
USERS_CTR="${USERS_CTR:-omnia-postgres-users}"
USERS_USER="${USERS_USER:-omnia_root}"
USERS_DB="${USERS_DB:-omnia_users}"
PLATFORM_SCRATCH_DB="omnia_restore_test_$$"
USERS_SCRATCH_DB="omnia_users_restore_test_$$"
extract_dir="$(mktemp -d "${TMPDIR:-/tmp}/omnia-restore-test.XXXXXX")"

# Choose by absolute modification time, not the directory text. This remains
# correct across the one-time migration from legacy VPS-local names to UTC.
latest="$(
  find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20*' \
    -printf '%T@ %p\n' 2>/dev/null \
    | sort -n \
    | tail -1 \
    | cut -d' ' -f2-
)"
[ -n "$latest" ] || { echo "[restore-test] no backup dir in $BACKUP_ROOT"; exit 1; }
platform_dump="${latest}/platform-${PLATFORM_DB}.sql.gz"
users_dump="${latest}/projects-${USERS_DB}.sql.gz"
encrypted="$(find "$latest" -maxdepth 1 -type f -name 'omnia-backup-*.cms' -print -quit)"
for required in \
  "$platform_dump" \
  "$users_dump" \
  "${latest}/projects-src.tgz" \
  "${latest}/minio-data.tgz" \
  "${latest}/runtime-config.tgz" \
  "${latest}/SHA256SUMS" \
  "${latest}/OFFHOST_SHA256" \
  "$encrypted"; do
  [ -f "$required" ] || { echo "[restore-test] missing required payload: $required"; exit 1; }
done
echo "[restore-test] source: $latest"
echo "[restore-test] scratch DBs: $PLATFORM_SCRATCH_DB, $USERS_SCRATCH_DB (live DBs untouched)"

cleanup(){
  docker exec "$PLATFORM_CTR" psql -U "$PLATFORM_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS \"$PLATFORM_SCRATCH_DB\";" >/dev/null 2>&1 || true
  docker exec "$USERS_CTR" psql -U "$USERS_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS \"$USERS_SCRATCH_DB\";" >/dev/null 2>&1 || true
  rm -rf "$extract_dir"
}
trap cleanup EXIT

echo "[restore-test] verifying checksums and encrypted envelope..."
( cd "$latest" && sha256sum -c SHA256SUMS && sha256sum -c OFFHOST_SHA256 )
openssl cms -cmsout -inform DER -in "$encrypted" -print >/dev/null

echo "[restore-test] extracting file/object archives to scratch..."
tar -xzf "${latest}/projects-src.tgz" -C "$extract_dir"
mkdir -p "${extract_dir}/minio"
tar -xzf "${latest}/minio-data.tgz" -C "${extract_dir}/minio"
mkdir -p "${extract_dir}/config"
tar -xzf "${latest}/runtime-config.tgz" -C "${extract_dir}/config"
for config in \
  "${extract_dir}/config/opt/omnia-runtime/.env" \
  "${extract_dir}/config/opt/omnia-runtime/.env.orchestrator" \
  "${extract_dir}/config/opt/omnia/apps/llm-gateway/deploy/full/.env"; do
  [ -s "$config" ] || { echo "[restore-test] missing restored runtime config"; exit 1; }
done
source_files=$(find "$extract_dir" -type f | wc -l | tr -d '[:space:]')
[ "${source_files:-0}" -ge 1 ] || { echo "[restore-test] archives restored 0 files"; exit 1; }

docker exec "$PLATFORM_CTR" psql -U "$PLATFORM_USER" -d postgres \
  -c "CREATE DATABASE \"$PLATFORM_SCRATCH_DB\";" >/dev/null
gunzip -c "$platform_dump" | docker exec -i "$PLATFORM_CTR" psql \
  -v ON_ERROR_STOP=1 -U "$PLATFORM_USER" -d "$PLATFORM_SCRATCH_DB" -q \
  >/tmp/omnia-platform-restore-test.log 2>&1
platform_tables=$(docker exec "$PLATFORM_CTR" psql -U "$PLATFORM_USER" -d "$PLATFORM_SCRATCH_DB" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" | tr -d '[:space:]')

docker exec "$USERS_CTR" psql -U "$USERS_USER" -d postgres \
  -c "CREATE DATABASE \"$USERS_SCRATCH_DB\";" >/dev/null
gunzip -c "$users_dump" | docker exec -i "$USERS_CTR" psql \
  -v ON_ERROR_STOP=1 -U "$USERS_USER" -d "$USERS_SCRATCH_DB" -q \
  >/tmp/omnia-users-restore-test.log 2>&1
users_tables=$(docker exec "$USERS_CTR" psql -U "$USERS_USER" -d "$USERS_SCRATCH_DB" -tAc \
  "SELECT count(*) FROM information_schema.tables
   WHERE table_schema NOT IN ('pg_catalog', 'information_schema');" | tr -d '[:space:]')

echo "[restore-test] platform tables: ${platform_tables:-0}"
echo "[restore-test] project-schema tables: ${users_tables:-0}"
echo "[restore-test] extracted files: ${source_files:-0}"
[ "${platform_tables:-0}" -ge 1 ] || {
  echo "[restore-test] FAIL — platform dump restored 0 tables"; exit 1;
}
[ "${users_tables:-0}" -ge 1 ] || {
  echo "[restore-test] FAIL — project DB dump restored 0 tables"; exit 1;
}
echo "[restore-test] OK — databases, runtime config, project sources and MinIO objects are restorable."
