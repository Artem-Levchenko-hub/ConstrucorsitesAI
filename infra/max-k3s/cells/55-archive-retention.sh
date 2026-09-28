#!/usr/bin/env bash
# Install archive retention on a cell host after deploying the reviewed revision.
# Default: validate the collector and show units. Explicit --apply installs them.
set -euo pipefail

REPO_ROOT=/opt/omnia
STATE_ROOT=/opt/omnia-runtime/state
APPLY=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --repo-root) REPO_ROOT="${2:?}"; shift ;;
    --state-root) STATE_ROOT="${2:?}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

# Values are embedded in systemd directives; reject specifiers and whitespace.
for path in "$REPO_ROOT" "$STATE_ROOT"; do
  [[ "$path" =~ ^/[a-zA-Z0-9_./-]+$ ]] || { echo 'unsafe path' >&2; exit 2; }
done
REPO_ROOT=$(readlink -e "$REPO_ROOT")
[[ -d "$STATE_ROOT" && ! -L "$STATE_ROOT" ]] || { echo 'state root must be a real directory' >&2; exit 2; }
SERVICE_USER=$(stat -c %U "$STATE_ROOT")
SERVICE_GROUP=$(stat -c %G "$STATE_ROOT")
[[ "$SERVICE_USER" != root && "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]*$ && "$SERVICE_GROUP" =~ ^[a-z_][a-z0-9_-]*$ ]] || {
  echo 'state must belong to the non-root orchestrator account' >&2; exit 2;
}
ORCH="$REPO_ROOT/apps/orchestrator"
PYTHON="$ORCH/.venv/bin/python"
COLLECTOR="$ORCH/scripts/cleanup_orphan_archives.py"
[[ -x "$PYTHON" && -f "$COLLECTOR" ]] || { echo 'deploy the orchestrator first' >&2; exit 2; }
if [ "$APPLY" = 1 ]; then
  [ "$EUID" = 0 ] || { echo 'installation requires sudo' >&2; exit 2; }
fi

temp_dir=$(mktemp -d)
trap 'rm -rf -- "$temp_dir"' EXIT
cat > "$temp_dir/yleum-archive-gc.service" <<EOF
[Unit]
Description=Collect old unreferenced Yleum machine archives
After=local-fs.target
ConditionPathIsDirectory=$STATE_ROOT/project-machines/artifacts

[Service]
Type=oneshot
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$ORCH
ExecStart=$PYTHON $COLLECTOR --state-root $STATE_ROOT --min-age-seconds 7200 --apply
TimeoutStartSec=300
Nice=19
IOSchedulingClass=idle
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$STATE_ROOT
UMask=0077
EOF
cat > "$temp_dir/yleum-archive-gc.timer" <<'EOF'
[Unit]
Description=Run Yleum archive retention every fifteen minutes

[Timer]
OnCalendar=*:0/15
RandomizedDelaySec=120
Persistent=true
Unit=yleum-archive-gc.service

[Install]
WantedBy=timers.target
EOF

systemd-analyze verify "$temp_dir/yleum-archive-gc.service" "$temp_dir/yleum-archive-gc.timer"
if [ "$EUID" = 0 ]; then
  runuser -u "$SERVICE_USER" -- "$PYTHON" "$COLLECTOR" --state-root "$STATE_ROOT"
else
  [ "$(id -un)" = "$SERVICE_USER" ] || { echo 'validate as the state owner or sudo' >&2; exit 2; }
  "$PYTHON" "$COLLECTOR" --state-root "$STATE_ROOT"
fi

if [ "$APPLY" = 0 ]; then
  cat "$temp_dir/yleum-archive-gc.service" "$temp_dir/yleum-archive-gc.timer"
  echo 'validated; use --apply to install and enable the timer'
  exit 0
fi

backup_dir="/var/backups/yleum-archive-gc/$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -m 0700 "$backup_dir"
for name in yleum-archive-gc.service yleum-archive-gc.timer; do
  if [ -e "/etc/systemd/system/$name" ]; then
    cp -a -- "/etc/systemd/system/$name" "$backup_dir/$name"
  fi
  install -m 0644 "$temp_dir/$name" "/etc/systemd/system/$name"
done
systemctl daemon-reload
systemctl enable --now yleum-archive-gc.timer
systemctl is-active yleum-archive-gc.timer
systemctl list-timers yleum-archive-gc.timer --no-pager
echo "previous units: $backup_dir"
