#!/usr/bin/env bash
# Install archive and local-image retention on each cell host after deployment.
# Default: validate the collector and show units. Explicit --apply installs them.
set -euo pipefail

REPO_ROOT=/opt/omnia
STATE_ROOT=/opt/omnia-runtime/state
RETENTION_ROOT=/opt/omnia-runtime/retention
ENV_FILE=
APPLY=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --repo-root) REPO_ROOT="${2:?}"; shift ;;
    --state-root) STATE_ROOT="${2:?}"; shift ;;
    --retention-root) RETENTION_ROOT="${2:?}"; shift ;;
    --env-file) ENV_FILE="${2:?}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

COLLECTOR="$REPO_ROOT/apps/orchestrator/scripts/cleanup_builds.py"
# shellcheck source=retention-common.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/retention-common.sh"
retention_prepare
cat > "$temp_dir/yleum-archive-gc.service" <<EOF
[Unit]
Description=Collect obsolete Yleum build archives and local application images
After=local-fs.target docker.service
ConditionPathIsDirectory=$STATE_ROOT

[Service]
Type=oneshot
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$ORCH
EnvironmentFile=$ENV_FILE
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$PYTHON $COLLECTOR --state-root $STATE_ROOT --min-age-seconds 7200 --report $RETENTION_ROOT/build-report.json --apply
TimeoutStartSec=300
Nice=19
IOSchedulingClass=idle
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$STATE_ROOT $RETENTION_ROOT
MemoryMax=1G
CPUQuota=50%
UMask=0077
EOF
cat > "$temp_dir/yleum-archive-gc.timer" <<'EOF'
[Unit]
Description=Run Yleum build retention every fifteen minutes

[Timer]
OnCalendar=*:0/15
RandomizedDelaySec=120
Persistent=true
Unit=yleum-archive-gc.service

[Install]
WantedBy=timers.target
EOF

systemd-analyze verify "$temp_dir/yleum-archive-gc.service" "$temp_dir/yleum-archive-gc.timer"
retention_validate --state-root "$STATE_ROOT"

if [ "$APPLY" = 0 ]; then
  cat "$temp_dir/yleum-archive-gc.service" "$temp_dir/yleum-archive-gc.timer"
  echo 'validated; use --apply to install and enable the timer'
  exit 0
fi

retention_install yleum-archive-gc
