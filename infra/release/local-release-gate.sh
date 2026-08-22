#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "release gate failed: $1" >&2
  exit 1
}

[[ -n "${EXPECTED_RELEASE_SHA:-}" ]] || fail "EXPECTED_RELEASE_SHA is required"
if [[ "${EXPECTED_RELEASE_SHA}" == "unknown" ]]; then
  fail "unknown release SHA is invalid"
fi
[[ "${EXPECTED_RELEASE_SHA}" =~ ^[0-9a-f]{7,40}$ ]] \
  || fail "EXPECTED_RELEASE_SHA is invalid"

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" \
  || fail "run this command from a Git worktree"
cd "${repo_root}"
head_sha="$(git rev-parse HEAD)"
[[ "${EXPECTED_RELEASE_SHA}" == "${head_sha}" ]] \
  || fail "expected release does not match the exact Git revision"
[[ -z "$(git status --porcelain=v1 --untracked-files=all)" ]] \
  || fail "Git worktree must be clean before release validation"

[[ -n "${DATABASE_URL:-}" ]] || fail "DATABASE_URL is required"
[[ -n "${DATABASE_TEST_URL:-}" ]] || fail "DATABASE_TEST_URL is required"
[[ -n "${JWT_SECRET:-}" ]] || fail "JWT_SECRET is required"
[[ "${DATABASE_URL}" != "${DATABASE_TEST_URL}" ]] \
  || fail "DATABASE_URL and DATABASE_TEST_URL must be different"

