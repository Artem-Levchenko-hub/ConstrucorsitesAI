#!/usr/bin/env bash
set -euo pipefail

project_cell_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ctl="${project_cell_dir}/phase2ctl.py"
deadman="${project_cell_dir}/deadman-control.sh"
installer="${project_cell_dir}/install-isolated-k3s.sh"
smoke="${project_cell_dir}/smoke-trusted-hello.sh"
sealer="${project_cell_dir}/seal-rollback-bundle.sh"
rollback="${project_cell_dir}/rollback-phase2.sh"
manifest="${project_cell_dir}/manifests/trusted-hello.yaml"
test_root="$(mktemp -d)"
trap 'rm -rf "${test_root}"' EXIT

# Windows Git Bash can resolve python3 to the Microsoft Store launcher even
# when the real interpreter is available through py.exe.  Keep production
# scripts strict about python3; adapt only this local test harness.
if [[ "$(python3 -c 'print("phase2-python-ok")' 2>/dev/null || true)" != phase2-python-ok ]]; then
  command -v py.exe >/dev/null 2>&1 || {
    echo "phase2 contract test failed: a real Python 3 interpreter is required" >&2
    exit 1
  }
  mkdir -p "${test_root}/portable-bin"
  printf '#!/usr/bin/env bash\nexec py.exe -3 "$@"\n' >"${test_root}/portable-bin/python3"
  chmod +x "${test_root}/portable-bin/python3"
  export PATH="${test_root}/portable-bin:${PATH}"
fi
if [[ "$(uname -s)" == MINGW* ]]; then
  export PHASE2_TEST_ALLOW_WINDOWS_ACL=1
  phase2_windows_acl=true
else
  phase2_windows_acl=false
fi
export PHASE2_TEST_MODE=1

fail() {
  echo "phase2 contract test failed: $1" >&2
  exit 1
}

expect_failure() {
  local label="$1"
  shift
  if "$@" >"${test_root}/${label}.out" 2>"${test_root}/${label}.err"; then
    fail "${label} unexpectedly passed"
  fi
}

file_mode() {
  if stat -f '%Lp' "$1" >/dev/null 2>&1; then
    stat -f '%Lp' "$1"
  else
    stat -c '%a' "$1"
  fi
}

make_bundle() {
  local bundle="$1"
  local evidence_now
  evidence_now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  mkdir -p "${bundle}/evidence" "${bundle}/restore"
  chmod 700 "${bundle}" "${bundle}/evidence" "${bundle}/restore"
  printf 'base-files 13.8\ncontainerd.io 2.2.1\n' >"${bundle}/evidence/package-versions.txt"
  printf 'docker=29.2.1\ncontainerd=2.2.1\nk3s=absent\nkata=absent\n' >"${bundle}/evidence/runtime-versions.txt"
  printf '{"docker.service":{"active":"active","enabled":"enabled"},"k3s.service":{"active":"inactive","enabled":"disabled"},"nginx.service":{"active":"active","enabled":"enabled"},"omnia-orchestrator.service":{"active":"active","enabled":"enabled"}}\n' >"${bundle}/evidence/systemd-state.json"
  printf 'kernel.pid_max = 4194304\nvm.swappiness = 60\n' >"${bundle}/evidence/sysctls.txt"
  printf 'kvm\nkvm_intel\n' >"${bundle}/evidence/modules.txt"
  printf '*filter\n:INPUT ACCEPT [0:0]\nCOMMIT\n' >"${bundle}/evidence/firewall-v4.rules"
  printf '*filter\n:INPUT ACCEPT [0:0]\nCOMMIT\n' >"${bundle}/evidence/firewall-v6.rules"
  printf 'absent:nft\n' >"${bundle}/evidence/nftables.rules"
  printf '[{"dst":"default","gateway":"170.168.72.1","dev":"eth0"},{"dst":"10.16.0.0/16","dev":"eth1"}]\n' >"${bundle}/evidence/routes.json"
  printf '{"format":1,"entries":[{"path":"/etc/cni","type":"absent"},{"path":"/etc/rancher/k3s/config.yaml","type":"absent"},{"path":"/opt/cni","type":"absent"},{"path":"/var/lib/cni","type":"absent"}]}\n' >"${bundle}/evidence/restore-contract.json"
  printf '{"kind":"file","sha256":"3b2d1f","target":null}\n' >"${bundle}/evidence/dns.json"
  printf '{"paths":[]}\n' >"${bundle}/evidence/cni.json"
  printf '[{"Name":"bridge","IPAM":{"Config":[{"Subnet":"172.17.0.0/16"}]}}]\n' >"${bundle}/evidence/docker-networks.json"
  printf '{"containers":40,"volumes":31,"production_compose":"full"}\n' >"${bundle}/evidence/docker-state.json"
  printf '127.0.0.1:8200 tcp\n127.0.0.1:8101 tcp\n0.0.0.0:80 tcp\n0.0.0.0:443 tcp\n' >"${bundle}/evidence/listeners.txt"
  printf '{"available_memory_bytes":8267812045,"free_disk_bytes":249108103168,"inode_used_percent":12,"swap_used_bytes":0}\n' >"${bundle}/evidence/capacity.json"
  printf '{"status":"passed","release_sha":"ebb7bcc3c33ea9c001a5ab25d75915edae2049e7","api":200,"gateway":200,"orchestrator":200,"web":200}\n' >"${bundle}/evidence/production-health.json"
  printf '{"status":"passed","verified_at":"%s","background_quiescence":"passed","active_generations":0,"active_builds":0,"active_deploys":0,"active_backups":0,"active_restores":0,"active_deletes":0,"active_promotions":0}\n' "${evidence_now}" >"${bundle}/evidence/active-operations.json"
  printf '{"status":"passed","verified_at":"%s","offhost":true,"checksum_verified":true,"restore_test":"passed"}\n' "${evidence_now}" >"${bundle}/evidence/backup.json"
  printf '{"paths":[{"path":"/etc/rancher/k3s/config.yaml","state":"absent"},{"path":"/var/lib/rancher/k3s","state":"absent"},{"path":"/etc/systemd/system/k3s.service","state":"absent"}]}\n' >"${bundle}/evidence/filesystem-state.json"
  cp "${bundle}/evidence/firewall-v4.rules" "${bundle}/restore/firewall-v4.rules"
  cp "${bundle}/evidence/firewall-v6.rules" "${bundle}/restore/firewall-v6.rules"
  cp "${bundle}/evidence/nftables.rules" "${bundle}/restore/nftables.rules"
  : >"${bundle}/restore/routes.bin"
  tar -cf "${bundle}/restore/host-files.tar" --files-from /dev/null
  chmod 600 "${bundle}/evidence/"*
  python3 "${ctl}" manifest --bundle "${bundle}" --hostname prod-host >/dev/null
}

