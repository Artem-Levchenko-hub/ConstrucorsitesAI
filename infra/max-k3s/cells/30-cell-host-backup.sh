#!/usr/bin/env bash
# Фаза 3, этап B: ночная копия ячеек агента на хосте ячеек (commerce) — как в backup-omnia.sh на core,
# но встроенная в хостовый max-backup (Stage 1): базы ячеек и журнал состояния оркестратора
# складываются в /var/backups/max-studio/<день>/cells.tgz, откуда max-backup-push.sh уносит их
# на соседний хост (rrsync, только запись). Запускается от root: ssh <host> sudo bash -s -- ADMIN_USER < этот файл
set -euo pipefail
ADMIN_USER=$1
cat > /usr/local/bin/max-cells-backup.sh <<'EOF'
#!/usr/bin/env bash
# Логическая копия каждой базы ячеек + журнал оркестратора (backup_cells.py), от имени оркестратора:
# каталог состояния 0700 принадлежит ему, а docker доступен через группу. Частичный результат
# (занятая ячейка) — предупреждение, не провал ночи.
set -euo pipefail
ADMIN_USER="__ADMIN_USER__"
ORCH=/opt/omnia/apps/orchestrator
STATE_ROOT=/opt/omnia-runtime/state
DEST=/var/backups/max-studio/$(date +%F)
install -d -m 700 "$DEST"
[ -d "$STATE_ROOT" ] || { echo "cells: no $STATE_ROOT — nothing to back up"; exit 0; }
export CELL_POSTGRES_IMAGE="$(sed -n 's/^CELL_POSTGRES_IMAGE=//p' "$ORCH/.env" | tail -1)"
export CELL_BACKUP_IMAGE="$(sed -n 's/^CELL_BACKUP_IMAGE=//p' "$ORCH/.env" | tail -1)"
scratch=$(mktemp -d /tmp/max-cells-backup.XXXXXX)
chown "$ADMIN_USER" "$scratch"
status=0
sudo -u "$ADMIN_USER" --preserve-env=CELL_POSTGRES_IMAGE,CELL_BACKUP_IMAGE \
  "$ORCH/.venv/bin/python" "$ORCH/scripts/backup_cells.py" backup --state-root "$STATE_ROOT" --out "$scratch/cells" \
  || status=$?
case "$status" in
  0) ;;
  2) echo "cells: WARNING partial backup — see MANIFEST.json" ;;
  *) echo "cells: backup failed (exit $status)"; rm -rf "$scratch"; exit "$status" ;;
esac
[ -f "$scratch/cells/MANIFEST.json" ] || { echo "cells: no manifest"; rm -rf "$scratch"; exit 1; }
tar -czf "$DEST/cells.tgz" -C "$scratch" cells
chmod 600 "$DEST/cells.tgz"
rm -rf "$scratch"
echo "cells: $(tar -tzf "$DEST/cells.tgz" | grep -c '\.dump$' || true) database dump(s) → $DEST/cells.tgz"
EOF
sed -i "s/__ADMIN_USER__/$ADMIN_USER/" /usr/local/bin/max-cells-backup.sh
chmod 755 /usr/local/bin/max-cells-backup.sh
install -d /etc/systemd/system/max-backup.service.d
printf '[Service]\nExecStartPre=/usr/local/bin/max-cells-backup.sh\n' > /etc/systemd/system/max-backup.service.d/cells.conf
systemctl daemon-reload
echo "== пробный прогон"
/usr/local/bin/max-cells-backup.sh
ls -la "/var/backups/max-studio/$(date +%F)/cells.tgz"
echo "CELL_HOST_BACKUP_DONE $(hostname)"
