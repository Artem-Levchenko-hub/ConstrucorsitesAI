#!/usr/bin/env bash
# Usage: bash scripts/build-public-max-core.sh sha256:<kit-id> <output-tag> sha256:<node-runtime-base-id>
set -euo pipefail
cd "$(dirname "$0")/.."
# CA-only usage: --ca-only <already-local-repository@sha256:manifest> <output-tag> <expected-local-image-id>
ca_file="../api/src/yleum_api/certs/russian_trusted_root_ca.pem"
ca_base64="$(python3 - "$ca_file" <<'CA'
import base64, hashlib, pathlib, sys
path = pathlib.Path(sys.argv[1])
if path.is_symlink() or not path.is_file():
    raise SystemExit("official MAX CA file required")
raw = path.read_bytes()
if hashlib.sha256(raw).hexdigest() != "aa800ef345422d6158c6fafe1c06c429dbda21c3df4bb1ccb45a920ec1111399":
    raise SystemExit("official MAX CA hash mismatch")
print(base64.b64encode(raw).decode("ascii"))
CA
)"
verified_alias() {
  local exact_id="$1" alias="$2"
  if docker image inspect "$alias" --format '{{.Id}}' >/dev/null 2>&1; then
    test "$(docker image inspect "$alias" --format '{{.Id}}')" = "$exact_id"
  else
    docker image tag "$exact_id" "$alias"
  fi
  test "$(docker image inspect "$alias" --format '{{.Id}}')" = "$exact_id"
}
if [[ "${1:-}" == --ca-only ]]; then
  [[ "$#" -eq 4 ]]
  parent_ref="$2"; output_tag="$3"; expected_parent_id="$4"
  [[ "$expected_parent_id" =~ ^sha256:[0-9a-f]{64}$ ]]
  [[ "$parent_ref" =~ ^[a-zA-Z0-9][a-zA-Z0-9./:_-]*@sha256:[0-9a-f]{64}$ ]]
  [[ "$output_tag" =~ ^omnia-max-public-core:[a-zA-Z0-9_.-]+$ ]]
  # Config is compared privately; only the resulting immutable ID is emitted.
  ca_metadata_format='{"id":{{json .Id}},"os":{{json .Os}},"arch":{{json .Architecture}},"layers":{{json .RootFS.Layers}},"digests":{{json .RepoDigests}},"config":{{json .Config}}}'
  parent_metadata="$(docker image inspect "$parent_ref" --format "$ca_metadata_format")"
  parent_id="$(python3 - "$parent_ref" "$parent_metadata" "$expected_parent_id" <<'PARENT'
import json, re, sys
reference, raw, expected_id = sys.argv[1:]; item = json.loads(raw); config = item.get("config") or {}
if (not re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("id", ""))
    or item.get("id") != expected_id
    or reference not in (item.get("digests") or []) or item.get("os") != "linux"
    or not item.get("arch") or not item.get("layers")
    or config.get("User") != "node" or config.get("WorkingDir") != "/app"
    or config.get("Cmd") != ["node", "server.js"]
    or any((config.get("Labels") or {}).get("omnia.max-core." + key) != "1"
           for key in ("protocol", "preview-protocol", "db-role-protocol"))
    or any(value.startswith("NODE_EXTRA_CA_CERTS=") for value in (config.get("Env") or []))):
    raise SystemExit("CA parent provenance or startup rejected")
print(item["id"])
PARENT
)"
  parent_tag="omnia-max-core-ca-base:${parent_id#sha256:}"
  verified_alias "$parent_id" "$parent_tag"
  ca_context="$(mktemp -d /tmp/omnia-max-core-ca.XXXXXXXX)"
  trap 'rm -rf -- "$ca_context"' EXIT
  printf '%s' "$ca_base64" | base64 --decode > "$ca_context/max-root-ca.pem"
  chmod 0444 "$ca_context/max-root-ca.pem"
  cat > "$ca_context/Dockerfile" <<'DOCKERFILE'
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY --chown=0:0 max-root-ca.pem /usr/local/share/yleum/max-root-ca.pem
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/yleum/max-root-ca.pem
DOCKERFILE
  docker build --pull=false --network=none --build-arg "BASE_IMAGE=$parent_tag" \
    -t "$output_tag" -f "$ca_context/Dockerfile" "$ca_context"
  test "$(docker image inspect "$parent_tag" --format '{{.Id}}')" = "$parent_id"
  child_metadata="$(docker image inspect "$output_tag" --format "$ca_metadata_format")"
  python3 - "$parent_metadata" "$child_metadata" <<'CHILD'