remanifest() {
  local bundle="$1"
  chmod 700 "${bundle}" "${bundle}/evidence" "${bundle}/restore"
  chmod 600 "${bundle}/evidence/"* "${bundle}/restore/"*
  rm "${bundle}/manifest.json" "${bundle}/bundle-id.txt"
  python3 "${ctl}" manifest --bundle "${bundle}" --hostname prod-host >/dev/null
}

assert_zero_dependency_hard_gate() {
  local label="$1"
  local entrypoint="$2"
  shift 2
  local sentinel_bin="${test_root}/${label}-sentinel-bin"
  local sentinel_py="${test_root}/${label}-sentinel-python"
  local sentinel_hit="${test_root}/${label}-sentinel.hit"
  local sentinel_state="${test_root}/${label}-state"
  local bash_bin
  bash_bin="$(command -v bash)"
  mkdir -p "${sentinel_bin}" "${sentinel_py}"
  for dependency in python3 dirname git curl sha256sum ip docker flock systemctl uname \
    kubectl mktemp openssl tar stat date rm mkdir cp install; do
    printf '#!/bin/sh\nprintf "%%s\\n" "$0" >>"%s"\nexit 97\n' \
      "${sentinel_hit}" >"${sentinel_bin}/${dependency}"
    chmod +x "${sentinel_bin}/${dependency}"
  done
  printf 'from pathlib import Path\nPath(r"%s").write_text("PYTHONPATH executed")\n' \
    "${sentinel_hit}" >"${sentinel_py}/sitecustomize.py"
  expect_failure "${label}" env -i PATH="${sentinel_bin}" PYTHONPATH="${sentinel_py}" \
    PHASE2_STATE_ROOT="${sentinel_state}" "${bash_bin}" "${entrypoint}" "$@"
  [[ ! -e "${sentinel_hit}" ]] || fail "${label} executed a dependency"
  [[ ! -e "${sentinel_state}" ]] || fail "${label} wrote host state"
}

