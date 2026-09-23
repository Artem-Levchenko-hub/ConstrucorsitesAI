#!/usr/bin/env bash
# Omnia nightly backup — the existential-risk mitigation (everything on one VPS).
#
# Captures everything a disk failure would erase:
#   1. Platform DB      — omnia-prod-postgres / db `omnia` (users, projects, wallets, snapshots meta)
#   2. Per-project DBs  — omnia-postgres-users / db `omnia_users` (ALL generated-app schemas, one dump)
#   3. Project sources  — /opt/omnia-runtime/projects (generated source + snapshots)
#   4. MinIO objects    — photos, generated media, uploads and preview artefacts
#   5. Runtime config   — current production env files needed to boot a new host
#   6. Project Cells    — the databases of the owners' MAX apps plus the
#                         orchestrator state journal (backup_cells.py)
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
# The archive helper only needs `tar`. Releases run SHA-tagged images, so the
# floating `omnia-api:prod` tag may be absent (that silently broke three nightly
# backups in September 2026): prefer the image of the running API container.
API_CTR="${API_CTR:-omnia-prod-api}"
if [ -z "${MINIO_BACKUP_IMAGE:-}" ]; then
  MINIO_BACKUP_IMAGE="$(docker inspect "$API_CTR" --format '{{.Config.Image}}' 2>/dev/null || true)"
  MINIO_BACKUP_IMAGE="${MINIO_BACKUP_IMAGE:-omnia-api:prod}"
fi
RUNTIME_ENV="${RUNTIME_ENV:-/opt/omnia-runtime/.env}"
# This is the EnvironmentFile loaded by omnia-orchestrator.service. Backing up
# a legacy mirror would produce a bundle that cannot faithfully boot the daemon.
ORCHESTRATOR_ENV="${ORCHESTRATOR_ENV:-/opt/omnia/apps/orchestrator/.env}"
# The MAX apps' own data lives in per-cell volumes, not in the databases above.
# backup_cells.py dumps each one logically; it needs the same digest-pinned
# images the orchestrator uses, which are declared in that env file.
CELLS_SCRIPT="${CELLS_SCRIPT:-/opt/omnia/apps/orchestrator/scripts/backup_cells.py}"
STATE_ROOT="${STATE_ROOT:-/opt/omnia-runtime/state}"
# The host python is 3.11 and the script needs 3.12+; the orchestrator's own
# interpreter is the one that matches its code. Never run the script through a
# mode bit: a checkout without +x would silently skip the cell backup.
CELLS_PYTHON="${CELLS_PYTHON:-/opt/omnia/apps/orchestrator/.venv/bin/python}"
[ -x "$CELLS_PYTHON" ] || CELLS_PYTHON="$(command -v python3 || true)"
FULLSTACK_ENV="${FULLSTACK_ENV:-/opt/omnia/apps/llm-gateway/deploy/full/.env}"

PLATFORM_CTR="${PLATFORM_CTR:-omnia-prod-postgres}"
PLATFORM_USER="${PLATFORM_USER:-omnia}"
PLATFORM_DB="${PLATFORM_DB:-omnia}"
USERS_CTR="${USERS_CTR:-omnia-postgres-users}"
USERS_USER="${USERS_USER:-omnia_root}"
USERS_DB="${USERS_DB:-omnia_users}"

# UTC makes the directory name portable across the VPS, GitHub runners and a
# future restore host in another timezone.
cells_archive=""
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

# 4. Current runtime config. It is intentionally included in the encrypted
#    bundle so a total VPS loss is recoverable. Raw local copy remains mode 0600
#    on the already-secret-bearing production disk and is never exported.
for config in "$RUNTIME_ENV" "$ORCHESTRATOR_ENV" "$FULLSTACK_ENV"; do
  [ -r "$config" ] || fail "required runtime config is not readable: ${config}"
done
log "tarring current runtime configuration..."
tar -czf "${dir}/runtime-config.tgz" -C / \
  "${RUNTIME_ENV#/}" \
  "${ORCHESTRATOR_ENV#/}" \
  "${FULLSTACK_ENV#/}" \
  || fail "runtime configuration archive failed"
chmod 0600 "${dir}/runtime-config.tgz"

# 5. MinIO objects. A short-lived helper container reads the named volume
#    read-only; it never pauses or mutates the live object store.
docker volume inspect "$MINIO_VOLUME" >/dev/null 2>&1 || fail "MinIO volume missing: ${MINIO_VOLUME}"
log "tarring MinIO volume ${MINIO_VOLUME}..."
docker run --rm \
  -v "${MINIO_VOLUME}:/source:ro" \
  -v "${dir}:/backup" \
  "$MINIO_BACKUP_IMAGE" \
  tar -czf /backup/minio-data.tgz -C /source . \
  || fail "MinIO archive failed"

