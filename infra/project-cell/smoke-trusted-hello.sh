#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == apply ]]; then
  printf '%s\n' \
    'phase2 trusted hello failed: Kubernetes resource mutation is hard-disabled in this delivery' >&2
  exit 1
fi

# Only read-only manifest preflight is reachable. Kubernetes apply scaffolding
# below is intentionally unreachable and is not verified live.

fail() {
  echo "phase2 trusted hello failed: $1" >&2
  exit 1
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ctl="${script_dir}/phase2ctl.py"
manifest="${script_dir}/manifests/trusted-hello.yaml"
kubectl_bin="${PHASE2_KUBECTL:-k3s kubectl}"
if [[ "${PHASE2_TEST_MODE:-0}" != 1 ]]; then
  [[ "${PHASE2_EXPECTED_REVISION:-}" =~ ^[0-9a-f]{40}$ ]] || fail "PHASE2_EXPECTED_REVISION is required"
  repo_root="$(git -C "${script_dir}" rev-parse --show-toplevel)"
  python3 "${ctl}" verify-checkout --repo "${repo_root}" \
    --expected-revision "${PHASE2_EXPECTED_REVISION}" >/dev/null
fi

command_name="${1:-}"
shift || true
output=""
acknowledgement=""
while (($#)); do
  case "$1" in
    --output) output="${2:-}"; shift 2 ;;
    --acknowledge) acknowledgement="${2:-}"; shift 2 ;;
    *) fail "unknown option: $1" ;;
  esac
done
[[ "${command_name}" == preflight || "${command_name}" == apply ]] \
  || fail "usage: smoke-trusted-hello.sh preflight|apply --output ABS [--acknowledge TRUSTED_HELLO_ONLY]"
python3 "${ctl}" validate-hello --manifest "${manifest}" >/dev/null
if [[ "${command_name}" == preflight ]]; then
  echo "trusted hello manifest preflight passed; no Kubernetes mutation performed"
  exit 0
fi
[[ "${acknowledgement}" == TRUSTED_HELLO_ONLY ]] \
  || fail "apply requires --acknowledge TRUSTED_HELLO_ONLY"
[[ "${output}" == /* ]] || fail "apply requires an absolute --output path"
[[ ! -e "${output}" && ! -L "${output}" ]] || fail "output already exists"

read -r -a kubectl_parts <<<"${kubectl_bin}"
"${kubectl_parts[@]}" version -o json >/dev/null
server_version="$("${kubectl_parts[@]}" version -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)["serverVersion"]["gitVersion"])')"
[[ "${server_version}" == v1.36.4+k3s1 ]] || fail "live K3s version differs from the Phase 2 pin"
"${kubectl_parts[@]}" apply --server-side --field-manager=omnia-phase2-trusted \
  -f "${manifest}" >/dev/null
"${kubectl_parts[@]}" -n omnia-project-cell-system rollout status \
  deployment/trusted-hello --timeout=180s >/dev/null

image_id="$("${kubectl_parts[@]}" -n omnia-project-cell-system get pod \
  -l app.kubernetes.io/name=trusted-hello -o jsonpath='{.items[0].status.containerStatuses[0].imageID}')"
[[ "${image_id}" == *'sha256:99c6b4bb4a1e1df3f0b3752168c89358794d02258ebebc26bf21c29399011a85' ]] \
  || fail "running hello imageID does not match the approved digest"

"${kubectl_parts[@]}" get pods -A -o json | python3 -c '
import json,sys
payload=json.load(sys.stdin)
unexpected=[]
for pod in payload.get("items",[]):
    ns=pod.get("metadata",{}).get("namespace")
    name=pod.get("metadata",{}).get("name")
    if ns not in {"kube-system","omnia-project-cell-system"}:
        unexpected.append(f"{ns}/{name}")
if unexpected:
    raise SystemExit("unexpected non-system workloads: "+", ".join(unexpected))
' >/dev/null

inventory_root="$(mktemp -d)"
for resource in deployments replicasets statefulsets daemonsets jobs cronjobs pods services ingresses networkpolicies persistentvolumeclaims roles rolebindings serviceaccounts secrets configmaps endpointslices; do
  "${kubectl_parts[@]}" -n omnia-project-cell-system get "${resource}" -o json >"${inventory_root}/${resource}.json"
done
python3 - "${inventory_root}" <<'PY'
import json, pathlib, sys
root=pathlib.Path(sys.argv[1])
items={p.stem:json.load(open(p,encoding="utf-8")).get("items",[]) for p in root.glob("*.json")}
exact={
 "deployments":{"trusted-hello"}, "statefulsets":set(), "daemonsets":set(), "jobs":set(), "cronjobs":set(),
 "services":{"trusted-hello"}, "ingresses":set(), "networkpolicies":set(), "persistentvolumeclaims":set(),
 "roles":set(), "rolebindings":set(), "serviceaccounts":{"default"}, "secrets":set(),
 "configmaps":{"kube-root-ca.crt"},
}
for resource,names in exact.items():
    actual={item.get("metadata",{}).get("name") for item in items[resource]}
    if actual!=names: raise SystemExit(f"unexpected live {resource}: {sorted(actual)}")
for resource,prefix in (("replicasets","trusted-hello-"),("pods","trusted-hello-"),("endpointslices","trusted-hello-")):
    actual=[item for item in items[resource] if str(item.get("metadata",{}).get("name","")).startswith(prefix)]
    if len(items[resource])!=1 or len(actual)!=1: raise SystemExit(f"unexpected live {resource} inventory")
pod=items["pods"][0]
containers=pod.get("spec",{}).get("containers",[])
statuses=pod.get("status",{}).get("containerStatuses",[])
if len(containers)!=1 or len(statuses)!=1 or not statuses[0].get("ready"):
    raise SystemExit("trusted hello must have exactly one Ready container")
PY
rm -rf "${inventory_root}"

port_log="$(mktemp)"
cleanup() {
  if [[ -n "${port_forward_pid:-}" ]]; then kill "${port_forward_pid}" >/dev/null 2>&1 || true; fi
  rm -f "${port_log}"
}
trap cleanup EXIT
"${kubectl_parts[@]}" -n omnia-project-cell-system port-forward \
  --address 127.0.0.1 service/trusted-hello 0:8080 >"${port_log}" 2>&1 &
port_forward_pid=$!
port=""
for _attempt in $(seq 1 60); do
  port="$(sed -nE 's/^Forwarding from 127\.0\.0\.1:([0-9]+) -> 8080$/\1/p' "${port_log}" | head -n 1)"
  [[ -n "${port}" ]] && break
  kill -0 "${port_forward_pid}" >/dev/null 2>&1 || fail "port-forward exited before readiness"
  sleep 0.25
done
[[ -n "${port}" ]] || fail "port-forward did not publish a localhost port"
body="$(curl -fsS --max-time 5 "http://127.0.0.1:${port}/echo?msg=trusted-hello")"
[[ "${body}" == trusted-hello ]] || fail "trusted hello response body is not exact"

version="${server_version}"
temporary="$(mktemp "${output}.tmp.XXXXXX")"
python3 - "${temporary}" "${version}" "${image_id}" <<'PY'
import json,sys
payload={"status":"passed","k3s_version":sys.argv[2],"image_id":sys.argv[3],"echo":"trusted-hello","exposure":"localhost-port-forward"}
open(sys.argv[1],"w",encoding="utf-8").write(json.dumps(payload,sort_keys=True,separators=(",",":"))+"\n")
PY
chmod 600 "${temporary}"
mv "${temporary}" "${output}"
trap - EXIT
cleanup
echo "phase2 trusted hello passed"
