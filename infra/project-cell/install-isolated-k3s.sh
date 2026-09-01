#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == apply ]]; then
  printf '%s\n' \
    'phase2 K3s install failed: live apply is hard-disabled until external backup freshness, worker quiescence, and rollback acceptance are independently proven' >&2
  exit 1
fi

# Only read-only preflight is reachable. Installation scaffolding below is
# intentionally unreachable and is not an enabled or live-verified contract.

fail() {
  echo "phase2 K3s install failed: $1" >&2
  exit 1
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ctl="${script_dir}/phase2ctl.py"
K3S_VERSION="$(python3 "${ctl}" policy-value --name K3S_VERSION)"
K3S_AMD64_SHA256="$(python3 "${ctl}" policy-value --name K3S_AMD64_SHA256)"
K3S_SHA256SUM_AMD64_SHA256="$(python3 "${ctl}" policy-value --name K3S_SHA256SUM_AMD64_SHA256)"
K3S_INSTALL_SH_SHA256="$(python3 "${ctl}" policy-value --name K3S_INSTALL_SH_SHA256)"
K3S_INSTALL_SH_URL="$(python3 "${ctl}" policy-value --name K3S_INSTALL_SH_URL)"
K3S_SHA256SUM_AMD64_URL="$(python3 "${ctl}" policy-value --name K3S_SHA256SUM_AMD64_URL)"
state_root="${PHASE2_STATE_ROOT:-/var/lib/omnia-project-cell-phase2}"
systemctl_bin="${PHASE2_SYSTEMCTL:-systemctl}"
trust_policy=/etc/omnia/project-cell/phase2-trust.json
if [[ "${PHASE2_TEST_MODE:-0}" == 1 ]]; then
  trust_policy="${PHASE2_TRUST_POLICY:?test mode requires PHASE2_TRUST_POLICY}"
else
  export PHASE2_REQUIRE_ROOT_OWNERSHIP=1
fi

command_name="${1:-}"
shift || true
bundle=""
offhost_marker=""
rescue_marker=""
bind_address=""
admin_cidr=""
expected_revision=""
acknowledgement=""
while (($#)); do
  case "$1" in
    --bundle) bundle="${2:-}"; shift 2 ;;
    --offhost-marker) offhost_marker="${2:-}"; shift 2 ;;
    --rescue-marker) rescue_marker="${2:-}"; shift 2 ;;
    --bind-address) bind_address="${2:-}"; shift 2 ;;
    --admin-cidr) admin_cidr="${2:-}"; shift 2 ;;
    --expected-revision) expected_revision="${2:-}"; shift 2 ;;
    --acknowledge) acknowledgement="${2:-}"; shift 2 ;;
    *) fail "unknown option: $1" ;;
  esac
done

