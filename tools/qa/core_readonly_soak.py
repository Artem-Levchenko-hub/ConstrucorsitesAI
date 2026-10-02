#!/usr/bin/env python3
"""Bounded sampled Core observation. It never certifies full load acceptance."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import urllib.error
import urllib.request

CONTAINERS = ("yleum-prod-api", "yleum-prod-worker", "yleum-prod-generation-worker")
CHECKS = ("generation_worker", "database", "redis", "worker", "deploy_control_plane", "preview_storage")
REVISIONS = {
    "worker": "worker_release_sha",
    "generation_worker": "generation_worker_release_sha",
    "orchestrator": "orchestrator_release_sha",
    "billing_worker": "billing_worker_release_sha",
}
COUNTS = (
    "generation_worker_running", "generation_worker_waiting_capacity",
    "generation_worker_other", "generation_worker_configured_limit",
    "generation_worker_effective_limit",
)
SHA = re.compile(r"^[0-9a-f]{40}$")
BILLING_PREFIX = re.compile(r"^[0-9a-f]{12}$")
STATES = {"ok", "failed", "unknown", "missing", "degraded"}
STOP = False


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def safe_sha(value: object) -> str:
    return value if isinstance(value, str) and SHA.fullmatch(value) else "unknown"


def safe_state(value: object) -> str:
    return value if isinstance(value, str) and value in STATES else "unknown"


def public_health(url: str, timeout: float, expected_revision: str | None = None) -> dict:
    started = time.monotonic()
    try:
        # urllib preserves inherited proxy configuration and verifies TLS.
        request = urllib.request.Request(url, headers={"User-Agent": "Yleum-QA-readonly-observer/1"})
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            code = response.status
            raw = response.read(262145)
        if len(raw) > 262144:
            raise ValueError("oversized health response")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("invalid health object")
        checks = data.get("checks") if isinstance(data.get("checks"), dict) else {}
        dependencies = data.get("dependencies") if isinstance(data.get("dependencies"), dict) else {}
        revision_map = {"api": safe_sha(data.get("release_sha"))}
        revision_map.update({name: safe_sha(dependencies.get(key)) for name, key in REVISIONS.items()})
        reference = safe_sha(expected_revision) if expected_revision is not None else revision_map["api"]
        billing_raw = dependencies.get("billing_worker_release_sha")
        reported = billing_raw if isinstance(billing_raw, str) and (SHA.fullmatch(billing_raw) or BILLING_PREFIX.fullmatch(billing_raw)) else "unknown"
        validation = "unknown"
        if reference != "unknown" and reported != "unknown":
            if SHA.fullmatch(reported):
                validation = "exact_full_sha" if reported == reference else "mismatch"
            elif reported == reference[:12]:
                revision_map["billing_worker"] = reference
                validation = "matching_approved_prefix"
            else:
                validation = "mismatch"
        billing_evidence = {"reported_sha": reported, "reference_full_sha": reference, "validation": validation}
        counts = {}
        for key in COUNTS:
            value = dependencies.get(key)
            if isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value):
                counts[key] = int(value)
            elif type(value) is int and 0 <= value <= 10**12:
                counts[key] = value
        load = dependencies.get("generation_worker_load")
        return {
            "http_status": code,
            "status": safe_state(data.get("status")),
            "checks": {key: safe_state(checks.get(key)) for key in CHECKS},
            "revision_map": revision_map,
            "billing_worker_status": safe_state(dependencies.get("billing_worker")),
            "billing_worker_release_evidence": billing_evidence,
            "queue_counts": counts,
            "generation_worker_load": load if isinstance(load, str) and re.fullmatch(r"[0-9]{1,12}/[0-9]{1,12}", load) else "unknown",
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        }
    except Exception as exc:
        # Exception messages and response bodies may contain infrastructure details.
        return {"error_type": type(exc).__name__, "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}


def memory_bytes(value: str) -> int:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(B|kB|KB|KiB|MB|MiB|GB|GiB|TB|TiB)", value.strip())
    if not match:
        raise ValueError("unrecognized memory unit")
    units = {"B": 1, "kB": 1000, "KB": 1000, "KiB": 1024, "MB": 1000**2, "MiB": 1024**2,
             "GB": 1000**3, "GiB": 1024**3, "TB": 1000**4, "TiB": 1024**4}
    return round(float(match[1]) * units[match[2]])


def docker_stats(timeout: float, sudo: bool) -> dict:
    command = (["sudo", "-n"] if sudo else []) + ["docker", "stats", "--no-stream", "--format", "{{json .}}", *CONTAINERS]
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
        if result.returncode:
            return {"error_type": "CommandFailed", "exit_code": result.returncode}
        rows = {}
        for line in result.stdout.splitlines():
            raw = json.loads(line)
            name = raw.get("Name")
            if name not in CONTAINERS:
                continue
            usage, limit = raw["MemUsage"].split(" / ")
            cpu, memory = float(raw["CPUPerc"].removesuffix("%")), float(raw["MemPerc"].removesuffix("%"))
            pids = int(raw["PIDs"])
            if not all(math.isfinite(value) and value >= 0 for value in (cpu, memory)) or pids < 0:
                raise ValueError("invalid resource metric")
            rows[name] = {"cpu_percent": cpu, "memory_percent": memory,
                          "memory_usage_bytes": memory_bytes(usage), "memory_limit_bytes": memory_bytes(limit), "pids": pids}
        return {"containers": rows, "complete": set(rows) == set(CONTAINERS)}
    except Exception as exc:
        return {"error_type": type(exc).__name__}


def host_resources(disk_path: Path) -> dict:
    try:
        memory = {}
        allowed = {"MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached", "SwapTotal", "SwapFree"}
        for line in Path("/proc/meminfo").read_text().splitlines():
            name, _, value = line.partition(":")
            if name in allowed:
                memory[name + "_bytes"] = int(value.split()[0]) * 1024
        if not {"MemTotal_bytes", "MemAvailable_bytes"}.issubset(memory):
            raise ValueError("missing host memory metrics")
        disk = os.statvfs(disk_path)
        total, free, available = disk.f_blocks * disk.f_frsize, disk.f_bfree * disk.f_frsize, disk.f_bavail * disk.f_frsize
        return {"memory": memory, "disk": {"total_bytes": total, "free_bytes": free, "available_bytes": available,
                                          "used_percent": round(100 * (total - free) / total, 3) if total else None}}
    except Exception as exc:
        return {"error_type": type(exc).__name__}


def db_connection_count(timeout: float) -> dict:
    try:
        # Existing libpq/peer authentication only; no passwords, env inspection, or customer rows.
        command = ["psql", "-X", "-At", "--no-password", "-v", "ON_ERROR_STOP=1", "-c",
                   "BEGIN READ ONLY; SELECT COUNT(*) FROM pg_stat_activity; ROLLBACK;"]
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
        if result.returncode:
            return {"error_type": "CommandFailed", "exit_code": result.returncode}
        counts = [int(line) for line in result.stdout.splitlines() if re.fullmatch(r"[0-9]{1,12}", line)]
        if len(counts) != 1:
            raise ValueError("invalid aggregate result")
        return {"connection_count": counts[0]}
    except Exception as exc:
        return {"error_type": type(exc).__name__}


class Observation:
    def __init__(self, interval: float, expected: str | None = None):
        self.interval, self.expected = interval, expected
        self.revision_map = None
        self.stable_since = None
        self.first_healthy = None
        self.last_healthy = None
        self.samples = self.valid_samples = self.revision_changes = self.window_resets = 0
        self.last_problem = None

    def update(self, sample: dict, now: float) -> None:
        self.samples += 1
        health, resources = sample["health"], sample["resources"]
        revision_map = health.get("revision_map")
        if self.revision_map is not None and revision_map != self.revision_map:
            self.revision_changes += 1
            self.stable_since = None
            self.window_resets += 1
        self.revision_map = revision_map
        shas = list(revision_map.values()) if isinstance(revision_map, dict) else []
        reasons = []
        if health.get("http_status") != 200 or health.get("status") != "ok":
            reasons.append("public_health_failed")
        if set(health.get("checks", {})) != set(CHECKS) or any(v != "ok" for v in health.get("checks", {}).values()):
            reasons.append("readiness_checks_failed_or_missing")
        if len(shas) != 5 or any(not SHA.fullmatch(value) for value in shas) or len(set(shas)) != 1:
            reasons.append("revision_map_unknown_or_mixed")
        if self.expected and any(value != self.expected for value in shas):
            reasons.append("unexpected_revision")
        if health.get("billing_worker_status") != "ok":
            reasons.append("billing_heartbeat_missing")
        if "billing_worker_release_evidence" in health and health["billing_worker_release_evidence"].get("validation") not in {"exact_full_sha", "matching_approved_prefix"}:
            reasons.append("billing_revision_unvalidated")
        if not resources.get("docker", {}).get("complete") or "error_type" in resources.get("host", {}):
            reasons.append("resource_metrics_incomplete")
        if "database" in sample and "error_type" in sample["database"]:
            reasons.append("optional_db_metrics_failed")
        if self.last_healthy is not None and now - self.last_healthy > self.interval + 35:
            reasons.append("sample_gap")
        sample["valid_observation"] = not reasons
        sample["problems"] = reasons
        if reasons:
            if self.stable_since is not None:
                self.window_resets += 1
            self.stable_since = None
            self.last_healthy = None
            self.last_problem = reasons
            return
        self.valid_samples += 1
        self.first_healthy = now if self.first_healthy is None else self.first_healthy
        self.stable_since = now if self.stable_since is None else self.stable_since
        self.last_healthy = now
        self.last_problem = None

    def status(self, now: float) -> dict:
        age = max(0, now - self.stable_since) if self.stable_since is not None else 0
        # Measure through actual resource-bearing samples; idle wall time cannot create PASS.
        sampled_span = max(0, self.last_healthy - self.stable_since) if self.last_healthy is not None and self.stable_since is not None else 0
        return {"sample_count": self.samples, "valid_sample_count": self.valid_samples,
                "revision_map": self.revision_map, "revision_change_count": self.revision_changes,
                "window_reset_count": self.window_resets, "stable_window_seconds": round(age, 3),
                "stable_resource_sample_span_seconds": round(sampled_span, 3),
                "sampled_24h_same_revision_observed": sampled_span >= 86400,
                "full_load_acceptance": False, "last_problem": self.last_problem}


def private_write(path: Path, data: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def collect_sample(deadline: float, args, started: float) -> dict:
    def budget(maximum: float) -> float:
        return max(0, min(maximum, deadline - time.monotonic()))
    sample = {"at_utc": utc_now(), "elapsed_seconds": round(time.monotonic() - started, 3)}
    sample["health"] = public_health("https://yleum.ru/api/health", max(.05, budget(10)), args.expected_revision)
    sample["resources"] = {
        "docker": docker_stats(budget(15), args.sudo_docker) if budget(15) > 0 else {"error_type": "BudgetExceeded"},
        "host": host_resources(args.output_dir),
    }
    if args.db_connection_count:
        sample["database"] = db_connection_count(budget(5)) if budget(5) > 0 else {"error_type": "BudgetExceeded"}
    sample["completed_utc"] = utc_now()
    return sample


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("/opt/omnia-runtime/qa-observe-20261001"))
    parser.add_argument("--duration-seconds", type=int, default=86400)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--expected-revision")
    parser.add_argument("--sudo-docker", action="store_true")
    parser.add_argument("--db-connection-count", action="store_true", help="Optional aggregate using existing local libpq authentication")
    parser.add_argument("--run", action="store_true", help="Explicitly start the observer; absent means print safe plan only")
    args = parser.parse_args()
    if not 1 <= args.duration_seconds <= 86400 or not 30 <= args.interval_seconds <= 3600:
        parser.error("duration must be 1..86400; interval must be 30..3600")
    if args.expected_revision and not SHA.fullmatch(args.expected_revision):
        parser.error("expected revision must be 40 lower-case hexadecimal characters")
    if not args.output_dir.name.startswith("qa-observe-"):
        parser.error("output directory must have an owned qa-observe- prefix")
    if not args.run:
        print(json.dumps({"started": False, "duration_seconds": args.duration_seconds, "interval_seconds": args.interval_seconds,
                          "maximum_total_seconds": args.duration_seconds + 60,
                          "health_url": "https://yleum.ru/api/health", "containers": CONTAINERS,
                          "database_aggregate": args.db_connection_count, "full_load_acceptance": False}))
        return 0
    os.umask(0o077)
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.output_dir.is_symlink():
        parser.error("output directory must not be a symbolic link")
    os.chmod(args.output_dir, 0o700)
    lock_fd = os.open(args.output_dir / "observer.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.fchmod(lock_fd, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("another observer holds this output directory")
    samples_path = args.output_dir / "samples.jsonl"
    descriptor = os.open(samples_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    observation = Observation(args.interval_seconds, args.expected_revision)
    started = time.monotonic()
    deadline = started + args.duration_seconds
    hard_deadline = deadline + 60
    next_sample = started
    first_resource_at = None
    final_sample_at = None
    final_attempted = False
    metadata = {"started_utc": utc_now(), "observer_pid": os.getpid(), "duration_seconds": args.duration_seconds,
                "interval_seconds": args.interval_seconds, "expected_revision": args.expected_revision,
                "maximum_total_seconds": args.duration_seconds + 60,
                "scope": "Т12.4 partial: sampled readiness/resource observation; no generated load or full acceptance"}
    def stop_requested(_signum, _frame):
        global STOP
        STOP = True
    signal.signal(signal.SIGTERM, stop_requested)
    signal.signal(signal.SIGINT, stop_requested)
    with os.fdopen(descriptor, "a", buffering=1) as stream:
        while not STOP and time.monotonic() < hard_deadline:
            now = time.monotonic()
            final_due = final_sample_at is not None and now >= final_sample_at
            regular_due = now < deadline and now >= next_sample
            if not final_due and not regular_due:
                wake_times = [hard_deadline]
                if now < deadline:
                    wake_times.append(min(next_sample, deadline))
                if final_sample_at is not None and final_sample_at < hard_deadline:
                    wake_times.append(final_sample_at)
                elif now >= deadline:
                    break  # No timely resource baseline: cannot fit a full final interval.
                time.sleep(min(1, max(0, min(wake_times) - now)))
                continue
            sample = collect_sample(hard_deadline, args, started)
            now = time.monotonic()
            if first_resource_at is None and sample["resources"]["docker"].get("complete") and "error_type" not in sample["resources"]["host"]:
                first_resource_at = now
                final_sample_at = now + args.duration_seconds
                metadata["first_resource_sample_utc"] = sample["completed_utc"]
                metadata["first_resource_elapsed_seconds"] = round(now - started, 3)
            sample["final_sample"] = final_due
            observation.update(sample, now)
            stream.write(json.dumps(sample, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            private_write(args.output_dir / "status.json", {**metadata, "state": "observing", "updated_utc": utc_now(),
                          "elapsed_seconds": round(now - started, 3), **observation.status(now)})
            if final_due:
                final_attempted = True
                break
            next_sample += args.interval_seconds
            if next_sample < now:
                next_sample = now + args.interval_seconds
        now = time.monotonic()
        private_write(args.output_dir / "status.json", {**metadata, "state": "stopped" if STOP else "window_finished",
                      "final_sample_attempted": final_attempted,
                      "final_sample_missing_reason": None if final_attempted else "interrupted_or_resource_baseline_outside_bounded_tail",
                      "updated_utc": utc_now(), "elapsed_seconds": round(now - started, 3), **observation.status(now)})
    os.close(lock_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
