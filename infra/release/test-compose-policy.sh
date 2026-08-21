#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "NOT EXECUTED: Docker Compose is unavailable; production policy was not verified" >&2
  exit 77
fi

repo_root="$(git rev-parse --show-toplevel)"
compose_file="${repo_root}/apps/llm-gateway/deploy/full/docker-compose.yml"
rendered="$(mktemp)"
blank_env="$(mktemp)"
chmod 600 "${blank_env}"
trap 'rm -f "${rendered}" "${blank_env}"' EXIT

(
  unset USE_PROJECT_MEMORY
  unset ACCEPTANCE_GAUNTLET_REFERENCE_GATE
  unset REFERENCE_CEILING_ENFORCED
  JWT_SECRET="compose-policy-jwt-secret" \
    SECRETS_ENCRYPTION_KEY="compose-policy-encryption-key" \
    ORCHESTRATOR_INTERNAL_TOKEN="compose-policy-orchestrator-token" \
    NEXTAUTH_SECRET="compose-policy-nextauth-secret" \
    OMNIA_RELEASE_SHA="0123456789abcdef0123456789abcdef01234567" \
    docker compose --env-file "${blank_env}" -f "${compose_file}" config --format json
) >"${rendered}"

python3 - "${rendered}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

document = json.loads(Path(sys.argv[1]).read_text())
services = document["services"]
api = services["api"]["environment"]
worker = services["worker"]["environment"]

assert api["USE_PROJECT_MEMORY"] == "false"
assert worker["USE_PROJECT_MEMORY"] == "false"
assert api["ACCEPTANCE_GAUNTLET_REFERENCE_GATE"] == "false"
assert worker["ACCEPTANCE_GAUNTLET_REFERENCE_GATE"] == "false"
assert api["REFERENCE_CEILING_ENFORCED"] == "false"
assert worker["REFERENCE_CEILING_ENFORCED"] == "false"
PY

echo "rendered production Compose policy passed"