make_signed_attestations() {
  local bundle="$1"
  local state="$2"
  local ciphertext="$3"
  local bundle_id cipher_sha now
  mkdir -p "${state}"
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
    -out "${state}/verifier.key" >/dev/null 2>&1
  openssl pkey -in "${state}/verifier.key" -pubout \
    -out "${state}/verifier.pub" >/dev/null 2>&1
  openssl req -x509 -new -key "${state}/verifier.key" -subj //CN=phase2-recovery \
    -days 1 -out "${state}/recovery-cert.pem" >/dev/null 2>&1
  python3 - "${state}/trust.json" "${state}/verifier.pub" "${state}/recovery-cert.pem" <<'PY'
import hashlib,json,sys
p={"format":1,"production_hostname":"prod-host","verifier_id":"recovery-host-01","verifier_public_key_sha256":hashlib.sha256(open(sys.argv[2],"rb").read()).hexdigest(),"recovery_certificate_sha256":hashlib.sha256(open(sys.argv[3],"rb").read()).hexdigest(),"allowed_offhost_locations":["s3://omnia-recovery/project-cell/"]}
open(sys.argv[1],"w",encoding="utf-8").write(json.dumps(p,sort_keys=True,separators=(",",":"))+"\n")
PY
  printf 'encrypted rollback bytes\n' >"${ciphertext}"
  bundle_id="$(tr -d '\n' <"${bundle}/bundle-id.txt")"
  cipher_sha="$(python3 - "${ciphertext}" <<'PY'
import hashlib
import sys
print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())
PY
)"
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  python3 - "${state}/offhost.json" "${bundle_id}" "${cipher_sha}" "${now}" <<'PY'
import json
import sys
path, bundle_id, cipher_sha, now = sys.argv[1:]
payload = {
    "kind": "offhost",
    "bundle_id": bundle_id,
    "ciphertext_sha256": cipher_sha,
    "production_hostname": "prod-host",
    "verifier_id": "recovery-host-01",
    "location": "s3://omnia-recovery/project-cell/phase2.cms",
    "verified_at": now,
    "checksum": "passed",
    "decrypt": "passed",
    "manifest": "passed",
    "restore_test": "passed",
}
open(path, "wb").write((json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode())
PY
  openssl dgst -sha256 -sign "${state}/verifier.key" \
    -out "${state}/offhost.sig" "${state}/offhost.json"
  python3 - "${state}/rescue.json" "${bundle_id}" "${now}" <<'PY'
import json
import sys
path, bundle_id, now = sys.argv[1:]
payload = {
    "kind": "provider_rescue",
    "bundle_id": bundle_id,
    "production_hostname": "prod-host",
    "verifier_id": "recovery-host-01",
    "verified_at": now,
    "console_login": "passed",
    "rescue_boot": "passed",
}
open(path, "wb").write((json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode())
PY
  openssl dgst -sha256 -sign "${state}/verifier.key" \
    -out "${state}/rescue.sig" "${state}/rescue.json"
  chmod 400 "${state}/verifier.pub" "${state}/trust.json" "${state}/recovery-cert.pem" \
    "${state}/offhost.json" "${state}/offhost.sig" "${state}/rescue.json" \
    "${state}/rescue.sig" "${ciphertext}"
  python3 "${ctl}" verify-attestation \
    --kind offhost --bundle "${bundle}" --attestation "${state}/offhost.json" \
    --signature "${state}/offhost.sig" --public-key "${state}/verifier.pub" \
    --trust-policy "${state}/trust.json" \
    --ciphertext "${ciphertext}" --output "${state}/offhost.verified.json" >/dev/null
  python3 "${ctl}" verify-attestation \
    --kind provider_rescue --bundle "${bundle}" --attestation "${state}/rescue.json" \
    --signature "${state}/rescue.sig" --public-key "${state}/verifier.pub" \
    --trust-policy "${state}/trust.json" \
    --output "${state}/rescue.verified.json" >/dev/null
}

test_evidence() {
  local bundle="${test_root}/bundle"
  local state="${test_root}/attestations"
  local ciphertext="${test_root}/bundle.tar.cms"
  make_bundle "${bundle}"
  python3 "${ctl}" verify-bundle --bundle "${bundle}" >/dev/null
  if [[ "${phase2_windows_acl}" == false ]]; then
    [[ "$(file_mode "${bundle}")" == 700 ]] || fail "bundle is not mode 700"
    [[ "$(file_mode "${bundle}/manifest.json")" == 400 ]] || fail "manifest is writable"
    [[ "$(file_mode "${bundle}/bundle-id.txt")" == 400 ]] || fail "bundle id is writable"
    [[ "$(file_mode "${bundle}/evidence/routes.json")" == 400 ]] || fail "evidence is writable"
  fi

  cp -a "${bundle}" "${test_root}/tampered"
  chmod 600 "${test_root}/tampered/evidence/routes.json"
  printf 'tamper\n' >>"${test_root}/tampered/evidence/routes.json"
  chmod 400 "${test_root}/tampered/evidence/routes.json"
  expect_failure tampered python3 "${ctl}" verify-bundle --bundle "${test_root}/tampered"

  if [[ "${phase2_windows_acl}" == false ]]; then
    cp -a "${bundle}" "${test_root}/permissive"
    chmod 644 "${test_root}/permissive/manifest.json"
    expect_failure permissive python3 "${ctl}" verify-bundle --bundle "${test_root}/permissive"
  fi

  cp -a "${bundle}" "${test_root}/extra"
  chmod 700 "${test_root}/extra/evidence"
  printf 'not manifested\n' >"${test_root}/extra/evidence/extra.txt"
  chmod 400 "${test_root}/extra/evidence/extra.txt"
  expect_failure extra python3 "${ctl}" verify-bundle --bundle "${test_root}/extra"

  cp -a "${bundle}" "${test_root}/missing"
  chmod 700 "${test_root}/missing/evidence"
  rm "${test_root}/missing/evidence/backup.json"
  expect_failure missing python3 "${ctl}" verify-bundle --bundle "${test_root}/missing"

  make_signed_attestations "${bundle}" "${state}" "${ciphertext}"
  cp "${state}/offhost.json" "${state}/consumed-failed-offhost.json"
  chmod 600 "${state}/consumed-failed-offhost.json"
  python3 - "${state}/consumed-failed-offhost.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1],encoding="utf-8")); p["restore_test"]="failed"
open(sys.argv[1],"wb").write((json.dumps(p,sort_keys=True,separators=(",",":"))+"\n").encode())
PY
  openssl dgst -sha256 -sign "${state}/verifier.key" \
    -out "${state}/consumed-failed-offhost.sig" "${state}/consumed-failed-offhost.json"
  cp "${state}/offhost.verified.json" "${state}/consumed-failed-offhost.verified.json"
  chmod 600 "${state}/consumed-failed-offhost.verified.json"
  python3 - "${state}/consumed-failed-offhost.verified.json" \
    "${state}/consumed-failed-offhost.json" "${state}/consumed-failed-offhost.sig" <<'PY'
import hashlib,json,sys
marker=json.load(open(sys.argv[1],encoding="utf-8"))
marker["sources"]["attestation"],marker["sources"]["signature"]=sys.argv[2:]
marker["attestation_sha256"]=hashlib.sha256(open(sys.argv[2],"rb").read()).hexdigest()
marker["signature_sha256"]=hashlib.sha256(open(sys.argv[3],"rb").read()).hexdigest()
open(sys.argv[1],"w",encoding="utf-8").write(json.dumps(marker,sort_keys=True,separators=(",",":"))+"\n")
PY
  chmod 400 "${state}/consumed-failed-offhost.json" "${state}/consumed-failed-offhost.sig" \
    "${state}/consumed-failed-offhost.verified.json"
  expect_failure consumed-failed-offhost python3 "${ctl}" validate-marker \
    --kind offhost --bundle "${bundle}" \
    --marker "${state}/consumed-failed-offhost.verified.json" \
    --expected-trust-policy "${state}/trust.json"
  python3 - "${state}/offhost.json" "${state}/noncanonical-offhost.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1],encoding="utf-8"))
open(sys.argv[2],"w",encoding="utf-8").write(json.dumps(p,indent=2)+"\n")
PY
  openssl dgst -sha256 -sign "${state}/verifier.key" \
    -out "${state}/noncanonical-offhost.sig" "${state}/noncanonical-offhost.json"
  chmod 400 "${state}/noncanonical-offhost.json" "${state}/noncanonical-offhost.sig"
  expect_failure noncanonical-offhost python3 "${ctl}" verify-attestation \
    --kind offhost --bundle "${bundle}" --attestation "${state}/noncanonical-offhost.json" \
    --signature "${state}/noncanonical-offhost.sig" --public-key "${state}/verifier.pub" \
    --trust-policy "${state}/trust.json" --ciphertext "${ciphertext}" \
    --output "${state}/noncanonical-offhost.verified.json"
  mkdir -p "${test_root}/sealed-valid" "${test_root}/sealed-private"
  chmod 700 "${test_root}/sealed-valid" "${test_root}/sealed-private"
  bash "${sealer}" "${bundle}" "${state}/recovery-cert.pem" \
    "${state}/trust.json" "${test_root}/sealed-valid" >/dev/null
  cp "${state}/recovery-cert.pem" "${state}/cert-with-private.pem"
  chmod 600 "${state}/cert-with-private.pem"
  cat "${state}/verifier.key" >>"${state}/cert-with-private.pem"
  expect_failure recipient-private-key bash "${sealer}" "${bundle}" \
    "${state}/cert-with-private.pem" "${state}/trust.json" "${test_root}/sealed-private"
  for private_kind in OPENSSH DSA; do
    mkdir -p "${test_root}/sealed-private-${private_kind}"
    chmod 700 "${test_root}/sealed-private-${private_kind}"
    cp "${state}/recovery-cert.pem" "${state}/cert-with-${private_kind}.pem"
    chmod 600 "${state}/cert-with-${private_kind}.pem"
    printf '\n-----BEGIN %s PRIVATE KEY-----\nforbidden\n-----END %s PRIVATE KEY-----\n' \
      "${private_kind}" "${private_kind}" >>"${state}/cert-with-${private_kind}.pem"
    expect_failure "recipient-${private_kind}-private-key" bash "${sealer}" "${bundle}" \
      "${state}/cert-with-${private_kind}.pem" "${state}/trust.json" "${test_root}/sealed-private-${private_kind}"
  done
  cp "${state}/offhost.json" "${state}/bad-offhost.json"
  chmod 600 "${state}/bad-offhost.json"
  python3 - "${state}/bad-offhost.json" <<'PY'
import json, sys
p=json.load(open(sys.argv[1], encoding="utf-8")); p["location"]="file:///tmp/fake"; open(sys.argv[1],"w",encoding="utf-8").write(json.dumps(p)+"\n")
PY
  openssl dgst -sha256 -sign "${state}/verifier.key" \
    -out "${state}/bad-offhost.sig" "${state}/bad-offhost.json"
  chmod 400 "${state}/bad-offhost.json" "${state}/bad-offhost.sig"
  expect_failure local-receipt python3 "${ctl}" verify-attestation \
    --kind offhost --bundle "${bundle}" --attestation "${state}/bad-offhost.json" \
    --signature "${state}/bad-offhost.sig" --public-key "${state}/verifier.pub" \
    --trust-policy "${state}/trust.json" \
    --ciphertext "${ciphertext}" --output "${state}/bad.verified.json"

  cp "${state}/offhost.json" "${state}/loopback-offhost.json"
  chmod 600 "${state}/loopback-offhost.json"
  python3 - "${state}/loopback-offhost.json" <<'PY'
import json, sys
p=json.load(open(sys.argv[1], encoding="utf-8")); p["location"]="https://127.0.0.1/phase2.cms"; open(sys.argv[1],"w",encoding="utf-8").write(json.dumps(p)+"\n")
PY
  openssl dgst -sha256 -sign "${state}/verifier.key" \
    -out "${state}/loopback-offhost.sig" "${state}/loopback-offhost.json"
  chmod 400 "${state}/loopback-offhost.json" "${state}/loopback-offhost.sig"
  expect_failure loopback-receipt python3 "${ctl}" verify-attestation \
    --kind offhost --bundle "${bundle}" --attestation "${state}/loopback-offhost.json" \
    --signature "${state}/loopback-offhost.sig" --public-key "${state}/verifier.pub" \
    --trust-policy "${state}/trust.json" \
    --ciphertext "${ciphertext}" --output "${state}/loopback.verified.json"

  python3 - "${state}/forged.verified.json" "${bundle}/bundle-id.txt" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" <<'PY'
import json, sys
bid=open(sys.argv[2],encoding="utf-8").read().strip()
p={"kind":"offhost","bundle_id":bid,"attestation_sha256":"a"*64,"signature_sha256":"b"*64,"verified_at":sys.argv[3]}
open(sys.argv[1],"w",encoding="utf-8").write(json.dumps(p)+"\n")
PY
  expect_failure forged-marker python3 "${ctl}" validate-marker \
    --kind offhost --bundle "${bundle}" --marker "${state}/forged.verified.json"

  chmod 600 "${state}/offhost.json"
  printf 'changed\n' >>"${state}/offhost.json"
  chmod 400 "${state}/offhost.json"
  expect_failure unsigned-change python3 "${ctl}" verify-attestation \
    --kind offhost --bundle "${bundle}" --attestation "${state}/offhost.json" \
    --signature "${state}/offhost.sig" --public-key "${state}/verifier.pub" \
    --trust-policy "${state}/trust.json" \
    --ciphertext "${ciphertext}" --output "${state}/changed.verified.json"

  cp -a "${bundle}" "${test_root}/missing-cni-contract"
  chmod 600 "${test_root}/missing-cni-contract/evidence/restore-contract.json"
  python3 - "${test_root}/missing-cni-contract/evidence/restore-contract.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1],encoding="utf-8")); p["entries"]=[e for e in p["entries"] if e["path"]!="/var/lib/cni"]
open(sys.argv[1],"wb").write((json.dumps(p,sort_keys=True,separators=(",",":"))+"\n").encode())
PY
  remanifest "${test_root}/missing-cni-contract"
  expect_failure missing-cni-contract python3 "${ctl}" verify-bundle --bundle "${test_root}/missing-cni-contract"

  for invalid_evidence in systemd-state.json sysctls.txt modules.txt; do
    cp -a "${bundle}" "${test_root}/invalid-${invalid_evidence}"
    chmod 600 "${test_root}/invalid-${invalid_evidence}/evidence/${invalid_evidence}"
    if [[ "${invalid_evidence}" == systemd-state.json ]]; then
      printf 'unknown\n' >"${test_root}/invalid-${invalid_evidence}/evidence/${invalid_evidence}"
    else
      printf 'capture-failed:%s\n' "${invalid_evidence}" \
        >"${test_root}/invalid-${invalid_evidence}/evidence/${invalid_evidence}"
    fi
    remanifest "${test_root}/invalid-${invalid_evidence}"
    expect_failure "invalid-${invalid_evidence}" python3 "${ctl}" verify-bundle \
      --bundle "${test_root}/invalid-${invalid_evidence}"
  done
}

test_install() {
  local gate="${test_root}/gate.json"
  local config="${test_root}/config.yaml"
  local sentinel_bin="${test_root}/installer-sentinel-bin"
  local sentinel_hit="${test_root}/installer-sentinel.hit"
  local sentinel_py="${test_root}/installer-sentinel-python"
  local bash_bin
  bash_bin="$(command -v bash)"
  mkdir -p "${sentinel_bin}" "${sentinel_py}"
  for dependency in python3 dirname git curl sha256sum ip docker flock systemctl uname; do
    printf '#!/bin/sh\nprintf "%%s\\n" "$0" >>"%s"\nexit 97\n' \
      "${sentinel_hit}" >"${sentinel_bin}/${dependency}"
    chmod +x "${sentinel_bin}/${dependency}"
  done
  printf 'from pathlib import Path\nPath(r"%s").write_text("PYTHONPATH executed")\n' \
    "${sentinel_hit}" >"${sentinel_py}/sitecustomize.py"
  expect_failure installer-zero-dependencies env -i PATH="${sentinel_bin}" \
    PYTHONPATH="${sentinel_py}" "${bash_bin}" "${installer}" apply
  [[ ! -e "${sentinel_hit}" ]] || fail "disabled installer apply executed a dependency"
  if grep -Eq 'exec [0-9]+>"?\$\{maintenance_lock\}"?' "${installer}"; then
    fail "read-only preflight truncates the maintenance lock"
  fi
  python3 - "${gate}" <<'PY'
import json, sys
from datetime import UTC,datetime
payload={
  "available_memory_bytes": 7 * 1024**3,
  "free_disk_bytes": 100 * 1024**3,
  "swap_used_bytes": 0,
  "production_health": "passed",
  "backup": "passed",
  "active_operations": 0,
  "maintenance_lock": "held",
  "deadman": "armed",
  "restorable_firewall": "passed",
  "active_operations_verified_at": datetime.now(UTC).isoformat().replace("+00:00","Z"),
  "backup_verified_at": datetime.now(UTC).isoformat().replace("+00:00","Z"),
  "listener_6443": "absent",
  "baseline_k3s_state": "absent",
  "background_quiescence": "passed",
  "expected_revision": "ebb7bcc3c33ea9c001a5ab25d75915edae2049e7",
  "server_revision": "ebb7bcc3c33ea9c001a5ab25d75915edae2049e7",
  "k3s_installed_version": None,
  "host_addresses": ["10.16.0.4"],
  "docker_networks": ["172.17.0.0/16", "172.31.0.0/16"],
  "host_routes": ["default", "10.16.0.0/16", "172.17.0.0/16"],
}
open(sys.argv[1],"w",encoding="utf-8").write(json.dumps(payload)+"\n")
PY
  python3 "${ctl}" install-preflight --gate "${gate}" \
    --bind-address 10.16.0.4 --admin-cidr 10.16.0.0/16 >/dev/null
  python3 - "${gate}" "${test_root}/admin-overlap.json" <<'PY'
import json, sys
p=json.load(open(sys.argv[1],encoding="utf-8")); p["host_addresses"]=["10.42.0.4"]
open(sys.argv[2],"w",encoding="utf-8").write(json.dumps(p)+"\n")
PY
  expect_failure gate-admin-overlap python3 "${ctl}" install-preflight --gate "${test_root}/admin-overlap.json" \
    --bind-address 10.42.0.4 --admin-cidr 10.42.0.0/16
  python3 "${ctl}" render-config --bind-address 10.16.0.4 \
    --admin-cidr 10.16.0.0/16 --host-address 10.16.0.4 \
    --network 172.17.0.0/16 --network 172.31.0.0/16 --output "${config}" >/dev/null
  grep -Fxq 'cluster-cidr: "10.42.0.0/16"' "${config}" || fail "pod CIDR missing"
  grep -Fxq 'service-cidr: "10.43.0.0/16"' "${config}" || fail "service CIDR missing"
  grep -Fxq '  - traefik' "${config}" || fail "Traefik is not disabled"
  grep -Fxq '  - servicelb' "${config}" || fail "ServiceLB is not disabled"
  grep -Fxq 'secrets-encryption: true' "${config}" || fail "secrets encryption is off"

  for mutation in memory disk swap health active lock deadman firewall revision existing public nonrfc1918 overlap; do
    python3 - "${gate}" "${test_root}/${mutation}.json" "${mutation}" <<'PY'
import json, sys
p=json.load(open(sys.argv[1], encoding="utf-8")); m=sys.argv[3]
if m=="memory": p["available_memory_bytes"]=6*1024**3-1
elif m=="disk": p["free_disk_bytes"]=60*1024**3-1
elif m=="swap": p["swap_used_bytes"]=1
elif m=="health": p["production_health"]="failed"
elif m=="active": p["active_operations"]=1
elif m=="lock": p["maintenance_lock"]="missing"
elif m=="deadman": p["deadman"]="inactive"
elif m=="firewall": p["restorable_firewall"]="missing"
elif m=="revision": p["server_revision"]="0"*40
elif m=="existing": p["k3s_installed_version"]="v1.35.0+k3s1"
elif m=="nonrfc1918": p["host_addresses"]=["198.18.0.1"]
elif m=="overlap": p["docker_networks"].append("10.42.0.0/24")
open(sys.argv[2],"w",encoding="utf-8").write(json.dumps(p)+"\n")
PY
    if [[ "${mutation}" == public ]]; then
      expect_failure "gate-${mutation}" python3 "${ctl}" install-preflight \
        --gate "${test_root}/${mutation}.json" --bind-address 170.168.72.200 \
        --admin-cidr 170.168.72.0/24
    elif [[ "${mutation}" == nonrfc1918 ]]; then
      expect_failure "gate-${mutation}" python3 "${ctl}" install-preflight \
        --gate "${test_root}/${mutation}.json" --bind-address 198.18.0.1 \
        --admin-cidr 198.18.0.0/15
    else
      expect_failure "gate-${mutation}" python3 "${ctl}" install-preflight \
        --gate "${test_root}/${mutation}.json" --bind-address 10.16.0.4 \
        --admin-cidr 10.16.0.0/16
    fi
  done

  expect_failure installer-no-ack bash "${installer}" apply
  expect_failure installer-hard-disabled bash "${installer}" apply \
    --bundle /nonexistent/bundle \
    --offhost-marker /nonexistent/offhost.json \
    --rescue-marker /nonexistent/rescue.json \
    --bind-address 10.16.0.4 \
    --admin-cidr 10.16.0.0/16 \
    --expected-revision 0123456789abcdef0123456789abcdef01234567 \
    --acknowledge PHASE2_NETWORK_MUTATION
}

test_deadman() {
  assert_zero_dependency_hard_gate deadman-arm-zero-dependencies "${deadman}" arm
  assert_zero_dependency_hard_gate deadman-disarm-zero-dependencies "${deadman}" disarm
  # Future arm/disarm acceptance scaffolding below is intentionally unreachable
  # while every mutating dead-man mode is hard-disabled in this delivery.
  return 0
  local bundle="${test_root}/deadman-bundle"
  local attest="${test_root}/deadman-attest"
  local ciphertext="${test_root}/deadman.cms"
  local fakebin="${test_root}/fakebin"
  local state_root="${test_root}/deadman-state"
  local libexec_root="${test_root}/libexec"
  local systemd_root="${test_root}/systemd"
  local systemctl_log="${test_root}/systemctl.log"
  mkdir -p "${fakebin}"
  make_bundle "${bundle}"
  make_signed_attestations "${bundle}" "${attest}" "${ciphertext}"
  cat >"${fakebin}/systemctl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"${PHASE2_TEST_SYSTEMCTL_LOG}"
if [[ "$1" == is-active ]]; then echo active; fi
if [[ "$1" == show ]]; then echo '2099-01-01 00:00:00 UTC'; fi
exit 0
EOF
  cat >"${fakebin}/flock" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "${fakebin}/systemctl" "${fakebin}/flock"
  export PATH="${fakebin}:${PATH}"
  PHASE2_STATE_ROOT="${state_root}" \
  PHASE2_LIBEXEC_ROOT="${libexec_root}" \
  PHASE2_SYSTEMD_ROOT="${systemd_root}" \
  PHASE2_SYSTEMCTL="${fakebin}/systemctl" \
  PHASE2_TEST_SYSTEMCTL_LOG="${systemctl_log}" \
  PHASE2_TRUST_POLICY="${attest}/trust.json" \
    bash "${deadman}" arm --bundle "${bundle}" \
      --offhost-marker "${attest}/offhost.verified.json" \
      --rescue-marker "${attest}/rescue.verified.json" >/dev/null
  [[ -f "${state_root}/armed.json" ]] || fail "dead-man did not arm"
  if [[ "${phase2_windows_acl}" == false ]]; then
    [[ "$(file_mode "${state_root}/armed.json")" == 600 ]] || fail "armed pointer is permissive"
  fi
  grep -Fq 'restart omnia-project-cell-deadman.timer' "${systemctl_log}" \
    || fail "dead-man timer was not restarted with a fresh deadline"

  python3 - "${state_root}/postflight-short.json" "${bundle}/bundle-id.txt" <<'PY'
import json, sys
bid=open(sys.argv[2],encoding="utf-8").read().strip()
p={"bundle_id":bid,"k3s_version":"v1.36.4+k3s1","trusted_hello":"passed","production_health":"passed","soak_seconds":899,"deadman_rehearsal":"passed","rollback_byte_compare":"passed"}
open(sys.argv[1],"w",encoding="utf-8").write(json.dumps(p)+"\n")
PY
  expect_failure short-soak env \
    PHASE2_STATE_ROOT="${state_root}" PHASE2_LIBEXEC_ROOT="${libexec_root}" \
    PHASE2_SYSTEMD_ROOT="${systemd_root}" PHASE2_SYSTEMCTL="${fakebin}/systemctl" \
    PHASE2_TEST_SYSTEMCTL_LOG="${systemctl_log}" \
    PHASE2_TRUST_POLICY="${attest}/trust.json" \
    bash "${deadman}" disarm --postflight "${state_root}/postflight-short.json"

  printf '{"status":"passed"}\n' >"${state_root}/hello-result.json"
  printf '{"status":"passed"}\n' >"${state_root}/health-result.json"
  printf '{"status":"passed"}\n' >"${state_root}/rollback-proof.json"
  python3 - "${state_root}/postflight.json" "${bundle}/bundle-id.txt" \
    "${state_root}/hello-result.json" "${state_root}/health-result.json" \
    "${state_root}/rollback-proof.json" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$(date -u -d '15 minutes ago' +%Y-%m-%dT%H:%M:%SZ)" <<'PY'
import hashlib,json,sys
bid=open(sys.argv[2],encoding="utf-8").read().strip()
sha=lambda p:hashlib.sha256(open(p,"rb").read()).hexdigest()
p={"kind":"postflight","bundle_id":bid,"production_hostname":"prod-host","verifier_id":"recovery-host-01","verified_at":sys.argv[6],"started_at":sys.argv[7],"finished_at":sys.argv[6],"k3s_version":"v1.36.4+k3s1","trusted_hello_evidence_sha256":sha(sys.argv[3]),"production_health_evidence_sha256":sha(sys.argv[4]),"rollback_evidence_sha256":sha(sys.argv[5]),"deadman_rehearsal":"passed","rollback_byte_compare":"passed"}
open(sys.argv[1],"wb").write((json.dumps(p,sort_keys=True,separators=(",",":"))+"\n").encode())
PY
  expect_failure unsigned-postflight env \
    PHASE2_STATE_ROOT="${state_root}" PHASE2_LIBEXEC_ROOT="${libexec_root}" \
    PHASE2_SYSTEMD_ROOT="${systemd_root}" PHASE2_SYSTEMCTL="${fakebin}/systemctl" \
    PHASE2_TEST_SYSTEMCTL_LOG="${systemctl_log}" PHASE2_TRUST_POLICY="${attest}/trust.json" \
    bash "${deadman}" disarm --postflight "${state_root}/postflight.json"
  openssl dgst -sha256 -sign "${attest}/verifier.key" \
    -out "${state_root}/postflight.sig" "${state_root}/postflight.json"
  chmod 400 "${state_root}/postflight.json" "${state_root}/postflight.sig" \
    "${state_root}/hello-result.json" "${state_root}/health-result.json" "${state_root}/rollback-proof.json"
  python3 "${ctl}" verify-postflight --armed "${state_root}/armed.json" \
    --postflight "${state_root}/postflight.json" --signature "${state_root}/postflight.sig" \
    --public-key "${attest}/verifier.pub" --trust-policy "${attest}/trust.json" \
    --hello-result "${state_root}/hello-result.json" --health-result "${state_root}/health-result.json" \
    --rollback-result "${state_root}/rollback-proof.json" --output "${state_root}/postflight.verified.json" >/dev/null

  cp "${state_root}/postflight.json" "${state_root}/unrelated-postflight.json"
  chmod 600 "${state_root}/unrelated-postflight.json"
  python3 - "${state_root}/unrelated-postflight.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1],encoding="utf-8")); p["kind"]="unrelated"; p["bundle_id"]="f"*64
open(sys.argv[1],"wb").write((json.dumps(p,sort_keys=True,separators=(",",":"))+"\n").encode())
PY
  openssl dgst -sha256 -sign "${attest}/verifier.key" \
    -out "${state_root}/unrelated-postflight.sig" "${state_root}/unrelated-postflight.json"
  cp "${state_root}/postflight.verified.json" "${state_root}/unrelated-postflight.verified.json"
  chmod 600 "${state_root}/unrelated-postflight.verified.json"
  python3 - "${state_root}/unrelated-postflight.verified.json" \
    "${state_root}/unrelated-postflight.json" "${state_root}/unrelated-postflight.sig" <<'PY'
import hashlib,json,sys
m=json.load(open(sys.argv[1],encoding="utf-8"))
for key,path in (("postflight",sys.argv[2]),("signature",sys.argv[3])):
    m[key]={"path":path,"sha256":hashlib.sha256(open(path,"rb").read()).hexdigest()}
open(sys.argv[1],"w",encoding="utf-8").write(json.dumps(m,sort_keys=True,separators=(",",":"))+"\n")
PY
  chmod 400 "${state_root}/unrelated-postflight.json" "${state_root}/unrelated-postflight.sig" \
    "${state_root}/unrelated-postflight.verified.json"
  expect_failure unrelated-signed-postflight python3 "${ctl}" validate-postflight-marker \
    --armed "${state_root}/armed.json" --marker "${state_root}/unrelated-postflight.verified.json" \
    --expected-trust-policy "${attest}/trust.json"

  cp "${state_root}/health-result.json" "${state_root}/failed-health-result.json"
  chmod 600 "${state_root}/failed-health-result.json"
  printf '{"status":"failed"}\n' >"${state_root}/failed-health-result.json"
  cp "${state_root}/postflight.json" "${state_root}/failed-evidence-postflight.json"
  chmod 600 "${state_root}/failed-evidence-postflight.json"
  python3 - "${state_root}/failed-evidence-postflight.json" "${state_root}/failed-health-result.json" <<'PY'
import hashlib,json,sys
p=json.load(open(sys.argv[1],encoding="utf-8")); p["production_health_evidence_sha256"]=hashlib.sha256(open(sys.argv[2],"rb").read()).hexdigest()
open(sys.argv[1],"wb").write((json.dumps(p,sort_keys=True,separators=(",",":"))+"\n").encode())
PY
  openssl dgst -sha256 -sign "${attest}/verifier.key" \
    -out "${state_root}/failed-evidence-postflight.sig" "${state_root}/failed-evidence-postflight.json"
  cp "${state_root}/postflight.verified.json" "${state_root}/failed-evidence-postflight.verified.json"
  chmod 600 "${state_root}/failed-evidence-postflight.verified.json"
  python3 - "${state_root}/failed-evidence-postflight.verified.json" \
    "${state_root}/failed-evidence-postflight.json" "${state_root}/failed-evidence-postflight.sig" \
    "${state_root}/failed-health-result.json" <<'PY'
import hashlib,json,sys
m=json.load(open(sys.argv[1],encoding="utf-8"))
for key,path in (("postflight",sys.argv[2]),("signature",sys.argv[3])):
    m[key]={"path":path,"sha256":hashlib.sha256(open(path,"rb").read()).hexdigest()}
m["evidence"]["health_result"]={"path":sys.argv[4],"sha256":hashlib.sha256(open(sys.argv[4],"rb").read()).hexdigest()}
open(sys.argv[1],"w",encoding="utf-8").write(json.dumps(m,sort_keys=True,separators=(",",":"))+"\n")
PY
  chmod 400 "${state_root}/failed-health-result.json" "${state_root}/failed-evidence-postflight.json" \
    "${state_root}/failed-evidence-postflight.sig" "${state_root}/failed-evidence-postflight.verified.json"
  expect_failure failed-signed-postflight-evidence python3 "${ctl}" validate-postflight-marker \
    --armed "${state_root}/armed.json" --marker "${state_root}/failed-evidence-postflight.verified.json" \
    --expected-trust-policy "${attest}/trust.json"

  PHASE2_STATE_ROOT="${state_root}" \
  PHASE2_LIBEXEC_ROOT="${libexec_root}" \
  PHASE2_SYSTEMD_ROOT="${systemd_root}" \
  PHASE2_SYSTEMCTL="${fakebin}/systemctl" \
  PHASE2_TEST_SYSTEMCTL_LOG="${systemctl_log}" \
  PHASE2_TRUST_POLICY="${attest}/trust.json" \
    bash "${deadman}" disarm --postflight-marker "${state_root}/postflight.verified.json" >/dev/null
  [[ ! -e "${state_root}/armed.json" ]] || fail "dead-man stayed armed after valid disarm"
}

test_smoke_and_compare() {
  assert_zero_dependency_hard_gate smoke-apply-zero-dependencies "${smoke}" apply
  python3 "${ctl}" validate-hello --manifest "${manifest}" >/dev/null
  cp "${manifest}" "${test_root}/bad-hello.yaml"
  python3 - "${test_root}/bad-hello.yaml" <<'PY'
import json, sys
p=json.load(open(sys.argv[1],encoding="utf-8"))
for item in p["items"]:
    if item["kind"]=="Service": item["spec"]["type"]="NodePort"
open(sys.argv[1],"w",encoding="utf-8").write(json.dumps(p)+"\n")
PY
  expect_failure hello-nodeport python3 "${ctl}" validate-hello --manifest "${test_root}/bad-hello.yaml"

  cp "${manifest}" "${test_root}/extra-init-hello.yaml"
  python3 - "${test_root}/extra-init-hello.yaml" <<'PY'
import json, sys
p=json.load(open(sys.argv[1],encoding="utf-8"))
for item in p["items"]:
    if item["kind"]=="Deployment": item["spec"]["template"]["spec"]["initContainers"]=[]
open(sys.argv[1],"w",encoding="utf-8").write(json.dumps(p)+"\n")
PY
  expect_failure hello-extra-init python3 "${ctl}" validate-hello --manifest "${test_root}/extra-init-hello.yaml"

  printf '{"dns":"abc","routes":"def","firewall":"ghi"}\n' >"${test_root}/before.json"
  cp "${test_root}/before.json" "${test_root}/after.json"
  python3 "${ctl}" compare --before "${test_root}/before.json" --after "${test_root}/after.json" >/dev/null
  printf '{"dns":"abc","routes":"changed","firewall":"ghi"}\n' >"${test_root}/after.json"
  expect_failure byte-compare python3 "${ctl}" compare --before "${test_root}/before.json" --after "${test_root}/after.json"

  # Fake Kubernetes mutation scaffolding below is intentionally unreachable;
  # this delivery verifies only the manifest and read-only byte comparison.
  return 0

  expect_failure smoke-no-ack bash "${smoke}" apply

  fakekubectl="${test_root}/fake-kubectl"
  fakecurl_dir="${test_root}/fakecurl-bin"
  mkdir -p "${fakecurl_dir}"
  cat >"${fakecurl_dir}/curl" <<'EOF'
#!/usr/bin/env bash
printf 'trusted-hello'
EOF
  cat >"${fakekubectl}" <<'EOF'
#!/usr/bin/env bash
args=" $* "
if [[ "${args}" == *' version -o json '* ]]; then printf '{"serverVersion":{"gitVersion":"v1.36.4+k3s1"}}\n'; exit 0; fi
if [[ "${args}" == *' apply '* || "${args}" == *' rollout status '* ]]; then exit 0; fi
if [[ "${args}" == *' get pod '*jsonpath* ]]; then printf 'registry.k8s.io/e2e-test-images/agnhost@sha256:99c6b4bb4a1e1df3f0b3752168c89358794d02258ebebc26bf21c29399011a85'; exit 0; fi
if [[ "${args}" == *' get pods -A '* ]]; then printf '{"items":[{"metadata":{"namespace":"kube-system","name":"coredns"}},{"metadata":{"namespace":"omnia-project-cell-system","name":"trusted-hello-abc"}}]}\n'; exit 0; fi
if [[ "${args}" == *' port-forward '* ]]; then printf 'Forwarding from 127.0.0.1:43123 -> 8080\n'; while true; do sleep 1; done; fi
resource=""
for candidate in deployments replicasets statefulsets daemonsets jobs cronjobs pods services ingresses networkpolicies persistentvolumeclaims roles rolebindings serviceaccounts secrets configmaps endpointslices; do [[ "${args}" == *" get ${candidate} "* ]] && resource="${candidate}"; done
case "${resource}" in
 deployments) names='trusted-hello' ;; replicasets|pods|endpointslices) names='trusted-hello-abc' ;;
 services) names='trusted-hello' ;; serviceaccounts) names='default' ;; configmaps) names='kube-root-ca.crt' ;; *) names='' ;;
