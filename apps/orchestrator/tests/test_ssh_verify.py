"""Юнит-тесты SSH-проверки чужого VPS (BYO-VPS) с замоканным shell.run —
без реального сервера. Проверяют разбор ответа и что секреты не идут в argv.
"""

from __future__ import annotations

import pytest

from omnia_orchestrator.core import ssh
from omnia_orchestrator.core.shell import CmdResult


def test_interpret_docker_ok() -> None:
    r = ssh._interpret(0, "27.1.1\n", "")
    assert r["ok"] is True
    assert r["docker_ok"] is True
    assert r["docker_version"] == "27.1.1"


def test_interpret_connected_no_docker() -> None:
    r = ssh._interpret(0, "NO_DOCKER\n", "")
    assert r["ok"] is True
    assert r["docker_ok"] is False
    assert "нет Docker" in r["detail"]  # type: ignore[operator]


def test_interpret_permission_denied() -> None:
    r = ssh._interpret(255, "", "Permission denied (publickey,password).")
    assert r["ok"] is False
    assert "доступ" in r["detail"].lower()  # type: ignore[union-attr]


def test_interpret_unresolved_host() -> None:
    r = ssh._interpret(255, "", "ssh: Could not resolve hostname foo: Name or service not known")
    assert r["ok"] is False
    assert "host" in r["detail"].lower()  # type: ignore[operator]


@pytest.mark.asyncio
async def test_verify_key_mode_does_not_leak_secret_in_argv(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run(cmd, *, timeout=30.0, env=None):
        # ssh-keyscan → пусто; собственно ssh → успех с версией докера.
        if cmd and cmd[0] == "ssh-keyscan":
            return CmdResult(rc=0, stdout="host ssh-ed25519 AAAA\n", stderr="")
        captured["cmd"] = cmd
        captured["env"] = env
        return CmdResult(rc=0, stdout="27.0.0\n", stderr="")

    monkeypatch.setattr(ssh, "run", fake_run)
    private_key = "-----BEGIN OPENSSH PRIVATE KEY-----\nSECRETKEYBODY\n-----END OPENSSH PRIVATE KEY-----"
    result = await ssh.verify_target(
        host="1.2.3.4", port=22, user="root", auth_type="key", secret=private_key
    )
    assert result["ok"] is True
    assert result["docker_version"] == "27.0.0"
    assert result["host_key"] == "host ssh-ed25519 AAAA"
    # Секрет НЕ должен быть ни одним из аргументов ssh (он во временном файле).
    assert all("SECRETKEYBODY" not in str(a) for a in captured["cmd"])  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_verify_password_mode_uses_env_not_argv(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run(cmd, *, timeout=30.0, env=None):
        if cmd and cmd[0] == "ssh-keyscan":
            return CmdResult(rc=0, stdout="", stderr="")
        captured["cmd"] = cmd
        captured["env"] = env
        return CmdResult(rc=0, stdout="26.1.4\n", stderr="")

    monkeypatch.setattr(ssh, "run", fake_run)
    result = await ssh.verify_target(
        host="1.2.3.4", port=2222, user="deploy", auth_type="password", secret="hunter2"
    )
    assert result["ok"] is True
    # Пароль передан в env SSHPASS, а не в argv.
    assert captured["env"]["SSHPASS"] == "hunter2"  # type: ignore[index]
    assert all("hunter2" not in str(a) for a in captured["cmd"])  # type: ignore[union-attr]
    assert "sshpass" in captured["cmd"][0]  # type: ignore[index]


@pytest.mark.asyncio
async def test_verify_password_mode_missing_sshpass(monkeypatch) -> None:
    async def fake_run(cmd, *, timeout=30.0, env=None):
        if cmd and cmd[0] == "ssh-keyscan":
            return CmdResult(rc=0, stdout="", stderr="")
        raise FileNotFoundError("sshpass")

    monkeypatch.setattr(ssh, "run", fake_run)
    result = await ssh.verify_target(
        host="h", port=22, user="u", auth_type="password", secret="p"
    )
    assert result["ok"] is False
    assert "sshpass" in result["detail"]  # type: ignore[operator]
