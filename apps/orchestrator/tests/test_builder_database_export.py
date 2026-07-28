"""Database snapshot transport for BYO-VPS deployments."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from omnia_orchestrator.services import builder, remote_deploy


@pytest.mark.asyncio
async def test_export_uses_matching_postgres_container_without_password_in_argv(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"CREATE TABLE canary();", b""

    async def fake_subprocess(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(
        builder,
        "_resolve_runtime_dsn",
        lambda _project_id: (
            "postgresql://project_user:project-secret@127.0.0.1:5432/omnia_users"
            "?options=-c%20search_path%3Dproj_1234"
        ),
    )
    monkeypatch.setattr(
        builder,
        "get_settings",
        lambda: SimpleNamespace(
            database_url="postgresql+asyncpg://admin:admin-secret@127.0.0.1:5432/omnia"
        ),
    )
    monkeypatch.setattr(builder.asyncio, "create_subprocess_exec", fake_subprocess)

    dump, schema = await builder._export_project_database("project-id")

    args = captured["args"]
    kwargs = captured["kwargs"]
    assert isinstance(args, tuple)
    assert isinstance(kwargs, dict)
    assert args[:5] == ("docker", "run", "--rm", "--network", "host")
    assert remote_deploy.POSTGRES_IMAGE in args
    assert "pg_dump" in args
    assert "project-secret" not in args
    assert kwargs["env"]["PGPASSWORD"] == "project-secret"
    assert schema == "proj_1234"
    assert dump == "CREATE TABLE canary();"
