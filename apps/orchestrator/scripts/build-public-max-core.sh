#!/usr/bin/env bash
# Usage: bash scripts/build-public-max-core.sh sha256:<kit-id> <output-tag> sha256:<node-runtime-base-id>
set -euo pipefail
cd "$(dirname "$0")/.."
base_id="${1:?existing immutable kit image ID required}"
output_tag="${2:?dedicated output image tag required}"
runtime_id="${3:?immutable Node runtime base image ID required}"
[[ "$#" -eq 3 ]]
[[ "$base_id" =~ ^sha256:[0-9a-f]{64}$ ]]
[[ "$runtime_id" =~ ^sha256:[0-9a-f]{64}$ ]]
[[ "$output_tag" =~ ^omnia-max-public-core:[a-zA-Z0-9_.-]+$ ]]
# Inspect only public provenance fields, never image environment/credentials.
metadata_format='{"id":{{json .Id}},"os":{{json .Os}},"arch":{{json .Architecture}},"layers":{{json .RootFS.Layers}}}'
kit_metadata="$(docker image inspect "$base_id" --format "$metadata_format")"
runtime_metadata="$(docker image inspect "$runtime_id" --format "$metadata_format")"
python3 - "$base_id" "$runtime_id" "$kit_metadata" "$runtime_metadata" <<'PY'
import json, sys
kit_id, runtime_id, kit_raw, runtime_raw = sys.argv[1:]
kit, runtime = json.loads(kit_raw), json.loads(runtime_raw)
if (kit.get("id") != kit_id or runtime.get("id") != runtime_id
    or not kit.get("os") or not kit.get("arch")
    or kit.get("os") != runtime.get("os") or kit.get("arch") != runtime.get("arch")
    or not isinstance(runtime.get("layers"), list) or not runtime["layers"]
    or not isinstance(kit.get("layers"), list)
    or kit["layers"][:len(runtime["layers"])] != runtime["layers"]):
    raise SystemExit("Node runtime base is not the verified kit ancestor")
PY
# A Debian ancestor can satisfy the layer prefix while containing no Node at
# all. Execute only the version command, isolated from networks and host files.
node_version() {
  docker run --rm --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --memory 128m --cpus 0.25 \
    --pids-limit 32 --user node --entrypoint node "$1" --version
}
kit_node_version="$(node_version "$base_id")"
runtime_node_version="$(node_version "$runtime_id")"
[[ "$kit_node_version" =~ ^v22\.[0-9]+\.[0-9]+$ ]]
test "$runtime_node_version" = "$kit_node_version"
# BuildKit treats a bare sha256:ID in FROM as a repository name. Pin dedicated
# local aliases and reject drift rather than retagging an unexpected image.
base_tag="omnia-max-core-build-base:${base_id#sha256:}"
runtime_tag="omnia-max-core-runtime-base:${runtime_id#sha256:}"
verified_alias() {
  local exact_id="$1" alias="$2"
  if docker image inspect "$alias" --format '{{.Id}}' >/dev/null 2>&1; then
    test "$(docker image inspect "$alias" --format '{{.Id}}')" = "$exact_id"
  else
    docker image tag "$exact_id" "$alias"
  fi
  test "$(docker image inspect "$alias" --format '{{.Id}}')" = "$exact_id"
}
verified_alias "$base_id" "$base_tag"
verified_alias "$runtime_id" "$runtime_tag"
docker build --pull=false --network=none --build-arg "BASE_IMAGE=$base_tag" \
  --build-arg "RUNTIME_BASE_IMAGE=$runtime_tag" \
  -t "$output_tag" -f scripts/public-max-core/Dockerfile .
test "$(docker image inspect "$base_tag" --format '{{.Id}}')" = "$base_id"
test "$(docker image inspect "$runtime_tag" --format '{{.Id}}')" = "$runtime_id"
for protocol in protocol preview-protocol db-role-protocol; do
  test "$(docker image inspect "$output_tag" --format "{{index .Config.Labels \"omnia.max-core.$protocol\"}}")" = 1
done
docker image inspect "$output_tag" --format '{{.Id}}'
