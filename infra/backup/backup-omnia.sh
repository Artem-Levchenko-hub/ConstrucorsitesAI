#!/usr/bin/env bash
# Omnia nightly backup — the existential-risk mitigation (everything on one VPS).
#
# Captures everything a disk failure would erase:
#   1. Platform DB      — omnia-prod-postgres / db `omnia` (users, projects, wallets, snapshots meta)
#   2. Per-project DBs  — omnia-postgres-users / db `omnia_users` (ALL generated-app schemas, one dump)
#   3. Project sources  — /opt/omnia-runtime/projects (generated source + snapshots)
#   4. MinIO objects    — photos, generated media, uploads and preview artefacts
#
# pg_dump is a consistent, read-only snapshot: it does NOT lock or interrupt the
# running apps. The complete bundle is checksummed and CMS-encrypted before it is
# exposed for the off-host GitHub Actions copy. Only the public certificate lives
# on the server; the private restore key must stay offline.
#
# Install (on the VPS):
#   crontab -e  ->  15 3 * * * /opt/omnia/infra/backup/backup-omnia.sh >> /opt/omnia-runtime/logs/backup.log 2>&1
# Restore: see restore-test-omnia.sh (proves a dump is loadable into a scratch DB).
set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/opt/omnia-runtime/backups}"
PROJECTS_DIR="${PROJECTS_DIR:-/opt/omnia-runtime/projects}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
OFFHOST_DEST="${BACKUP_OFFHOST_DEST:-}" # optional second encrypted copy via rsync/scp
PUBLIC_CERT="${BACKUP_PUBLIC_CERT:-/opt/omnia/infra/backup/offhost-backup-cert.pem}"
MINIO_VOLUME="${MINIO_VOLUME:-full_minio-data}"
MINIO_BACKUP_IMAGE="${MINIO_BACKUP_IMAGE:-omnia-api:prod}"

PLATFORM_CTR="${PLATFORM_CTR:-omnia-prod-postgres}"
PLATFORM_USER="${PLATFORM_USER:-omnia}"
PLATFORM_DB="${PLATFORM_DB:-omnia}"
USERS_CTR="${USERS_CTR:-omnia-postgres-users}"
USERS_USER="${USERS_USER:-omnia_root}"
USERS_DB="${USERS_DB:-omnia_users}"

# UTC makes the directory name portable across the VPS, GitHub runners and a
# future restore host in another timezone.
ts="$(date -u +%Y%m%d-%H%M%S)"
dir="${BACKUP_ROOT}/${ts}"
bundle_tmp="${BACKUP_ROOT}/.omnia-backup-${ts}.tgz"
mkdir -p "$dir"
log(){ echo "[backup ${ts}] $*"; }
fail(){ log "ERROR: $*"; exit 1; }
cleanup(){ rm -f "$bundle_tmp"; }
trap cleanup EXIT

command -v docker >/dev/null || fail "docker not found"
command -v openssl >/dev/null || fail "openssl not found"
[ -r "$PUBLIC_CERT" ] || fail "public encryption certificate not readable: ${PUBLIC_CERT}"

# 1. Platform DB — pipe pg_dump | gzip; the dump must be non-trivially sized.
log "dumping platform DB ${PLATFORM_DB}..."
docker exec "$PLATFORM_CTR" pg_dump -U "$PLATFORM_USER" -d "$PLATFORM_DB" --no-owner --clean --if-exists \
  | gzip > "${dir}/platform-${PLATFORM_DB}.sql.gz" || fail "platform pg_dump failed"

# 2. Per-project DB host — one dump captures every project schema.
log "dumping per-project DB ${USERS_DB} (all schemas)..."
docker exec "$USERS_CTR" pg_dump -U "$USERS_USER" -d "$USERS_DB" --no-owner --clean --if-exists \
  | gzip > "${dir}/projects-${USERS_DB}.sql.gz" || fail "per-project pg_dump failed"

# 3. Project sources + snapshots. Missing source storage is a failed full backup.
[ -d "$PROJECTS_DIR" ] || fail "project source directory missing: ${PROJECTS_DIR}"
log "tarring project sources (${PROJECTS_DIR})..."
tar -czf "${dir}/projects-src.tgz" -C "$(dirname "$PROJECTS_DIR")" "$(basename "$PROJECTS_DIR")" \
  || fail "projects tar failed"

