from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy.engine import make_url

API_ROOT = Path(__file__).resolve().parents[1]
_NAME_RE = re.compile(r"omnia_cell_fence_[0-9a-f]{32}")


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _create_database(admin_dsn: str, name: str) -> None:
    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute(f'CREATE DATABASE "{name}"')
    finally:
        await connection.close()


async def _drop_database(admin_dsn: str, name: str) -> None:
    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            name,
        )
        await connection.execute(f'DROP DATABASE "{name}"')
    finally:
        await connection.close()


def _run_alembic(database_url: str, *args: str) -> None:
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    env["DATABASE_TEST_URL"] = database_url
    subprocess.run(
        ["uv", "run", "alembic", *args],
        cwd=API_ROOT,
        env=env,
        check=True,
        shell=False,
    )


async def _seed_running_0052(database_url: str) -> tuple[UUID, UUID, dict[str, object]]:
    connection = await asyncpg.connect(_asyncpg_dsn(database_url))
    owner_id = uuid4()
    project_id = uuid4()
    workspace_id = uuid4()
    operation_id = uuid4()
    request: dict[str, object] = {"profile_version": "docker-owner-cell-resources-v1"}
    canonical_request = json.dumps(
        request,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    try:
        await connection.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            owner_id,
            f"migration-{owner_id.hex}@example.com",
            "x",
        )
        await connection.execute(
            """
            INSERT INTO projects (id, owner_id, name, slug, template)
            VALUES ($1, $2, $3, $4, 'blank')
            """,
            project_id,
            owner_id,
            "Migration fixture",
            f"migration-{project_id.hex}",
        )
        await connection.execute(
            """
            INSERT INTO project_cell_workspaces
                (id, project_id, owner_id, provider, state, fencing_epoch, version)
            VALUES ($1, $2, $3, 'docker_owner_canary', 'provisioning', 7, 3)
            """,
            workspace_id,
            project_id,
            owner_id,
        )
        await connection.execute(
            """
            INSERT INTO project_cell_operations
                (id, workspace_id, idempotency_key, request_digest, kind, status,
                 request_payload, started_at)
            VALUES ($1, $2, $3, $4, 'ensure', 'running', $5::jsonb, now())
            """,
            operation_id,
            workspace_id,
            "migration:running",
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            canonical_request,
        )
    finally:
        await connection.close()
    return workspace_id, operation_id, request


async def _read_operation(
    database_url: str,
    operation_id: UUID,
) -> tuple[int | None, dict[str, object], str]:
    connection = await asyncpg.connect(_asyncpg_dsn(database_url))
    try:
        row = await connection.fetchrow(
            """
            SELECT fencing_epoch, request_payload, request_digest
            FROM project_cell_operations
            WHERE id = $1
            """,
            operation_id,
        )
        assert row is not None
        return row["fencing_epoch"], json.loads(row["request_payload"]), row["request_digest"]
    finally:
        await connection.close()


@pytest.fixture
def disposable_migration_database() -> Iterator[str]:
    configured_url = os.environ.get("DATABASE_TEST_URL") or os.environ["DATABASE_URL"]
    parsed = make_url(configured_url)
    name = f"omnia_cell_fence_{uuid4().hex}"
    assert _NAME_RE.fullmatch(name)
    target_url = parsed.set(database=name).render_as_string(hide_password=False)
    admin_url = parsed.set(database="postgres").render_as_string(hide_password=False)
    admin_dsn = _asyncpg_dsn(admin_url)

    asyncio.run(_create_database(admin_dsn, name))
    try:
        yield target_url
    finally:
        assert _NAME_RE.fullmatch(name)
        asyncio.run(_drop_database(admin_dsn, name))


def test_0053_roundtrip(disposable_migration_database: str) -> None:
    _run_alembic(
        disposable_migration_database,
        "upgrade",
        "0052_project_cell_control_foundation",
    )
    workspace_id, operation_id, request = asyncio.run(
        _seed_running_0052(disposable_migration_database)
    )

    _run_alembic(disposable_migration_database, "upgrade", "head")
    fencing_epoch, envelope, digest = asyncio.run(
        _read_operation(disposable_migration_database, operation_id)
    )
    assert fencing_epoch == 7
    assert envelope == {
        "workspace_id": str(workspace_id),
        "generation_run_id": None,
        "kind": "ensure",
        "request": request,
    }
    expected_canonical = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    assert digest == hashlib.sha256(expected_canonical.encode("utf-8")).hexdigest()

    _run_alembic(
        disposable_migration_database,
        "downgrade",
        "0052_project_cell_control_foundation",
    )
    _run_alembic(disposable_migration_database, "upgrade", "head")
    fencing_epoch, restored_envelope, restored_digest = asyncio.run(
        _read_operation(disposable_migration_database, operation_id)
    )
    assert fencing_epoch == 7
    assert restored_envelope == envelope
    assert restored_digest == digest
