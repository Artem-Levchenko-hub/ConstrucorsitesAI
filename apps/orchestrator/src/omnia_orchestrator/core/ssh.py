"""Pinned SSH transport and BYO-VPS preflight.

The first request only resolves the public address and shows the server-key
fingerprint. Credentials are used only after the owner confirms that key.
Every later connection uses a one-host known_hosts file and the resolved IP,
which prevents host-key substitution and DNS rebinding.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import os
import re
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncssh

from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.shell import CmdResult, run

_PREFLIGHT = r"""
set -eu
printf 'os='
(. /etc/os-release 2>/dev/null && \
  printf '%s %s' "${ID:-unknown}" "${VERSION_ID:-}") || printf unknown
printf '\narch='; uname -m
printf '\ndisk_free_kb='; df -Pk "$HOME" | awk 'NR==2 {print $4}'
printf '\nmemory_kb='; awk '/MemTotal/ {print $2}' /proc/meminfo
printf '\ndocker_version='; docker version --format '{{.Server.Version}}' 2>/dev/null || true
printf '\ndocker_ok='; docker info >/dev/null 2>&1 && printf true || printf false
printf '\ncurl_ok='; command -v curl >/dev/null 2>&1 && printf true || printf false
printf '\ngzip_ok='; command -v gzip >/dev/null 2>&1 && printf true || printf false
printf '\nbase64_ok='; command -v base64 >/dev/null 2>&1 && printf true || printf false
printf '\nedge_running='
docker inspect -f '{{.State.Running}}' omnia-edge 2>/dev/null || printf false
printf '\nports='
(ss -ltnH 2>/dev/null || netstat -ltn 2>/dev/null || true) \
  | awk '{print $4}' | tail -80 | paste -sd,