esac
python3 - "${resource}" "${names}" <<'PY'
import json,sys
resource,names=sys.argv[1:]
items=[]
for name in filter(None,names.split(',')):
 p={"metadata":{"name":name}}
 if resource=="pods": p.update(spec={"containers":[{"name":"trusted-hello"}]},status={"containerStatuses":[{"ready":True}]})
 items.append(p)
print(json.dumps({"items":items}))
PY
EOF
  chmod +x "${fakekubectl}" "${fakecurl_dir}/curl"
  PATH="${fakecurl_dir}:${PATH}" PHASE2_KUBECTL="${fakekubectl}" bash "${smoke}" apply \
    --output "${test_root}/trusted-result.json" --acknowledge TRUSTED_HELLO_ONLY >/dev/null
  [[ -f "${test_root}/trusted-result.json" ]] || fail "fake kubectl smoke did not write proof"
}

test_rollback() {
  assert_zero_dependency_hard_gate rollback-zero-dependencies "${rollback}" apply
  # Future rollback acceptance scaffolding below is intentionally unreachable
  # because host rollback mutation is hard-disabled in this delivery.
  return 0
  local bundle="${test_root}/rollback-bundle" attest="${test_root}/rollback-attest"
  local state="${test_root}/rollback-state" host="${test_root}/rollback-host"
  local fakebin="${test_root}/rollback-bin" ciphertext="${test_root}/rollback.cms"
  mkdir -p "${state}" "${host}" "${fakebin}"
  make_bundle "${bundle}"
  make_signed_attestations "${bundle}" "${attest}" "${ciphertext}"
  cp "${attest}/offhost.verified.json" "${state}/offhost.verified.json"
  cp "${attest}/rescue.verified.json" "${state}/rescue.verified.json"
  chmod 400 "${state}/offhost.verified.json" "${state}/rescue.verified.json"
  sha256sum "${ctl}" "${rollback}" "${project_cell_dir}/capture-host-evidence.sh" \
    "${project_cell_dir}/verify-rollback.sh" >"${state}/runtime-tools.sha256"
  chmod 400 "${state}/runtime-tools.sha256"
  PHASE2_TEST_BUNDLE_SHELL="${bundle}" python3 - "${state}/armed.json" "${bundle}/bundle-id.txt" <<'PY'
import json,os,sys
p={"bundle_path":os.environ["PHASE2_TEST_BUNDLE_SHELL"],"bundle_id":open(sys.argv[2],encoding="utf-8").read().strip(),"production_hostname":"prod-host"}
open(sys.argv[1],"w",encoding="utf-8").write(json.dumps(p)+"\n")
PY
  for relative in usr/local/bin/k3s usr/local/bin/k3s-killall.sh usr/local/bin/k3s-uninstall.sh etc/rancher/k3s/config.yaml var/lib/rancher/k3s/agent/etc/kubelet.conf.d/10-project-cell-reserves.conf etc/systemd/system/k3s.service; do
    mkdir -p "$(dirname "${host}/${relative}")"
    printf '#!/usr/bin/env bash\nexit 0\n' >"${host}/${relative}"
    chmod 700 "${host}/${relative}"
  done
  PHASE2_TEST_INSTALLED_ROOT="${host}" python3 "${ctl}" installed-manifest \
    --bundle "${bundle}" --output "${state}/installed-state.json" >/dev/null
  cp "${host}/usr/local/bin/k3s-killall.sh" "${host}/usr/local/bin/k3s-killall.good"
  printf '# tampered\n' >>"${host}/usr/local/bin/k3s-killall.sh"
  expect_failure installed-helper-tamper env PHASE2_TEST_INSTALLED_ROOT="${host}" \
    python3 "${ctl}" verify-installed --manifest "${state}/installed-state.json"
  mv "${host}/usr/local/bin/k3s-killall.good" "${host}/usr/local/bin/k3s-killall.sh"
  cat >"${fakebin}/fake" <<'EOF'
#!/usr/bin/env bash
if [[ "$(basename "$0")" == sysctl && "${1:-}" == -n ]]; then echo 0; fi
exit 0
EOF
  for name in flock systemctl iptables-restore ip6tables-restore nft ip sysctl modprobe; do cp "${fakebin}/fake" "${fakebin}/${name}"; done
  chmod +x "${fakebin}/"*
  cat >"${test_root}/fake-capture.sh" <<'EOF'
#!/usr/bin/env bash
cp -a "${PHASE2_TEST_BASELINE}" "$1"
if [[ "${PHASE2_TEST_TAMPER_AFTER:-0}" == 1 ]]; then
  chmod 600 "$1/evidence/listeners.txt"
  printf 'tamper\n' >>"$1/evidence/listeners.txt"
fi
EOF
  chmod +x "${test_root}/fake-capture.sh"
  expect_failure rollback-byte-mismatch env PATH="${fakebin}:${PATH}" PHASE2_STATE_ROOT="${state}" \
    PHASE2_MAINTENANCE_LOCK="${state}/maintenance.lock" PHASE2_TRUST_POLICY="${attest}/trust.json" \
    PHASE2_TEST_HOST_ROOT="${host}" PHASE2_TEST_INSTALLED_ROOT="${host}" \
    PHASE2_TEST_CAPTURE_COMMAND="${test_root}/fake-capture.sh" PHASE2_TEST_BASELINE="${bundle}" \
    PHASE2_TEST_TAMPER_AFTER=1 bash "${rollback}" apply --acknowledge BYTE_COMPARE_REQUIRED
  [[ -f "${state}/armed.json" ]] || fail "failed rollback disarmed the dead-man"
  rm -rf "${state}/rollback-after-$(tr -d '\r\n' <"${bundle}/bundle-id.txt")"
  PATH="${fakebin}:${PATH}" PHASE2_STATE_ROOT="${state}" \
    PHASE2_MAINTENANCE_LOCK="${state}/maintenance.lock" PHASE2_TRUST_POLICY="${attest}/trust.json" \
    PHASE2_TEST_HOST_ROOT="${host}" PHASE2_TEST_INSTALLED_ROOT="${host}" \
    PHASE2_TEST_CAPTURE_COMMAND="${test_root}/fake-capture.sh" PHASE2_TEST_BASELINE="${bundle}" \
    bash "${rollback}" apply --acknowledge BYTE_COMPARE_REQUIRED >/dev/null
  python3 - "${state}/rollback-result.json" <<'PY'
import json,sys
assert json.load(open(sys.argv[1],encoding="utf-8"))["status"]=="passed"
PY
  [[ -f "${state}/rollback-proof.json" ]] || fail "rollback did not retain byte-compare proof"
}

for required in "${ctl}" "${deadman}" "${installer}" "${smoke}" "${sealer}" "${rollback}" "${manifest}"; do
  [[ -f "${required}" ]] || fail "required artifact is missing: ${required}"
done

group="${1:-all}"
case "${group}" in
  evidence) test_evidence ;;
  install) test_install ;;
  deadman) test_deadman ;;
  smoke) test_smoke_and_compare ;;
  rollback) test_rollback ;;
  all)
    test_evidence
    test_install
    test_deadman
    test_smoke_and_compare
    test_rollback
    ;;
  *) fail "unknown test group: ${group}" ;;
esac

echo "phase2 contract tests passed (${group})"
