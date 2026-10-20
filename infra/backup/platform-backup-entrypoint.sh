#!/usr/bin/env bash
# Install as /usr/local/sbin/yleum-platform-backup; never pin a bootstrap copy.
set -euo pipefail
umask 077
export BACKUP_PUBLIC_CERT=/opt/omnia/infra/backup/offhost-backup-cert.pem
export MINIO_BACKUP_IMAGE=$(sed -n 's/^CELL_BACKUP_IMAGE=//p' /opt/omnia/apps/orchestrator/.env)
export MINIO_VOLUME=$(docker inspect yleum-prod-minio --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}')
export PLATFORM_DB_MODE=host
export CELLS_PYTHON=/usr/local/sbin/yleum-cells-backup
export BACKUP_PYTHON=/opt/omnia/apps/orchestrator/.venv/bin/python
/opt/omnia/infra/backup/backup-omnia.sh
latest=$(find /opt/omnia-runtime/backups -mindepth 1 -maxdepth 1 -type d -name '20*' -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)
[[ -n "$latest" && -f "$latest/peers.tgz" ]]
name=$(basename "$latest")
dest=/var/backups/max-studio/$(date +%F)/platform-encrypted/$name
install -d -m 0700 "$dest"
cp "$latest"/*.cms "$latest/OFFHOST_SHA256" "$dest/"
chmod 0600 "$dest"/*
echo PLATFORM_BACKUP_DONE