printf '\n'
""".strip()


@dataclass(slots=True)
class SSHSession:
    """Small transport facade used by remote deployment and tests."""

    connection: Any
    known_hosts_path: Path

    async def run(
        self,
        command: str,
        *,
        timeout: float = 30.0,  # noqa: ASYNC109 - per-command deadline
        input_data: bytes | str | None = None,
    ) -> CmdResult:
        try:
            result = await asyncio.wait_for(
                self.connection.run(command, input=input_data, check=False),
                timeout=timeout,
            )
        except TimeoutError:
            return CmdResult(124, "", f"timed out after {timeout}s")
        stdout = result.stdout
        stderr = result.stderr
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        exit_status = result.exit_status if result.exit_status is not None else -1
        return CmdResult(int(exit_status), str(stdout or ""), str(stderr or ""))

    async def close(self) -> None:
        self.connection.close()
        await self.connection.wait_closed()
        self.known_hosts_path.unlink(missing_ok=True)


def _is_allowed_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    blocked = {item.strip() for item in get_settings().byo_blocked_ips.split(",") if item.strip()}
    return address.is_global and str(address) not in blocked


async def resolve_public_host(host: str) -> str:
    """Resolve a target once and reject loopback/private/reserved networks."""
    try:
        infos = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: socket.getaddrinfo(
                host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
            ),
        )
    except socket.gaierror as exc:
        raise ValueError("Не удалось разрешить адрес сервера.") from exc
    addresses = sorted({str(info[4][0]) for info in infos})
    allowed = [address for address in addresses if _is_allowed_ip(address)]
    if not allowed:
        raise ValueError(
            "Нужен публичный IP-адрес. Локальные, внутренние и служебные сети запрещены."
        )
    # Prefer IPv4 because DNS A records and the current domain UI use IPv4.
    return next((item for item in allowed if "." in item), allowed[0])


async def host_key(host: str, port: int) -> str | None:
    """Read a key without authenticating, so first-use confirmation leaks no credential."""
    scan = await run(
        ["ssh-keyscan", "-p", str(port), "-t", "ed25519,ecdsa,rsa", host],
        timeout_seconds=15,
    )
    lines = [
        line.strip()
        for line in (scan.stdout or "").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not lines:
        return None

    # ssh-keyscan probes the requested algorithms concurrently, so its output
    # order is not stable. Persisting the first line made the confirmation step
    # randomly compare (for example) an ECDSA key with an ED25519 key from the
    # same server and report a false host-key change. Select one algorithm by a
    # fixed preference order instead.
    priority = {
        "ssh-ed25519": 0,
        "ecdsa-sha2-nistp256": 1,
        "ssh-rsa": 2,
    }

    def sort_key(line: str) -> tuple[int, str]:
        parts = line.split()
        algorithm = parts[1] if len(parts) >= 3 else ""
        return priority.get(algorithm, 99), line

    return min(lines, key=sort_key)


def host_fingerprint(known_host_line: str) -> str:
    """Return the familiar SHA256 OpenSSH fingerprint."""
    parts = known_host_line.split()
    if len(parts) < 3:
        return "неизвестен"
    try:
        encoded = parts[2] + "=" * (-len(parts[2]) % 4)
        digest = hashlib.sha256(base64.b64decode(encoded)).digest()
    except (ValueError, TypeError):
        return "неизвестен"
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


async def connect(
    *,
    resolved_ip: str,
    port: int,
    user: str,
    auth_type: str,
    secret: str,
    known_host_key: str,
) -> SSHSession:
    """Open an authenticated connection pinned to one exact host key."""
    if not _is_allowed_ip(resolved_ip):
        raise ValueError("Сохранённый адрес VPS больше не является публичным.")
    tmp = tempfile.NamedTemporaryFile(
        mode="w", prefix="omnia-known-host-", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(known_host_key.rstrip() + "\n")
        tmp.close()
        kwargs: dict[str, Any] = {
            "host": resolved_ip,
            "port": port,
            "username": user,
            "known_hosts": tmp.name,
            "login_timeout": 15,
            "keepalive_interval": 15,
            "keepalive_count_max": 3,
        }
        if auth_type == "key":
            kwargs["client_keys"] = [asyncssh.import_private_key(secret)]
            kwargs["password"] = None
        else:
            kwargs["client_keys"] = []
            kwargs["password"] = secret
        connection = await asyncssh.connect(**kwargs)
        return SSHSession(connection=connection, known_hosts_path=Path(tmp.name))
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def _preflight(stdout: str) -> dict[str, object]:
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    docker_ok = values.get("docker_ok") == "true"
    disk_kb = int(values.get("disk_free_kb") or 0)
    memory_kb = int(values.get("memory_kb") or 0)
    required_tools = all(values.get(name) == "true" for name in ("curl_ok", "gzip_ok", "base64_ok"))
    ports = values.get("ports", "")
    web_ports_busy = any(
        re.search(rf"(?:^|[^0-9]){port}$", item) for port in (80, 443) for item in ports.split(",")
    )
    edge_running = values.get("edge_running") == "true"
    ready = (
        docker_ok
        and disk_kb >= 2_000_000
        and memory_kb >= 900_000
        and required_tools
        and (not web_ports_busy or edge_running)
    )
    problems: list[str] = []
    if not docker_ok:
        problems.append("Docker не установлен или пользователь не имеет доступа к нему")
    if disk_kb < 2_000_000:
        problems.append("нужно минимум 2 ГБ свободного места")
    if memory_kb < 900_000:
        problems.append("нужно минимум 1 ГБ оперативной памяти")
    if not required_tools:
        problems.append("нужны curl, gzip и base64")
    if web_ports_busy and not edge_running:
        problems.append("порты 80/443 уже заняты другим веб-сервером")
    return {
        "ok": ready,
        "docker_ok": docker_ok,
        "docker_version": values.get("docker_version") or None,
        "capabilities": {
            "os": values.get("os", "unknown"),
            "arch": values.get("arch", "unknown"),
            "disk_free_mb": disk_kb // 1024,
            "memory_mb": memory_kb // 1024,
            "curl": values.get("curl_ok") == "true",
            "gzip": values.get("gzip_ok") == "true",
            "base64": values.get("base64_ok") == "true",
            "listening_ports": ports,
        },
        "detail": (
            f"Сервер готов: Docker {values.get('docker_version')}, {disk_kb // 1024} МБ свободно."
            if ready
            else "Preflight не пройден: " + "; ".join(problems) + "."
        ),
    }


async def verify_target(
    *,
    host: str,
    port: int,
    user: str,
    auth_type: str,
    secret: str,
    known_host_key: str | None = None,
    resolved_ip: str | None = None,
) -> dict[str, object]:
    """Two-step verify: scan/fingerprint, then pinned authentication + preflight."""
    try:
        current_ip = resolved_ip or await resolve_public_host(host)
        scanned = await host_key(current_ip, port)
    except (ValueError, OSError) as exc:
        return {"ok": False, "detail": str(exc), "docker_ok": False}
    if not scanned:
        return {
            "ok": False,
            "detail": "SSH-сервер не отдал ключ. Проверьте адрес, порт и фаервол.",
            "docker_ok": False,
            "resolved_ip": current_ip,
        }
    fingerprint = host_fingerprint(scanned)
    if known_host_key is None:
        return {
            "ok": False,
            "requires_confirmation": True,
            "detail": f"Сверьте отпечаток сервера и подтвердите доверие: {fingerprint}",
            "docker_ok": False,
            "host_key": scanned,
            "host_fingerprint": fingerprint,
            "resolved_ip": current_ip,
        }
    if scanned.split(maxsplit=1)[-1] != known_host_key.split(maxsplit=1)[-1]:
        return {
            "ok": False,
            "detail": "Ключ сервера изменился. Подключение заблокировано для защиты от подмены.",
            "docker_ok": False,
            "host_key": scanned,
            "host_fingerprint": fingerprint,
            "resolved_ip": current_ip,
        }
    try:
        session = await connect(
            resolved_ip=current_ip,
            port=port,
            user=user,
            auth_type=auth_type,
            secret=secret,
            known_host_key=known_host_key,
        )
        try:
            result = await session.run(_PREFLIGHT, timeout=35)
        finally:
            await session.close()
    except asyncssh.PermissionDenied:
        return {
            "ok": False,
            "detail": "Отказ в доступе — проверьте пользователя, ключ или пароль.",
            "docker_ok": False,
            "host_key": scanned,
            "host_fingerprint": fingerprint,
            "resolved_ip": current_ip,
        }
    except (asyncssh.Error, OSError, ValueError) as exc:
        return {
            "ok": False,
            "detail": f"Не удалось подключиться: {str(exc)[:240]}",
            "docker_ok": False,
            "host_key": scanned,
            "host_fingerprint": fingerprint,
            "resolved_ip": current_ip,
        }
    if result.rc != 0:
        return {
            "ok": False,
            "detail": f"Не удалось выполнить preflight: {result.stderr[-240:]}",
            "docker_ok": False,
            "host_key": scanned,
            "host_fingerprint": fingerprint,
            "resolved_ip": current_ip,
        }
    response = _preflight(result.stdout)
    response.update(
        {
            "host_key": scanned,
            "host_fingerprint": fingerprint,
            "resolved_ip": current_ip,
            "requires_confirmation": False,
        }
    )
    return response
