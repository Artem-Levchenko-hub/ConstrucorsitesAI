#!/usr/bin/env bash
# Keep controller credential ownership; only a fresh backup stage changes owner.
set -euo pipefail
umask 077
[[ "$#" -eq 7 && "$1" == /opt/omnia/apps/orchestrator/scripts/backup_cells.py &&
   "$2" == backup && "$3" == --local-scope && "$4" == --state-root &&
   "$5" == /opt/omnia-runtime/state && "$6" == --out ]] || exit 64
[[ "$7" =~ ^/opt/omnia-runtime/backups/[0-9]{8}-[0-9]{6}/cells$ && ! -e "$7" && ! -L "$7" ]] || exit 65
[[ -n "${CELL_POSTGRES_IMAGE:-}" && -n "${CELL_BACKUP_IMAGE:-}" ]] || exit 66
[[ -n "${BACKUP_PLATFORM_CENSUS_JSON:-}" ]] || exit 66
stage=$(mktemp -d /opt/omnia-runtime/backups/.cells-backup.XXXXXX)
[[ "$stage" == /opt/omnia-runtime/backups/.cells-backup.* && -d "$stage" && ! -L "$stage" ]] || exit 67
chown zeuszcz:zeuszcz "$stage"
rc=0
sudo -n -u zeuszcz env CELL_POSTGRES_IMAGE="$CELL_POSTGRES_IMAGE" CELL_BACKUP_IMAGE="$CELL_BACKUP_IMAGE" \
  BACKUP_PLATFORM_CENSUS_JSON="$BACKUP_PLATFORM_CENSUS_JSON" \
  /opt/omnia/apps/orchestrator/.venv/bin/python -B "$1" backup --local-scope \
  --resolve-docker-sources-with-sudo \
  --state-root "$5" --out "$stage/cells" || rc=$?
if [[ -d "$stage/cells" && ! -L "$stage/cells" ]]; then mv -- "$stage/cells" "$7"; fi
# Failed evidence is never recursively deleted.
rmdir -- "$stage" || true
exit "$rc"
