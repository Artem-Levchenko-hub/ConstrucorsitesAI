#!/usr/bin/env bash
# Фаза 3, этап A (хвост): ночная копия баз опубликованных приложений в кластере runtime.
# Данные приложений живут в PVC (local-path) и хостовым max-backup (Stage 1) не покрывались.
# Каждое приложение — namespace app-<project>: логический дамп project-postgres и core-postgres
# в /var/backups/max-studio/<день>/apps/, откуда max-backup-push.sh уносит их на соседний хост.
# Запускается от root на runtime: ssh max-runtime sudo bash -s < этот файл
set -euo pipefail
cat > /usr/local/bin/max-apps-backup.sh <<'EOF'
#!/usr/bin/env bash
# Дамп баз каждого опубликованного приложения (namespace app-*, объекты оркестратора).
# Пароль берётся из Secret'а приложения; дамп идёт через kubectl exec в под базы.
# Одно неотвечающее приложение — предупреждение, не провал всей ночи.
set -euo pipefail
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
DEST=/var/backups/max-studio/$(date +%F)/apps
install -d -m 700 "$DEST"
ok=0; failed=0
for ns in $(k3s kubectl get ns -l app.kubernetes.io/managed-by=omnia-orchestrator -o jsonpath='{.items[*].metadata.name}'); do
  retired=$(k3s kubectl get ns "$ns" -o jsonpath='{.metadata.labels.omnia\.retired}')
  [ "$retired" = "true" ] && continue
  for pair in "project-postgres:app-config" "core-postgres:core-config"; do
    sts=${pair%%:*}; secret=${pair##*:}
    pw=$(k3s kubectl -n "$ns" get secret "$secret" -o jsonpath='{.data.POSTGRES_PASSWORD}' 2>/dev/null | base64 -d || true)
    if [ -z "$pw" ] || ! k3s kubectl -n "$ns" get pod "$sts-0" >/dev/null 2>&1; then
      echo "apps: $ns/$sts — no pod or secret, skipped"; failed=$((failed+1)); continue
    fi
    out="$DEST/$ns-$sts.sql.gz"
    if k3s kubectl -n "$ns" exec "$sts-0" -c postgres -- sh -c "PGPASSWORD='$pw' pg_dumpall -U postgres -h 127.0.0.1" 2>/dev/null | gzip > "$out" \
       && [ "$(stat -c %s "$out")" -gt 200 ]; then
      chmod 600 "$out"; ok=$((ok+1))
    else
      rm -f "$out"; echo "apps: $ns/$sts — dump failed"; failed=$((failed+1))
    fi
  done
done
echo "apps: $ok dump(s) ok, $failed failed → $DEST"
[ "$failed" -eq 0 ] || exit 2
EOF
chmod 755 /usr/local/bin/max-apps-backup.sh
install -d /etc/systemd/system/max-backup.service.d
# «-» перед командой: частичный результат (exit 2) не отменяет остальной ночной бэкап
printf '[Service]\nExecStartPre=-/usr/local/bin/max-apps-backup.sh\n' > /etc/systemd/system/max-backup.service.d/apps.conf
systemctl daemon-reload
echo "== пробный прогон"
/usr/local/bin/max-apps-backup.sh || echo "(partial: exit $?)"
ls -la "/var/backups/max-studio/$(date +%F)/apps/" 2>/dev/null | tail -n +2
echo "RUNTIME_APPS_BACKUP_DONE $(hostname)"
