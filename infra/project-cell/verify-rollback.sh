#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "phase2 rollback verification failed: $1" >&2
  exit 1
}

[[ "$#" -ge 2 && "$#" -le 3 ]] || fail "usage: verify-rollback.sh BEFORE_BUNDLE AFTER_BUNDLE [PROOF_OUTPUT]"
before="$1"
after="$2"
proof_output="${3:-}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ctl="${script_dir}/phase2ctl.py"
for bundle in "${before}" "${after}"; do
  [[ "${bundle}" == /* ]] || fail "bundle paths must be absolute"
  python3 "${ctl}" verify-bundle --bundle "${bundle}" >/dev/null
done

before_host="$(python3 - "${before}/manifest.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1],encoding="utf-8"))["hostname"])
PY
)"
after_host="$(python3 - "${after}/manifest.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1],encoding="utf-8"))["hostname"])
PY
)"
[[ "${before_host}" == "${after_host}" ]] || fail "snapshots belong to different hosts"

contract_files=(
  package-versions.txt
  runtime-versions.txt
  systemd-state.json
  sysctls.txt
  modules.txt
  firewall-v4.rules
  firewall-v6.rules
  nftables.rules
  routes.json
  restore-contract.json
  dns.json
  cni.json
  docker-networks.json
  docker-state.json
  listeners.txt
  production-health.json
  filesystem-state.json
)
mismatches=()
for relative in "${contract_files[@]}"; do
  if ! cmp -s "${before}/evidence/${relative}" "${after}/evidence/${relative}"; then
    mismatches+=("${relative}")
  fi
done
if ((${#mismatches[@]})); then
  fail "byte comparison differs: ${mismatches[*]}"
fi
python3 - "${before}/evidence/production-health.json" "${after}/evidence/production-health.json" <<'PY'
import json,sys
before=json.load(open(sys.argv[1],encoding="utf-8")); after=json.load(open(sys.argv[2],encoding="utf-8"))
if before.get("status")!="passed" or after.get("status")!="passed" or before.get("release_sha")!=after.get("release_sha"):
    raise SystemExit("production health/release did not return to the exact baseline")
PY
if [[ -n "${proof_output}" ]]; then
  [[ "${proof_output}" == /* && ! -e "${proof_output}" && ! -L "${proof_output}" ]] \
    || fail "proof output must be a new absolute path"
  temporary="$(mktemp "${proof_output}.tmp.XXXXXX")"
  python3 - "${temporary}" "$(tr -d '\n' <"${before}/bundle-id.txt")" <<'PY'
import json,sys
open(sys.argv[1],"w",encoding="utf-8").write(json.dumps({"bundle_id":sys.argv[2],"status":"passed","byte_compare":"passed","production_health":"passed"},sort_keys=True,separators=(",",":"))+"\n")
PY
  chmod 400 "${temporary}"
  mv "${temporary}" "${proof_output}"
fi
echo "phase2 rollback byte comparison passed"
