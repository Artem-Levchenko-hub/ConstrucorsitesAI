"""Blue/green BYO deployment tests with an in-memory SSH facade."""

from __future__ import annotations

import pytest

from omnia_orchestrator.core.shell import CmdResult
from omnia_orchestrator.services import builder, remote_deploy


class FakeSession:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.inputs: list[bytes | str | None] = []
        self.closed = False

    async def run(
        self,
        command: str,
        *,
        timeout: float = 30,  # noqa: ASYNC109 - mirrors production facade
        input_data=None,
    ) -> CmdResult:
        self.commands.append(command)
        self.inputs.append(input_data)
        if "image inspect" in command:
            return CmdResult(0, "amd64\n", "")
        if command == "uname -m":
            return CmdResult(0, "x86_64\n", "")
        if "docker run -d --name omnia-app-" in command:
            return CmdResult(0, "candidate-id\n", "")
        if "docker port" in command:
            return CmdResult(0, "34568\n", "")
        if "docker inspect" in command and "omnia-edge" in command:
            return CmdResult(0, "true\n", "")
        if "docker ps -aq" in command:
            return CmdResult(0, "old-id\ncandidate-id\n", "")
        return CmdResult(0, "", "")

    async def close(self) -> None:
        self.closed = True


def _creds() -> dict[str, object]:
    return {
        "host": "vps.example",
        "resolved_ip": "203.0.113.9",
        "port": 22,
        "user": "root",
        "auth_type": "key",
        "secret": "PRIVATE-KEY",
        "known_host_key": "203.0.113.9 ssh-ed25519 AAAA",
    }


@pytest.mark.asyncio
async def test_blue_green_swaps_after_health_and_keeps_secrets_out_of_commands(
    monkeypatch,
) -> None:
    session = FakeSession()
    connected: dict[str, object] = {}

    async def fake_connect(**kwargs):
        connected.update(kwargs)
        return session

    async def fake_save_load(_tag, _session, _progress=None):
        return True, "loaded"

    async def healthy(_session, _port):
        return True

    monkeypatch.setattr(remote_deploy.ssh, "connect", fake_connect)
    monkeypatch.setattr(remote_deploy, "_save_load", fake_save_load)
    monkeypatch.setattr(remote_deploy, "_health", healthy)

    result = await remote_deploy.deploy_to_target(
        creds=_creds(),
        image_tag="omnia-app-shop:1",
        project_id="11111111-2222-3333-4444-555555555555",
        run_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        slug="shop",
        host_port=34567,
        env={"AUTH_SECRET": "APP-SUPER-SECRET"},
        needs_database=False,
    )
    assert result["ok"] is True
    assert result["url"] == "http://203.0.113.9:34567"
    commands = "\n".join(session.commands)
    assert "APP-SUPER-SECRET" not in commands
    assert "PRIVATE-KEY" not in commands
    candidate_index = next(
        index
        for index, value in enumerate(session.commands)
        if "docker run -d --name omnia-app-" in value
    )
    reload_index = next(
        index for index, value in enumerate(session.commands) if "caddy reload" in value
    )
    old_remove_index = next(
        index for index, value in enumerate(session.commands) if value == "docker rm -f old-id"
    )
    assert candidate_index < reload_index < old_remove_index
    assert connected["known_host_key"] == _creds()["known_host_key"]
    assert session.closed is True


@pytest.mark.asyncio
async def test_failed_candidate_is_removed_without_touching_old(monkeypatch) -> None:
    session = FakeSession()

    async def fake_connect(**_kwargs):
        return session

    async def fake_save_load(_tag, _session, _progress=None):
        return True, "loaded"

    async def unhealthy(_session, _port):
        return False

    monkeypatch.setattr(remote_deploy.ssh, "connect", fake_connect)
    monkeypatch.setattr(remote_deploy, "_save_load", fake_save_load)
    monkeypatch.setattr(remote_deploy, "_health", unhealthy)
    result = await remote_deploy.deploy_to_target(
        creds=_creds(),
        image_tag="img:1",
        project_id="11111111-2222-3333-4444-555555555555",
        run_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        slug="shop",
        host_port=34567,
        needs_database=False,
    )
    assert result["ok"] is False
    commands = "\n".join(session.commands)
    assert "docker rm -f omnia-app-" in commands
    assert "docker rm -f old-id" not in commands
    assert "caddy reload" not in commands


def test_caddy_routes_are_project_scoped() -> None:
    domain = remote_deploy._caddyfile(["a.example.ru", "b.example.ru"], 34568)
    direct = remote_deploy._caddyfile([], 34568, 34567)
    assert "a.example.ru, b.example.ru {" in domain
    assert "reverse_proxy 127.0.0.1:34568" in domain
    assert "http://:34567 {" in direct


def test_remote_port_deterministic_and_in_range() -> None:
    assert builder._remote_port("my-shop") == builder._remote_port("my-shop")
    assert builder._remote_port("my-shop") != builder._remote_port("other-site")
    assert 30000 <= builder._remote_port("my-shop") < 50000