usage="usage: install-isolated-k3s.sh preflight|apply --bundle ABS --offhost-marker ABS --rescue-marker ABS --bind-address IP --admin-cidr CIDR --expected-revision SHA [--acknowledge PHASE2_NETWORK_MUTATION]"
[[ "${command_name}" == preflight || "${command_name}" == apply ]] || fail "${usage}"
for value in "${bundle}" "${offhost_marker}" "${rescue_marker}"; do
  [[ "${value}" == /* ]] || fail "${usage}"
done
[[ -n "${bind_address}" && -n "${admin_cidr}" ]] || fail "${usage}"
[[ "${expected_revision}" =~ ^[0-9a-f]{40}$ ]] || fail "expected revision must be a full Git SHA"
for command in python3 curl sha256sum ip docker git "${systemctl_bin}"; do
  command -v "${command}" >/dev/null 2>&1 || fail "required command is unavailable: ${command}"
done
[[ "$(uname -m)" == x86_64 ]] || fail "the pinned Phase 2 artifact is amd64-only"

repo_root="$(git -C "${script_dir}" rev-parse --show-toplevel)"
python3 "${ctl}" verify-checkout --repo "${repo_root}" \
  --expected-revision "${expected_revision}" >/dev/null

python3 "${ctl}" verify-bundle --bundle "${bundle}" >/dev/null
python3 "${ctl}" validate-marker --kind offhost --bundle "${bundle}" \
  --marker "${offhost_marker}" --expected-trust-policy "${trust_policy}" >/dev/null
python3 "${ctl}" validate-marker --kind provider_rescue --bundle "${bundle}" \
  --marker "${rescue_marker}" --expected-trust-policy "${trust_policy}" >/dev/null
bundle_id="$(tr -d '\n' <"${bundle}/bundle-id.txt")"
[[ -f "${state_root}/armed.json" && ! -L "${state_root}/armed.json" ]] \
  || fail "host-local dead-man is not armed"
armed_bundle_id="$(python3 - "${state_root}/armed.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1],encoding="utf-8")).get("bundle_id",""))
PY
)"
[[ "${armed_bundle_id}" == "${bundle_id}" ]] || fail "dead-man is armed for a different bundle"
[[ "$("${systemctl_bin}" is-active omnia-project-cell-deadman.timer)" == active ]] \
  || fail "dead-man timer is not active"

temporary_root="$(mktemp -d -t omnia-phase2-install.XXXXXXXXXX)"
cleanup() {
  rm -rf "${temporary_root}"
}
trap cleanup EXIT
gate="${temporary_root}/live-gate.json"

python3 - \
  "${gate}" \
  "${bundle}/evidence/capacity.json" \
  "${bundle}/evidence/production-health.json" \
  "${bundle}/evidence/active-operations.json" \
  "${bundle}/evidence/backup.json" \
  "${expected_revision}" \
  "${K3S_VERSION}" <<'PY'
import json, os, shutil, subprocess, sys, urllib.request
out, baseline_capacity, baseline_health, operations_path, backup_path, expected_revision, pinned_version = sys.argv[1:]
operations=json.load(open(operations_path,encoding="utf-8"))
backup=json.load(open(backup_path,encoding="utf-8"))
if operations.get("status") != "passed":
    raise SystemExit("active-operation evidence is not passed")
operation_fields=("active_generations","active_builds","active_deploys","active_backups","active_restores","active_deletes","active_promotions")
if any(type(operations.get(field)) is not int or operations[field] != 0 for field in operation_fields):
    raise SystemExit("one or more production operations are active or unverified")
if backup.get("status") != "passed" or backup.get("checksum_verified") is not True or backup.get("restore_test") != "passed":
    raise SystemExit("backup evidence is not passed")
if operations.get("background_quiescence") != "passed":
    raise SystemExit("background quiescence evidence is not passed")
mem={}
for line in open("/proc/meminfo",encoding="ascii"):
    key,value=line.split(":",1); mem[key]=int(value.strip().split()[0])*1024
health=True
for url in ("http://127.0.0.1:8200/api/health","http://127.0.0.1:8101/health","http://127.0.0.1:8003/health","http://127.0.0.1:3100/web-health"):
    try:
        with urllib.request.urlopen(url,timeout=5) as response:
            health = health and response.status == 200
    except Exception:
        health=False
revision=subprocess.run(["git","-C","/opt/omnia","rev-parse","HEAD"],text=True,capture_output=True,check=False).stdout.strip()
addresses=json.loads(subprocess.run(["ip","-j","address","show"],text=True,capture_output=True,check=True).stdout)
host_addresses=[]
for interface in addresses:
    for info in interface.get("addr_info",[]):
        if info.get("family")=="inet" and info.get("local"): host_addresses.append(info["local"])
network_ids=subprocess.run(["docker","network","ls","-q"],text=True,capture_output=True,check=True).stdout.split()
docker_networks=[]
if network_ids:
    networks=json.loads(subprocess.run(["docker","network","inspect",*network_ids],text=True,capture_output=True,check=True).stdout)
    for network in networks:
        for config in (network.get("IPAM") or {}).get("Config") or []:
            if config.get("Subnet"): docker_networks.append(config["Subnet"])
routes=json.loads(subprocess.run(["ip","-j","route","show","table","all"],text=True,capture_output=True,check=True).stdout)
host_routes=[route.get("dst","0.0.0.0/0") for route in routes]
installed=None
version=subprocess.run(["k3s","--version"],text=True,capture_output=True,check=False)
if version.returncode==0: installed=version.stdout.splitlines()[0].split()[2]
listeners=subprocess.run(["ss","-Hlnat"],text=True,capture_output=True,check=True).stdout.splitlines()
listener_6443="present" if any(":6443" in line.split()[3] for line in listeners if len(line.split()) > 3) else "absent"
filesystem=json.load(open(os.path.join(os.path.dirname(baseline_capacity),"filesystem-state.json"),encoding="utf-8"))
baseline_k3s_state="absent" if all(item.get("state")=="absent" for item in filesystem.get("paths",[])) and installed is None else "present"
payload={
  "available_memory_bytes":mem.get("MemAvailable",0),
  "free_disk_bytes":shutil.disk_usage("/").free,
  "swap_used_bytes":mem.get("SwapTotal",0)-mem.get("SwapFree",0),
  "production_health":"passed" if health else "failed",
  "backup":"passed",
  "active_operations":sum(operations[field] for field in operation_fields),
  "active_operations_verified_at":operations.get("verified_at"),
  "backup_verified_at":backup.get("verified_at"),
  "background_quiescence":operations.get("background_quiescence"),
  "listener_6443":listener_6443,
  "baseline_k3s_state":baseline_k3s_state,
  "maintenance_lock":"not-held-read-only",
  "deadman":"armed",
  "restorable_firewall":"passed" if ((shutil.which("iptables-save") and shutil.which("iptables-restore")) or shutil.which("nft")) else "missing",
  "expected_revision":expected_revision,
  "server_revision":revision,
  "k3s_installed_version":installed,
  "host_addresses":sorted(set(host_addresses)),
  "docker_networks":sorted(set(docker_networks)),
  "host_routes":sorted(set(host_routes)),
}
open(out,"w",encoding="utf-8").write(json.dumps(payload,sort_keys=True,separators=(",",":"))+"\n")
PY
chmod 600 "${gate}"
python3 "${ctl}" install-preflight --read-only --gate "${gate}" \
  --bind-address "${bind_address}" --admin-cidr "${admin_cidr}" >/dev/null

host_addresses=()
while IFS= read -r address; do host_addresses+=(--host-address "${address}"); done < <(
  python3 - "${gate}" <<'PY'
import json,sys
for value in json.load(open(sys.argv[1],encoding="utf-8"))["host_addresses"]: print(value)
PY
)
networks=()
while IFS= read -r network; do networks+=(--network "${network}"); done < <(
  python3 - "${gate}" <<'PY'
import json,sys
p=json.load(open(sys.argv[1],encoding="utf-8"))
for value in p["docker_networks"]+p["host_routes"]: print(value)
PY
)
rendered_config="${temporary_root}/config.yaml"
python3 "${ctl}" render-config --bind-address "${bind_address}" \
  --admin-cidr "${admin_cidr}" "${host_addresses[@]}" "${networks[@]}" \
  --output "${rendered_config}" >/dev/null

if [[ "${command_name}" == preflight ]]; then
  echo "phase2 K3s preflight passed; no host mutation performed"
  exit 0
fi

checksum_file="${temporary_root}/sha256sum-amd64.txt"
install_script="${temporary_root}/install.sh"
curl -fL --proto '=https' --tlsv1.2 --retry 3 --connect-timeout 15 \
  -o "${checksum_file}" "${K3S_SHA256SUM_AMD64_URL}"
curl -fL --proto '=https' --tlsv1.2 --retry 3 --connect-timeout 15 \
  -o "${install_script}" "${K3S_INSTALL_SH_URL}"
[[ "$(sha256sum "${checksum_file}" | awk '{print $1}')" == "${K3S_SHA256SUM_AMD64_SHA256}" ]] \
  || fail "official K3s checksum file digest mismatch"
[[ "$(sha256sum "${install_script}" | awk '{print $1}')" == "${K3S_INSTALL_SH_SHA256}" ]] \
  || fail "pinned K3s installer digest mismatch"
grep -Fxq "${K3S_AMD64_SHA256}  k3s" "${checksum_file}" \
  || fail "official checksum file does not contain the pinned amd64 binary"

install -d -m 700 /etc/rancher/k3s
install -m 600 "${rendered_config}" /etc/rancher/k3s/config.yaml
install -d -m 700 /var/lib/rancher/k3s/agent/etc/kubelet.conf.d
install -m 600 "${script_dir}/config/10-project-cell-reserves.conf" \
  /var/lib/rancher/k3s/agent/etc/kubelet.conf.d/10-project-cell-reserves.conf
INSTALL_K3S_VERSION="${K3S_VERSION}" \
INSTALL_K3S_SKIP_START=true \
INSTALL_K3S_SKIP_ENABLE=true \
INSTALL_K3S_EXEC=server \
  sh "${install_script}"
[[ "$(k3s --version | head -n 1)" == "k3s version ${K3S_VERSION} "* ]] \
  || fail "installed K3s version does not match the pin"
"${systemctl_bin}" enable --now k3s.service
ready=false
for _attempt in $(seq 1 120); do
  if k3s kubectl get node -o json | python3 -c \
    'import json,sys; p=json.load(sys.stdin); raise SystemExit(0 if any(c.get("type")=="Ready" and c.get("status")=="True" for n in p.get("items",[]) for c in n.get("status",{}).get("conditions",[])) else 1)' \
    >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
[[ "${ready}" == true ]] || fail "K3s node did not become Ready"
if k3s kubectl -n kube-system get deploy,daemonset -o name | grep -Eq '(traefik|svclb)'; then
  fail "Traefik or ServiceLB was deployed despite the disabled configuration"
fi
k3s ctr version >/dev/null 2>&1 || fail "K3s bundled containerd is not reachable"
python3 - "${bind_address}" "${bundle}/evidence/listeners.txt" <<'PY'
import json,subprocess,sys,urllib.request
bind,baseline_listeners=sys.argv[1:]
lines=subprocess.run(["ss","-Hlnpt"],text=True,capture_output=True,check=True).stdout.splitlines()
api=[line for line in lines if any(token.endswith(":6443") for token in line.split())]
if len(api)!=1 or not any(token.startswith(bind+":6443") for token in api[0].split()):
    raise SystemExit("K3s API listener is not exactly private-bound")
def edge(values): return sorted(line for line in values if any(token.endswith((":80",":443")) for token in line.split()))
before=open(baseline_listeners,encoding="utf-8").read().splitlines()
if edge(before)!=edge(lines): raise SystemExit("port 80/443 listener owners changed")
for url in ("http://127.0.0.1:8200/api/health","http://127.0.0.1:8101/health","http://127.0.0.1:8003/health","http://127.0.0.1:3100/web-health"):
    with urllib.request.urlopen(url,timeout=5) as response:
        if response.status!=200: raise SystemExit("production health changed after K3s start")
PY
node_json="$(k3s kubectl get node -o json)"
service_ip="$(k3s kubectl -n default get service kubernetes -o jsonpath='{.spec.clusterIP}')"
node_name="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["items"][0]["metadata"]["name"])' <<<"${node_json}")"
configz="$(k3s kubectl get --raw "/api/v1/nodes/${node_name}/proxy/configz")"
python3 - "${service_ip}" "${node_json}" "${configz}" <<'PY'
import ipaddress,json,sys
service_ip,node_raw,config_raw=sys.argv[1:]
if ipaddress.ip_address(service_ip) not in ipaddress.ip_network("10.43.0.0/16"):
    raise SystemExit("live Kubernetes service CIDR differs from policy")
node=json.loads(node_raw)["items"][0]
pod=ipaddress.ip_network(node["spec"]["podCIDR"])
if not pod.subnet_of(ipaddress.ip_network("10.42.0.0/16")):
    raise SystemExit("live node pod CIDR differs from policy")
kubelet=json.loads(config_raw).get("kubeletconfig",{})
if kubelet.get("systemReserved")!={"cpu":"1500m","memory":"4Gi","ephemeral-storage":"10Gi","pid":"4096"}:
    raise SystemExit("live systemReserved differs from policy")
if kubelet.get("kubeReserved")!={"cpu":"500m","memory":"1Gi","ephemeral-storage":"2Gi","pid":"1000"}:
    raise SystemExit("live kubeReserved differs from policy")
if node.get("status",{}).get("allocatable")==node.get("status",{}).get("capacity"):
    raise SystemExit("NodeAllocatable did not reflect host reservations")
PY
install -d -m 700 "${state_root}"
python3 "${ctl}" installed-manifest --bundle "${bundle}" \
  --output "${state_root}/installed-state.json" >/dev/null
[[ "$("${systemctl_bin}" is-active omnia-project-cell-deadman.timer)" == active ]] \
  || fail "dead-man timer stopped during K3s installation"
echo "phase2 isolated K3s installed; dead-man remains armed"
