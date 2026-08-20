#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: rollback-manifest.sh write POINTER RELEASE_RECORD FULL_ENV_BACKUP ORCHESTRATOR_ENV_BACKUP RELEASE_SHA ROLLBACK_SHA" >&2
  echo "   or: rollback-manifest.sh read POINTER FIELD" >&2
  exit 2
}

command_name="${1:-}"

if [[ "${command_name}" == write ]]; then
  [[ "$#" -eq 7 ]] || usage
  pointer="$2"
  release_record="$3"
  full_env_backup="$4"
  orchestrator_env_backup="$5"
  release_sha="$6"
  rollback_sha="$7"
  pointer_parent="$(dirname "${pointer}")"

  [[ "${pointer}" == /* && "${release_record}" == /* \
    && "${full_env_backup}" == /* && "${orchestrator_env_backup}" == /* ]] \
    || usage
  [[ -d "${pointer_parent}" && -d "${release_record}" ]] || usage
  [[ -f "${full_env_backup}" && -f "${orchestrator_env_backup}" ]] || usage
  [[ "${release_sha}" =~ ^[0-9a-f]{40}$ ]] || usage
  [[ "${rollback_sha}" =~ ^[0-9a-f]{40}$ ]] || usage

  temp_pointer="$(mktemp "${pointer}.tmp.XXXXXX")"
  cleanup_temp_pointer() {
    rm -f "${temp_pointer}"
  }
  trap cleanup_temp_pointer EXIT
  python3 - \
    "${release_record}" \
    "${full_env_backup}" \
    "${orchestrator_env_backup}" \
    "${release_sha}" \
    "${rollback_sha}" >"${temp_pointer}" <<'PY'
from __future__ import annotations

import json
import sys

keys = (
    "release_record",
    "full_env_backup",
    "orchestrator_env_backup",
    "release_sha",
    "rollback_sha",
)
values = sys.argv[1:]
if any("\n" in value or "\r" in value for value in values):
    raise SystemExit("rollback manifest values must be single-line")
json.dump(dict(zip(keys, values)), sys.stdout, sort_keys=True)
sys.stdout.write("\n")
PY
  chmod 600 "${temp_pointer}"
  mv -f "${temp_pointer}" "${pointer}"
  trap - EXIT
  echo "wrote permission-protected rollback pointer"
  exit 0
fi

if [[ "${command_name}" == read ]]; then
  [[ "$#" -eq 3 ]] || usage
  pointer="$2"
  field="$3"
  [[ -f "${pointer}" ]] || usage
  python3 - "${pointer}" "${field}" <<'PY'
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

pointer = Path(sys.argv[1])
field = sys.argv[2]
expected = {
    "release_record",
    "full_env_backup",
    "orchestrator_env_backup",
    "release_sha",
    "rollback_sha",
}
with pointer.open(encoding="utf-8") as pointer_file:
    document = json.load(pointer_file)
if not isinstance(document, dict) or set(document) != expected or field not in expected:
    raise SystemExit("invalid rollback manifest schema")
if any(not isinstance(value, str) or "\n" in value or "\r" in value for value in document.values()):
    raise SystemExit("invalid rollback manifest value")
for path_field in ("release_record", "full_env_backup", "orchestrator_env_backup"):
    if not document[path_field].startswith("/"):
        raise SystemExit("rollback paths must be absolute")
for sha_field in ("release_sha", "rollback_sha"):
    if re.fullmatch(r"[0-9a-f]{40}", document[sha_field]) is None:
        raise SystemExit("invalid rollback revision")
sys.stdout.write(document[field])
PY
  exit 0
fi

usage
