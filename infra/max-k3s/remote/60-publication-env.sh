#!/usr/bin/env bash
# Выполняется на core от root (через lib.sh run_remote). Дополняет .env оркестратора значениями
# для размещения опубликованных приложений в кластере runtime (Фаза 3, этап A).
# Аргументы: <registry host> <registry user> <registry password> <artifact base url> <public suffix> <owner user>
# PUBLICATION_BACKEND и PUBLIC_HOST_SUFFIX добавляются только если их ещё нет (docker / apps.<домен>):
# включение кластера как цели — отдельное действие оператора.
set -euo pipefail
registry=$1 user=$2 password=$3 artifacts=$4 suffix=$5 owner=$6
f=/opt/omnia/apps/orchestrator/.env
touch "$f"
set_kv() {
  if grep -q "^$1=" "$f"; then
    python3 - "$f" "$1" "$2" <<'PY'
import re, sys
path, key, value = sys.argv[1:]
text = open(path).read()
open(path, "w").write(re.sub(rf"^{re.escape(key)}=.*$", lambda _m: f"{key}={value}", text, flags=re.M))
PY
  else
    printf '%s=%s\n' "$1" "$2" >> "$f"
  fi
}
set_kv IMAGE_REGISTRY "$registry"
set_kv IMAGE_REGISTRY_USERNAME "$user"
set_kv IMAGE_REGISTRY_PASSWORD "$password"
set_kv K8S_KUBECONFIG_PATH /etc/max-studio/runtime-kubeconfig.yaml
set_kv ARTIFACT_BASE_URL "$artifacts"
grep -q '^PUBLICATION_BACKEND=' "$f" || printf 'PUBLICATION_BACKEND=docker\n' >> "$f"
grep -q '^PUBLIC_HOST_SUFFIX=' "$f" || printf 'PUBLIC_HOST_SUFFIX=%s\n' "$suffix" >> "$f"
chown "$owner:$owner" "$f"; chmod 600 "$f"
grep -E '^(IMAGE_REGISTRY|K8S_|ARTIFACT_BASE_URL|PUBLICATION_BACKEND|PUBLIC_HOST_SUFFIX)' "$f" | sed -E 's/(PASSWORD)=.*/\1=<hidden>/'
echo "  core: .env дополнен; перезапуск оркестратора — вручную, когда нет активных генераций"
