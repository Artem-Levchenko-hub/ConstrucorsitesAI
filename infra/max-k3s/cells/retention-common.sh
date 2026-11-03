#!/usr/bin/env bash
# Shared validation for the addressed build-retention installers. Source only.

retention_prepare() {
  local path
  ENV_FILE=${ENV_FILE:-$REPO_ROOT/apps/orchestrator/.env}
  for path in "$REPO_ROOT" "$STATE_ROOT" "$RETENTION_ROOT" "$ENV_FILE"; do
    [[ "$path" =~ ^/[a-zA-Z0-9_./-]+$ && "$(readlink -m "$path")" = "$path" ]] || {
      echo 'paths must be absolute, canonical and free of symlinks' >&2; exit 2;
    }
  done
  [[ -d "$REPO_ROOT" && -d "$STATE_ROOT" && -f "$ENV_FILE" ]] || {
    echo 'deploy the orchestrator and its environment first' >&2; exit 2;
  }
  case "$RETENTION_ROOT/" in
    "$STATE_ROOT/"*|"$REPO_ROOT/"*) echo 'retention journals must be outside live state/code' >&2; exit 2 ;;
  esac
  case "$STATE_ROOT/" in
    "$RETENTION_ROOT/"*) echo 'retention journals must not contain live state' >&2; exit 2 ;;
  esac
  SERVICE_USER=$(stat -c %U "$STATE_ROOT")
  SERVICE_GROUP=$(stat -c %G "$STATE_ROOT")
  [[ "$SERVICE_USER" != root && "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]*$ && "$SERVICE_GROUP" =~ ^[a-z_][a-z0-9_-]*$ ]] || {
    echo 'state must belong to the non-root orchestrator account' >&2; exit 2;
  }
  ORCH="$REPO_ROOT/apps/orchestrator"
  PYTHON="$ORCH/.venv/bin/python"
  [[ -x "$PYTHON" && -f "$COLLECTOR" ]] || { echo 'deploy the collector first' >&2; exit 2; }
  if [ "$APPLY" = 1 ]; then
    [ "$EUID" = 0 ] || { echo 'installation requires sudo' >&2; exit 2; }
  elif [ "$EUID" != 0 ]; then
    [ "$(id -un)" = "$SERVICE_USER" ] || { echo 'validate as the state owner or sudo' >&2; exit 2; }
  fi
  if [ -e "$RETENTION_ROOT" ]; then
    [[ -d "$RETENTION_ROOT" && "$(stat -c %U "$RETENTION_ROOT")" = "$SERVICE_USER" && "$(stat -c %a "$RETENTION_ROOT")" = 700 ]] || {
      echo 'retention directory must be private and owned by the state owner' >&2; exit 2;
    }
  elif [ "$APPLY" = 1 ]; then
    install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$RETENTION_ROOT"
  fi
  temp_dir=$(mktemp -d)
  if [ "$EUID" = 0 ]; then chown "$SERVICE_USER:$SERVICE_GROUP" "$temp_dir"; fi
  trap 'rm -rf -- "$temp_dir"' EXIT
}

retention_validate() {
  local -a owner=()
  if [ "$EUID" = 0 ]; then owner=(runuser -u "$SERVICE_USER" --); fi
  # Parse dotenv as data, never source credentials as shell code or echo them.
  (cd "$ORCH" && "${owner[@]}" "$PYTHON" - "$ENV_FILE" "$COLLECTOR" "$@" <<'PY'
import os
import subprocess
import sys

from dotenv import dotenv_values

environment = dict(os.environ)
environment.update({key: value for key, value in dotenv_values(sys.argv[1]).items() if value is not None})
environment["PYTHONDONTWRITEBYTECODE"] = "1"
raise SystemExit(subprocess.run([sys.executable, *sys.argv[2:]], env=environment).returncode)
PY
  )
}

retention_install() {
  local prefix=$1 name
  local backup_dir="/var/backups/$prefix/$(date -u +%Y%m%dT%H%M%SZ)-$$"
  install -d -m 0700 "$backup_dir"
  for name in "$prefix.service" "$prefix.timer"; do
    if [ -e "/etc/systemd/system/$name" ]; then
      cp -a -- "/etc/systemd/system/$name" "$backup_dir/$name"
    fi
    install -m 0644 "$temp_dir/$name" "/etc/systemd/system/$name"
  done
  systemctl daemon-reload
  systemctl enable --now "$prefix.timer"
  systemctl is-active "$prefix.timer"
  systemctl list-timers "$prefix.timer" --no-pager
  echo "previous units: $backup_dir"
}
