#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "NOT EXECUTED: Docker Compose is unavailable; production policy was not verified" >&2
  exit 77
fi

repo_root="$(git rev-parse --show-toplevel)"
compose_file="${repo_root}/apps/llm-gateway/deploy/full/docker-compose.yml"
env_example="${repo_root}/apps/llm-gateway/deploy/full/.env.example"
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
    USE_MAX_FINALIZATION_COORDINATOR="true" \
    USE_PROJECT_CELL_ACTIVITY_WATCHDOG="true" \
    USE_GENERATION_EVENT_REPLAY="true" \
    USE_CELL_RESOURCE_PROFILE_V2="true" \
    MAX_GENERATION_DEADLINE_SECONDS="1600" \
    PROJECT_CELL_HEARTBEAT_SECONDS="16" \
    PROJECT_CELL_WATCHDOG_GRACE_SECONDS="21" \
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

assert api["USE_PROJECT_MEMORY"] == "true"
assert worker["USE_PROJECT_MEMORY"] == "true"
assert api["ACCEPTANCE_GAUNTLET_REFERENCE_GATE"] == "false"
assert worker["ACCEPTANCE_GAUNTLET_REFERENCE_GATE"] == "false"
assert api["REFERENCE_CEILING_ENFORCED"] == "false"
assert worker["REFERENCE_CEILING_ENFORCED"] == "false"
expected_finalization = {
    "USE_MAX_FINALIZATION_COORDINATOR": "true",
    "USE_PROJECT_CELL_ACTIVITY_WATCHDOG": "true",
    "USE_GENERATION_EVENT_REPLAY": "true",
    "USE_CELL_RESOURCE_PROFILE_V2": "true",
    "MAX_GENERATION_DEADLINE_SECONDS": "1600",
    "PROJECT_CELL_HEARTBEAT_SECONDS": "16",
    "PROJECT_CELL_WATCHDOG_GRACE_SECONDS": "21",
}
for key, value in expected_finalization.items():
    assert api[key] == value
    assert worker[key] == value
assert "generation-report-worker" not in services
for service in services.values():
    environment = service.get("environment", {})
    assert "DEV_GENERATION_TELEGRAM_REPORTS" not in environment
    assert "TELEGRAM_BOT_TOKEN" not in environment
    assert "TELEGRAM_CHAT_ID" not in environment
PY

grep -qx 'USE_PROJECT_MEMORY=true' "${env_example}"
grep -qx 'USE_MAX_FINALIZATION_COORDINATOR=false' "${env_example}"
grep -qx 'USE_PROJECT_CELL_ACTIVITY_WATCHDOG=false' "${env_example}"
grep -qx 'USE_GENERATION_EVENT_REPLAY=false' "${env_example}"
grep -qx 'USE_CELL_RESOURCE_PROFILE_V2=false' "${env_example}"

echo "rendered production Compose policy passed"