# 4. MinIO objects. A short-lived helper container reads the named volume
#    read-only; it never pauses or mutates the live object store.
docker volume inspect "$MINIO_VOLUME" >/dev/null 2>&1 || fail "MinIO volume missing: ${MINIO_VOLUME}"
log "tarring MinIO volume ${MINIO_VOLUME}..."
docker run --rm \
  -v "${MINIO_VOLUME}:/source:ro" \
  -v "${dir}:/backup" \
  "$MINIO_BACKUP_IMAGE" \
  tar -czf /backup/minio-data.tgz -C /source . \
  || fail "MinIO archive failed"

# 5. Integrity: refuse a backup whose payloads are suspiciously empty (a silent
#    pg_dump failure that still exits 0 would otherwise ship a useless backup).
for f in "${dir}/platform-${PLATFORM_DB}.sql.gz" "${dir}/projects-${USERS_DB}.sql.gz"; do
  sz=$(stat -c%s "$f" 2>/dev/null || echo 0)
  [ "$sz" -ge 200 ] || fail "dump ${f} is only ${sz} bytes — aborting (treat as failure)"
done
for f in "${dir}/projects-src.tgz" "${dir}/minio-data.tgz"; do
  sz=$(stat -c%s "$f" 2>/dev/null || echo 0)
  [ "$sz" -ge 200 ] || fail "archive ${f} is only ${sz} bytes — aborting"
done
(
  cd "$dir"
  sha256sum \
    "platform-${PLATFORM_DB}.sql.gz" \
    "projects-${USERS_DB}.sql.gz" \
    projects-src.tgz \
    minio-data.tgz > SHA256SUMS
)
du -sh \
  "${dir}/platform-${PLATFORM_DB}.sql.gz" \
  "${dir}/projects-${USERS_DB}.sql.gz" \
  "${dir}/projects-src.tgz" \
  "${dir}/minio-data.tgz" | tee "${dir}/MANIFEST.txt"

# 6. Build one portable payload and encrypt it with AES-256 + the offline RSA
#    recipient certificate. The unencrypted temporary bundle is always removed.
log "building encrypted off-host bundle..."
tar -czf "$bundle_tmp" -C "$dir" \
  "platform-${PLATFORM_DB}.sql.gz" \
  "projects-${USERS_DB}.sql.gz" \
  projects-src.tgz \
  minio-data.tgz \
  SHA256SUMS \
  MANIFEST.txt \
  || fail "portable backup bundle failed"
encrypted="${dir}/omnia-backup-${ts}.cms"
openssl cms -encrypt -binary -aes-256-cbc \
  -in "$bundle_tmp" \
  -out "$encrypted" \
  -outform DER \
  "$PUBLIC_CERT" \
  || fail "backup encryption failed"
rm -f "$bundle_tmp"
(
  cd "$dir"
  sha256sum "$(basename "$encrypted")" > OFFHOST_SHA256
)
openssl cms -cmsout -inform DER -in "$encrypted" -print >/dev/null \
  || fail "encrypted CMS envelope validation failed"
log "backup complete: ${dir}"

# 7. Optional second off-host destination. Only encrypted material is copied;
#    raw dumps never leave the server through this path.
if [ -n "$OFFHOST_DEST" ]; then
  log "copying encrypted bundle off-host -> ${OFFHOST_DEST}..."
  if command -v rsync >/dev/null; then
    rsync -a "$encrypted" "${dir}/OFFHOST_SHA256" "${OFFHOST_DEST%/}/${ts}/" \
      && log "secondary off-host OK" || fail "off-host rsync failed"
  else
    scp -q "$encrypted" "${dir}/OFFHOST_SHA256" "${OFFHOST_DEST%/}/" \
      && log "secondary off-host OK (scp)" || fail "off-host scp failed"
  fi
fi

# 8. Retention — prune local backups older than RETENTION_DAYS.
find "$BACKUP_ROOT" -maxdepth 1 -type d -name '20*' -mtime "+${RETENTION_DAYS}" -exec rm -rf {} + 2>/dev/null || true
log "retained local backups from the last ${RETENTION_DAYS} days."
