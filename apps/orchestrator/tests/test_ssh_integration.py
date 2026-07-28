"""Real AsyncSSH transport tests against an in-process SSH server."""

from __future__ import annotations

import asyncssh
import pytest

from omnia_orchestrator.core import ssh


class _Server(asyncssh.SSHServer):
    def begin_auth(self, _username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    def public_key_auth_supported(self) -> bool:
        return True

    def validate_password(self, _username: str, password: str) -> bool:
        return password == "correct-password"

    def validate_public_key(self, _username: str, _key: asyncssh.SSHKey) -> bool:
        return True


async def _handle_process(process) -> None:
    process.stdout.write("transport-ok\n")
    process.exit(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_type", ["password", "key"])
async def test_pinned_password_and_key_auth(auth_type: str, monkeypatch) -> None:
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server = await asyncssh.create_server(
        _Server,
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        process_factory=_handle_process,
    )
    monkeypatch.setattr(ssh, "_is_allowed_ip", lambda _value: True)
    port = server.get_port()
    public = host_key.export_public_key("openssh").decode().strip()
    known = f"[127.0.0.1]:{port} {public}"
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    secret = (
        "correct-password"
        if auth_type == "password"
        else client_key.export_private_key("openssh").decode()
    )
    try:
        session = await ssh.connect(
            resolved_ip="127.0.0.1",
            port=port,
            user="deploy",
            auth_type=auth_type,
            secret=secret,
            known_host_key=known,
        )
        result = await session.run("probe")
        assert result.ok
        assert result.stdout == "transport-ok\n"
        await session.close()
    finally:
        server.close()
        await server.wait_closed()
