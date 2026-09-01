#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "phase2 rollback sealing failed: $1" >&2
  exit 1
}

[[ "$#" -eq 4 ]] || fail "usage: seal-rollback-bundle.sh BUNDLE RECIPIENT_CERT TRUST_POLICY OUTPUT_DIRECTORY"
bundle="$1"
recipient_cert="$2"
trust_policy="$3"
output_directory="$4"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ctl="${script_dir}/phase2ctl.py"
if [[ "${PHASE2_TEST_MODE:-0}" != 1 && "${trust_policy}" != /etc/omnia/project-cell/phase2-trust.json ]]; then
  fail "production sealing requires the pinned out-of-checkout trust policy"
fi
if [[ "${PHASE2_TEST_MODE:-0}" != 1 ]]; then
  export PHASE2_REQUIRE_ROOT_OWNERSHIP=1
  [[ "${PHASE2_EXPECTED_REVISION:-}" =~ ^[0-9a-f]{40}$ ]] || fail "PHASE2_EXPECTED_REVISION is required"
  repo_root="$(git -C "${script_dir}" rev-parse --show-toplevel)"
  python3 "${ctl}" verify-checkout --repo "${repo_root}" \
    --expected-revision "${PHASE2_EXPECTED_REVISION}" >/dev/null
fi

for value in "${bundle}" "${recipient_cert}" "${trust_policy}" "${output_directory}"; do
  [[ "${value}" == /* ]] || fail "all paths must be absolute"
done
[[ -d "${bundle}" && ! -L "${bundle}" ]] || fail "bundle must be a non-symlink directory"
[[ -f "${recipient_cert}" && ! -L "${recipient_cert}" ]] || fail "recipient certificate must be a regular file"
[[ -d "${output_directory}" && ! -L "${output_directory}" ]] || fail "output directory must already exist"
if [[ "${PHASE2_TEST_ALLOW_WINDOWS_ACL:-0}" != 1 ]]; then
  [[ "$(stat -c '%a' "${output_directory}" 2>/dev/null || stat -f '%Lp' "${output_directory}")" == 700 ]] \
    || fail "output directory must be mode 700"
fi
for command_name in python3 openssl tar sha256sum; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "required command is unavailable: ${command_name}"
done
python3 "${ctl}" verify-recipient --certificate "${recipient_cert}" \
  --trust-policy "${trust_policy}" >/dev/null
python3 "${ctl}" verify-bundle --bundle "${bundle}" >/dev/null

bundle_id="$(tr -d '\n' <"${bundle}/bundle-id.txt")"
plaintext="$(mktemp "${output_directory}/.${bundle_id}.tar.XXXXXX")"
ciphertext="${output_directory}/${bundle_id}.tar.cms"
checksum="${ciphertext}.sha256"
cleanup() {
  rm -f "${plaintext}"
}
trap cleanup EXIT
[[ ! -e "${ciphertext}" && ! -L "${ciphertext}" && ! -e "${checksum}" && ! -L "${checksum}" ]] \
  || fail "sealed output already exists"
chmod 600 "${plaintext}"
tar --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner \
  -C "$(dirname "${bundle}")" -cf "${plaintext}" "$(basename "${bundle}")"
openssl cms -encrypt -binary -aes-256-cbc -outform DER \
  -in "${plaintext}" -out "${ciphertext}" "${recipient_cert}"
chmod 600 "${ciphertext}"
printf '%s  %s\n' "$(sha256sum "${ciphertext}" | awk '{print $1}')" "$(basename "${ciphertext}")" \
  >"${checksum}"
chmod 600 "${checksum}"
trap - EXIT
rm -f "${plaintext}"
echo "sealed rollback bundle: ${ciphertext}"
