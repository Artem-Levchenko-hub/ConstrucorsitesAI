#!/usr/bin/env bash
# Refuses a release while any generation is still running. Reads the platform DB
# where it lives: inside yleum-prod-postgres, or (after the Phase 2 move) on the
# host PostgreSQL of core over PLATFORM_DSN — the same switch as infra/backup.
set -euo pipefail

if [ "${1:-}" = --compatible-target ]; then
  [[ "${2:-}" =~ ^[0-9a-f]{40}$ ]] || exit 2
  # The same owned drain classification used by admission and the worker.
  # An unavailable/incompatible target never falls back to a permissive query.
  exec docker exec yleum-prod-api python -m yleum_api.services.generation_deployment_drain check "$2"
fi
strict_readers=0
if [ "${1:-}" = --require-no-recovery-state ]; then
  strict_readers=1
elif [ "$#" != 0 ]; then
  exit 2
fi

FULLSTACK_ENV="${FULLSTACK_ENV:-/opt/omnia/apps/llm-gateway/deploy/full/.env}"
PLATFORM_CREDS="${PLATFORM_CREDS:-/etc/max-studio/platform-postgres.env}"
PLATFORM_CTR="${PLATFORM_CTR:-yleum-prod-postgres}"
PLATFORM_DSN="${PLATFORM_DSN:-}"
query='select status,count(*) from generation_runs
     where finished_at is null
     group by status
     order by status;'
if [ "$strict_readers" = 1 ]; then
  query="select status,count(*) from generation_runs
       where finished_at is null or agent_state ? 'recovery_v1'
       group by status order by status;"
fi

if [ -z "$PLATFORM_DSN" ] && { [ -z "$PLATFORM_CTR" ] || { [ -r "$FULLSTACK_ENV" ] && grep -q '^PLATFORM_DATABASE_URL=' "$FULLSTACK_ENV"; }; }; then
  if [ -r "$PLATFORM_CREDS" ]; then
    PLATFORM_DSN="$(sed -n 's/^PLATFORM_PG_DSN=//p' "$PLATFORM_CREDS" | tail -1)"
  fi
  if [ -z "$PLATFORM_DSN" ] && [ -r "$FULLSTACK_ENV" ]; then
    PLATFORM_DSN="$(sed -n 's/^PLATFORM_DATABASE_URL=//p' "$FULLSTACK_ENV" | tail -1 | sed 's#^postgresql+asyncpg://#postgresql://#')"
  fi
fi

if [ -n "$PLATFORM_DSN" ]; then
  active_generations="$(psql -X "$PLATFORM_DSN" -Atc "$query")"
else
  active_generations="$({
    docker exec "$PLATFORM_CTR" sh -lc \
      'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "$1"' _ "$query"
  })"
fi

if [[ -n "${active_generations}" ]]; then
  printf '%s\n' "active generations block release:" >&2
  printf '%s\n' "${active_generations}" >&2
  exit 1
fi

printf '%s\n' "no active generations"
