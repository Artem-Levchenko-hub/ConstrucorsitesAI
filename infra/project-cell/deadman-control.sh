#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == arm || "${1:-}" == disarm ]]; then
  printf '%s\n' \
    'phase2 dead-man control failed: mutating arm/disarm are hard-disabled in this delivery' >&2
  exit 1
fi

# Only `status` is reachable. The arm/disarm case bodies below are retained as
# explicitly unreachable future scaffolding and are not an enabled contract.

fail() {
  echo "phase2 dead-man control failed: $1" >&2
  exit 1
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ctl="${script_dir}/phase2ctl.py"
state_root="${PHASE2_STATE_ROOT:-/var/lib/omnia-project-cell-phase2}"
libexec_root="${PHASE2_LIBEXEC_ROOT:-/usr/local/libexec/omnia-project-cell}"
systemd_root="${PHASE2_SYSTEMD_ROOT:-/etc/systemd/system}"
systemctl_bin="${PHASE2_SYSTEMCTL:-systemctl}"
trust_policy=/etc/omnia/project-cell/phase2-trust.json
if [[ "${PHASE2_TEST_MODE:-0}" == 1 ]]; then
  trust_policy="${PHASE2_TRUST_POLICY:?test mode requires PHASE2_TRUST_POLICY}"
else
  export PHASE2_REQUIRE_ROOT_OWNERSHIP=1
fi

for path in "${state_root}" "${libexec_root}" "${systemd_root}"; do
  [[ "${path}" == /* ]] || fail "protected paths must be absolute"
  [[ ! -L "${path}" ]] || fail "protected path must not be a symlink: ${path}"
done
if [[ "${state_root}" == /var/lib/* && "$(id -u)" -ne 0 ]]; then
  fail "production dead-man control must run as root"
fi

atomic_json() {
  local destination="$1"
  shift
  local temporary
  temporary="$(mktemp "${destination}.tmp.XXXXXX")"
  chmod 600 "${temporary}"
  python3 - "${temporary}" "$@" <<'PY'
import json, sys
destination=sys.argv[1]
pairs=sys.argv[2:]
payload={pairs[i]:pairs[i+1] for i in range(0,len(pairs),2)}
open(destination,"w",encoding="utf-8").write(json.dumps(payload,sort_keys=True,separators=(",",":"))+"\n")
PY
  mv -f "${temporary}" "${destination}"
  chmod 600 "${destination}"
}

command_name="${1:-}"
shift || true
case "${command_name}" in
  arm)
    bundle=""
    offhost_marker=""
    rescue_marker=""
    expected_revision=""
    while (($#)); do
      case "$1" in
        --bundle) bundle="${2:-}"; shift 2 ;;
        --offhost-marker) offhost_marker="${2:-}"; shift 2 ;;
        --rescue-marker) rescue_marker="${2:-}"; shift 2 ;;
        --expected-revision) expected_revision="${2:-}"; shift 2 ;;
        *) fail "unknown arm option: $1" ;;
      esac
    done
    [[ "${bundle}" == /* && "${offhost_marker}" == /* && "${rescue_marker}" == /* ]] \
      || fail "arm paths must be absolute"
    if [[ "${PHASE2_TEST_MODE:-0}" != 1 ]]; then
      [[ "${expected_revision}" =~ ^[0-9a-f]{40}$ ]] || fail "arm requires --expected-revision"
      repo_root="$(git -C "${script_dir}" rev-parse --show-toplevel)"
      python3 "${ctl}" verify-checkout --repo "${repo_root}" \
        --expected-revision "${expected_revision}" >/dev/null
    fi
    mkdir -p "${state_root}"
    chmod 700 "${state_root}"
    exec 8>"${state_root}/state-transition.lock"
    flock -x 8
    bundle_id="$(python3 "${ctl}" verify-bundle --bundle "${bundle}")"
    python3 "${ctl}" validate-marker --kind offhost --bundle "${bundle}" \
      --marker "${offhost_marker}" --expected-trust-policy "${trust_policy}" >/dev/null
    python3 "${ctl}" validate-marker --kind provider_rescue --bundle "${bundle}" \
      --marker "${rescue_marker}" --expected-trust-policy "${trust_policy}" >/dev/null
    mkdir -p "${libexec_root}" "${systemd_root}"
    chmod 700 "${state_root}" "${libexec_root}"
    if [[ -e "${state_root}/armed.json" || -L "${state_root}/armed.json" ]]; then
      existing_id="$(python3 - "${state_root}/armed.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1],encoding="utf-8")).get("bundle_id",""))
PY
)"
      [[ "${existing_id}" == "${bundle_id}" ]] || fail "a different rollback bundle is already armed"
    fi
    install -m 700 "${script_dir}/phase2ctl.py" "${libexec_root}/phase2ctl.py"
    install -m 700 "${script_dir}/rollback-phase2.sh" "${libexec_root}/rollback-phase2.sh"
    install -m 700 "${script_dir}/capture-host-evidence.sh" "${libexec_root}/capture-host-evidence.sh"
    install -m 700 "${script_dir}/verify-rollback.sh" "${libexec_root}/verify-rollback.sh"
    install -m 600 "${script_dir}/versions.env" "${libexec_root}/versions.env"
    install -m 600 "${script_dir}/systemd/omnia-project-cell-deadman.service" \
      "${systemd_root}/omnia-project-cell-deadman.service"
    install -m 600 "${script_dir}/systemd/omnia-project-cell-deadman.timer" \
      "${systemd_root}/omnia-project-cell-deadman.timer"
    runtime_manifest="${state_root}/runtime-tools.sha256"
    temporary_manifest="$(mktemp "${runtime_manifest}.tmp.XXXXXX")"
    sha256sum "${libexec_root}/phase2ctl.py" "${libexec_root}/rollback-phase2.sh" \
      "${libexec_root}/capture-host-evidence.sh" "${libexec_root}/verify-rollback.sh" \
      >"${temporary_manifest}"
    chmod 400 "${temporary_manifest}"
    mv -f "${temporary_manifest}" "${runtime_manifest}"
    install -m 400 "${offhost_marker}" "${state_root}/offhost.verified.json"
    install -m 400 "${rescue_marker}" "${state_root}/rescue.verified.json"
    candidate="${state_root}/armed.next.json"
    rm -f "${candidate}"
    trap 'rm -f "${candidate}"' EXIT
    production_hostname="$(python3 - "${bundle}/manifest.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1],encoding="utf-8"))["hostname"])
PY
)"
    atomic_json "${candidate}" \
      bundle_id "${bundle_id}" bundle_path "${bundle}" production_hostname "${production_hostname}" \
      armed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    "${systemctl_bin}" daemon-reload
    "${systemctl_bin}" enable omnia-project-cell-deadman.timer
    "${systemctl_bin}" restart omnia-project-cell-deadman.timer
    [[ "$("${systemctl_bin}" is-active omnia-project-cell-deadman.timer)" == active ]] \
      || fail "dead-man timer did not become active"
    next_elapse="$(LC_ALL=C "${systemctl_bin}" show omnia-project-cell-deadman.timer \
      --property=NextElapseUSecRealtime --value)"
    [[ -n "${next_elapse}" && "${next_elapse}" != n/a ]] || fail "dead-man has no future deadline"
    next_epoch="$(LC_ALL=C date -d "${next_elapse}" +%s 2>/dev/null)" \
      || fail "dead-man future deadline is not parseable"
    ((next_epoch > $(date +%s))) || fail "dead-man deadline is not in the future"
    mv -f "${candidate}" "${state_root}/armed.json"
    chmod 600 "${state_root}/armed.json"
    trap - EXIT
    echo "phase2 dead-man armed for bundle ${bundle_id}"
    ;;
  disarm)
    postflight_marker=""
    while (($#)); do
      case "$1" in
        --postflight-marker) postflight_marker="${2:-}"; shift 2 ;;
        *) fail "unknown disarm option: $1" ;;
      esac
    done
    [[ -n "${postflight_marker}" ]] || fail "disarm requires --postflight-marker"
    exec 8>"${state_root}/state-transition.lock"
    flock -x 8
    [[ -f "${state_root}/armed.json" && ! -L "${state_root}/armed.json" ]] \
      || fail "dead-man is not armed"
    python3 "${ctl}" validate-postflight-marker --armed "${state_root}/armed.json" \
      --marker "${postflight_marker}" --expected-trust-policy "${trust_policy}" >/dev/null
    "${systemctl_bin}" disable --now omnia-project-cell-deadman.timer
    rm -f "${state_root}/armed.json"
    echo "phase2 dead-man disarmed after verified postflight"
    ;;
  status)
    [[ -f "${state_root}/armed.json" && ! -L "${state_root}/armed.json" ]] \
      || fail "dead-man is not armed"
    "${systemctl_bin}" is-active omnia-project-cell-deadman.timer
    ;;
  *) fail "usage: deadman-control.sh arm|disarm|status ..." ;;
esac
