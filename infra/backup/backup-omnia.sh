#!/usr/bin/env bash
# Omnia nightly backup — the existential-risk mitigation (everything on one VPS).
#
# Captures the three things a disk failure would erase:
#   1. Platform DB      — omnia-prod-postgres / db `omnia` (users, projects, wallets, snapshots meta)
#   2. Per-project DBs  — omnia-postgres-users / db `omnia_users` (ALL generated-app schemas, one dump)
#   3. Project sources  — /opt/omnia-runtime/projects (generated source + snapshots)
#
# pg_dump is a consistent, read-only snapshot: it does NOT lock or interrupt the
# running apps. Output is timestamped + checksummed, pruned by retention, and
# (opt-in) copied off-host — a LOCAL-only backup does not survive a disk failure,
# which is the whole point, so set BACKUP_OFFHOST_DEST for real safety.
#
# Install (on the VPS):
#   crontab -e  ->  15 3 * * *  BACKUP_OFFHOST_DEST=user@host:/omnia-backups /opt/omnia/infra/backup/backup-omnia.sh >> /opt/omnia-runtime/logs/backup.log 2>&1
# Restore: see restore-test-omnia.sh (proves a dump is loadable into a scratch DB).
set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/opt/omnia-runtime/backups}"
PROJECTS_DIR="${PROJECTS_DIR:-/opt/omnia-runtime/projects}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
OFFHOST_DEST="${BACKUP_OFFHOST_DEST:-}"   # rsync target, e.g. user@host:/omnia-backups  (empty = local only)

PLATFORM_CTR="${PLATFORM_CTR:-omnia-prod-postgres}"
PLATFORM_USER="${PLATFORM_USER:-omnia}"
PLATFORM_DB="${PLATFORM_DB:-omnia}"
USERS_CTR="${USERS_CTR:-omnia-postgres-users}"
USERS_USER="${USERS_USER:-omnia_root}"
USERS_DB="${USERS_DB:-omnia_users}"

ts="$(date +%Y%m%d-%H%M%S)"
dir="${BACKUP_ROOT}/${ts}"
mkdir -p "$dir"
log(){ echo "[backup ${ts}] $*"; }
fail(){ log "ERROR: $*"; exit 1; }

command -v docker >/dev/null || fail "docker not found"

# 1. Platform DB — pipe pg_dump | gzip; the dump must be non-trivially sized.
log "dumping platform DB ${PLATFORM_DB}..."
docker exec "$PLATFORM_CTR" pg_dump -U "$PLATFORM_USER" -d "$PLATFORM_DB" --no-owner --clean --if-exists \
  | gzip > "${dir}/platform-${PLATFORM_DB}.sql.gz" || fail "platform pg_dump failed"

# 2. Per-project DB host — one dump captures every project schema.
log "dumping per-project DB ${USERS_DB} (all schemas)..."
docker exec "$USERS_CTR" pg_dump -U "$USERS_USER" -d "$USERS_DB" --no-owner --clean --if-exists \
  | gzip > "${dir}/projects-${USERS_DB}.sql.gz" || fail "per-project pg_dump failed"

# 3. Project sources + snapshots.
if [ -d "$PROJECTS_DIR" ]; then
  log "tarring project sources (${PROJECTS_DIR})..."
  tar -czf "${dir}/projects-src.tgz" -C "$(dirname "$PROJECTS_DIR")" "$(basename "$PROJECTS_DIR")" \
    || fail "projects tar failed"
else
  log "WARNING: ${PROJECTS_DIR} missing — skipping source tar"
fi

# 4. Integrity: refuse a backup whose dumps are suspiciously empty (a silent
#    pg_dump failure that still exits 0 would otherwise ship a useless backup).
for f in "${dir}/platform-${PLATFORM_DB}.sql.gz" "${dir}/projects-${USERS_DB}.sql.gz"; do
  sz=$(stat -c%s "$f" 2>/dev/null || echo 0)
  [ "$sz" -ge 200 ] || fail "dump ${f} is only ${sz} bytes — aborting (treat as failure)"
done
( cd "$dir" && sha256sum ./* > SHA256SUMS )
du -sh "${dir}"/* | tee "${dir}/MANIFEST.txt"
log "backup complete: ${dir}"

# 5. Off-host copy (opt-in but STRONGLY recommended).
if [ -n "$OFFHOST_DEST" ]; then
  log "copying off-host -> ${OFFHOST_DEST}..."
  if command -v rsync >/dev/null; then
    rsync -az "${dir}/" "${OFFHOST_DEST%/}/${ts}/" && log "off-host OK" || fail "off-host rsync failed"
  else
    scp -q -r "${dir}" "${OFFHOST_DEST%/}/" && log "off-host OK (scp)" || fail "off-host scp failed"
  fi
else
  log "WARNING: BACKUP_OFFHOST_DEST unset — backup is LOCAL ONLY. A disk failure loses it. Set an off-host target."
fi

# 6. Retention — prune local backups older than RETENTION_DAYS.
find "$BACKUP_ROOT" -maxdepth 1 -type d -name '20*' -mtime "+${RETENTION_DAYS}" -exec rm -rf {} + 2>/dev/null || true
log "retained local backups from the last ${RETENTION_DAYS} days."