# 5b. Project Cell databases and the orchestrator state journal. A single busy or
#     halted cell must not cost the whole nightly backup, so a partial result
#     (exit 2) is reported and kept; only a hard failure aborts.
if [ -f "$CELLS_SCRIPT" ] && [ -d "$STATE_ROOT" ]; then
  [ -n "$CELLS_PYTHON" ] || fail "no python to run ${CELLS_SCRIPT}"
  for name in CELL_POSTGRES_IMAGE CELL_BACKUP_IMAGE; do
    if [ -z "${!name:-}" ] && [ -r "$ORCHESTRATOR_ENV" ]; then
      # Read only these two lines: the file holds production secrets.
      value="$(sed -n "s/^${name}=//p" "$ORCHESTRATOR_ENV" | tail -1)"
      [ -n "$value" ] && export "${name}=${value}"
    fi
  done
  log "backing up Project Cell databases..."
  cells_status=0
  "$CELLS_PYTHON" "$CELLS_SCRIPT" backup --state-root "$STATE_ROOT" --out "${dir}/cells" \
    || cells_status=$?
  case "$cells_status" in
    0) : ;;
    2) log "WARNING: Project Cell backup is partial — see cells/MANIFEST.json" ;;
    *) fail "Project Cell backup failed (exit ${cells_status})" ;;
  esac
  [ -f "${dir}/cells/MANIFEST.json" ] || fail "Project Cell backup left no manifest"
  tar -czf "${dir}/cells.tgz" -C "${dir}" cells || fail "Project Cell archive failed"
  rm -rf "${dir}/cells"
  chmod 600 "${dir}/cells.tgz"
  cells_archive=1
else
  # Not a silent skip: this is a real gap in the night's backup.
  log "WARNING: Project Cell backup skipped (no ${CELLS_SCRIPT} or ${STATE_ROOT})"
fi

# 6. Integrity: refuse a backup whose payloads are suspiciously empty (a silent
#    pg_dump failure that still exits 0 would otherwise ship a useless backup).
for f in "${dir}/platform-${PLATFORM_DB}.sql.gz" "${dir}/projects-${USERS_DB}.sql.gz"; do
  sz=$(stat -c%s "$f" 2>/dev/null || echo 0)
  [ "$sz" -ge 200 ] || fail "dump ${f} is only ${sz} bytes — aborting (treat as failure)"
done
for f in "${dir}/projects-src.tgz" "${dir}/minio-data.tgz" "${dir}/runtime-config.tgz"; do
  sz=$(stat -c%s "$f" 2>/dev/null || echo 0)
  [ "$sz" -ge 200 ] || fail "archive ${f} is only ${sz} bytes — aborting"
done
if [ -f "${dir}/cells.tgz" ]; then
  sz=$(stat -c%s "${dir}/cells.tgz" 2>/dev/null || echo 0)
  [ "$sz" -ge 200 ] || fail "archive ${dir}/cells.tgz is only ${sz} bytes — aborting"
fi

# Content, not just size: a green report about an archive that holds no platform
# is worse than no report (23.09.2026 the MinIO buckets were wiped while the only
# nightly job on the host reported success without ever touching the platform).
log "verifying that the bundle actually holds the platform..."
for table in users projects snapshots; do
  zcat "${dir}/platform-${PLATFORM_DB}.sql.gz" | grep -q "^CREATE TABLE public\.${table} " \
    || fail "platform dump has no ${table} table — this is not the platform database"
done
live_projects="$(docker exec "$PLATFORM_CTR" psql -U "$PLATFORM_USER" -d "$PLATFORM_DB" -Atc \
  'SELECT count(*) FROM projects' 2>/dev/null || echo unknown)"
archived_repos="$(tar -tzf "${dir}/minio-data.tgz" \
  | grep -c -E '^\./projects/repos/[0-9a-f-]{36}\.tar\.gz/xl\.meta$' || true)"
case "$live_projects" in
  ''|unknown) log "WARNING: could not count live projects; MinIO content check skipped" ;;
  0) : ;;
  *) [ "$archived_repos" -ge "$live_projects" ] \
       || fail "MinIO archive holds ${archived_repos} project repos but the platform has ${live_projects} projects" ;;
esac
log "content check OK: ${archived_repos} project repos archived for ${live_projects} live projects"
(
  cd "$dir"
  sha256sum \
    "platform-${PLATFORM_DB}.sql.gz" \
    "projects-${USERS_DB}.sql.gz" \
    projects-src.tgz \
    minio-data.tgz > SHA256SUMS
  sha256sum runtime-config.tgz >> SHA256SUMS
  [ -f cells.tgz ] && sha256sum cells.tgz >> SHA256SUMS
)
du -sh \
  "${dir}/platform-${PLATFORM_DB}.sql.gz" \
  "${dir}/projects-${USERS_DB}.sql.gz" \
  "${dir}/projects-src.tgz" \
  "${dir}/minio-data.tgz" \
  "${dir}/runtime-config.tgz" \
  ${cells_archive:+"${dir}/cells.tgz"} | tee "${dir}/MANIFEST.txt"

# 7. Build one portable payload and encrypt it with AES-256 + the offline RSA
#    recipient certificate. The unencrypted temporary bundle is always removed.
log "building encrypted off-host bundle..."
tar -czf "$bundle_tmp" -C "$dir" \
  "platform-${PLATFORM_DB}.sql.gz" \
  "projects-${USERS_DB}.sql.gz" \
  projects-src.tgz \
  minio-data.tgz \
  runtime-config.tgz \
  ${cells_archive:+cells.tgz} \
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

# 8. Optional second off-host destination. Only encrypted material is copied;
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

# 9. Retention — prune local backups older than RETENTION_DAYS.
find "$BACKUP_ROOT" -maxdepth 1 -type d -name '20*' -mtime "+${RETENTION_DAYS}" -exec rm -rf {} + 2>/dev/null || true
log "retained local backups from the last ${RETENTION_DAYS} days."
