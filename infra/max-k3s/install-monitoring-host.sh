#!/usr/bin/env bash
# Install the host collector on an existing production node with a private node_exporter.
set -euo pipefail
[[ $# = 1 && "$1" =~ ^(core|runtime|commerce)$ ]] || { echo "usage: $0 HOST_ROLE" >&2; exit 2; }
[[ $EUID = 0 && "$(hostname -s)" = "$1" ]] || { echo "run as root on the selected host" >&2; exit 2; }
role="$1"
source_dir="$(cd "$(dirname "$0")" && pwd -P)"
collector=/usr/local/lib/yleum/monitoring-host.py
credentials=/etc/yleum/monitoring
textfiles=/var/lib/prometheus/node-exporter

install -d -m 0755 "$(dirname "$collector")" "$textfiles"
install -m 0644 "$source_dir/monitoring-host.py" "$collector"
if [[ "$role" = core ]]; then
  install -d -m 0700 "$credentials"
  umask 077
  python3 - "$credentials/api-token" <<'PY'
import os, re, sys, tempfile
from pathlib import Path

output = Path(sys.argv[1])
env = Path("/opt/omnia/apps/llm-gateway/deploy/full/.env")
values = [line.partition("=")[2] for line in env.read_text().splitlines()
          if line.startswith("MONITORING_SCRAPE_TOKEN=")]
if len(values) != 1 or not re.fullmatch(r"[0-9a-f]{64}", values[0]):
    raise SystemExit("stage one API scrape token in the production compose env")
with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as stream:
    path = Path(stream.name)
    try:
        stream.write(values[0].encode("ascii"))
        stream.close()
        os.replace(path, output)
    finally:
        path.unlink(missing_ok=True)
PY
  umask 022
fi

if python3 - "$textfiles" <<'PY'
import os, re, stat, sys, tempfile
from pathlib import Path

config = Path("/etc/default/prometheus-node-exporter")
content = config.read_text()
match = re.search(r'^ARGS="([^"\n]*)"$', content, re.MULTILINE)
if match is None:
    raise SystemExit("expected one double-quoted node_exporter ARGS assignment")
args = match[1]
if re.search(r"(?:^| )--web\.listen-address=10\.10\.0\.[123]:9100(?: |$)", args) is None:
    raise SystemExit("expected node_exporter to listen on its private WireGuard address")
flag = "--collector.textfile.directory=" + sys.argv[1]
if flag in args.split():
    raise SystemExit(3)
if "--collector.textfile.directory" in args:
    raise SystemExit("node_exporter already uses a different textfile directory")
metadata = config.stat()
with tempfile.NamedTemporaryFile(dir=config.parent, mode="w", delete=False) as stream:
    path = Path(stream.name)
    try:
        stream.write(content[:match.end(1)] + " " + flag + content[match.end(1):])
        os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
        os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
        stream.close()
        os.replace(path, config)
    finally:
        path.unlink(missing_ok=True)
PY
then
  systemctl restart prometheus-node-exporter.service
else
  status="$?"
  [[ "$status" = 3 ]] || exit "$status"
fi

cat > /etc/systemd/system/yleum-monitoring-host.service <<EOF
[Unit]
Description=Yleum host telemetry for private Prometheus

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $collector $role
TimeoutStartSec=25s
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=$textfiles
EOF
if [[ "$role" = core ]]; then
  printf 'LoadCredential=api-token:%s/api-token\n' "$credentials" >> /etc/systemd/system/yleum-monitoring-host.service
fi
cat > /etc/systemd/system/yleum-monitoring-host.timer <<'EOF'
[Unit]
Description=Refresh Yleum host telemetry every 30 seconds

[Timer]
OnBootSec=20s
OnUnitActiveSec=30s
AccuracySec=1s

[Install]
WantedBy=timers.target
EOF
systemd-analyze verify /etc/systemd/system/yleum-monitoring-host.service /etc/systemd/system/yleum-monitoring-host.timer
systemctl daemon-reload
systemctl enable --now yleum-monitoring-host.timer
systemctl start yleum-monitoring-host.service
