"""Security and preflight tests for the two-step BYO SSH handshake."""

from __future__ import annotations

import pytest

from omnia_orchestrator.core import ssh
from omnia_orchestrator.core.shell import CmdResult

HOST_KEY = "203.0.113.9 ssh-ed25519 QUJDREVGR0g="


def test_preflight_ready() -> None:
    result = ssh._preflight(
        "\n".join(
            [
                "os=ubuntu 24.04",
                "arch=x86_64",
                "disk_free_kb=5000000",
                "memory_kb=4000000",
                "docker_version=27.1.1",
                "docker_ok=true",
                "curl_ok=true",
                "gzip_ok=true",
                "base64_ok=true",
                "ports=0.0.0.0:22",
            ]
        )
    )
    assert result["ok"] is True
    assert result["docker_version"] == "27.1.1"
    assert result["capabilities"]["arch"] == "x86_64"  # type: ignore[index]


def test_preflight_rejects_small_host() -> None:
    result = ssh._preflight(
        "disk_free_kb=100\nmemory_kb=100\ndocker_ok=false\n"
        "curl_ok=false\ngzip_ok=false\nbase64_ok=false\n"
    )
    assert result["ok"] is False
    assert "Preflight не пройден" in str(result["detail"])


def test_preflight_rejects_foreign_web_server() -> None:
    result = ssh._preflight(
        "disk_free_kb=5000000\nmemory_kb=4000000\ndocker_ok=true\n"
        "curl_ok=true\ngzip_ok=true\nbase64_ok=true\nedge_running=false\n"
        "ports=0.0.0.0:22,0.0.0.0:80,[::]:443\n"
    )
    assert result["ok"] is False
    assert "80/443" in str(result["detail"])


@pytest.mark.asyncio
async def test_first_verify_only_returns_fingerprint(monkeypatch) -> None:
    used_secret = False

    async def fake_resolve(_host: str) -> str:
        return "203.0.113.9"

    async def fake_key(_host: str, _port: int) -> str:
        return HOST_KEY

    async def forbidden_connect(**_kwargs):
        nonlocal used_secret
        used_secret = True
        raise AssertionError("credentials must not be used before confirmation")

    monkeypatch.setattr(ssh, "resolve_public_host", fake_resolve)
    monkeypatch.setattr(ssh, "host_key", fake_key)
    monkeypatch.setattr(ssh, "connect", forbidden_connect)
    result = await ssh.verify_target(
        host="vps.example",
        port=22,
        user="root",
        auth_type="password",
        secret="super-secret",
    )
    assert result["requires_confirmation"] is True
    assert result["resolved_ip"] == "203.0.113.9"
    assert str(result["host_fingerprint"]).startswith("SHA256:")
    assert used_secret is False


@pytest.mark.asyncio
async def test_confirmed_verify_is_pinned_and_runs_preflight(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Session:
        async def run(self, _command: str, **_kwargs) -> CmdResult:
            return CmdResult(
                0,
                "os=ubuntu 24.04\narch=x86_64\ndisk_free_kb=5000000\n"
                "memory_kb=4000000\ndocker_version=27.1\ndocker_ok=true\n"
                "curl_ok=true\ngzip_ok=true\nbase64_ok=true\nports=:22\n",
                "",
            )

        async def close(self) -> None:
            return None

    async def fake_key(_host: str, _port: int) -> str:
        return HOST_KEY

    async def fake_connect(**kwargs):
        captured.update(kwargs)
        return Session()

    monkeypatch.setattr(ssh, "host_key", fake_key)
    monkeypatch.setattr(ssh, "connect", fake_connect)
    result = await ssh.verify_target(
        host="vps.example",
        port=2222,
        user="deploy",
        auth_type="password",
        secret="super-secret",
        known_host_key=HOST_KEY,
        resolved_ip="203.0.113.9",
    )
    assert result["ok"] is True
    assert captured["known_host_key"] == HOST_KEY
    assert captured["resolved_ip"] == "203.0.113.9"


@pytest.mark.asyncio
async def test_changed_host_key_blocks_authentication(monkeypatch) -> None:
    connected = False

    async def changed(_host: str, _port: int) -> str:
        return HOST_KEY.replace("QUJDREVGR0g=", "SElKS0xNTk8=")

    async def forbidden(**_kwargs):
        nonlocal connected
        connected = True

    monkeypatch.setattr(ssh, "host_key", changed)
    monkeypatch.setattr(ssh, "connect", forbidden)
    result = await ssh.verify_target(
        host="vps.example",
        port=22,
        user="root",
        auth_type="key",
        secret="private",
        known_host_key=HOST_KEY,
        resolved_ip="203.0.113.9",
    )
    assert result["ok"] is False
    assert "изменился" in str(result["detail"])
    assert connected is False


@pytest.mark.asyncio
async def test_private_target_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 0))],
    )
    with pytest.raises(ValueError, match="публичный"):
        await ssh.resolve_public_host("localhost")