import json, re, sys
parent, child = map(json.loads, sys.argv[1:])
a, b = dict(parent["config"]), dict(child["config"])
expected = list(a.pop("Env", []) or []) + ["NODE_EXTRA_CA_CERTS=/usr/local/share/yleum/max-root-ca.pem"]
actual = b.pop("Env", []) or []
# Docker's Config.Image is build-cache bookkeeping, not a runtime setting.
a.pop("Image", None); b.pop("Image", None)
if (not re.fullmatch(r"sha256:[0-9a-f]{64}", child.get("id", ""))
    or parent["os"] != child["os"] or parent["arch"] != child["arch"]
    or child["layers"][:-1] != parent["layers"]
    or len(child["layers"]) != len(parent["layers"]) + 1 or a != b or actual != expected):
    raise SystemExit("CA candidate changed inherited runtime or lineage")
CHILD
  docker image save --output "$ca_context/candidate.tar" "$output_tag"
  python3 - "$ca_context/candidate.tar" "$child_metadata" <<'LAYER'
import gzip, hashlib, io, json, sys, tarfile
path, raw = sys.argv[1:]; child = json.loads(raw)
try:
    with tarfile.open(path) as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
        if len(manifest) != 1 or len(manifest[0]["Layers"]) != len(child["layers"]):
            raise ValueError()
        cfg = json.load(archive.extractfile(manifest[0]["Config"]))
        if cfg["rootfs"]["diff_ids"] != child["layers"]:
            raise ValueError()
        blob = archive.extractfile(manifest[0]["Layers"][-1]).read(1024 * 1024 + 1)
        if blob.startswith(b"\x1f\x8b"):
            with gzip.GzipFile(fileobj=io.BytesIO(blob)) as compressed:
                blob = compressed.read(1024 * 1024 + 1)
        if len(blob) > 1024 * 1024 or "sha256:" + hashlib.sha256(blob).hexdigest() != child["layers"][-1]:
            raise ValueError()
    allowed_dirs = {"usr", "usr/local", "usr/local/share", "usr/local/share/yleum"}
    found = False
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as layer:
        for entry in layer:
            name = entry.name.rstrip("/")
            if entry.isdir() and name in allowed_dirs and entry.uid == entry.gid == 0 and entry.mode == 0o755:
                continue
            if (found or name != "usr/local/share/yleum/max-root-ca.pem" or not entry.isfile()
                or entry.uid != 0 or entry.gid != 0 or entry.mode != 0o444 or entry.size > 32768):
                raise ValueError()
            cert = layer.extractfile(entry).read(32769)
            if hashlib.sha256(cert).hexdigest() != "aa800ef345422d6158c6fafe1c06c429dbda21c3df4bb1ccb45a920ec1111399":
                raise ValueError()
            found = True
    if not found:
        raise ValueError()
except Exception:
    raise SystemExit("CA candidate layer is not certificate-only") from None
LAYER
  test "$(docker image inspect "$output_tag" --format '{{.Id}}')" = "$(python3 - "$child_metadata" <<'ID'
import json, sys
print(json.loads(sys.argv[1])["id"])
ID
)"
  docker image inspect "$output_tag" --format '{{.Id}}'
  exit 0
fi
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
verified_alias "$base_id" "$base_tag"
verified_alias "$runtime_id" "$runtime_tag"
docker build --pull=false --network=none --build-arg "BASE_IMAGE=$base_tag" \
  --build-arg "RUNTIME_BASE_IMAGE=$runtime_tag" --build-arg "MAX_ROOT_CA_BASE64=$ca_base64" \
  -t "$output_tag" -f scripts/public-max-core/Dockerfile .
test "$(docker image inspect "$base_tag" --format '{{.Id}}')" = "$base_id"
test "$(docker image inspect "$runtime_tag" --format '{{.Id}}')" = "$runtime_id"
for protocol in protocol preview-protocol db-role-protocol; do
  test "$(docker image inspect "$output_tag" --format "{{index .Config.Labels \"omnia.max-core.$protocol\"}}")" = 1
done
docker image inspect "$output_tag" --format '{{.Id}}'
