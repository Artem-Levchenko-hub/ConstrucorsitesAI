#!/usr/bin/env bash
set -euo pipefail

active_generations="$({
  docker exec omnia-prod-postgres sh -lc \
    'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
    "select status,count(*) from generation_runs
     where finished_at is null
     group by status
     order by status;"'
})"

if [[ -n "${active_generations}" ]]; then
  printf '%s\n' "active generations block release:" >&2
  printf '%s\n' "${active_generations}" >&2
  exit 1
fi

printf '%s\n' "no active generations"