require_local_database_url() {
  local name="$1"
  local value="$2"
  if [[ ! "${value}" =~ (//|@)localhost([:/]|$) \
    && ! "${value}" =~ (//|@)127\.0\.0\.1([:/]|$) \
    && ! "${value}" =~ (//|@)\[::1\]([:/]|$) \
    && ! "${value}" =~ ^postgres(ql)?(\+asyncpg)?:/// ]]; then
    fail "${name} must target a loopback address or local Unix socket"
  fi
}

require_local_database_url DATABASE_URL "${DATABASE_URL}"
require_local_database_url DATABASE_TEST_URL "${DATABASE_TEST_URL}"

for command_name in uv docker corepack python3; do
  command -v "${command_name}" >/dev/null 2>&1 \
    || fail "required command is unavailable: ${command_name}"
done

evidence_root="${repo_root}/.release-evidence"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
evidence_dir="${evidence_root}/${head_sha}-${timestamp}"
mkdir -p "${evidence_dir}"
manifest="${evidence_dir}/manifest.json"
manifest_step_count=0
gate_status="failed"
manifest_closed=false
raw_log_path=""
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '{"release_sha":"%s","started_at":"%s","steps":[' \
  "${head_sha}" "${started_at}" >"${manifest}"

finish_manifest() {
  if [[ "${manifest_closed}" == true ]]; then
    return
  fi
  local finished_at
  finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '],"finished_at":"%s","status":"%s"}\n' \
    "${finished_at}" "${gate_status}" >>"${manifest}"
  manifest_closed=true
}
finalize_gate() {
  if [[ -n "${raw_log_path}" ]]; then
    rm -f "${raw_log_path}"
  fi
  finish_manifest
}
trap finalize_gate EXIT

redact_log() {
  local source_path="$1"
  local destination_path="$2"
  python3 - "${source_path}" "${destination_path}" <<'PY'
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
secret_name = re.compile(r"(?:SECRET|PASSWORD|TOKEN|API_KEY|DATABASE(?:_TEST)?_URL)")
values: set[str] = set()
for name, value in os.environ.items():
    if not value or secret_name.search(name) is None:
        continue
    if len(value) >= 6:
        values.add(value)
    if name.endswith("_URL"):
        try:
            password = urlsplit(value).password
        except ValueError:
            password = None
        if password and len(password) >= 6:
            values.add(password)

output = source.read_text(encoding="utf-8", errors="replace")
for value in sorted(values, key=len, reverse=True):
    output = output.replace(value, "[REDACTED]")
with destination.open("a", encoding="utf-8") as log_file:
    log_file.write(output)
PY
}

run_step() {
  local name="$1"
  local log_name="$2"
  local command_description="$3"
  shift 3
  local log_path="${evidence_dir}/${log_name}"
  local step_started step_finished exit_code
  step_started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '[%s] %s\n' "${step_started}" "${name}" >>"${log_path}"
  raw_log_path="$(mktemp "${TMPDIR:-/tmp}/omnia-release-gate-log.XXXXXX")"
  chmod 600 "${raw_log_path}"
  if "$@" >"${raw_log_path}" 2>&1; then
    exit_code=0
  else
    exit_code=$?
  fi
  redact_log "${raw_log_path}" "${log_path}"
  rm -f "${raw_log_path}"
  raw_log_path=""
  step_finished="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if ((manifest_step_count > 0)); then
    printf ',' >>"${manifest}"
  fi
  printf '{"name":"%s","command":"%s","started_at":"%s","finished_at":"%s","exit_code":%d}' \
    "${name}" "${command_description}" "${step_started}" "${step_finished}" "${exit_code}" \
    >>"${manifest}"
  manifest_step_count=$((manifest_step_count + 1))
  if ((exit_code != 0)); then
    fail "step ${name} failed; inspect ${log_name} in the release evidence directory"
  fi
}

run_step api_sync api.log "API locked dependency sync" bash -c \
  'cd apps/api && uv sync --frozen'
run_step api_ruff api.log "API lint" bash -c 'cd apps/api && uv run ruff check .'
run_step api_mypy api.log "API typecheck" bash -c \
  'cd apps/api && MYPYPATH=src uv run mypy src'
run_step api_migrate api.log "API local database migration" bash -c \
  'cd apps/api && uv run alembic upgrade head'
run_step api_tests api.log "Release-critical API tests" bash -c \
  'cd apps/api && uv run pytest -q \
    tests/test_deploy_gate_defaults.py \
    tests/test_creator_privilege_migration.py \
    tests/test_release_identity.py \
    tests/test_readiness.py \
    tests/test_auth.py \
    tests/test_agent_native.py \
    tests/test_generation_runs.py \
    tests/test_generation_telegram_removal_migration.py \
    tests/test_telegram_reporting_removed.py \
    tests/test_render_settle.py \
    tests/test_project_memory.py \
    tests/test_production_canary.py'

run_step orchestrator_sync orchestrator.log "Orchestrator locked dependency sync" bash -c \
  'cd apps/orchestrator && uv sync --frozen'
run_step orchestrator_ruff orchestrator.log "Release-critical orchestrator lint" bash -c \
  'cd apps/orchestrator && uv run ruff check \
    src/omnia_orchestrator/core/release.py \
    src/omnia_orchestrator/routers/health.py \
    tests/test_health.py'
run_step orchestrator_mypy orchestrator.log "Orchestrator typecheck" bash -c \
  'cd apps/orchestrator && uv run mypy src'
run_step orchestrator_tests orchestrator.log "Orchestrator tests" bash -c \
  'cd apps/orchestrator && uv run pytest -q'

run_step web_install web.log "Web frozen install" bash -c \
  'cd apps/web && corepack pnpm install --frozen-lockfile && git diff --exit-code -- package.json pnpm-lock.yaml pnpm-workspace.yaml'
run_step web_typecheck web.log "Web typecheck" bash -c \
  'cd apps/web && corepack pnpm typecheck'
run_step web_tests web.log "Web tests" bash -c 'cd apps/web && corepack pnpm test'
run_step web_build web.log "Web production build" bash -c 'cd apps/web && corepack pnpm build'

compose_file="apps/llm-gateway/deploy/full/docker-compose.yml"
compose_environment=(
  env
  "OMNIA_RELEASE_SHA=${EXPECTED_RELEASE_SHA}"
  "API_IMAGE=omnia-api:${EXPECTED_RELEASE_SHA}"
  "WEB_IMAGE=omnia-web:${EXPECTED_RELEASE_SHA}"
  "JWT_SECRET=${JWT_SECRET}"
  "SECRETS_ENCRYPTION_KEY=release-gate-non-secret-encryption-key"
  "ORCHESTRATOR_INTERNAL_TOKEN=release-gate-non-secret-orchestrator-token"
  "NEXTAUTH_SECRET=release-gate-non-secret-nextauth-secret"
)
run_step compose_config compose.log "Production Compose config validation" \
  "${compose_environment[@]}" \
  docker compose -f "${compose_file}" config --quiet
run_step compose_build compose.log "Revision-tagged production image build" \
  "${compose_environment[@]}" \
  docker compose -f "${compose_file}" build api web
run_step compose_images images.log "Revision-tagged image inventory" \
  "${compose_environment[@]}" \
  docker compose -f "${compose_file}" images api web

local_container_smoke() (
  set -euo pipefail
  local smoke_prefix="omnia-rg-${head_sha:0:12}-$$"
  local smoke_network="${smoke_prefix}-network"
  local postgres_container="${smoke_prefix}-postgres"
  local api_container="${smoke_prefix}-api"
  local web_container="${smoke_prefix}-web"
  local api_image="omnia-api:${EXPECTED_RELEASE_SHA}"
  local web_image="omnia-web:${EXPECTED_RELEASE_SHA}"
  local smoke_database_url="postgresql+asyncpg://omnia:release-gate@postgres:5432/omnia_release_gate"
  local ready

  cleanup_smoke() {
    docker rm -f \
      "${api_container}" \
      "${web_container}" \
      "${postgres_container}" >/dev/null 2>&1 || true
    docker network rm "${smoke_network}" >/dev/null 2>&1 || true
  }
  trap cleanup_smoke EXIT

  docker network create "${smoke_network}" >/dev/null
  docker run -d \
    --name "${postgres_container}" \
    --network "${smoke_network}" \
    --network-alias postgres \
    -e POSTGRES_USER=omnia \
    -e POSTGRES_PASSWORD=release-gate \
    -e POSTGRES_DB=omnia_release_gate \
    --health-cmd 'pg_isready -U omnia -d omnia_release_gate' \
    --health-interval 1s \
    --health-timeout 3s \
    --health-retries 30 \
    postgres:16-alpine >/dev/null

  ready=false
  for _attempt in $(seq 1 60); do
    if [[ "$(docker inspect --format '{{.State.Health.Status}}' "${postgres_container}")" == healthy ]]; then
      ready=true
      break
    fi
    sleep 1
  done
  [[ "${ready}" == true ]] || {
    docker logs "${postgres_container}"
    return 1
  }

  docker run --rm \
    --network "${smoke_network}" \
    -e "DATABASE_URL=${smoke_database_url}" \
    -e JWT_SECRET=release-gate-container-jwt-secret-at-least-32-bytes \
    -e "OMNIA_RELEASE_SHA=${EXPECTED_RELEASE_SHA}" \
    "${api_image}" \
    /app/.venv/bin/alembic upgrade head

  docker run -d \
    --name "${api_container}" \
    --network "${smoke_network}" \
    -e "DATABASE_URL=${smoke_database_url}" \
    -e JWT_SECRET=release-gate-container-jwt-secret-at-least-32-bytes \
    -e "OMNIA_RELEASE_SHA=${EXPECTED_RELEASE_SHA}" \
    "${api_image}" \
    /app/.venv/bin/uvicorn omnia_api.main:app --host 0.0.0.0 --port 8000 \
    >/dev/null

  ready=false
  for _attempt in $(seq 1 60); do
    if docker exec "${api_container}" /app/.venv/bin/python -c \
      'import json,sys,urllib.request; payload=json.load(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2)); raise SystemExit(0 if payload == {"status": "ok", "release_sha": sys.argv[1]} else 1)' \
      "${EXPECTED_RELEASE_SHA}"; then
      ready=true
      break
    fi
    sleep 1
  done
  [[ "${ready}" == true ]] || {
    docker logs "${api_container}"
    return 1
  }

  docker run -d \
    --name "${web_container}" \
    --network "${smoke_network}" \
    -e "OMNIA_RELEASE_SHA=${EXPECTED_RELEASE_SHA}" \
    -e NEXTAUTH_SECRET=release-gate-container-nextauth-secret \
    "${web_image}" >/dev/null

  ready=false
  for _attempt in $(seq 1 60); do
    if docker exec "${web_container}" node -e \
      'fetch("http://127.0.0.1:3000/web-health").then(async response => { const payload = await response.json(); process.exit(response.ok && payload.status === "ok" && payload.service === "web" && payload.release_sha === process.argv[1] ? 0 : 1); }).catch(() => process.exit(1));' \
      "${EXPECTED_RELEASE_SHA}"; then
      ready=true
      break
    fi
    sleep 1
  done
  [[ "${ready}" == true ]] || {
    docker logs "${web_container}"
    return 1
  }
)

run_step container_smoke images.log "Isolated migration and container health smoke" \
  local_container_smoke

gate_status="passed"
finish_manifest
trap - EXIT
echo "release gate passed; evidence: .release-evidence/${head_sha}-${timestamp}"
