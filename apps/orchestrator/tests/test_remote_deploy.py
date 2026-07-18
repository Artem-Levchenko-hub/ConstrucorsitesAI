"""Юнит-тесты удалённого деплоя (BYO-VPS) с замоканным ssh/переносом — без
реального сервера. Проверяют конструирование docker-команд, что секрет не в
argv, ветки результата и детерминизм порта."""

from __future__ import annotations

import pytest

from omnia_orchestrator.core.shell import CmdResult
from omnia_orchestrator.services import builder, remote_deploy


def _fake_prefix(**kw):
    # Имитируем ssh.ssh_prefix: префикс без секрета, без temp-файла.
    argv = ["ssh", "-i", "/tmp/k", f"{kw['user']}@{kw['host']}"]
    return argv, None, (lambda: None)


@pytest.mark.asyncio
async def test_deploy_builds_correct_docker_run(monkeypatch) -> None:
    calls: list[list[str]] = []

    async def fake_save_load(image_tag, prefix, env):
        return True, "Loaded image: omnia-app-shop:1"

    async def fake_run(cmd, *, timeout=30.0, env=None):
        calls.append(cmd)
        if "curl" in cmd[-1]:
            return CmdResult(rc=0, stdout="200", stderr="")
        return CmdResult(rc=0, stdout="", stderr="")

    monkeypatch.setattr(remote_deploy.ssh, "ssh_prefix", _fake_prefix)
    monkeypatch.setattr(remote_deploy, "_save_load", fake_save_load)
    monkeypatch.setattr(remote_deploy, "run", fake_run)

    res = await remote_deploy.deploy_to_target(
        creds={"host": "203.0.113.9", "port": 22, "user": "root",
               "auth_type": "key", "secret": "PRIVKEYBODY"},
        image_tag="omnia-app-shop:1", slug="shop", host_port=34567,
        env={"NODE_ENV": "production", "PORT": "3000"},
    )
    assert res["ok"] is True
    assert res["url"] == "http://203.0.113.9:34567"
    runcmd = next(c[-1] for c in calls if c[-1].startswith("docker run"))
    assert "--name omnia-app-shop" in runcmd
    assert "-p 34567:3000" in runcmd
    assert "-e NODE_ENV='production'" in runcmd
    assert "omnia-app-shop:1" in runcmd
    # Секрет не должен фигурировать ни в одной команде.
    assert all("PRIVKEYBODY" not in " ".join(c) for c in calls)


@pytest.mark.asyncio
async def test_deploy_transfer_failure_returns_error(monkeypatch) -> None:
    async def fake_save_load(image_tag, prefix, env):
        return False, "docker load на сервере упал: no space left"

    monkeypatch.setattr(remote_deploy.ssh, "ssh_prefix", _fake_prefix)
    monkeypatch.setattr(remote_deploy, "_save_load", fake_save_load)

    res = await remote_deploy.deploy_to_target(
        creds={"host": "h", "port": 22, "user": "u", "auth_type": "key", "secret": "k"},
        image_tag="img:1", slug="x", host_port=30000,
    )
    assert res["ok"] is False
    assert "Перенос образа не удался" in res["detail"]


@pytest.mark.asyncio
async def test_deploy_container_warming_still_ok(monkeypatch) -> None:
    async def fake_save_load(image_tag, prefix, env):
        return True, "Loaded image: img:1"

    async def fake_run(cmd, *, timeout=30.0, env=None):
        last = cmd[-1]
        if "curl" in last:
            return CmdResult(rc=0, stdout="000", stderr="")  # ещё не отвечает
        if "docker ps" in last:
            return CmdResult(rc=0, stdout="Up 2 seconds", stderr="")
        return CmdResult(rc=0, stdout="", stderr="")

    monkeypatch.setattr(remote_deploy.ssh, "ssh_prefix", _fake_prefix)
    monkeypatch.setattr(remote_deploy, "_save_load", fake_save_load)
    monkeypatch.setattr(remote_deploy, "run", fake_run)

    res = await remote_deploy.deploy_to_target(
        creds={"host": "h", "port": 22, "user": "u", "auth_type": "key", "secret": "k"},
        image_tag="img:1", slug="x", host_port=30000,
    )
    assert res["ok"] is True
    assert "прогревается" in res["detail"]


def test_remote_port_deterministic_and_in_range() -> None:
    p1 = builder._remote_port("my-shop")
    p2 = builder._remote_port("my-shop")
    p3 = builder._remote_port("other-site")
    assert p1 == p2  # детерминизм
    assert p1 != p3  # разные проекты — разные порты (обычно)
    assert 30000 <= p1 < 50000
    assert 30000 <= p3 < 50000
