"""Publish bounded platform telemetry through the existing private node exporter."""

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

OUTPUT = Path("/var/lib/prometheus/node-exporter")
API_URL = "http://127.0.0.1:8200/internal/monitoring/metrics"
PUBLIC_HEALTH = "https://yleum.ru/api/health"
BACKUP_ROOT = Path("/var/backups/max-studio")
MAX_API_BYTES = 4 * 1024 * 1024
DEVICE_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,15}")


def command(*args: str) -> str:
    environment = {**os.environ, "LC_ALL": "C"}
    return subprocess.check_output(args, text=True, env=environment, timeout=5)


def external_device() -> str:
    routes = json.loads(command("ip", "-j", "-4", "route", "show", "default"))
    candidates = sorted(
        (route for route in routes if DEVICE_PATTERN.fullmatch(route.get("dev", ""))),
        key=lambda route: route.get("metric", 0),
    )
    return candidates[0]["dev"] if candidates else ""


def external_probe() -> bool:
    try:
        with urllib.request.urlopen(PUBLIC_HEALTH, timeout=7) as response:
            document = json.load(response)
        return document["status"] == "ok" and document["checks"]["generation_worker"] == "ok"
    except (OSError, ValueError, TypeError, KeyError):
        return False


class LocalMetricsOpener(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("unexpected redirect from the private metrics endpoint")


def api_metrics(path: Path) -> bytes:
    token = path.read_text(encoding="ascii").strip()
    if not re.fullmatch("[0-9a-f]{64}", token):
        raise ValueError("invalid monitoring scrape credential")
    request = urllib.request.Request(API_URL, headers={"Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), LocalMetricsOpener())
    with opener.open(request, timeout=5) as response:
        if response.headers.get_content_type() != "text/plain":
            raise ValueError("API did not send a Prometheus text response")
        data = response.read(MAX_API_BYTES + 1)
    if not data.startswith(b"# HELP yleum_") or len(data) > MAX_API_BYTES:
        raise ValueError("invalid API metrics snapshot")
    return data


def backup_samples(root: Path, now: datetime) -> list[str]:
    """Use terminal receipts from the already scheduled, checksum-verifying host backup."""
    records = []
    first_day = now.date() - timedelta(days=31)
    for day in range(32):
        directory = root / (first_day + timedelta(days=day)).isoformat()
        for path in directory.glob("*/HOST-SNAPSHOT.json"):
            if not re.fullmatch(r"[0-9a-f]{32}", path.parent.name):
                continue
            try:
                if path.is_symlink() or path.stat().st_size > 256 * 1024:
                    continue
                receipt = json.loads(path.read_bytes())
                if (receipt["run_id"] != path.parent.name
                    or receipt["scope"] != "host-artifact-snapshot"):
                    continue
                start = datetime.fromisoformat(receipt["started_at_utc"])
                ended = datetime.fromisoformat(receipt["checked_at_utc"])
                if not now - timedelta(days=32) <= start <= ended <= now:
                    continue
                status = receipt["status"]
                if status in ("complete", "failed") and (status == "complete") == (receipt["exit_code"] == 0):
                    records.append((start, ended, status))
            except (OSError, ValueError, KeyError, TypeError):
                continue

    samples = []
    finished = [ended for _, ended, state in records if state == "complete"]
    if finished:
        samples.append(f"yleum_backup_last_verified_timestamp_seconds {max(finished).timestamp():.3f}")
    if not records:
        return samples

    # The host job is scheduled at 03:15 MSK with random delay. Observe an
    # elapsed window only after the first retained receipt and a two-hour grace.
    now_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    origin = min(start.date() for start, _, _ in records)
    for days in (1, 7, 30):
        start = now - timedelta(days=days)
        closed = (
            now_day - timedelta(days=day) + timedelta(hours=2, minutes=15)
            for day in range(days + 1)
        )
        missed = sum(
            start < deadline <= now and deadline.date() > origin
            and not any(
                deadline - timedelta(hours=2) <= began < deadline
                for began, _, _ in records
            )
            for deadline in closed
        )
        failed = sum(start < ended <= now and state == "failed" for _, ended, state in records)
        for result, count in (("failed", failed), ("missed", missed)):
            samples.append(
                f'yleum_backup_window_results{{result="{result}",window="{days * 24}h"}} {count}'
            )
    return samples


def publish(path: Path, content: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
        temp = Path(stream.name)
        try:
            with stream:
                stream.write(content)
            temp.chmod(0o644)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def collect(role: str) -> None:
    device = external_device()
    lines = [f'yleum_external_interface{{device="{device}"}} 1'] if device else []
    if role == "commerce":
        lines.append(
            'yleum_generation_external_probe_success{target_vps="core"} '
            f"{int(external_probe())}"
        )
    lines.extend(backup_samples(BACKUP_ROOT, datetime.now(UTC)))
    publish(OUTPUT / "yleum_host.prom", ("\n".join(lines) + "\n").encode())

    if role == "core":
        path = OUTPUT / "yleum_platform_api.prom"
        try:
            credential = Path(os.environ["CREDENTIALS_DIRECTORY"]) / "api-token"
            publish(path, api_metrics(credential))
        except (OSError, ValueError, KeyError):
            path.unlink(missing_ok=True)
            raise RuntimeError("cannot collect authenticated platform API metrics") from None


def block_io_samples(event: dict) -> bytes | None:
    if event.get("type") != "hist":
        return None
    rows = event["data"]["@latency"]
    samples = []
    for key, values in sorted(rows.items()):
        device, separator, kind = key.partition(",")
        if not separator or not DEVICE_PATTERN.fullmatch(device) or kind not in {"0", "1"}:
            continue
        buckets = {int(bucket["max"]) + 1: int(bucket["count"]) for bucket in values}
        direction = "read" if kind == "0" else "write"
        labels = f'device="{device}",direction="{direction}"'
        total = 0
        for power in range(1, 35):
            upper = 2**power
            total += buckets.get(upper, 0)
            samples.append(
                "yleum_block_io_duration_seconds_bucket"
                f'{{{labels},le="{upper / 1_000_000:.6f}"}} {total}'
            )
        samples.append(
            f'yleum_block_io_duration_seconds_bucket{{{labels},le="+Inf"}} {sum(buckets.values())}'
        )
    return ("\n".join(samples) + "\n").encode()


def collect_block_io(program: Path) -> None:
    destination = OUTPUT / "yleum_block_io.prom"
    destination.unlink(missing_ok=True)
    try:
        with subprocess.Popen(
            ("/usr/bin/bpftrace", "-q", "-f", "json", str(program)),
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        ) as process:
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    if line.strip():
                        snapshot = block_io_samples(json.loads(line))
                        if snapshot is not None:
                            publish(destination, snapshot)
            finally:
                if process.poll() is None:
                    process.terminate()
            raise RuntimeError(f"block I/O tracer stopped with status {process.returncode}")
    finally:
        destination.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "block-io":
        collect_block_io(Path(sys.argv[2]))
    elif len(sys.argv) == 2 and sys.argv[1] in {"core", "runtime", "commerce"}:
        collect(sys.argv[1])
    else:
        raise SystemExit("expected a production host role or block-io PROGRAM")
