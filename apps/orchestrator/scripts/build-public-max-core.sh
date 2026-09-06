#!/usr/bin/env bash
# Usage: bash scripts/build-public-max-core.sh sha256:<existing-kit-id> <output-tag>
set -euo pipefail
cd "$(dirname "$0")/.."
base_id="${1:?existing immutable kit image ID required}"
output_tag="${2:?dedicated output image tag required}"
[[ "$base_id" =~ ^sha256:[0-9a-f]{64}$ ]]
[[ "$output_tag" =~ ^omnia-max-public-core:[a-zA-Z0-9_.-]+$ ]]
test "$(docker image inspect "$base_id" --format '{{.Id}}')" = "$base_id"
# BuildKit treats a bare sha256:ID in FROM as a repository name. Pin a dedicated
# local build alias, verify it before/after, and never mutate the agent kit tag.
base_tag="omnia-max-core-build-base:${base_id#sha256:}"
if docker image inspect "$base_tag" >/dev/null 2>&1; then
  test "$(docker image inspect "$base_tag" --format '{{.Id}}')" = "$base_id"
else
  docker image tag "$base_id" "$base_tag"
fi
docker build --pull=false --network=none --build-arg "BASE_IMAGE=$base_tag" \
  -t "$output_tag" -f scripts/public-max-core/Dockerfile .
test "$(docker image inspect "$base_tag" --format '{{.Id}}')" = "$base_id"
test "$(docker image inspect "$output_tag" --format '{{index .Config.Labels "omnia.max-core.protocol"}}')" = 1
docker image inspect "$output_tag" --format '{{.Id}}'
