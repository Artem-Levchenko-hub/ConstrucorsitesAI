#!/usr/bin/env bash
# Install only on Core, after both controllers expose authenticated build roots.
# Default validates without deleting manifests or changing the Deployment.
set -euo pipefail

REPO_ROOT=/opt/omnia
STATE_ROOT=/opt/omnia-runtime/state
RETENTION_ROOT=/opt/omnia-runtime/retention
ENV_FILE=
CONTROLLERS_FILE=
PEER_URL=http://172.197.102.6:8003
APPLY=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --repo-root) REPO_ROOT="${2:?}"; shift ;;
    --state-root) STATE_ROOT="${2:?}"; shift ;;
    --retention-root) RETENTION_ROOT="${2:?}"; shift ;;
    --env-file) ENV_FILE="${2:?}"; shift ;;
    --controllers-file) CONTROLLERS_FILE="${2:?}"; shift ;;
    --peer-url) PEER_URL="${2:?}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

COLLECTOR="$REPO_ROOT/apps/orchestrator/scripts/cleanup_registry.py"
# shellcheck source=retention-common.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/retention-common.sh"
retention_prepare
CONTROLLERS_FILE=${CONTROLLERS_FILE:-$RETENTION_ROOT/controllers.json}
[[ "$CONTROLLERS_FILE" =~ ^/[a-zA-Z0-9_./-]+$ && "$(readlink -m "$CONTROLLERS_FILE")" = "$CONTROLLERS_FILE" ]] || {
  echo 'controller file path must be canonical and free of symlinks' >&2; exit 2;
}
case "$CONTROLLERS_FILE/" in
  "$STATE_ROOT/"*) echo 'controller coverage must be outside live state' >&2; exit 2 ;;
esac

VALIDATION_CONFIG=$CONTROLLERS_FILE
if [ ! -e "$CONTROLLERS_FILE" ]; then
  VALIDATION_CONFIG="$temp_dir/controllers.json"
  "$PYTHON" - "$VALIDATION_CONFIG" "$PEER_URL" <<'PY'
import json
import pathlib
import sys

pathlib.Path(sys.argv[1]).write_text(json.dumps({"version": 1, "controllers": [
    {"name": "core", "url": "local"}, {"name": "commerce", "url": sys.argv[2]},
]}), encoding="utf-8")
PY
  chmod 0600 "$VALIDATION_CONFIG"
  if [ "$EUID" = 0 ]; then chown "$SERVICE_USER:$SERVICE_GROUP" "$VALIDATION_CONFIG"; fi
fi

# Validation has its own observation store, so it never rewrites a live journal.
install -d -m 0700 "$temp_dir/journal"
if [ "$EUID" = 0 ]; then chown "$SERVICE_USER:$SERVICE_GROUP" "$temp_dir/journal"; fi
retention_validate --state-root "$STATE_ROOT" --controllers-file "$VALIDATION_CONFIG" --journal-root "$temp_dir/journal"

JOURNAL_ROOT="$RETENTION_ROOT/registry"
cat > "$temp_dir/yleum-registry-gc.service" <<EOF
[Unit]
Description=Collect obsolete Yleum application manifests and registry blobs
After=network-online.target yleum-orchestrator.service
Wants=network-online.target
ConditionPathIsDirectory=$STATE_ROOT

[Service]
Type=oneshot
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$ORCH
EnvironmentFile=$ENV_FILE
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStartPre=$PYTHON $COLLECTOR --state-root $STATE_ROOT --controllers-file $CONTROLLERS_FILE --journal-root $JOURNAL_ROOT --report $JOURNAL_ROOT/registry-recovery-report.json --recover-only
ExecStart=$PYTHON $COLLECTOR --state-root $STATE_ROOT --controllers-file $CONTROLLERS_FILE --journal-root $JOURNAL_ROOT --apply
ExecStopPost=$PYTHON $COLLECTOR --state-root $STATE_ROOT --controllers-file $CONTROLLERS_FILE --journal-root $JOURNAL_ROOT --report $JOURNAL_ROOT/registry-recovery-report.json --recover-only
TimeoutStartSec=900
TimeoutStopSec=300
Nice=19
IOSchedulingClass=idle
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$JOURNAL_ROOT
MemoryMax=1G
CPUQuota=50%
UMask=0077
EOF
cat > "$temp_dir/yleum-registry-gc.timer" <<'EOF'
[Unit]
Description=Run Yleum registry retention nightly

[Timer]
OnCalendar=*-*-* 04:37:00 Europe/Moscow
RandomizedDelaySec=60
Persistent=true
Unit=yleum-registry-gc.service

[Install]
WantedBy=timers.target
EOF
systemd-analyze verify "$temp_dir/yleum-registry-gc.service" "$temp_dir/yleum-registry-gc.timer"
if [ "$APPLY" = 0 ]; then
  cat "$temp_dir/yleum-registry-gc.service" "$temp_dir/yleum-registry-gc.timer"
  echo 'validated; use --apply on Core to install and enable the timer'
  exit 0
fi

if [ "$VALIDATION_CONFIG" != "$CONTROLLERS_FILE" ]; then
  [[ -d "$(dirname -- "$CONTROLLERS_FILE")" && ! -e "$CONTROLLERS_FILE" ]] || {
    echo 'controller configuration appeared or its parent is missing' >&2; exit 2;
  }
  # Never replace a concurrently provisioned coverage file.
  "$PYTHON" - "$VALIDATION_CONFIG" "$CONTROLLERS_FILE" "$SERVICE_USER" "$SERVICE_GROUP" <<'PY'
import grp
import os
import pathlib
import pwd
import sys

payload = pathlib.Path(sys.argv[1]).read_bytes()
fd = os.open(sys.argv[2], os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, "wb") as stream:
    os.fchown(stream.fileno(), pwd.getpwnam(sys.argv[3]).pw_uid, grp.getgrnam(sys.argv[4]).gr_gid)
    stream.write(payload)
    stream.flush()
    os.fsync(stream.fileno())
PY
fi
[[ ! -L "$JOURNAL_ROOT" && "$(readlink -m "$JOURNAL_ROOT")" = "$JOURNAL_ROOT" ]] || {
  echo 'unsafe registry journal directory' >&2; exit 2;
}
install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$JOURNAL_ROOT"
retention_install yleum-registry-gc
