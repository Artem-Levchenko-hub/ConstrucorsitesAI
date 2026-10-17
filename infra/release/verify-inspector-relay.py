#!/usr/bin/env python3
"""Commerce-only public inspector relay. No credentials or upstream body in output."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ASSET = "/api/kit/omnia-inspector.js"
CONFIG = Path("/etc/nginx/conf.d/omnia-inspector-relay.conf")
LOCK = Path("/run/lock/omnia-inspector-relay.lock")
INVENTORY_CONFIG = Path("/var/lib/yleum-generation-readers/config.json")
PROC = Path("/proc")
LIMIT = 1048576


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError(code)


def command(*args: str) -> str:
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=30, check=False
    )
    require(result.returncode == 0, "relay_command_failed")
    return result.stdout


def trusted_origin(release: str) -> str:
    """Resolve installed host authority and the live controller's Settings.

    sudo does not inherit the service environment. Never use caller env as host
    authority, and never expose the controller environment or Settings errors.
    """
    directory = INVENTORY_CONFIG.parent.lstat()
    require(stat.S_ISDIR(directory.st_mode) and directory.st_uid == 0
            and not directory.st_mode & 0o022, "relay_inventory_directory")
    fd = os.open(INVENTORY_CONFIG, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
                and not info.st_mode & 0o022 and 0 < info.st_size <= 16384,
                "relay_inventory_file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            config = json.load(stream)
    finally:
        os.close(fd)
    require(type(config) is dict and type(config.get("version")) is int
            and config["version"] == 1 and config.get("host") == "commerce"
            and config.get("release_sha") == release, "relay_commerce_release")
    processes = set()
    for service in ("yleum-orchestrator", "omnia-orchestrator"):
        try:
            value = command("systemctl", "show", service, "--property=MainPID", "--value").strip()
        except ValueError:
            continue  # An absent alias is allowed; no live controller is not.
        require(value.isdigit(), "relay_controller_pid")
        if int(value) > 0:
            processes.add(int(value))
    require(len(processes) == 1, "relay_controller_unique")
    proc = PROC / str(processes.pop())
    before = (proc / "stat").read_bytes()
    argv = (proc / "cmdline").read_bytes()
    require(b"yleum_orchestrator.main:app" in argv.split(b"\0"), "relay_controller_command")
    cwd = (proc / "cwd").resolve(strict=True)
    require(cwd == ROOT / "apps/orchestrator", "relay_controller_checkout")
    environment = dict(item.decode().split("=", 1)
                       for item in (proc / "environ").read_bytes().split(b"\0") if b"=" in item)
    from yleum_orchestrator.core.config import Settings

    previous = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(environment)
        settings = Settings(_env_file=cwd / ".env")
    finally:
        os.environ.clear()
        os.environ.update(previous)
    # Whole stat includes volatile CPU counters; only start ticks identify PID reuse.
    def start_ticks(value: bytes) -> int:
        return int(value.rsplit(b")", 1)[1].split()[19])

    require(start_ticks(before) == start_ticks((proc / "stat").read_bytes())
            and (proc / "cmdline").read_bytes() == argv
            and (proc / "cwd").resolve(strict=True) == cwd,
            "relay_controller_changed")
    require(settings.omnia_release_sha == release, "relay_controller_release")
    return settings.workspace_origin


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str,
        headers: Any, newurl: str,
    ) -> None:
        return None


def fetch(url: str) -> tuple[int, str, bytes]:
    # Ignore ambient proxy variables; the only HTTPS authority is operator config.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    try:
        response = opener.open(urllib.request.Request(url, method="GET"), timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read(LIMIT + 1)
        require(len(body) <= LIMIT, "relay_response_too_large")
        return response.status, response.headers.get("Content-Type", ""), body


def validate_asset(status: int, mime: str, body: bytes, expected: str) -> None:
    require(status == 200, "relay_asset_status")
    require(
        mime.split(";", 1)[0].strip().lower()
        in {"application/javascript", "text/javascript"},
        "relay_asset_type",
    )
    require(hashlib.sha256(body).hexdigest() == expected, "relay_asset_digest")


def validate_listener(raw: str, *, owned_config: bool, nginx_pids: set[int]) -> None:
    for line in raw.splitlines():
        fields = line.split()
        require(
            len(fields) >= 6
            and fields[0] == "LISTEN"
            and fields[3] == "127.0.0.1:8200",
            "relay_foreign_listener",
        )
        owners = re.findall(r'\("([^"]+)",pid=(\d+),fd=\d+\)', line)
        require(bool(owners) and owned_config, "relay_listener_unowned")
        require(
            all(name == "nginx" and int(pid) in nginx_pids for name, pid in owners),
            "relay_listener_owner",
        )


def config_bytes() -> bytes | None:
    try:
        descriptor = os.open(CONFIG, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        require(
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_uid == 0
            and not metadata.st_mode & 0o022,
            "relay_config_owner",
        )
        data = stream.read(16385)
    require(len(data) <= 16384, "relay_config_size")
    return data


def nginx_identity() -> set[int]:
    main = int(
        command("systemctl", "show", "nginx", "--property=MainPID", "--value").strip()
    )
    require(main > 1, "relay_nginx_inactive")
    expected = Path("/usr/sbin/nginx").resolve(strict=True)
    require(
        Path(f"/proc/{main}/exe").resolve(strict=True) == expected,
        "relay_nginx_executable",
    )
    result = {main}
    for directory in Path("/proc").iterdir():
        if not directory.name.isdecimal():
            continue
        try:
            status = (directory / "status").read_text()
            parent = re.search(r"^PPid:\s+(\d+)$", status, re.MULTILINE)
            if (
                parent
                and int(parent[1]) == main
                and (directory / "exe").resolve(strict=True) == expected
            ):
                result.add(int(directory.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
    return result


def check_owner(expected: bytes) -> None:
    existing = config_bytes()
    require(existing is None or existing == expected, "relay_config_conflict")
    # NGINX -T also rejects an invalid or duplicate configuration. Inspect only
    # port declarations in memory, never print the potentially private dump.
    dump = command("nginx", "-T")
    current = ""
    for line in dump.splitlines():
        if line.startswith("# configuration file ") and line.endswith(":"):
            current = line[len("# configuration file ") : -1]
        if re.match(r"\s*listen\s+[^;]*\b8200\b", line):
            require(
                current == str(CONFIG)
                and existing == expected
                and line.strip() == "listen 127.0.0.1:8200;",
                "relay_port_config_conflict",
            )
    raw = command("ss", "-H", "-ltnp", "sport = :8200")
    validate_listener(
        raw, owned_config=existing == expected, nginx_pids=nginx_identity()
    )


def atomic_config(data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=".omnia-inspector-", dir=CONFIG.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, CONFIG)
        directory = os.open(CONFIG.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def verify(origin: str, release: str, asset: str) -> dict[str, object]:
    status, _mime, body = fetch(origin + "/api/health")
    require(
        status == 200 and json.loads(body).get("release_sha") == release,
        "relay_canonical_release",
    )
    validate_asset(*fetch(origin + ASSET), asset)
    validate_asset(*fetch("http://127.0.0.1:8200" + ASSET), asset)
    require(
        fetch("http://127.0.0.1:8200/api/private")[0] == 404, "relay_unrelated_route"
    )
    return {
        "release_sha": release,
        "asset_sha256": asset,
        "canonical": True,
        "relay": True,
        "unrelated_404": True,
    }


def install(
    expected: bytes, origin: str, release: str, asset: str
) -> dict[str, object]:
    import fcntl

    require(os.geteuid() == 0, "relay_requires_root")
    # Existing canonical deployment owns the outer release lock/drain. This
    # local lock additionally serializes relay installation and ownership checks.
    descriptor = os.open(
        LOCK,
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "rb") as lock:
        metadata = os.fstat(lock.fileno())
        require(
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_uid == 0
            and not metadata.st_mode & 0o077,
            "relay_lock_owner",
        )
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check_owner(expected)
        validate_asset(*fetch(origin + ASSET), asset)
        status, _mime, body = fetch(origin + "/api/health")
        require(
            status == 200 and json.loads(body).get("release_sha") == release,
            "relay_canonical_release",
        )
        created = config_bytes() is None
        if created:
            atomic_config(expected)
        try:
            command("nginx", "-t")
            command("systemctl", "reload", "nginx")
            last_error: Exception | None = None
            for _ in range(10):
                try:
                    check_owner(expected)
                    return verify(origin, release, asset)
                except Exception as error:
                    last_error = error
                    time.sleep(0.5)
            raise ValueError("relay_readiness_failed") from last_error
        except Exception:
            # Remove only bytes created by this call, never an unknown owner's
            # replacement. Existing valid config remains intact on reload error.
            if created and config_bytes() == expected:
                CONFIG.unlink()
                command("nginx", "-t")
                command("systemctl", "reload", "nginx")
            raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--asset-sha256", required=True)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    require(
        bool(re.fullmatch(r"[0-9a-f]{40}", args.expected_sha)), "relay_release_invalid"
    )
    require(
        bool(re.fullmatch(r"[0-9a-f]{64}", args.asset_sha256)), "relay_digest_invalid"
    )
    sys.path.insert(0, str(ROOT / "apps/orchestrator/src"))
    from yleum_orchestrator.services.nginx_writer import _render_inspector_relay

    origin = trusted_origin(args.expected_sha)
    expected = _render_inspector_relay(origin).encode()
    local_asset = ROOT / "apps/api/src/yleum_api/static/omnia-inspector.js"
    require(
        hashlib.sha256(local_asset.read_bytes()).hexdigest() == args.asset_sha256,
        "relay_checkout_asset",
    )
    if args.install:
        result = install(expected, origin, args.expected_sha, args.asset_sha256)
    else:
        check_owner(expected)
        result = verify(origin, args.expected_sha, args.asset_sha256)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never expose URL, certificate diagnostics, command stderr or body.
        print(json.dumps({"ok": False, "error_class": type(error).__name__}))
        raise SystemExit(1) from None
