#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "phase2 evidence capture failed: $1" >&2
  exit 1
}

[[ "$#" -eq 2 ]] || fail "usage: capture-host-evidence.sh OUTPUT_BUNDLE PRODUCTION_HOSTNAME"
bundle="$1"
production_hostname="$2"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ctl="${script_dir}/phase2ctl.py"
if [[ "${PHASE2_TEST_MODE:-0}" != 1 && "${PHASE2_ROLLBACK_CAPTURE:-0}" != 1 ]]; then
  [[ "${PHASE2_EXPECTED_REVISION:-}" =~ ^[0-9a-f]{40}$ ]] || fail "PHASE2_EXPECTED_REVISION is required"
  repo_root="$(git -C "${script_dir}" rev-parse --show-toplevel)"
  python3 "${ctl}" verify-checkout --repo "${repo_root}" \
    --expected-revision "${PHASE2_EXPECTED_REVISION}" >/dev/null
fi

[[ "${bundle}" == /* ]] || fail "OUTPUT_BUNDLE must be absolute"
[[ "${production_hostname}" =~ ^[A-Za-z0-9._-]+$ ]] || fail "invalid production hostname"
[[ ! -e "${bundle}" && ! -L "${bundle}" ]] || fail "output already exists"
if [[ "${PHASE2_TEST_MODE:-0}" != 1 && "$(id -u)" -ne 0 ]]; then
  fail "production evidence capture must run as root"
fi
for command_name in python3 sha256sum stat ip ss systemctl docker curl; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "required command is unavailable: ${command_name}"
done

umask 077
export PHASE2_REQUIRE_ROOT_OWNERSHIP=1
mkdir -p "${bundle}/evidence" "${bundle}/restore"
chmod 700 "${bundle}" "${bundle}/evidence" "${bundle}/restore"
evidence="${bundle}/evidence"
restore="${bundle}/restore"

capture_or_absent() {
  local output="$1"
  local command_name="$2"
  shift 2
  if command -v "${command_name}" >/dev/null 2>&1; then
    if ! "$command_name" "$@" >"${output}" 2>&1; then
      printf 'capture-failed:%s\n' "${command_name}" >"${output}"
    fi
  else
    printf 'absent:%s\n' "${command_name}" >"${output}"
  fi
  chmod 600 "${output}"
}

if command -v dpkg-query >/dev/null 2>&1; then
  dpkg-query -W -f='${Package} ${Version}\n' | LC_ALL=C sort >"${evidence}/package-versions.txt"
else
  printf 'absent:dpkg-query\n' >"${evidence}/package-versions.txt"
fi

{
  printf 'docker=%s\n' "$(docker --version 2>/dev/null || printf absent)"
  printf 'containerd=%s\n' "$(containerd --version 2>/dev/null || printf absent)"
  printf 'runc=%s\n' "$(runc --version 2>/dev/null | head -n 1 || printf absent)"
  printf 'k3s=%s\n' "$(k3s --version 2>/dev/null | head -n 1 || printf absent)"
  printf 'kata=%s\n' "$(kata-runtime --version 2>/dev/null | head -n 1 || printf absent)"
} >"${evidence}/runtime-versions.txt"

python3 - "${evidence}/systemd-state.json" <<'PY'
import json, subprocess, sys
units = ["docker.service", "nginx.service", "omnia-orchestrator.service", "k3s.service"]
state = {}
for unit in units:
    active = subprocess.run(["systemctl", "is-active", unit], text=True, capture_output=True)
    enabled = subprocess.run(["systemctl", "is-enabled", unit], text=True, capture_output=True)
    active_value=active.stdout.strip()
    enabled_value=enabled.stdout.strip()
    if active_value not in {"active","inactive","failed","activating","deactivating","reloading"}:
        raise SystemExit(f"unknown mandatory active state for {unit}: {active_value!r}")
    if enabled_value not in {"enabled","enabled-runtime","disabled","static","masked","masked-runtime","indirect","generated","transient","alias","linked","linked-runtime"}:
        raise SystemExit(f"unknown mandatory enabled state for {unit}: {enabled_value!r}")
    state[unit] = {
        "active": active_value,
        "enabled": enabled_value,
    }
open(sys.argv[1], "w", encoding="utf-8").write(json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n")
PY

sysctl -a >"${evidence}/sysctls.txt" || fail "mandatory sysctl capture failed"
[[ -s "${evidence}/sysctls.txt" ]] || fail "mandatory sysctl capture is empty"
LC_ALL=C sort -o "${evidence}/sysctls.txt" "${evidence}/sysctls.txt"
if [[ -r /proc/modules ]]; then
  awk '{print $1}' /proc/modules | LC_ALL=C sort -u >"${evidence}/modules.txt"
else
  fail "mandatory module capture is unavailable"
fi
[[ -s "${evidence}/modules.txt" ]] || fail "mandatory module capture is empty"
firewall_backend=false
if command -v iptables-save >/dev/null 2>&1 && command -v iptables-restore >/dev/null 2>&1; then
  iptables-save >"${evidence}/firewall-v4.rules" || fail "iptables firewall capture failed"
  iptables-restore --test <"${evidence}/firewall-v4.rules" || fail "iptables firewall snapshot is not restorable"
  firewall_backend=true
else
  printf 'absent:iptables-save+iptables-restore\n' >"${evidence}/firewall-v4.rules"
fi
if command -v ip6tables-save >/dev/null 2>&1 && command -v ip6tables-restore >/dev/null 2>&1; then
  ip6tables-save >"${evidence}/firewall-v6.rules" || fail "ip6tables firewall capture failed"
  ip6tables-restore --test <"${evidence}/firewall-v6.rules" || fail "ip6tables firewall snapshot is not restorable"
else
  printf 'absent:ip6tables-save+ip6tables-restore\n' >"${evidence}/firewall-v6.rules"
fi
if command -v nft >/dev/null 2>&1; then
  nft -s list ruleset >"${evidence}/nftables.rules" || fail "nftables firewall capture failed"
  nft -c -f "${evidence}/nftables.rules" || fail "nftables firewall snapshot is not restorable"
  firewall_backend=true
else
  printf 'absent:nft\n' >"${evidence}/nftables.rules"
fi
[[ "${firewall_backend}" == true ]] || fail "no restorable firewall backend is available"

ip -j route show table all | python3 -c \
  'import json,sys; print(json.dumps(json.load(sys.stdin),sort_keys=True,separators=(",",":")))' \
  >"${evidence}/routes.json"

python3 - "${evidence}/dns.json" <<'PY'
import hashlib, json, os, sys
path = "/etc/resolv.conf"
if os.path.islink(path):
    payload = {"kind": "symlink", "target": os.readlink(path)}
elif os.path.isfile(path):
    data = open(path, "rb").read()
    payload = {"kind": "file", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
else:
    payload = {"kind": "absent"}
open(sys.argv[1], "w", encoding="utf-8").write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
PY

python3 - "${evidence}/cni.json" <<'PY'
import json, os, sys
roots = ["/etc/cni", "/opt/cni", "/var/lib/cni", "/var/lib/rancher/k3s"]
payload = []
for root in roots:
    payload.append({"path": root, "state": "present" if os.path.lexists(root) else "absent"})
open(sys.argv[1], "w", encoding="utf-8").write(json.dumps({"paths": payload}, sort_keys=True, separators=(",", ":")) + "\n")
PY

network_ids="$(docker network ls -q)"
if [[ -n "${network_ids}" ]]; then
  # shellcheck disable=SC2086
  docker network inspect ${network_ids} | python3 -c \
    'import json,sys; data=json.load(sys.stdin); data.sort(key=lambda x:x.get("Id","")); print(json.dumps(data,sort_keys=True,separators=(",",":")))' \
    >"${evidence}/docker-networks.json"
else
  printf '[]\n' >"${evidence}/docker-networks.json"
fi

python3 - "${evidence}/docker-state.json" <<'PY'
import json, subprocess, sys
def lines(*args):
    result=subprocess.run(args,text=True,capture_output=True,check=True)
    return sorted(line for line in result.stdout.splitlines() if line)
payload={
    "containers": lines("docker","ps","-a","--no-trunc","--format","{{.ID}} {{.Names}} {{.State}} {{.Image}}"),
    "volumes": lines("docker","volume","ls","--format","{{.Name}}"),
    "compose_projects": lines("docker","compose","ls","--format","json"),
}
open(sys.argv[1],"w",encoding="utf-8").write(json.dumps(payload,sort_keys=True,separators=(",",":"))+"\n")
PY
ss -Hlnatu | LC_ALL=C sort >"${evidence}/listeners.txt"

python3 - "${evidence}/capacity.json" <<'PY'
import json, os, shutil, sys
mem={}
for line in open("/proc/meminfo",encoding="ascii"):
    key,value=line.split(":",1); mem[key]=int(value.strip().split()[0])*1024
swap_used=mem.get("SwapTotal",0)-mem.get("SwapFree",0)
disk=shutil.disk_usage("/")
vfs=os.statvfs("/")
payload={
    "available_memory_bytes": mem.get("MemAvailable",0),
    "free_disk_bytes": disk.free,
    "inode_used_percent": round((1-vfs.f_favail/vfs.f_files)*100,2) if vfs.f_files else None,
    "swap_used_bytes": swap_used,
}
open(sys.argv[1],"w",encoding="utf-8").write(json.dumps(payload,sort_keys=True,separators=(",",":"))+"\n")
PY

python3 - "${evidence}/production-health.json" <<'PY'
import json, subprocess, sys, urllib.request
endpoints={
    "api":"http://127.0.0.1:8200/api/health",
    "gateway":"http://127.0.0.1:8101/health",
    "orchestrator":"http://127.0.0.1:8003/health",
    "web":"http://127.0.0.1:3100/web-health",
}
result={}
ok=True
for name,url in endpoints.items():
    try:
        with urllib.request.urlopen(url,timeout=5) as response:
            result[name]={"status":response.status,"sha256":__import__("hashlib").sha256(response.read()).hexdigest()}
            ok = ok and response.status == 200
    except Exception as exc:
        result[name]={"error":type(exc).__name__}; ok=False
try:
    revision=subprocess.run(["git","-C","/opt/omnia","rev-parse","HEAD"],text=True,capture_output=True,check=True).stdout.strip()
except Exception:
    revision="unverified"
payload={"status":"passed" if ok else "failed","release_sha":revision,"endpoints":result}
open(sys.argv[1],"w",encoding="utf-8").write(json.dumps(payload,sort_keys=True,separators=(",",":"))+"\n")
PY

if [[ -n "${PHASE2_ACTIVE_OPERATIONS_EVIDENCE:-}" ]]; then
  [[ -f "${PHASE2_ACTIVE_OPERATIONS_EVIDENCE}" && ! -L "${PHASE2_ACTIVE_OPERATIONS_EVIDENCE}" ]] \
    || fail "active-operations evidence must be a regular file"
  cp "${PHASE2_ACTIVE_OPERATIONS_EVIDENCE}" "${evidence}/active-operations.json"
else
  printf '{"status":"unverified","active_operations":null}\n' >"${evidence}/active-operations.json"
fi
if [[ -n "${PHASE2_BACKUP_EVIDENCE:-}" ]]; then
  [[ -f "${PHASE2_BACKUP_EVIDENCE}" && ! -L "${PHASE2_BACKUP_EVIDENCE}" ]] \
    || fail "backup evidence must be a regular file"
  cp "${PHASE2_BACKUP_EVIDENCE}" "${evidence}/backup.json"
else
  printf '{"status":"unverified","offhost":false,"checksum_verified":false,"restore_test":"unverified"}\n' >"${evidence}/backup.json"
fi

python3 - "${evidence}/filesystem-state.json" <<'PY'
import json, os, stat, sys
paths=[
  "/etc/rancher/k3s/config.yaml",
  "/var/lib/rancher/k3s",
  "/etc/systemd/system/k3s.service",
  "/usr/local/bin/k3s",
  "/usr/local/bin/k3s-killall.sh",
  "/usr/local/bin/k3s-uninstall.sh",
]
entries=[]
for path in paths:
    if os.path.islink(path): state="symlink"
    elif os.path.isdir(path): state="directory"
    elif os.path.isfile(path): state="file"
    else: state="absent"
    entry={"path":path,"state":state}
    if state=="symlink": entry["target"]=os.readlink(path)
    entries.append(entry)
open(sys.argv[1],"w",encoding="utf-8").write(json.dumps({"paths":entries},sort_keys=True,separators=(",",":"))+"\n")
PY

python3 - "${evidence}/restore-contract.json" <<'PY'
import hashlib, json, os, stat, sys
roots = [
  "/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf",
  "/etc/docker/daemon.json", "/etc/containerd/config.toml",
  "/etc/sysctl.conf", "/etc/sysctl.d", "/etc/modules", "/etc/modules-load.d",
  "/etc/network/interfaces", "/etc/network/interfaces.d",
  "/etc/systemd/system/docker.service.d", "/etc/systemd/system/nginx.service.d",
  "/etc/systemd/system/omnia-orchestrator.service",
  "/etc/systemd/system/omnia-orchestrator.service.d",
  "/etc/cni", "/opt/cni", "/var/lib/cni",
  "/etc/rancher/k3s/config.yaml", "/var/lib/rancher/k3s",
  "/var/lib/rancher/k3s/agent/etc/kubelet.conf.d/10-project-cell-reserves.conf",
  "/etc/systemd/system/k3s.service", "/etc/systemd/system/k3s.service.env",
  "/usr/local/bin/k3s", "/usr/local/bin/k3s-killall.sh", "/usr/local/bin/k3s-uninstall.sh",
]
entries=[]
def record(path):
    try: info=os.lstat(path)
    except FileNotFoundError:
        entries.append({"path":path,"type":"absent"}); return
    base={"path":path,"uid":info.st_uid,"gid":info.st_gid,"mode":format(stat.S_IMODE(info.st_mode),"04o")}
    if stat.S_ISLNK(info.st_mode): base.update(type="symlink",target=os.readlink(path))
    elif stat.S_ISREG(info.st_mode):
        data=open(path,"rb").read(); base.update(type="file",size=len(data),sha256=hashlib.sha256(data).hexdigest())
    elif stat.S_ISDIR(info.st_mode): base.update(type="directory")
    else: raise SystemExit(f"unsupported restore-contract object: {path}")
    entries.append(base)
for root in roots:
    record(root)
    if os.path.isdir(root) and not os.path.islink(root):
        for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
            dirs.sort(); files.sort()
            for name in dirs+files: record(os.path.join(current,name))
open(sys.argv[1],"w",encoding="utf-8").write(json.dumps({"format":1,"entries":sorted(entries,key=lambda x:x["path"])},sort_keys=True,separators=(",",":"))+"\n")
PY

restore_paths=()
for path in \
  /etc/resolv.conf \
  /etc/hosts \
  /etc/nsswitch.conf \
  /etc/docker/daemon.json \
  /etc/containerd/config.toml \
  /etc/sysctl.conf \
  /etc/sysctl.d \
  /etc/modules \
  /etc/modules-load.d \
  /etc/network/interfaces \
  /etc/network/interfaces.d \
  /etc/systemd/system/docker.service.d \
  /etc/systemd/system/nginx.service.d \
  /etc/systemd/system/omnia-orchestrator.service \
  /etc/systemd/system/omnia-orchestrator.service.d \
  /etc/cni \
  /opt/cni \
  /var/lib/cni; do
  if [[ -e "${path}" || -L "${path}" ]]; then
    restore_paths+=("${path#/}")
  fi
done
if ((${#restore_paths[@]})); then
  tar -C / -cpf "${restore}/host-files.tar" "${restore_paths[@]}"
else
  tar -C / -cpf "${restore}/host-files.tar" --files-from /dev/null
fi
ip route save table all >"${restore}/routes.bin"
cp "${evidence}/firewall-v4.rules" "${restore}/firewall-v4.rules"
cp "${evidence}/firewall-v6.rules" "${restore}/firewall-v6.rules"
cp "${evidence}/nftables.rules" "${restore}/nftables.rules"

chmod 600 "${evidence}/"* "${restore}/"*
python3 "${ctl}" manifest --bundle "${bundle}" --hostname "${production_hostname}" >/dev/null
python3 "${ctl}" verify-bundle --bundle "${bundle}" >/dev/null
echo "phase2 evidence captured: ${bundle}"
