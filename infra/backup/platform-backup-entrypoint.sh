#!/usr/bin/env bash
# Install as /usr/local/sbin/yleum-platform-backup; never pin a bootstrap copy.
set -euo pipefail
umask 077
# Prepare one private UTC day before starting any collection. GNU install -d
# sets only the final directory's mode; missing leading directories become 0755.
backup_day=$(/usr/bin/python3 - /var/backups/max-studio <<'PY_PRIVATE_DAY'
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path


def utc_day(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")


def directory_identity(info):
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0:
        raise RuntimeError("backup output directory is not root-owned and physical")
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))


def prepare_day(root, day):
    root = Path(root)
    if not root.is_absolute() or ".." in root.parts or not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", day):
        raise ValueError("canonical backup output path and UTC day required")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptors, bindings = [], []
    try:
        current = os.open(root.anchor, flags)
        descriptors.append(current)
        if directory_identity(os.fstat(current))[-1] & 0o022:
            raise RuntimeError("unsafe backup output ancestor")
        for name in root.parts[1:]:
            before = os.stat(name, dir_fd=current, follow_symlinks=False)
            identity = directory_identity(before)
            if identity[-1] & 0o022:
                raise RuntimeError("unsafe backup output ancestor")
            child = os.open(name, flags, dir_fd=current)
            descriptors.append(child)
            if directory_identity(os.fstat(child)) != identity:
                raise RuntimeError("backup output ancestor changed")
            bindings.append((current, name, child, identity))
            current = child
        if stat.S_IMODE(os.fstat(current).st_mode) != 0o700:
            raise RuntimeError("private backup output parent required")
        try:
            os.mkdir(day, 0o700, dir_fd=current)
        except FileExistsError:
            pass
        before = os.stat(day, dir_fd=current, follow_symlinks=False)
        identity = directory_identity(before)
        if identity[-1] not in (0o700, 0o755):
            raise RuntimeError("unsupported backup day permissions")
        child = os.open(day, flags, dir_fd=current)
        descriptors.append(child)
        if directory_identity(os.fstat(child)) != identity:
            raise RuntimeError("backup output day changed")
        if directory_identity(os.stat(day, dir_fd=current, follow_symlinks=False)) != identity:
            raise RuntimeError("backup output day replaced")
        if identity[-1] == 0o755:
            # Tighten only this held, verified root-owned directory, never files.
            os.fchmod(child, 0o700)
        final = (*identity[:-1], 0o700)
        if directory_identity(os.fstat(child)) != final or directory_identity(
                os.stat(day, dir_fd=current, follow_symlinks=False)) != final:
            raise RuntimeError("private backup day unconfirmed")
        for parent, name, fd, expected in bindings:
            if directory_identity(os.fstat(fd)) != expected or directory_identity(
                    os.stat(name, dir_fd=parent, follow_symlinks=False)) != expected:
                raise RuntimeError("backup output ancestor replaced")
        os.fsync(child)
        os.fsync(current)
        return str(root / day)
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


if __name__ == "__main__":
    if os.geteuid() != 0:
        raise RuntimeError("root backup output owner required")
    print(prepare_day(sys.argv[1], utc_day()))
PY_PRIVATE_DAY
)
export BACKUP_PUBLIC_CERT=/opt/omnia/infra/backup/offhost-backup-cert.pem
# Keep legacy ciphertext until its operator explicitly approves expiry.
export BACKUP_PRUNE_ENABLED="${BACKUP_PRUNE_ENABLED:-false}"
export MINIO_BACKUP_IMAGE=$(sed -n 's/^CELL_BACKUP_IMAGE=//p' /opt/omnia/apps/orchestrator/.env)
export MINIO_VOLUME=$(docker inspect yleum-prod-minio --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}')
export PLATFORM_DB_MODE=host
export CELLS_PYTHON=/usr/local/sbin/yleum-cells-backup
export BACKUP_PYTHON=/opt/omnia/apps/orchestrator/.venv/bin/python
/opt/omnia/infra/backup/backup-omnia.sh
latest=$(find /opt/omnia-runtime/backups -mindepth 1 -maxdepth 1 -type d -name '20*' -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)
[[ -n "$latest" && -f "$latest/peers.tgz" ]]
name=$(basename "$latest")
dest="$backup_day/platform-encrypted/$name"
install -d -m 0700 "$dest"
cp "$latest"/*.cms "$latest/OFFHOST_SHA256" "$dest/"
chmod 0600 "$dest"/*
echo PLATFORM_BACKUP_DONE
