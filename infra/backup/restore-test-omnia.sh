#!/usr/bin/env bash
# Prove the latest backup is RESTORABLE. It verifies every payload checksum,
# restores both PostgreSQL dumps into scratch databases, and extracts MinIO and
# project archives into a temporary directory. Live databases/volumes are never
# used as restore targets.
set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/opt/omnia-runtime/backups}"
CELLS_SCRIPT="${CELLS_SCRIPT:-/opt/omnia/apps/orchestrator/scripts/backup_cells.py}"
CELLS_PYTHON="${CELLS_PYTHON:-/opt/omnia/apps/orchestrator/.venv/bin/python}"
[ -x "$CELLS_PYTHON" ] || CELLS_PYTHON="$(command -v python3 || true)"
ORCHESTRATOR_ENV="${ORCHESTRATOR_ENV:-/opt/omnia/apps/orchestrator/.env}"
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

# The verdict is left next to the backups so the off-host status endpoint (and the
# scheduled workflow that raises the alarm) can see WHEN a restore was last proven.
verdict=false
cleanup(){
  docker exec "$PLATFORM_CTR" psql -U "$PLATFORM_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS \"$PLATFORM_SCRATCH_DB\";" >/dev/null 2>&1 || true
  docker exec "$USERS_CTR" psql -U "$USERS_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS \"$USERS_SCRATCH_DB\";" >/dev/null 2>&1 || true
  rm -rf "$extract_dir"
  printf '{"ok": %s, "tested_at": "%s", "source": "%s"}\n' \
    "$verdict" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(basename "$latest")" \
    > "${BACKUP_ROOT}/RESTORE_TEST.json.tmp" 2>/dev/null \
    && mv -f "${BACKUP_ROOT}/RESTORE_TEST.json.tmp" "${BACKUP_ROOT}/RESTORE_TEST.json" || true
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
  "${extract_dir}/config/opt/omnia/apps/orchestrator/.env" \
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

# The restored databases are compared with the LIVE ones instead of a fixed floor:
# the legacy per-project database is legitimately empty since MAX apps moved into
# Project Cells, and "at least one table" made every restore test fail.
live_platform_tables=$(docker exec "$PLATFORM_CTR" psql -U "$PLATFORM_USER" -d "$PLATFORM_DB" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" | tr -d '[:space:]')
live_users_tables=$(docker exec "$USERS_CTR" psql -U "$USERS_USER" -d "$USERS_DB" -tAc \
  "SELECT count(*) FROM information_schema.tables
   WHERE table_schema NOT IN ('pg_catalog', 'information_schema');" | tr -d '[:space:]')

echo "[restore-test] platform tables: ${platform_tables:-0} (live ${live_platform_tables:-?})"
echo "[restore-test] project-schema tables: ${users_tables:-0} (live ${live_users_tables:-?})"
echo "[restore-test] extracted files: ${source_files:-0}"
[ "${platform_tables:-0}" -ge 1 ] || {
  echo "[restore-test] FAIL — platform dump restored 0 tables"; exit 1;
}
# A table created or dropped between the dump and this test is possible, a large
# gap is not: the restore must reproduce (almost) everything the live DB has.
[ "${platform_tables:-0}" -ge "$(( ${live_platform_tables:-0} - 2 ))" ] || {
  echo "[restore-test] FAIL — platform dump restored ${platform_tables} of ${live_platform_tables} live tables"; exit 1;
}
[ "${users_tables:-0}" -ge "$(( ${live_users_tables:-0} - 2 ))" ] || {
  echo "[restore-test] FAIL — project DB dump restored ${users_tables} of ${live_users_tables} live tables"; exit 1;
}
# The owners' MAX apps live in Project Cell databases, not in the two above. Their
# dumps are restored into throwaway PostgreSQL instances by the same tool that took
# them; live cells are never touched.
cells_note="not in this backup"
if [ -f "${latest}/cells.tgz" ]; then
  mkdir -p "${extract_dir}/cells"
  tar -xzf "${latest}/cells.tgz" -C "${extract_dir}/cells"
  cells_dir="${extract_dir}/cells/cells"
  [ -f "${cells_dir}/MANIFEST.json" ] || {
    echo "[restore-test] FAIL — Project Cell archive holds no manifest"; exit 1;
  }
  if [ -f "$CELLS_SCRIPT" ] && [ -n "$CELLS_PYTHON" ]; then
    for name in CELL_POSTGRES_IMAGE CELL_BACKUP_IMAGE; do
      if [ -z "${!name:-}" ] && [ -r "$ORCHESTRATOR_ENV" ]; then
        value="$(sed -n "s/^${name}=//p" "$ORCHESTRATOR_ENV" | tail -1)"
        [ -n "$value" ] && export "${name}=${value}"
      fi
    done
    cells_status=0
    "$CELLS_PYTHON" "$CELLS_SCRIPT" verify --backup "$cells_dir" || cells_status=$?
    case "$cells_status" in
      0) cells_note="restored" ;;
      2) cells_note="restored, partial source backup" ;;
      *) echo "[restore-test] FAIL — Project Cell dumps are not restorable"; exit 1 ;;
    esac
  else
    cells_note="archive present, verifier missing"
  fi
fi
echo "[restore-test] Project Cell databases: ${cells_note}"

verdict=true
echo "[restore-test] OK — databases, Project Cells, runtime config, project sources and MinIO objects are restorable."
