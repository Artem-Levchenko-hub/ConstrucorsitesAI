"""Controller-only PostgreSQL connection and authentication for restored code."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import tempfile
import time
from pathlib import Path
from typing import Any

from omnia_orchestrator.core.cell_resources import CellIdentityConflict, CellResourceError
from omnia_orchestrator.services.project_machine import (
    machine_remaining_seconds,
    write_controller_json,
)
from omnia_orchestrator.services.restoration_data_contract import DataContract, database_policy_sql

HBA = """# Controller owned; the product does not mount the socket or this file.
local all postgres trust
local all all reject
host all omnia_runtime 127.0.0.1/32 scram-sha-256
host all all 0.0.0.0/0 reject
host all all ::/0 reject
"""


def policy_path(backend: Any) -> Path:
    return Path(backend.root) / str(backend.workspace_id) / "data-policy.json"


def load_policy(backend: Any) -> dict[str, Any] | None:
    from omnia_orchestrator.services.cell_state import _read_plain_json_file

    path = policy_path(backend)
    if not path.exists():
        return None
    value = _read_plain_json_file(path)
    if (
        value.get("workspace_id") != str(backend.workspace_id)
        or value.get("project_id") != str(backend.project_id)
        or value.get("owner_id") != str(backend.owner_id)
        or not isinstance(value.get("password"), str)
        or not isinstance(value.get("token_secret"), str)
        or type(value.get("epoch")) is not int
    ):
        raise CellIdentityConflict("database policy identity mismatch")
    DataContract.model_validate(value["contract"])
    return value


def stage_policy(
    backend: Any,
    contract: DataContract,
    epoch: int,
    *,
    blocked_deletes: list[str],
) -> dict[str, Any]:
    """Write intent before recreating PG. Replays retain exact credentials."""
    previous = load_policy(backend)
    if previous is not None and previous["epoch"] > epoch:
        raise CellIdentityConflict("database policy fence is newer")
    if previous is not None and previous["epoch"] == epoch:
        if (
            previous["contract"] != contract.model_dump(mode="json")
            or previous["blocked_deletes"] != blocked_deletes
        ):
            raise CellIdentityConflict("database policy changed within epoch")
        _authentication_files(backend)
        return previous
    value = {
        "workspace_id": str(backend.workspace_id),
        "project_id": str(backend.project_id),
        "owner_id": str(backend.owner_id),
        "epoch": epoch,
        "password": secrets.token_urlsafe(32),
        "token_secret": secrets.token_hex(32),
        "contract": contract.model_dump(mode="json"),
        "blocked_deletes": blocked_deletes,
    }
    _authentication_files(backend)
    write_controller_json(policy_path(backend), value)
    return value


def recover_policy(
    backend: Any,
    contract: DataContract,
    epoch: int,
    *,
    blocked_deletes: list[str],
    operation_id: str,
) -> dict[str, Any]:
    """Rotate a failed activation inside its admitted fence, after all writers stop.

    Reconciliation reuses this exact recovery intent. Advancing a hidden epoch here
    would strand the API's durable lease; rotating both credentials fences the failed
    process without changing the externally admitted lifecycle identity.
    """
    previous = load_policy(backend)
    if previous is not None and previous.get("recovery_operation_id") == operation_id:
        if (
            previous["epoch"] != epoch
            or previous["contract"] != contract.model_dump(mode="json")
            or previous["blocked_deletes"] != blocked_deletes
        ):
            raise CellIdentityConflict("database recovery envelope changed")
        _authentication_files(backend)
        return previous
    if backend._container() is not None or backend._project_postgres() is not None:
        raise CellIdentityConflict("database recovery requires confirmed writer removal")
    if previous is not None and previous["epoch"] > epoch:
        raise CellIdentityConflict("database recovery fence is newer")
    value = {
        "workspace_id": str(backend.workspace_id),
        "project_id": str(backend.project_id),
        "owner_id": str(backend.owner_id),
        "epoch": epoch,
        "password": secrets.token_urlsafe(32),
        "token_secret": secrets.token_hex(32),
        "contract": contract.model_dump(mode="json"),
        "blocked_deletes": blocked_deletes,
        "recovery_operation_id": operation_id,
    }
    _authentication_files(backend)
    write_controller_json(policy_path(backend), value)
    return value


def _authentication_files(backend: Any) -> None:
    root = policy_path(backend).parent
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, content in (
        ("postgres-hba.conf", HBA),
        ("postgres-controller.conf", "# Controller-owned configuration\n"),
    ):
        path = root / name
        if path.is_symlink():
            raise CellIdentityConflict("unsafe database authentication file")
        if path.exists() and path.read_text(encoding="utf-8") == content:
            continue  # Preserve the inode while a container has a bind mount.
        fd, temporary = tempfile.mkstemp(prefix=".auth-", dir=root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o644)  # Non-secret; only PG receives the read-only mount.
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def admin_args(backend: Any) -> tuple[list[str], dict[str, str]]:
    protected = load_policy(backend) is not None
    return (
        ["-h", "/tmp" if protected else "127.0.0.1", "-U", "postgres", "-d", "postgres"],
        {} if protected else {"PGPASSWORD": backend.project_postgres_password},
    )


def admin_sql(backend: Any, sql: str, *, max_bytes: int = 4 * 1024 * 1024) -> bytes:
    postgres = backend._project_postgres()
    if postgres is None:
        raise CellResourceError("project database is not running")
    args, env = admin_args(backend)
    execution = backend.client.api.exec_create(
        postgres.id,
        ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", *args],
        stdin=True,
        environment=env,
    )
    connection = backend.client.api.exec_start(execution["Id"], socket=True)
    try:
        connection._sock.settimeout(machine_remaining_seconds(60))
        connection._sock.sendall(sql.encode())
        connection._sock.shutdown(socket.SHUT_WR)
        output = read_controller_output(connection, max_bytes=max_bytes)
    finally:
        connection.close()
    outcome = backend.client.api.exec_inspect(execution["Id"])
    if outcome.get("Running") or outcome.get("ExitCode") != 0:
        # PostgreSQL errors can echo SQL, credentials or row contents.
        raise CellResourceError("controller database operation failed")
    return output


def read_controller_output(connection: Any, *, max_bytes: int) -> bytes:
    """Bound Docker stdout while reading, including a truncated or oversized frame."""
    output, pending = bytearray(), bytearray()
    while chunk := connection._sock.recv(65536):
        pending.extend(chunk)
        while len(pending) >= 8:
            size = int.from_bytes(pending[4:8], "big")
            if size > max_bytes or pending[0] not in {1, 2}:
                raise CellResourceError("database output budget exceeded or invalid")
            if len(pending) < size + 8:
                break
            if pending[0] == 1:
                if len(output) + size > max_bytes:
                    raise CellResourceError("database output budget exceeded")
                output.extend(pending[8 : size + 8])
            del pending[: size + 8]
    if pending:
        raise CellResourceError("incomplete controller database output")
    return bytes(output)


def install_policy(backend: Any) -> None:
    value = load_policy(backend)
    if value is None:
        raise CellResourceError("database policy was not staged")
    # Old connections carry old roles until terminated; HBA only covers new logins.
    admin_sql(
        backend,
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE pid <> pg_backend_pid() AND backend_type='client backend';",
    )
    admin_sql(
        backend,
        database_policy_sql(
            DataContract.model_validate(value["contract"]),
            epoch=value["epoch"],
            project_id=value["project_id"],
            token_secret=value["token_secret"],
            password=value["password"],
            blocked_deletes=value["blocked_deletes"],
        ),
    )


def actor_token(policy: dict[str, Any], user_id: str, *, ttl: int = 60) -> str:
    if not 1 <= ttl <= 120:
        raise ValueError("actor token lifetime out of range")
    payload = (
        json.dumps(
            {
                "purpose": "omnia-data",
                "project_id": policy["project_id"],
                "epoch": policy["epoch"],
                "user_id": user_id,
                "expires_at": int(time.time()) + ttl,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        .encode()
        .hex()
    )
    signature = hmac.new(
        policy["token_secret"].encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return payload + "." + signature
