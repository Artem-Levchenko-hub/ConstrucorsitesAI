#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "NOT EXECUTED: Docker Compose is unavailable; production policy was not verified" >&2
  exit 77
fi

repo_root="$(git rev-parse --show-toplevel)"
compose_file="${repo_root}/apps/llm-gateway/deploy/full/docker-compose.yml"
rendered="$(mktemp)"
configured_rendered="$(mktemp)"
blank_env="$(mktemp)"
chmod 600 "${blank_env}"
trap 'rm -f "${rendered}" "${configured_rendered}" "${blank_env}"' EXIT

(
  unset USE_PROJECT_MEMORY
  unset ACCEPTANCE_GAUNTLET_REFERENCE_GATE
  unset REFERENCE_CEILING_ENFORCED
  unset DEV_GENERATION_TELEGRAM_REPORTS
  unset TELEGRAM_BOT_TOKEN
  unset TELEGRAM_CHAT_ID
  JWT_SECRET="compose-policy-jwt-secret" \
    SECRETS_ENCRYPTION_KEY="compose-policy-encryption-key" \
    ORCHESTRATOR_INTERNAL_TOKEN="compose-policy-orchestrator-token" \
    NEXTAUTH_SECRET="compose-policy-nextauth-secret" \
    OMNIA_RELEASE_SHA="0123456789abcdef0123456789abcdef01234567" \
    docker compose --env-file "${blank_env}" -f "${compose_file}" config --format json
) >"${rendered}"

(
  JWT_SECRET="compose-policy-jwt-secret" \
    SECRETS_ENCRYPTION_KEY="compose-policy-encryption-key" \
    ORCHESTRATOR_INTERNAL_TOKEN="compose-policy-orchestrator-token" \
    NEXTAUTH_SECRET="compose-policy-nextauth-secret" \
    OMNIA_RELEASE_SHA="0123456789abcdef0123456789abcdef01234567" \
    DEV_GENERATION_TELEGRAM_REPORTS="true" \
    TELEGRAM_BOT_TOKEN="synthetic-compose-token" \
    TELEGRAM_CHAT_ID="-1001234567890" \
    docker compose --env-file "${blank_env}" -f "${compose_file}" config --format json
) >"${configured_rendered}"

python3 - "${rendered}" "${configured_rendered}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

document = json.loads(Path(sys.argv[1]).read_text())
services = document["services"]
api = services["api"]["environment"]
worker = services["worker"]["environment"]
reports = services["generation-report-worker"]["environment"]

assert api["USE_PROJECT_MEMORY"] == "false"
assert worker["USE_PROJECT_MEMORY"] == "false"
assert api["ACCEPTANCE_GAUNTLET_REFERENCE_GATE"] == "false"
assert worker["ACCEPTANCE_GAUNTLET_REFERENCE_GATE"] == "false"
assert api["REFERENCE_CEILING_ENFORCED"] == "false"
assert worker["REFERENCE_CEILING_ENFORCED"] == "false"
assert api["DEV_GENERATION_TELEGRAM_REPORTS"] == "false"
assert worker["DEV_GENERATION_TELEGRAM_REPORTS"] == "false"
assert reports["DEV_GENERATION_TELEGRAM_REPORTS"] == "false"
assert reports["TELEGRAM_BOT_TOKEN"] == ""
assert reports["TELEGRAM_CHAT_ID"] == "0"
assert "TELEGRAM_BOT_TOKEN" not in api
assert "TELEGRAM_CHAT_ID" not in api
assert "TELEGRAM_BOT_TOKEN" not in worker
assert "TELEGRAM_CHAT_ID" not in worker
assert "redis" not in services["generation-report-worker"].get("depends_on", {})

configured = json.loads(Path(sys.argv[2]).read_text())["services"]
configured_reports = configured["generation-report-worker"]["environment"]
assert configured_reports["DEV_GENERATION_TELEGRAM_REPORTS"] == "true"
assert configured_reports["TELEGRAM_BOT_TOKEN"] == "synthetic-compose-token"
assert configured_reports["TELEGRAM_CHAT_ID"] == "-1001234567890"
PY

echo "rendered production Compose policy passed"
